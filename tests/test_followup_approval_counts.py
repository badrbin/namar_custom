from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
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

    def __setattr__(self, key, value):
        self[key] = value


class FakeLifecycleTodo(FakeFrappeDict):
    def __init__(self, values, on_save=None):
        super().__init__(values)
        self._on_save = on_save

    def as_dict(self):
        return self

    def save(self, **kwargs):
        if self._on_save:
            self._on_save(self)
        return self


class FakeLifecycleDatabase:
    def __init__(self, todos, thread, comments):
        self.todos = todos
        self.thread = thread
        self.comments = comments
        self.snapshot = None
        self.rolled_back = False
        self.released = False

    def get_value(self, doctype, filters, fieldname="name", **kwargs):
        if doctype != "ToDo" or not isinstance(filters, dict):
            return None
        for todo in self.todos.values():
            matched = True
            for key, expected in filters.items():
                actual = todo.get(key)
                if isinstance(expected, list) and expected[:1] == ["!="]:
                    matched = matched and actual != expected[1]
                else:
                    matched = matched and actual == expected
            if matched:
                return todo.get(fieldname)
        return None

    def savepoint(self, name):
        self.snapshot = (
            deepcopy(self.todos),
            deepcopy(self.thread),
            deepcopy(self.comments),
        )

    def rollback(self, save_point=None):
        self.rolled_back = True
        todos, thread, comments = self.snapshot
        self.todos.clear()
        self.todos.update(todos)
        self.thread.clear()
        self.thread.update(thread)
        self.comments.clear()
        self.comments.extend(comments)

    def release_savepoint(self, name):
        self.released = True


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


def run_complete_and_schedule_next(*, fail_transfer: bool = False):
    module, fake_frappe = load_service()
    current = FakeFrappeDict(
        name="TODO-1",
        description="متابعة المورد",
        status="Open",
        priority="Medium",
        date="2026-08-23",
        allocated_to="employee@example.com",
        assigned_by="employee@example.com",
        reference_type="Purchase Invoice",
        reference_name="PINV-1",
    )
    todos = {current.name: current}
    thread = FakeFrappeDict(
        status="Converted",
        converted_to_todo=current.name,
    )
    comments = []
    database = FakeLifecycleDatabase(todos, thread, comments)
    fake_frappe.db = database
    fake_frappe.get_doc = lambda doctype, name: todos[name]

    active_suppression = set()
    mention_events = ModuleType("namar_test.mentions.events")

    @contextmanager
    def suppress_todo_mention_sync(todo_name):
        active_suppression.add(todo_name)
        try:
            yield
        finally:
            active_suppression.remove(todo_name)

    def transfer_linked_mentions_to_next_todo(previous, next_todo):
        thread.converted_to_todo = next_todo.name
        if fail_transfer:
            raise fake_frappe.ValidationError("تعذر نقل رابط المتابعة")
        return 1

    mention_events.suppress_todo_mention_sync = suppress_todo_mention_sync
    mention_events.transfer_linked_mentions_to_next_todo = (
        transfer_linked_mentions_to_next_todo
    )
    mention_package = ModuleType("namar_test.mentions")
    mention_package.events = mention_events

    def close_exact_todo(todo):
        if todo.name not in active_suppression:
            raise AssertionError("أُغلقت المتابعة القديمة خارج نطاق suppression")
        todo.status = "Closed"

    def add_assignment(args):
        todos["TODO-2"] = FakeFrappeDict(
            name="TODO-2",
            description=args["description"],
            status="Open",
            priority=args["priority"],
            date=args["date"],
            allocated_to="employee@example.com",
            assigned_by=args["assigned_by"],
            reference_type=args["doctype"],
            reference_name=args["name"],
        )

    def add_result_comment(*args, **kwargs):
        comment = FakeFrappeDict(name="COMMENT-RESULT", content="تم الإنجاز")
        comments.append(comment)
        return comment

    module.assign_to.add = add_assignment
    result = None
    caught = None
    with (
        patch.object(module, "_get_owned_todo", return_value=current),
        patch.object(module, "_get_reference_doc", return_value=FakeFrappeDict()),
        patch.object(module, "_add_timeline_comment", side_effect=add_result_comment),
        patch.object(module, "_close_exact_todo", side_effect=close_exact_todo),
        patch.object(module, "_serialize_todo", side_effect=lambda todo: {"name": todo.name}),
        patch.object(
            module,
            "_serialize_comment",
            side_effect=lambda comment: {"name": comment.name},
        ),
        patch.dict(
            sys.modules,
            {
                "namar_test.mentions": mention_package,
                "namar_test.mentions.events": mention_events,
            },
        ),
    ):
        try:
            result = module.complete_and_schedule_next(
                "TODO-1",
                "تم الإنجاز",
                "2026-08-24",
            )
        except Exception as error:
            caught = error

    return (
        result,
        caught,
        database,
        todos,
        thread,
        comments,
        active_suppression,
    )


