from __future__ import annotations

import math
from typing import Any, Iterable

import frappe
from frappe.model import get_permitted_fields
from frappe.model.workflow import (
    apply_workflow as frappe_apply_workflow,
    get_transitions,
    get_workflow,
    get_workflow_name,
)
from frappe.utils import cint, flt, nowdate

from namar_test.operation.logic import (
    MAX_PAGE_LENGTH,
    clean_text,
    date_not_before,
    normalize_item_payloads,
    page_window,
    parse_mapping,
    role_can_edit,
    sanitize_fields,
    timestamps_match,
)


DOCTYPE = "Material Request"
ITEM_DOCTYPE = "Material Request Item"

HEADER_EDITABLE_FIELDS = (
    "transaction_date",
    "material_request_type",
    "delivery_date",
    "company",
    "territory",
    "custom_district",
    "الفرع",
)

ITEM_EDITABLE_FIELDS = (
    "item_code",
    "qty",
    "uom",
    "warehouse",
    "schedule_date",
    "description",
)

HEADER_DETAIL_FIELDS = (
    "name",
    "owner",
    "creation",
    "modified",
    "modified_by",
    "docstatus",
    "workflow_state",
    "status",
    "title",
    "material_request_type",
    "transaction_date",
    "schedule_date",
    "delivery_date",
    "company",
    "set_warehouse",
    "set_from_warehouse",
    "sales_order",
    "territory",
    "custom_district",
    "الفرع",
    "project",
    "cost_center",
    "custom_planned_date",
    "custom_installation_date",
    "custom_request_scenario",
    "custom_request_kind",
    "custom_scenario_reference",
    "custom_reference_material_request",
    "custom_project_name",
    "custom_mobile_no",
    "custom_google_map",
    "custom_latitude",
    "custom_longitude",
    "customer",
    "total_qty",
    "per_ordered",
    "per_received",
)

OPERATION_READONLY_FIELDS = (
    "custom_manufacturing_status",
    "custom_manufacturing_remaining_count",
    "custom_manufacturing_total_count",
    "custom_manufacturing_completed_at",
    "custom_manufacturing_completed_by",
    "custom_component_manufacturing_status",
    "custom_component_manufacturing_remaining_count",
    "custom_component_manufacturing_total_count",
    "custom_delivery_readiness_status",
    "custom_delivery_readiness_summary",
    "custom_manufactured_doors",
    "custom_delivery_loading_code",
    "custom_delivery_component_status",
    "custom_delivery_component_remaining_packages",
    "custom_delivery_component_total_packages",
    "custom_delivery_component_summary",
)

ITEM_DETAIL_FIELDS = (
    "name",
    "idx",
    "item_code",
    "item_name",
    "description",
    "qty",
    "uom",
    "stock_uom",
    "conversion_factor",
    "stock_qty",
    "warehouse",
    "from_warehouse",
    "schedule_date",
    "project",
    "cost_center",
    "branch",
    "sales_order",
    "sales_order_item",
    "bom_no",
    "actual_qty",
    "ordered_qty",
    "received_qty",
    "rate",
    "amount",
    "custom_manufactured_qty",
    "custom_is_manufactured",
    "custom_manufactured_at",
    "custom_manufactured_by",
    "custom_handle_color",
    "custom_hinge_option",
)

LIST_FIELDS = (
    "name",
    "modified",
    "modified_by",
    "docstatus",
    "workflow_state",
    "status",
    "material_request_type",
    "transaction_date",
    "schedule_date",
    "delivery_date",
    "sales_order",
    "company",
    "territory",
    "custom_district",
    "الفرع",
    "custom_project_name",
    "custom_request_scenario",
    "custom_request_kind",
    "total_qty",
    *OPERATION_READONLY_FIELDS,
)

