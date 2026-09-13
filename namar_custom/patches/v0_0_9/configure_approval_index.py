"""Prepare the private approval index without enabling it or scanning documents."""

import frappe


def execute():
    from namar_custom.followups.approval_routing_settings import configure_approval_visibility_fields

    configure_approval_visibility_fields()
    indexes = (
        (
            "Namar Approval Index Recipient",
            ["for_user", "epoch", "action_name"],
            "approval_recipient_user_epoch_action",
        ),
        (
            "Namar Approval Index Recipient",
            ["action_name"],
            "action_name_index",
        ),
        (
            "Namar Approval Index Action",
            ["state", "epoch"],
            "approval_index_state_epoch",
        ),
        (
            "Namar Approval Index Action",
            ["reference_doctype", "reference_name"],
            "approval_index_reference",
        ),
        (
            "Workflow Action",
            ["reference_doctype", "reference_name", "status"],
            "approval_native_reference_status",
        ),
    )
    for doctype, fields, name in indexes:
        if frappe.db.table_exists(doctype):
            frappe.db.add_index(doctype, fields, name)

    if not frappe.db.exists("Namar Approval Index Control", "current"):
        frappe.get_doc(
            {
                "doctype": "Namar Approval Index Control",
                "name": "current",
                "epoch": 1,
                "scan_complete": 0,
            }
        ).insert(ignore_permissions=True)
