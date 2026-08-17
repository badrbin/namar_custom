import frappe
from frappe.model.document import Document


class NamarMentionEvent(Document):
    pass


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
    return False


def on_doctype_update() -> None:
    frappe.db.add_index(
        "Namar Mention Event",
        ["for_user", "thread", "mentioned_at"],
        "mention_event_owner_thread",
    )
