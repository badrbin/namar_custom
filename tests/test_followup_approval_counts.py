from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


SERVICE_PATH = (
    Path(__file__).resolve().parents[1]
    / "namar_test"
    / "followups"
    / "service.py"
)


class FakeFrappeDict(dict):
    def __getattr__(self, key):
        return self.get(key)


def load_service(*, action_rows=None, count_rows=None):
    fake_frappe = ModuleType("frappe")
    fake_frappe.session = SimpleNamespace(user="employee@example.com")
    fake_frappe.PermissionError = type("PermissionError", (Exception,), {})
    fake_frappe.ValidationError = type("ValidationError", (Exception,), {})
    fake_frappe.DoesNotExistError = type("DoesNotExistError", (Exception,), {})
    fake_frappe.get_list_calls = []

    def throw(message, exc_type=Exception):
        raise exc_type(message)

    def get_list(doctype, **kwargs):
        fake_frappe.get_list_calls.append((doctype, kwargs))
        if kwargs.get("fields") == ["count(name) as count"]:
            return list(count_rows or [])
        return list(action_rows or [])

    def missing_doc(*args, **kwargs):
        raise fake_frappe.DoesNotExistError

    fake_frappe.throw = throw
    fake_frappe.get_list = get_list
    fake_frappe.get_doc = missing_doc
    fake_frappe.get_cached_value = lambda *args, **kwargs: None

    fake_desk = ModuleType("frappe.desk")
    fake_desk_form = ModuleType("frappe.desk.form")
    fake_desk_form.assign_to = SimpleNamespace()
    fake_desk.form = fake_desk_form
    fake_frappe.desk = fake_desk

    fake_model = ModuleType("frappe.model")
    fake_workflow = ModuleType("frappe.model.workflow")
    fake_workflow.get_transitions = lambda doc: []
    fake_workflow.get_workflow_name = lambda doctype: None
    fake_workflow.get_workflow_state_field = lambda workflow: None
    fake_model.workflow = fake_workflow
    fake_frappe.model = fake_model

    fake_utils = ModuleType("frappe.utils")
    fake_utils.get_absolute_url = lambda doctype, name: f"/app/{doctype}/{name}"
    fake_utils.nowdate = lambda: "2026-08-19"
    fake_frappe.utils = fake_utils

    module_name = f"_namar_followup_approval_counts_test_{id(fake_frappe)}"
    spec = importlib.util.spec_from_file_location(module_name, SERVICE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("تعذر تحميل خدمة المتابعات")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(
        sys.modules,
        {
            "frappe": fake_frappe,
            "frappe.desk": fake_desk,
            "frappe.desk.form": fake_desk_form,
            "frappe.model": fake_model,
            "frappe.model.workflow": fake_workflow,
            "frappe.utils": fake_utils,
            module_name: module,
        },
    ):
        spec.loader.exec_module(module)
    return module, fake_frappe


def workflow_action(name: str) -> FakeFrappeDict:
    return FakeFrappeDict(
        name=name,
        status="Open",
        reference_doctype="Material Request",
        reference_name=f"MREQ-{name}",
        workflow_state="Pending Approval",
        user=None,
        creation="2026-08-19 10:00:00",
        modified="2026-08-19 11:00:00",
    )


class ApprovalCountsTestCase(unittest.TestCase):
    def test_get_approvals_returns_permission_aware_open_count(self):
        module, fake_frappe = load_service(
            action_rows=[workflow_action("WA-1")],
            count_rows=[FakeFrappeDict(count=7)],
        )

        result = module.get_approvals(search="  طلب  ", page_length=2)

        self.assertEqual(result["counts"], {"open": 7})
        self.assertEqual(result["search"], "طلب")
        self.assertEqual([row["name"] for row in result["items"]], ["WA-1"])
        self.assertEqual(len(fake_frappe.get_list_calls), 2)

        list_doctype, list_options = fake_frappe.get_list_calls[0]
        self.assertEqual(list_doctype, "Workflow Action")
        self.assertEqual(list_options["filters"], {"status": "Open"})
        self.assertEqual(
            list_options["or_filters"],
            [
                ["Workflow Action", "reference_doctype", "like", "%طلب%"],
                ["Workflow Action", "reference_name", "like", "%طلب%"],
                ["Workflow Action", "workflow_state", "like", "%طلب%"],
            ],
        )

        count_doctype, count_options = fake_frappe.get_list_calls[1]
        self.assertEqual(count_doctype, "Workflow Action")
        self.assertEqual(count_options["fields"], ["count(name) as count"])
        self.assertEqual(count_options["filters"], {"status": "Open"})
        self.assertEqual(count_options["limit_page_length"], 1)
        self.assertNotIn("or_filters", count_options)

    def test_empty_aggregate_result_returns_zero(self):
        module, _ = load_service(action_rows=[], count_rows=[])

        result = module.get_approvals()

        self.assertEqual(result["counts"], {"open": 0})

    def test_search_scopes_limit_fields_to_the_selected_meaning(self):
        module, _ = load_service()

        self.assertEqual(
            module._followup_search_filters("PINV", "document"),
            [["ToDo", "reference_name", "like", "%PINV%"]],
        )
        self.assertEqual(
            module._followup_search_filters("خالد", "employee"),
            [
                ["ToDo", "assigned_by", "like", "%خالد%"],
                ["ToDo", "assigned_by_full_name", "like", "%خالد%"],
            ],
        )
        self.assertEqual(
            module._approval_search_filters("اعتماد", "state"),
            [["Workflow Action", "workflow_state", "like", "%اعتماد%"]],
        )

    def test_approval_search_scope_reaches_the_query_and_response_contract(self):
        module, fake_frappe = load_service(
            action_rows=[workflow_action("WA-1")],
            count_rows=[FakeFrappeDict(count=1)],
        )

        result = module.get_approvals(
            search="اعتماد",
            search_scope="state",
            page_length=2,
        )

        self.assertEqual(result["search_scope"], "state")
        self.assertEqual(
            fake_frappe.get_list_calls[0][1]["or_filters"],
            [["Workflow Action", "workflow_state", "like", "%اعتماد%"]],
        )

    def test_followup_open_count_uses_permission_aware_aggregate(self):
        module, fake_frappe = load_service(count_rows=[FakeFrappeDict(count=4)])

        self.assertEqual(module._followup_open_count("employee@example.com"), 4)
        doctype, options = fake_frappe.get_list_calls[-1]
        self.assertEqual(doctype, "ToDo")
        self.assertEqual(options["fields"], ["count(name) as count"])
        self.assertEqual(
            options["filters"],
            {"allocated_to": "employee@example.com", "status": "Open"},
        )
        self.assertEqual(options["limit_page_length"], 1)

    def test_unified_counts_are_open_only_and_total_is_the_sum(self):
        module, _ = load_service()
        mention_service = ModuleType("namar_test.mentions.service")
        mention_service.get_open_mention_count = lambda: 2
        mention_package = ModuleType("namar_test.mentions")
        mention_package.service = mention_service

        with (
            patch.object(module, "_followup_open_count", return_value=4),
            patch.object(module, "_approval_counts", return_value={"open": 6}),
            patch.dict(
                sys.modules,
                {
                    "namar_test.mentions": mention_package,
                    "namar_test.mentions.service": mention_service,
                },
            ),
        ):
            result = module.get_my_followups_counts()

        self.assertEqual(
            result,
            {"counts": {"mentions": 2, "followups": 4, "approvals": 6, "total": 12}},
        )
        self.assertNotIn("items", result)

    def test_unified_counts_reject_guest_before_loading_mentions(self):
        module, fake_frappe = load_service()
        fake_frappe.session.user = "Guest"

        with self.assertRaisesRegex(fake_frappe.PermissionError, "يلزم تسجيل الدخول"):
            module.get_my_followups_counts()


if __name__ == "__main__":
    unittest.main()
