from __future__ import annotations

from configparser import ConfigParser
from contextlib import contextmanager
from copy import deepcopy
import importlib.util
from pathlib import Path
import runpy
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "namar_test" / "mentions" / "reference_cleanup.py"


class Row(dict):
    def __getattr__(self, key):
        return self.get(key)


class Meta(Row):
    def get_table_fields(self):
        return self.get("table_fields", [])


class Database:
    def __init__(self):
        self.rows = {}
        self.metadata = {}
        self.deletions = []
        self.fail_on_delete = None
        self.commits = 0
        self.locking_reads = []
        self.reads = []
        for doctype in (
            "Material Request", "Sales Order", "Namar Mention Thread", "Namar Mention Event",
            "ToDo", "Workflow Action", "Workflow Action Permitted Role", "Notification Log",
        ):
            self.metadata[doctype] = Meta(name=doctype, issingle=0, is_virtual=0, istable=0)
            self.rows[doctype] = {}
        self.metadata["Workflow Action"]["table_fields"] = [
            Row(fieldname="permitted_roles", options="Workflow Action Permitted Role")
        ]
        self.metadata["Workflow Action Permitted Role"]["istable"] = 1

    def add(self, doctype, name, **values):
        self.rows[doctype][name] = Row(name=name, **values)

    def table_exists(self, doctype):
        return doctype in self.rows

    def exists(self, doctype, name):
        if doctype == "DocType":
            return name if name in self.metadata else None
        return name if name in self.rows.get(doctype, {}) else None

    @staticmethod
    def matches(row, filters):
        if isinstance(filters, str):
            return row.name == filters
        for field, value in (filters or {}).items():
            if isinstance(value, list):
                if value[0] != "in":
                    raise AssertionError(value)
                if row.get(field) not in value[1]:
                    return False
            elif row.get(field) != value:
                return False
        return True

    def get_values(self, doctype, filters=None, fieldname="name", **kwargs):
        if filters is None or (isinstance(filters, str) and filters == doctype):
            raise AssertionError("Frappe would query Singles for these filters")
        self.reads.append((doctype, deepcopy(filters), kwargs.get("for_update", False)))
        if kwargs.get("for_update"):
            self.locking_reads.append((doctype, deepcopy(filters)))
        rows = [row for row in self.rows[doctype].values() if self.matches(row, filters)]
        if kwargs.get("pluck"):
            return [row.get(fieldname) for row in rows]
        values = [Row({field: row.get(field) for field in fieldname}) for row in rows]
        if kwargs.get("distinct"):
            values = list({tuple(row.items()): row for row in values}.values())
        return values

    def get_value(self, doctype, filters, fieldname="name", **kwargs):
        rows = self.get_values(doctype, filters, fieldname, **{**kwargs, "pluck": True})
        return rows[0] if rows else None

    def delete(self, doctype, filters):
        if not filters or any(value in (None, "", []) for value in filters.values()):
            raise AssertionError("Refusing a broad delete")
        if self.fail_on_delete == doctype:
            raise RuntimeError("simulated database failure")
        self.deletions.append((doctype, deepcopy(filters)))
        for name, row in list(self.rows[doctype].items()):
            if self.matches(row, filters):
                del self.rows[doctype][name]

    def commit(self):
        self.commits += 1
        raise AssertionError("Cleanup must not own the transaction")

    @contextmanager
    def transaction(self):
        before = deepcopy(self.rows)
        try:
            yield
        except Exception:
            self.rows = before
            raise


def load_module():
    database = Database()
    frappe = ModuleType("frappe")
    frappe.db = database
    frappe.flags = Row()
    frappe.get_meta = lambda doctype: database.metadata[doctype]

    def forbidden(*args, **kwargs):
        raise AssertionError("Cleanup must not check permissions or create document activity")

    frappe.has_permission = forbidden
    frappe.get_doc = forbidden
    frappe.delete_doc = forbidden
    frappe.enqueue = forbidden
    name = f"_followup_reference_cleanup_test_{id(database)}"
    spec = importlib.util.spec_from_file_location(name, MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"frappe": frappe, name: module}):
        spec.loader.exec_module(module)
    return module, frappe, database


