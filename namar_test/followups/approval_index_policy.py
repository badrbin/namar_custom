"""One-action, fail-closed recipient evaluation for the background approval index.

This module never enumerates Workflow Actions and never changes the session user.
It is a projection builder, not an approval endpoint. Native Workflow permissions
remain authoritative when an employee opens a document or executes an action.
"""

from __future__ import annotations

import ast
import hashlib
import json
from typing import Any

from namar_test.followups.approval_routing_settings import (
    FIELD_TARGET, HIDE_FIELD, OWNER_TARGET, ROLE_TARGET, ROUTING_FIELD,
    USER_TARGET, parse_routing_targets,
)
from namar_test.followups.reference_access import quiet_reference_errors


MAX_RECIPIENT_CANDIDATES = 5000
MAX_CONDITION_LENGTH = 8000
MAX_CONDITION_NODES = 800
# Match Frappe's WHITELISTED_SAFE_EVAL_GLOBALS, not Python's full builtins.
PURE_CALLS = frozenset({"int", "float", "long", "round"})
TARGET_LABELS = {USER_TARGET: "موظف محدد", OWNER_TARGET: "منشئ المستند", ROLE_TARGET: "دور محدد", FIELD_TARGET: "موظف من حقل"}
ROUTING_EXCEPTION_REASONS = frozenset({"no_eligible_recipients", "invalid_recipient_configuration", "invalid_recipient_field"})
NATIVE_PERMISSION_HOOKS = {
    "has_permission": {"frappe.workflow.doctype.workflow_action.workflow_action.has_permission"},
    "permission_query_conditions": {"frappe.workflow.doctype.workflow_action.workflow_action.get_permission_query_conditions"},
    "override_doctype_class": set(),
}


def _path(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _path(node.value)
        return f"{base}.{node.attr}" if base else ""
    return ""


def analyze_condition(condition: str | None) -> dict[str, Any]:
    """Accept only conditions whose dependencies are source data and recipient.

    Database/clock/utility/controller calls deliberately have no permissive
    fallback. They need an explicit dependency contract before being indexable.
    Child indexed fields depend on the whole source document. Comprehensions,
    generators and external/clock calls are intentionally unsupported.
    """
    result = {"supported": True, "document_fields": (), "recipient_context": False, "reason": ""}
    if not condition or not condition.strip():
        return result
    if len(condition) > MAX_CONDITION_LENGTH:
        return {**result, "supported": False, "reason": "condition_too_large"}
    try:
        tree = ast.parse(condition, mode="eval")
    except (SyntaxError, ValueError, RecursionError):
        return {**result, "supported": False, "reason": "invalid_condition"}
    nodes = list(ast.walk(tree))
    if len(nodes) > MAX_CONDITION_NODES:
        return {**result, "supported": False, "reason": "condition_too_large"}
    # A short AST can otherwise allocate unbounded containers or run a Cartesian
    # product. Initial conditions are scalar expressions/direct child lookups;
    # native safe_eval does not even expose any/all/sum to consume generators.
    if any(isinstance(node, ast.comprehension) for node in nodes):
        return {**result, "supported": False, "reason": "unsupported_generator"}
    local_names = {node.target.id for node in nodes if isinstance(node, ast.comprehension) and isinstance(node.target, ast.Name)}
    if local_names & {"doc", "frappe", *PURE_CALLS}:
        return {**result, "supported": False, "reason": "reserved_generator_target"}
    parents = {child: node for node in nodes for child in ast.iter_child_nodes(node)}
    fields = set()
    recipient = False
    allowed_nodes = (
        ast.Expression, ast.BoolOp, ast.UnaryOp, ast.BinOp, ast.Compare,
        ast.Name, ast.Load, ast.Store, ast.Constant, ast.Attribute, ast.Subscript,
        ast.List, ast.Tuple, ast.Set, ast.Dict, ast.Call, ast.keyword,
        ast.IfExp, ast.Slice, ast.And, ast.Or, ast.Not, ast.USub, ast.UAdd,
        ast.Add, ast.Sub, ast.Div, ast.FloorDiv,
        ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
        ast.In, ast.NotIn, ast.Is, ast.IsNot,
    )
    for node in nodes:
        reason = ""
        if not isinstance(node, allowed_nodes):
            reason = "unsupported_expression"
        elif isinstance(node, ast.Name) and (node.id.startswith("_") or node.id not in {"doc", "frappe", *PURE_CALLS, *local_names}):
            reason = "external_name"
        elif isinstance(node, ast.Name) and node.id == "frappe":
            # The namespace itself must never become a value or alias: an
            # expression such as frappe['db'] bypasses Attribute-only analysis.
            session = parents.get(node)
            user = parents.get(session)
            if not (isinstance(session, ast.Attribute) and session.attr == "session"
                    and isinstance(user, ast.Attribute) and user.attr == "user"):
                reason = "external_namespace"
        elif isinstance(node, ast.Attribute):
            path = _path(node)
            if node.attr.startswith("_"):
                reason = "private_attribute"
            elif path == "frappe.session":
                parent = parents.get(node)
                if not isinstance(parent, ast.Attribute) or parent.attr != "user":
                    reason = "external_namespace"
                else:
                    recipient = True
            elif path == "frappe.session.user":
                recipient = True
            elif path.startswith("frappe."):
                reason = "external_dependency"
            elif path.startswith("doc."):
                first = path.split(".")[1]
                if first != "get":
                    fields.add(first)
            elif path and path.split(".")[0] not in local_names:
                reason = "external_attribute"
        elif isinstance(node, ast.Subscript):
            if isinstance(node.value, ast.Name) and node.value.id == "doc":
                if not isinstance(node.slice, ast.Constant) or not isinstance(node.slice.value, str):
                    reason = "dynamic_document_field"
                else:
                    fields.add(node.slice.value)
        elif isinstance(node, ast.Call):
            function = _path(node.func)
            if function in PURE_CALLS:
                pass
            elif isinstance(node.func, ast.Attribute) and node.func.attr == "get" and (
                _path(node.func.value) == "doc" or _path(node.func.value) in local_names
            ):
                if not node.args or not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value, str):
                    reason = "dynamic_document_field"
                elif _path(node.func.value) == "doc":
                    fields.add(node.args[0].value)
            else:
                reason = "external_or_mutating_call"
        elif isinstance(node, ast.comprehension):
            if node.is_async or not isinstance(node.target, ast.Name):
                reason = "unsupported_generator"
        if reason:
            return {**result, "supported": False, "reason": reason}
    return {**result, "document_fields": tuple(sorted(fields)), "recipient_context": recipient}


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":")).encode()).hexdigest()


