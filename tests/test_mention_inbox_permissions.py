from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
DOCTYPE_ROOT = ROOT / "namar_test" / "namar_test" / "doctype"
THREAD_CONTROLLER = (
    DOCTYPE_ROOT / "namar_mention_thread" / "namar_mention_thread.py"
)
EVENT_CONTROLLER = DOCTYPE_ROOT / "namar_mention_event" / "namar_mention_event.py"
THREAD_SCHEMA = THREAD_CONTROLLER.with_suffix(".json")
EVENT_SCHEMA = EVENT_CONTROLLER.with_suffix(".json")
SERVICE_PATH = ROOT / "namar_test" / "mentions" / "service.py"


class FakeDatabase:
    def __init__(self, tables: set[str] | None = None):
        self.tables = tables or set()
        self.delete_calls: list[tuple[str, dict[str, str]]] = []

    @staticmethod
    def escape(value):
        return "'" + str(value).replace("'", "''") + "'"

    def table_exists(self, doctype: str) -> bool:
        return doctype in self.tables

    def delete(self, doctype: str, filters: dict[str, str]) -> None:
        self.delete_calls.append((doctype, filters))


class FakeFrappeDict(dict):
    def __getattr__(self, key):
        return self.get(key)


class FakeMentionDatabase:
    def __init__(self, thread_rows: list[dict]):
        self.thread_rows = thread_rows
        self.get_values_calls: list[tuple[str, object, object, dict]] = []

    def get_values(self, doctype, filters=None, fieldname="name", **kwargs):
        self.get_values_calls.append((doctype, filters, fieldname, kwargs))
        owner = filters.get("for_user") if isinstance(filters, dict) else None
        return [
            FakeFrappeDict(row)
            for row in self.thread_rows
            if row.get("for_user") == owner
        ]


def load_controller(
    path: Path,
    *,
    user: str,
    roles: list[str],
    database: FakeDatabase | None = None,
):
    fake_frappe = ModuleType("frappe")
    fake_frappe.db = database or FakeDatabase()
    fake_frappe.session = SimpleNamespace(user=user)
    fake_frappe.get_roles = lambda requested_user: roles if requested_user == user else []

    fake_model = ModuleType("frappe.model")
    fake_document = ModuleType("frappe.model.document")

    class Document:
        pass

    fake_document.Document = Document
    fake_frappe.model = fake_model
    fake_model.document = fake_document

    module_name = f"_namar_permission_test_{path.stem}_{id(fake_frappe)}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"تعذر تحميل {path}")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(
        sys.modules,
        {
            "frappe": fake_frappe,
            "frappe.model": fake_model,
            "frappe.model.document": fake_document,
            module_name: module,
        },
    ):
        spec.loader.exec_module(module)
    return module