SEARCH_CONFIG = {
    "Sales Order": {
        "fields": (
            "name",
            "customer",
            "customer_name",
            "transaction_date",
            "delivery_date",
            "status",
            "company",
        ),
        "search_fields": ("name", "customer", "customer_name"),
        "filters": {"docstatus": 1, "status": ["not in", ["Closed", "Cancelled"]]},
    },
    "Item": {
        "fields": ("name", "item_name", "stock_uom", "default_warehouse"),
        "search_fields": ("name", "item_name"),
        "filters": {"disabled": 0},
    },
    "Warehouse": {
        "fields": ("name", "warehouse_name", "company"),
        "search_fields": ("name", "warehouse_name"),
        "filters": {"disabled": 0, "is_group": 0},
    },
    "Company": {"fields": ("name",), "search_fields": ("name",), "filters": {}},
    "UOM": {"fields": ("name",), "search_fields": ("name",), "filters": {"enabled": 1}},
    "Branch": {"fields": ("name",), "search_fields": ("name",), "filters": {}},
    "Territory": {"fields": ("name",), "search_fields": ("name",), "filters": {}},
    "Project": {
        "fields": ("name", "project_name", "status"),
        "search_fields": ("name", "project_name"),
        "filters": {"status": "Open"},
    },
    "Cost Center": {
        "fields": ("name", "cost_center_name", "company"),
        "search_fields": ("name", "cost_center_name"),
        "filters": {"disabled": 0, "is_group": 0},
    },
}

OPTION_TYPE_DOCTYPE = {
    "sales_order": "Sales Order",
    "item": "Item",
    "warehouse": "Warehouse",
    "uom": "UOM",
}

MAX_LIST_OFFSET = MAX_PAGE_LENGTH * 1000


def _assert_authenticated() -> None:
    if frappe.session.user == "Guest":
        frappe.throw(
            "يلزم تسجيل الدخول لاستخدام صفحة التشغيل",
            frappe.PermissionError,
        )


def _existing_fields(doctype: str, fields: Iterable[str]) -> list[str]:
    meta = frappe.get_meta(doctype)
    return [
        fieldname
        for fieldname in fields
        if fieldname in meta.default_fields or meta.has_field(fieldname)
    ]


def _readable_fields(doctype: str, fields: Iterable[str], parenttype: str | None = None) -> list[str]:
    permission_type = "select" if frappe.only_has_select_perm(doctype) else "read"
    permitted = set(
        get_permitted_fields(
            doctype,
            parenttype=parenttype,
            permission_type=permission_type,
        )
    )
    return [fieldname for fieldname in _existing_fields(doctype, fields) if fieldname in permitted]


def _editable_fields(
    doc,
    fields: Iterable[str],
    *,
    doctype: str | None = None,
    permission_type: str = "write",
) -> list[str]:
    target_doctype = doctype or doc.doctype
    meta = frappe.get_meta(target_doctype)
    allowed_permlevels = set(doc.get_permlevel_access(permission_type))
    permitted: list[str] = []
    for fieldname in _existing_fields(target_doctype, fields):
        field = meta.get_field(fieldname)
        if not field or cint(field.read_only) or cint(field.permlevel or 0) not in allowed_permlevels:
            continue
        permitted.append(fieldname)
    return permitted


def _workflow_states() -> list[str]:
    workflow_name = get_workflow_name(DOCTYPE)
    if not workflow_name:
        return []
    return [clean_text(row.state) for row in get_workflow(DOCTYPE).states if clean_text(row.state)]


def _workflow_allows_edit(doc) -> bool:
    workflow_name = get_workflow_name(DOCTYPE)
    if not workflow_name:
        return True
    workflow = get_workflow(DOCTYPE)
    current_state = clean_text(doc.get(workflow.workflow_state_field))
    state_row = next((row for row in workflow.states if clean_text(row.state) == current_state), None)
    if not state_row:
        return True
    return role_can_edit(
        state_row.allow_edit,
        frappe.get_roles(),
        is_administrator=frappe.session.user == "Administrator",
    )


def _can_edit(doc) -> bool:
    return cint(doc.docstatus) == 0 and doc.has_permission("write") and _workflow_allows_edit(doc)


def _assert_can_edit(doc) -> None:
    doc.check_permission("write")
    if cint(doc.docstatus) != 0:
        frappe.throw(
            "لا يمكن تعديل الأصناف أو الكميات "
            "بعد اعتماد طلب المواد",
            frappe.ValidationError,
        )
    if not _workflow_allows_edit(doc):
        frappe.throw(
            "حالة الطلب الحالية لا تسمح لك بتعديل المسودة",
            frappe.PermissionError,
        )


