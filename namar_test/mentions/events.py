from __future__ import annotations

from collections.abc import Iterable
from contextlib import contextmanager
from typing import Any

import frappe
from frappe.desk.notifications import extract_mentions
from frappe.utils import get_datetime, now_datetime

from namar_test.followups.logic import (
    MAX_NOTE_LENGTH,
    MAX_REFERENCE_LENGTH,
    clean_text,
    mention_event_key,
    mention_state_event_key,
    mention_thread_key,
    plain_text,
)


THREAD_DOCTYPE = "Namar Mention Thread"
EVENT_DOCTYPE = "Namar Mention Event"
TODO_SYNC_FLAG = "namar_suppressed_mention_todo_sync"


def _value(doc: Any, fieldname: str, default: Any = None) -> Any:
    if hasattr(doc, "get"):
        value = doc.get(fieldname)
        return default if value is None else value
    return getattr(doc, fieldname, default)


def _unique(values: Iterable[str] | None) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        normalized = clean_text(value, MAX_REFERENCE_LENGTH)
        if not normalized or normalized in seen:
            continue
        result.append(normalized)
        seen.add(normalized)
    return result


def _eligible_recipient(user: str) -> bool:
    return bool(
        frappe.db.get_value(
            "User",
            {
                "name": user,
                "enabled": 1,
                "user_type": "System User",
                "allowed_in_mentions": 1,
            },
            "name",
        )
    )


def _can_read_reference(user: str, reference_doctype: str, reference_name: str) -> bool:
    try:
        return bool(
            frappe.has_permission(
                reference_doctype,
                "read",
                doc=reference_name,
                user=user,
            )
        )
    except (frappe.DoesNotExistError, frappe.PermissionError):
        return False


def _enqueue_snapshot(comment_doc: Any, recipients: Iterable[str], from_user: str) -> None:
    comment_name = clean_text(_value(comment_doc, "name"), MAX_REFERENCE_LENGTH)
    comment_modified = clean_text(_value(comment_doc, "modified"), MAX_REFERENCE_LENGTH)
    reference_doctype = clean_text(
        _value(comment_doc, "reference_doctype"),
        MAX_REFERENCE_LENGTH,
    )
    reference_name = clean_text(
        _value(comment_doc, "reference_name"),
        MAX_REFERENCE_LENGTH,
    )
    content = str(_value(comment_doc, "content", "") or "")
    if not all((comment_name, comment_modified, reference_doctype, reference_name, content)):
        return

    for for_user in _unique(recipients):
        if not _eligible_recipient(for_user):
            continue
        event_key = mention_event_key(for_user, comment_name, comment_modified, content)
        frappe.enqueue(
            "namar_test.mentions.events.process_mention_event",
            queue="short",
            enqueue_after_commit=True,
            job_id=f"namar-mention-{event_key}",
            deduplicate=True,
            for_user=for_user,
            reference_doctype=reference_doctype,
            reference_name=reference_name,
            comment_name=comment_name,
            comment_modified=comment_modified,
            content=content,
            from_user=from_user,
            event_key=event_key,
        )


def capture_mentions_after_insert(comment_doc: Any, method: str | None = None) -> None:
    if _value(comment_doc, "comment_type") != "Comment":
        return
    content = str(_value(comment_doc, "content", "") or "")
    if not content:
        return

    from_user = clean_text(
        getattr(getattr(frappe, "session", None), "user", None)
        or _value(comment_doc, "modified_by")
        or _value(comment_doc, "owner"),
        MAX_REFERENCE_LENGTH,
    )
    _enqueue_snapshot(comment_doc, extract_mentions(content), from_user)


