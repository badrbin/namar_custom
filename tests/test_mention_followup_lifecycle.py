from __future__ import annotations

from configparser import ConfigParser
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from namar_custom.followups.logic import mention_event_key, mention_thread_key


ROOT = Path(__file__).resolve().parents[1]
EVENTS_PATH = ROOT / "namar_custom" / "mentions" / "events.py"
MENTION_SERVICE_PATH = ROOT / "namar_custom" / "mentions" / "service.py"
HOOKS_PATH = ROOT / "namar_custom" / "hooks.py"
PATCHES_PATH = ROOT / "namar_custom" / "patches.txt"
SYNC_PATCH_PATH = (
    ROOT
    / "namar_custom"
    / "patches"
    / "v0_0_5"
    / "sync_converted_mention_followups.py"
)


class FakeDict(dict):
    def __getattr__(self, key):
        return self.get(key)

    def __setattr__(self, key, value):
        self[key] = value


class FakeDocument(FakeDict):
    def __init__(self, database, values):
        super().__init__(values)
        self._database = database
        self.save_count = 0

    def save(self, **kwargs):
        self.save_count += 1
        self.modified = f"thread-modified-{self.save_count}"
        return self

    def insert(self, **kwargs):
        self._database.events[self.name or self.event_key] = self
        return self


class FakeDatabase:
    def __init__(self):
        self.threads: dict[str, FakeDocument] = {}
        self.todos: dict[str, FakeDocument] = {}
        self.events: dict[str, FakeDocument] = {}
        self.comments: dict[str, FakeDict] = {}

    def table_exists(self, doctype):
        return doctype in {"Namar Mention Thread", "Namar Mention Event"}

    def get_values(self, doctype, filters=None, fieldname="name", **kwargs):
        if doctype != "Namar Mention Thread":
            return []
        rows = [
            row
            for row in self.threads.values()
            if all(row.get(key) == value for key, value in (filters or {}).items())
        ]
        if kwargs.get("pluck"):
            return [row.get(fieldname) for row in rows]
        return rows

    def get_value(self, doctype, filters, fieldname="name", **kwargs):
        if doctype == "User" and isinstance(filters, dict):
            return filters.get("name")
        if doctype == "Namar Mention Thread" and isinstance(filters, str):
            row = self.threads.get(filters)
            return row.get(fieldname) if row else None
        if doctype == "ToDo" and isinstance(filters, str):
            row = self.todos.get(filters)
            if not row:
                return None
            if isinstance(fieldname, (list, tuple)):
                return FakeDict({key: row.get(key) for key in fieldname})
            return row.get(fieldname)
        if doctype == "Comment" and isinstance(filters, str):
            row = self.comments.get(filters)
            if not row:
                return None
            if isinstance(fieldname, (list, tuple)):
                return FakeDict({key: row.get(key) for key in fieldname})
            return row.get(fieldname)
        return None

    def exists(self, doctype, filters):
        if doctype == "Namar Mention Event":
            if isinstance(filters, dict):
                event_name = filters.get("name")
                return event_name if event_name in self.events else None
            return filters if filters in self.events else None
        if doctype == "Namar Mention Thread":
            return filters if filters in self.threads else None
        if doctype == "ToDo":
            return filters if filters in self.todos else None
        return None


