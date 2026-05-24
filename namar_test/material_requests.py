from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import add_days, cint, flt, nowdate


MR_BRANCH_FIELD = "الفرع"
PERSON_FIELDNAMES = {
    "manufactured_by",
    "custom_manufactured_by",
    "manufacturing_completed_by",
    "custom_manufacturing_completed_by",
    "completed_by",
    "employee",
    "employee_name",
    "owner",
    "modified_by",
}
PERSON_LABEL_HINTS = ("تم بواسطة", "بواسطة التصنيع", "الموظف", "المستخدم")
DELEGATED_REPORTS = {
    "نتائج التخصيم": "نتائج التخصيم",
    "تفاصيل المخازن": "داشبورد تفاصيل المخازن",
}


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
    if isinstance(filters, str):
        try:
            filters = frappe.parse_json(filters)
        except Exception:
            filters = {}
    return frappe._dict(filters or {})


def _has_mri_column(fieldname: str) -> bool:
    return bool(frappe.db.has_column("Material Request Item", fieldname))


def _can_view_person_fields() -> bool:
    roles = set(frappe.get_roles(frappe.session.user))
    if "System Manager" in roles:
        return True
    if frappe.db.exists("DocType", "Employee") and frappe.has_permission("Employee", "read"):
        return True
    return bool(roles.intersection({"HR Manager", "HR User"}))


def _column_fieldname(column: Any) -> str:
    if isinstance(column, dict):
        return (column.get("fieldname") or "").strip()
    if isinstance(column, str):
        return (column.split(":", 1)[0] or "").strip()
    return ""


def _column_label(column: Any) -> str:
    if isinstance(column, dict):
        return (column.get("label") or column.get("fieldname") or "").strip()
    if isinstance(column, str):
        return (column.split(":", 1)[0] or "").strip()
    return ""


def _is_person_column(column: Any) -> bool:
    fieldname = _column_fieldname(column)
    label = _column_label(column)
    if fieldname in PERSON_FIELDNAMES:
        return True
    if any(hint in label for hint in PERSON_LABEL_HINTS):
        return True
    return False


def _apply_person_permissions(columns: list[Any], rows: list[Any]) -> tuple[list[Any], list[Any]]:
    if _can_view_person_fields():
        return columns, rows

    blocked_indexes = []
    allowed_columns = []
    blocked_fieldnames = set()
    for index, column in enumerate(columns or []):
        if _is_person_column(column):
            blocked_indexes.append(index)
            fieldname = _column_fieldname(column)
            if fieldname:
                blocked_fieldnames.add(fieldname)
            continue
        allowed_columns.append(column)

    cleaned_rows = []
    for row in rows or []:
        if isinstance(row, dict):
            cleaned = dict(row)
            for fieldname in blocked_fieldnames:
                cleaned.pop(fieldname, None)
            cleaned_rows.append(cleaned)
        elif isinstance(row, (list, tuple)) and blocked_indexes:
            cleaned_rows.append([value for index, value in enumerate(row) if index not in blocked_indexes])
        else:
            cleaned_rows.append(row)
    return allowed_columns, cleaned_rows


def _safe_limit(filters: frappe._dict, default: int = 500, maximum: int = 2000) -> int:
    return max(1, min(cint(filters.get("limit")) or default, maximum))


def _empty_report(message: str):
    return [_column("رسالة", "message", "Data", 420)], [{"message": message}]


def _run_existing_report(report_name: str, filters: frappe._dict):
    from frappe.desk.query_report import run

    delegated_filters = {
        key: value
        for key, value in dict(filters).items()
        if key not in {"view_mode", "operation_preset", "limit"} and value not in (None, "")
    }
    try:
        response = run(
            report_name=report_name,
            filters=frappe.as_json(delegated_filters),
            ignore_prepared_report=1,
        )
    except Exception as exc:
        columns, rows = _empty_report(f"تعذر تشغيل التقرير المرتبط {report_name}: {exc}")
        return columns, rows

    columns = response.get("columns") or []
    rows = response.get("result") or []
    columns, rows = _apply_person_permissions(columns, rows)
    return (
        columns,
        rows,
        response.get("message"),
        response.get("chart"),
        response.get("report_summary"),
        response.get("skip_total_row") or 0,
    )


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