def capture_mentions_on_update(comment_doc: Any, method: str | None = None) -> None:
    if _value(comment_doc, "comment_type") != "Comment":
        return
    try:
        previous_doc = comment_doc.get_doc_before_save()
    except Exception:
        previous_doc = None
    if not previous_doc:
        return

    previous_content = str(_value(previous_doc, "content", "") or "")
    content = str(_value(comment_doc, "content", "") or "")
    if not content or content == previous_content:
        return

    from_user = clean_text(
        getattr(getattr(frappe, "session", None), "user", None)
        or _value(comment_doc, "modified_by")
        or _value(comment_doc, "owner"),
        MAX_REFERENCE_LENGTH,
    )
    _enqueue_snapshot(comment_doc, extract_mentions(content), from_user)


def _linked_todo_is_open(thread) -> bool:
    if not thread.converted_to_todo:
        return False
    values = frappe.db.get_value(
        "ToDo",
        thread.converted_to_todo,
        ["status", "allocated_to", "reference_type", "reference_name"],
        as_dict=True,
    )
    return bool(
        values
        and values.status == "Open"
        and values.allocated_to == thread.for_user
        and values.reference_type == thread.reference_doctype
        and values.reference_name == thread.reference_name
    )


def _mention_tables_ready() -> bool:
    return bool(
        frappe.db.table_exists(THREAD_DOCTYPE)
        and frappe.db.table_exists(EVENT_DOCTYPE)
    )


def _todo_actor(todo: Any) -> str:
    actor = clean_text(
        _value(todo, "modified_by")
        or getattr(getattr(frappe, "session", None), "user", None)
        or "Administrator",
        MAX_REFERENCE_LENGTH,
    )
    return actor or "Administrator"


def _closed_via_followup(thread: Any) -> bool:
    return str(_value(thread, "closed_via_followup", 0) or 0).strip().lower() in {
        "1",
        "true",
    }


def _todo_matches_thread(todo: Any, thread: Any) -> bool:
    return bool(
        clean_text(_value(todo, "name"), MAX_REFERENCE_LENGTH)
        == clean_text(thread.converted_to_todo, MAX_REFERENCE_LENGTH)
        and clean_text(_value(todo, "allocated_to"), MAX_REFERENCE_LENGTH)
        == clean_text(thread.for_user, MAX_REFERENCE_LENGTH)
        and clean_text(_value(todo, "reference_type"), MAX_REFERENCE_LENGTH)
        == clean_text(thread.reference_doctype, MAX_REFERENCE_LENGTH)
        and clean_text(_value(todo, "reference_name"), MAX_REFERENCE_LENGTH)
        == clean_text(thread.reference_name, MAX_REFERENCE_LENGTH)
    )


def _todo_before_change(todo: Any) -> Any | None:
    try:
        return todo.get_doc_before_save()
    except (AttributeError, TypeError):
        return None


def _linked_thread_names(todo_name: str) -> list[str]:
    return list(
        frappe.db.get_values(
            THREAD_DOCTYPE,
            {"converted_to_todo": todo_name},
            "name",
            pluck=True,
        )
        or []
    )


def _locked_thread(thread_name: str):
    frappe.db.get_value(THREAD_DOCTYPE, thread_name, "name", for_update=True)
    return frappe.get_doc(THREAD_DOCTYPE, thread_name)


def _append_todo_state_event(
    thread: Any,
    event_type: str,
    content: str,
    actor: str,
) -> None:
    event_key = mention_state_event_key(
        event_type,
        thread.name,
        actor,
        thread.modified,
    )
    if frappe.db.exists(EVENT_DOCTYPE, event_key):
        return
    frappe.get_doc(
        {
            "doctype": EVENT_DOCTYPE,
            "event_key": event_key,
            "for_user": thread.for_user,
            "thread": thread.name,
            "event_type": event_type,
            "mentioned_at": now_datetime(),
            "from_user": actor,
            "content_plain": content,
        }
    ).insert(ignore_permissions=True, ignore_if_duplicate=True)


def _sync_is_suppressed(todo_name: str) -> bool:
    flags = getattr(frappe, "flags", None)
    counters = getattr(flags, TODO_SYNC_FLAG, {}) if flags is not None else {}
    return int((counters or {}).get(todo_name, 0)) > 0