def _available_actions(doc) -> list[dict[str, Any]]:
    if not get_workflow_name(DOCTYPE):
        return []
    return [
        {
            "action": clean_text(row.get("action")),
            "next_state": clean_text(row.get("next_state")),
            "allowed": clean_text(row.get("allowed")),
        }
        for row in get_transitions(doc)
        if clean_text(row.get("action"))
    ]


def _field_definitions(
    doctype: str,
    fields: Iterable[str],
    parenttype: str | None = None,
) -> list[dict[str, Any]]:
    meta = frappe.get_meta(doctype)
    readable = set(_readable_fields(doctype, fields, parenttype=parenttype))
    definitions = []
    for fieldname in fields:
        if fieldname not in readable:
            continue
        field = meta.get_field(fieldname)
        if not field:
            continue
        definitions.append(
            {
                "fieldname": fieldname,
                "label": field.label or fieldname,
                "fieldtype": field.fieldtype,
                "options": field.options or "",
                "reqd": cint(field.reqd),
                "read_only": cint(field.read_only),
            }
        )
    return definitions


def _option_lines(doctype: str, fieldname: str) -> list[str]:
    field = frappe.get_meta(doctype).get_field(fieldname)
    if not field or not field.options:
        return []
    return [line.strip() for line in str(field.options).splitlines() if line.strip()]


def get_bootstrap() -> dict[str, Any]:
    _assert_authenticated()
    if not frappe.has_permission(DOCTYPE, "read"):
        frappe.throw(
            "ليس لديك صلاحية لقراءة طلبات المواد",
            frappe.PermissionError,
        )
    return {
        "user": frappe.session.user,
        "today": nowdate(),
        "can_create": bool(frappe.has_permission(DOCTYPE, "create")),
        "statuses": _workflow_states(),
        "material_request_types": _option_lines(DOCTYPE, "material_request_type"),
        "views": [
            {"value": "all", "label": "الكل"},
            {"value": "draft", "label": "المسودات"},
            {"value": "submitted", "label": "المعتمدة"},
            {"value": "cancelled", "label": "الملغاة"},
        ],
        "header_fields": _field_definitions(DOCTYPE, (*HEADER_EDITABLE_FIELDS, "sales_order")),
        "item_fields": _field_definitions(ITEM_DOCTYPE, ITEM_EDITABLE_FIELDS, parenttype=DOCTYPE),
        "search_doctypes": list(SEARCH_CONFIG),
    }


def _list_filters(
    status: str | None,
    view: str | None,
    material_request_type: str | None,
    sales_order: str | None,
) -> dict[str, Any]:
    filters: dict[str, Any] = {}
    status = clean_text(status, 140)
    if status:
        if status not in _workflow_states():
            frappe.throw("حالة الطلب المحددة غير معتمدة", frappe.ValidationError)
        filters["workflow_state"] = status

    view = clean_text(view or "all", 20).lower()
    view_docstatus = {"all": None, "draft": 0, "submitted": 1, "cancelled": 2}
    if view not in view_docstatus:
        frappe.throw("طريقة العرض غير معتمدة", frappe.ValidationError)
    if view_docstatus[view] is not None:
        filters["docstatus"] = view_docstatus[view]

    request_type = clean_text(material_request_type, 140)
    if request_type:
        filters["material_request_type"] = request_type
    source_order = clean_text(sales_order, 140)
    if source_order and frappe.get_meta(DOCTYPE).has_field("sales_order"):
        filters["sales_order"] = source_order
    return filters


def _count_material_requests(filters: dict[str, Any], or_filters: list[list[Any]]) -> int:
    count_rows = frappe.get_list(
        DOCTYPE,
        filters=filters,
        or_filters=or_filters,
        fields=["count(name) as total"],
        order_by="",
        limit_page_length=1,
    )
    return cint(count_rows[0].get("total")) if count_rows else 0


