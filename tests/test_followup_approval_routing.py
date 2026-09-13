from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
import json
import sqlite3
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from test_followup_approval_counts import FakeFrappeDict, load_service, workflow_action

try:
    from pypika import Case, Table
    from pypika.dialects import MySQLQueryBuilder
    from pypika.functions import Cast
except ImportError:
    MySQLQueryBuilder = None


FIELD = "custom_followups_routing_targets"
HIDE = "custom_followups_hide_from_approvals"
A = "a@example.com"
B = "b@example.com"
C = "c@example.com"


class RuntimeDocument(FakeFrappeDict):
    def as_dict(self):
        return self


class RoutingRuntime:
    """In-memory DB with the standard list-permission boundary preserved."""

    def __init__(self, targets=(), *, size=1):
        self.service, self.frappe = load_service()
        self.frappe.session.user = A
        self.calls = []
        self.doc_reads = []
        self.constructed_docs = []
        self.permission_reads = []
        self.read_denied = set()
        self.child_tables = {}
        self.condition_queries = []
        self.condition_evaluations = []
        self.denied = set()
        self.user_fields = {"responsible_user"}
        self.data = {
            "Workflow": [FakeFrappeDict(name="WF-A", document_type="Material Request", is_active=1)],
            "Workflow Document State": [FakeFrappeDict(
                parent="WF-A", parenttype="Workflow", state="Pending Approval",
                **{FIELD: self.setting(targets)},
            )],
            "Workflow Transition": [FakeFrappeDict(
                parent="WF-A", parenttype="Workflow", state="Pending Approval",
                allowed="Approver", allow_self_approval=1,
            )],
            "User": [FakeFrappeDict(name=name, enabled=1, user_type="System User", full_name=name[0].upper()) for name in (A, B, C)],
            "Has Role": [FakeFrappeDict(parent=name, parenttype="User", role=role) for name, role in (
                (A, "Approver"), (B, "Approver"), (C, "Approver"), (A, "Branch"), (B, "Branch"),
            )],
            "Role": [FakeFrappeDict(name=role) for role in ("Approver", "Branch", "Other", "All", "Guest", "Desk User")],
            "Workflow Action": [],
            "Workflow Action Permitted Role": [],
            "Material Request": [],
        }
        for index in range(size):
            row = workflow_action(f"WA-{index:05}")
            self.data["Workflow Action"].append(row)
            self.data["Workflow Action Permitted Role"].append(FakeFrappeDict(parent=row.name, parenttype="Workflow Action", role="Approver"))
            self.data["Material Request"].append(FakeFrappeDict(name=row.reference_name, owner=A, responsible_user=B, sales_order_owner=C))
        self.frappe.get_meta = self.get_meta
        self.frappe.get_all = self.get_all
        self.frappe.get_list = self.get_list
        self.frappe.get_doc = self.get_doc
        self.frappe.has_permission = self.has_permission
        self.frappe._dict = FakeFrappeDict
        self.frappe.message_log = [{"message": "سابق"}]
        self.frappe.flags = FakeFrappeDict(error_message="previous")
        self.frappe.safe_eval = self.safe_eval
        self.condition_namespace = FakeFrappeDict(
            db=FakeFrappeDict(get_list=self.condition_get_list, get_value=lambda *args: None),
            session=FakeFrappeDict(user=self.frappe.session.user),
            utils=FakeFrappeDict(),
        )
        self.service.get_workflow_safe_globals = lambda: {"frappe": self.condition_namespace}

    @staticmethod
    def setting(targets):
        return json.dumps({"version": 1, "targets": list(targets)})

    def set_targets(self, targets):
        self.data["Workflow Document State"][0][FIELD] = self.setting(targets)

    def get_meta(self, doctype):
        return SimpleNamespace(
            has_field=lambda name: doctype == "Workflow Document State" and name in (FIELD, HIDE),
            issingle=False,
            is_virtual=False,
            fields=[SimpleNamespace(fieldname=name, fieldtype="Link", options="User") for name in self.user_fields],
            get_table_fields=lambda: [SimpleNamespace(fieldname=field, options=child) for field, child in self.child_tables.get(doctype, ())],
        )

    @staticmethod
    def matches(row, filters):
        for field, expected in (filters or {}).items():
            actual = row.get(field)
            if isinstance(expected, list):
                op, value = expected
                if op == "in" and actual not in value:
                    return False
                if op == "not in" and actual in value:
                    return False
            elif actual != expected:
                return False
        return True

    def query(self, doctype, options, *, permission=False):
        rows = [row for row in self.data.get(doctype, ()) if self.matches(row, options.get("filters"))]
        if permission:
            rows = [row for row in rows if row.get("name") not in self.denied]
        or_filters = options.get("or_filters") or ()
        if or_filters:
            rows = [row for row in rows if any(
                str(pattern).strip("%").casefold() in str(row.get(field) or "").casefold()
                for _, field, _, pattern in or_filters
            )]
        if options.get("fields") == ["count(name) as count"]:
            return [FakeFrappeDict(count=len(rows))]
        if options.get("order_by") == "name asc":
            rows.sort(key=lambda row: row["name"])
        offset = options.get("limit_start", 0)
        length = options.get("limit_page_length", 0)
        rows = rows[offset : offset + length] if length else rows[offset:]
        return [FakeFrappeDict(row if options.get("fields") == ["*"] else {
            field: row.get(field) for field in options.get("fields", row.keys())
        }) for row in rows]

    def get_all(self, doctype, **options):
        self.calls.append(("all", doctype, options))
        if doctype == "Workflow Action":
            raise AssertionError("Never bypass Workflow Action permissions")
        return self.query(doctype, options)

    def get_list(self, doctype, **options):
        self.calls.append(("list", doctype, options))
        return self.query(doctype, options, permission=True)

    def get_doc(self, doctype, name=None):
        if isinstance(doctype, dict):
            self.constructed_docs.append(doctype)
            return RuntimeDocument(doctype)
        self.doc_reads.append((doctype, name))
        if doctype == "Workflow Action":
            return next(row for row in self.data[doctype] if row.name == name)
        raise self.frappe.DoesNotExistError(name)

    def has_permission(self, doctype, permission, *, doc, user, throw=False):
        self.permission_reads.append((doctype, doc.name, user))
        return (doc.name, user) not in self.read_denied

    def condition_get_list(self, doctype, **kwargs):
        self.condition_queries.append((doctype, kwargs))
        return [FakeFrappeDict(name="visible")] if kwargs.get("user") == B else []

    def safe_eval(self, condition, globals_, locals_):
        self.condition_evaluations.append((condition, globals_["frappe"].session.user))
        return eval(condition, {"__builtins__": {}, **globals_}, locals_)

    def approvals(self, **kwargs):
        with patch.object(self.service, "_readable_reference_title", return_value="المستند"):
            return self.service.get_approvals(**kwargs)

    def ids(self, **kwargs):
        return [row["name"] for row in self.approvals(**kwargs)["items"]]