@contextmanager
def suppress_todo_mention_sync(todo_name: str):
    """Defer one exact ToDo transition while it is replaced inside a savepoint."""

    normalized_name = clean_text(todo_name, MAX_REFERENCE_LENGTH)
    flags = getattr(frappe, "flags", None)
    if not normalized_name or flags is None:
        yield
        return

    counters = dict(getattr(flags, TODO_SYNC_FLAG, {}) or {})
    counters[normalized_name] = int(counters.get(normalized_name, 0)) + 1
    setattr(flags, TODO_SYNC_FLAG, counters)
    try:
        yield
    finally:
        counters = dict(getattr(flags, TODO_SYNC_FLAG, {}) or {})
        remaining = int(counters.get(normalized_name, 0)) - 1
        if remaining > 0:
            counters[normalized_name] = remaining
        else:
            counters.pop(normalized_name, None)
        setattr(flags, TODO_SYNC_FLAG, counters)


def sync_linked_mentions_on_todo_change(todo: Any, method: str | None = None) -> None:
    """Keep a converted mention aligned with its exact linked ToDo."""

    todo_name = clean_text(_value(todo, "name"), MAX_REFERENCE_LENGTH)
    if not todo_name or _sync_is_suppressed(todo_name) or not _mention_tables_ready():
        return

    status = clean_text(_value(todo, "status"), 20)
    if status not in {"Open", "Closed", "Cancelled"}:
        return

    actor = _todo_actor(todo)
    previous_todo = _todo_before_change(todo)
    for thread_name in _linked_thread_names(todo_name):
        thread = _locked_thread(thread_name)
        if not _todo_matches_thread(todo, thread):
            if previous_todo and _todo_matches_thread(previous_todo, thread):
                _reopen_converted_thread(
                    thread,
                    (
                        f"تغيّر مكلّف أو مرجع المتابعة {todo_name}؛ "
                        "عادت الرسالة إلى تحتاج قرارًا"
                    ),
                    actor,
                )
            continue

        if status == "Closed" and thread.status == "Converted":
            _append_todo_state_event(
                thread,
                "Closed",
                f"أُنجزت المتابعة {todo_name}",
                actor,
            )
            thread.status = "Closed"
            thread.closed_at = _value(todo, "modified") or now_datetime()
            thread.closed_by = actor
            thread.closed_via_followup = 1
            thread.save(ignore_permissions=True)
        elif status == "Cancelled" and (
            thread.status == "Converted"
            or (thread.status == "Closed" and _closed_via_followup(thread))
        ):
            _append_todo_state_event(
                thread,
                "Reopened",
                f"أُلغيت المتابعة {todo_name} وعادت الرسالة إلى تحتاج قرارًا",
                actor,
            )
            thread.status = "Open"
            thread.closed_at = None
            thread.closed_by = None
            thread.closed_via_followup = 0
            thread.save(ignore_permissions=True)
        elif status == "Open" and (
            thread.status == "Open"
            or (thread.status == "Closed" and _closed_via_followup(thread))
        ):
            _append_todo_state_event(
                thread,
                "Converted",
                f"أُعيد فتح المتابعة {todo_name}",
                actor,
            )
            thread.status = "Converted"
            thread.closed_at = None
            thread.closed_by = None
            thread.closed_via_followup = 0
            thread.save(ignore_permissions=True)


def sync_linked_mentions_on_todo_trash(todo: Any, method: str | None = None) -> None:
    """Return only active converted mentions to the decision queue on deletion."""

    todo_name = clean_text(_value(todo, "name"), MAX_REFERENCE_LENGTH)
    if not todo_name or not _mention_tables_ready():
        return

    actor = _todo_actor(todo)
    for thread_name in _linked_thread_names(todo_name):
        thread = _locked_thread(thread_name)
        if not _todo_matches_thread(todo, thread):
            continue
        if thread.status == "Converted":
            _append_todo_state_event(
                thread,
                "Reopened",
                f"حُذفت المتابعة {todo_name} وعادت الرسالة إلى تحتاج قرارًا",
                actor,
            )
            thread.status = "Open"
            thread.converted_to_todo = None
            thread.closed_at = None
            thread.closed_by = None
            thread.closed_via_followup = 0
            thread.save(ignore_permissions=True)
        elif thread.status == "Closed" and _closed_via_followup(thread):
            thread.converted_to_todo = None
            thread.save(ignore_permissions=True)