def list_material_requests(
    search: str | None = None,
    status: str | None = None,
    docstatus: int | str | None = None,
    page: int | str | None = None,
    page_length: int | str | None = None,
    limit_start: int | str | None = None,
    view: str | None = None,
    material_request_type: str | None = None,
    sales_order: str | None = None,
) -> dict[str, Any]:
    _assert_authenticated()
    if not frappe.has_permission(DOCTYPE, "read"):
        frappe.throw(
            "ليس لديك صلاحية لقراءة طلبات المواد",
            frappe.PermissionError,
        )
    if limit_start not in (None, ""):
        try:
            safe_start = min(max(int(limit_start), 0), MAX_LIST_OFFSET)
        except (TypeError, ValueError):
            safe_start = 0
        try:
            requested_length = min(max(int(page_length or 20), 1), MAX_PAGE_LENGTH)
        except (TypeError, ValueError):
            requested_length = 20
        page = int(safe_start / requested_length) + 1
    safe_page, safe_length, offset, fetch_length = page_window(page, page_length)
    if limit_start not in (None, ""):
        offset = safe_start
    if docstatus not in (None, "") and not view:
        docstatus_views = {"0": "draft", "1": "submitted", "2": "cancelled"}
        view = docstatus_views.get(clean_text(docstatus))
        if not view:
            frappe.throw("قيمة docstatus غير معتمدة", frappe.ValidationError)
    filters = _list_filters(status, view, material_request_type, sales_order)
    fields = _readable_fields(DOCTYPE, LIST_FIELDS)

    search_text = clean_text(search, 100)
    or_filters: list[list[Any]] = []
    if search_text:
        search_fields = _readable_fields(
            DOCTYPE,
            ("name", "sales_order", "custom_project_name", "custom_district"),
        )
        or_filters = [[DOCTYPE, fieldname, "like", f"%{search_text}%"] for fieldname in search_fields]

    rows = frappe.get_list(
        DOCTYPE,
        filters=filters,
        or_filters=or_filters,
        fields=fields,
        order_by="modified desc, name desc",
        limit_start=offset,
        limit_page_length=fetch_length,
    )
    total = _count_material_requests(filters, or_filters)
    has_more = len(rows) > safe_length
    rows = rows[:safe_length]
    workflow_states = _workflow_states()
    return {
        "items": rows,
        "page": safe_page,
        "page_length": safe_length,
        "total": total,
        "has_more": has_more,
        "status": clean_text(status),
        "view": clean_text(view or "all"),
        "statuses": workflow_states,
        "workflow_states": workflow_states,
    }


def _readonly_summary(doc) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    readable = set(_readable_fields(DOCTYPE, OPERATION_READONLY_FIELDS))
    for fieldname in OPERATION_READONLY_FIELDS:
        if fieldname not in readable:
            continue
        value = doc.get(fieldname)
        if value in (None, "", [], {}):
            continue
        field = doc.meta.get_field(fieldname)
        rows.append(
            {
                "fieldname": fieldname,
                "label": field.label if field else fieldname,
                "fieldtype": field.fieldtype if field else "Data",
                "value": value,
            }
        )
    return rows


def _serialize_document(doc, *, prepared: bool = False) -> dict[str, Any]:
    doc.check_permission("create" if prepared else "read")
    if not prepared:
        doc.apply_fieldlevel_read_permissions()

    readable_header = set(_readable_fields(DOCTYPE, (*HEADER_DETAIL_FIELDS, *OPERATION_READONLY_FIELDS)))
    output = {
        fieldname: doc.get(fieldname)
        for fieldname in (*HEADER_DETAIL_FIELDS, *OPERATION_READONLY_FIELDS)
        if fieldname in readable_header
    }
    readable_items = set(_readable_fields(ITEM_DOCTYPE, ITEM_DETAIL_FIELDS, parenttype=DOCTYPE))
    output["items"] = [
        {fieldname: row.get(fieldname) for fieldname in ITEM_DETAIL_FIELDS if fieldname in readable_items}
        for row in doc.get("items") or []
    ]
    output["doctype"] = DOCTYPE
    return output


def _detail_response(doc, *, prepared: bool = False, message: str | None = None) -> dict[str, Any]:
    actions = [] if prepared else _available_actions(doc)
    result = {
        "doc": _serialize_document(doc, prepared=prepared),
        "available_actions": actions,
        "transitions": actions,
        "can_edit": bool(prepared or _can_edit(doc)),
        "readonly_summary": [] if prepared else _readonly_summary(doc),
    }
    if message:
        result["message"] = message
    return result


def get_material_request(name: str) -> dict[str, Any]:
    _assert_authenticated()
    name = clean_text(name, 140)
    if not name:
        frappe.throw("رقم طلب المواد مطلوب", frappe.ValidationError)
    doc = frappe.get_doc(DOCTYPE, name)
    doc.check_permission("read")
    return _detail_response(doc)


