from __future__ import annotations

import html
from contextlib import contextmanager
from typing import Any, Callable, TypeVar
from uuid import uuid4

import frappe
from frappe.utils import get_absolute_url, now_datetime

from namar_test.followups import service as followup_service
from namar_test.followups.logic import (
    MAX_DESCRIPTION_LENGTH,
    MAX_NOTE_LENGTH,
    MAX_REFERENCE_LENGTH,
    clean_text,
    mention_reply_event_key,
    mention_state_event_key,
    normalize_date,
    normalize_mention_bucket,
    normalize_priority,
    normalize_request_id,
    normalize_search,
    normalize_seen,
    parse_non_negative_int,
    parse_positive_int,
    plain_text,
    require_text,
    validate_expected_mention_event_key,
)


THREAD_DOCTYPE = "Namar Mention Thread"
EVENT_DOCTYPE = "Namar Mention Event"
MAX_MENTION_PAGE_LENGTH = 100
DEFAULT_MENTION_PAGE_LENGTH = 25

THREAD_FIELDS = (
    "name",
    "for_user",
    "status",
    "reference_doctype",
    "reference_name",
    "latest_comment",
    "latest_from_user",
    "latest_preview_plain",
    "first_mentioned_at",
    "latest_mentioned_at",
    "mention_count",
    "last_event_key",
    "last_seen_event_key",
    "converted_to_todo",
    "converted_at",
    "converted_by",
    "closed_at",
    "closed_by",
    "last_replied_at",
    "last_replied_by",
    "last_reply_comment",
    "creation",
    "modified",
)

EVENT_FIELDS = (
    "name",
    "event_key",
    "for_user",
    "thread",
    "event_type",
    "mentioned_at",
    "comment",
    "comment_modified",
    "from_user",
    "content_plain",
    "request_id",
    "creation",
    "modified",
)

T = TypeVar("T")


def _assert_authenticated() -> str:
    user = clean_text(frappe.session.user, MAX_REFERENCE_LENGTH)
    if not user or user == "Guest":
        frappe.throw("يلزم تسجيل الدخول لعرض وارد الإشارات", frappe.PermissionError)
    return user


def _logic(call: Callable[..., T], *args, **kwargs) -> T:
    try:
        return call(*args, **kwargs)
    except PermissionError as exc:
        frappe.throw(str(exc), frappe.PermissionError)
    except ValueError as exc:
        frappe.throw(str(exc), frappe.ValidationError)
    raise AssertionError("frappe.throw should have raised")


def _required(value: Any, label: str, max_length: int) -> str:
    return _logic(require_text, value, label, max_length)


def _plain_required(value: Any, label: str, max_length: int) -> str:
    raw = _required(value, label, max_length)
    return _required(plain_text(raw, max_length), label, max_length)


@contextmanager
def _savepoint(prefix: str):
    name = f"{prefix}_{uuid4().hex[:12]}"
    frappe.db.savepoint(name)
    try:
        yield
    except Exception:
        frappe.db.rollback(save_point=name)
        raise
    else:
        frappe.db.release_savepoint(name)


def _get_owned_thread(thread_name: Any, *, for_update: bool = False):
    user = _assert_authenticated()
    name = _required(thread_name, "عنصر وارد الإشارات", MAX_REFERENCE_LENGTH)
    owned_name = frappe.db.get_value(
        THREAD_DOCTYPE,
        {"name": name, "for_user": user},
        "name",
        for_update=for_update,
    )
    if not owned_name:
        frappe.throw("عنصر وارد الإشارات غير متاح لك", frappe.PermissionError)

    return frappe.get_doc(THREAD_DOCTYPE, owned_name)


def _assert_expected_event(thread, expected_last_event_key: Any) -> str:
    """Validate the version displayed by the caller while the thread row is locked."""

    return _logic(
        validate_expected_mention_event_key,
        expected_last_event_key,
        thread.last_event_key,
    )


def _get_reference_doc(thread):
    try:
        doc = frappe.get_doc(thread.reference_doctype, thread.reference_name)
        doc.check_permission("read")
    except (frappe.DoesNotExistError, frappe.PermissionError):
        frappe.throw("المستند المرجعي غير متاح لك", frappe.PermissionError)
    return doc


