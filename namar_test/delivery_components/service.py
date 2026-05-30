from __future__ import annotations

import json
from typing import Any

import frappe
from frappe.utils import cint, flt, now_datetime

from namar_test.delivery_components.package_logic import (
    build_package_specs,
    clean_count,
    is_valid_loading_prefix,
    loading_prefix_from_index,
    package_status,
)


PACKAGE_DOCTYPE = "Material Request Delivery Component Package"
PACKAGE_PARENTFIELD = "custom_delivery_component_packages"
RULE_DOCTYPE = "Delivery Component Packaging Rule"
MR_LOADING_CODE_FIELD = "custom_delivery_loading_code"
PACKAGE_LOADING_CODE_FIELD = "loading_code"
REALTIME_EVENT = "delivery_component_package_ready"


def normalize_material_request(value: str | None) -> str:
    material_request = (value or "").strip()
    if material_request and not material_request.startswith("MREQ-"):
        material_request = "MREQ-" + material_request
    return material_request


def parse_store_data(item_row: Any) -> list[dict[str, Any]]:
    raw_value = item_row.get("custom_store_data") or ""
    if not raw_value and item_row.get("name"):
        raw_value = frappe.db.get_value("Material Request Item", item_row.get("name"), "custom_store_data") or ""
    if not raw_value:
        return []
    try:
        parsed = json.loads(raw_value) or []
    except Exception:
        try:
            parsed = frappe.parse_json(raw_value) or []
        except Exception:
            return []
    if isinstance(parsed, dict):
        parsed = parsed.get("stores") or parsed.get("data") or []
    try:
        return list(parsed)
    except Exception:
        return []


def load_rules() -> list[dict[str, Any]]:
    if not frappe.db.exists("DocType", RULE_DOCTYPE):
        return []
    fields = [
        "name",
        "rule_name",
        "match_mode",
        "component",
        "component_contains",
        "full_pack_qty",
        "full_pack_label",
        "remainder_pack_label",
        "remainder_multi_pack_label",
        "required_for_delivery",
        "sort_order",
    ]
    if frappe.get_meta(RULE_DOCTYPE).has_field("exclude_from_delivery"):
        fields.append("exclude_from_delivery")
    return frappe.get_all(
        RULE_DOCTYPE,
        filters={"enabled": 1},
        fields=fields,
        order_by="sort_order asc, name asc",
        limit_page_length=0,
    )


def rule_priority(rule: dict[str, Any]) -> int:
    mode = rule.get("match_mode") or ""
    if mode == "مطابقة مكون":
        return 1
    if mode == "يحتوي اسم المكون":
        return 2
    return 3


def find_rule(component: str | None, component_label: str | None, rules: list[dict[str, Any]]) -> dict[str, Any] | None:
    component = (component or "").strip()
    component_label = (component_label or "").strip()
    sorted_rules = sorted(
        rules,
        key=lambda rule: (rule_priority(rule), cint(rule.get("sort_order") or 0), rule.get("name") or ""),
    )
    for rule in sorted_rules:
        mode = rule.get("match_mode") or ""
        if mode == "افتراضي":
            continue
        if mode == "مطابقة مكون" and rule.get("component") and rule.get("component") in (component, component_label):
            return rule
        if mode == "يحتوي اسم المكون":
            needle = (rule.get("component_contains") or "").strip()
            if needle and (needle in component or needle in component_label):
                return rule
    return None


def load_component_sort_order() -> dict[str, int]:
    if not frappe.db.exists("DocType", "Store Component"):
        return {}

    meta = frappe.get_meta("Store Component")
    fields = ["name"]
    for fieldname in ("component_name", "label_ar", "custom_print_sort_order"):
        if meta.has_field(fieldname):
            fields.append(fieldname)

    sort_map: dict[str, int] = {}
    for row in frappe.get_all("Store Component", fields=fields, limit_page_length=0):
        sort_value = cint(row.get("custom_print_sort_order") or 0)
        if sort_value <= 0:
            continue
        for key in (row.get("name"), row.get("component_name"), row.get("label_ar")):
            text = (key or "").strip()
            if text and text not in sort_map:
                sort_map[text] = sort_value
    return sort_map