def get_manufacturing_daily_report(filters: dict[str, Any] | None = None):
    filters = _filters_dict(filters)
    can_view_people = _can_view_person_fields()

    columns = [
        _column("وقت التصنيع", "manufactured_at", "Datetime", 155),
        _column("نوع السطر", "line_type", "Data", 90),
        _column("طلب المواد", "material_request", "Link", 130, "Material Request"),
        _column("أمر البيع", "sales_order", "Link", 130, "Sales Order"),
        _column("حالة الطلب", "workflow_state", "Data", 120),
        _column("العميل", "customer_name", "Data", 180),
        _column("رقم الباب", "door_no", "Int", 80),
        _column("المكون", "component_label", "Data", 130),
        _column("كود الصنف", "item_code", "Link", 130, "Item"),
        _column("اسم الصنف", "item_name", "Data", 220),
        _column("مجموعة الصنف", "item_group", "Link", 150, "Item Group"),
        _column("المطلوب", "required_qty", "Float", 90),
        _column("الكمية المصنعة", "manufactured_qty", "Float", 110),
        _column("المتبقي", "remaining_qty", "Float", 90),
        _column("حالة السطر", "manufacturing_status", "Data", 100),
    ]
    if can_view_people:
        columns.append(_column("تم بواسطة", "manufactured_by", "Link", 170, "User"))

    if not frappe.db.exists("DocType", "Material Request Manufacturing Detail"):
        return columns, [], "لا يوجد جدول تفاصيل التصنيع في هذه البيئة.", None, [], 0

    conditions = [
        "mr.docstatus = 1",
        "IFNULL(mrd.manufactured_qty, 0) > 0",
    ]
    values: dict[str, Any] = {"limit": _safe_limit(filters, default=500)}

    if filters.get("from_date"):
        conditions.append("DATE(mrd.manufactured_at) >= %(from_date)s")
        values["from_date"] = filters.from_date
    if filters.get("to_date"):
        conditions.append("DATE(mrd.manufactured_at) <= %(to_date)s")
        values["to_date"] = filters.to_date
    if filters.get("material_request"):
        conditions.append("mrd.parent = %(material_request)s")
        values["material_request"] = filters.material_request
    if filters.get("sales_order"):
        conditions.append("IFNULL(mr.sales_order, '') = %(sales_order)s")
        values["sales_order"] = filters.sales_order
    if filters.get("customer_name"):
        conditions.append("INSTR(IFNULL(mr.customer_name, ''), %(customer_name)s) > 0")
        values["customer_name"] = filters.customer_name
    if filters.get("workflow_state"):
        conditions.append("IFNULL(mr.workflow_state, '') = %(workflow_state)s")
        values["workflow_state"] = filters.workflow_state
    if filters.get("manufactured_by") and can_view_people:
        conditions.append("IFNULL(mrd.manufactured_by, '') = %(manufactured_by)s")
        values["manufactured_by"] = filters.manufactured_by
    if filters.get("line_type"):
        conditions.append("mrd.line_type = %(line_type)s")
        values["line_type"] = filters.line_type
    if filters.get("item_code"):
        conditions.append("mrd.item_code = %(item_code)s")
        values["item_code"] = filters.item_code
    if filters.get("item_group"):
        conditions.append("IFNULL(it.item_group, '') = %(item_group)s")
        values["item_group"] = filters.item_group

    select_parts = [
        "mrd.manufactured_at AS manufactured_at",
        "mrd.line_type AS line_type",
        "mrd.parent AS material_request",
        "mr.sales_order AS sales_order",
        "mr.workflow_state AS workflow_state",
        "IFNULL(mr.customer_name, '') AS customer_name",
        "mrd.material_request_row AS door_no",
        "IFNULL(mrd.component_label, '') AS component_label",
        "mrd.item_code AS item_code",
        "mrd.item_name AS item_name",
        "IFNULL(it.item_group, '') AS item_group",
        "mrd.required_qty AS required_qty",
        "mrd.manufactured_qty AS manufactured_qty",
        "mrd.remaining_qty AS remaining_qty",
        "mrd.status AS manufacturing_status",
    ]
    if can_view_people:
        select_parts.append("IFNULL(mrd.manufactured_by, '') AS manufactured_by")

    rows = frappe.db.sql(
        f"""
        SELECT {", ".join(select_parts)}
        FROM `tabMaterial Request Manufacturing Detail` mrd
        INNER JOIN `tabMaterial Request` mr ON mr.name = mrd.parent
        LEFT JOIN `tabItem` it ON it.name = mrd.item_code
        WHERE {" AND ".join(conditions)}
        ORDER BY
            IFNULL(mrd.manufactured_at, '') DESC,
            mrd.parent DESC,
            mrd.material_request_row ASC,
            mrd.line_type ASC,
            mrd.component_label ASC
        LIMIT %(limit)s
        """,
        values,
        as_dict=True,
    )

    total_manufactured_qty = 0
    request_names = {}
    item_codes = {}
    line_type_totals = {}
    for row in rows:
        manufactured_qty = flt(row.get("manufactured_qty") or 0)
        total_manufactured_qty += manufactured_qty
        if row.get("material_request"):
            request_names[row.material_request] = 1
        if row.get("item_code"):
            item_codes[row.item_code] = 1
        line_type = row.get("line_type") or "غير محدد"
        line_type_totals[line_type] = line_type_totals.get(line_type, 0) + manufactured_qty

    report_summary = [
        {"value": total_manufactured_qty, "label": "إجمالي الكمية المصنعة", "datatype": "Float", "indicator": "green"},
        {"value": len(rows), "label": "عدد السطور المصنعة", "datatype": "Int", "indicator": "blue"},
        {"value": len(request_names), "label": "عدد طلبات المواد", "datatype": "Int", "indicator": "blue"},
        {"value": len(item_codes), "label": "عدد الأصناف", "datatype": "Int", "indicator": "orange"},
    ]
    for line_type, manufactured_qty in sorted(line_type_totals.items()):
        report_summary.append(
            {
                "value": manufactured_qty,
                "label": "مصنع - " + line_type,
                "datatype": "Float",
                "indicator": "green" if line_type == "باب" else "blue",
            }
        )

    message = "لا توجد أبواب أو مكونات مصنعة ضمن الفلاتر الحالية." if not rows else None
    if filters.get("manufactured_by") and not can_view_people:
        message = "تم إخفاء فلتر وعمود المستخدم لعدم وجود صلاحية قراءة بيانات الموظفين."
    return columns, rows, message, None, report_summary, 0