def _can_read_reference(row, user: str) -> bool:
    try:
        return bool(
            frappe.has_permission(
                row.reference_doctype,
                "read",
                doc=row.reference_name,
                user=user,
            )
        )
    except (frappe.DoesNotExistError, frappe.PermissionError):
        return False


def _is_unread(row) -> bool:
    return bool(row.last_event_key and row.last_seen_event_key != row.last_event_key)


def _user_full_name(user: Any) -> str | None:
    if not user:
        return None
    return frappe.get_cached_value("User", user, "full_name") or str(user)


def _reference_title(row) -> str:
    return (
        followup_service._readable_reference_title(  # noqa: SLF001
            row.reference_doctype,
            row.reference_name,
        )
        or row.reference_name
    )


def _serialize_thread(row, *, reference_title: str | None = None) -> dict[str, Any]:
    values = {fieldname: row.get(fieldname) for fieldname in THREAD_FIELDS}
    values["latest_preview_plain"] = plain_text(
        values.get("latest_preview_plain"),
        MAX_NOTE_LENGTH,
    )
    values["content_format"] = "plain_text"
    values["latest_from_user_name"] = _user_full_name(values.get("latest_from_user"))
    values["unread"] = int(_is_unread(row))
    values["reference_type"] = values.get("reference_doctype")
    values["reference_title"] = reference_title or _reference_title(row)
    values["reference_route"] = get_absolute_url(
        values["reference_doctype"],
        values["reference_name"],
    )
    return values


def _serialize_event(event) -> dict[str, Any]:
    return {
        "event_key": event.get("event_key"),
        "event_type": event.get("event_type"),
        "mentioned_at": event.get("mentioned_at"),
        "comment": event.get("comment"),
        "comment_modified": event.get("comment_modified"),
        "from_user": event.get("from_user"),
        "from_user_name": _user_full_name(event.get("from_user")),
        "content_plain": plain_text(event.get("content_plain"), MAX_NOTE_LENGTH),
        "content_format": "plain_text",
        "request_id": event.get("request_id"),
    }


def _get_thread_events(thread) -> list[Any]:
    return frappe.get_all(
        EVENT_DOCTYPE,
        fields=list(EVENT_FIELDS),
        filters={"for_user": thread.for_user, "thread": thread.name},
        order_by="mentioned_at asc, creation asc, name asc",
        limit_page_length=0,
    )


def _thread_permissions(thread) -> dict[str, bool]:
    is_closed = thread.status == "Closed"
    return {
        "can_reply": not is_closed,
        "can_close": not is_closed,
        "can_reopen": is_closed,
        "can_convert": thread.status == "Open",
    }


def _safe_threads(user: str) -> list[Any]:
    rows = frappe.get_all(
        THREAD_DOCTYPE,
        fields=list(THREAD_FIELDS),
        filters={"for_user": user},
        order_by="latest_mentioned_at desc, modified desc",
        limit_page_length=0,
    )
    permission_cache: dict[tuple[str, str], bool] = {}
    safe_rows: list[Any] = []
    for row in rows:
        key = (row.reference_doctype, row.reference_name)
        allowed = permission_cache.get(key)
        if allowed is None:
            allowed = _can_read_reference(row, user)
            permission_cache[key] = allowed
        if allowed:
            safe_rows.append(row)
    safe_rows.sort(
        key=lambda row: max(
            clean_text(row.latest_mentioned_at),
            clean_text(row.last_replied_at),
            clean_text(row.modified),
        ),
        reverse=True,
    )
    return safe_rows


def _mention_counts(rows: list[Any]) -> dict[str, int]:
    counts = {"all": len(rows), "open": 0, "unread": 0, "converted": 0, "closed": 0}
    for row in rows:
        status_key = clean_text(row.status, 20).lower()
        if status_key in counts:
            counts[status_key] += 1
        if _is_unread(row):
            counts["unread"] += 1
    return counts


def _matches_bucket(row, bucket: str) -> bool:
    if bucket == "unread":
        return _is_unread(row)
    return clean_text(row.status, 20).lower() == bucket