def _clean_search_filters(doctype: str, configured: dict[str, Any], company: str | None) -> dict[str, Any]:
    meta = frappe.get_meta(doctype)
    filters = {
        fieldname: value
        for fieldname, value in configured.get("filters", {}).items()
        if meta.has_field(fieldname)
    }
    company = clean_text(company, 140)
    if company and meta.has_field("company"):
        filters["company"] = company
    return filters


def search_options(
    doctype: str | None = None,
    option_type: str | None = None,
    search: str | None = None,
    page: int | str | None = None,
    page_length: int | str | None = None,
    company: str | None = None,
) -> dict[str, Any]:
    _assert_authenticated()
    option_type = clean_text(option_type, 40).lower()
    doctype = doctype or OPTION_TYPE_DOCTYPE.get(option_type)
    doctype = clean_text(doctype, 140)
    configured = SEARCH_CONFIG.get(doctype)
    if not configured:
        frappe.throw("نوع خيارات البحث غير مسموح", frappe.PermissionError)

    safe_page, safe_length, offset, fetch_length = page_window(page, page_length)
    fields = _readable_fields(doctype, configured["fields"])
    search_fields = _readable_fields(doctype, configured["search_fields"])
    filters = _clean_search_filters(doctype, configured, company)
    search_text = clean_text(search, 100)
    or_filters = (
        [[doctype, fieldname, "like", f"%{search_text}%"] for fieldname in search_fields]
        if search_text
        else []
    )
    rows = frappe.get_list(
        doctype,
        filters=filters,
        or_filters=or_filters,
        fields=fields,
        order_by="modified desc" if "modified" in frappe.get_meta(doctype).default_fields else "name asc",
        limit_start=offset,
        limit_page_length=fetch_length,
    )
    has_more = len(rows) > safe_length
    normalized = [_normalize_option(doctype, row) for row in rows[:safe_length]]
    return {
        "doctype": doctype,
        "option_type": option_type,
        "items": normalized,
        "options": normalized,
        "results": normalized,
        "page": safe_page,
        "page_length": safe_length,
        "has_more": has_more,
    }


def _normalize_option(doctype: str, row: dict[str, Any]) -> dict[str, Any]:
    value = clean_text(row.get("name"))
    output = dict(row)
    output["value"] = value
    if doctype == "Sales Order":
        output["sales_order"] = value
        customer = clean_text(row.get("customer_name") or row.get("customer"))
        output["label"] = f"{value} · {customer}" if customer else value
    elif doctype == "Item":
        output["item_code"] = value
        item_name = clean_text(row.get("item_name"))
        output["label"] = f"{value} · {item_name}" if item_name and item_name != value else value
    else:
        output["label"] = value
    return output


def _prepare_sales_order_doc(sales_order: str):
    from erpnext.selling.doctype.sales_order.sales_order import make_material_request

    sales_order = clean_text(sales_order, 140)
    if not sales_order:
        frappe.throw("أمر البيع مطلوب", frappe.ValidationError)
    source = frappe.get_doc("Sales Order", sales_order)
    source.check_permission("read")
    if cint(source.docstatus) != 1:
        frappe.throw(
            "يجب اعتماد أمر البيع قبل إنشاء طلب المواد",
            frappe.ValidationError,
        )

    doc = make_material_request(source.name)
    doc.check_permission("create")
    trusted_header_values = {
        "sales_order": source.name,
        "company": source.get("company"),
        "territory": source.get("territory"),
        "delivery_date": source.get("delivery_date"),
        "custom_district": source.get("custom_district"),
        "الفرع": source.get("الفرع") or source.get("branch"),
        "custom_project_name": source.get("custom_project_name"),
    }
    for fieldname, value in trusted_header_values.items():
        if doc.meta.has_field(fieldname) and value and not doc.get(fieldname):
            doc.set(fieldname, value)
    if doc.meta.has_field("transaction_date") and not doc.get("transaction_date"):
        doc.set("transaction_date", nowdate())
    if doc.meta.has_field("schedule_date") and not doc.get("schedule_date"):
        doc.set("schedule_date", doc.get("delivery_date") or nowdate())
    if doc.meta.has_field("material_request_type") and not doc.get("material_request_type"):
        doc.set("material_request_type", "Purchase")
    transaction_date = clean_text(doc.get("transaction_date"), 10) or nowdate()
    adjusted_dates = 0

    def normalize_prepared_date(value, fallback: str) -> str:
        nonlocal adjusted_dates
        original = clean_text(value, 10)
        normalized = date_not_before(original or fallback, transaction_date)
        if original and normalized != original:
            adjusted_dates += 1
        return normalized

    delivery_date = transaction_date
    if doc.meta.has_field("delivery_date"):
        delivery_date = normalize_prepared_date(doc.get("delivery_date"), transaction_date)
        doc.set("delivery_date", delivery_date)
    if doc.meta.has_field("schedule_date"):
        doc.set(
            "schedule_date",
            normalize_prepared_date(doc.get("schedule_date"), delivery_date),
        )
    for item in doc.get("items") or []:
        item.schedule_date = normalize_prepared_date(item.get("schedule_date"), delivery_date)
    doc.flags.operation_date_adjustment_count = adjusted_dates
    if not doc.get("items"):
        frappe.throw(
            "لا توجد كميات متبقية لإنشاء طلب مواد من أمر البيع",
            frappe.ValidationError,
        )
    return doc