def get_manufacturing_followup_report(filters: dict[str, Any] | None = None):
    filters = _filters_dict(filters)
    required_fields = ("custom_is_manufactured", "custom_manufactured_at", "custom_manufactured_by")
    if not all(_has_mri_column(fieldname) for fieldname in required_fields):
        columns, rows = _empty_report("حقول متابعة التصنيع غير موجودة على سطور طلب المواد.")
        return columns, rows

    can_view_people = _can_view_person_fields()
    columns = [
        _column("تاريخ التصنيع", "manufactured_at", "Datetime", 170),
        _column("طلب المواد", "material_request", "Link", 150, "Material Request"),
        _column("رقم الباب", "door_no", "Int", 90),
        _column("كود الصنف", "item_code", "Link", 140, "Item"),
        _column("اسم الصنف", "item_name", "Data", 260),
        _column("الكمية", "qty", "Float", 90),
        _column("الكمية المتبقية", "remaining_qty", "Float", 110),
        _column("العميل", "customer_name", "Data", 220),
        _column("الحالة", "workflow_state", "Data", 140),
    ]
    if can_view_people:
        columns.append(_column("بواسطة التصنيع", "manufactured_by", "Data", 180))

    conditions = ["IFNULL(mri.custom_is_manufactured, 0) = 1"]
    values: dict[str, Any] = {"limit": _safe_limit(filters, default=500)}
    if filters.get("from_date"):
        conditions.append("DATE(mri.custom_manufactured_at) >= %(from_date)s")
        values["from_date"] = filters.from_date
    if filters.get("to_date"):
        conditions.append("DATE(mri.custom_manufactured_at) <= %(to_date)s")
        values["to_date"] = filters.to_date
    if filters.get("material_request"):
        conditions.append("mri.parent = %(material_request)s")
        values["material_request"] = filters.material_request
    if filters.get("item_code"):
        conditions.append("mri.item_code = %(item_code)s")
        values["item_code"] = filters.item_code
    if filters.get("customer_name"):
        conditions.append("INSTR(IFNULL(mr.customer_name, ''), %(customer_name)s) > 0")
        values["customer_name"] = filters.customer_name
    if filters.get("manufactured_by") and can_view_people:
        conditions.append("IFNULL(mri.custom_manufactured_by, '') = %(manufactured_by)s")
        values["manufactured_by"] = filters.manufactured_by

    select_parts = [
        "mri.custom_manufactured_at AS manufactured_at",
        "mr.name AS material_request",
        "mri.idx AS door_no",
        "mri.item_code AS item_code",
        "mri.item_name AS item_name",
        "mri.qty AS qty",
        "0 AS remaining_qty",
        "COALESCE(mr.customer_name, mr.customer, '') AS customer_name",
        "mr.workflow_state AS workflow_state",
    ]
    if can_view_people:
        select_parts.append("mri.custom_manufactured_by AS manufactured_by")

    rows = frappe.db.sql(
        f"""
        SELECT {", ".join(select_parts)}
        FROM `tabMaterial Request Item` mri
        INNER JOIN `tabMaterial Request` mr ON mr.name = mri.parent
        WHERE {" AND ".join(conditions)}
        ORDER BY mri.custom_manufactured_at DESC, mr.name DESC, mri.idx ASC
        LIMIT %(limit)s
        """,
        values,
        as_dict=True,
    )
    message = None
    if filters.get("manufactured_by") and not can_view_people:
        message = "تم إخفاء فلتر وعمود المستخدم لعدم وجود صلاحية قراءة بيانات الموظفين."
    return columns, rows, message, None, [{"value": len(rows), "label": "عدد السطور", "datatype": "Int"}], 0