class ApprovalRoutingTestCase(unittest.TestCase):
    def test_default_roles_keep_original_aggregate_and_pagination(self):
        runtime = RoutingRuntime(size=4)
        result = runtime.approvals(page_length=2)
        self.assertEqual(result["counts"], {"open": 4})
        self.assertTrue(result["has_more"])
        self.assertEqual(result["items"][0]["routing"]["mode"], "Role")
        self.assertEqual([call[1] for call in runtime.calls if call[0] == "list"], ["Workflow Action", "Workflow Action"])
        self.assertFalse(any(call[1] in ("User", "Has Role", "Workflow Transition", "Material Request") for call in runtime.calls))

    def test_named_users_union_keeps_both_same_role_users_and_deduplicates(self):
        runtime = RoutingRuntime([{"type": "user", "user": A}, {"type": "user", "user": B}, {"type": "user", "user": A}])
        for user in (A, B):
            runtime.frappe.session.user = user
            self.assertEqual(len(runtime.ids()), 1)
            self.assertEqual(runtime.service._approval_counts(), {"open": 1})
        runtime.frappe.session.user = C
        self.assertEqual(runtime.ids(), [])
        self.assertEqual(runtime.service._approval_counts(), {"open": 0})

    def test_target_user_never_expands_base_permission(self):
        runtime = RoutingRuntime([{"type": "user", "user": A}])
        runtime.denied.add("WA-00000")
        self.assertEqual(runtime.ids(), [])
        self.assertEqual(runtime.service._approval_counts(), {"open": 0})
        self.assertFalse(any(call[1] == "Material Request" for call in runtime.calls))
        with self.assertRaises(runtime.frappe.PermissionError):
            runtime.service.get_approval_detail("WA-00000")
        self.assertEqual(runtime.doc_reads, [])

    def test_hidden_detail_rejected_without_loading_reference_or_changing_roles(self):
        runtime = RoutingRuntime([{"type": "user", "user": B}])
        before_roles = list(runtime.data["Has Role"])
        with self.assertRaisesRegex(runtime.frappe.PermissionError, "الموافقة غير متاحة"):
            runtime.service.get_approval_detail("WA-00000")
        self.assertEqual(runtime.doc_reads, [])
        self.assertEqual(runtime.data["Has Role"], before_roles)

    def test_visible_detail_returns_same_routing_metadata_as_list(self):
        runtime = RoutingRuntime([{"type": "user", "user": A}, {"type": "role", "role": "Branch"}])
        list_routing = runtime.approvals()["items"][0]["routing"]
        action = runtime.data["Workflow Action"][0]
        reference = SimpleNamespace(check_permission=lambda permission: None)
        with (
            patch.object(runtime.frappe, "get_doc", side_effect=lambda doctype, name=None: RuntimeDocument(doctype) if isinstance(doctype, dict) else action if doctype == "Workflow Action" else reference),
            patch.object(runtime.service, "_reference_summary", return_value={}),
            patch.object(runtime.service, "_readable_reference_title", return_value="المستند"),
            patch.object(runtime.service, "_get_timeline", return_value=[]),
        ):
            detail = runtime.service.get_approval_detail(action.name)
        self.assertEqual(detail["approval"]["routing"], list_routing)

    def test_navbar_and_tab_counts_use_same_routed_open_actions(self):
        runtime = RoutingRuntime([{"type": "user", "user": B}])
        mention_service = ModuleType("namar_custom.mentions.service")
        mention_service.get_open_mention_count = lambda: 0
        mention_package = ModuleType("namar_custom.mentions")
        mention_package.service = mention_service
        with (
            patch.object(runtime.service, "_followup_open_count", return_value=0),
            patch.object(runtime.service, "_followup_overdue_count", return_value=0),
            patch.dict(sys.modules, {"namar_custom.mentions": mention_package, "namar_custom.mentions.service": mention_service}),
        ):
            for user, expected in ((A, 0), (B, 1)):
                runtime.frappe.session.user = user
                result = runtime.service.get_my_followups_counts()
                self.assertEqual(result["counts"]["approvals"], expected)
                self.assertEqual(result["attention_counts"]["approvals"], expected)
                self.assertEqual(runtime.approvals()["counts"]["open"], expected)

    def test_owner_uses_this_document_and_reloads_after_owner_change(self):
        runtime = RoutingRuntime([{"type": "owner"}])
        self.assertEqual(len(runtime.ids()), 1)
        runtime.frappe.session.user = C
        self.assertEqual(runtime.ids(), [])  # The linked order's owner is C.
        runtime.data["Material Request"][0].owner = C
        self.assertEqual(len(runtime.ids()), 1)

    def test_owner_with_role_is_union_not_intersection(self):
        runtime = RoutingRuntime([{"type": "owner"}, {"type": "role", "role": "Branch"}])
        for user in (A, B):
            runtime.frappe.session.user = user
            self.assertEqual(len(runtime.ids()), 1)
        runtime.frappe.session.user = C
        self.assertEqual(runtime.ids(), [])

    def test_field_user_is_direct_link_user_and_reloads_current_value(self):
        runtime = RoutingRuntime([{"type": "field", "field": "responsible_user"}])
        runtime.frappe.session.user = B
        result = runtime.approvals()
        self.assertEqual(result["items"][0]["routing"]["targets"][0]["field"], "responsible_user")
        runtime.data["Material Request"][0].responsible_user = A
        self.assertEqual(runtime.ids(), [])
        runtime.frappe.session.user = A
        self.assertEqual(len(runtime.ids()), 1)

    def test_non_user_field_or_builtin_owner_cannot_bypass_field_allowlist(self):
        for field in ("owner", "sales_order_owner", "items.user", "name"):
            runtime = RoutingRuntime([{"type": "field", "field": field}])
            with self.subTest(field=field):
                item = runtime.approvals()["items"][0]
                self.assertTrue(item["routing"]["fallback"])
                self.assertEqual(item["routing"]["responsible_users"], [])

    def test_invalid_subset_never_widens_valid_targets_to_everyone(self):
        runtime = RoutingRuntime([{"type": "user", "user": "missing@example.com"}, {"type": "user", "user": B}])
        self.assertEqual(runtime.ids(), [])
        runtime.frappe.session.user = B
        routing = runtime.approvals()["items"][0]["routing"]
        self.assertFalse(routing["fallback"])
        self.assertEqual([target["user"] for target in routing["targets"]], [B])
        self.assertNotIn("missing@example.com", json.dumps(routing))

    def test_all_unusable_targets_fall_back_without_exposing_invalid_target(self):
        for change in ("disabled", "website", "missing", "no_role", "no_current_role", "field_empty"):
            runtime = RoutingRuntime([{"type": "user", "user": B}])
            if change == "disabled":
                runtime.data["User"][1].enabled = 0
            elif change == "website":
                runtime.data["User"][1].user_type = "Website User"
            elif change == "missing":
                runtime.data["User"].pop(1)
            elif change == "no_role":
                runtime.data["Has Role"] = [row for row in runtime.data["Has Role"] if row.parent != B]
            elif change == "no_current_role":
                runtime.data["Workflow Transition"][0].allowed = "Other"
            else:
                runtime.set_targets([{"type": "field", "field": "responsible_user"}])
                runtime.data["Material Request"][0].responsible_user = None
            with self.subTest(change=change):
                routing = runtime.approvals()["items"][0]["routing"]
                self.assertTrue(routing["fallback"])
                self.assertIn("حسب أدوار", routing["note"])
                self.assertNotIn(B, json.dumps(routing))

    def test_owner_self_approval_rule_is_considered_before_routing(self):
        runtime = RoutingRuntime([{"type": "owner"}])
        runtime.data["Workflow Transition"][0].allow_self_approval = 0
        self.assertTrue(runtime.approvals()["items"][0]["routing"]["fallback"])
        runtime.data["Workflow Transition"].append(FakeFrappeDict(
            parent="WF-A", parenttype="Workflow", state="Pending Approval",
            allowed="Approver", allow_self_approval=1,
        ))
        self.assertFalse(runtime.approvals()["items"][0]["routing"]["fallback"])

    def test_role_targets_intersect_eligible_members_and_empty_roles_fall_back(self):
        runtime = RoutingRuntime([{"type": "role", "role": "Branch"}])
        runtime.data["Has Role"] = [row for row in runtime.data["Has Role"] if not (row.parent == B and row.role == "Approver")]
        runtime.frappe.session.user = B
        self.assertEqual(runtime.ids(), [])
        runtime.frappe.session.user = C
        runtime.set_targets([{"type": "role", "role": "Other"}])
        self.assertTrue(runtime.approvals()["items"][0]["routing"]["fallback"])

    def test_automatic_roles_and_administrator_match_frappe_membership(self):
        runtime = RoutingRuntime([{"type": "role", "role": "Branch"}])
        runtime.data["Workflow Action Permitted Role"][0].role = "Desk User"
        runtime.data["Workflow Transition"][0].allowed = "Desk User"
        self.assertFalse(runtime.approvals()["items"][0]["routing"]["fallback"])
        runtime.data["User"].append(FakeFrappeDict(name="Administrator", enabled=1, user_type="System User", full_name="Administrator"))
        runtime.frappe.session.user = "Administrator"
        runtime.set_targets([{"type": "role", "role": "Other"}])
        self.assertTrue(runtime.approvals()["items"][0]["routing"]["fallback"])
        runtime.set_targets([{"type": "user", "user": "Administrator"}])
        self.assertFalse(runtime.approvals()["items"][0]["routing"]["fallback"])

    def test_read_denied_target_falls_back_to_standard_roles(self):
        runtime = RoutingRuntime([{"type": "user", "user": B}])
        runtime.read_denied.add((runtime.data["Material Request"][0].name, B))
        self.assertTrue(runtime.approvals()["items"][0]["routing"]["fallback"])
        self.assertEqual(runtime.doc_reads, [])

    def test_read_denied_target_does_not_widen_another_valid_target(self):
        runtime = RoutingRuntime([{"type": "user", "user": A}, {"type": "user", "user": B}])
        runtime.read_denied.add((runtime.data["Material Request"][0].name, A))
        self.assertEqual(runtime.ids(), [])
        runtime.frappe.session.user = B
        routing = runtime.approvals()["items"][0]["routing"]
        self.assertFalse(routing["fallback"])
        self.assertEqual([target["user"] for target in routing["targets"]], [B])

    def test_role_members_read_is_checked_per_reference_not_owner_group(self):
        runtime = RoutingRuntime([{"type": "role", "role": "Branch"}], size=2)
        for name in (A, B):
            runtime.read_denied.add((runtime.data["Material Request"][0].name, name))
        results = runtime.approvals()["items"]
        self.assertTrue(results[0]["routing"]["fallback"])
        self.assertFalse(results[1]["routing"]["fallback"])

    def test_reference_full_values_and_children_are_batched_and_permissions_cached(self):
        runtime = RoutingRuntime([{"type": "user", "user": A}, {"type": "owner"}, {"type": "role", "role": "Branch"}])
        runtime.child_tables["Material Request"] = [("items", "Material Request Item"), ("alternatives", "Material Request Item")]
        parent = runtime.data["Material Request"][0].name
        runtime.data["Material Request Item"] = [FakeFrappeDict(
            name=f"CHILD-{field}", parent=parent, parenttype="Material Request", parentfield=field, idx=1,
            warehouse=f"Warehouse-{field}", qty=5,
        ) for field in ("items", "alternatives")]
        runtime.approvals()
        self.assertEqual(runtime.doc_reads, [])
        self.assertEqual(len(runtime.constructed_docs), 1)
        doc = runtime.constructed_docs[0]
        self.assertEqual(doc["items"][0]["warehouse"], "Warehouse-items")
        self.assertEqual(doc["alternatives"][0]["warehouse"], "Warehouse-alternatives")
        self.assertEqual(doc["items"][0]["qty"], 5)
        self.assertEqual(doc["items"][0]["doctype"], "Material Request Item")
        self.assertEqual(Counter(runtime.permission_reads)[("Material Request", parent, A)], 1)

    def test_session_condition_uses_recipient_without_mutating_viewer_or_factory_globals(self):
        runtime = RoutingRuntime([{"type": "user", "user": A}, {"type": "user", "user": B}])
        runtime.data["Workflow Transition"][0].condition = f"frappe.session.user == '{B}'"
        native_query = runtime.condition_namespace.db.get_list
        self.assertEqual(runtime.ids(), [])
        self.assertEqual(runtime.frappe.session.user, A)
        self.assertEqual(runtime.condition_namespace.session.user, A)
        self.assertEqual(runtime.condition_namespace.db.get_list, native_query)
        runtime.frappe.session.user = B
        self.assertFalse(runtime.approvals()["items"][0]["routing"]["fallback"])
        self.assertEqual(runtime.condition_namespace.session.user, A)

    def test_document_condition_is_rechecked_for_changed_parent_values(self):
        runtime = RoutingRuntime([{"type": "user", "user": A}])
        runtime.data["Material Request"][0].total = 50
        runtime.data["Workflow Transition"][0].condition = "doc.total > 100"
        self.assertTrue(runtime.approvals()["items"][0]["routing"]["fallback"])
        runtime.data["Material Request"][0].total = 150
        self.assertFalse(runtime.approvals()["items"][0]["routing"]["fallback"])

    def test_condition_query_defaults_to_recipient_and_preserves_explicit_user(self):
        runtime = RoutingRuntime([{"type": "user", "user": A}, {"type": "user", "user": B}])
        runtime.data["Workflow Transition"][0].condition = "frappe.db.get_list('Material Request')"
        self.assertEqual(runtime.ids(), [])
        self.assertEqual({call[1]["user"] for call in runtime.condition_queries}, {A, B})
        runtime.data["Workflow Transition"][0].condition = f"frappe.db.get_list('Material Request', user='{B}')"
        runtime.condition_queries.clear()
        self.assertFalse(runtime.approvals()["items"][0]["routing"]["fallback"])
        self.assertEqual({call[1]["user"] for call in runtime.condition_queries}, {B})

    def test_expected_condition_errors_fall_back_and_restore_only_local_messages(self):
        runtime = RoutingRuntime([{"type": "user", "user": B}])
        runtime.data["Workflow Transition"][0].condition = "bad_condition"

        def fail_expected(*args):
            runtime.frappe.message_log.append({"message": "رفض المستلم"})
            runtime.frappe.flags.error_message = "denied"
            raise runtime.frappe.ValidationError("invalid condition")

        runtime.frappe.safe_eval = fail_expected
        self.assertTrue(runtime.approvals()["items"][0]["routing"]["fallback"])
        self.assertEqual(runtime.frappe.message_log, [{"message": "سابق"}])
        self.assertEqual(runtime.frappe.flags.error_message, "previous")
        for condition in ("missing_name", "doc.absent.attribute", "bad("):
            runtime.frappe.safe_eval = runtime.safe_eval
            runtime.data["Workflow Transition"][0].condition = condition
            self.assertTrue(runtime.approvals()["items"][0]["routing"]["fallback"])

    def test_unexpected_condition_database_error_is_not_silenced(self):
        runtime = RoutingRuntime([{"type": "user", "user": A}])
        runtime.data["Workflow Transition"][0].condition = "db_condition"
        runtime.frappe.safe_eval = lambda *args: (_ for _ in ()).throw(RuntimeError("database unavailable"))
        with self.assertRaisesRegex(RuntimeError, "database unavailable"):
            runtime.approvals()

    def test_numeric_null_lookup_and_arithmetic_expression_failures_fall_back(self):
        runtime = RoutingRuntime([{"type": "user", "user": B}])
        runtime.data["Material Request"][0].total = None
        for condition in ("doc.total > 100", "doc['missing']", "[][0]", "1 / 0"):
            runtime.data["Workflow Transition"][0].condition = condition
            with self.subTest(condition=condition):
                self.assertTrue(runtime.approvals()["items"][0]["routing"]["fallback"])
                self.assertEqual(runtime.service._approval_counts(), {"open": 1})
                self.assertEqual(runtime.frappe.message_log, [{"message": "سابق"}])
                self.assertEqual(runtime.frappe.flags.error_message, "previous")

    def test_value_error_from_expression_is_quiet_but_database_errors_propagate(self):
        runtime = RoutingRuntime([{"type": "user", "user": B}])
        runtime.data["Workflow Transition"][0].condition = "expression"

        def fail_expression(*args):
            runtime.frappe.message_log.append({"message": "خطأ تعبير"})
            runtime.frappe.flags.error_message = "expression"
            raise ValueError("invalid conversion")

        runtime.frappe.safe_eval = fail_expression
        self.assertTrue(runtime.approvals()["items"][0]["routing"]["fallback"])
        self.assertEqual(runtime.frappe.message_log, [{"message": "سابق"}])
        self.assertEqual(runtime.frappe.flags.error_message, "previous")
        for error_name in ("OperationalError", "ProgrammingError"):
            error_type = type(error_name, (Exception,), {})
            runtime.frappe.safe_eval = lambda *args: (_ for _ in ()).throw(error_type("database failure"))
            with self.subTest(error=error_name):
                with self.assertRaisesRegex(error_type, "database failure"):
                    runtime.approvals()

    def test_condition_results_are_cached_per_reference_recipient_and_expression(self):
        runtime = RoutingRuntime([{"type": "user", "user": A}, {"type": "owner"}, {"type": "role", "role": "Branch"}])
        runtime.data["Workflow Transition"][0].condition = "doc.owner == frappe.session.user"
        runtime.approvals()
        self.assertEqual(Counter(runtime.condition_evaluations)[("doc.owner == frappe.session.user", A)], 1)
        self.assertEqual(runtime.frappe.session.user, A)

    def test_stored_automatic_administrator_role_is_not_inherited_by_regular_users(self):
        runtime = RoutingRuntime([{"type": "user", "user": B}])
        runtime.data["Has Role"].append(FakeFrappeDict(parent=B, parenttype="User", role="Administrator"))
        runtime.data["Workflow Action Permitted Role"][0].role = "Administrator"
        runtime.data["Workflow Transition"][0].allowed = "Administrator"
        self.assertTrue(runtime.approvals()["items"][0]["routing"]["fallback"])

    def test_administrator_still_requires_matching_snapshot_and_current_transition_roles(self):
        runtime = RoutingRuntime([{"type": "user", "user": "Administrator"}])
        runtime.data["User"].append(FakeFrappeDict(name="Administrator", enabled=1, user_type="System User"))
        runtime.data["Workflow Transition"][0].allowed = "Other"
        self.assertTrue(runtime.approvals()["items"][0]["routing"]["fallback"])

    def test_administrator_can_self_approve_when_transition_roles_intersect(self):
        runtime = RoutingRuntime([{"type": "owner"}])
        runtime.data["User"].append(FakeFrappeDict(name="Administrator", enabled=1, user_type="System User"))
        runtime.data["Material Request"][0].owner = "Administrator"
        runtime.data["Workflow Transition"][0].allow_self_approval = 0
        runtime.frappe.session.user = "Administrator"
        self.assertFalse(runtime.approvals()["items"][0]["routing"]["fallback"])

    def test_search_pages_counts_share_routing_and_no_total_cap(self):
        runtime = RoutingRuntime([{"type": "field", "field": "responsible_user"}], size=1107)
        for index, doc in enumerate(runtime.data["Material Request"]):
            doc.responsible_user = A if index % 2 == 0 else B
        result = runtime.approvals(search="WA-01", search_scope="document", limit_start=2, page_length=3)
        self.assertEqual(result["counts"], {"open": 554})
        self.assertEqual([item["name"] for item in result["items"]], ["WA-01004", "WA-01006", "WA-01008"])
        self.assertTrue(result["has_more"])
        final_query = [call for call in runtime.calls if call[0] == "list"][-1][2]
        self.assertEqual(final_query["or_filters"], [["Workflow Action", "reference_name", "like", "%WA-01%"]])
        self.assertEqual(final_query["limit_start"], 2)
        queries = Counter(doctype for kind, doctype, _ in runtime.calls)
        self.assertEqual(queries["Workflow Action"], 3)
        self.assertEqual(queries["Material Request"], 2)
        self.assertEqual(queries["User"], 1)
        self.assertEqual(queries["Has Role"], 1)
        self.assertEqual(runtime.service._approval_counts(), {"open": 554})

    def test_rules_reloaded_per_request_and_scoped_to_active_workflow_state(self):
        runtime = RoutingRuntime([{"type": "user", "user": A}])
        self.assertEqual(len(runtime.ids()), 1)
        runtime.set_targets([{"type": "user", "user": B}])
        self.assertEqual(runtime.ids(), [])
        runtime.data["Workflow"][0].is_active = 0
        self.assertEqual(len(runtime.ids()), 1)
        runtime.data["Workflow"][0].is_active = 1
        runtime.data["Workflow Document State"][0].state = "Different State"
        self.assertEqual(len(runtime.ids()), 1)

    def test_malformed_saved_rule_falls_back_to_role_and_never_crashes(self):
        runtime = RoutingRuntime()
        runtime.data["Workflow Document State"][0][FIELD] = "{broken"
        self.assertTrue(runtime.approvals()["items"][0]["routing"]["fallback"])

    def test_hide_without_targets_overrides_fallback_and_rejects_detail_without_source_reads(self):
        for value in (None, "{broken", RoutingRuntime.setting([{"type": "owner"}])):
            runtime = RoutingRuntime()
            runtime.data["Workflow Document State"][0].update({HIDE: 1, FIELD: value})
            with self.subTest(setting=value):
                result = runtime.approvals()
                self.assertEqual(result["items"], [])
                self.assertEqual(result["counts"], {"open": 0})
                with self.assertRaisesRegex(runtime.frappe.PermissionError, "الموافقة غير متاحة"):
                    runtime.service.get_approval_detail("WA-00000")
                self.assertEqual(runtime.doc_reads, [])
                self.assertEqual(runtime.constructed_docs, [])
                self.assertFalse(any(call[1] in ("Material Request", "User", "Has Role", "Workflow Transition") for call in runtime.calls))
                self.assertEqual(runtime.data["Workflow Action"][0].status, "Open")

    def test_hidden_rules_are_scoped_to_actual_doctype_state_pairs(self):
        runtime = RoutingRuntime()
        runtime.data["Workflow Document State"][0][HIDE] = 1
        runtime.data["Workflow"].append(FakeFrappeDict(name="WF-SO", document_type="Sales Order", is_active=1))
        runtime.data["Workflow Document State"].append(FakeFrappeDict(
            parent="WF-SO", parenttype="Workflow", state="Another State", **{HIDE: 1},
        ))
        action = workflow_action("SO-PENDING")
        action.reference_doctype = "Sales Order"
        runtime.data["Workflow Action"].append(action)
        result = runtime.approvals()
        self.assertEqual([row["name"] for row in result["items"]], ["SO-PENDING"])
        self.assertEqual(result["counts"], {"open": 1})
        self.assertEqual(result["items"][0]["routing"]["mode"], "Role")

    def test_exclusions_preserve_mixed_default_pages_and_search_counts(self):
        runtime = RoutingRuntime([{"type": "owner"}], size=6)
        for index, (action, reference) in enumerate(zip(runtime.data["Workflow Action"], runtime.data["Material Request"])):
            if index % 2:
                action.workflow_state = "Unconfigured State"
            elif index != 2:
                reference.owner = B
        runtime.denied.add("WA-00000")
        result = runtime.approvals(limit_start=1, page_length=2)
        self.assertEqual(result["counts"], {"open": 4})
        self.assertEqual([row["name"] for row in result["items"]], ["WA-00002", "WA-00003"])
        self.assertTrue(result["has_more"])
        self.assertEqual(result["items"][0]["routing"]["mode"], "Targets")
        self.assertEqual(result["items"][1]["routing"]["mode"], "Role")
        final_query = [options for kind, doctype, options in runtime.calls if kind == "list"][-1]
        self.assertEqual(final_query["filters"]["name"], ["not in", ["WA-00004"]])
        searched = runtime.approvals(search="WA-00005", search_scope="document", page_length=1)
        self.assertEqual(searched["counts"], {"open": 4})
        self.assertEqual([row["name"] for row in searched["items"]], ["WA-00005"])
        self.assertFalse(searched["has_more"])

    def test_7500_actions_hide_4250_without_any_parent_or_child_loading(self):
        runtime = RoutingRuntime([{"type": "owner"}], size=7500)
        runtime.data["Workflow Document State"][0][HIDE] = 1
        runtime.child_tables["Material Request"] = [("items", "Material Request Item")]
        for action in runtime.data["Workflow Action"][4250:]:
            action.workflow_state = "Unconfigured State"
        result = runtime.approvals(page_length=25)
        self.assertEqual(result["counts"], {"open": 3250})
        self.assertEqual(result["items"][0]["name"], "WA-04250")
        self.assertEqual(len(result["items"]), 25)
        self.assertTrue(result["has_more"])
        self.assertEqual(runtime.constructed_docs, [])
        self.assertEqual(runtime.permission_reads, [])
        self.assertFalse(any(doctype in ("Material Request", "Material Request Item", "User", "Has Role") for _, doctype, _ in runtime.calls))
        action_queries = [options for kind, doctype, options in runtime.calls if kind == "list"]
        self.assertEqual(len(action_queries), 5)  # Aggregate + 3 affected batches + final page.
        self.assertEqual(action_queries[0]["fields"], ["count(name) as count"])
        self.assertTrue(all(options["filters"].get("workflow_state") == ["in", ["Pending Approval"]] for options in action_queries[1:-1]))
        self.assertEqual(len(action_queries[-1]["filters"]["name"][1]), 4250)

    def test_7500_actions_only_preload_headers_for_4250_ineligible_owners(self):
        runtime = RoutingRuntime([{"type": "owner"}], size=7500)
        runtime.child_tables["Material Request"] = [("items", "Material Request Item")]
        runtime.data["Has Role"] = [row for row in runtime.data["Has Role"] if row.parent != C]
        for index, (action, reference) in enumerate(zip(runtime.data["Workflow Action"], runtime.data["Material Request"])):
            if index >= 4250:
                action.workflow_state = "Unconfigured State"
            reference.owner = C
        result = runtime.approvals(page_length=1)
        self.assertEqual(result["counts"], {"open": 7500})
        self.assertTrue(result["items"][0]["routing"]["fallback"])
        parent_queries = [options for kind, doctype, options in runtime.calls if doctype == "Material Request"]
        self.assertEqual(len(parent_queries), 3)
        self.assertEqual(sum(len(options["filters"]["name"][1]) for options in parent_queries), 4250)
        self.assertTrue(all(options["fields"] == ["name", "owner"] for options in parent_queries))
        self.assertFalse(any(doctype == "Material Request Item" for _, doctype, _ in runtime.calls))
        self.assertEqual(runtime.constructed_docs, [])
        self.assertEqual(runtime.permission_reads, [])
        final_query = [options for kind, doctype, options in runtime.calls if kind == "list"][-1]
        self.assertNotIn("name", final_query["filters"])

    def test_full_reference_loading_only_for_initially_eligible_candidates(self):
        runtime = RoutingRuntime([{"type": "owner"}], size=3)
        runtime.child_tables["Material Request"] = [("items", "Material Request Item")]
        runtime.data["Material Request"][1].owner = "missing@example.com"
        runtime.data["Material Request"][2].owner = "missing@example.com"
        result = runtime.approvals()
        self.assertEqual(result["counts"], {"open": 3})
        parent_queries = [options for _, doctype, options in runtime.calls if doctype == "Material Request"]
        self.assertEqual(parent_queries[0]["fields"], ["name", "owner"])
        self.assertEqual(len(parent_queries[0]["filters"]["name"][1]), 3)
        self.assertEqual(parent_queries[1]["fields"], ["*"])
        self.assertEqual(parent_queries[1]["filters"]["name"][1], [runtime.data["Material Request"][0].name])
        self.assertEqual(len(runtime.constructed_docs), 1)
        self.assertEqual(len(runtime.permission_reads), 1)

    def test_unhide_is_rechecked_next_request_and_preserves_saved_targets(self):
        runtime = RoutingRuntime([{"type": "user", "user": B}])
        state = runtime.data["Workflow Document State"][0]
        original_targets = state[FIELD]
        state[HIDE] = 1
        self.assertEqual(runtime.approvals()["counts"], {"open": 0})
        state[HIDE] = 0
        self.assertEqual(runtime.ids(), [])
        runtime.frappe.session.user = B
        self.assertEqual(runtime.approvals()["counts"], {"open": 1})
        self.assertEqual(state[FIELD], original_targets)

    def test_fixed_user_skips_headers_but_rechecks_self_approval_on_full_reference(self):
        runtime = RoutingRuntime([{"type": "user", "user": A}], size=2)
        runtime.data["Workflow Transition"][0].allow_self_approval = 0
        runtime.data["Material Request"][1].owner = B
        result = runtime.approvals()
        self.assertTrue(result["items"][0]["routing"]["fallback"])
        self.assertFalse(result["items"][1]["routing"]["fallback"])
        parent_queries = [options for _, doctype, options in runtime.calls if doctype == "Material Request"]
        self.assertEqual(len(parent_queries), 1)
        self.assertEqual(parent_queries[0]["fields"], ["*"])
        self.assertEqual(len(runtime.constructed_docs), 2)

    def test_fixed_role_coarse_candidates_cannot_bypass_reference_owner_rules(self):
        runtime = RoutingRuntime([{"type": "role", "role": "Branch"}], size=2)
        runtime.data["Workflow Transition"][0].allow_self_approval = 0
        runtime.data["Material Request"][1].owner = B
        result = runtime.approvals()
        self.assertEqual([row["name"] for row in result["items"]], ["WA-00001"])
        self.assertEqual(result["counts"], {"open": 1})
        parent_queries = [options for _, doctype, options in runtime.calls if doctype == "Material Request"]
        self.assertEqual(len(parent_queries), 1)
        self.assertEqual(parent_queries[0]["fields"], ["*"])

    def test_mixed_fixed_and_dynamic_targets_still_read_headers(self):
        for dynamic in ({"type": "owner"}, {"type": "field", "field": "responsible_user"}):
            runtime = RoutingRuntime([{"type": "user", "user": A}, dynamic])
            runtime.approvals()
            parent_queries = [options for _, doctype, options in runtime.calls if doctype == "Material Request"]
            with self.subTest(dynamic=dynamic):
                self.assertNotEqual(parent_queries[0]["fields"], ["*"])
                self.assertIn("owner", parent_queries[0]["fields"])
                if dynamic["type"] == "field":
                    self.assertIn("responsible_user", parent_queries[0]["fields"])

    def test_children_sort_per_parent_and_field_with_sql_null_first_semantics(self):
        runtime = RoutingRuntime([{"type": "user", "user": A}], size=2)
        runtime.child_tables["Material Request"] = [("items", "Material Request Item"), ("alternatives", "Material Request Item")]
        children = []
        expected = {}
        for parent in runtime.data["Material Request"]:
            for field in ("items", "alternatives"):
                expected[(parent.name, field)] = []
                for suffix, idx in (("three", 3), ("zero", 0), ("null", None), ("two", 2), ("one", 1)):
                    child = FakeFrappeDict(
                        name=f"{parent.name}-{field}-{suffix}", parent=parent.name,
                        parenttype="Material Request", parentfield=field, idx=idx,
                        warehouse=f"WH-{suffix}", qty=13,
                    )
                    children.append(child)
                expected[(parent.name, field)] = [f"{parent.name}-{field}-{suffix}" for suffix in ("null", "zero", "one", "two", "three")]
        runtime.data["Material Request Item"] = list(reversed(children))
        runtime.approvals()
        for doc in runtime.constructed_docs:
            for field in ("items", "alternatives"):
                self.assertEqual([row["name"] for row in doc[field]], expected[(doc["name"], field)])
                self.assertTrue(all(row["qty"] == 13 and row["warehouse"].startswith("WH-") for row in doc[field]))
        for kind, doctype, options in runtime.calls:
            if kind == "all":
                self.assertIn("order_by", options)
                self.assertIsNone(options["order_by"])
            elif options.get("limit_page_length") == 2000:
                self.assertEqual(options["order_by"], "name asc")

    def test_4250_fixed_targets_keep_bounded_batches_and_skip_all_header_queries(self):
        runtime = RoutingRuntime([{"type": "user", "user": A}], size=4250)
        runtime.approvals(page_length=25)
        parent_queries = [options for _, doctype, options in runtime.calls if doctype == "Material Request"]
        self.assertEqual(len(parent_queries), 3)
        self.assertTrue(all(options["fields"] == ["*"] for options in parent_queries))
        sizes = [len(options["filters"]["name"][1]) for options in parent_queries]
        self.assertEqual(sorted(sizes), [250, 2000, 2000])
        self.assertEqual(len(runtime.constructed_docs), 4250)
        self.assertEqual(len(runtime.permission_reads), 4250)

    def test_bulk_visibility_checks_viewer_before_other_targets_in_the_whole_union(self):
        alternatives = (
            [{"type": "user", "user": B}, {"type": "user", "user": A}],
            [{"type": "role", "role": "Only B"}, {"type": "user", "user": A}],
            [{"type": "user", "user": B}, {"type": "role", "role": "Branch"}],
        )
        for targets in alternatives:
            for sequence in (targets, list(reversed(targets))):
                runtime = RoutingRuntime(sequence)
                runtime.data["Role"].append(FakeFrappeDict(name="Only B"))
                runtime.data["Has Role"].append(FakeFrappeDict(parent=B, parenttype="User", role="Only B"))
                with self.subTest(targets=sequence):
                    self.assertEqual(runtime.service._approval_counts(), {"open": 1})
                    self.assertEqual([user for _, _, user in runtime.permission_reads], [A])

    def test_bulk_excludes_only_after_viewer_fails_and_another_recipient_passes(self):
        for failure in ("read", "self", "condition", "all"):
            runtime = RoutingRuntime([{"type": "user", "user": B}, {"type": "user", "user": A}])
            reference = runtime.data["Material Request"][0]
            if failure in ("read", "all"):
                runtime.read_denied.add((reference.name, A))
            if failure == "all":
                runtime.read_denied.add((reference.name, B))
            elif failure == "self":
                runtime.data["Workflow Transition"][0].allow_self_approval = 0
            elif failure == "condition":
                runtime.data["Workflow Transition"][0].condition = f"frappe.session.user == '{B}'"
            with self.subTest(failure=failure):
                self.assertEqual(runtime.service._approval_counts(), {"open": 1 if failure == "all" else 0})
                self.assertEqual([user for _, _, user in runtime.permission_reads], [A, B])

    def test_bulk_decision_is_boolean_only_but_page_metadata_contains_all_valid_targets(self):
        runtime = RoutingRuntime([{"type": "user", "user": B}, {"type": "user", "user": A}], size=60)
        expected_resolver = runtime.service.ApprovalRoutingResolver(runtime.frappe, A, runtime.service.get_workflow_safe_globals)
        expected = expected_resolver.route_rows(runtime.data["Workflow Action"])
        runtime.permission_reads.clear()
        runtime.constructed_docs.clear()
        resolver = runtime.service.ApprovalRoutingResolver(runtime.frappe, A, runtime.service.get_workflow_safe_globals)
        visibility = resolver.exclusions()
        self.assertEqual(visibility.excluded_names, set())
        self.assertFalse(hasattr(visibility, "routing"))
        self.assertEqual(Counter(user for _, _, user in runtime.permission_reads), {A: 60})
        calls_after_bulk = len(runtime.calls)
        page_rows = runtime.data["Workflow Action"][:2]
        page = resolver.route_rows(page_rows)
        self.assertEqual(page, {row.name: expected[row.name] for row in page_rows})
        self.assertEqual([target["user"] for target in page[page_rows[0].name]["targets"]], [B, A])
        self.assertEqual(Counter(user for _, _, user in runtime.permission_reads), {A: 60, B: 2})
        self.assertEqual(len(runtime.calls), calls_after_bulk)
        self.assertEqual(len(runtime.constructed_docs), 60)

    def test_boolean_decisions_match_full_routing_for_mixed_rules_and_native_guards(self):
        alternatives = (
            [{"type": "user", "user": B}, {"type": "owner"}],
            [{"type": "role", "role": "Branch"}, {"type": "field", "field": "responsible_user"}],
            [{"type": "user", "user": "missing@example.com"}, {"type": "user", "user": B}],
        )
        for targets in alternatives:
            for self_approval in (0, 1):
                runtime = RoutingRuntime(targets, size=7)
                runtime.data["Workflow Transition"][0].allow_self_approval = self_approval
                runtime.data["Workflow Transition"][0].condition = "doc.owner == frappe.session.user or doc.responsible_user == frappe.session.user"
                for index, reference in enumerate(runtime.data["Material Request"]):
                    reference.owner = (A, B, C)[index % 3]
                    reference.responsible_user = (B, C, A)[index % 3]
                    if index % 2:
                        runtime.read_denied.add((reference.name, A))
                    if index % 3 == 0:
                        runtime.read_denied.add((reference.name, B))
                resolver = runtime.service.ApprovalRoutingResolver(runtime.frappe, A, runtime.service.get_workflow_safe_globals)
                full = resolver.route_rows(runtime.data["Workflow Action"])
                boolean = runtime.service.ApprovalRoutingResolver(runtime.frappe, A, runtime.service.get_workflow_safe_globals)
                excluded = boolean.exclusions().excluded_names
                with self.subTest(targets=targets, self_approval=self_approval):
                    self.assertEqual(excluded, {row.name for row in runtime.data["Workflow Action"]} - full.keys())
                    self.assertEqual(boolean.route_rows([row for row in runtime.data["Workflow Action"] if row.name not in excluded]), full)