def prepare_from_sales_order(sales_order: str) -> dict[str, Any]:
    _assert_authenticated()
    doc = _prepare_sales_order_doc(sales_order)
    result = _detail_response(doc, prepared=True)
    adjusted_dates = cint(doc.flags.get("operation_date_adjustment_count"))
    if adjusted_dates:
        result["date_adjustments"] = adjusted_dates
        result["warnings"] = [
            f"عُدّلت {adjusted_dates} تواريخ قديمة إلى تاريخ الطلب حتى يقبل النظام الحفظ."
        ]
    return result


def _assert_source_rows(source_doc, rows: list[dict[str, Any]]) -> None:
    source_names = {
        clean_text(row.get("sales_order_item"))
        for row in source_doc.get("items") or []
        if clean_text(row.get("sales_order_item"))
    }
    used_source_names: set[str] = set()
    for row in rows:
        source_name = clean_text(row.get("sales_order_item"))
        if source_name and source_name not in source_names:
            frappe.throw(
                "أحد البنود لا يتبع أمر البيع المحدد",
                frappe.PermissionError,
            )
        if source_name and source_name in used_source_names:
            frappe.throw(
                "لا يمكن تكرار رابط بند أمر البيع نفسه",
                frappe.ValidationError,
            )
        if source_name:
            used_source_names.add(source_name)


def _assert_linked_row_identity(source_row, row_values: dict[str, Any]) -> None:
    source_link = clean_text(source_row.get("sales_order_item"))
    if not source_link:
        return
    incoming_item = clean_text(row_values.get("item_code"))
    incoming_uom = clean_text(row_values.get("uom"))
    if incoming_item != clean_text(source_row.get("item_code")):
        frappe.throw(
            "لا يمكن تغيير الصنف في بند مرتبط بأمر البيع؛ احذف البند وأضف بندًا جديدًا.",
            frappe.ValidationError,
        )
    if incoming_uom != clean_text(source_row.get("uom")):
        frappe.throw(
            "لا يمكن تغيير وحدة القياس في بند مرتبط بأمر البيع.",
            frappe.ValidationError,
        )


def _assert_link_selectable(doctype: str, name: str, label: str) -> None:
    permission_type = "select" if frappe.only_has_select_perm(doctype) else "read"
    if not frappe.has_permission(doctype, permission_type, doc=name):
        frappe.throw(f"ليس لديك صلاحية اختيار {label}", frappe.PermissionError)