def get_store_details_report(filters: dict[str, Any] | None = None):
    filters = _filters_dict(filters)
    if not _has_mri_column("custom_store_data"):
        columns, rows = _empty_report("حقل بيانات المخازن غير موجود على سطور طلب المواد.")
        return columns, rows

    columns = [
        _column("طلب المواد", "material_request", "Link", 140, "Material Request"),
        _column("التاريخ", "transaction_date", "Date", 110),
        _column("الحالة", "workflow_state", "Data", 140),
        _column("العميل", "customer_name", "Data", 180),
        _column("كود الصنف", "item_code", "Link", 140, "Item"),
        _column("اسم الصنف", "item_name", "Data", 220),
        _column("كمية السطر", "line_qty", "Float", 90),
        _column("النموذج", "cutting_template", "Data", 140),
        _column("المكون", "component", "Data", 130),
        _column("الصنف المخزني", "store_item", "Link", 180, "Item"),
        _column("اللون", "color", "Data", 100),
        _column("كمية المخزن", "store_qty", "Float", 110),
    ]

    conditions = ["mr.docstatus < 2", "IFNULL(mri.custom_store_data, '') != ''"]
    values: dict[str, Any] = {"limit": _safe_limit(filters, default=500)}
    if filters.get("from_date"):
        conditions.append("mr.transaction_date >= %(from_date)s")
        values["from_date"] = filters.from_date
    if filters.get("to_date"):
        conditions.append("mr.transaction_date <= %(to_date)s")
        values["to_date"] = filters.to_date
    if filters.get("workflow_state"):
        conditions.append("mr.workflow_state = %(workflow_state)s")
        values["workflow_state"] = filters.workflow_state
    if filters.get("material_request"):
        conditions.append("mri.parent = %(material_request)s")
        values["material_request"] = filters.material_request
    if filters.get("customer_name"):
        conditions.append("INSTR(IFNULL(mr.customer_name, ''), %(customer_name)s) > 0")
        values["customer_name"] = filters.customer_name
    if filters.get("item_code"):
        conditions.append("mri.item_code = %(item_code)s")
        values["item_code"] = filters.item_code

    item_rows = frappe.db.sql(
        f"""
        SELECT
            mri.parent AS material_request,
            mr.transaction_date,
            mr.workflow_state,
            mr.customer_name,
            mri.item_code,
            mri.item_name,
            mri.qty AS line_qty,
            {_mri_optional_column("custom_cutting_template", "cutting_template")},
            mri.custom_store_data AS store_data
        FROM `tabMaterial Request Item` mri
        INNER JOIN `tabMaterial Request` mr ON mr.name = mri.parent
        WHERE {" AND ".join(conditions)}
        ORDER BY mr.transaction_date DESC, mri.parent DESC, mri.idx ASC
        LIMIT %(limit)s
        """,
        values,
        as_dict=True,
    )

    component_labels = {
        row.component_name: row.label_ar or row.component_name
        for row in frappe.get_all("Store Component", fields=["component_name", "label_ar"], limit_page_length=0)
    } if frappe.db.exists("DocType", "Store Component") else {}

    selected_component = (filters.get("component") or "").strip()
    rows = []
    for item in item_rows:
        try:
            stores = frappe.parse_json(item.store_data) or []
        except Exception:
            stores = []
        if not isinstance(stores, list):
            continue
        for store in stores:
            if not isinstance(store, dict):
                continue
            component = (store.get("component") or "").strip()
            if selected_component and component != selected_component:
                continue
            rows.append(
                {
                    "material_request": item.material_request,
                    "transaction_date": item.transaction_date,
                    "workflow_state": item.workflow_state,
                    "customer_name": item.customer_name,
                    "item_code": item.item_code,
                    "item_name": item.item_name,
                    "line_qty": item.line_qty,
                    "cutting_template": item.cutting_template,
                    "component": component_labels.get(component, component),
                    "store_item": store.get("item") or store.get("item_code"),
                    "color": store.get("color") or store.get("colour") or "",
                    "store_qty": flt(store.get("qty")) * flt(item.line_qty or 1),
                }
            )
    message = "لا توجد بيانات مخازن ضمن الفلاتر الحالية." if not rows else None
    return columns, rows, message, None, [{"value": len(rows), "label": "عدد السطور", "datatype": "Int"}], 0