@contextmanager
def projected_child_reader(runtime, child_doctype, columns):
    """Exercise the compiled PyPika SELECT against a local collation model.

    Only MariaDB's BINARY cast is translated to SQLite BLOB for execution;
    SELECT/WHERE/CASE and quoting come from the production builder unchanged.
    Production Frappe additionally binds query values when its QB.run walks it.
    """
    runtime.projection_sql = []
    runtime.projected_transport = []
    runtime.frappe.db.db_type = "mariadb"
    runtime.frappe.db.get_table_columns = lambda doctype: list(columns)

    class Query(MySQLQueryBuilder):
        def run(self, *, as_dict):
            sql = self.get_sql()
            runtime.projection_sql.append(sql)
            connection = sqlite3.connect(":memory:")
            connection.row_factory = sqlite3.Row
            connection.create_collation("FRAPPE_TEST_CI", lambda a, b: (a.rstrip().casefold() > b.rstrip().casefold()) - (a.rstrip().casefold() < b.rstrip().casefold()))
            table = '"' + ("tab" + child_doctype).replace('"', '""') + '"'
            quoted = ['"' + column.replace('"', '""') + '"' for column in columns]
            declarations = [name + (" INTEGER" if column in ("idx", "qty") else " TEXT COLLATE FRAPPE_TEST_CI") for name, column in zip(quoted, columns)]
            connection.execute("CREATE TABLE " + table + " (" + ",".join(declarations) + ")")
            connection.executemany(
                "INSERT INTO " + table + " VALUES (" + ",".join("?" for _ in columns) + ")",
                [[row.get(column) for column in columns] for row in runtime.data[child_doctype]],
            )
            rows = [FakeFrappeDict(row) for row in connection.execute(sql.replace(" AS BINARY)", " AS BLOB)"))]
            connection.close()
            runtime.projected_transport.extend(FakeFrappeDict(row) for row in rows)
            return rows

    runtime.frappe.qb = SimpleNamespace(DocType=lambda doctype: Table("tab" + doctype), from_=lambda table: Query().from_(table))
    query_module = ModuleType("frappe.query_builder")
    query_module.Case = Case
    functions_module = ModuleType("frappe.query_builder.functions")
    functions_module.Cast = Cast
    with patch.dict(sys.modules, {"frappe.query_builder": query_module, "frappe.query_builder.functions": functions_module}):
        yield


