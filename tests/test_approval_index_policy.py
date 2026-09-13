from __future__ import annotations

import json
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_followup_approval_routing import A, B, C, RoutingRuntime, RuntimeDocument
from test_followup_approval_counts import FakeFrappeDict


class IndexPolicyRuntime(RoutingRuntime):
    def __init__(self, targets=(), size=1):
        super().__init__(targets, size=size)
        with patch.dict(sys.modules, {"frappe": self.frappe}):
            from namar_custom.followups.approval_index_policy import ApprovalIndexPolicyEvaluator
        self.evaluator_class = ApprovalIndexPolicyEvaluator
        self.data["Workflow"][0].update(workflow_state_field="workflow_state", modified="policy-v1")
        for action in self.data["Workflow Action"]:
            action["doctype"] = "Workflow Action"
        for reference in self.data["Material Request"]:
            reference.update(doctype="Material Request", workflow_state="Pending Approval", modified="reference-v1", grand_total=120)
        self.frappe.get_roles = self.get_roles
        self.hooks = {}
        self.frappe.get_hooks = lambda hook: self.hooks.get(hook, {})

    def get_doc(self, doctype, name=None):
        self.doc_reads.append((doctype, name))
        row = next((row for row in self.data.get(doctype, ()) if row.get("name") == name), None)
        if not row:
            raise self.frappe.DoesNotExistError(name)
        doc = RuntimeDocument(row)
        if doctype == "Workflow":
            doc["states"] = self.data["Workflow Document State"]
            doc["transitions"] = self.data["Workflow Transition"]
        elif doctype == "Workflow Action":
            doc["permitted_roles"] = [row for row in self.data["Workflow Action Permitted Role"] if row.parent == name]
        else:
            doc["meta"] = SimpleNamespace(
                fields=[SimpleNamespace(fieldname=field, fieldtype="Link", options="User") for field in self.user_fields],
                get_title_field=lambda: "name",
            )
            for field, childtype in self.child_tables.get(doctype, ()):
                doc[field] = [RuntimeDocument(child) for child in self.data[childtype] if child.get("parent") == name]
        return doc

    def get_roles(self, user):
        if user == "Administrator":
            return [row["name"] for row in self.data["Role"]]
        return [row.role for row in self.data["Has Role"] if row.parent == user] + ["All", "Guest", "Desk User"]

    def has_permission(self, doctype, permission, *, doc, user, throw=False):
        self.permission_reads.append((doctype, doc.name, user))
        if doctype == "Workflow Action":
            return doc.name not in self.denied and (user == "Administrator" or bool(set(self.get_roles(user)) & {row.role for row in doc.permitted_roles}))
        return (doc.name, user) not in self.read_denied

    def result(self, name="WA-00000"):
        return self.evaluator_class(self.frappe, lambda: {"frappe": self.condition_namespace}).evaluate_action(name)