def _mri_optional_column(fieldname: str, alias: str) -> str:
    if not _has_mri_column(fieldname):
        return f"NULL AS {_quote_identifier(alias)}"
    return f"mri.{_quote_identifier(fieldname)} AS {_quote_identifier(alias)}"


def get_operational_states_report(filters: dict[str, Any] | None = None):
    filters = _filters_dict(filters)
    preset = filters.get("operation_preset") or "جاري التصنيع"
    if preset == "طلبات التصنيع":
        return get_manufacturing_requests_items_report(filters)

    columns = [
        _column("حالة Workflow", "workflow_state", "Data", 150),
        _column("طلب المواد", "material_request", "Link", 150, "Material Request"),
        _column("حالة المستند", "docstatus", "Int", 90),
        _column("تاريخ الطلب", "transaction_date", "Date", 110),
        _column("أمر البيع", "sales_order", "Link", 140, "Sales Order"),
        _column("العميل", "customer_name", "Data", 190),
        _column("الجوال", "mobile_no", "Data", 120),
        _column("المنطقة", "territory", "Link", 120, "Territory"),
        _column("الحي", "district", "Data", 120),
        _column("رابط الخريطة", "google_map", "Data", 220),
    ]

    conditions = ["mr.docstatus < 2"]
    values: dict[str, Any] = {"limit": _safe_limit(filters, default=100)}
    if preset == "جاري التصنيع":
        conditions.append("mr.workflow_state IN ('جاري تصنيع الاستبدال', 'جاري التصنيع')")
    elif preset == "توريدات معلقة":
        conditions.append("mr.workflow_state IN ('تم تصنيع الاستبدال', 'تم التصنيع')")
    elif preset == "مقاسات معلقة":
        conditions.append("mr.workflow_state = 'مقاسات معلقة'")
    elif preset == "صيانة معلقة":
        conditions.append("mr.workflow_state = 'صيانة معلقة'")
        if _has_mr_column("company"):
            conditions.append("IFNULL(mr.company, '') LIKE %(company_like)s")
            values["company_like"] = "%شركة رواد المهارة للمقاولات المعمارية%"
    elif preset == "استحقاق خلال أسبوعين":
        conditions.append("mr.workflow_state IN ('جاري التصنيع', 'اعتماد التصنيع', 'تم التصنيع', 'المصنع')")
        if _has_mr_column("delivery_date"):
            conditions.append("mr.delivery_date BETWEEN %(today)s AND %(after_14)s")
            values["today"] = nowdate()
            values["after_14"] = add_days(nowdate(), 14)
        if _has_mr_column(MR_BRANCH_FIELD):
            conditions.append(f"IFNULL(mr.{_quote_identifier(MR_BRANCH_FIELD)}, '') != 'المصنع'")

    if filters.get("material_request"):
        conditions.append("mr.name = %(material_request)s")
        values["material_request"] = filters.material_request
    if filters.get("customer_name"):
        conditions.append("INSTR(IFNULL(mr.customer_name, ''), %(customer_name)s) > 0")
        values["customer_name"] = filters.customer_name
    if filters.get("workflow_state"):
        conditions.append("mr.workflow_state = %(workflow_state)s")
        values["workflow_state"] = filters.workflow_state

    rows = frappe.db.sql(
        f"""
        SELECT
            {_mr_column("workflow_state", "workflow_state")},
            mr.name AS material_request,
            mr.docstatus,
            {_mr_column("transaction_date", "transaction_date")},
            {_mr_column("sales_order", "sales_order")},
            {_mr_column("customer_name", "customer_name")},
            {_mr_column("custom_mobile_no", "mobile_no")},
            {_mr_column("territory", "territory")},
            {_mr_column("custom_district", "district")},
            {_mr_column("custom_google_map", "google_map")}
        FROM `tabMaterial Request` mr
        WHERE {" AND ".join(conditions)}
        ORDER BY mr.transaction_date DESC, mr.name DESC
        LIMIT %(limit)s
        """,
        values,
        as_dict=True,
    )
    return columns, rows, None, None, [{"value": len(rows), "label": preset, "datatype": "Int"}], 0