@unittest.skipIf(MySQLQueryBuilder is None, "PyPika is supplied by the Frappe runtime")
class ChildProjectionTestCase(unittest.TestCase):
    def make_runtime(self):
        runtime = RoutingRuntime([{"type": "user", "user": A}])
        child_doctype = "Material Request Item"
        runtime.child_tables["Material Request"] = [("items", child_doctype)]
        parent = runtime.data["Material Request"][0].name
        rows = [FakeFrappeDict(
            name=name, parent=parent, parenttype=parenttype, parentfield=parentfield,
            idx=idx, qty=7, extra_field="كل الأعمدة محفوظة", nullable_field=None,
        ) for name, parenttype, parentfield, idx in (
            ("normal", "Material Request", "items", 2),
            ("different-case", "material request", "ITEMS", 1),
            ("trailing-space", "Material Request ", "items ", 0),
            ("null-idx", "Material Request", "items", None),
            ("wrong-type", "Sales Order", "items", 5),
            ("wrong-field", "Material Request", "other_items", 6),
            ("null-type", None, "items", 8),
        )]
        runtime.data[child_doctype] = rows
        columns = list(rows[0])
        return runtime, child_doctype, columns

    def test_projection_restores_all_values_before_constructor_and_preserves_variants(self):
        runtime, child_doctype, columns = self.make_runtime()
        with projected_child_reader(runtime, child_doctype, columns):
            runtime.approvals()
        children = runtime.constructed_docs[0]["items"]
        self.assertEqual([row["name"] for row in children], ["null-idx", "trailing-space", "different-case", "normal"])
        expected = {row["name"]: row for row in runtime.data[child_doctype]}
        for row in children:
            self.assertEqual({column: row[column] for column in columns}, expected[row["name"]])
            self.assertEqual(row["doctype"], child_doctype)
            self.assertIsNone(row["nullable_field"])
        transport = {row["name"]: row for row in runtime.projected_transport}
        self.assertIsNone(transport["normal"]["parenttype"])
        self.assertIsNone(transport["normal"]["parentfield"])
        self.assertEqual(transport["different-case"]["parenttype"], "material request")
        self.assertEqual(transport["different-case"]["parentfield"], "ITEMS")
        self.assertEqual(transport["trailing-space"]["parenttype"], "Material Request ")
        self.assertEqual(transport["trailing-space"]["parentfield"], "items ")
        sql = runtime.projection_sql[0]
        select, where = sql.split(" WHERE ", 1)
        self.assertEqual(select.count("CASE WHEN"), 2)
        self.assertNotIn("BINARY", where)
        self.assertIn("`parenttype`=", where)
        self.assertIn("`parentfield`=", where)
        self.assertIn("`parent` IN ", where)
        self.assertNotIn("ORDER BY", sql)
        self.assertEqual(len(runtime.permission_reads), 1)

    def test_scope_values_with_quotes_stay_values_in_compiled_query(self):
        runtime, child_doctype, columns = self.make_runtime()
        parenttype = "Material Request' OR '1'='1"
        parentfield = "items'; SELECT 1 --"
        wanted = runtime.data[child_doctype][0]
        wanted.parenttype = parenttype
        wanted.parentfield = parentfield
        with projected_child_reader(runtime, child_doctype, columns):
            resolver = runtime.service.ApprovalRoutingResolver(runtime.frappe, A, runtime.service.get_workflow_safe_globals)
            rows = resolver._child_rows(child_doctype, parenttype, parentfield, [wanted.parent])
        self.assertEqual([row["name"] for row in rows], ["normal"])
        self.assertEqual(rows[0]["parenttype"], parenttype)
        self.assertEqual(rows[0]["parentfield"], parentfield)

    def test_other_databases_and_nonstandard_columns_keep_the_original_reader(self):
        for mode in ("postgres", "unusual-column", "arabic-doctype", "punctuation-doctype"):
            runtime, child_doctype, columns = self.make_runtime()
            if mode in ("arabic-doctype", "punctuation-doctype"):
                replacement = "بنود طلب المواد" if mode == "arabic-doctype" else "Material Request Item (Legacy)"
                runtime.data[replacement] = runtime.data.pop(child_doctype)
                child_doctype = replacement
                runtime.child_tables["Material Request"] = [("items", child_doctype)]
            if mode == "unusual-column":
                columns.append("odd`column")
                runtime.data[child_doctype][0]["odd`column"] = "preserved"
            with projected_child_reader(runtime, child_doctype, columns):
                if mode == "postgres":
                    runtime.frappe.db.db_type = "postgres"
                runtime.approvals()
            self.assertEqual(runtime.projection_sql, [])
            calls = [options for _, doctype, options in runtime.calls if doctype == child_doctype]
            self.assertTrue(calls)
            self.assertTrue(all(options["fields"] == ["*"] for options in calls))

    def test_unicode_column_names_remain_projected_when_the_doctype_is_supported(self):
        runtime, child_doctype, columns = self.make_runtime()
        columns.append("ملاحظة")
        runtime.data[child_doctype][0]["ملاحظة"] = "قيمة محفوظة"
        with projected_child_reader(runtime, child_doctype, columns):
            runtime.approvals()
        self.assertTrue(runtime.projection_sql)
        normal = next(row for row in runtime.constructed_docs[0]["items"] if row["name"] == "normal")
        self.assertEqual(normal["ملاحظة"], "قيمة محفوظة")


if __name__ == "__main__":
    unittest.main()