def _derive_item_units(row, *, require_select_permission: bool = False) -> None:
    from erpnext.stock.get_item_details import get_conversion_factor

    item_code = clean_text(row.get("item_code"), 140)
    item = frappe.get_cached_value(
        "Item",
        item_code,
        ["item_name", "stock_uom", "disabled"],
        as_dict=True,
    )
    if not item or cint(item.get("disabled")):
        frappe.throw(f"الصنف غير موجود أو معطل: {item_code}", frappe.ValidationError)

    stock_uom = clean_text(item.get("stock_uom"), 140)
    uom = clean_text(row.get("uom"), 140) or stock_uom
    if not cint(frappe.db.get_value("UOM", uom, "enabled")):
        frappe.throw(f"وحدة القياس غير موجودة أو معطلة: {uom}", frappe.ValidationError)
    if require_select_permission:
        _assert_link_selectable("Item", item_code, "هذا الصنف")
        _assert_link_selectable("UOM", uom, "وحدة القياس هذه")
    conversion_factor = flt(
        (get_conversion_factor(item_code, uom) or {}).get("conversion_factor")
    )
    stock_qty = flt(row.get("qty")) * conversion_factor
    if not stock_uom or conversion_factor <= 0 or not math.isfinite(conversion_factor):
        frappe.throw(
            f"لا يوجد معامل تحويل صالح للصنف {item_code} بوحدة {uom}",
            frappe.ValidationError,
        )
    if not math.isfinite(stock_qty):
        frappe.throw(f"كمية المخزون غير صالحة للصنف {item_code}", frappe.ValidationError)

    row.item_name = item.get("item_name") or item_code
    row.stock_uom = stock_uom
    row.uom = uom
    row.conversion_factor = conversion_factor
    row.stock_qty = stock_qty


def _replace_new_items(doc, incoming_items: Any) -> None:
    allowed = _editable_fields(
        doc,
        ITEM_EDITABLE_FIELDS,
        doctype=ITEM_DOCTYPE,
        permission_type="create",
    )
    rows = normalize_item_payloads(incoming_items, allowed)
    _assert_source_rows(doc, rows)

    source_by_name = {
        clean_text(row.get("sales_order_item")): row
        for row in doc.get("items") or []
        if clean_text(row.get("sales_order_item"))
    }
    source_by_item: dict[str, list[Any]] = {}
    for source_row in doc.get("items") or []:
        source_by_item.setdefault(clean_text(source_row.get("item_code")), []).append(source_row)

    desired = []
    used_source_rows: set[str] = set()
    for row_values in rows:
        source_name = clean_text(row_values.pop("sales_order_item", ""))
        source_row = source_by_name.get(source_name) if source_name else None
        if not source_row:
            candidates = source_by_item.get(clean_text(row_values.get("item_code")), [])
            source_row = next((row for row in candidates if str(id(row)) not in used_source_rows), None)

        if source_row:
            used_source_rows.add(str(id(source_row)))
            _assert_linked_row_identity(source_row, row_values)
            source_row.update(row_values)
            _derive_item_units(source_row)
            desired.append(source_row)
        else:
            item_row = doc.append("items", row_values)
            _derive_item_units(item_row, require_select_permission=True)
            desired.append(item_row)
    doc.set("items", desired)


def _replace_existing_items(doc, incoming_items: Any) -> None:
    existing = {clean_text(row.name): row for row in doc.get("items") or [] if clean_text(row.name)}
    allowed = _editable_fields(doc, ITEM_EDITABLE_FIELDS, doctype=ITEM_DOCTYPE)
    rows = normalize_item_payloads(
        incoming_items,
        allowed,
        existing_names=existing,
        allow_existing_names=True,
    )
    desired = []
    for row_values in rows:
        row_name = clean_text(row_values.pop("name", ""))
        row_values.pop("sales_order_item", None)
        if row_name:
            item_row = existing[row_name]
            linked = bool(clean_text(item_row.get("sales_order_item")))
            _assert_linked_row_identity(item_row, row_values)
            item_changed = clean_text(row_values.get("item_code")) != clean_text(item_row.get("item_code"))
            uom_changed = clean_text(row_values.get("uom")) != clean_text(item_row.get("uom"))
            if (item_changed or uom_changed) and not linked:
                for fieldname in ("rate", "amount", "price_list_rate", "last_purchase_rate"):
                    if item_row.meta.has_field(fieldname):
                        item_row.set(fieldname, None)
            item_row.update(row_values)
            _derive_item_units(
                item_row,
                require_select_permission=(item_changed or uom_changed) and not linked,
            )
            desired.append(item_row)
        else:
            item_row = doc.append("items", row_values)
            _derive_item_units(item_row, require_select_permission=True)
            desired.append(item_row)
    doc.set("items", desired)


