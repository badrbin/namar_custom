from __future__ import annotations

from collections import Counter
from typing import Any

import frappe
from frappe.utils import cint

from namar_custom.delivery_components.snapshot_backfill import (
    SNAPSHOT_FIELDS,
    build_event_snapshot_updates,
)


EVENT_DOCTYPE = "Material Request Component Package Event"
PACKAGE_DOCTYPE = "Material Request Delivery Component Package"
PACKAGE_PARENTFIELD = "custom_delivery_component_packages"
PRODUCTION_APP = "namar_custom"
BACKFILL_CONFIRMATION = "NAMAR_CUSTOM_EVENT_SNAPSHOT_BACKFILL_20260813"


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
