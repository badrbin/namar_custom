from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import cint, flt


MR_BRANCH_FIELD = "الفرع"


def _parse_current_items(raw_items: Any) -> list[dict[str, Any]]:
    if not raw_items:
        return []
    if isinstance(raw_items, list):
        return [row for row in raw_items if isinstance(row, dict)]
    try:
        parsed = frappe.parse_json(raw_items)
    except Exception:
        return []
    return parsed if isinstance(parsed, list) else []


def _empty_item_row(item_code: str, item_name: str | None, is_extra: bool) -> dict[str, Any]:
    return {
        "item_code": item_code,
        "item_name": item_name or item_code,
        "so_qty": 0.0,
        "delivered_qty": 0.0,
        "billed_qty": 0.0,
        "installed_qty": 0.0,
        "other_mr_qty": 0.0,
        "current_mr_qty": 0.0,
        "mr_qty": 0.0,
        "balance_without_current": 0.0,
        "balance": 0.0,
        "current_delivered_qty": 0.0,
        "current_billed_qty": 0.0,
        "current_installed_qty": 0.0,
        "current_billed_balance": 0.0,
        "current_delivered_balance": 0.0,
        "current_installed_balance": 0.0,
        "is_extra": is_extra,
    }


def _get_item_row(
    summary_data: dict[str, dict[str, Any]],
    item_code: str,
    item_name: str | None = None,
    is_extra: bool = False,
) -> dict[str, Any]:
    if item_code not in summary_data:
        summary_data[item_code] = _empty_item_row(item_code, item_name, is_extra)
    row = summary_data[item_code]
    if item_name and (not row.get("item_name") or row.get("item_name") == row.get("item_code")):
        row["item_name"] = item_name
    if not is_extra:
        row["is_extra"] = False
    return row


