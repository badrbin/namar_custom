"""Preview and reconcile existing TEST manufacturing headers after deployment.

No package, fulfillment, workflow, manufacturing event or completion timestamp
is written. An apply consumes an unchanged preview and never commits itself.
"""
from __future__ import annotations

import frappe
from frappe.utils import cint

from namar_test.delivery_components.package_logic import combined_manufacturing_status
from namar_test.delivery_components.service import (
    door_manufacturing_counts,
    locked_material_request_source,
)


FIELDS = (
    "custom_manufacturing_status",
    "custom_manufacturing_total_count",
    "custom_manufacturing_remaining_count",
)


def _check_access():
    frappe.only_for("System Manager")
    if "namar_test" not in frappe.get_installed_apps():
        frappe.throw("هذه المعالجة مخصصة للتجريبي فقط")


def _change(doc):
    total, remaining = door_manufacturing_counts(doc)
    after = {
        "custom_manufacturing_status": combined_manufacturing_status({
            "door_total": total, "door_ready": total - remaining,
        }),
        "custom_manufacturing_total_count": total,
        "custom_manufacturing_remaining_count": remaining,
    }
    before = {field: doc.get(field) for field in FIELDS}
    if before == after:
        return None
    return {"name": doc.name, "before": before, "after": after}


@frappe.whitelist()
def preview(start_after="", limit=100):
    _check_access()
    limit = min(max(cint(limit), 1), 250)
    names = frappe.get_all(
        "Material Request", filters={"docstatus": ["!=", 2], "name": [">", start_after or ""]},
        pluck="name", order_by="name asc", limit_page_length=limit,
    )
    changes = []
    for name in names:
        change = _change(frappe.get_doc("Material Request", name))
        if change:
            changes.append(change)
    return {
        "site": frappe.local.site,
        "scanned": len(names),
        "next_cursor": names[-1] if names else None,
        "changes": changes,
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
    names = [change.get("name") for change in changes]
    if any(not name for name in names) or len(set(names)) != len(names):
        frappe.throw("خطة المعالجة تحتوي طلبات مكررة أو غير صالحة")
    try:
        # Consistent lock order, followed by a fresh calculation, avoids
        # overwriting a scanner/cancellation update since the preview.
        for expected in sorted(changes, key=lambda change: change["name"]):
            doc, _packages = locked_material_request_source(expected["name"])
            if cint(doc.docstatus) == 2 or _change(doc) != expected:
                frappe.throw("تغير الطلب %s بعد المعاينة؛ أعد مراجعته" % expected["name"])
            frappe.db.set_value("Material Request", doc.name, expected["after"], update_modified=False)
    except Exception:
        frappe.db.rollback()
        raise
    return {"updated": len(changes), "changes": changes}