def component_sort_key(component_row: dict[str, Any], sort_map: dict[str, int]) -> tuple[int, str, str]:
    component = (component_row.get("component") or "").strip()
    component_label = (component_row.get("component_label") or "").strip()
    item_code = (component_row.get("item_code") or "").strip()
    sort_value = sort_map.get(component) or sort_map.get(component_label) or 999999
    return (sort_value, component_label or component, item_code)


def missing_rule_label(component_row: dict[str, Any]) -> str:
    label = (component_row.get("component_label") or component_row.get("component") or "").strip()
    item_code = (component_row.get("item_code") or "").strip()
    if item_code:
        return "%s (%s)" % (label, item_code)
    return label


def throw_missing_delivery_rules(component_rows: list[dict[str, Any]]) -> None:
    labels: list[str] = []
    seen: dict[str, int] = {}
    for row in component_rows:
        label = missing_rule_label(row)
        if label and label not in seen:
            seen[label] = 1
            labels.append(label)
    frappe.throw(
        "لا توجد قاعدة توريد لهذه المكونات: %s. أضف قاعدة تغليف مكونات التوريد لكل مكون ثم أعد تحديث الحزم."
        % ", ".join(labels)
    )


def get_existing_packages(material_request: str) -> dict[str, dict[str, Any]]:
    fields = [
        "name",
        "package_key",
        "ready_qty",
        "ready_at",
        "ready_by",
        "source",
        "status",
        "barcode_key",
    ]
    if frappe.get_meta(PACKAGE_DOCTYPE).has_field(PACKAGE_LOADING_CODE_FIELD):
        fields.append(PACKAGE_LOADING_CODE_FIELD)
    rows = frappe.get_all(
        PACKAGE_DOCTYPE,
        filters={"parent": material_request, "parentfield": PACKAGE_PARENTFIELD},
        fields=fields,
        limit_page_length=0,
    )
    return {row.get("package_key"): row for row in rows if row.get("package_key")}


def get_packages_for_summary(material_request: str) -> list[dict[str, Any]]:
    return frappe.get_all(
        PACKAGE_DOCTYPE,
        filters={"parent": material_request, "parentfield": PACKAGE_PARENTFIELD},
        fields=["name", "package_qty", "ready_qty", "required_for_delivery"],
        limit_page_length=0,
    )


def aggregate_components(mr_doc: Any) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for item_row in mr_doc.items:
        row_qty = flt(item_row.qty or 0)
        if row_qty <= 0:
            row_qty = 1
        stores = parse_store_data(item_row)
        for store in stores:
            component = (store.get("component") or store.get("component_name") or "").strip()
            component_label = (store.get("component_ar") or component).strip()
            item_code = (store.get("item") or store.get("item_code") or "").strip()
            item_name = (store.get("item_name") or item_code).strip()
            per_row_qty = flt(store.get("qty") or 0)
            if not component or per_row_qty <= 0:
                continue
            key = component + "||" + item_code
            if key not in grouped:
                grouped[key] = {
                    "component": component,
                    "component_label": component_label,
                    "item_code": item_code,
                    "item_name": item_name,
                    "required_qty": 0,
                }
                order.append(key)
            grouped[key]["required_qty"] = flt(grouped[key].get("required_qty") or 0) + (per_row_qty * row_qty)
    return [grouped[key] for key in order]