def _matches_search(row, search: str, title: str) -> bool:
    if not search:
        return True
    needle = search.casefold()
    values = (
        row.reference_doctype,
        row.reference_name,
        title,
        row.latest_preview_plain,
        row.latest_from_user,
        _user_full_name(row.latest_from_user),
    )
    return any(needle in clean_text(value).casefold() for value in values if value)


def get_mentions(
    bucket: str = "open",
    search: str = "",
    limit_start: int | str = 0,
    page_length: int | str = DEFAULT_MENTION_PAGE_LENGTH,
) -> dict[str, Any]:
    user = _assert_authenticated()
    normalized_bucket = _logic(normalize_mention_bucket, bucket)
    normalized_search = normalize_search(search)
    start = parse_non_negative_int(limit_start, 0, 1_000_000)
    length = parse_positive_int(
        page_length,
        DEFAULT_MENTION_PAGE_LENGTH,
        MAX_MENTION_PAGE_LENGTH,
    )

    safe_rows = _safe_threads(user)
    counts = _mention_counts(safe_rows)
    matching: list[tuple[Any, str]] = []
    for row in safe_rows:
        if not _matches_bucket(row, normalized_bucket):
            continue
        title = _reference_title(row)
        if _matches_search(row, normalized_search, title):
            matching.append((row, title))

    visible = matching[start : start + length]
    has_more = start + len(visible) < len(matching)
    return {
        "items": [_serialize_thread(row, reference_title=title) for row, title in visible],
        "bucket": normalized_bucket,
        "search": normalized_search,
        "counts": counts,
        "total": len(matching),
        "limit_start": start,
        "page_length": length,
        "has_more": has_more,
        "next_start": start + len(visible) if has_more else None,
    }


def get_mention_detail(thread_name: str) -> dict[str, Any]:
    thread = _get_owned_thread(thread_name)
    reference_doc = _get_reference_doc(thread)
    messages = [
        _serialize_event(event)
        for event in _get_thread_events(thread)
        if event.event_type in {"Mention", "Reply"}
    ]
    return {
        "mention": _serialize_thread(thread),
        "reference": followup_service._reference_summary(reference_doc),  # noqa: SLF001
        "messages": messages,
        "permissions": _thread_permissions(thread),
    }


def mark_mention_seen(
    thread_name: str,
    seen: Any,
    expected_last_event_key: str,
) -> dict[str, Any]:
    normalized_seen = _logic(normalize_seen, seen)
    with _savepoint("mention_seen"):
        thread = _get_owned_thread(thread_name, for_update=True)
        _get_reference_doc(thread)
        expected_event_key = _assert_expected_event(thread, expected_last_event_key)
        value = expected_event_key if normalized_seen else None
        if thread.last_seen_event_key != value:
            thread.last_seen_event_key = value
            thread.save(ignore_permissions=True)
    return {"mention": _serialize_thread(thread)}


def _insert_event(thread, values: dict[str, Any]):
    event_key = values["event_key"]
    existing_name = frappe.db.get_value(
        EVENT_DOCTYPE,
        {
            "name": event_key,
            "for_user": thread.for_user,
            "thread": thread.name,
        },
        "name",
    )
    if existing_name:
        return frappe.get_doc(EVENT_DOCTYPE, existing_name)

    event = frappe.get_doc(
        {
            "doctype": EVENT_DOCTYPE,
            "for_user": thread.for_user,
            "thread": thread.name,
            **values,
        }
    )
    event.insert(ignore_permissions=True, ignore_if_duplicate=True)
    return event


def _append_state_event(thread, event_type: str, content: str):
    user = _assert_authenticated()
    event_key = _logic(
        mention_state_event_key,
        event_type,
        thread.name,
        user,
        thread.modified,
    )
    return _insert_event(
        thread,
        {
            "event_key": event_key,
            "event_type": event_type,
            "mentioned_at": now_datetime(),
            "from_user": user,
            "content_plain": content,
        },
    )


def _close_in_memory(thread, expected_event_key: str) -> bool:
    if thread.status == "Closed":
        return False
    user = _assert_authenticated()
    _append_state_event(thread, "Closed", "أُغلقت الإشارة")
    thread.status = "Closed"
    thread.closed_at = now_datetime()
    thread.closed_by = user
    thread.last_seen_event_key = expected_event_key
    return True


