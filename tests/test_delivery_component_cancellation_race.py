"""Cancellation races exercised against the real service without a Frappe bench.

The database double deliberately keeps old non-locking reads after a simulated
transaction starts. Locking SQL returns current committed rows. Threaded cases
hold the request lock while a cancellation or barcode change commits, so reading
get_doc/get_all after the wait would expose the original bug.
"""
from __future__ import annotations

from contextlib import ExitStack
from copy import deepcopy
import importlib.util
from pathlib import Path
import re
import sys
import threading
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch


SERVICE_PATH = Path(__file__).resolve().parents[1] / "namar_test/delivery_components/service.py"
PACKAGE = "Material Request Delivery Component Package"
EVENT = "Material Request Component Package Event"
MR = "MREQ-RACE"


class Row(dict):
    def __getattr__(self, name):
        return self.get(name)


class Document(SimpleNamespace):
    def get(self, name, default=None):
        return getattr(self, name, default)


class ValidationError(Exception):
    pass


class DoesNotExistError(Exception):
    pass


def row(name="MRI-A", **values):
    result = Row(name=name, parent=MR, parenttype="Material Request", idx=1,
                 item_code="DOOR", qty=4, custom_result_leaf_w=80,
                 custom_manufactured_qty=1, custom_component_manufacturing_policy="يدخل في التصنيع")
    result.update(values)
    return result


def package(**values):
    result = Row(name="PACKAGE-1", parent=MR, parentfield="custom_delivery_component_packages",
                 idx=1, package_key="frame||white||COMP||1", barcode_key="barcode-old",
                 loading_code="ABC-01", package_qty=10, ready_qty=0, tracking_revision=1,
                 tracking_route="تصنيع مستقل - باركود", active=1, required_for_delivery=1)
    result.update(values)
    return result


def document(values):
    result = deepcopy(dict(values))
    result["items"] = [Row(value) for value in result.get("items", [])]
    return Document(**result)


class SnapshotDatabase:
    def __init__(self):
        self.parent = Row(name=MR, docstatus=1, custom_manufacturing_total_count=99,
                          custom_manufacturing_remaining_count=0,
                          custom_manufacturing_status="مصنع بالكامل", custom_fulfillment_ready=1,
                          custom_manufacturing_completed_at="old-stamp",
                          custom_manufacturing_completed_by="old-user",
                          custom_delivery_component_source_hash="old-hash")
        self.items = [row()]
        self.packages = [package()]
        self.other_children = {"custom_manufactured_doors": []}
        self.old_document = document({**self.parent, "items": self.items,
                                      "custom_delivery_component_packages": self.packages,
                                      **self.other_children})
        self.old_packages = deepcopy(self.packages)
        self.events = []
        self.old_events = []
        self.inserted_events = []
        self.exists_calls = []
        self.full_document_reads = []
        self.lock = threading.Lock()
        self.owner = None
        self.waiting = threading.Event()
        self.queries = []
        self.writes = []
        self.commits = 0
        self.get_value = Mock(return_value=None)

    def exists(self, doctype, name):
        self.exists_calls.append((doctype, name))
        if doctype in (PACKAGE, EVENT):
            rows = self.old_packages if doctype == PACKAGE else self.old_events
            filters = name if isinstance(name, dict) else {"name": name}
            return any(all(entry.get(key) == value for key, value in filters.items()) for entry in rows)
        return True

    def current_document(self):
        if self.owner != threading.get_ident():
            raise AssertionError("Full document read without owning request lock")
        tables = [entry[0] for entry in self.queries]
        if tables[:3] != ["Material Request", "Material Request Item", PACKAGE]:
            raise AssertionError("Full child read must follow MR, MRI and package locks")
        self.full_document_reads.append(tuple(tables))
        return document({**self.parent,
                         "items": sorted(self.items, key=lambda entry: entry["idx"]),
                         "custom_delivery_component_packages": self.packages,
                         **self.other_children})

    def sql(self, query, values, as_dict=False):
        if "FOR UPDATE" not in query:
            raise AssertionError("Expected a current locking read")
        table = re.search(r"FROM `tab([^`]+)`", query).group(1)
        if table == "Material Request":
            self.waiting.set()
            self.lock.acquire()
            self.owner = threading.get_ident()
            rows = [self.parent]
        elif table == "Material Request Item":
            if self.owner != threading.get_ident():
                raise AssertionError("Items read without first owning request lock")
            rows = sorted(self.items, key=lambda item: item["name"])
        elif table == PACKAGE:
            if self.owner != threading.get_ident():
                raise AssertionError("Packages read without owning request lock")
            rows = sorted(self.packages, key=lambda item: item["name"])
            if re.search(r"WHERE\s+`?name`?\s*=", query, re.IGNORECASE):
                rows = [entry for entry in rows if entry["name"] == values[0]]
        elif table == EVENT:
            if self.owner != threading.get_ident():
                raise AssertionError("Events read without owning request lock")
            if PACKAGE not in [entry[0] for entry in self.queries]:
                raise AssertionError("Events read before package locks")
            field = "event_key" if "event_key" in query else "package_id"
            rows = [entry for entry in self.events if entry.get(field) == values[0]]
        else:
            raise AssertionError(table)
        self.queries.append((table, query, values))
        return deepcopy(rows)

    def set_value(self, doctype, name, values, **kwargs):
        self.writes.append((doctype, name, deepcopy(values)))
        if doctype == "Material Request":
            self.parent.update(values)
        elif doctype == PACKAGE:
            next(item for item in self.packages if item["name"] == name).update(values)
        else:
            raise AssertionError(doctype)

    def finish(self):
        if self.owner == threading.get_ident():
            self.owner = None
            self.lock.release()

    def commit(self):
        self.commits += 1
        self.finish()