def get_or_make_loading_prefix(mr_doc: Any, dry_run: bool) -> str:
    mr_meta = frappe.get_meta("Material Request")
    if not mr_meta.has_field(MR_LOADING_CODE_FIELD):
        return ""

    existing_prefix = (mr_doc.get(MR_LOADING_CODE_FIELD) or "").strip().upper()
    if is_valid_loading_prefix(existing_prefix):
        return existing_prefix

    used_requests = frappe.get_all(
        "Material Request",
        filters={MR_LOADING_CODE_FIELD: ["!=", ""]},
        pluck="name",
        limit_page_length=0,
    )
    prefix = loading_prefix_from_index(len(used_requests))
    if not dry_run:
        frappe.db.set_value("Material Request", mr_doc.name, MR_LOADING_CODE_FIELD, prefix, update_modified=False)
        mr_doc.set(MR_LOADING_CODE_FIELD, prefix)
    return prefix


def assign_loading_codes(package_rows: list[dict[str, Any]], loading_prefix: str) -> list[dict[str, Any]]:
    if not loading_prefix or not frappe.get_meta(PACKAGE_DOCTYPE).has_field(PACKAGE_LOADING_CODE_FIELD):
        return package_rows
    for index, row in enumerate(package_rows, start=1):
        row[PACKAGE_LOADING_CODE_FIELD] = "%s-%s" % (loading_prefix, str(index).zfill(2))
    return package_rows


def build_package_rows(mr_doc: Any) -> list[dict[str, Any]]:
    rules = load_rules()
    component_rows = aggregate_components(mr_doc)
    sort_map = load_component_sort_order()
    component_rows = sorted(component_rows, key=lambda row: component_sort_key(row, sort_map))
    existing = get_existing_packages(mr_doc.name)
    has_loading_code_field = frappe.get_meta(PACKAGE_DOCTYPE).has_field(PACKAGE_LOADING_CODE_FIELD)
    package_rows: list[dict[str, Any]] = []
    missing_rule_rows: list[dict[str, Any]] = []

    for component_row in component_rows:
        required_qty = flt(component_row.get("required_qty") or 0)
        if required_qty <= 0:
            continue
        rule = find_rule(component_row.get("component"), component_row.get("component_label"), rules)
        if not rule:
            missing_rule_rows.append(component_row)
            continue
        if cint(rule.get("exclude_from_delivery") or 0):
            continue

        package_specs = build_package_specs(
            required_qty=required_qty,
            full_pack_qty=rule.get("full_pack_qty") or 0,
            full_label=(rule.get("full_pack_label") or "حزمة").strip(),
            remainder_one_label=(rule.get("remainder_pack_label") or "مغلف منفرد").strip(),
            remainder_multi_label=(rule.get("remainder_multi_pack_label") or "كرتون ناقص").strip(),
        )
        required_for_delivery = 1 if cint(rule.get("required_for_delivery", 1)) else 0

        package_count = len(package_specs)
        for index, package_spec in enumerate(package_specs, start=1):
            package_key = "%s||%s||%s" % (
                component_row.get("component") or "",
                component_row.get("item_code") or "",
                index,
            )
            existing_row = existing.get(package_key) or {}
            package_qty = flt(package_spec.get("package_qty") or 0)
            ready_qty = min(flt(existing_row.get("ready_qty") or 0), package_qty)
            remaining = max(package_qty - ready_qty, 0)
            package_row = {
                "package_key": package_key,
                "component": component_row.get("component") or "",
                "component_label": component_row.get("component_label") or component_row.get("component") or "",
                "item_code": component_row.get("item_code") or "",
                "item_name": component_row.get("item_name") or component_row.get("item_code") or "",
                "package_no": index,
                "package_count": package_count,
                "package_label": package_spec.get("package_label") or "حزمة",
                "package_qty": clean_count(package_qty),
                "ready_qty": clean_count(ready_qty),
                "remaining_qty": clean_count(remaining),
                "status": package_status(package_qty, ready_qty),
                "required_for_delivery": required_for_delivery,
                "ready_at": existing_row.get("ready_at"),
                "ready_by": existing_row.get("ready_by"),
                "source": existing_row.get("source") or "",
                "barcode_key": existing_row.get("barcode_key") or "",
            }
            if has_loading_code_field:
                package_row[PACKAGE_LOADING_CODE_FIELD] = existing_row.get(PACKAGE_LOADING_CODE_FIELD) or ""
            package_rows.append(package_row)

    if missing_rule_rows:
        throw_missing_delivery_rules(missing_rule_rows)
    return package_rows


