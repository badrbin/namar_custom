from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable

from namar_test.followups.approval_routing_settings import (
    FIELD_TARGET,
    HIDE_FIELD,
    OWNER_TARGET,
    ROLE_TARGET,
    ROUTING_FIELD,
    USER_TARGET,
    parse_routing_targets,
)
from namar_test.followups.reference_access import quiet_reference_errors


BATCH_SIZE = 500
AUTOMATIC_ROLES = {"All", "Guest", "Desk User", "Administrator"}
FALLBACK_NOTE = "تعذر تحديد مستلمين مؤهلين؛ تظهر الموافقة حسب أدوار سير العمل."
TARGET_LABELS = {
    USER_TARGET: "موظف محدد",
    OWNER_TARGET: "منشئ المستند",
    ROLE_TARGET: "دور محدد",
    FIELD_TARGET: "موظف من حقل",
}


@dataclass
class ApprovalVisibility:
    excluded_names: set[str]
    routing: dict[str, dict[str, Any]]


def _batches(values: Iterable[Any]):
    values = list(values)
    for start in range(0, len(values), BATCH_SIZE):
        yield values[start : start + BATCH_SIZE]


def role_routing(*, fallback: bool = False) -> dict[str, Any]:
    return {
        "mode": "Role",
        "targets": [],
        "responsible_users": [],
        "fallback": fallback,
        "note": FALLBACK_NOTE if fallback else "",
    }