def _existing_reply_event(thread, event_key: str):
    event_name = frappe.db.get_value(
        EVENT_DOCTYPE,
        {
            "name": event_key,
            "for_user": thread.for_user,
            "thread": thread.name,
            "event_type": "Reply",
        },
        "name",
    )
    return frappe.get_doc(EVENT_DOCTYPE, event_name) if event_name else None


def _reply_target(thread, current_user: str) -> str | None:
    target = clean_text(thread.latest_from_user, MAX_REFERENCE_LENGTH)
    if not target or target == current_user:
        return None
    eligible = frappe.db.get_value(
        "User",
        {
            "name": target,
            "enabled": 1,
            "user_type": "System User",
            "allowed_in_mentions": 1,
        },
        "name",
    )
    if not eligible or not _can_read_reference(thread, eligible):
        return None
    return eligible


def _add_reply_comment(reference_doc, thread, reply_text: str, current_user: str):
    reference_doc.check_permission("read")
    target = _reply_target(thread, current_user)
    safe_reply = html.escape(reply_text).replace("\n", "<br>")
    if target:
        safe_target = html.escape(target, quote=True)
        safe_label = html.escape(_user_full_name(target) or target)
        mention = (
            f'<span class="mention" data-id="{safe_target}" data-is-group="false">'
            f"@{safe_label}</span>"
        )
        content = f"<p>{mention}</p><p>{safe_reply}</p>"
    else:
        content = f"<p><strong>رد على الإشارة:</strong></p><p>{safe_reply}</p>"
    return reference_doc.add_comment("Comment", content)


def _reply(
    thread_name: str,
    reply: str,
    request_id: str,
    expected_last_event_key: str,
    *,
    close_after: bool,
) -> dict[str, Any]:
    user = _assert_authenticated()
    reply_text = _plain_required(reply, "الرد", MAX_NOTE_LENGTH)
    normalized_request_id = _logic(normalize_request_id, request_id)
    event_key = _logic(
        mention_reply_event_key,
        thread_name,
        user,
        normalized_request_id,
    )

    with _savepoint("mention_reply"):
        thread = _get_owned_thread(thread_name, for_update=True)
        reference_doc = _get_reference_doc(thread)
        expected_event_key = _assert_expected_event(thread, expected_last_event_key)
        existing = _existing_reply_event(thread, event_key)
        if existing:
            if plain_text(existing.content_plain, MAX_NOTE_LENGTH) != reply_text:
                frappe.throw(
                    "استُخدم معرّف الطلب نفسه لرد مختلف",
                    frappe.ValidationError,
                )
            comment = (
                frappe.get_doc("Comment", existing.comment)
                if existing.comment and frappe.db.exists("Comment", existing.comment)
                else None
            )
            reply_event = existing
        else:
            if thread.status == "Closed":
                frappe.throw("أعد فتح الإشارة قبل الرد عليها", frappe.ValidationError)
            comment = _add_reply_comment(
                reference_doc,
                thread,
                reply_text,
                user,
            )
            reply_event = _insert_event(
                thread,
                {
                    "event_key": event_key,
                    "event_type": "Reply",
                    "mentioned_at": now_datetime(),
                    "comment": comment.name,
                    "comment_modified": comment.modified,
                    "from_user": user,
                    "content_plain": reply_text,
                    "request_id": normalized_request_id,
                },
            )
            thread.last_replied_at = now_datetime()
            thread.last_replied_by = user
            thread.last_reply_comment = comment.name
            thread.last_seen_event_key = expected_event_key
            if close_after:
                _close_in_memory(thread, expected_event_key)
            thread.save(ignore_permissions=True)

    return {
        "mention": _serialize_thread(thread),
        "reply": _serialize_event(reply_event),
        "comment": followup_service._serialize_comment(comment) if comment else None,  # noqa: SLF001
    }


def reply_mention(
    thread_name: str,
    reply: str,
    request_id: str,
    expected_last_event_key: str,
) -> dict[str, Any]:
    return _reply(
        thread_name,
        reply,
        request_id,
        expected_last_event_key,
        close_after=False,
    )


def reply_and_close(
    thread_name: str,
    reply: str,
    request_id: str,
    expected_last_event_key: str,
) -> dict[str, Any]:
    return _reply(
        thread_name,
        reply,
        request_id,
        expected_last_event_key,
        close_after=True,
    )


