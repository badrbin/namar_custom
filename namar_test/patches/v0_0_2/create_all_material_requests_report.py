from __future__ import annotations

import frappe


REPORT_NAME = "كل طلبات المواد"


def execute():
    values = {
        "report_name": REPORT_NAME,
        "ref_doctype": "Material Request",
        "module": "Namar Test",
        "is_standard": "Yes",
        "report_type": "Script Report",
        "prepared_report": 0,
        "disabled": 0,
        "add_total_row": 0,
    }

    if frappe.db.exists("Report", REPORT_NAME):
        report = frappe.get_doc("Report", REPORT_NAME)
        for fieldname, value in values.items():
            report.set(fieldname, value)
        report.set("roles", [])
    else:
        report = frappe.get_doc({"doctype": "Report", "name": REPORT_NAME, **values})

    for role in ("System Manager", "Stock User", "Stock Manager", "Sales User"):
        if frappe.db.exists("Role", role):
            report.append("roles", {"role": role})

    report.save(ignore_permissions=True)