class ApprovalRoutingResolver:
    """Narrow My Followups visibility after the normal Workflow Action query.

    This resolver deliberately does not grant permissions, alter assignments,
    mutate the current session, or persist cross-request caches.
    Child permitted_roles are Frappe's snapshot of the transitions applicable
    when the action was created. They are intersected with current transition
    roles, self-approval rules and the recipient's standard document read
    permission. Conditions use the standard safe evaluator with a copied
    recipient context; the standard workflow remains authoritative at execution.
    """

    def __init__(self, frappe, user: str, workflow_globals_factory):
        self.frappe = frappe
        self.user = user
        self.rules: dict[tuple[str, str], tuple[dict[str, str], ...] | None] = {}
        self.hidden_states: set[tuple[str, str]] = set()
        self.transitions: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        self._user_fields: dict[str, set[str]] = {}
        self._metadata = {}
        self._documents = {}
        self._read_permissions = {}
        self._condition_results = {}
        self._workflow_globals_factory = workflow_globals_factory
        self._visibility: ApprovalVisibility | None = None
        self._load_rules()

    def _meta(self, doctype):
        if doctype not in self._metadata:
            self._metadata[doctype] = self.frappe.get_meta(doctype)
        return self._metadata[doctype]

    @property
    def has_rules(self) -> bool:
        return bool(self.rules)

    @property
    def has_policy(self) -> bool:
        return bool(self.rules or self.hidden_states)

    def _load_rules(self) -> None:
        meta = self._meta("Workflow Document State")
        setting_fields = [field for field in (ROUTING_FIELD, HIDE_FIELD) if meta.has_field(field)]
        if not setting_fields:
            return
        workflows = self.frappe.get_all(
            "Workflow",
            fields=["name", "document_type"],
            filters={"is_active": 1},
            limit_page_length=0,
        )
        workflow_doctypes = {row["name"]: row["document_type"] for row in workflows}
        for workflow_names in _batches(workflow_doctypes):
            states = self.frappe.get_all(
                "Workflow Document State",
                fields=["parent", "state", *setting_fields],
                filters={"parent": ["in", workflow_names], "parenttype": "Workflow"},
                limit_page_length=0,
            )
            for state in states:
                key = (workflow_doctypes[state["parent"]], state["state"])
                if int(state.get(HIDE_FIELD) or 0):
                    self.hidden_states.add(key)
                    continue
                try:
                    targets = parse_routing_targets(state.get(ROUTING_FIELD))
                except ValueError:
                    # Corrupt/imported settings retain the standard role path.
                    self.rules[key] = None
                else:
                    if targets:
                        self.rules[key] = targets
        if self.has_rules:
            for workflow_names in _batches(workflow_doctypes):
                for transition in self.frappe.get_all(
                    "Workflow Transition",
                    fields=["parent", "state", "allowed", "allow_self_approval", "condition"],
                    filters={"parent": ["in", workflow_names], "parenttype": "Workflow"},
                    limit_page_length=0,
                ):
                    key = (workflow_doctypes[transition["parent"]], transition["state"])
                    self.transitions[key].append(transition)

    def _rule(self, action):
        return self.rules.get((action.get("reference_doctype"), action.get("workflow_state")), ())

    def exclusions(self) -> ApprovalVisibility:
        """Resolve only policy-affected actions, after standard permissions.

        Unconfigured states stay in the native aggregate/list queries. There is
        no need to fetch thousands of their IDs or include them in a SQL IN.
        """
        if self._visibility is not None:
            return self._visibility
        states_by_doctype = defaultdict(set)
        for doctype, state in set(self.rules) | self.hidden_states:
            states_by_doctype[doctype].add(state)
        rows = []
        for doctype, states in states_by_doctype.items():
            offset = 0
            while True:
                batch = self.frappe.get_list(
                    "Workflow Action",
                    fields=["name", "reference_doctype", "reference_name", "workflow_state"],
                    filters={
                        "status": "Open", "reference_doctype": doctype,
                        "workflow_state": ["in", sorted(states)],
                    },
                    order_by="name asc",
                    limit_start=offset,
                    limit_page_length=BATCH_SIZE,
                )
                rows.extend(batch)
                if len(batch) < BATCH_SIZE:
                    break
                offset += len(batch)
        routing = self.route_rows(rows)
        self._visibility = ApprovalVisibility(
            excluded_names={row["name"] for row in rows} - routing.keys(),
            routing=routing,
        )
        return self._visibility

    def route_rows(self, rows) -> dict[str, dict[str, Any]]:
        """Return routing metadata only for visible, already-permitted rows."""
        rows = [
            row for row in rows
            if (row.get("reference_doctype"), row.get("workflow_state")) not in self.hidden_states
        ]
        configured = [row for row in rows if self._rule(row)]
        headers = self._reference_headers(configured)
        targets_by_action = {
            row["name"]: self._resolve_targets(row, headers) for row in configured
        }
        users, roles, known_roles, role_members = self._users_and_roles(targets_by_action)
        permitted_roles = self._permitted_roles(configured)
        candidates_by_action = self._candidate_targets(
            configured, headers, targets_by_action, users, roles,
            known_roles, role_members, permitted_roles,
        )
        # Most owners may have no role for this stage. Decide that using small
        # headers before loading full parents and children for read/conditions.
        references = self._reference_values(
            row for row in configured if candidates_by_action[row["name"]]
        )
        visible = {}
        for row in rows:
            targets = self._rule(row)
            if targets is None:
                visible[row["name"]] = role_routing(fallback=True)
                continue
            if not targets:
                visible[row["name"]] = role_routing()
                continue
            recipients = set()
            valid_targets = []
            personal_users = set()
            allowed = permitted_roles.get(row["name"], set())
            state_key = (row.get("reference_doctype"), row.get("workflow_state"))
            transitions = self.transitions.get(state_key, ())
            reference = references.get((row.get("reference_doctype"), row.get("reference_name")), {})
            for target, candidates in candidates_by_action[row["name"]]:
                mode = target["type"]
                if mode == ROLE_TARGET:
                    role = target.get("role")
                    # A role target needs two facts: whether the viewer is an
                    # eligible member, or (otherwise) whether any other member
                    # prevents fallback. It need not enumerate every reader.
                    members = set()
                    for name in candidates:
                        if self._can_approve_reference(reference, name, roles, allowed, transitions):
                            members.add(name)
                            break
                    if not members:
                        continue
                    recipients.update(members)
                    valid_targets.append({"type": mode, "label": TARGET_LABELS[mode], "role": role})
                else:
                    name = target.get("user")
                    if not self._can_approve_reference(reference, name, roles, allowed, transitions):
                        continue
                    recipients.add(name)
                    personal_users.add(name)
                    detail = {
                        "type": mode,
                        "label": TARGET_LABELS[mode],
                        "user": name,
                        "user_name": users[name].get("full_name") or name,
                    }
                    if mode == FIELD_TARGET:
                        detail["field"] = target["field"]
                    valid_targets.append(detail)
            if not recipients:
                visible[row["name"]] = role_routing(fallback=True)
            elif self.user in recipients:
                visible[row["name"]] = {
                    "mode": "Targets",
                    "targets": valid_targets,
                    "responsible_users": [
                        {"user": name, "user_name": users[name].get("full_name") or name}
                        for name in sorted(personal_users)
                    ],
                    "fallback": False,
                    "note": "",
                }
        return visible

    def _candidate_targets(self, actions, headers, targets_by_action, users, roles, known_roles, role_members, permitted_roles):
        eligible_role_members = {}
        result = {}
        for action in actions:
            name = action["name"]
            state_key = (action.get("reference_doctype"), action.get("workflow_state"))
            owner = headers.get((action.get("reference_doctype"), action.get("reference_name")), {}).get("owner")
            transitions = self.transitions.get(state_key, ())
            allowed = permitted_roles.get(name, set())
            result[name] = []
            for target in targets_by_action[name]:
                if target["type"] == ROLE_TARGET:
                    role = target.get("role")
                    cache_key = (role, state_key, frozenset(allowed), owner)
                    if cache_key not in eligible_role_members:
                        eligible_role_members[cache_key] = {
                            member for member in role_members.get(role, ())
                            if role in known_roles
                            and self._eligible_user(member, users, roles, allowed, transitions, owner)
                        }
                    members = eligible_role_members[cache_key]
                    candidates = ([self.user] if self.user in members else []) + sorted(members - {self.user})
                else:
                    member = target.get("user")
                    candidates = [member] if self._eligible_user(member, users, roles, allowed, transitions, owner) else []
                if candidates:
                    result[name].append((target, candidates))
        return result

    def _reference_headers(self, actions):
        names_by_doctype = defaultdict(set)
        requested_fields = defaultdict(set)
        for action in actions:
            doctype = action.get("reference_doctype")
            name = action.get("reference_name")
            if doctype and name:
                names_by_doctype[doctype].add(name)
                for target in self._rule(action):
                    if target["type"] == FIELD_TARGET:
                        requested_fields[doctype].add(target["field"])
        headers = {}
        for doctype, names in names_by_doctype.items():
            try:
                meta = self._meta(doctype)
            except self.frappe.DoesNotExistError:
                continue
            if meta.issingle or meta.is_virtual:
                continue
            self._user_fields[doctype] = {
                field.fieldname for field in meta.fields
                if field.fieldtype == "Link" and field.options == "User"
            }
            fields = ["name", "owner", *sorted(requested_fields[doctype] & self._user_fields[doctype])]
            for batch in _batches(names):
                for row in self.frappe.get_all(
                    doctype, fields=fields, filters={"name": ["in", batch]}, limit_page_length=0,
                ):
                    headers[(doctype, row["name"])] = row
        return headers

    def _reference_values(self, actions) -> dict[tuple[str, str], dict[str, Any]]:
        names_by_doctype = defaultdict(set)
        for action in actions:
            doctype = action.get("reference_doctype")
            name = action.get("reference_name")
            if doctype and name:
                names_by_doctype[doctype].add(name)
        references = {}
        for doctype, names in names_by_doctype.items():
            try:
                meta = self._meta(doctype)
            except self.frappe.DoesNotExistError:
                continue
            if meta.issingle or meta.is_virtual:
                continue
            for batch in _batches(names):
                parent_rows = self.frappe.get_all(
                    doctype,
                    fields=["*"],
                    filters={"name": ["in", batch]},
                    limit_page_length=0,
                )
                parents = {doc["name"]: doc for doc in parent_rows}
                # User Permissions inspect Link fields in children, while
                # controller permission hooks may use any other child value.
                # Load complete rows in batches instead of get_doc(name) per
                # action; constructing get_doc(dict) below performs no row read.
                for field in meta.get_table_fields():
                    child_meta = self._meta(field.options)
                    if child_meta.is_virtual:
                        continue
                    for parent in parents.values():
                        parent[field.fieldname] = []
                    for child in self.frappe.get_all(
                        field.options,
                        fields=["*"],
                        filters={
                            "parenttype": doctype,
                            "parentfield": field.fieldname,
                            "parent": ["in", list(parents)],
                        },
                        order_by="idx asc",
                        limit_page_length=0,
                    ) if parents else ():
                        child["doctype"] = field.options
                        parents[child["parent"]][field.fieldname].append(child)
                for doc in parent_rows:
                    doc["doctype"] = doctype
                    references[(doctype, doc["name"])] = doc
        return references

    def _can_read_reference(self, reference, user: str) -> bool:
        if not reference:
            return False
        reference_key = (reference["doctype"], reference["name"])
        permission_key = (*reference_key, user)
        if permission_key not in self._read_permissions:
            try:
                with quiet_reference_errors(self.frappe):
                    if reference_key not in self._documents:
                        self._documents[reference_key] = self.frappe.get_doc(dict(reference))
                    allowed = bool(self.frappe.has_permission(
                        reference["doctype"], "read",
                        doc=self._documents[reference_key], user=user, throw=False,
                    ))
            except (self.frappe.PermissionError, self.frappe.DoesNotExistError):
                allowed = False
            self._read_permissions[permission_key] = allowed
        return self._read_permissions[permission_key]

    def _can_approve_reference(self, reference, user, roles, allowed, transitions) -> bool:
        if not self._can_read_reference(reference, user):
            return False
        for transition in transitions:
            role = transition.get("allowed")
            if role not in allowed or role not in roles.get(user, ()):
                continue
            if user != "Administrator" and user == reference.get("owner") and not int(transition.get("allow_self_approval") or 0):
                continue
            if self._condition_satisfied(reference, user, transition.get("condition")):
                return True
        return False

    def _condition_satisfied(self, reference, user, condition) -> bool:
        if not condition:
            return True
        key = (reference["doctype"], reference["name"], user, condition)
        if key in self._condition_results:
            return self._condition_results[key]
        original = self._workflow_globals_factory()
        globals_ = dict(original)
        namespace = self.frappe._dict(original["frappe"])
        database = self.frappe._dict(namespace.db)
        session = self.frappe._dict(namespace.session)
        native_get_list = database.get_list

        def recipient_get_list(*args, **kwargs):
            kwargs.setdefault("user", user)
            return native_get_list(*args, **kwargs)

        database.get_list = recipient_get_list
        session.user = user
        namespace.db = database
        namespace.session = session
        globals_["frappe"] = namespace
        # Only messages from expected condition failures are discarded. A DB
        # outage or other unexpected exception must remain visible to diagnose.
        message_log = getattr(self.frappe, "message_log", None)
        previous_messages = list(message_log) if message_log is not None else None
        flags = getattr(self.frappe, "flags", None)
        missing = object()
        previous_error = flags.get("error_message", missing) if flags is not None else missing
        try:
            doc = self._documents[(reference["doctype"], reference["name"])]
            result = bool(self.frappe.safe_eval(condition, globals_, {"doc": doc.as_dict()}))
        except (
            SyntaxError, NameError, AttributeError, TypeError, ValueError,
            LookupError, ArithmeticError,
            self.frappe.ValidationError, self.frappe.PermissionError,
            self.frappe.DoesNotExistError,
        ):
            if previous_messages is not None:
                self.frappe.message_log[:] = previous_messages
            if flags is not None:
                if previous_error is missing:
                    flags.pop("error_message", None)
                else:
                    flags.error_message = previous_error
            result = False
        self._condition_results[key] = result
        return result

    def _resolve_targets(self, action, references):
        reference = references.get((action.get("reference_doctype"), action.get("reference_name")), {})
        result = []
        for target in self._rule(action):
            target = dict(target)
            if target["type"] == OWNER_TARGET:
                target["user"] = reference.get("owner")
            elif target["type"] == FIELD_TARGET:
                field = target.get("field")
                target["user"] = reference.get(field) if field in self._user_fields.get(action.get("reference_doctype"), set()) else None
            result.append(target)
        return result

    def _users_and_roles(self, targets_by_action):
        targets = [target for action in targets_by_action.values() for target in action]
        names = {target.get("user") for target in targets if target.get("user")}
        users = {}
        fields = ["name", "enabled", "user_type", "full_name"]
        if any(target["type"] == ROLE_TARGET for target in targets):
            # Role receivers are a union of actual active system users. This
            # also distinguishes an empty/ineligible role from a valid target.
            users.update({row["name"]: row for row in self.frappe.get_all(
                "User", fields=fields,
                filters={"enabled": 1, "user_type": "System User"},
                limit_page_length=0,
            )})
        for batch in _batches(names - users.keys()):
            users.update({row["name"]: row for row in self.frappe.get_all(
                "User", fields=fields, filters={"name": ["in", batch]}, limit_page_length=0,
            )})
        roles = defaultdict(set)
        role_members = defaultdict(set)
        for batch in _batches(users):
            for row in self.frappe.get_all(
                "Has Role", fields=["parent", "role"],
                filters={"parenttype": "User", "parent": ["in", batch]},
                limit_page_length=0,
            ):
                # Named role targets use actual memberships, like Frappe's
                # get_users_with_role; Administrator's universal permissions
                # are not implicit membership of every targeted department.
                if row["parent"] != "Administrator":
                    role_members[row["role"]].add(row["parent"])
                if row["role"] not in AUTOMATIC_ROLES:
                    roles[row["parent"]].add(row["role"])
        known_roles = set()
        if users and ("Administrator" in users or any(target["type"] == ROLE_TARGET for target in targets)):
            known_roles = {row["name"] for row in self.frappe.get_all("Role", fields=["name"], limit_page_length=0)}
        for name, user in users.items():
            if name == "Administrator":
                roles[name] = set(known_roles)
            elif name != "Guest":
                roles[name].update({"All", "Guest"})
                if user.get("user_type") == "System User":
                    roles[name].add("Desk User")
        return users, roles, known_roles, role_members

    def _permitted_roles(self, actions):
        roles = defaultdict(set)
        for batch in _batches(row["name"] for row in actions):
            for role in self.frappe.get_all(
                "Workflow Action Permitted Role", fields=["parent", "role"],
                filters={"parenttype": "Workflow Action", "parent": ["in", batch]},
                limit_page_length=0,
            ):
                roles[role["parent"]].add(role["role"])
        return roles

    @staticmethod
    def _eligible_user(name, users, roles, allowed, transitions, owner) -> bool:
        user = users.get(name)
        if not user or name == "Guest" or not int(user.get("enabled") or 0) or user.get("user_type") != "System User":
            return False
        current_allowed = {
            row.get("allowed") for row in transitions
            if row.get("allowed") and (
                name == "Administrator" or name != owner or int(row.get("allow_self_approval") or 0)
            )
        }
        return bool(roles.get(name, set()) & allowed & current_allowed)