class ConditionDependenciesTests(unittest.TestCase):
    def analyze(self, condition):
        # Runtime loads the existing Frappe test boundary before importing code.
        runtime = IndexPolicyRuntime()
        with patch.dict(sys.modules, {"frappe": runtime.frappe}):
            from namar_custom.followups.approval_index_policy import analyze_condition
        return analyze_condition(condition)

    def test_doc_fields_and_recipient_identity_are_explicit(self):
        result = self.analyze("doc.grand_total > 20 and doc.get('owner') == frappe.session.user")
        self.assertTrue(result["supported"])
        self.assertEqual(result["document_fields"], ("grand_total", "owner"))
        self.assertTrue(result["recipient_context"])

    def test_child_indexed_field_is_source_document_dependency(self):
        result = self.analyze("doc.get('items')[0].qty > 0")
        self.assertTrue(result["supported"])
        self.assertEqual(result["document_fields"], ("items",))

    def test_external_time_db_dynamic_mutating_and_private_calls_fail_closed(self):
        conditions = [
            "frappe.db.get_value('User', doc.owner, 'enabled')",
            "frappe.utils.today() > doc.transaction_date", "frappe.session.data.user",
            "doc.get(doc.field_name)", "doc.pop('owner')", "doc.__class__",
            "external_hook(doc)", "doc.owner.startswith('admin')", "(lambda: True)()",
            "any(len('User') for len in [frappe['db']['get_list']])",
            "frappe.session['data']", "frappe['session']['user']", "bool(frappe)",
            "bool(frappe.session)", "any(doc for doc in [True])", "any(len(doc) for len in [str])",
            "sum([1] * 100000000000)", "any(row.qty for row in doc.items for other in doc.items)",
            "sum([row.qty for row in doc.items])", "any(any(other.qty for other in doc.items) for row in doc.items)",
            "'%100000000000s' % 'x'", "[int('User') for int in [frappe['db']['get_list']]]",
            "[1] * 100000000000", "any(row.qty for row in doc.get('items'))",
        ]
        for condition in conditions:
            with self.subTest(condition=condition):
                self.assertFalse(self.analyze(condition)["supported"])