def _reopen_converted_thread(
    thread: Any,
    reason: str,
    actor: str,
) -> bool:
    if thread.status != "Converted":
        return False

    normalized_actor = clean_text(actor, MAX_REFERENCE_LENGTH) or "Administrator"
    normalized_reason = plain_text(reason, MAX_NOTE_LENGTH) or "تعذر العثور على المتابعة المرتبطة"
    _append_todo_state_event(
        thread,
        "Reopened",
        normalized_reason,
        normalized_actor,
    )
    thread.status = "Open"
    thread.closed_at = None
    thread.closed_by = None
    thread.closed_via_followup = 0
    thread.save(ignore_permissions=True)
    return True


def reopen_stale_converted_mention(
    thread_name: str,
    reason: str,
    actor: str = "Administrator",
) -> bool:
    """Repair a converted thread whose stored ToDo can no longer be active."""

    if not _mention_tables_ready():
        return False
    thread = _locked_thread(clean_text(thread_name, MAX_REFERENCE_LENGTH))
    return _reopen_converted_thread(thread, reason, actor)


def transfer_linked_mentions_to_next_todo(current_todo: Any, next_todo: Any) -> int:
    """Move converted mention pointers atomically to the next open follow-up."""

    current_name = clean_text(_value(current_todo, "name"), MAX_REFERENCE_LENGTH)
    next_name = clean_text(_value(next_todo, "name"), MAX_REFERENCE_LENGTH)
    if not current_name or not next_name or not _mention_tables_ready():
        return 0
    if clean_text(_value(next_todo, "status"), 20) != "Open":
        frappe.throw("المتابعة القادمة ليست مفتوحة", frappe.ValidationError)
    if any(
        clean_text(_value(current_todo, fieldname), MAX_REFERENCE_LENGTH)
        != clean_text(_value(next_todo, fieldname), MAX_REFERENCE_LENGTH)
        for fieldname in ("allocated_to", "reference_type", "reference_name")
    ):
        frappe.throw("المتابعة القادمة لا تطابق الرسالة المحوّلة", frappe.ValidationError)

    actor = _todo_actor(next_todo)
    transferred = 0
    for thread_name in _linked_thread_names(current_name):
        thread = _locked_thread(thread_name)
        if thread.status != "Converted" or not _todo_matches_thread(current_todo, thread):
            continue
        _append_todo_state_event(
            thread,
            "Converted",
            f"أُنجزت المتابعة {current_name} وجُدولت المتابعة التالية {next_name}",
            actor,
        )
        thread.converted_to_todo = next_name
        thread.converted_at = now_datetime()
        thread.converted_by = actor
        thread.closed_at = None
        thread.closed_by = None
        thread.closed_via_followup = 0
        thread.save(ignore_permissions=True)
        transferred += 1
    return transferred


def _get_or_create_thread(
    for_user: str,
    reference_doctype: str,
    reference_name: str,
):
    thread_name = mention_thread_key(for_user, reference_doctype, reference_name)
    if not frappe.db.exists(THREAD_DOCTYPE, thread_name):
        thread = frappe.get_doc(
            {
                "doctype": THREAD_DOCTYPE,
                "thread_key": thread_name,
                "for_user": for_user,
                "status": "Open",
                "reference_doctype": reference_doctype,
                "reference_name": reference_name,
                "mention_count": 0,
            }
        )
        thread.insert(ignore_permissions=True, ignore_if_duplicate=True)

    frappe.db.get_value(THREAD_DOCTYPE, thread_name, "name", for_update=True)
    return frappe.get_doc(THREAD_DOCTYPE, thread_name)


