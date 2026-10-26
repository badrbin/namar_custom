"""Review/apply production manufacturing status changes, preserving counters."""
from __future__ import annotations

import math

import frappe
from frappe.utils import cint

from namar_custom.delivery_components.package_logic import (
    build_fulfillment_readiness,
    combined_manufacturing_status,
)


FIELDS = (
    "docstatus", "custom_manufacturing_status",
    "custom_manufacturing_total_count", "custom_manufacturing_remaining_count",
)


def _check_access():
    frappe.only_for("System Manager")
    if "namar_custom" not in frappe.get_installed_apps():
        frappe.throw("هذه المعالجة مخصصة لتطبيق الأساسي namar_custom")


def _proposal(row):
    total = row.get("custom_manufacturing_total_count")
    remaining = row.get("custom_manufacturing_remaining_count")
    try:
        valid = total is not None and remaining is not None
        valid = valid and math.isfinite(float(total)) and math.isfinite(float(remaining))
        valid = valid and 0 <= float(remaining) <= float(total)
    except (TypeError, ValueError):
        valid = False
    if not valid:
        return None
    # Use exactly the same normalization and rounding as the existing service.
    readiness = build_fulfillment_readiness(
        door_total=total, door_remaining=remaining,
        package_total=0, package_ready=0, package_loaded=0, package_delivered=0,
    )
    return {
        "name": row["name"],
        "before": {field: row.get(field) for field in FIELDS},
        "after": {"custom_manufacturing_status": combined_manufacturing_status(readiness)},
    }


@frappe.whitelist()
def preview(start_after="", limit=100):
    _check_access()
    rows = frappe.get_all(
        "Material Request", filters={"docstatus": ["!=", 2], "name": [">", start_after or ""]},
        fields=["name", *FIELDS], order_by="name asc",
        limit_page_length=min(max(cint(limit), 1), 250),
    )
    changes, review = [], []
    for row in rows:
        proposed = _proposal(row)
        if proposed is None:
            review.append({"name": row["name"], "reason": "عدادات الأبواب مفقودة أو غير صالحة"})
        elif proposed["after"]["custom_manufacturing_status"] != row.get("custom_manufacturing_status"):
            changes.append(proposed)
    return {
        "site": frappe.local.site, "scanned": len(rows),
        "next_cursor": rows[-1]["name"] if rows else None,
        "changes": changes, "review": review,
    }


@frappe.whitelist(methods=["POST"])
def apply(plan):
    _check_access()
    if isinstance(plan, str):
        plan = frappe.parse_json(plan)
    if not isinstance(plan, dict) or plan.get("site") != frappe.local.site:
        frappe.throw("خطة المعالجة لا تخص هذا الموقع")
    changes = plan.get("changes")
    if not isinstance(changes, list) or len(changes) > 250:
        frappe.throw("خطة المعالجة غير صالحة")
    if any(not isinstance(change, dict) for change in changes):
        frappe.throw("خطة المعالجة غير صالحة")
    names = [change.get("name") for change in changes]
    if any(not isinstance(name, str) or not name for name in names) or len(set(names)) != len(names):
        frappe.throw("خطة المعالجة تحتوي طلبات مكررة أو غير صالحة")
    try:
        for expected in sorted(changes, key=lambda change: change["name"]):
            rows = frappe.db.sql(
                "SELECT name, docstatus, custom_manufacturing_status, "
                "custom_manufacturing_total_count, custom_manufacturing_remaining_count "
                "FROM `tabMaterial Request` WHERE name = %s FOR UPDATE",
                (expected["name"],), as_dict=True,
            )
            current = rows[0] if rows else None
            if not current or cint(current.get("docstatus")) == 2 or _proposal(current) != expected:
                frappe.throw("تغير الطلب %s بعد المعاينة؛ أعد مراجعته" % expected["name"])
            frappe.db.set_value("Material Request", current["name"], expected["after"], update_modified=False)
    except Exception:
        frappe.db.rollback()
        raise
    # POST commits atomically at the normal request boundary, never here.
    return {"updated": len(changes), "changes": changes}