def _apply_header_values(doc, payload: dict[str, Any], *, is_new: bool) -> None:
    fields = list(
        _editable_fields(
            doc,
            HEADER_EDITABLE_FIELDS,
            permission_type="create" if is_new else "write",
        )
    )
    values = sanitize_fields(payload, fields)
    for fieldname, value in values.items():
        doc.set(fieldname, value)


def _assert_modified(expected_modified: str | None, actual_modified: Any) -> None:
    if not clean_text(expected_modified):
        frappe.throw(
            "يلزم تحديث الصفحة قبل الحفظ؛ قيمة modified غير موجودة",
            frappe.TimestampMismatchError,
        )
    if not timestamps_match(expected_modified, actual_modified):
        frappe.throw(
            "تم تعديل الطلب من مستخدم آخر. "
            "حدّث الصفحة ثم أعد المحاولة.",
            frappe.TimestampMismatchError,
        )


def _lock_and_reload(doc, expected_modified: str | None):
    locked = frappe.db.get_value(
        DOCTYPE,
        doc.name,
        ["modified", "docstatus"],
        as_dict=True,
        for_update=True,
    )
    if not locked:
        frappe.throw("طلب المواد غير موجود", frappe.DoesNotExistError)
    _assert_modified(expected_modified, locked.get("modified"))
    doc.reload()
    return doc


def _create_material_request(payload: dict[str, Any]):
    sales_order = clean_text(payload.get("sales_order"), 140)
    doc = _prepare_sales_order_doc(sales_order)
    _apply_header_values(doc, payload, is_new=True)
    incoming_items = payload.get("items")
    if incoming_items is not None:
        _replace_new_items(doc, incoming_items)
    doc.insert()
    return doc


def _update_material_request(payload: dict[str, Any], expected_modified: str | None):
    name = clean_text(payload.get("name"), 140)
    doc = frappe.get_doc(DOCTYPE, name)
    doc.check_permission("write")
    doc = _lock_and_reload(doc, expected_modified or payload.get("modified"))
    _assert_can_edit(doc)

    requested_source = clean_text(payload.get("sales_order"), 140)
    if requested_source and requested_source != clean_text(doc.get("sales_order"), 140):
        frappe.throw(
            "لا يمكن تغيير أمر البيع المرتبط بطلب المواد",
            frappe.PermissionError,
        )

    _apply_header_values(doc, payload, is_new=False)
    if "items" in payload:
        _replace_existing_items(doc, payload.get("items"))
    doc.save()
    return doc


def save_material_request(doc: Any, expected_modified: str | None = None) -> dict[str, Any]:
    _assert_authenticated()
    try:
        payload = parse_mapping(doc, "طلب المواد")
    except ValueError as exc:
        frappe.throw(str(exc), frappe.ValidationError)

    name = clean_text(payload.get("name"), 140)
    try:
        saved_doc = (
            _update_material_request(payload, expected_modified)
            if name
            else _create_material_request(payload)
        )
    except ValueError as exc:
        frappe.throw(str(exc), frappe.ValidationError)
    return _detail_response(saved_doc, message="تم حفظ طلب المواد بنجاح")


def apply_workflow(name: str, action: str, expected_modified: str) -> dict[str, Any]:
    _assert_authenticated()
    name = clean_text(name, 140)
    action = clean_text(action, 140)
    if not name or not action:
        frappe.throw(
            "رقم الطلب وإجراء سير العمل مطلوبان",
            frappe.ValidationError,
        )

    doc = frappe.get_doc(DOCTYPE, name)
    doc.check_permission("read")
    doc = _lock_and_reload(doc, expected_modified)
    doc.check_permission("read")

    transitions = _available_actions(doc)
    if action not in {row["action"] for row in transitions}:
        frappe.throw(
            "إجراء سير العمل غير متاح لك في الحالة الحالية",
            frappe.PermissionError,
        )

    updated_doc = frappe_apply_workflow(doc.as_dict(), action)
    queued = updated_doc is None
    if queued:
        updated_doc = frappe.get_doc(DOCTYPE, name)
    return {
        **_detail_response(
            updated_doc,
            message=(
                "تم إرسال إجراء سير العمل"
                if queued
                else "تم تنفيذ إجراء سير العمل بنجاح"
            ),
        ),
        "queued": queued,
    }
