from __future__ import annotations

from collections import Counter
from typing import Any

import frappe
from frappe.utils import cint, flt

from namar_custom.delivery_components.package_logic import (
    TRACKING_ROUTE_BARCODE,
    TRACKING_ROUTE_DELIVERY_ONLY,
    normalize_tracking_status,
)
from namar_custom.delivery_components import service
from namar_custom.delivery_components.snapshot_backfill import (
    SNAPSHOT_FIELDS,
    build_event_snapshot_updates,
)


EVENT_DOCTYPE = "Material Request Component Package Event"
PACKAGE_DOCTYPE = "Material Request Delivery Component Package"
PACKAGE_PARENTFIELD = "custom_delivery_component_packages"
PRODUCTION_APP = "namar_custom"
BACKFILL_CONFIRMATION = "NAMAR_CUSTOM_EVENT_SNAPSHOT_BACKFILL_20260813"
LEGACY_ROUTE_CONFIRMATION = "NAMAR_CUSTOM_LEGACY_ROUTE_ADOPTION_20260813"


def _ensure_production_app() -> None:
    if PRODUCTION_APP not in frappe.get_installed_apps():
        frappe.throw("هذا الترحيل مخصص لتطبيق namar_custom على الموقع الأساسي فقط")


def _load_backfill_rows() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    events = frappe.get_all(
        EVENT_DOCTYPE,
        fields=["name", "package_id", *SNAPSHOT_FIELDS],
        order_by="creation asc",
        limit_page_length=0,
    )
    packages = frappe.get_all(
        PACKAGE_DOCTYPE,
        filters={
            "parenttype": "Material Request",
            "parentfield": PACKAGE_PARENTFIELD,
        },
        fields=[
            "name",
            "component",
            "component_label",
            "color",
            "item_name",
            "package_label",
            "package_qty",
            "tracking_route",
        ],
        order_by="parent asc, idx asc",
        limit_page_length=0,
    )
    return events, packages


def backfill_component_package_event_snapshots(
    *,
    dry_run: int | str | bool = 1,
    confirmation: str | None = None,
) -> dict[str, Any]:
    _ensure_production_app()
    if not frappe.db.exists("DocType", EVENT_DOCTYPE):
        frappe.throw("سجل حركات حزم القطاعات غير موجود")
    if not frappe.db.exists("DocType", PACKAGE_DOCTYPE):
        frappe.throw("جدول حزم القطاعات غير موجود")

    events, packages = _load_backfill_rows()
    updates, orphans = build_event_snapshot_updates(events, packages)
    apply_updates = not bool(cint(dry_run))

    if apply_updates and confirmation != BACKFILL_CONFIRMATION:
        frappe.throw("عبارة تأكيد ترحيل سجل القطاعات غير صحيحة")

    if apply_updates:
        for row in updates:
            frappe.db.set_value(
                EVENT_DOCTYPE,
                row["name"],
                row["values"],
                update_modified=False,
            )

    field_counts = Counter(
        fieldname
        for row in updates
        for fieldname in row.get("values", {})
    )
    return {
        "status": "applied" if apply_updates else "dry_run",
        "event_count": len(events),
        "package_count": len(packages),
        "update_count": len(updates),
        "field_update_count": sum(field_counts.values()),
        "field_counts": dict(sorted(field_counts.items())),
        "orphan_count": len(orphans),
        "orphan_sample": orphans[:20],
        "update_sample": updates[:20] if not apply_updates else [],
    }


def adopt_legacy_started_barcode_route(
    *,
    material_request: str,
    package_id: str,
    confirmation: str | None = None,
) -> dict[str, Any]:
    _ensure_production_app()
    if confirmation != LEGACY_ROUTE_CONFIRMATION:
        frappe.throw("عبارة تأكيد اعتماد مسار الحزمة التاريخية غير صحيحة")
    if not frappe.db.exists("Material Request", material_request):
        frappe.throw("طلب المواد غير موجود")

    package_fields = service.doctype_fields(PACKAGE_DOCTYPE)
    existing = frappe.get_all(
        PACKAGE_DOCTYPE,
        filters={
            "name": package_id,
            "parent": material_request,
            "parentfield": PACKAGE_PARENTFIELD,
        },
        fields=service.selected_fields(
            PACKAGE_DOCTYPE,
            [
                "name",
                "parent",
                "package_key",
                "component",
                "item_code",
                "package_qty",
                "ready_qty",
                "loading_code",
            ],
            service.PACKAGE_OPTIONAL_FIELDS,
        ),
        limit_page_length=1,
    )
    if not existing:
        frappe.throw("الحزمة التاريخية غير موجودة داخل طلب المواد")
    existing_row = existing[0]
    if not service.package_started(existing_row):
        frappe.throw("الحزمة لم يبدأ تتبعها ولا تحتاج اعتمادًا تاريخيًا")

    old_route = service.tracking_route(existing_row.get("tracking_route"))
    if old_route == TRACKING_ROUTE_BARCODE:
        return {
            "status": "already_adopted",
            "material_request": material_request,
            "package_id": package_id,
        }
    if old_route != TRACKING_ROUTE_DELIVERY_ONLY:
        frappe.throw("مسار الحزمة التاريخية لا يسمح بالاعتماد الآمن")

    mr_doc = frappe.get_doc("Material Request", material_request)
    desired_rows = service.build_package_rows(
        mr_doc,
        component_rows=service.aggregate_components(mr_doc, validate_colors=False),
    )
    matches = [
        row for row in desired_rows if row.get("_existing_name") == package_id
    ]
    if len(matches) != 1:
        frappe.throw("مطابقة الحزمة التاريخية غير أحادية؛ لم يتم تعديلها")
    desired = matches[0]
    if service.tracking_route(desired.get("tracking_route")) != TRACKING_ROUTE_BARCODE:
        frappe.throw("المسار الحالي للمكون لا يتطلب باركود")

    if (existing_row.get("component") or "").strip() != (desired.get("component") or "").strip():
        frappe.throw("تغير مكون الحزمة التاريخية")
    if (existing_row.get("item_code") or "").strip() != (desired.get("item_code") or "").strip():
        frappe.throw("تغير صنف مخزن الحزمة التاريخية")
    if abs(flt(existing_row.get("package_qty")) - flt(desired.get("package_qty"))) > 0.000001:
        frappe.throw("تغيرت كمية الحزمة التاريخية")
    if abs(flt(existing_row.get("ready_qty")) - flt(desired.get("ready_qty"))) > 0.000001:
        frappe.throw("تغيرت الكمية المسجلة للحزمة التاريخية")
    old_code = (existing_row.get("loading_code") or "").strip()
    new_code = (desired.get("loading_code") or "").strip()
    if old_code and new_code and old_code != new_code:
        frappe.throw("تغير تكويد الحزمة التاريخية")

    values = {
        "tracking_route": TRACKING_ROUTE_BARCODE,
        "tracking_status": normalize_tracking_status(
            desired.get("tracking_status"),
            desired.get("package_qty"),
            desired.get("ready_qty"),
        ),
    }
    frappe.db.set_value(
        PACKAGE_DOCTYPE,
        package_id,
        {key: value for key, value in values.items() if key in package_fields},
        update_modified=False,
    )
    frappe.db.commit()
    return {
        "status": "adopted",
        "material_request": material_request,
        "package_id": package_id,
        "package_key": desired.get("package_key") or "",
        **values,
    }