def close_mention(thread_name: str, expected_last_event_key: str) -> dict[str, Any]:
    with _savepoint("mention_close"):
        thread = _get_owned_thread(thread_name, for_update=True)
        _get_reference_doc(thread)
        expected_event_key = _assert_expected_event(thread, expected_last_event_key)
        if _close_in_memory(thread, expected_event_key):
            thread.save(ignore_permissions=True)
    return {"mention": _serialize_thread(thread)}


def reopen_mention(thread_name: str, expected_last_event_key: str) -> dict[str, Any]:
    user = _assert_authenticated()
    with _savepoint("mention_reopen"):
        thread = _get_owned_thread(thread_name, for_update=True)
        _get_reference_doc(thread)
        expected_event_key = _assert_expected_event(thread, expected_last_event_key)
        if thread.status == "Open":
            return {"mention": _serialize_thread(thread)}
        if thread.status != "Closed":
            frappe.throw("لا يمكن إعادة فتح إشارة محوّلة", frappe.ValidationError)
        _append_state_event(thread, "Reopened", "أُعيد فتح الإشارة")
        thread.status = "Open"
        thread.closed_at = None
        thread.closed_by = None
        thread.last_seen_event_key = expected_event_key
        thread.save(ignore_permissions=True)
    return {"mention": _serialize_thread(thread)}


def _valid_linked_todo(thread):
    if not thread.converted_to_todo:
        return None
    todo = frappe.db.get_value(
        "ToDo",
        thread.converted_to_todo,
        [
            "name",
            "status",
            "allocated_to",
            "reference_type",
            "reference_name",
        ],
        as_dict=True,
    )
    if not todo or (
        todo.allocated_to != thread.for_user
        or todo.reference_type != thread.reference_doctype
        or todo.reference_name != thread.reference_name
    ):
        return None
    return todo


def convert_mention_to_followup(
    thread_name: str,
    due_date: str,
    priority: str = "Medium",
    description: str = "",
    *,
    expected_last_event_key: str,
) -> dict[str, Any]:
    user = _assert_authenticated()
    normalized_date = _logic(normalize_date, due_date, "تاريخ الاستحقاق")
    normalized_priority = _logic(normalize_priority, priority)

    with _savepoint("mention_convert"):
        thread = _get_owned_thread(thread_name, for_update=True)
        _get_reference_doc(thread)
        expected_event_key = _assert_expected_event(thread, expected_last_event_key)
        if thread.status == "Closed":
            frappe.throw("أعد فتح الإشارة قبل تحويلها إلى متابعة", frappe.ValidationError)

        description_text = (
            _plain_required(description, "وصف المتابعة", MAX_DESCRIPTION_LENGTH)
            if clean_text(description)
            else _required(
                plain_text(thread.latest_preview_plain, MAX_DESCRIPTION_LENGTH),
                "وصف المتابعة",
                MAX_DESCRIPTION_LENGTH,
            )
        )

        todo = _valid_linked_todo(thread) if thread.status == "Converted" else None
        if not todo:
            todo_name = frappe.db.get_value(
                "ToDo",
                {
                    "reference_type": thread.reference_doctype,
                    "reference_name": thread.reference_name,
                    "allocated_to": user,
                    "status": "Open",
                },
                "name",
                order_by="creation desc",
                for_update=True,
            )
            if todo_name:
                todo = frappe.get_doc("ToDo", todo_name)
            else:
                created = followup_service.create_followup(
                    thread.reference_doctype,
                    thread.reference_name,
                    description_text,
                    normalized_date,
                    priority=normalized_priority,
                    allocated_to=user,
                )
                todo = frappe.get_doc("ToDo", created["followup"]["name"])

            _append_state_event(thread, "Converted", "حُوّلت الإشارة إلى متابعة")
            thread.status = "Converted"
            thread.converted_to_todo = todo.name
            thread.converted_at = now_datetime()
            thread.converted_by = user
            thread.last_seen_event_key = expected_event_key
            thread.save(ignore_permissions=True)

    return {
        "mention": _serialize_thread(thread),
        "followup": followup_service._serialize_todo(todo),  # noqa: SLF001
    }
