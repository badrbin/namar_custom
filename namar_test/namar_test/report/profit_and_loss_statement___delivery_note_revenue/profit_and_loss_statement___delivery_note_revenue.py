# Copyright (c) 2026, Namar
# License: MIT

"""Profit and Loss Statement variant whose income comes from Delivery Notes.

This is the application-backed version of the custom report
"Profit and Loss Statement - Delivery Note Revenue".  It intentionally keeps
ERPNext's standard Profit and Loss Statement output shape and replaces only the
Income data source with submitted Delivery Note net amounts.
"""

from __future__ import annotations

import copy

import frappe
from frappe import _
from frappe.utils import cstr

from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import (
    get_accounting_dimensions,
    get_dimension_with_children,
)
from erpnext.accounts.report.financial_statements import (
    accumulate_values_into_parents,
    add_total_row,
    calculate_values,
    compute_growth_view_data,
    filter_accounts,
    filter_out_zero_value_rows,
    get_accounts,
    get_appropriate_currency,
    get_columns,
    get_cost_centers_with_children,
    get_data,
    get_period_list,
    prepare_data,
)
from erpnext.accounts.report.profit_and_loss_statement.profit_and_loss_statement import (
    get_chart_data,
    get_net_profit_loss,
    get_report_summary,
)

REPORT_NAME = "Profit and Loss Statement - Delivery Note Revenue"


def execute(filters=None):
    filters = frappe._dict(filters or {})
    filters.setdefault("selected_view", "Report")
    filters.setdefault("accumulated_values", 0)
    filters.setdefault("include_default_book_entries", 1)
    filters.setdefault("show_zero_values", 0)

    period_list = get_period_list(
        filters.from_fiscal_year,
        filters.to_fiscal_year,
        filters.period_start_date,
        filters.period_end_date,
        filters.filter_based_on,
        filters.periodicity,
        company=filters.company,
    )

    income = get_delivery_note_income_data(
        filters.company,
        period_list,
        filters=filters,
        accumulated_values=filters.accumulated_values,
    )

    expense = get_data(
        filters.company,
        "Expense",
        "Debit",
        period_list,
        filters=filters,
        accumulated_values=filters.accumulated_values,
        ignore_closing_entries=True,
    )

    net_profit_loss = get_net_profit_loss(
        income, expense, period_list, filters.company, filters.presentation_currency
    )

    data = []
    data.extend(income or [])
    data.extend(expense or [])
    if net_profit_loss:
        data.append(net_profit_loss)

    columns = get_columns(filters.periodicity, period_list, filters.accumulated_values, filters.company)
    currency = filters.presentation_currency or frappe.get_cached_value(
        "Company", filters.company, "default_currency"
    )
    chart = get_chart_data(filters, period_list, income, expense, net_profit_loss, currency)
    report_summary, primitive_summary = get_report_summary(
        period_list, filters.periodicity, income, expense, net_profit_loss, currency, filters
    )

    if filters.get("selected_view") == "Growth":
        compute_growth_view_data(data, period_list)

    if filters.get("selected_view") == "Margin":
        compute_margin_view_data_using_income_total(data, period_list, filters.accumulated_values, income)

    return columns, data, None, chart, report_summary, primitive_summary


@frappe.whitelist()
def app_report_ping():
    """Health check used before switching the Report doc to app-backed mode."""
    return {"ok": True, "report": REPORT_NAME, "mode": "app"}


def get_delivery_note_income_data(company, period_list, filters=None, accumulated_values=1, total=True):
    accounts = get_accounts(company, "Income")
    if not accounts:
        return None

    accounts, accounts_by_name, parent_children_map = filter_accounts(accounts)
    company_currency = get_appropriate_currency(company, filters)

    delivery_note_entries_by_account = {}
    for entry in get_delivery_note_income_entries(company, period_list, filters):
        delivery_note_entries_by_account.setdefault(entry.account, []).append(entry)

    calculate_values(accounts_by_name, delivery_note_entries_by_account, period_list, accumulated_values, False)
    accumulate_values_into_parents(accounts, accounts_by_name, period_list)
    out = prepare_data(
        accounts,
        "Credit",
        period_list,
        company_currency,
        accumulated_values=filters.accumulated_values,
    )
    out = filter_out_zero_value_rows(out, parent_children_map, filters.show_zero_values)

    if out and total:
        add_total_row(out, "Income", "Credit", period_list, company_currency)

    return out


def get_delivery_note_income_entries(company, period_list, filters):
    default_income_account = frappe.get_cached_value("Company", company, "default_income_account")
    if not default_income_account:
        frappe.throw(_("Default Income Account is required on Company {0}").format(company))

    values = {
        "company": company,
        "from_date": period_list[0]["year_start_date"],
        "to_date": period_list[-1]["to_date"],
        "default_income_account": default_income_account,
    }
    conditions = [
        "dn.docstatus = 1",
        "dn.company = %(company)s",
        "dn.posting_date >= %(from_date)s",
        "dn.posting_date <= %(to_date)s",
        "IFNULL(dni.base_net_amount, 0) != 0",
    ]
    add_delivery_note_filter_conditions(conditions, values, filters)

    income_sources = []
    if table_has_column("Delivery Note Item", "income_account"):
        income_sources.append("NULLIF(dni.income_account, '')")
    income_sources.extend(
        [
            "NULLIF(item_default.income_account, '')",
            "NULLIF(item_group_default.income_account, '')",
            "NULLIF(brand_default.income_account, '')",
            "%(default_income_account)s",
        ]
    )
    income_account_expr = "COALESCE(" + ", ".join(income_sources) + ")"

    return frappe.db.sql(
        """
        SELECT
            {income_account_expr} AS account,
            dn.posting_date AS posting_date,
            NULL AS fiscal_year,
            0 AS debit,
            SUM(IFNULL(dni.base_net_amount, 0)) AS credit
        FROM `tabDelivery Note` dn
        INNER JOIN `tabDelivery Note Item` dni ON dni.parent = dn.name
        LEFT JOIN `tabItem` item ON item.name = dni.item_code
        LEFT JOIN `tabItem Default` item_default
            ON item_default.parent = dni.item_code
            AND item_default.parenttype = 'Item'
            AND item_default.company = dn.company
        LEFT JOIN `tabItem Default` item_group_default
            ON item_group_default.parent = item.item_group
            AND item_group_default.parenttype = 'Item Group'
            AND item_group_default.company = dn.company
        LEFT JOIN `tabItem Default` brand_default
            ON brand_default.parent = item.brand
            AND brand_default.parenttype = 'Brand'
            AND brand_default.company = dn.company
        WHERE {conditions}
        GROUP BY {income_account_expr}, dn.posting_date
        """.format(
            income_account_expr=income_account_expr,
            conditions=" AND ".join(conditions),
        ),
        values,
        as_dict=True,
    )