def summarize_packages(package_rows: list[dict[str, Any]]) -> dict[str, Any]:
    total_required = 0
    ready_required = 0
    total_packages = 0
    remaining_packages = 0
    for row in package_rows:
        if not cint(row.get("required_for_delivery")):
            continue
        total_packages += 1
        package_qty = flt(row.get("package_qty") or 0)
        ready_qty = flt(row.get("ready_qty") or 0)
        total_required += package_qty
        ready_required += ready_qty
        if package_qty and ready_qty + 0.000001 < package_qty:
            remaining_packages += 1

    if total_packages <= 0:
        status = "لا توجد مكونات"
    elif remaining_packages <= 0:
        status = "مكتمل"
    elif ready_required > 0:
        status = "جزئي"
    else:
        status = "غير جاهز"

    summary = "حزم %s/%s | كمية %s/%s" % (
        clean_count(total_packages - remaining_packages),
        clean_count(total_packages),
        clean_count(ready_required),
        clean_count(total_required),
    )
    return {
        "status": status,
        "total_packages": clean_count(total_packages),
        "remaining_packages": clean_count(remaining_packages),
        "total_required_qty": clean_count(total_required),
        "ready_required_qty": clean_count(ready_required),
        "summary": summary,
    }


def update_material_request_summary(material_request: str, package_rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if package_rows is None:
        package_rows = get_packages_for_summary(material_request)
    summary = summarize_packages(package_rows)
    mr_meta = frappe.get_meta("Material Request")
    field_map = {
        "custom_delivery_component_status": summary.get("status"),
        "custom_delivery_component_total_packages": summary.get("total_packages"),
        "custom_delivery_component_remaining_packages": summary.get("remaining_packages"),
        "custom_delivery_component_summary": summary.get("summary"),
    }
    update_values = {
        fieldname: value
        for fieldname, value in field_map.items()
        if mr_meta.has_field(fieldname)
    }
    if update_values:
        frappe.db.set_value("Material Request", material_request, update_values, update_modified=False)
    return summary


def replace_package_rows(material_request: str, package_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    existing_names = frappe.get_all(
        PACKAGE_DOCTYPE,
        filters={"parent": material_request, "parentfield": PACKAGE_PARENTFIELD},
        pluck="name",
        limit_page_length=0,
    )
    for existing_name in existing_names:
        frappe.delete_doc(PACKAGE_DOCTYPE, existing_name, ignore_permissions=True, force=True)

    for index, row in enumerate(package_rows, start=1):
        child = dict(row)
        child.update(
            {
                "doctype": PACKAGE_DOCTYPE,
                "parent": material_request,
                "parenttype": "Material Request",
                "parentfield": PACKAGE_PARENTFIELD,
                "idx": index,
            }
        )
        doc = frappe.get_doc(child)
        doc.db_insert()
        if not child.get("barcode_key"):
            frappe.db.set_value(PACKAGE_DOCTYPE, doc.name, "barcode_key", doc.name, update_modified=False)
            row["barcode_key"] = doc.name
        row["name"] = doc.name
    return package_rows


def get_packages(material_request: str) -> list[dict[str, Any]]:
    if not frappe.db.exists("DocType", PACKAGE_DOCTYPE):
        return []
    fields = [
        "name",
        "package_key",
        "component",
        "component_label",
        "item_code",
        "item_name",
        "package_no",
        "package_count",
        "package_label",
        "package_qty",
        "ready_qty",
        "remaining_qty",
        "status",
        "required_for_delivery",
        "ready_at",
        "ready_by",
        "source",
        "barcode_key",
    ]
    if frappe.get_meta(PACKAGE_DOCTYPE).has_field(PACKAGE_LOADING_CODE_FIELD):
        fields.append(PACKAGE_LOADING_CODE_FIELD)
    rows = frappe.get_all(
        PACKAGE_DOCTYPE,
        filters={"parent": material_request, "parentfield": PACKAGE_PARENTFIELD},
        fields=fields,
        order_by="idx asc",
        limit_page_length=0,
    )
    result = []
    for row in rows:
        package_qty = flt(row.get("package_qty") or 0)
        ready_qty = flt(row.get("ready_qty") or 0)
        remaining = max(package_qty - ready_qty, 0)
        row["package_qty"] = clean_count(package_qty)
        row["ready_qty"] = clean_count(ready_qty)
        row["remaining_qty"] = clean_count(remaining)
        row["status"] = package_status(package_qty, ready_qty)
        row["barcode_key"] = row.get("barcode_key") or row.get("name")
        result.append(row)
    return result


def get_package(material_request: str, package_token: str) -> dict[str, Any] | None:
    for row in get_packages(material_request):
        if package_token in (
            row.get("name"),
            row.get("barcode_key"),
            row.get("package_key"),
            row.get(PACKAGE_LOADING_CODE_FIELD),
        ):
            return row
    return None


def sync_delivery_component_packages(material_request: str | None, dry_run: int | str | bool = 0) -> dict[str, Any]:
    material_request = normalize_material_request(material_request)
    dry_run_bool = bool(cint(dry_run or 0))
    if not material_request:
        frappe.throw("اسم طلب المواد مطلوب")
    if not frappe.db.exists("Material Request", material_request):
        frappe.throw("طلب المواد غير موجود: " + material_request)
    if not frappe.db.exists("DocType", PACKAGE_DOCTYPE):
        frappe.throw("جدول حزم مكونات التوريد غير مثبت على الموقع")

    mr_doc = frappe.get_doc("Material Request", material_request)
    package_rows = build_package_rows(mr_doc)
    loading_prefix = get_or_make_loading_prefix(mr_doc, dry_run_bool)
    package_rows = assign_loading_codes(package_rows, loading_prefix)
    summary = summarize_packages(package_rows)

    if not dry_run_bool:
        package_rows = replace_package_rows(material_request, package_rows)
        summary = update_material_request_summary(material_request, package_rows)
        frappe.db.commit()

    return {
        "status": "dry_run" if dry_run_bool else "synced",
        "material_request": material_request,
        "summary": summary,
        "loading_code": loading_prefix,
        "packages": package_rows,
        "package_count": len(package_rows),
    }


def get_delivery_component_packages(
    material_request: str | None = None,
    component_package: str | None = None,
) -> dict[str, Any]:
    material_request = normalize_material_request(material_request)
    component_package = (component_package or "").strip()
    if not material_request and component_package:
        package_parent = (
            frappe.db.get_value(PACKAGE_DOCTYPE, component_package, "parent")
            if frappe.db.exists("DocType", PACKAGE_DOCTYPE)
            else ""
        )
        material_request = normalize_material_request(package_parent)

    if not material_request:
        frappe.throw("اسم طلب المواد مطلوب")
    if not frappe.db.exists("Material Request", material_request):
        frappe.throw("طلب المواد غير موجود: " + material_request)

    packages = get_packages(material_request)
    selected_package = None
    if component_package:
        for row in packages:
            if component_package in (
                row.get("name"),
                row.get("barcode_key"),
                row.get("package_key"),
                row.get(PACKAGE_LOADING_CODE_FIELD),
            ):
                selected_package = row
                break
        if not selected_package:
            frappe.throw("حزمة مكونات التوريد غير موجودة أو لم يتم توليدها بعد")

    summary = summarize_packages(packages)
    return {
        "material_request": material_request,
        "summary": summary,
        "packages": packages,
        "package": selected_package,
        "selected_package": selected_package,
        "package_count": len(packages),
        "needs_sync": 1 if not packages else 0,
    }


def publish_package_ready_event(material_request: str, package_row: dict[str, Any], summary: dict[str, Any]) -> None:
    try:
        frappe.publish_realtime(
            REALTIME_EVENT,
            {
                "material_request": material_request,
                "package_token": package_row.get("name") or package_row.get("barcode_key") or "",
                "loading_code": package_row.get(PACKAGE_LOADING_CODE_FIELD) or "",
                "status": package_row.get("status") or "",
                "summary": summary,
            },
            doctype="Material Request",
            docname=material_request,
            after_commit=True,
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Delivery component realtime publish failed")


def mark_delivery_component_package_ready(
    material_request: str | None = None,
    component_package: str | None = None,
    mode: str = "full",
    ready_qty: float | int | str | None = None,
    source: str = "QR",
) -> dict[str, Any]:
    material_request = normalize_material_request(material_request)
    package_token = (component_package or "").strip()
    mode = (mode or "full").strip()
    source = (source or "QR").strip()

    if not package_token:
        frappe.throw("مفتاح حزمة مكونات التوريد مطلوب")
    if not frappe.db.exists("DocType", PACKAGE_DOCTYPE):
        frappe.throw("جدول حزم مكونات التوريد غير مثبت على الموقع")
    if not material_request:
        package_parent = frappe.db.get_value(PACKAGE_DOCTYPE, package_token, "parent")
        material_request = normalize_material_request(package_parent)
    if not material_request:
        frappe.throw("اسم طلب المواد مطلوب")
    if not frappe.db.exists("Material Request", material_request):
        frappe.throw("طلب المواد غير موجود: " + material_request)

    package_row = get_package(material_request, package_token)
    if not package_row:
        frappe.throw("حزمة مكونات التوريد غير موجودة. شغّل تحديث حزم المكونات أولًا.")

    package_qty = flt(package_row.get("package_qty") or 0)
    current_ready_qty = flt(package_row.get("ready_qty") or 0)
    requested_qty = flt(ready_qty or 0)

    if mode == "partial":
        if requested_qty <= 0:
            frappe.throw("أدخل كمية جزئية أكبر من صفر")
        new_ready_qty = current_ready_qty + requested_qty
    else:
        new_ready_qty = package_qty

    new_ready_qty = min(max(new_ready_qty, 0), package_qty)
    remaining_qty = max(package_qty - new_ready_qty, 0)
    new_status = package_status(package_qty, new_ready_qty)
    stamp = now_datetime()
    user = frappe.session.user if frappe.session.user and frappe.session.user != "Guest" else "Guest"

    frappe.db.set_value(
        PACKAGE_DOCTYPE,
        package_row.get("name"),
        {
            "ready_qty": clean_count(new_ready_qty),
            "remaining_qty": clean_count(remaining_qty),
            "status": new_status,
            "ready_at": stamp,
            "ready_by": user,
            "source": source if source in ("QR", "يدوي", "تزامن") else "QR",
        },
        update_modified=False,
    )
    summary = update_material_request_summary(material_request)

    package_row["ready_qty"] = clean_count(new_ready_qty)
    package_row["remaining_qty"] = clean_count(remaining_qty)
    package_row["status"] = new_status
    package_row["ready_at"] = stamp
    package_row["ready_by"] = user
    package_row["source"] = source
    publish_package_ready_event(material_request, package_row, summary)
    frappe.db.commit()

    return {
        "status": "done" if new_status == "جاهز" else "partial",
        "material_request": material_request,
        "package": package_row,
        "summary": summary,
        "message": "تم تسجيل حزمة مكونات التوريد" if new_status == "جاهز" else "تم تسجيل كمية جزئية من الحزمة",
    }