def get_related_items(
    sales_order: str | None = None,
    mr_name: str | None = None,
    current_items: Any = None,
) -> list[dict[str, Any]]:
    if not sales_order and mr_name:
        sales_order = frappe.db.get_value("Material Request", mr_name, "sales_order")
    if not sales_order:
        return []

    summary_data: dict[str, dict[str, Any]] = {}
    parsed_current_items = _parse_current_items(current_items)

    if mr_name and not parsed_current_items:
        parsed_current_items = frappe.get_all(
            "Material Request Item",
            filters={"parent": mr_name},
            fields=["item_code", "item_name", "qty"],
            ignore_permissions=True,
        )

    so_items = frappe.db.sql(
        """
        SELECT
            so_item.name AS so_detail,
            so_item.item_code,
            so_item.item_name,
            so_item.qty,
            so_item.delivered_qty,
            IFNULL((
                SELECT SUM(sii.qty)
                FROM `tabSales Invoice Item` sii
                INNER JOIN `tabSales Invoice` si ON si.name = sii.parent
                WHERE sii.so_detail = so_item.name
                AND si.docstatus = 1
            ), 0) AS billed_actual_qty
        FROM `tabSales Order Item` so_item
        WHERE so_item.parent = %s
        """,
        (sales_order,),
        as_dict=True,
    )

    for item in so_items:
        row = _get_item_row(summary_data, item.item_code, item.item_name, False)
        row["so_qty"] += flt(item.qty)
        row["delivered_qty"] += flt(item.delivered_qty)
        row["billed_qty"] += flt(item.billed_actual_qty)

    related_mrs = frappe.get_all(
        "Material Request",
        filters={"sales_order": sales_order, "docstatus": ["<", 2]},
        fields=["name"],
        ignore_permissions=True,
    )
    other_related_mrs = [related_mr.name for related_mr in related_mrs if related_mr.name != mr_name]

    if other_related_mrs:
        other_mr_items = frappe.get_all(
            "Material Request Item",
            filters={"parent": ["in", other_related_mrs]},
            fields=["item_code", "item_name", "qty"],
            ignore_permissions=True,
        )
        for item in other_mr_items:
            row = _get_item_row(summary_data, item.item_code, item.item_name, True)
            row["other_mr_qty"] += flt(item.qty)

    for item in parsed_current_items:
        item_code = item.get("item_code")
        if not item_code:
            continue
        row = _get_item_row(summary_data, item_code, item.get("item_name"), True)
        row["current_mr_qty"] += flt(item.get("qty"))

    inst_items = frappe.db.sql(
        """
        SELECT child.item_code, child.qty
        FROM `tabInstallation Note Item` child
        INNER JOIN `tabInstallation Note` parent ON child.parent = parent.name
        WHERE parent.docstatus = 1
        AND parent.custom_sales_order = %s
        """,
        (sales_order,),
        as_dict=True,
    )

    for item in inst_items:
        row = _get_item_row(summary_data, item.item_code, item.item_code, True)
        row["installed_qty"] += flt(item.qty)

    if mr_name:
        current_delivered_rows = frappe.db.sql(
            """
            SELECT child.item_code, SUM(child.qty) AS total_qty
            FROM `tabDelivery Note Item` child
            INNER JOIN `tabDelivery Note` parent ON child.parent = parent.name
            WHERE parent.docstatus = 1
            AND parent.custom_material_request = %s
            GROUP BY child.item_code
            """,
            (mr_name,),
            as_dict=True,
        )
        for item in current_delivered_rows:
            row = _get_item_row(summary_data, item.item_code, item.item_code, True)
            row["current_delivered_qty"] += flt(item.total_qty)

        current_installed_rows = frappe.db.sql(
            """
            SELECT child.item_code, SUM(child.qty) AS total_qty
            FROM `tabInstallation Note Item` child
            INNER JOIN `tabInstallation Note` parent ON child.parent = parent.name
            WHERE parent.docstatus = 1
            AND parent.custom_material_request = %s
            GROUP BY child.item_code
            """,
            (mr_name,),
            as_dict=True,
        )
        for item in current_installed_rows:
            row = _get_item_row(summary_data, item.item_code, item.item_code, True)
            row["current_installed_qty"] += flt(item.total_qty)

        current_billed_rows = frappe.db.sql(
            """
            SELECT
                COALESCE(dni.item_code, sii.item_code) AS item_code,
                SUM(sii.qty) AS total_qty
            FROM `tabSales Invoice Item` sii
            INNER JOIN `tabSales Invoice` si ON si.name = sii.parent
            LEFT JOIN `tabDelivery Note Item` dni ON dni.name = sii.dn_detail
            LEFT JOIN `tabDelivery Note` dn ON dn.name = COALESCE(dni.parent, sii.delivery_note)
            WHERE si.docstatus = 1
            AND dn.custom_material_request = %s
            GROUP BY COALESCE(dni.item_code, sii.item_code)
            """,
            (mr_name,),
            as_dict=True,
        )
        for item in current_billed_rows:
            row = _get_item_row(summary_data, item.item_code, item.item_code, True)
            row["current_billed_qty"] += flt(item.total_qty)

    final_rows = []
    for row in summary_data.values():
        row["mr_qty"] = flt(row["other_mr_qty"]) + flt(row["current_mr_qty"])
        row["balance_without_current"] = flt(row["so_qty"]) - flt(row["other_mr_qty"])
        row["balance"] = flt(row["so_qty"]) - flt(row["mr_qty"])
        row["current_billed_balance"] = flt(row["current_billed_qty"]) - flt(row["current_mr_qty"])
        row["current_delivered_balance"] = flt(row["current_delivered_qty"]) - flt(row["current_mr_qty"])
        row["current_installed_balance"] = flt(row["current_installed_qty"]) - flt(row["current_mr_qty"])
        if not row.get("item_name"):
            row["item_name"] = row["item_code"]
        final_rows.append(row)

    return sorted(final_rows, key=lambda row: (1 if row.get("is_extra") else 0, row.get("item_code") or ""))


def _column(label: str, fieldname: str, fieldtype: str = "Data", width: int = 120, options: str | None = None):
    column = {"label": label, "fieldname": fieldname, "fieldtype": fieldtype, "width": width}
    if options:
        column["options"] = options
    return column