def add_delivery_note_filter_conditions(conditions, values, filters):
    if filters.get("project"):
        projects = normalize_filter_values(filters.get("project"))
        if projects:
            values["project_values"] = tuple(projects)
            project_conditions = []
            if table_has_column("Delivery Note Item", "project"):
                project_conditions.append("dni.project IN %(project_values)s")
            if table_has_column("Delivery Note", "project"):
                project_conditions.append("dn.project IN %(project_values)s")
            if project_conditions:
                conditions.append("(" + " OR ".join(project_conditions) + ")")

    if filters.get("cost_center"):
        cost_centers = get_cost_centers_with_children(filters.get("cost_center"))
        if cost_centers:
            values["cost_center_values"] = tuple(cost_centers)
            cost_center_conditions = []
            if table_has_column("Delivery Note Item", "cost_center"):
                cost_center_conditions.append("dni.cost_center IN %(cost_center_values)s")
            if table_has_column("Delivery Note", "cost_center"):
                cost_center_conditions.append("dn.cost_center IN %(cost_center_values)s")
            if cost_center_conditions:
                conditions.append("(" + " OR ".join(cost_center_conditions) + ")")

    for dimension in get_accounting_dimensions(as_list=False):
        fieldname = dimension.fieldname
        selected_values = filters.get(fieldname)
        if not selected_values:
            continue

        item_has_field = table_has_column("Delivery Note Item", fieldname)
        header_has_field = table_has_column("Delivery Note", fieldname)
        if not item_has_field and not header_has_field:
            continue

        dimension_values = get_dimension_values(dimension, selected_values)
        if not dimension_values:
            continue

        key = "dn_dimension_" + fieldname
        values[key] = tuple(dimension_values)
        field_conditions = []
        if item_has_field:
            field_conditions.append("dni." + quoted_column(fieldname) + " IN %(" + key + ")s")
        if header_has_field:
            field_conditions.append("dn." + quoted_column(fieldname) + " IN %(" + key + ")s")
        if field_conditions:
            conditions.append("(" + " OR ".join(field_conditions) + ")")


def get_dimension_values(dimension, selected_values):
    values = normalize_filter_values(selected_values)
    if not values:
        return []

    if dimension.document_type and frappe.get_cached_value("DocType", dimension.document_type, "is_tree"):
        return get_dimension_with_children(dimension.document_type, values)

    return values


def normalize_filter_values(raw_value):
    if not raw_value:
        return []

    values = raw_value
    if isinstance(values, str):
        value_text = values.strip()
        if value_text.startswith("[") and value_text.endswith("]"):
            values = frappe.parse_json(value_text)
        else:
            values = value_text.replace("\n", ",").replace(";", ",").split(",")
    elif not isinstance(values, (list, tuple)):
        values = [values]

    normalized = []
    for value in values:
        if isinstance(value, dict):
            value = value.get("value") or value.get("name") or value.get("label") or ""
        value = cstr(value).strip()
        if value and value not in normalized:
            normalized.append(value)

    return normalized


def table_has_column(doctype, fieldname):
    if hasattr(frappe.db, "has_column"):
        return bool(frappe.db.has_column(doctype, fieldname))

    table_name = "tab" + cstr(doctype).replace("`", "``")
    return bool(
        frappe.db.sql(
            """
            SELECT 1
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = %s
              AND COLUMN_NAME = %s
            LIMIT 1
            """,
            (table_name, fieldname),
        )
    )


def quoted_column(fieldname):
    return "`" + cstr(fieldname).replace("`", "``") + "`"


def compute_margin_view_data_using_income_total(data, columns, accumulated_values, income):
    if not columns:
        return

    margin_columns = list(columns)
    if not accumulated_values:
        margin_columns.append({"key": "total"})

    if income and len(income) >= 2:
        base_row = copy.deepcopy(income[-2])
    else:
        base_row = None

    if not base_row:
        data_copy = copy.deepcopy(data)
        for row in data_copy:
            account_text = (row or {}).get("account") or (row or {}).get("account_name") or ""
            if "Total Income" in account_text or _("Total Income") in account_text:
                base_row = row
                break

    if not base_row:
        return

    data_copy = copy.deepcopy(data)
    for row_idx, row in enumerate(data_copy):
        if not row:
            continue

        for column in margin_columns:
            curr_period = column.get("key")
            base_value = base_row.get(curr_period)
            curr_value = row.get(curr_period)

            if curr_value is None or not base_value or base_value <= 0:
                data[row_idx][curr_period] = None
                continue

            data[row_idx][curr_period] = round((curr_value / base_value) * 100, 2)
