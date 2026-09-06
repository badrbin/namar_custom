"""Keep native Report Builder execution while checking its actual field usage."""
from __future__ import annotations

import json
import re

import frappe

from .policy import COMMON_FIELDS, Denied, checked_filters, field_names, parse_json


def prepare(report, plan, supplied_filters):
    from .boundary import _field_scopes, _meta, _role_rows
    parent = _meta(report.ref_doctype)
    general, conditional, owner_only = _field_scopes(parent, plan)
    levels = {int(r.permlevel or 0) for r in _role_rows(parent, plan["role"], "read") if not int(r.if_owner or 0)}
    owned_levels = {int(r.permlevel or 0) for r in _role_rows(parent, plan["role"], "read")}
    def field_scope(doctype, name):
        field_names([name])
        if doctype == parent.name:
            field = parent.get_field(name)
            if field and field.fieldtype in ("Password", "Table", "Table MultiSelect"):
                return False, False
            return name in general, name in general | conditional
        tables = [field for field in parent.get_table_fields() if field.options == doctype]
        if len(tables) != 1:
            raise Denied("Ambiguous child relation requires a reviewed Builder adapter")
        table = tables[0]
        child = frappe.get_meta(doctype)
        field = child.get_field(name)
        if not child.istable or (not field and name not in COMMON_FIELDS):
            raise Denied("Report Builder field is outside the parent/child schema")
        level = int(field.permlevel or 0) if field else 0
        public = table.fieldname in general and level in levels
        owned = table.fieldname in general | conditional and level in owned_levels
        if field and field.fieldtype in ("Password", "Table", "Table MultiSelect"):
            return False, False
        return public, owned

    params = parse_json(report.json, {})
    if not isinstance(params, dict) or params.get("group_by"):
        raise Denied("Grouped Builder reports require a reviewed adapter")
    selected = []
    private_columns = []
    for column in report.get_standard_report_columns(params):
        if not isinstance(column, (list, tuple)) or len(column) != 2:
            raise Denied("Invalid Report Builder column")
        name, doctype = column
        public, owned = field_scope(doctype, name)
        if owned:
            if not public:
                private_columns.append(len(selected))
            selected.append([name, doctype])
    if not selected:
        raise Denied("No permitted Report Builder columns")

    restrict_owner = owner_only
    stored_filters = []
    for condition in params.get("filters") or []:
        if len(condition) == 5 and isinstance(condition[-1], bool):
            condition = condition[:4]
        if len(condition) == 3:
            condition = [parent.name, *condition]
        if len(condition) != 4:
            raise Denied("Invalid Report Builder filter")
        doctype, name, operator, value = condition
        public, owned = field_scope(doctype, name)
        if not owned:
            raise Denied("Report Builder filter uses a hidden field")
        checked = checked_filters([[name, operator, value]], {name})[0]
        restrict_owner = restrict_owner or not public
        stored_filters.append([doctype, *checked])
    for name, operator, value in checked_filters(supplied_filters, general | conditional):
        restrict_owner = restrict_owner or name in conditional

    order = []
    if params.get("sort_by"):
        for key, direction_key in (("sort_by", "sort_order"), ("sort_by_next", "sort_order_next")):
            if params.get(key):
                doctype, separator, name = params[key].rpartition(".")
                if not separator:
                    raise Denied("Invalid Report Builder sort field")
                order.append((doctype, name, params.get(direction_key) or "asc"))
    else:
        for item in (params.get("order_by") or f"`tab{parent.name}`.`modified` desc").split(","):
            match = re.fullmatch(r"\s*`tab([^`]+)`\.`([^`]+)`\s+(asc|desc)\s*", item, re.IGNORECASE)
            if not match:
                raise Denied("Report Builder order expression is outside the reviewed format")
            order.append(match.groups())
    for doctype, name, direction in order:
        if str(direction).lower() not in ("asc", "desc"):
            raise Denied("Invalid sort direction")
        public, owned = field_scope(doctype, name)
        if not owned:
            raise Denied("Report Builder sort uses a hidden field")
        restrict_owner = restrict_owner or not public
    if restrict_owner:
        stored_filters.append([parent.name, "owner", "=", plan["user"]])

    owner_column = None
    extra_owner = False
    if private_columns and not restrict_owner:
        owner_field = ["owner", parent.name]
        if owner_field not in selected:
            selected.append(owner_field)
            extra_owner = True
        owner_column = selected.index(owner_field)
        # Native append_totals_row would aggregate owner-only values before
        # masking. Recompute totals from the visible rows in finish().
    totals = bool(params.get("add_totals_row"))
    params["add_totals_row"] = False
    params["fields"] = selected
    params["filters"] = stored_filters
    report.json = json.dumps(params, ensure_ascii=False)
    return {"private_columns": private_columns, "owner_column": owner_column,
            "extra_owner": extra_owner, "user": plan["user"], "totals": totals}


def finish(columns, rows, scope):
    if scope["owner_column"] is not None:
        for row in rows:
            if str(row[scope["owner_column"]]).lower() != scope["user"].lower():
                for index in scope["private_columns"]:
                    row[index] = None
        if scope["extra_owner"]:
            columns.pop(scope["owner_column"])
            for row in rows:
                row.pop(scope["owner_column"])
    if scope["totals"]:
        from frappe.desk.reportview import append_totals_row
        rows = append_totals_row(rows)
    return columns, rows