def _has_mr_column(fieldname: str) -> bool:
    return bool(frappe.db.has_column("Material Request", fieldname))


def _quote_identifier(value: str) -> str:
    return "`" + value.replace("`", "``") + "`"


def _mr_column(fieldname: str, alias: str) -> str:
    if not _has_mr_column(fieldname):
        return f"NULL AS {_quote_identifier(alias)}"
    return f"mr.{_quote_identifier(fieldname)} AS {_quote_identifier(alias)}"


def _filters_dict(filters: dict[str, Any] | None) -> frappe._dict:
    return frappe._dict(filters or {})


def get_all_material_requests(filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    filters = _filters_dict(filters)
    where = []
    params: dict[str, Any] = {}

    if not cint(filters.get("include_cancelled")):
        where.append("mr.docstatus < 2")
    if filters.get("from_date") and _has_mr_column("transaction_date"):
        where.append("mr.transaction_date >= %(from_date)s")
        params["from_date"] = filters.from_date
    if filters.get("to_date") and _has_mr_column("transaction_date"):
        where.append("mr.transaction_date <= %(to_date)s")
        params["to_date"] = filters.to_date
    if filters.get("material_request"):
        where.append("mr.name = %(material_request)s")
        params["material_request"] = filters.material_request

    exact_field_filters = {
        "company": "company",
        "sales_order": "sales_order",
        "customer": "customer",
        "workflow_state": "workflow_state",
        "branch": MR_BRANCH_FIELD,
        "request_scenario": "custom_request_scenario",
        "manufacturing_status": "custom_manufacturing_status",
        "delivery_readiness_status": "custom_delivery_readiness_status",
    }
    for filter_name, fieldname in exact_field_filters.items():
        value = filters.get(filter_name)
        if value and _has_mr_column(fieldname):
            where.append(f"mr.{_quote_identifier(fieldname)} = %({filter_name})s")
            params[filter_name] = value

    if filters.get("customer_vip") and _has_mr_column("custom_customer_vip"):
        where.append("mr.custom_customer_vip = 1")

    if filters.get("item_code"):
        where.append(
            """
            EXISTS (
                SELECT 1
                FROM `tabMaterial Request Item` item_filter
                WHERE item_filter.parent = mr.name
                AND item_filter.item_code = %(item_code)s
            )
            """
        )
        params["item_code"] = filters.item_code

    limit = max(1, min(cint(filters.get("limit")) or 500, 2000))
    params["limit"] = limit

    where_sql = " AND ".join(where) if where else "1 = 1"
    rows = frappe.db.sql(
        f"""
        SELECT
            mr.name AS material_request,
            {_mr_column("transaction_date", "transaction_date")},
            {_mr_column("schedule_date", "schedule_date")},
            {_mr_column("material_request_type", "material_request_type")},
            {_mr_column("workflow_state", "workflow_state")},
            {_mr_column("status", "status")},
            mr.docstatus AS docstatus,
            {_mr_column("company", "company")},
            {_mr_column("sales_order", "sales_order")},
            {_mr_column("customer", "customer")},
            {_mr_column("customer_name", "customer_name")},
            {_mr_column(MR_BRANCH_FIELD, "branch")},
            {_mr_column("custom_customer_vip", "customer_vip")},
            {_mr_column("custom_is_urgent", "is_urgent")},
            {_mr_column("custom_request_scenario", "request_scenario")},
            {_mr_column("custom_manufacturing_status", "manufacturing_status")},
            {_mr_column("custom_component_manufacturing_status", "component_manufacturing_status")},
            {_mr_column("custom_delivery_readiness_status", "delivery_readiness_status")},
            {_mr_column("custom_workflow_state_duration", "workflow_state_duration")},
            {_mr_column("custom_manufacturing_remaining_count", "manufacturing_remaining_count")},
            {_mr_column("custom_component_manufacturing_remaining_count", "component_manufacturing_remaining_count")},
            IFNULL(item_totals.total_qty, 0) AS total_qty,
            IFNULL(item_totals.item_count, 0) AS item_count
        FROM `tabMaterial Request` mr
        LEFT JOIN (
            SELECT parent, SUM(qty) AS total_qty, COUNT(*) AS item_count
            FROM `tabMaterial Request Item`
            GROUP BY parent
        ) item_totals ON item_totals.parent = mr.name
        WHERE {where_sql}
        ORDER BY mr.modified DESC
        LIMIT %(limit)s
        """,
        params,
        as_dict=True,
    )

    for row in rows:
        row["customer_display"] = row.get("customer_name") or row.get("customer")
        row["customer_vip"] = "نعم" if cint(row.get("customer_vip")) else ""
        row["is_urgent"] = "نعم" if cint(row.get("is_urgent")) else ""
    return rows


def get_all_material_requests_columns() -> list[dict[str, Any]]:
    return [
        _column("طلب المواد", "material_request", "Link", 160, "Material Request"),
        _column("تاريخ الطلب", "transaction_date", "Date", 110),
        _column("تاريخ الاستحقاق", "schedule_date", "Date", 120),
        _column("نوع الطلب", "material_request_type", "Data", 110),
        _column("حالة Workflow", "workflow_state", "Link", 150, "Workflow State"),
        _column("الحالة", "status", "Data", 120),
        _column("الشركة", "company", "Link", 140, "Company"),
        _column("أمر البيع", "sales_order", "Link", 150, "Sales Order"),
        _column("العميل", "customer", "Link", 150, "Customer"),
        _column("اسم العميل", "customer_display", "Data", 190),
        _column("الفرع", "branch", "Link", 110, "Branch"),
        _column("VIP", "customer_vip", "Data", 70),
        _column("مستعجل", "is_urgent", "Data", 80),
        _column("سيناريو الطلب", "request_scenario", "Data", 120),
        _column("حالة التصنيع", "manufacturing_status", "Data", 130),
        _column("حالة تصنيع المكونات", "component_manufacturing_status", "Data", 150),
        _column("جاهزية التوريد", "delivery_readiness_status", "Data", 130),
        _column("مدة الحالة", "workflow_state_duration", "Duration", 110),
        _column("متبقي التصنيع", "manufacturing_remaining_count", "Float", 120),
        _column("متبقي المكونات", "component_manufacturing_remaining_count", "Float", 130),
        _column("عدد الأصناف", "item_count", "Int", 90),
        _column("إجمالي الكمية", "total_qty", "Float", 120),
    ]


def get_sales_order_summary_columns() -> list[dict[str, Any]]:
    return [
        _column("الصنف", "item_code", "Link", 180, "Item"),
        _column("اسم الصنف", "item_name", "Data", 220),
        _column("مطلوب أمر البيع", "so_qty", "Float", 120),
        _column("تم طلبه في الطلب الحالي", "current_mr_qty", "Float", 150),
        _column("تم طلبه إجماليًا", "mr_qty", "Float", 130),
        _column("المتبقي", "balance", "Float", 110),
        _column("مسلم للطلب الحالي", "current_delivered_qty", "Float", 140),
        _column("مفوتر للطلب الحالي", "current_billed_qty", "Float", 140),
        _column("مركب للطلب الحالي", "current_installed_qty", "Float", 140),
        _column("رصيد المسلم", "current_delivered_balance", "Float", 120),
        _column("رصيد المفوتر", "current_billed_balance", "Float", 120),
        _column("رصيد التركيب", "current_installed_balance", "Float", 120),
        _column("إضافي", "is_extra", "Check", 80),
    ]


def execute_all_material_requests_report(filters: dict[str, Any] | None = None):
    filters = _filters_dict(filters)
    if filters.get("view_mode") == "ملخص أمر البيع":
        rows = get_related_items(
            sales_order=filters.get("sales_order"),
            mr_name=filters.get("material_request"),
        )
        return get_sales_order_summary_columns(), rows
    return get_all_material_requests_columns(), get_all_material_requests(filters)
