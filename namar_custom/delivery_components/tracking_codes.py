from __future__ import annotations

from typing import Any

import frappe
from frappe.model.naming import getseries
from frappe.utils import cint

from namar_custom.delivery_components.tracking_code_logic import (
    is_valid_request_tracking_code,
    normalize_tracking_code,
    package_tracking_code,
    split_package_tracking_code,
    tracking_code_from_sequence,
)


TRACKING_CODE_FIELD = "custom_delivery_loading_code"
TRACKING_SERIES_KEY = "NAMAR-MATERIAL-REQUEST-TRACKING-CODE-"
PACKAGE_DOCTYPE = "Material Request Delivery Component Package"
PACKAGE_PARENTFIELD = "custom_delivery_component_packages"


def material_request_has_tracking_field() -> bool:
    return frappe.get_meta("Material Request").has_field(TRACKING_CODE_FIELD)


def tracking_code_exists(code: str, *, exclude_name: str | None = None) -> bool:
    filters: dict[str, Any] = {TRACKING_CODE_FIELD: code}
    if exclude_name:
        filters["name"] = ["!=", exclude_name]
    return bool(frappe.db.exists("Material Request", filters))


def next_material_request_tracking_code() -> str:
    for _attempt in range(1000):
        sequence = cint(getseries(TRACKING_SERIES_KEY, 8))
        code = tracking_code_from_sequence(sequence)
        if not tracking_code_exists(code):
            return code
    frappe.throw("تعذر إنشاء رمز تتبع فريد لطلب المواد")
    return ""


def ensure_material_request_tracking_code(doc: Any, method: str | None = None) -> str:
    del method
    if not material_request_has_tracking_field():
        return ""

    existing = normalize_tracking_code(doc.get(TRACKING_CODE_FIELD))
    if doc.get("amended_from"):
        existing = ""
    if existing:
        if not is_valid_request_tracking_code(existing):
            frappe.throw("رمز تتبع طلب المواد غير صالح: " + existing)
        if tracking_code_exists(existing, exclude_name=doc.get("name")):
            frappe.throw("رمز تتبع طلب المواد مستخدم مسبقًا: " + existing)
        doc.set(TRACKING_CODE_FIELD, existing)
        return existing

    code = next_material_request_tracking_code()
    doc.set(TRACKING_CODE_FIELD, code)
    return code


def count_missing_tracking_codes() -> int:
    result = frappe.db.sql(
        "SELECT COUNT(*) FROM `tabMaterial Request` "
        "WHERE IFNULL(`%s`, '') = ''" % TRACKING_CODE_FIELD
    )
    return cint(result[0][0] if result else 0)


def audit_material_request_tracking_codes() -> dict[str, Any]:
    rows = frappe.get_all(
        "Material Request",
        fields=["name", TRACKING_CODE_FIELD],
        order_by="creation asc, name asc",
        limit_page_length=0,
    )
    seen: dict[str, str] = {}
    duplicates: list[dict[str, str]] = []
    invalid: list[dict[str, str]] = []
    missing = 0
    for row in rows:
        code = normalize_tracking_code(row.get(TRACKING_CODE_FIELD))
        if not code:
            missing += 1
            continue
        if not is_valid_request_tracking_code(code):
            invalid.append({"material_request": row.get("name"), "tracking_code": code})
            continue
        if code in seen:
            duplicates.append(
                {
                    "tracking_code": code,
                    "first_material_request": seen[code],
                    "material_request": row.get("name"),
                }
            )
        else:
            seen[code] = row.get("name")
    return {
        "total": len(rows),
        "coded": len(rows) - missing,
        "missing": missing,
        "duplicates": duplicates,
        "invalid": invalid,
    }


def backfill_material_request_tracking_codes(
    *, limit: int | str | None = 250, dry_run: int | str | bool = 1
) -> dict[str, Any]:
    if not material_request_has_tracking_field():
        frappe.throw("حقل رمز تتبع طلب المواد غير مثبت")

    audit = audit_material_request_tracking_codes()
    if not bool(cint(dry_run or 0)) and (audit.get("duplicates") or audit.get("invalid")):
        frappe.throw("تعذر ترحيل رموز التتبع لوجود رموز مكررة أو غير صالحة. شغّل الفحص أولًا.")

    batch_limit = max(min(cint(limit or 250), 1000), 1)
    dry_run_bool = bool(cint(dry_run or 0))
    rows = frappe.db.sql(
        "SELECT name, creation FROM `tabMaterial Request` "
        "WHERE IFNULL(`%s`, '') = '' "
        "ORDER BY creation ASC, name ASC LIMIT %%s" % TRACKING_CODE_FIELD,
        (batch_limit,),
        as_dict=True,
    )
    if dry_run_bool:
        return {
            "status": "dry_run",
            "processed": 0,
            "candidates": [row.get("name") for row in rows],
            "remaining": count_missing_tracking_codes(),
            "audit": audit,
        }

    assigned: list[dict[str, str]] = []
    for row in rows:
        code = next_material_request_tracking_code()
        frappe.db.set_value(
            "Material Request",
            row.get("name"),
            TRACKING_CODE_FIELD,
            code,
            update_modified=False,
        )
        assigned.append({"material_request": row.get("name"), "tracking_code": code})
    frappe.db.commit()
    return {
        "status": "done",
        "processed": len(assigned),
        "assigned": assigned,
        "remaining": count_missing_tracking_codes(),
        "audit": audit_material_request_tracking_codes(),
    }