class ApprovalCountsTestCase(unittest.TestCase):
    def test_complete_followup_uses_exact_close_and_preserves_mention_unread(self):
        module, fake_frappe = load_service()
        thread = FakeFrappeDict(
            status="Converted",
            converted_to_todo="TODO-1",
            closed_via_followup=0,
            last_event_key="event-new",
            last_seen_event_key="event-old",
        )

        def sync_linked_thread(todo):
            thread.status = "Closed"
            thread.closed_via_followup = 1

        todo = FakeLifecycleTodo(
            {
                "name": "TODO-1",
                "description": "متابعة المورد",
                "status": "Open",
                "priority": "Medium",
                "allocated_to": "employee@example.com",
                "assigned_by": "employee@example.com",
                "reference_type": "Purchase Invoice",
                "reference_name": "PINV-1",
            },
            on_save=sync_linked_thread,
        )
        fake_frappe.get_doc = lambda doctype, name: todo

        def set_status(**kwargs):
            self.assertEqual(kwargs["todo"], "TODO-1")
            self.assertEqual(kwargs["status"], "Closed")
            todo.status = kwargs["status"]
            todo.save(ignore_permissions=True)

        module.assign_to.set_status = set_status
        comment = FakeFrappeDict(name="COMMENT-RESULT", content="تم الإنجاز")
        with (
            patch.object(module, "_get_owned_todo", return_value=todo),
            patch.object(module, "_get_reference_doc", return_value=FakeFrappeDict()),
            patch.object(module, "_add_timeline_comment", return_value=comment),
            patch.object(module, "_serialize_todo", side_effect=lambda row: {"name": row.name}),
            patch.object(
                module,
                "_serialize_comment",
                side_effect=lambda row: {"name": row.name},
            ),
        ):
            result = module.complete_followup("TODO-1", "تم الإنجاز")

        self.assertEqual(result["followup"], {"name": "TODO-1"})
        self.assertEqual(todo.status, "Closed")
        self.assertEqual(thread.status, "Closed")
        self.assertEqual(thread.closed_via_followup, 1)
        self.assertEqual(thread.last_event_key, "event-new")
        self.assertEqual(thread.last_seen_event_key, "event-old")

    def test_complete_and_schedule_next_transfers_inside_one_savepoint(self):
        (
            result,
            caught,
            database,
            todos,
            thread,
            comments,
            active_suppression,
        ) = run_complete_and_schedule_next()

        self.assertIsNone(caught)
        self.assertEqual(result["completed_followup"], {"name": "TODO-1"})
        self.assertEqual(result["next_followup"], {"name": "TODO-2"})
        self.assertEqual(todos["TODO-1"].status, "Closed")
        self.assertEqual(todos["TODO-2"].status, "Open")
        self.assertEqual(thread.converted_to_todo, "TODO-2")
        self.assertEqual([comment.name for comment in comments], ["COMMENT-RESULT"])
        self.assertTrue(database.released)
        self.assertFalse(database.rolled_back)
        self.assertEqual(active_suppression, set())

    def test_complete_and_schedule_next_rolls_everything_back_when_transfer_fails(self):
        (
            result,
            caught,
            database,
            todos,
            thread,
            comments,
            active_suppression,
        ) = run_complete_and_schedule_next(fail_transfer=True)

        self.assertIsNone(result)
        self.assertIsNotNone(caught)
        self.assertRegex(str(caught), "تعذر نقل رابط المتابعة")
        self.assertEqual(set(todos), {"TODO-1"})
        self.assertEqual(todos["TODO-1"].status, "Open")
        self.assertEqual(thread.converted_to_todo, "TODO-1")
        self.assertEqual(comments, [])
        self.assertTrue(database.rolled_back)
        self.assertFalse(database.released)
        self.assertEqual(active_suppression, set())

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

    def test_followup_overdue_count_uses_permission_aware_aggregate(self):
        module, fake_frappe = load_service(count_rows=[FakeFrappeDict(count=3)])

        self.assertEqual(
            module._followup_overdue_count("employee@example.com", "2026-08-19"),
            3,
        )
        doctype, options = fake_frappe.get_list_calls[-1]
        self.assertEqual(doctype, "ToDo")
        self.assertEqual(options["fields"], ["count(name) as count"])
        self.assertEqual(
            options["filters"],
            {
                "allocated_to": "employee@example.com",
                "status": "Open",
                "date": ["<", "2026-08-19"],
            },
        )
        self.assertEqual(options["limit_page_length"], 1)

    def test_unified_counts_keep_open_totals_and_add_attention_totals(self):
        module, _ = load_service()
        mention_service = ModuleType("namar_test.mentions.service")
        mention_service.get_open_mention_count = lambda: 2
        mention_package = ModuleType("namar_test.mentions")
        mention_package.service = mention_service

        with (
            patch.object(module, "_followup_open_count", return_value=4),
            patch.object(module, "_followup_overdue_count", return_value=3),
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
            {
                "counts": {
                    "mentions": 2,
                    "followups": 4,
                    "approvals": 6,
                    "total": 12,
                },
                "attention_counts": {
                    "mentions": 2,
                    "followups": 3,
                    "approvals": 6,
                    "total": 11,
                },
            },
        )
        self.assertNotIn("items", result)

    def test_unified_counts_reject_guest_before_loading_mentions(self):
        module, fake_frappe = load_service()
        fake_frappe.session.user = "Guest"

        with self.assertRaisesRegex(fake_frappe.PermissionError, "يلزم تسجيل الدخول"):
            module.get_my_followups_counts()


if __name__ == "__main__":
    unittest.main()
