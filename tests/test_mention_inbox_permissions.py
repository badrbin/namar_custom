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

    def test_internal_service_uses_explicit_owner_filtered_get_all(self):
        source = SERVICE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("frappe.get_list(", source)
        self.assertGreaterEqual(source.count("frappe.get_all("), 2)
        self.assertIn('filters={"for_user": thread.for_user, "thread": thread.name}', source)
        self.assertIn('filters={"for_user": user}', source)


if __name__ == "__main__":
    unittest.main()