def add_work_items(database, reference_type="Material Request", reference_name="MREQ-MISSING"):
    for index, status in enumerate(("Open", "Converted", "Closed")):
        thread_name = f"THREAD-{index}"
        database.add(
            "Namar Mention Thread", thread_name, reference_doctype=reference_type,
            reference_name=reference_name, for_user=f"user-{index}", status=status,
        )
        database.add("Namar Mention Event", f"EVENT-{index}", thread=thread_name, event_type=status)
        database.add(
            "ToDo", f"TODO-{index}", reference_type=reference_type,
            reference_name=reference_name, allocated_to=f"user-{index}", status=status,
        )
        database.add(
            "Workflow Action", f"ACTION-{index}", reference_doctype=reference_type,
            reference_name=reference_name, user=f"user-{index}", status=status,
        )
        database.add(
            "Workflow Action Permitted Role", f"ROLE-{index}", parent=f"ACTION-{index}",
            parenttype="Workflow Action", parentfield="permitted_roles", role="Sales User",
        )
        database.add(
            "Notification Log", f"NOTIFICATION-{index}", document_type=reference_type,
            document_name=reference_name, for_user=f"user-{index}", read=index % 2,
        )


class ReferenceCleanupTests(unittest.TestCase):
    def setUp(self):
        self.module, self.frappe, self.database = load_module()

    def test_deleted_source_removes_all_users_states_and_workflow_children(self):
        add_work_items(self.database)
        counts = self.module.purge_reference_followups("Material Request", "MREQ-MISSING")
        self.assertEqual(counts, {
            "threads": 3, "events": 3, "todos": 3, "workflow_actions": 3,
            "notifications": 3, "child_rows": 3,
        })
        self.assertTrue(all(not rows for rows in self.database.rows.values()))
        self.assertEqual(self.database.commits, 0)

    def test_identical_name_under_another_doctype_is_not_removed(self):
        add_work_items(self.database)
        self.database.add(
            "Namar Mention Thread", "KEEP-THREAD", reference_doctype="Sales Order",
            reference_name="MREQ-MISSING", for_user="user-0", status="Closed",
        )
        self.database.add("Namar Mention Event", "KEEP-EVENT", thread="KEEP-THREAD")
        self.database.add(
            "ToDo", "KEEP-TODO", reference_type="Sales Order", reference_name="MREQ-MISSING"
        )
        self.database.add(
            "Workflow Action", "KEEP-ACTION", reference_doctype="Sales Order",
            reference_name="MREQ-MISSING",
        )
        self.database.add(
            "Notification Log", "KEEP-NOTIFICATION", document_type="Sales Order",
            document_name="MREQ-MISSING",
        )
        self.database.add(
            "Workflow Action Permitted Role", "KEEP-CHILD", parent="ACTION-0",
            parenttype="Other Parent", parentfield="permitted_roles",
        )
        self.module.purge_reference_followups("Material Request", "MREQ-MISSING")
        for doctype, name in (
            ("Namar Mention Thread", "KEEP-THREAD"), ("Namar Mention Event", "KEEP-EVENT"),
            ("ToDo", "KEEP-TODO"), ("Workflow Action", "KEEP-ACTION"),
            ("Notification Log", "KEEP-NOTIFICATION"),
            ("Workflow Action Permitted Role", "KEEP-CHILD"),
        ):
            self.assertIn(name, self.database.rows[doctype])

    def test_existing_canceled_or_permission_inaccessible_source_is_untouched(self):
        add_work_items(self.database)
        self.database.add("Material Request", "MREQ-MISSING", docstatus=2)
        before = deepcopy(self.database.rows)
        self.assertFalse(any(self.module.purge_reference_followups("Material Request", "MREQ-MISSING").values()))
        self.assertEqual(before, self.database.rows)
        self.assertEqual(self.module.scan_deleted_reference_followups()["missing_references"], 0)
        self.assertEqual(self.database.deletions, [])

    def test_source_named_like_its_doctype_still_counts_as_existing(self):
        add_work_items(self.database, reference_name="Material Request")
        self.database.add("Material Request", "Material Request")
        counts = self.module.purge_reference_followups("Material Request", "Material Request")
        self.assertFalse(any(counts.values()))
        self.assertEqual(self.database.deletions, [])

    def test_hook_only_runs_after_normal_physical_delete(self):
        add_work_items(self.database)
        doc = Row(doctype="Material Request", name="MREQ-MISSING", flags=Row())
        self.module.cleanup_deleted_reference(doc)
        self.assertEqual(self.database.deletions, [])
        doc.flags["in_delete"] = True
        self.database.add("Material Request", "MREQ-MISSING")
        self.module.cleanup_deleted_reference(doc)
        self.assertEqual(self.database.deletions, [])
        del self.database.rows["Material Request"]["MREQ-MISSING"]
        self.module.cleanup_deleted_reference(doc, "after_delete")
        self.assertFalse(self.database.rows["Namar Mention Thread"])

    def test_schema_reload_migration_install_and_uninstall_skip_hook(self):
        for flag in self.module.SCHEMA_FLAGS:
            for location in ("document", "global"):
                with self.subTest(flag=flag, location=location):
                    module, frappe, database = load_module()
                    add_work_items(database)
                    doc = Row(doctype="Material Request", name="MREQ-MISSING", flags=Row(in_delete=True))
                    (doc.flags if location == "document" else frappe.flags)[flag] = True
                    module.cleanup_deleted_reference(doc)
                    self.assertEqual(database.deletions, [])

    def test_cleanup_failure_rolls_back_with_source_delete(self):
        add_work_items(self.database)
        self.database.add("Material Request", "MREQ-MISSING")
        before = deepcopy(self.database.rows)
        self.database.fail_on_delete = "Workflow Action"
        with self.assertRaises(RuntimeError), self.database.transaction():
            self.database.delete("Material Request", {"name": "MREQ-MISSING"})
            self.module.cleanup_deleted_reference(
                Row(doctype="Material Request", name="MREQ-MISSING", flags=Row(in_delete=True))
            )
        self.assertEqual(self.database.rows, before)
        self.assertEqual(self.database.commits, 0)

    def test_scan_is_private_count_only_and_does_not_mutate_or_lock(self):
        add_work_items(self.database)
        before = deepcopy(self.database.rows)
        summary = self.module.scan_deleted_reference_followups()
        self.assertEqual(summary["missing_references"], 1)
        self.assertEqual(summary["references_scanned"], 1)
        self.assertEqual(summary["counts"]["threads"], 3)
        self.assertEqual(self.database.rows, before)
        self.assertEqual(self.database.deletions, [])
        self.assertEqual(self.database.locking_reads, [])
        self.assertNotIn("MREQ-MISSING", repr(summary))
        self.assertNotIn("user-0", repr(summary))

    def test_thread_is_locked_before_event_selection_to_serialize_replies(self):
        add_work_items(self.database)
        self.module.purge_reference_followups("Material Request", "MREQ-MISSING")
        first_event_read = next(
            index for index, (doctype, _, _) in enumerate(self.database.reads)
            if doctype == "Namar Mention Event"
        )
        locked_threads = [
            index for index, (doctype, _, for_update) in enumerate(self.database.reads)
            if doctype == "Namar Mention Thread" and for_update
        ]
        source_lock = next(
            index for index, (doctype, _, for_update) in enumerate(self.database.reads)
            if doctype == "Material Request" and for_update
        )
        self.assertTrue(locked_threads)
        self.assertLess(source_lock, locked_threads[0])
        self.assertLess(locked_threads[0], first_event_read)

    def test_old_cleanup_is_idempotent_and_runs_during_patch_migration(self):
        add_work_items(self.database)
        self.frappe.flags["in_migrate"] = True
        first = self.module.purge_deleted_reference_followups()
        second = self.module.purge_deleted_reference_followups()
        self.assertEqual(first["missing_references"], 1)
        self.assertEqual(second["missing_references"], 0)
        self.assertFalse(any(second["counts"].values()))

    def test_old_cleanup_finds_todo_and_workflow_without_a_mention_thread(self):
        self.database.add("ToDo", "TODO-ONLY", reference_type="Material Request", reference_name="MREQ-1")
        self.database.add(
            "Workflow Action", "ACTION-ONLY", reference_doctype="Sales Order", reference_name="SO-1"
        )
        summary = self.module.purge_deleted_reference_followups()
        self.assertEqual(summary["missing_references"], 2)
        self.assertEqual(summary["counts"]["todos"], 1)
        self.assertEqual(summary["counts"]["workflow_actions"], 1)
        self.assertEqual(summary["counts"]["threads"], 0)

    def test_many_source_and_thread_names_are_cleaned_in_bounded_batches(self):
        for index in range(self.module.BATCH_SIZE + 1):
            self.database.add(
                "Namar Mention Thread", f"THREAD-{index}", reference_doctype="Material Request",
                reference_name="MREQ-MISSING", for_user=f"user-{index}", status="Closed",
            )
            self.database.add("Namar Mention Event", f"EVENT-{index}", thread=f"THREAD-{index}")
        result = self.module.purge_deleted_reference_followups()
        self.assertEqual(result["counts"]["threads"], self.module.BATCH_SIZE + 1)
        self.assertEqual(result["counts"]["events"], self.module.BATCH_SIZE + 1)
        for _, filters in self.database.deletions:
            for value in filters.values():
                if isinstance(value, list):
                    self.assertLessEqual(len(value[1]), self.module.BATCH_SIZE)

    def test_invalid_unknown_single_virtual_and_missing_tables_are_skipped(self):
        for doctype in ("Unknown Type", "Settings", "Virtual Source", "Without Table"):
            self.database.add("ToDo", doctype, reference_type=doctype, reference_name="SOURCE-1")
        self.database.metadata["Settings"] = Meta(issingle=1)
        self.database.metadata["Virtual Source"] = Meta(is_virtual=1)
        self.database.metadata["Without Table"] = Meta(issingle=0, is_virtual=0)
        for index, pair in enumerate(((None, "X"), ("Material Request", ""), ("Material Request", " X "))):
            self.database.add("ToDo", f"INVALID-{index}", reference_type=pair[0], reference_name=pair[1])
        summary = self.module.purge_deleted_reference_followups()
        self.assertEqual(summary["missing_references"], 0)
        self.assertEqual(summary["skipped_references"], 7)
        self.assertEqual(self.database.deletions, [])

    def test_empty_partial_and_coerced_targets_cannot_issue_deletes(self):
        add_work_items(self.database)
        for doctype, name in ((None, None), ("", "MREQ-MISSING"), ("Material Request", None),
                              ("Material Request", ""), (" Material Request", "MREQ-MISSING"),
                              ("Material Request", ["MREQ-MISSING"]), ("Material Request", "x" * 141)):
            with self.subTest(doctype=doctype, name=name):
                self.assertFalse(any(self.module.purge_reference_followups(doctype, name).values()))
        self.assertEqual(self.database.deletions, [])

    def test_source_restored_after_scan_is_rechecked_and_preserved(self):
        add_work_items(self.database)
        with patch.object(self.module, "_missing_references", return_value=([("Material Request", "MREQ-MISSING")], 1, 0)):
            self.database.add("Material Request", "MREQ-MISSING")
            summary = self.module.purge_deleted_reference_followups()
        self.assertFalse(any(summary["counts"].values()))
        self.assertEqual(self.database.deletions, [])

    def test_patch_and_after_delete_hook_are_registered_without_cancel_hook(self):
        hooks = runpy.run_path(str(ROOT / "namar_test" / "hooks.py"))["doc_events"]
        cleanup = "namar_test.mentions.reference_cleanup.cleanup_deleted_reference"
        self.assertIn(cleanup, hooks["*"].get("after_delete", []))
        # Other independent projections may register their own lifecycle hooks;
        # reference cleanup itself must still run only after final deletion.
        for event, handlers in hooks["*"].items():
            if event != "after_delete":
                self.assertNotIn(cleanup, handlers if isinstance(handlers, list) else [handlers])
        parser = ConfigParser(allow_no_value=True)
        parser.read(ROOT / "namar_test" / "patches.txt")
        self.assertIn("namar_test.patches.v0_0_7.purge_deleted_reference_followups", parser["post_model_sync"])


if __name__ == "__main__":
    unittest.main()