def load_service():
    db = SnapshotDatabase()
    frappe = ModuleType("frappe")
    frappe.db = db
    frappe.session = SimpleNamespace(user="operator@example.test")
    frappe.PermissionError = ValidationError
    frappe.DoesNotExistError = DoesNotExistError
    frappe.has_permission = Mock(return_value=True)
    frappe.get_all = Mock(side_effect=AssertionError("Stale non-locking package read"))
    frappe.delete_doc = Mock()
    frappe.generate_hash = Mock(return_value="new-generated-token")
    frappe.parse_json = __import__("json").loads
    def get_doc(first, *args, **kwargs):
        if not isinstance(first, dict):
            return db.current_document() if kwargs.get("for_update") else db.old_document
        doc = document(first)
        if first.get("doctype") == EVENT:
            def insert(**kwargs):
                if any(entry["event_key"] == first["event_key"] for entry in db.events):
                    raise AssertionError("Attempted duplicate event insertion")
                values = Row(first, name="EVENT-%s" % (len(db.events) + 1))
                db.events.append(values)
                db.inserted_events.append(values)
                return doc
            doc.insert = Mock(side_effect=insert)
        return doc

    frappe.get_doc = Mock(side_effect=get_doc)
    fields = {"name", "idx", "package_key", "package_qty", "ready_qty", "remaining_qty", "status",
              "source", "barcode_key", "loading_code", "tracking_route", "tracking_status",
              "tracking_revision", "active", "ready_at", "ready_by", "loaded_at", "loaded_by",
              "delivered_at", "delivered_by", "component", "component_label", "item_code",
              "item_name", "required_for_delivery", "package_no", "package_label", "color",
              "packaging_rule", "pack_size_snapshot"}
    frappe.get_meta = lambda doctype: SimpleNamespace(
        has_field=lambda name: True, fields=[SimpleNamespace(fieldname=name) for name in fields])

    def throw(message, error=None, **kwargs):
        raise (error or ValidationError)(message)

    frappe.throw = throw
    utils = ModuleType("frappe.utils")
    utils.cint = lambda value=0: int(float(value or 0))
    utils.flt = lambda value=0: float(value or 0)
    utils.now_datetime = lambda: "2026-10-14 12:00:00"
    frappe.utils = utils
    tracking = ModuleType("namar_test.delivery_components.tracking_codes")
    tracking.ensure_material_request_tracking_code = Mock(return_value="ABC")
    name = f"_cancellation_race_service_{id(db)}"
    spec = importlib.util.spec_from_file_location(name, SERVICE_PATH)
    service = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"frappe": frappe, "frappe.utils": utils,
                                 "namar_test.delivery_components.tracking_codes": tracking,
                                 name: service}):
        spec.loader.exec_module(service)
    return service, frappe, db