def load_mention_service(
    thread_rows: list[dict],
    *,
    user: str = "employee@example.com",
    readable_references: set[tuple[str, str]] | None = None,
):
    fake_frappe = ModuleType("frappe")
    fake_frappe.session = SimpleNamespace(user=user)
    fake_frappe.db = FakeMentionDatabase(thread_rows)
    fake_frappe._dict = FakeFrappeDict
    fake_frappe.ValidationError = type("ValidationError", (Exception,), {})
    fake_frappe.PermissionError = type("PermissionError", (Exception,), {})
    fake_frappe.DoesNotExistError = type("DoesNotExistError", (Exception,), {})
    fake_frappe.permission_calls = []

    def throw(message, exc_type=Exception):
        raise exc_type(message)

    def has_permission(doctype, permission_type, *, doc, user):
        fake_frappe.permission_calls.append((doctype, permission_type, doc, user))
        allowed = readable_references
        return allowed is None or (doctype, doc) in allowed

    fake_frappe.throw = throw
    fake_frappe.has_permission = has_permission
    fake_frappe.get_cached_value = lambda *args, **kwargs: None
    fake_frappe.get_all = lambda *args, **kwargs: []
    fake_frappe.get_doc = lambda *args, **kwargs: None

    fake_utils = ModuleType("frappe.utils")
    fake_utils.get_absolute_url = lambda doctype, name: f"/app/{doctype}/{name}"
    fake_utils.now_datetime = lambda: "2026-08-18 12:00:00"
    fake_frappe.utils = fake_utils

    fake_followup_service = ModuleType("namar_test.followups.service")
    fake_followup_service._readable_reference_title = lambda *args: None
    fake_followup_service._reference_summary = lambda doc: {}
    fake_followup_service._serialize_comment = lambda doc: {}
    fake_followup_service._serialize_todo = lambda doc: {}

    module_name = f"_namar_mention_service_permission_test_{id(fake_frappe)}"
    spec = importlib.util.spec_from_file_location(module_name, SERVICE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("تعذر تحميل خدمة وارد الإشارات")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(
        sys.modules,
        {
            "frappe": fake_frappe,
            "frappe.utils": fake_utils,
            "namar_test.followups.service": fake_followup_service,
            module_name: module,
        },
    ):
        spec.loader.exec_module(module)
    return module, fake_frappe


def mention_thread(
    name: str,
    *,
    for_user: str = "employee@example.com",
    reference_name: str = "TODO-1",
    last_event_key: str = "event-1",
    last_seen_event_key: str | None = None,
    **extra,
) -> dict:
    return {
        "name": name,
        "for_user": for_user,
        "status": "Open",
        "reference_doctype": "ToDo",
        "reference_name": reference_name,
        "latest_preview_plain": "راجع المستند",
        "latest_from_user": "sender@example.com",
        "latest_mentioned_at": "2026-08-18 10:00:00",
        "last_event_key": last_event_key,
        "last_seen_event_key": last_seen_event_key,
        "modified": "2026-08-18 10:00:00",
        **extra,
    }


class MentionInboxPermissionTestCase(unittest.TestCase):
    def test_doctypes_have_no_rest_read_permission(self):
        thread_schema = json.loads(THREAD_SCHEMA.read_text(encoding="utf-8"))
        event_schema = json.loads(EVENT_SCHEMA.read_text(encoding="utf-8"))

        self.assertFalse(any(row.get("read") for row in thread_schema["permissions"]))
        self.assertEqual(
            thread_schema["permissions"],
            [{"delete": 1, "role": "System Manager"}],
        )
        self.assertEqual(event_schema["permissions"], [])

    def test_event_is_independent_and_thread_has_no_child_event_field(self):
        thread_schema = json.loads(THREAD_SCHEMA.read_text(encoding="utf-8"))
        event_schema = json.loads(EVENT_SCHEMA.read_text(encoding="utf-8"))

        self.assertFalse(event_schema.get("istable"))
        self.assertNotIn("events", {field["fieldname"] for field in thread_schema["fields"]})
        event_fields = {field["fieldname"]: field for field in event_schema["fields"]}
        self.assertEqual(event_fields["for_user"]["fieldtype"], "Link")
        self.assertEqual(event_fields["thread"]["fieldtype"], "Data")

    def test_controller_hooks_deny_all_read_and_event_delete(self):
        event = load_controller(
            EVENT_CONTROLLER,
            user="employee@example.com",
            roles=["System Manager"],
        )
        thread = load_controller(
            THREAD_CONTROLLER,
            user="employee@example.com",
            roles=["System Manager"],
        )
        own = SimpleNamespace(for_user="employee@example.com")
        self.assertEqual(event.get_permission_query_conditions(), "1 = 0")
        self.assertEqual(thread.get_permission_query_conditions(), "1 = 0")
        self.assertFalse(event.has_permission(own, "read"))
        self.assertFalse(thread.has_permission(own, "read"))
        self.assertFalse(event.has_permission(own, "delete"))

    def test_only_owner_system_manager_can_delete_thread(self):
        thread = load_controller(
            THREAD_CONTROLLER,
            user="employee@example.com",
            roles=["System Manager"],
        )
        own = SimpleNamespace(for_user="employee@example.com")
        other = SimpleNamespace(for_user="other@example.com")
        self.assertTrue(thread.has_permission(own, "delete"))
        self.assertFalse(thread.has_permission(other, "delete"))

    def test_guest_queries_are_denied(self):
        event = load_controller(EVENT_CONTROLLER, user="Guest", roles=[])
        thread = load_controller(THREAD_CONTROLLER, user="Guest", roles=[])
        self.assertEqual(event.get_permission_query_conditions(), "1 = 0")
        self.assertEqual(thread.get_permission_query_conditions(), "1 = 0")

    def test_thread_delete_cascades_owned_events_when_table_exists(self):
        database = FakeDatabase({"Namar Mention Event"})
        thread = load_controller(
            THREAD_CONTROLLER,
            user="employee@example.com",
            roles=["System Manager"],
            database=database,
        )
        thread.NamarMentionThread.on_trash(
            SimpleNamespace(name="THREAD-1", for_user="employee@example.com")
        )
        self.assertEqual(
            database.delete_calls,
            [
                (
                    "Namar Mention Event",
                    {"thread": "THREAD-1", "for_user": "employee@example.com"},
                )
            ],
        )

    def test_thread_delete_skips_missing_event_table_during_uninstall(self):
        database = FakeDatabase()
        thread = load_controller(
            THREAD_CONTROLLER,
            user="Administrator",
            roles=["System Manager"],
            database=database,
        )
        thread.NamarMentionThread.on_trash(
            SimpleNamespace(name="THREAD-2", for_user="employee@example.com")
        )
        self.assertEqual(database.delete_calls, [])

    def test_internal_service_uses_owner_filtered_bulk_database_read(self):
        source = SERVICE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("frappe.get_list(", source)
        self.assertIn("frappe.db.get_values(", source)
        self.assertIn('filters={"for_user": thread.for_user, "thread": thread.name}', source)
        self.assertIn('{"for_user": user}', source)

    def test_safe_threads_bulk_reads_owner_rows_then_drops_unknown_fields(self):
        module, fake_frappe = load_mention_service(
            [
                mention_thread("THREAD-1", future_secret="must-not-leak"),
                mention_thread(
                    "THREAD-OTHER",
                    for_user="other@example.com",
                    future_secret="other-secret",
                ),
            ]
        )

        rows = module._safe_threads("employee@example.com")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].name, "THREAD-1")
        self.assertIsInstance(rows[0], FakeFrappeDict)
        self.assertNotIn("future_secret", rows[0])
        self.assertEqual(
            fake_frappe.db.get_values_calls,
            [
                (
                    "Namar Mention Thread",
                    {"for_user": "employee@example.com"},
                    "*",
                    {
                        "as_dict": True,
                        "order_by": "latest_mentioned_at desc, modified desc",
                    },
                )
            ],
        )

    def test_seen_thread_is_absent_from_unread_and_count_is_zero(self):
        module, _ = load_mention_service(
            [
                mention_thread(
                    "THREAD-SEEN",
                    last_event_key="event-seen",
                    last_seen_event_key="event-seen",
                    future_secret="must-not-leak",
                )
            ]
        )

        result = module.get_mentions(bucket="unread")

        self.assertEqual(result["items"], [])
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["counts"]["all"], 1)
        self.assertEqual(result["counts"]["unread"], 0)

    def test_unseen_thread_is_returned_without_unknown_fields(self):
        module, _ = load_mention_service(
            [mention_thread("THREAD-UNSEEN", future_secret="must-not-leak")]
        )

        result = module.get_mentions(bucket="unread")

        self.assertEqual(result["total"], 1)
        self.assertEqual(result["counts"]["unread"], 1)
        self.assertEqual(result["items"][0]["name"], "THREAD-UNSEEN")
        self.assertEqual(result["items"][0]["unread"], 1)
        self.assertNotIn("future_secret", result["items"][0])

    def test_reference_permission_is_cached_for_repeated_reference(self):
        module, fake_frappe = load_mention_service(
            [
                mention_thread("THREAD-1", reference_name="TODO-SHARED"),
                mention_thread("THREAD-2", reference_name="TODO-SHARED"),
            ],
            readable_references={("ToDo", "TODO-SHARED")},
        )

        rows = module._safe_threads("employee@example.com")

        self.assertEqual({row.name for row in rows}, {"THREAD-1", "THREAD-2"})
        self.assertEqual(
            fake_frappe.permission_calls,
            [
                (
                    "ToDo",
                    "read",
                    "TODO-SHARED",
                    "employee@example.com",
                )
            ],
        )


if __name__ == "__main__":
    unittest.main()
