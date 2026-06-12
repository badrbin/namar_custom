from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any


MentionExtractor = Callable[[str], Iterable[str]]


def _get_value(doc: Any, fieldname: str, default: Any = None) -> Any:
    if hasattr(doc, "get"):
        value = doc.get(fieldname)
        return default if value is None else value
    return getattr(doc, fieldname, default)


def _unique_preserve_order(values: Iterable[str] | None) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        if not value or value in seen:
            continue
        unique.append(value)
        seen.add(value)
    return unique


def get_new_mentions(
    old_content: str | None,
    new_content: str | None,
    extractor: MentionExtractor | None = None,
) -> list[str]:
    """Return mentions that exist in the new comment content only.

    Frappe's default Comment controller sends mention notifications only on
    insert.  This helper intentionally computes a delta for updates so saving
    an already-mentioned user again does not duplicate notifications.
    """

    if not new_content:
        return []

    if extractor is None:
        from frappe.desk.notifications import extract_mentions

        extractor = extract_mentions

    old_mentions = set(_unique_preserve_order(extractor(old_content or "")))
    new_mentions = _unique_preserve_order(extractor(new_content or ""))
    return [mention for mention in new_mentions if mention not in old_mentions]


def _get_allowed_mention_recipient_emails(usernames: Iterable[str]) -> list[str]:
    import frappe

    recipients: list[str] = []
    seen: set[str] = set()
    for username in usernames:
        email = frappe.db.get_value(
            "User",
            {
                "enabled": 1,
                "name": username,
                "user_type": "System User",
                "allowed_in_mentions": 1,
            },
            "email",
        )
        if email and email not in seen:
            recipients.append(email)
            seen.add(email)
    return recipients


def _enqueue_mention_notifications(comment_doc: Any, mentioned_users: Iterable[str]) -> None:
    import frappe
    from frappe import _
    from frappe.desk.doctype.notification_log.notification_log import (
        enqueue_create_notification,
        get_title,
        get_title_html,
    )
    from frappe.utils import get_fullname

    recipients = _get_allowed_mention_recipient_emails(mentioned_users)
    if not recipients:
        return

    reference_doctype = _get_value(comment_doc, "reference_doctype")
    reference_name = _get_value(comment_doc, "reference_name")
    content = _get_value(comment_doc, "content")
    from_user = (
        getattr(getattr(frappe, "session", None), "user", None)
        or _get_value(comment_doc, "modified_by")
        or _get_value(comment_doc, "owner")
    )

    sender_fullname = get_fullname(from_user)
    title = get_title(reference_doctype, reference_name)
    notification_message = _("""{0} mentioned you in a comment in {1} {2}""").format(
        frappe.bold(sender_fullname),
        frappe.bold(reference_doctype),
        get_title_html(title),
    )

    enqueue_create_notification(
        recipients,
        {
            "type": "Mention",
            "document_type": reference_doctype,
            "document_name": reference_name,
            "subject": notification_message,
            "from_user": from_user,
            "email_content": content,
        },
    )


def notify_new_mentions_on_comment_update(comment_doc: Any, method: str | None = None) -> None:
    """Notify only users newly mentioned when an existing Comment is edited."""

    if _get_value(comment_doc, "comment_type") != "Comment":
        return

    if not (
        _get_value(comment_doc, "reference_doctype")
        and _get_value(comment_doc, "reference_name")
        and _get_value(comment_doc, "content")
    ):
        return

    try:
        previous_doc = comment_doc.get_doc_before_save()
    except Exception:
        previous_doc = None

    # On insert, Frappe's built-in Comment.after_insert already handles all
    # mentions.  Without a previous document there is no safe update delta.
    if not previous_doc:
        return

    previous_content = _get_value(previous_doc, "content")
    current_content = _get_value(comment_doc, "content")
    if previous_content == current_content:
        return

    new_mentions = get_new_mentions(previous_content, current_content)
    if not new_mentions:
        return

    _enqueue_mention_notifications(comment_doc, new_mentions)