def load_events_module():
    database = FakeDatabase()
    fake_frappe = ModuleType("frappe")
    fake_frappe.db = database
    fake_frappe.flags = SimpleNamespace()
    fake_frappe.session = SimpleNamespace(user="employee@example.com")
    fake_frappe.ValidationError = type("ValidationError", (Exception,), {})
    fake_frappe.DoesNotExistError = type("DoesNotExistError", (Exception,), {})
    fake_frappe.PermissionError = type("PermissionError", (Exception,), {})

    def throw(message, exc_type=Exception):
        raise exc_type(message)

    def get_doc(doctype_or_values, name=None):
        if isinstance(doctype_or_values, dict):
            values = dict(doctype_or_values)
            values.setdefault("name", values.get("event_key"))
            return FakeDocument(database, values)
        if doctype_or_values == "Namar Mention Thread":
            return database.threads[name]
        if doctype_or_values == "ToDo":
            return database.todos[name]
        raise KeyError((doctype_or_values, name))

    fake_frappe.throw = throw
    fake_frappe.get_doc = get_doc
    fake_frappe.has_permission = lambda *args, **kwargs: True

    fake_desk = ModuleType("frappe.desk")
    fake_notifications = ModuleType("frappe.desk.notifications")
    fake_notifications.extract_mentions = lambda content: (
        ["employee@example.com"] if "@employee" in str(content) else []
    )
    fake_utils = ModuleType("frappe.utils")
    fake_utils.get_datetime = lambda value: value
    fake_utils.now_datetime = lambda: "2026-08-23 12:00:00"

    module_name = f"_namar_mention_lifecycle_test_{id(database)}"
    spec = importlib.util.spec_from_file_location(module_name, EVENTS_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("تعذر تحميل مزامن دورة المتابعات")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(
        sys.modules,
        {
            "frappe": fake_frappe,
            "frappe.desk": fake_desk,
            "frappe.desk.notifications": fake_notifications,
            "frappe.utils": fake_utils,
            module_name: module,
        },
    ):
        spec.loader.exec_module(module)
    return module, fake_frappe, database


def make_todo(database, name="TODO-1", status="Open"):
    todo = FakeDocument(
        database,
        {
            "doctype": "ToDo",
            "name": name,
            "status": status,
            "allocated_to": "employee@example.com",
            "reference_type": "Sales Order",
            "reference_name": "SO-1",
            "modified": "2026-08-23 11:00:00",
            "modified_by": "employee@example.com",
        },
    )
    database.todos[name] = todo
    return todo


def make_thread(
    database,
    todo_name="TODO-1",
    status="Converted",
    name="THREAD-1",
    reference_name="SO-1",
):
    thread = FakeDocument(
        database,
        {
            "doctype": "Namar Mention Thread",
            "name": name,
            "for_user": "employee@example.com",
            "status": status,
            "reference_doctype": "Sales Order",
            "reference_name": reference_name,
            "converted_to_todo": todo_name,
            "converted_at": "2026-08-22 10:00:00",
            "converted_by": "employee@example.com",
            "closed_at": None,
            "closed_by": None,
            "closed_via_followup": 0,
            "last_event_key": "event-new",
            "last_seen_event_key": "event-old",
            "modified": "thread-modified-0",
        },
    )
    database.threads[thread.name] = thread
    return thread


def run_sync_patch(events_module, fake_frappe):
    module_name = f"_namar_sync_patch_test_{id(fake_frappe.db)}"
    spec = importlib.util.spec_from_file_location(module_name, SYNC_PATCH_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("تعذر تحميل patch مزامنة المتابعات")
    patch_module = importlib.util.module_from_spec(spec)
    mention_package = ModuleType("namar_custom.mentions")
    mention_package.events = events_module
    with patch.dict(
        sys.modules,
        {
            "frappe": fake_frappe,
            "namar_custom.mentions": mention_package,
            "namar_custom.mentions.events": events_module,
            module_name: patch_module,
        },
    ):
        spec.loader.exec_module(patch_module)
        patch_module.execute()


class MentionFollowupLifecycleTestCase(unittest.TestCase):
    def test_external_completion_closes_thread_and_preserves_unread_and_audit(self):
        module, _, database = load_events_module()
        todo = make_todo(database, status="Closed")
        thread = make_thread(database)

        module.sync_linked_mentions_on_todo_change(todo)

        self.assertEqual(thread.status, "Closed")
        self.assertEqual(thread.converted_to_todo, "TODO-1")
        self.assertEqual(thread.converted_at, "2026-08-22 10:00:00")
        self.assertEqual(thread.closed_via_followup, 1)
        self.assertEqual(thread.last_event_key, "event-new")
        self.assertEqual(thread.last_seen_event_key, "event-old")
        self.assertEqual(len(database.events), 1)

        module.sync_linked_mentions_on_todo_change(todo)
        self.assertEqual(len(database.events), 1)
        self.assertEqual(thread.save_count, 1)

    def test_cancelled_followup_returns_thread_to_decision_without_marking_read(self):
        module, _, database = load_events_module()
        todo = make_todo(database, status="Cancelled")
        thread = make_thread(database)

        module.sync_linked_mentions_on_todo_change(todo)

        self.assertEqual(thread.status, "Open")
        self.assertEqual(thread.converted_to_todo, "TODO-1")
        self.assertEqual(thread.closed_via_followup, 0)
        self.assertEqual(thread.last_event_key, "event-new")
        self.assertEqual(thread.last_seen_event_key, "event-old")

    def test_cancelling_completed_followup_reopens_thread_and_preserves_unread(self):
        module, _, database = load_events_module()
        todo = make_todo(database, status="Cancelled")
        thread = make_thread(database, status="Closed")
        thread.closed_via_followup = 1
        thread.closed_at = "2026-08-23 10:00:00"

        module.sync_linked_mentions_on_todo_change(todo)

        self.assertEqual(thread.status, "Open")
        self.assertEqual(thread.closed_via_followup, 0)
        self.assertIsNone(thread.closed_at)
        self.assertEqual(thread.last_event_key, "event-new")
        self.assertEqual(thread.last_seen_event_key, "event-old")

    def test_reopening_completed_todo_restores_in_progress_state(self):
        module, _, database = load_events_module()
        todo = make_todo(database, status="Open")
        thread = make_thread(database, status="Closed")
        thread.closed_via_followup = 1
        thread.closed_at = "2026-08-23 10:00:00"

        module.sync_linked_mentions_on_todo_change(todo)

        self.assertEqual(thread.status, "Converted")
        self.assertEqual(thread.closed_via_followup, 0)
        self.assertIsNone(thread.closed_at)

    def test_schedule_next_transfers_only_exact_link_and_preserves_unread(self):
        module, _, database = load_events_module()
        current = make_todo(database, status="Closed")
        next_todo = make_todo(database, name="TODO-2", status="Open")
        thread = make_thread(database)

        transferred = module.transfer_linked_mentions_to_next_todo(current, next_todo)

        self.assertEqual(transferred, 1)
        self.assertEqual(thread.status, "Converted")
        self.assertEqual(thread.converted_to_todo, "TODO-2")
        self.assertEqual(thread.last_event_key, "event-new")
        self.assertEqual(thread.last_seen_event_key, "event-old")
        self.assertIn("TODO-1", next(iter(database.events.values())).content_plain)
        self.assertIn("TODO-2", next(iter(database.events.values())).content_plain)

    def test_suppression_is_exact_and_scoped(self):
        module, _, database = load_events_module()
        todo = make_todo(database, status="Closed")
        thread = make_thread(database)

        with module.suppress_todo_mention_sync("TODO-1"):
            module.sync_linked_mentions_on_todo_change(todo)
            self.assertEqual(thread.status, "Converted")

        module.sync_linked_mentions_on_todo_change(todo)
        self.assertEqual(thread.status, "Closed")

    def test_mismatched_assignee_is_never_mutated(self):
        module, _, database = load_events_module()
        todo = make_todo(database, status="Closed")
        todo.allocated_to = "other@example.com"
        thread = make_thread(database)

        module.sync_linked_mentions_on_todo_change(todo)

        self.assertEqual(thread.status, "Converted")
        self.assertEqual(thread.save_count, 0)
        self.assertEqual(database.events, {})

    def test_reassignment_returns_previously_matching_thread_to_decision(self):
        module, _, database = load_events_module()
        todo = make_todo(database, status="Open")
        previous = FakeDocument(database, dict(todo))
        todo.allocated_to = "other@example.com"
        todo.get_doc_before_save = lambda: previous
        thread = make_thread(database)

        module.sync_linked_mentions_on_todo_change(todo)

        self.assertEqual(thread.status, "Open")
        self.assertEqual(thread.converted_to_todo, "TODO-1")
        self.assertEqual(thread.last_event_key, "event-new")
        self.assertEqual(thread.last_seen_event_key, "event-old")
        self.assertEqual(len(database.events), 1)

    def test_conversion_locks_todos_before_locking_thread(self):
        source = MENTION_SERVICE_PATH.read_text(encoding="utf-8")
        conversion = source.split("def convert_mention_to_followup(", 1)[1]
        conversion = conversion.split("return {", 1)[0]

        self.assertLess(
            conversion.index("locked_todos = _lock_open_todos(thread_snapshot)"),
            conversion.index("_get_owned_thread(thread_name, for_update=True)"),
        )
        lock_helper = source.split("def _lock_open_todos(", 1)[1].split(
            "def _select_locked_todo",
            1,
        )[0]
        self.assertIn("for_update=True", lock_helper)

    def test_stale_conversion_returns_to_decision_and_keeps_unread_keys(self):
        module, _, database = load_events_module()
        thread = make_thread(database, todo_name="TODO-MISSING")

        repaired = module.reopen_stale_converted_mention(
            thread.name,
            "المتابعة المرتبطة غير موجودة",
        )

        self.assertTrue(repaired)
        self.assertEqual(thread.status, "Open")
        self.assertEqual(thread.converted_to_todo, "TODO-MISSING")
        self.assertEqual(thread.last_event_key, "event-new")
        self.assertEqual(thread.last_seen_event_key, "event-old")

    def test_backfill_patch_reconciles_all_states_and_is_idempotent(self):
        module, fake_frappe, database = load_events_module()
        make_todo(database, name="TODO-OPEN", status="Open")
        make_todo(database, name="TODO-CLOSED", status="Closed")
        make_todo(database, name="TODO-CANCELLED", status="Cancelled")
        mismatched = make_todo(database, name="TODO-MISMATCH", status="Open")
        mismatched.allocated_to = "other@example.com"

        open_thread = make_thread(database, "TODO-OPEN", name="THREAD-OPEN")
        closed_thread = make_thread(database, "TODO-CLOSED", name="THREAD-CLOSED")
        cancelled_thread = make_thread(database, "TODO-CANCELLED", name="THREAD-CANCELLED")
        missing_thread = make_thread(database, "TODO-MISSING", name="THREAD-MISSING")
        null_thread = make_thread(database, None, name="THREAD-NULL")
        mismatch_thread = make_thread(database, "TODO-MISMATCH", name="THREAD-MISMATCH")

        run_sync_patch(module, fake_frappe)
        event_count = len(database.events)
        run_sync_patch(module, fake_frappe)

        self.assertEqual(open_thread.status, "Converted")
        self.assertEqual(closed_thread.status, "Closed")
        self.assertEqual(closed_thread.closed_via_followup, 1)
        self.assertEqual(cancelled_thread.status, "Open")
        self.assertEqual(missing_thread.status, "Open")
        self.assertEqual(null_thread.status, "Open")
        self.assertEqual(mismatch_thread.status, "Open")
        self.assertEqual(len(database.events), event_count)

    def test_new_mention_reopens_completed_thread_without_losing_audit_or_marking_seen(self):
        module, _, database = load_events_module()
        reference_doctype = "Sales Order"
        reference_name = "SO-NEW"
        thread_name = mention_thread_key(
            "employee@example.com",
            reference_doctype,
            reference_name,
        )
        thread = make_thread(
            database,
            "TODO-CLOSED",
            status="Closed",
            name=thread_name,
            reference_name=reference_name,
        )
        thread.closed_via_followup = 1
        thread.mention_count = 1
        thread.latest_mentioned_at = "2026-08-22 10:00:00"
        thread.first_mentioned_at = "2026-08-22 10:00:00"
        thread.last_event_key = "event-old"
        thread.last_seen_event_key = "event-old"
        database.comments["COMMENT-1"] = FakeDict(
            reference_doctype=reference_doctype,
            reference_name=reference_name,
        )
        content = "@employee راجع الرد الجديد"
        event_key = mention_event_key(
            "employee@example.com",
            "COMMENT-1",
            "2026-08-23 13:00:00",
            content,
        )

        module.process_mention_event(
            for_user="employee@example.com",
            reference_doctype=reference_doctype,
            reference_name=reference_name,
            comment_name="COMMENT-1",
            comment_modified="2026-08-23 13:00:00",
            content=content,
            from_user="sender@example.com",
            event_key=event_key,
        )

        self.assertEqual(thread.status, "Open")
        self.assertEqual(thread.converted_to_todo, "TODO-CLOSED")
        self.assertEqual(thread.closed_via_followup, 0)
        self.assertEqual(thread.last_event_key, event_key)
        self.assertEqual(thread.last_seen_event_key, "event-old")

    def test_new_mention_keeps_open_followup_in_progress_and_unread(self):
        module, _, database = load_events_module()
        reference_doctype = "Sales Order"
        reference_name = "SO-ACTIVE"
        todo = make_todo(database, name="TODO-ACTIVE", status="Open")
        todo.reference_name = reference_name
        thread_name = mention_thread_key(
            "employee@example.com",
            reference_doctype,
            reference_name,
        )
        thread = make_thread(
            database,
            "TODO-ACTIVE",
            name=thread_name,
            reference_name=reference_name,
        )
        thread.mention_count = 1
        thread.latest_mentioned_at = "2026-08-22 10:00:00"
        thread.first_mentioned_at = "2026-08-22 10:00:00"
        thread.last_event_key = "event-old"
        thread.last_seen_event_key = "event-old"
        database.comments["COMMENT-2"] = FakeDict(
            reference_doctype=reference_doctype,
            reference_name=reference_name,
        )
        content = "@employee تحديث جديد"
        event_key = mention_event_key(
            "employee@example.com",
            "COMMENT-2",
            "2026-08-23 14:00:00",
            content,
        )

        module.process_mention_event(
            for_user="employee@example.com",
            reference_doctype=reference_doctype,
            reference_name=reference_name,
            comment_name="COMMENT-2",
            comment_modified="2026-08-23 14:00:00",
            content=content,
            from_user="sender@example.com",
            event_key=event_key,
        )

        self.assertEqual(thread.status, "Converted")
        self.assertEqual(thread.converted_to_todo, "TODO-ACTIVE")
        self.assertEqual(thread.last_event_key, event_key)
        self.assertEqual(thread.last_seen_event_key, "event-old")

    def test_patch_file_uses_explicit_pre_and_post_model_sections(self):
        parser = ConfigParser(allow_no_value=True, delimiters="\n")
        parser.optionxform = str
        with PATCHES_PATH.open(encoding="utf-8") as patches_file:
            parser.read_file(patches_file)

        self.assertIn("pre_model_sync", parser.sections())
        self.assertIn("post_model_sync", parser.sections())
        self.assertIn(
            "namar_custom.patches.v0_0_5.sync_converted_mention_followups",
            parser["post_model_sync"],
        )

    def test_hooks_cover_db_set_changes_and_deletion(self):
        source = HOOKS_PATH.read_text(encoding="utf-8")
        self.assertIn(
            '"namar_custom.mentions.events.sync_linked_mentions_on_todo_change"',
            source,
        )
        self.assertIn(
            '"namar_custom.mentions.events.sync_linked_mentions_on_todo_trash"',
            source,
        )


if __name__ == "__main__":
    unittest.main()
