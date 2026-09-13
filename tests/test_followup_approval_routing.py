from __future__ import annotations

from collections import Counter
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_followup_approval_counts import FakeFrappeDict, load_service, workflow_action
from namar_custom.followups.logic import page_window, pagination


FIELD = "custom_followups_routing_targets"
A = "a@example.com"
B = "b@example.com"
C = "c@example.com"


class RuntimeDocument(FakeFrappeDict):
    def as_dict(self):
        return self


class RoutingRuntime:
    """Shared in-memory DB; legacy adapters below are NOT service endpoints.

    The original resolver is retained as historical compatibility code only.
    Its all-document scan and automatic fallback are intentionally absent from
    HTTP. New index policy tests reuse the DB fixture, not legacy semantics.
    Service contracts live in test_approval_index_service.py.
    """

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
            has_field=lambda name: doctype == "Workflow Document State" and name == FIELD,
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

    def legacy_resolver(self):
        # load_service() initializes this module under its isolated Frappe stub;
        # patch.dict restores sys.modules afterward. Reuse that exact object.
        resolver_class = self.service.role_routing.__globals__["ApprovalRoutingResolver"]
        return resolver_class(self.frappe, self.frappe.session.user, self.service.get_workflow_safe_globals)

    def legacy_counts(self, resolver=None, visible=None):
        resolver = resolver or self.legacy_resolver()
        if resolver.has_rules:
            visible = resolver.visible_actions(list(self.service.WORKFLOW_ACTION_FIELDS)) if visible is None else visible
            return {"open": len(visible)}
        rows = self.get_list("Workflow Action", fields=["count(name) as count"], filters={"status": "Open"}, limit_page_length=1)
        return {"open": rows[0]["count"] if rows else 0}

    def approvals(self, *, search="", search_scope="all", limit_start=0, page_length=50):
        """Exercise the retired resolver directly, never get_approvals()."""
        resolver = self.legacy_resolver()
        visible = resolver.visible_actions(list(self.service.WORKFLOW_ACTION_FIELDS)) if resolver.has_rules else None
        filters = {"status": "Open"}
        if visible is not None:
            filters["name"] = ["in", list(visible)]
        start, length, query_length = page_window(limit_start, page_length)
        rows = [] if visible == {} else self.get_list(
            "Workflow Action", fields=list(self.service.WORKFLOW_ACTION_FIELDS),
            filters=filters, or_filters=self.service._approval_search_filters(search, search_scope),
            order_by="modified desc", limit_start=start, limit_page_length=query_length,
        )
        result = pagination([
            dict(row, routing=visible[row["name"]] if visible is not None else self.service.role_routing())
            for row in rows
        ], start, length)
        result["counts"] = self.legacy_counts(resolver, visible)
        return result

    def legacy_detail_routing(self, name):
        rows = self.get_list("Workflow Action", fields=list(self.service.WORKFLOW_ACTION_FIELDS), filters={"name": name, "status": "Open"}, limit_page_length=1)
        if not rows:
            raise self.frappe.PermissionError("الموافقة غير متاحة")
        visible = self.legacy_resolver().route_rows(rows)
        if name not in visible:
            raise self.frappe.PermissionError("الموافقة غير متاحة")
        return {"approval": {"routing": visible[name]}}

    def ids(self, **kwargs):
        return [row["name"] for row in self.approvals(**kwargs)["items"]]


class LegacyApprovalResolverTestCase(unittest.TestCase):
    """Historical resolver-only tests, not evidence of live endpoint behavior."""
    def test_site_switch_skips_routing_queries_and_keeps_native_permissions(self):
        runtime = RoutingRuntime([{"type": "owner"}], size=4)
        saved = runtime.data["Workflow Document State"][0][FIELD]
        runtime.frappe.conf.disable_followup_approval_routing = True
        runtime.denied.add("WA-00000")
        with patch.object(runtime.frappe, "get_meta", side_effect=AssertionError("Routing metadata must not load")):
            self.assertEqual(runtime.legacy_counts(), {"open": 3})
            result = runtime.approvals(page_length=2)
        self.assertEqual(result["counts"], {"open": 3})
        self.assertEqual(len(result["items"]), 2)
        self.assertTrue(result["has_more"])
        self.assertTrue(all(item["routing"]["mode"] == "Role" for item in result["items"]))
        self.assertTrue(all(kind == "list" and doctype == "Workflow Action" for kind, doctype, _ in runtime.calls))
        self.assertEqual(runtime.doc_reads, [])
        self.assertEqual(runtime.permission_reads, [])
        self.assertEqual(runtime.data["Workflow Document State"][0][FIELD], saved)

    def test_missing_or_false_site_switch_preserves_saved_recipients(self):
        for config in ({}, {"disable_followup_approval_routing": False}):
            runtime = RoutingRuntime([{"type": "user", "user": B}])
            runtime.frappe.conf.update(config)
            with self.subTest(config=config):
                self.assertEqual(runtime.ids(), [])
                runtime.frappe.session.user = B
                self.assertEqual(runtime.ids(), ["WA-00000"])

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
            self.assertEqual(runtime.legacy_counts(), {"open": 1})
        runtime.frappe.session.user = C
        self.assertEqual(runtime.ids(), [])
        self.assertEqual(runtime.legacy_counts(), {"open": 0})

    def test_target_user_never_expands_base_permission(self):
        runtime = RoutingRuntime([{"type": "user", "user": A}])
        runtime.denied.add("WA-00000")
        self.assertEqual(runtime.ids(), [])
        self.assertEqual(runtime.legacy_counts(), {"open": 0})
        self.assertFalse(any(call[1] == "Material Request" for call in runtime.calls))
        with self.assertRaises(runtime.frappe.PermissionError):
            runtime.legacy_detail_routing("WA-00000")
        self.assertEqual(runtime.doc_reads, [])

    def test_hidden_detail_rejected_without_loading_reference_or_changing_roles(self):
        runtime = RoutingRuntime([{"type": "user", "user": B}])
        before_roles = list(runtime.data["Has Role"])
        with self.assertRaisesRegex(runtime.frappe.PermissionError, "الموافقة غير متاحة"):
            runtime.legacy_detail_routing("WA-00000")
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
            detail = runtime.legacy_detail_routing(action.name)
        self.assertEqual(detail["approval"]["routing"], list_routing)

    def test_legacy_count_and_list_use_same_routed_open_actions(self):
        runtime = RoutingRuntime([{"type": "user", "user": B}])
        for user, expected in ((A, 0), (B, 1)):
            runtime.frappe.session.user = user
            self.assertEqual(runtime.legacy_counts()["open"], expected)
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
                self.assertEqual(runtime.legacy_counts(), {"open": 1})
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
        self.assertEqual(queries["Workflow Action"], 4)
        self.assertEqual(queries["Material Request"], 3)
        self.assertEqual(queries["User"], 1)
        self.assertEqual(queries["Has Role"], 1)
        self.assertEqual(runtime.legacy_counts(), {"open": 554})

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


if __name__ == "__main__":
    unittest.main()