class ApprovalIndexPolicyEvaluator:
    """Evaluate a single current native Workflow Action, only in a worker.

    All permission calls receive the recipient explicitly. Caches are confined
    to this evaluator instance / worker batch, never a persistent user session.
    """

    def __init__(self, frappe, workflow_globals_factory=None):
        self.frappe = frappe
        self._workflow_globals_factory = workflow_globals_factory
        self._roles = {}
        self._workflows = {}
        self._permission_dependencies = {}

    def _result(self, action, state, reason="", **values):
        result = {
            "state": state, "recipients": (), "reason": reason,
            "routing": {"mode": "Role", "targets": [], "responsible_users": [], "fallback": False, "note": ""},
            "reference_doctype": action.get("reference_doctype"),
            "reference_name": action.get("reference_name"),
            "workflow_state": action.get("workflow_state"),
            "action_modified": str(action.get("modified") or ""),
            "reference_modified": "", "workflow_name": "", "workflow_modified": "",
            "dependencies": {"supported": True, "document_fields": (), "recipient_context": False, "reason": ""},
            **values,
        }
        result["fingerprint"] = _digest({key: value for key, value in result.items() if key != "fingerprint"})
        return result

    def evaluate_action(self, action_or_name) -> dict[str, Any]:
        action = {"name": action_or_name} if isinstance(action_or_name, str) else action_or_name
        try:
            with quiet_reference_errors(self.frappe):
                # Header dictionaries are not sufficient for native WA.has_permission.
                if not callable(getattr(action, "as_dict", None)):
                    action = self.frappe.get_doc("Workflow Action", action.get("name"))
                if action.get("status") != "Open":
                    return self._result(action, "excluded", "action_closed")
                doctype, name = action.get("reference_doctype"), action.get("reference_name")
                if not doctype or not name:
                    return self._result(action, "excluded", "missing_reference")
                workflow_count, workflow = self._active_workflow(doctype)
                if workflow_count != 1:
                    return self._result(action, "error" if workflow_count else "excluded", "ambiguous_workflow" if workflow_count else "no_active_workflow")
                metadata = {"workflow_name": workflow.name, "workflow_modified": str(workflow.get("modified") or "")}
                states = [row for row in workflow.get("states") or () if row.get("state") == action.get("workflow_state")]
                if len(states) != 1:
                    return self._result(action, "excluded", "state_not_configured", **metadata)
                state = states[0]
                if int(state.get(HIDE_FIELD) or 0):
                    return self._result(action, "excluded", "stage_hidden", **metadata)
                transitions = [row for row in workflow.get("transitions") or () if row.get("state") == action.get("workflow_state")]
                if not transitions:
                    return self._result(action, "excluded", "no_outgoing_transition", **metadata)
                try:
                    targets = parse_routing_targets(state.get(ROUTING_FIELD))
                except ValueError:
                    return self._result(action, "excluded", "invalid_recipient_configuration", **metadata)
                if not self._permission_dependencies_supported(doctype):
                    return self._result(action, "error", "unsupported_permission_dependency", **metadata)
                reference = self.frappe.get_doc(doctype, name)
                metadata["reference_modified"] = str(reference.get("modified") or "")
                if int(reference.get("docstatus") or 0) == 2:
                    return self._result(action, "excluded", "source_cancelled", **metadata)
                state_field = workflow.get("workflow_state_field")
                if not state_field or reference.get(state_field) != action.get("workflow_state"):
                    return self._result(action, "excluded", "stale_workflow_action", **metadata)
                allowed_roles = {row.get("role") for row in action.get("permitted_roles") or () if row.get("role")}
                transitions = [row for row in transitions if row.get("allowed") in allowed_roles]
                if not transitions:
                    return self._result(action, "excluded", "no_native_transition", **metadata)
                dependencies = [analyze_condition(row.get("condition")) for row in transitions]
                metadata["dependencies"] = {
                    "supported": all(row["supported"] for row in dependencies),
                    "document_fields": tuple(sorted({state_field, "owner", *(field for row in dependencies for field in row["document_fields"]), *(target["field"] for target in targets if target["type"] == FIELD_TARGET)})),
                    "recipient_context": any(row["recipient_context"] for row in dependencies),
                    "reason": next((row["reason"] for row in dependencies if not row["supported"]), ""),
                }
                if not metadata["dependencies"]["supported"]:
                    return self._result(action, "error", "unsupported_condition_dependency", **metadata)
                candidates, details, candidate_error = self._candidates(targets, reference, allowed_roles)
                if candidate_error:
                    return self._result(action, "excluded" if candidate_error in ROUTING_EXCEPTION_REASONS else "error", candidate_error, **metadata)
                recipients = []
                condition_error = False
                for user in sorted(candidates):
                    roles = self._user_roles(user)
                    usable = [row for row in transitions if row.get("allowed") in roles and (
                        user == "Administrator" or user != reference.get("owner") or int(row.get("allow_self_approval") or 0)
                    )]
                    if not usable or not self._can_read(action, reference, user):
                        continue
                    for transition in usable:
                        matched = self._condition_satisfied(reference, user, transition.get("condition"))
                        if matched is None:
                            condition_error = True
                        elif matched:
                            recipients.append(user)
                            break
                if condition_error:
                    return self._result(action, "error", "condition_evaluation_failed", **metadata)
                if targets and not recipients:
                    return self._result(action, "excluded", "no_eligible_recipients", **metadata)
                routing = {
                    "mode": "Targets" if targets else "Role", "targets": details,
                    "responsible_users": [{"user": user, "user_name": user} for user in recipients] if targets else [],
                    "fallback": False, "note": "",
                }
                title_field = reference.meta.get_title_field()
                title = str(reference.get(title_field) or name) if title_field else str(name)
                return self._result(action, "ready", recipients=tuple(recipients), routing=routing,
                    reference_title=title[:500], subject=title[:500], title=title[:500], **metadata)
        except self.frappe.DoesNotExistError:
            return self._result(action, "excluded", "source_deleted")
        except self.frappe.PermissionError:
            return self._result(action, "error", "source_access_failed")

    def _user_roles(self, user):
        if user not in self._roles:
            self._roles[user] = set(self.frappe.get_roles(user))
        return self._roles[user]

    def _active_workflow(self, doctype):
        # Instance lifetime is a bounded worker slice. A concurrent policy edit
        # bumps the engine generation, so CAS rejects this instance's old work.
        if doctype not in self._workflows:
            rows = self.frappe.get_all(
                "Workflow", fields=["name"], filters={"document_type": doctype, "is_active": 1}, limit_page_length=2,
            )
            self._workflows[doctype] = (len(rows), self.frappe.get_doc("Workflow", rows[0]["name"]) if len(rows) == 1 else None)
        return self._workflows[doctype]

    def _permission_dependencies_supported(self, doctype):
        """Unknown permission extensions cannot promise an event-complete index.

        The native Workflow Action hooks are known role-based dependencies.
        Custom permission hooks must gain an explicit invalidation contract;
        merely executing them once as Administrator is not a safe substitute.
        """
        if doctype not in self._permission_dependencies:
            self._permission_dependencies[doctype] = self._inspect_permission_dependencies(doctype)
        return self._permission_dependencies[doctype]

    def _inspect_permission_dependencies(self, doctype):
        for hook, permitted in NATIVE_PERMISSION_HOOKS.items():
            registered = self.frappe.get_hooks(hook) or {}
            if not isinstance(registered, dict):
                return False
            for target in {doctype, "Workflow Action", "*"}:
                values = registered.get(target) or ()
                values = (values,) if isinstance(values, str) else values
                if any(value not in (permitted if target == "Workflow Action" else set()) for value in values):
                    return False
        scripts = self.frappe.get_all(
            "Server Script", fields=["name"], filters={
                "disabled": 0, "script_type": "Permission Query",
                "reference_doctype": ["in", [doctype, "Workflow Action"]],
            }, limit_page_length=1,
        )
        return not scripts

    def _candidates(self, targets, reference, allowed_roles):
        explicit = set()
        selected_roles = set()
        details = []
        user_fields = {field.fieldname for field in reference.meta.fields if field.fieldtype == "Link" and field.options == "User"}
        for target in targets:
            kind = target["type"]
            detail = {**target, "label": TARGET_LABELS[kind]}
            if kind == ROLE_TARGET:
                selected_roles.add(target["role"])
            else:
                user = reference.get("owner") if kind == OWNER_TARGET else target.get("user")
                if kind == FIELD_TARGET:
                    if target["field"] not in user_fields:
                        return set(), [], "invalid_recipient_field"
                    user = reference.get(target["field"])
                if user:
                    explicit.add(user)
                    detail["user"] = user
            details.append(detail)
        if not targets:
            selected_roles = set(allowed_roles)
            # Native Administrator sees every WA, but is not an implicit member
            # of a specifically selected department role.
            explicit.add("Administrator")
        names = set(explicit)
        if selected_roles & {"All", "Guest", "Desk User"}:
            rows = self.frappe.get_all("User", fields=["name"], filters={"enabled": 1, "user_type": "System User"}, limit_page_length=MAX_RECIPIENT_CANDIDATES + 1)
            if len(rows) > MAX_RECIPIENT_CANDIDATES:
                return set(), [], "recipient_candidate_limit"
            names.update(row["name"] for row in rows if row["name"] != "Administrator")
        elif selected_roles:
            rows = self.frappe.get_all(
                "Has Role", fields=["parent"], filters={"parenttype": "User", "role": ["in", sorted(selected_roles)]},
                limit_page_length=MAX_RECIPIENT_CANDIDATES + 1, distinct=True,
            )
            if len(rows) > MAX_RECIPIENT_CANDIDATES:
                return set(), [], "recipient_candidate_limit"
            names.update(row["parent"] for row in rows if row["parent"] != "Administrator")
        if len(names) > MAX_RECIPIENT_CANDIDATES:
            return set(), [], "recipient_candidate_limit"
        if not names:
            return set(), details, ""
        users = self.frappe.get_all(
            "User", fields=["name"], filters={"name": ["in", sorted(names)], "enabled": 1, "user_type": "System User"},
            limit_page_length=MAX_RECIPIENT_CANDIDATES + 1,
        )
        return {row["name"] for row in users if row["name"] != "Guest"}, details, ""

    def _can_read(self, action, reference, user):
        try:
            with quiet_reference_errors(self.frappe):
                return bool(self.frappe.has_permission("Workflow Action", "read", doc=action, user=user, throw=False)) and bool(
                    self.frappe.has_permission(reference.doctype, "read", doc=reference, user=user, throw=False)
                )
        except (self.frappe.PermissionError, self.frappe.DoesNotExistError):
            return False

    def _condition_satisfied(self, reference, user, condition):
        if not condition or not condition.strip():
            return True
        if self._workflow_globals_factory is None:
            from frappe.model.workflow import get_workflow_safe_globals
            self._workflow_globals_factory = get_workflow_safe_globals
        original = self._workflow_globals_factory()
        globals_ = dict(original)
        namespace = self.frappe._dict(original.get("frappe") or {})
        namespace.session = self.frappe._dict(namespace.get("session") or {})
        namespace.session.user = user
        globals_["frappe"] = namespace
        try:
            with quiet_reference_errors(self.frappe):
                return bool(self.frappe.safe_eval(condition, globals_, {"doc": reference.as_dict()}))
        except (SyntaxError, NameError, AttributeError, TypeError, ValueError, LookupError, ArithmeticError,
                self.frappe.ValidationError, self.frappe.PermissionError, self.frappe.DoesNotExistError):
            return None


def evaluate_action(action_or_name, *, frappe_module=None):
    """Worker entrypoint. There is intentionally no whitelisted HTTP endpoint."""
    if frappe_module is None:
        import frappe as frappe_module
    return ApprovalIndexPolicyEvaluator(frappe_module).evaluate_action(action_or_name)