class CancellationRaceTests(unittest.TestCase):
    def setUp(self):
        self.service, self.frappe, self.db = load_service()
        self.addCleanup(self.db.finish)

    def after_lock_wait(self, operation, mutation):
        """Start the service behind cancellation's lock, then commit its changes."""
        result = {}
        self.db.lock.acquire()

        def worker():
            try:
                result["value"] = operation()
            except BaseException as exc:
                result["error"] = exc
            finally:
                self.db.finish()

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        try:
            self.assertTrue(self.db.waiting.wait(2), "Service did not attempt request lock")
            mutation()
        finally:
            self.db.lock.release()
        thread.join(3)
        self.assertFalse(thread.is_alive(), "Service did not finish after lock release")
        return result

    def test_locked_source_uses_current_sql_rows_and_lock_order_after_wait(self):
        def cancel():
            self.db.parent["custom_manufacturing_status"] = "قيد التصنيع"
            self.db.items = [row("MRI-Z"), row("MRI-A")]
            self.db.items[0].update(idx=1, custom_is_cancelled_row=1)
            self.db.items[1].update(idx=2, qty=7)
            self.db.packages = [package(barcode_key="barcode-new")]

        outcome = self.after_lock_wait(lambda: self.service.locked_material_request_source(MR), cancel)
        self.assertNotIn("error", outcome)
        current, packages = outcome["value"]
        self.assertEqual([entry[0] for entry in self.db.queries], ["Material Request", "Material Request Item", PACKAGE])
        self.assertEqual([item.name for item in current.items], ["MRI-Z", "MRI-A"])
        self.assertEqual(current.items[0].custom_is_cancelled_row, 1)
        self.assertEqual(current.get("custom_manufacturing_status"), "قيد التصنيع")
        self.assertEqual(packages[0]["barcode_key"], "barcode-new")
        self.assertEqual(self.db.old_document.items[0].get("custom_is_cancelled_row"), None)
        self.assertTrue(all(isinstance(call.args[0], dict) or call.kwargs.get("for_update")
                            for call in self.frappe.get_doc.call_args_list))
        self.assertEqual(len(self.db.full_document_reads), 1)
        self.frappe.get_all.assert_not_called()
        self.assertEqual(self.db.commits, 0)

    def test_sync_passes_current_source_and_all_locked_packages_through_every_stage(self):
        seen = {}

        def build(doc, component_rows, *, existing_packages):
            seen.update(doc=doc, components=component_rows, packages=existing_packages)
            return [package(name="PACKAGE-FRESH", package_qty=6)]

        def upsert(name, rows, **kwargs):
            self.assertIs(kwargs["existing_rows"], seen["packages"])
            return rows

        def summary(name, rows, **kwargs):
            self.assertIs(kwargs["locked_source"], seen["doc"])
            self.assertEqual(kwargs["source_hash"], "fresh-hash")
            self.assertEqual(rows[0]["package_qty"], 6)
            return {"status": "غير جاهز"}

        def cancel():
            self.db.items[0].update(custom_is_cancelled_row=1, custom_store_data='[{"component":"old","qty":99}]')
            active = row("MRI-NEW")
            active.update(idx=2, qty=2, custom_store_data='[{"component":"frame","item":"COMP","qty":3}]')
            self.db.items.append(active)
            self.db.packages = [package(active=0, barcode_key="retired", loading_code="ABC-09")]

        with ExitStack() as stack:
            stack.enter_context(patch.object(self.service, "load_component_color_flags", return_value={}))
            stack.enter_context(patch.object(self.service, "build_source_hash", return_value="fresh-hash"))
            build_mock = stack.enter_context(patch.object(self.service, "build_package_rows", side_effect=build))
            stack.enter_context(patch.object(self.service, "get_or_make_loading_prefix", return_value="ABC"))
            stack.enter_context(patch.object(self.service, "assign_loading_codes", side_effect=lambda rows, *args, **kwargs: rows))
            stack.enter_context(patch.object(self.service, "upsert_package_rows", side_effect=upsert))
            stack.enter_context(patch.object(self.service, "update_material_request_summary", side_effect=summary))
            outcome = self.after_lock_wait(lambda: self.service.sync_delivery_component_packages(MR), cancel)
        self.assertNotIn("error", outcome)
        self.assertEqual(outcome["value"]["status"], "synced")
        self.assertEqual(seen["components"][0]["required_qty"], 6)
        self.assertEqual(len(seen["components"]), 1)
        self.assertEqual(seen["packages"][0]["active"], 0)
        self.assertFalse(hasattr(self.service, "get_reserved_loading_codes"))
        self.assertEqual(build_mock.call_count, 1)
        self.frappe.get_all.assert_not_called()
        self.assertEqual(self.db.commits, 1)

    def test_empty_locked_package_snapshot_does_not_fall_back_to_old_get_all(self):
        current = document({**self.db.parent, "items": []})
        with patch.object(self.service, "load_rules", return_value=[]), patch.object(self.service, "load_component_sort_order", return_value={}):
            self.assertEqual(self.service.build_package_rows(current, [], existing_packages=[]), [])
            self.assertEqual(self.service.upsert_package_rows(MR, [], existing_rows=[]), [])
        self.frappe.get_all.assert_not_called()
        self.frappe.delete_doc.assert_not_called()

    def run_sync_removing_current_package(self, delete):
        """Use real aggregation/build/upsert for a row cancelled while waiting."""
        self.frappe.delete_doc.side_effect = delete

        def cancel():
            self.db.items[0]["custom_is_cancelled_row"] = 1
            self.db.packages = [package(tracking_revision=0)]

        with ExitStack() as stack:
            stack.enter_context(patch.object(self.service, "load_rules", return_value=[]))
            stack.enter_context(patch.object(self.service, "load_component_color_flags", return_value={}))
            stack.enter_context(patch.object(self.service, "load_component_sort_order", return_value={}))
            stack.enter_context(patch.object(self.service, "get_or_make_loading_prefix", return_value=""))
            summary = stack.enter_context(patch.object(self.service, "update_material_request_summary", return_value={}))
            outcome = self.after_lock_wait(lambda: self.service.sync_delivery_component_packages(MR), cancel)
        return outcome, summary

    def test_delete_snapshot_missing_aborts_sync_before_summary_or_commit(self):
        self.db.old_packages = []

        def frappe_like_delete(doctype, name, *, ignore_missing=True, **kwargs):
            # Frappe delete_doc first checks ordinary db.exists, even when the
            # caller already obtained this package through SELECT FOR UPDATE.
            if not self.db.exists(doctype, name):
                if not ignore_missing:
                    raise DoesNotExistError(name)
                return False
            self.db.packages = [entry for entry in self.db.packages if entry["name"] != name]

        outcome, summary = self.run_sync_removing_current_package(frappe_like_delete)
        self.assertIsInstance(outcome.get("error"), DoesNotExistError)
        self.assertEqual(self.db.packages[0]["name"], "PACKAGE-1")
        self.assertEqual([entry[0] for entry in self.db.queries], ["Material Request", "Material Request Item", PACKAGE])
        self.assertFalse(self.frappe.delete_doc.call_args.kwargs["ignore_missing"])
        summary.assert_not_called()
        self.assertEqual(self.db.writes, [])
        self.assertEqual(self.db.commits, 0)

    def test_delete_noop_is_detected_by_current_postcheck_before_summary_or_commit(self):
        outcome, summary = self.run_sync_removing_current_package(lambda *args, **kwargs: None)
        self.assertIsInstance(outcome.get("error"), ValidationError)
        self.assertEqual(len(self.db.packages), 1)
        self.assertEqual(self.db.queries[-1][0], PACKAGE)
        self.assertEqual(self.db.queries[-1][2][0], "PACKAGE-1")
        self.assertEqual(sum(entry[0] == PACKAGE for entry in self.db.queries), 2)
        summary.assert_not_called()
        self.assertEqual(self.db.writes, [])
        self.assertEqual(self.db.commits, 0)

    def test_successful_delete_runs_current_postcheck_and_finishes_sync(self):
        def delete(doctype, name, **kwargs):
            self.db.packages = [entry for entry in self.db.packages if entry["name"] != name]

        outcome, summary = self.run_sync_removing_current_package(delete)
        self.assertNotIn("error", outcome)
        self.assertEqual(self.db.packages, [])
        self.assertEqual(outcome["value"]["packages"], [])
        self.assertEqual(sum(entry[0] == PACKAGE for entry in self.db.queries), 2)
        summary.assert_called_once()
        self.assertEqual(self.db.commits, 1)

    def test_current_full_child_permission_denies_sync_after_wait(self):
        def can_read(doctype, *, ptype, doc):
            return not any(child.get("item_code") == "RESTRICTED-COMPONENT"
                           for child in doc.get("custom_manufactured_doors", []))

        self.frappe.has_permission.side_effect = can_read
        self.assertTrue(can_read("Material Request", ptype="read", doc=self.db.old_document))

        def restricted_child_added():
            self.db.other_children["custom_manufactured_doors"] = [Row(item_code="RESTRICTED-COMPONENT")]

        with patch.object(self.service, "aggregate_components") as aggregate, patch.object(self.service, "update_material_request_summary") as summary:
            outcome = self.after_lock_wait(lambda: self.service.sync_delivery_component_packages(MR), restricted_child_added)
        self.assertIsInstance(outcome.get("error"), ValidationError)
        self.assertEqual(len(self.db.full_document_reads), 1)
        checked_doc = self.frappe.has_permission.call_args.kwargs["doc"]
        self.assertEqual(checked_doc.custom_manufactured_doors[0]["item_code"], "RESTRICTED-COMPONENT")
        self.assertEqual(len(checked_doc.custom_delivery_component_packages), 1)
        aggregate.assert_not_called()
        summary.assert_not_called()
        self.assertEqual(self.db.writes, [])
        self.assertEqual(self.db.commits, 0)

    def test_current_baseline_event_after_wait_prevents_duplicate_in_real_upsert(self):
        def concurrent_sync():
            self.db.events = [Row(name="EVENT-CONCURRENT", package_id="PACKAGE-1",
                                  event_key="PACKAGE-1|0|توليد")]
            self.db.packages[0]["tracking_revision"] = 0

        def sync_existing():
            _, packages = self.service.locked_material_request_source(MR)
            return self.service.upsert_package_rows(MR, deepcopy(packages), existing_rows=packages)

        with patch.object(self.service, "load_rules", return_value=[]):
            outcome = self.after_lock_wait(sync_existing, concurrent_sync)
        self.assertNotIn("error", outcome)
        self.assertEqual(len(outcome["value"]), 1)
        self.assertEqual(len(self.db.events), 1)
        self.assertEqual(self.db.inserted_events, [])
        self.assertEqual(self.db.old_events, [])
        self.assertEqual(self.db.queries[-1][0], EVENT)
        self.assertFalse(any(doctype == EVENT for doctype, _ in self.db.exists_calls))

    def test_current_event_key_after_wait_prevents_duplicate_insert(self):
        def concurrent_event():
            self.db.events = [Row(name="EVENT-CONCURRENT", package_id="PACKAGE-1",
                                  event_key="PACKAGE-1|8|تجهيز جزئي")]

        def insert_existing():
            _, packages = self.service.locked_material_request_source(MR)
            self.service.insert_package_event(material_request=MR, package_row=packages[0],
                                              action="تجهيز جزئي", from_status="", to_status="",
                                              quantity=5, source="QR", revision=8)

        outcome = self.after_lock_wait(insert_existing, concurrent_event)
        self.assertNotIn("error", outcome)
        self.assertEqual(len(self.db.events), 1)
        self.assertEqual(self.db.inserted_events, [])
        self.assertEqual(self.db.old_events, [])
        self.assertIn("event_key", self.db.queries[-1][1])
        self.assertFalse(any(doctype == EVENT for doctype, _ in self.db.exists_calls))

    def test_missing_baseline_is_inserted_once_and_current_recheck_sees_it(self):
        _, packages = self.service.locked_material_request_source(MR)
        self.service.ensure_package_event_baseline(MR, packages[0])
        self.service.ensure_package_event_baseline(MR, packages[0])
        self.assertEqual(len(self.db.events), 1)
        self.assertEqual(len(self.db.inserted_events), 1)
        self.assertEqual(self.db.inserted_events[0]["package_id"], "PACKAGE-1")
        self.assertEqual(self.db.inserted_events[0]["revision"], 0)
        self.assertEqual(self.db.old_events, [])
        self.assertEqual(self.db.commits, 0)

    def test_cancelled_components_are_skipped_before_qty_and_store_fallback(self):
        cancelled = row()
        cancelled.update(custom_is_cancelled_row=1, qty="invalid if read", custom_store_data="")
        self.db.get_value.side_effect = AssertionError("Cancelled store fallback must not run")
        with patch.object(self.service, "load_component_color_flags", return_value={}):
            self.assertEqual(self.service.aggregate_components(document({"items": [cancelled]})), [])
            self.assertEqual(self.service.parse_store_data(cancelled), [])
        self.db.get_value.assert_not_called()

    def test_cleared_current_store_data_does_not_reload_stale_snapshot(self):
        current = row(custom_is_cancelled_row=0, custom_store_data="")
        self.db.get_value.return_value = '[{"component":"stale-frame","qty":9}]'
        with patch.object(self.service, "load_component_color_flags", return_value={}):
            self.assertEqual(self.service.aggregate_components(document({"items": [current]})), [])
            self.assertEqual(self.service.parse_store_data(current), [])
        self.db.get_value.assert_not_called()

    def test_only_archived_packages_without_saved_hash_do_not_require_sync(self):
        current = document({"name": MR, "items": [], "custom_delivery_component_source_hash": ""})
        with patch.object(self.service, "load_rules", return_value=[]), patch.object(self.service, "load_component_color_flags", return_value={}):
            needs_sync = self.service.packages_need_sync(current, [package(active=0)])
        self.assertFalse(needs_sync)
        self.frappe.get_all.assert_not_called()

    def test_door_counts_preserve_partial_positive_qty_policy_and_legacy_fallback(self):
        def make(name, **values):
            result = row(name)
            result.update(values)
            return result

        rows = [
            make("PARTIAL", qty=5, custom_manufactured_qty=2, custom_is_manufactured=1),
            make("CANCELLED", qty=99, custom_is_cancelled_row=1, custom_cutting_template="MUST-NOT-READ"),
            make("EXCLUDED", qty=8, custom_component_manufacturing_policy="لا يدخل في التصنيع"),
            make("TEMPLATE", qty=6, custom_component_manufacturing_policy="", custom_cutting_template="EXCLUDED-TEMPLATE"),
            make("OVERRIDE", qty=2, custom_manufactured_qty=2, custom_cutting_template="EXCLUDED-TEMPLATE"),
            make("LEGACY", qty=0, custom_result_leaf_w=0, custom_result_leaf_w_text="0", custom_manufactured_qty=0, custom_is_manufactured=1),
            make("CLAMP", qty=3, custom_manufactured_qty=9),
            make("NO-DIMENSION", qty=100, custom_result_leaf_w=0),
        ]
        self.db.get_value.return_value = "لا يدخل في التصنيع"
        self.assertEqual(self.service.door_manufacturing_counts(document({"items": rows})), (11.0, 3.0))
        self.assertEqual(self.db.get_value.call_count, 1)
        self.assertEqual(self.db.get_value.call_args.args[1]["parent"], "EXCLUDED-TEMPLATE")

    def test_scan_rejects_rotated_deleted_or_inactive_package_after_wait(self):
        for change in ("rotated", "deleted", "inactive"):
            with self.subTest(change=change):
                self.service, self.frappe, self.db = load_service()

                def cancel():
                    if change == "rotated":
                        self.db.packages[0]["barcode_key"] = "barcode-new"
                    elif change == "deleted":
                        self.db.packages = []
                    else:
                        self.db.packages[0]["active"] = 0

                with patch.object(self.service, "insert_package_event") as event, patch.object(self.service, "packages_need_sync", return_value=False):
                    outcome = self.after_lock_wait(lambda: self.service.mark_delivery_component_package_event(MR, "barcode-old"), cancel)
                self.assertIsInstance(outcome.get("error"), ValidationError)
                event.assert_not_called()
                self.assertEqual(self.db.writes, [])
                self.assertEqual(self.db.commits, 0)
                self.frappe.get_all.assert_not_called()

    def test_partial_scan_uses_current_ready_quantity_and_revision_after_wait(self):
        def concurrent_scan():
            self.db.packages[0].update(ready_qty=3, tracking_revision=7)

        def summary(name, packages, *, locked_source):
            self.assertEqual(packages[0]["ready_qty"], 5)
            self.assertEqual(packages[0]["tracking_revision"], 8)
            return {"overall": {"status": "جاهزية جزئية"}}

        with ExitStack() as stack:
            stack.enter_context(patch.object(self.service, "packages_need_sync", return_value=False))
            event = stack.enter_context(patch.object(self.service, "insert_package_event"))
            stack.enter_context(patch.object(self.service, "update_material_request_summary", side_effect=summary))
            stack.enter_context(patch.object(self.service, "publish_package_changed_event"))
            stack.enter_context(patch.object(self.service, "get_package_events", return_value=[]))
            outcome = self.after_lock_wait(lambda: self.service.mark_delivery_component_package_event(MR, "barcode-old", mode="partial", ready_qty=2), concurrent_scan)
        self.assertNotIn("error", outcome)
        self.assertEqual(outcome["value"]["package"]["ready_qty"], 5)
        self.assertEqual(event.call_args.kwargs["revision"], 8)
        self.assertEqual(event.call_args.kwargs["quantity"], 5)
        self.assertEqual(self.db.writes[0][2]["ready_qty"], 5)
        self.assertEqual(self.db.commits, 1)
        self.frappe.get_all.assert_not_called()

    def test_scan_rechecks_actual_source_hash_after_cancellation_wait(self):
        self.db.items[0]["custom_store_data"] = '[{"component":"frame","item":"COMP","qty":3}]'
        self.db.old_document = document({**self.db.parent, "items": self.db.items})
        with ExitStack() as stack:
            stack.enter_context(patch.object(self.service, "load_rules", return_value=[]))
            stack.enter_context(patch.object(self.service, "load_component_color_flags", return_value={}))
            old_hash = self.service.build_source_hash(self.db.old_document)
            self.db.parent["custom_delivery_component_source_hash"] = old_hash
            self.db.old_document.custom_delivery_component_source_hash = old_hash
            event = stack.enter_context(patch.object(self.service, "insert_package_event"))
            outcome = self.after_lock_wait(
                lambda: self.service.mark_delivery_component_package_event(MR, "barcode-old"),
                lambda: self.db.items[0].update(custom_is_cancelled_row=1),
            )
        self.assertIsInstance(outcome.get("error"), ValidationError)
        self.assertIn("تغيرت مكونات الطلب", str(outcome["error"]))
        event.assert_not_called()
        self.assertEqual(self.db.writes, [])
        self.assertEqual(self.db.commits, 0)

    def test_standalone_summary_discards_supplied_stale_packages_and_header_counters(self):
        stale_packages = [package(ready_qty=10)]

        def cancel():
            self.db.items[0].update(qty=4, custom_manufactured_qty=1)
            self.db.packages[0].update(ready_qty=0)

        with patch.object(self.service, "packages_need_sync", return_value=False):
            outcome = self.after_lock_wait(lambda: self.service.update_material_request_summary(MR, stale_packages), cancel)
        self.assertNotIn("error", outcome)
        result = outcome["value"]
        self.assertEqual(result["overall"]["door_total"], 4)
        self.assertEqual(result["overall"]["door_remaining"], 3)
        self.assertEqual(result["overall"]["package_ready"], 0)
        self.assertFalse(result["overall"]["is_ready"])
        self.assertEqual(result["manufacturing_status"], "قيد التصنيع")
        self.assertEqual(self.db.parent["custom_fulfillment_ready"], 0)
        self.assertIsNone(self.db.parent["custom_manufacturing_completed_at"])
        self.assertEqual(self.db.commits, 0)
        self.frappe.get_all.assert_not_called()


if __name__ == "__main__":
    unittest.main()