def audit_component_package_identifiers() -> dict[str, Any]:
    if not frappe.db.exists("DocType", PACKAGE_DOCTYPE):
        return {
            "total": 0,
            "missing_loading_codes": 0,
            "missing_barcode_keys": 0,
            "duplicate_loading_codes": [],
            "duplicate_barcode_keys": [],
            "invalid_loading_codes": [],
        }

    rows = frappe.get_all(
        PACKAGE_DOCTYPE,
        filters={"parentfield": PACKAGE_PARENTFIELD},
        fields=["name", "parent", "loading_code", "barcode_key"],
        order_by="parent asc, idx asc, name asc",
        limit_page_length=0,
    )
    request_codes = {
        row.get("name"): normalize_tracking_code(row.get(TRACKING_CODE_FIELD))
        for row in frappe.get_all(
            "Material Request",
            fields=["name", TRACKING_CODE_FIELD],
            limit_page_length=0,
        )
    }
    seen_loading: dict[str, str] = {}
    seen_barcode: dict[str, str] = {}
    duplicate_loading: list[dict[str, str]] = []
    duplicate_barcode: list[dict[str, str]] = []
    invalid_loading: list[dict[str, str]] = []
    missing_loading = 0
    missing_barcode = 0

    for row in rows:
        row_name = row.get("name") or ""
        loading_code = normalize_tracking_code(row.get("loading_code"))
        barcode_key = (row.get("barcode_key") or "").strip()
        request_code = request_codes.get(row.get("parent")) or ""
        if not loading_code:
            missing_loading += 1
        else:
            parsed = split_package_tracking_code(loading_code)
            if not parsed or parsed[0] != request_code:
                invalid_loading.append(
                    {
                        "package": row_name,
                        "material_request": row.get("parent") or "",
                        "loading_code": loading_code,
                        "request_code": request_code,
                    }
                )
            if loading_code in seen_loading:
                duplicate_loading.append(
                    {
                        "loading_code": loading_code,
                        "first_package": seen_loading[loading_code],
                        "package": row_name,
                    }
                )
            else:
                seen_loading[loading_code] = row_name

        if not barcode_key:
            missing_barcode += 1
        elif barcode_key in seen_barcode:
            duplicate_barcode.append(
                {
                    "barcode_key": barcode_key,
                    "first_package": seen_barcode[barcode_key],
                    "package": row_name,
                }
            )
        else:
            seen_barcode[barcode_key] = row_name

    return {
        "total": len(rows),
        "missing_loading_codes": missing_loading,
        "missing_barcode_keys": missing_barcode,
        "duplicate_loading_codes": duplicate_loading,
        "duplicate_barcode_keys": duplicate_barcode,
        "invalid_loading_codes": invalid_loading,
    }


def backfill_component_package_identifiers(
    *, limit: int | str | None = 100, dry_run: int | str | bool = 1
) -> dict[str, Any]:
    if not frappe.db.exists("DocType", PACKAGE_DOCTYPE):
        frappe.throw("جدول حزم مكونات التوريد غير مثبت")

    audit = audit_component_package_identifiers()
    blockers = (
        audit.get("duplicate_loading_codes")
        or audit.get("duplicate_barcode_keys")
        or audit.get("invalid_loading_codes")
    )
    dry_run_bool = bool(cint(dry_run or 0))
    if not dry_run_bool and blockers:
        frappe.throw("تعذر ترحيل معرفات الحزم لوجود قيم مكررة أو غير صالحة. شغّل الفحص أولًا.")

    batch_limit = max(min(cint(limit or 100), 500), 1)
    parents = frappe.db.sql(
        "SELECT DISTINCT parent FROM `tab%s` "
        "WHERE parentfield = %%s AND (IFNULL(loading_code, '') = '' OR IFNULL(barcode_key, '') = '') "
        "ORDER BY parent ASC LIMIT %%s" % PACKAGE_DOCTYPE,
        (PACKAGE_PARENTFIELD, batch_limit),
        as_dict=True,
    )
    if dry_run_bool:
        return {
            "status": "dry_run",
            "processed_requests": 0,
            "candidates": [row.get("parent") for row in parents],
            "audit": audit,
        }

    updated_packages = 0
    for parent_row in parents:
        material_request = parent_row.get("parent") or ""
        request_code = normalize_tracking_code(
            frappe.db.get_value("Material Request", material_request, TRACKING_CODE_FIELD)
        )
        if not is_valid_request_tracking_code(request_code):
            frappe.throw("طلب المواد %s لا يحتوي رمز تتبع صالح" % material_request)

        package_rows = frappe.get_all(
            PACKAGE_DOCTYPE,
            filters={"parent": material_request, "parentfield": PACKAGE_PARENTFIELD},
            fields=["name", "loading_code", "barcode_key"],
            order_by="idx asc, name asc",
            limit_page_length=0,
        )
        used_codes = {
            normalize_tracking_code(row.get("loading_code"))
            for row in package_rows
            if normalize_tracking_code(row.get("loading_code"))
        }
        next_number = 1
        for row in package_rows:
            values: dict[str, str] = {}
            if not normalize_tracking_code(row.get("loading_code")):
                candidate = package_tracking_code(request_code, next_number)
                while candidate in used_codes:
                    next_number += 1
                    candidate = package_tracking_code(request_code, next_number)
                values["loading_code"] = candidate
                used_codes.add(candidate)
                next_number += 1
            if not (row.get("barcode_key") or "").strip():
                # Preserve compatibility with labels that historically used the child-row name.
                values["barcode_key"] = row.get("name") or frappe.generate_hash(length=20)
            if values:
                frappe.db.set_value(
                    PACKAGE_DOCTYPE,
                    row.get("name"),
                    values,
                    update_modified=False,
                )
                updated_packages += 1

    frappe.db.commit()
    return {
        "status": "done",
        "processed_requests": len(parents),
        "updated_packages": updated_packages,
        "audit": audit_component_package_identifiers(),
    }
