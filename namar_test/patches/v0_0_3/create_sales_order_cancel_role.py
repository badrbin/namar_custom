from __future__ import annotations

import frappe


ROLE_NAME = "إلغاء أمر البيع"


def execute():
    if frappe.db.exists("Role", ROLE_NAME):
        role = frappe.get_doc("Role", ROLE_NAME)
        role.disabled = 0
        role.desk_access = 1
        role.save(ignore_permissions=True)
        return

    role = frappe.get_doc(
        {
            "doctype": "Role",
            "role_name": ROLE_NAME,
            "desk_access": 1,
            "disabled": 0,
        }
    )
    role.insert(ignore_permissions=True)