def get_manufacturing_requests_items_report(filters: dict[str, Any] | None = None):
    filters = _filters_dict(filters)
    columns = [
        _column("طلب المواد", "material_request", "Link", 150, "Material Request"),
        _column("العميل", "customer", "Link", 140, "Customer"),
        _column("التاريخ", "transaction_date", "Date", 110),
        _column("الحالة", "status", "Data", 110),
        _column("فاتورة المبيعات", "sales_invoice", "Link", 140, "Sales Invoice"),
        _column("الكمية", "qty", "Float", 90),
        _column("الصنف", "item_code", "Link", 140, "Item"),
        _column("اسم الصنف", "item_name", "Data", 220),
        _column("نوع الباب", "door_type", "Data", 110),
        _column("عرض", "width", "Float", 90),
        _column("طول", "height", "Float", 90),
        _column("عرض الجدار", "wall_width", "Float", 100),
        _column("ملاحظات", "notes", "Data", 220),
    ]
    conditions = ["mr.docstatus != 2"]
    values: dict[str, Any] = {"limit": _safe_limit(filters, default=200)}
    if filters.get("material_request"):
        conditions.append("mr.name = %(material_request)s")
        values["material_request"] = filters.material_request
    if filters.get("item_code"):
        conditions.append("mri.item_code = %(item_code)s")
        values["item_code"] = filters.item_code
    if filters.get("customer_name"):
        conditions.append("INSTR(COALESCE(mr.customer_name, mr.customer, ''), %(customer_name)s) > 0")
        values["customer_name"] = filters.customer_name

    rows = frappe.db.sql(
        f"""
        SELECT
            mr.name AS material_request,
            {_mr_column("customer", "customer")},
            {_mr_column("transaction_date", "transaction_date")},
            {_mr_column("status", "status")},
            {_mr_column("sales_invoice", "sales_invoice")},
            mri.qty,
            mri.item_code,
            mri.item_name,
            {_mri_optional_column("نوع_الباب", "door_type")},
            {_mri_optional_column("عرض", "width")},
            {_mri_optional_column("طول", "height")},
            {_mri_optional_column("عرض_الجدار", "wall_width")},
            {_mri_optional_column("ملاحظات", "notes")}
        FROM `tabMaterial Request` mr
        INNER JOIN `tabMaterial Request Item` mri ON mri.parent = mr.name
        WHERE {" AND ".join(conditions)}
        ORDER BY mr.transaction_date DESC, mr.name DESC, mri.idx ASC
        LIMIT %(limit)s
        """,
        values,
        as_dict=True,
    )
    return columns, rows, None, None, [{"value": len(rows), "label": "طلبات التصنيع", "datatype": "Int"}], 0


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


@frappe.whitelist()
def execute_all_material_requests_report(filters: dict[str, Any] | None = None):
    filters = _filters_dict(filters)
    view_mode = filters.get("view_mode") or "طلبات المواد"

    if view_mode == "ملخص أمر البيع":
        rows = get_related_items(
            sales_order=filters.get("sales_order"),
            mr_name=filters.get("material_request"),
        )
        return get_sales_order_summary_columns(), rows
    if view_mode == "نتائج التخصيم":
        return _run_existing_report(DELEGATED_REPORTS[view_mode], filters)
    if view_mode == "التصنيع اليومي":
        return get_manufacturing_daily_report(filters)
    if view_mode == "متابعة التصنيع":
        return get_manufacturing_followup_report(filters)
    if view_mode == "تفاصيل المخازن":
        return get_store_details_report(filters)
    if view_mode == "حالات تشغيلية":
        return get_operational_states_report(filters)
    return get_all_material_requests_columns(), get_all_material_requests(filters)
