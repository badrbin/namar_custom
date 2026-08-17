from __future__ import annotations

import frappe
from frappe.model.document import Document


class NamarMentionThread(Document):
    def on_trash(self) -> None:
        if frappe.db.table_exists("Namar Mention Event"):
            frappe.db.delete(
                "Namar Mention Event",
                {"thread": self.name, "for_user": self.for_user},
            )


def get_permission_query_conditions(
    user: str | None = None,
    doctype: str | None = None,
) -> str:
    return "1 = 0"


def has_permission(
    doc,
    ptype: str = "read",
    user: str | None = None,
    debug: bool = False,
) -> bool:
    user = user or frappe.session.user
    if ptype != "delete":
        return False
    return bool(
        user
        and user != "Guest"
        and doc.for_user == user
        and "System Manager" in frappe.get_roles(user)
    )


def on_doctype_update() -> None:
    frappe.db.add_index(
        "Namar Mention Thread",
        ["for_user", "status", "latest_mentioned_at"],
        "mention_user_status_activity",
    )
    frappe.db.add_index(
        "Namar Mention Thread",
        ["reference_doctype", "reference_name"],
        "mention_reference",
    )