def process_mention_event(
    *,
    for_user: str,
    reference_doctype: str,
    reference_name: str,
    comment_name: str,
    comment_modified: str,
    content: str,
    from_user: str,
    event_key: str,
) -> str | None:
    """Persist one trusted Comment snapshot; safe to retry after queue failures."""

    recipients = _unique(extract_mentions(content or ""))
    expected_event_key = mention_event_key(
        for_user,
        comment_name,
        comment_modified,
        content,
    )
    if event_key != expected_event_key or for_user not in recipients:
        return None
    if not _eligible_recipient(for_user):
        return None
    if not _can_read_reference(for_user, reference_doctype, reference_name):
        return None

    comment_reference = frappe.db.get_value(
        "Comment",
        comment_name,
        ["reference_doctype", "reference_name"],
        as_dict=True,
    )
    if not comment_reference or (
        comment_reference.reference_doctype != reference_doctype
        or comment_reference.reference_name != reference_name
    ):
        return None

    thread = _get_or_create_thread(for_user, reference_doctype, reference_name)
    if frappe.db.exists(
        EVENT_DOCTYPE,
        {"name": event_key, "for_user": for_user, "thread": thread.name},
    ):
        return thread.name

    event_at = comment_modified or now_datetime()
    content_plain = plain_text(content, MAX_NOTE_LENGTH)
    event = frappe.get_doc(
        {
            "doctype": EVENT_DOCTYPE,
            "event_key": event_key,
            "for_user": for_user,
            "thread": thread.name,
            "event_type": "Mention",
            "mentioned_at": event_at,
            "comment": comment_name,
            "comment_modified": comment_modified,
            "from_user": from_user,
            "content_plain": content_plain,
        },
    )
    event.insert(ignore_permissions=True, ignore_if_duplicate=True)
    thread.mention_count = int(thread.mention_count or 0) + 1
    thread.last_event_key = event_key
    incoming_at = get_datetime(event_at)
    current_latest_at = (
        get_datetime(thread.latest_mentioned_at) if thread.latest_mentioned_at else None
    )
    if not thread.first_mentioned_at or incoming_at < get_datetime(thread.first_mentioned_at):
        thread.first_mentioned_at = event_at
    if not current_latest_at or incoming_at >= current_latest_at:
        thread.latest_comment = comment_name
        thread.latest_from_user = from_user
        thread.latest_preview_plain = content_plain
        thread.latest_mentioned_at = event_at
    if thread.status == "Closed":
        thread.status = "Open"
        thread.closed_at = None
        thread.closed_by = None
        thread.closed_via_followup = 0
    elif thread.status == "Converted" and not _linked_todo_is_open(thread):
        thread.status = "Open"
        thread.closed_at = None
        thread.closed_by = None
        thread.closed_via_followup = 0
    thread.save(ignore_permissions=True)
    return thread.name


def link_notification_to_mention_thread(
    notification_doc: Any,
    method: str | None = None,
) -> None:
    if _value(notification_doc, "type") != "Mention":
        return
    for_user = clean_text(_value(notification_doc, "for_user"), MAX_REFERENCE_LENGTH)
    reference_doctype = clean_text(
        _value(notification_doc, "document_type"),
        MAX_REFERENCE_LENGTH,
    )
    reference_name = clean_text(
        _value(notification_doc, "document_name"),
        MAX_REFERENCE_LENGTH,
    )
    if not all((for_user, reference_doctype, reference_name)):
        return
    if not _eligible_recipient(for_user) or not _can_read_reference(
        for_user,
        reference_doctype,
        reference_name,
    ):
        return

    thread_name = mention_thread_key(for_user, reference_doctype, reference_name)
    notification_doc.link = f"/app/my-followups?source=mentions&thread={thread_name}"