class ApprovalIndexPolicyTests(unittest.TestCase):
    def test_owner_resolves_only_this_action_without_loading_unrelated_actions(self):
        runtime = IndexPolicyRuntime([{"type": "owner"}], size=100)
        result = runtime.result()
        self.assertEqual(result["state"], "ready")
        self.assertEqual(result["recipients"], (A,))
        self.assertEqual(runtime.doc_reads, [("Workflow Action", "WA-00000"), ("Workflow", "WF-A"), ("Material Request", "MREQ-WA-00000")])
        self.assertFalse(any(call[1] == "Workflow Action" for call in runtime.calls))
        self.assertEqual(result["reference_modified"], "reference-v1")
        self.assertEqual(result["workflow_modified"], "policy-v1")
        self.assertEqual(len(result["fingerprint"]), 64)

    def test_owner_user_field_and_role_union_deduplicates_all_eligible_members(self):
        runtime = IndexPolicyRuntime([
            {"type": "owner"}, {"type": "field", "field": "responsible_user"},
            {"type": "user", "user": B}, {"type": "role", "role": "Approver"},
        ])
        self.assertEqual(runtime.result()["recipients"], (A, B, C))

    def test_unconfigured_uses_native_eligible_roles_not_configured_fallback(self):
        runtime = IndexPolicyRuntime()
        result = runtime.result()
        self.assertEqual(result["recipients"], (A, B, C))
        self.assertEqual(result["routing"]["mode"], "Role")
        self.assertFalse(result["routing"]["fallback"])

    def test_invalid_disabled_and_ineligible_explicit_owner_do_not_broadcast(self):
        for reason in ("disabled", "no_role", "no_read", "no_self"):
            with self.subTest(reason=reason):
                runtime = IndexPolicyRuntime([{"type": "owner"}])
                if reason == "disabled":
                    runtime.data["User"][0]["enabled"] = 0
                elif reason == "no_role":
                    runtime.data["Has Role"] = [row for row in runtime.data["Has Role"] if row.parent != A]
                elif reason == "no_read":
                    runtime.read_denied.add(("MREQ-WA-00000", A))
                else:
                    runtime.data["Workflow Transition"][0]["allow_self_approval"] = 0
                result = runtime.result()
                self.assertEqual(result["state"], "excluded")
                self.assertEqual(result["reason"], "no_eligible_recipients")
                self.assertFalse(result["routing"]["fallback"])
                self.assertEqual(result["recipients"], ())

    def test_invalid_imported_settings_do_not_fallback_or_read_source(self):
        runtime = IndexPolicyRuntime()
        runtime.data["Workflow Document State"][0]["custom_followups_routing_targets"] = "broken"
        result = runtime.result()
        self.assertEqual(result["state"], "excluded")
        self.assertEqual(result["reason"], "invalid_recipient_configuration")
        self.assertFalse(any(doctype == "Material Request" for doctype, _ in runtime.doc_reads))

    def test_hidden_stage_has_no_source_hydration_or_recipient_queries(self):
        runtime = IndexPolicyRuntime([{"type": "owner"}])
        runtime.data["Workflow Document State"][0]["custom_followups_hide_from_approvals"] = 1
        result = runtime.result()
        self.assertEqual((result["state"], result["reason"]), ("excluded", "stage_hidden"))
        self.assertFalse(any(doctype == "Material Request" for doctype, _ in runtime.doc_reads))
        self.assertFalse(any(call[1] in {"User", "Has Role"} for call in runtime.calls))

    def test_other_stage_policy_does_not_hide_this_stage(self):
        runtime = IndexPolicyRuntime([{"type": "owner"}])
        runtime.data["Workflow Document State"].append(FakeFrappeDict(state="مكتمل", custom_followups_hide_from_approvals=1))
        self.assertEqual(runtime.result()["recipients"], (A,))

    def test_terminal_stale_closed_deleted_actions_are_excluded(self):
        cases = {"terminal": "no_outgoing_transition", "stale": "stale_workflow_action", "closed": "action_closed", "deleted": "source_deleted"}
        for case, reason in cases.items():
            with self.subTest(case=case):
                runtime = IndexPolicyRuntime([{"type": "owner"}])
                if case == "terminal":
                    runtime.data["Workflow Transition"] = []
                elif case == "stale":
                    runtime.data["Material Request"][0]["workflow_state"] = "مكتمل"
                elif case == "closed":
                    runtime.data["Workflow Action"][0]["status"] = "Completed"
                else:
                    runtime.data["Material Request"] = []
                self.assertEqual((runtime.result()["state"], runtime.result()["reason"]), ("excluded", reason))

    def test_source_docstatus_excludes_cancelled_even_with_matching_open_action(self):
        for targets in ([], [{"type": "owner"}]):
            for docstatus in (0, 1, 2, "2"):
                with self.subTest(targets=targets, docstatus=docstatus):
                    runtime = IndexPolicyRuntime(targets)
                    runtime.data["Material Request"][0]["docstatus"] = docstatus
                    result = runtime.result()
                    if int(docstatus) == 2:
                        self.assertEqual((result["state"], result["reason"]), ("excluded", "source_cancelled"))
                        self.assertEqual(result["recipients"], ())
                        self.assertFalse(any(call[1] in {"User", "Has Role"} for call in runtime.calls))
                        self.assertEqual(runtime.permission_reads, [])
                    else:
                        self.assertEqual(result["state"], "ready")
                        self.assertEqual(result["recipients"], (A,) if targets else (A, B, C))
                    self.assertEqual(runtime.data["Workflow Action"][0]["status"], "Open")
                    self.assertEqual(runtime.data["Material Request"][0]["workflow_state"], "Pending Approval")

    def test_recipient_condition_context_does_not_mutate_original_session(self):
        runtime = IndexPolicyRuntime([{"type": "role", "role": "Approver"}])
        runtime.data["Workflow Transition"][0]["condition"] = "frappe.session.user == doc.responsible_user and doc.grand_total > 100"
        original = runtime.condition_namespace.session.user
        result = runtime.result()
        self.assertEqual(result["recipients"], (B,))
        self.assertEqual(runtime.condition_namespace.session.user, original)
        self.assertEqual(runtime.frappe.session.user, original)
        self.assertEqual({user for _, user in runtime.condition_evaluations}, {A, B, C})

    def test_unsupported_condition_is_visible_exception_not_empty_success(self):
        runtime = IndexPolicyRuntime([{"type": "owner"}])
        runtime.data["Workflow Transition"][0]["condition"] = "frappe.db.get_list('Material Request')"
        result = runtime.result()
        self.assertEqual(result["reason"], "unsupported_condition_dependency")
        self.assertEqual(result["state"], "error")
        self.assertEqual(runtime.condition_evaluations, [])

    def test_failed_condition_is_explicit_exception_and_not_broadcast(self):
        runtime = IndexPolicyRuntime([{"type": "owner"}])
        runtime.data["Workflow Transition"][0]["condition"] = "doc.grand_total / 0 > 1"
        result = runtime.result()
        self.assertEqual((result["state"], result["reason"]), ("error", "condition_evaluation_failed"))

    def test_child_values_are_used_for_condition_and_native_read_permission(self):
        runtime = IndexPolicyRuntime([{"type": "owner"}])
        runtime.child_tables["Material Request"] = [("items", "Material Request Item")]
        runtime.data["Material Request Item"] = [FakeFrappeDict(parent="MREQ-WA-00000", qty=2)]
        runtime.data["Workflow Transition"][0]["condition"] = "doc.get('items')[0].qty == 2"
        self.assertEqual(runtime.result()["recipients"], (A,))

    def test_index_evaluator_does_not_change_or_use_emergency_flag(self):
        runtime = IndexPolicyRuntime([{"type": "owner"}])
        runtime.frappe.conf.disable_followup_approval_routing = True
        self.assertEqual(runtime.result()["recipients"], (A,))
        self.assertTrue(runtime.frappe.conf.disable_followup_approval_routing)

    def test_unknown_permission_hooks_and_permission_scripts_fail_closed(self):
        for kind in ("has_permission", "permission_query_conditions", "override_doctype_class", "server_script"):
            with self.subTest(kind=kind):
                runtime = IndexPolicyRuntime([{"type": "owner"}])
                if kind == "server_script":
                    runtime.data["Server Script"] = [FakeFrappeDict(name="custom", disabled=0, script_type="Permission Query", reference_doctype="Material Request")]
                else:
                    runtime.hooks[kind] = {"Material Request": ["custom_permission.hook"]}
                result = runtime.result()
                self.assertEqual((result["state"], result["reason"]), ("error", "unsupported_permission_dependency"))

    def test_known_native_workflow_permission_hooks_are_allowed(self):
        runtime = IndexPolicyRuntime([{"type": "owner"}])
        runtime.hooks["has_permission"] = {"Workflow Action": ["frappe.workflow.doctype.workflow_action.workflow_action.has_permission"]}
        runtime.hooks["permission_query_conditions"] = {"Workflow Action": ["frappe.workflow.doctype.workflow_action.workflow_action.get_permission_query_conditions"]}
        self.assertEqual(runtime.result()["recipients"], (A,))

    def test_automatic_role_candidate_limit_is_checked_before_admin_filter(self):
        runtime = IndexPolicyRuntime([{"type": "role", "role": "All"}])
        runtime.data["User"] = [FakeFrappeDict(name="Administrator", enabled=1, user_type="System User")] + [
            FakeFrappeDict(name=f"user-{i}@example.com", enabled=1, user_type="System User") for i in range(5000)
        ]
        result = runtime.result()
        self.assertEqual((result["state"], result["reason"]), ("error", "recipient_candidate_limit"))
        self.assertEqual(result["recipients"], ())

    def test_short_worker_instance_reuses_only_workflow_and_permission_metadata(self):
        runtime = IndexPolicyRuntime([{"type": "owner"}], size=2)
        evaluator = runtime.evaluator_class(runtime.frappe, lambda: {"frappe": runtime.condition_namespace})
        self.assertEqual(evaluator.evaluate_action("WA-00000")["recipients"], (A,))
        self.assertEqual(evaluator.evaluate_action("WA-00001")["recipients"], (A,))
        self.assertEqual(sum(doctype == "Workflow" for doctype, _ in runtime.doc_reads), 1)
        self.assertEqual(sum(doctype == "Material Request" for doctype, _ in runtime.doc_reads), 2)
        self.assertEqual(sum(call[1] == "Server Script" for call in runtime.calls), 1)


if __name__ == "__main__":
    unittest.main()
