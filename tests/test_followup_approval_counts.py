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


if __name__ == "__main__":
    unittest.main()
