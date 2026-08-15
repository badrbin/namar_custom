from __future__ import annotations

import html
import json
import re
from datetime import date, datetime
from typing import Any, Mapping


DEFAULT_PAGE_LENGTH = 50
MAX_PAGE_LENGTH = 100
MAX_LIMIT_START = 1_000_000

FOLLOWUP_BUCKETS = ("all", "open", "overdue", "today", "upcoming", "recent")
PRIORITIES = ("High", "Medium", "Low")

MAX_SEARCH_LENGTH = 140
MAX_DESCRIPTION_LENGTH = 4_000
MAX_RESULT_LENGTH = 4_000
MAX_NOTE_LENGTH = 4_000
MAX_REFERENCE_LENGTH = 140

HTML_TAG_PATTERN = re.compile(r"<[^>]*>")


def clean_text(value: Any, max_length: int | None = None) -> str:
    text = "" if value is None else str(value).strip()
    if max_length is not None:
        text = text[:max_length]
    return text


def require_text(value: Any, label: str, max_length: int) -> str:
    text = clean_text(value)
    if not text:
        raise ValueError(f"{label} مطلوب")
    if len(text) > max_length:
        raise ValueError(f"{label} يتجاوز الحد الأعلى وهو {max_length} حرفًا")
    return text


def parse_non_negative_int(value: Any, default: int = 0, maximum: int | None = None) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    if number < 0:
        number = default
    if maximum is not None:
        number = min(number, maximum)
    return number


def parse_positive_int(value: Any, default: int, maximum: int | None = None) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    if number < 1:
        number = default
    if maximum is not None:
        number = min(number, maximum)
    return number


def page_window(limit_start: Any, page_length: Any) -> tuple[int, int, int]:
    start = parse_non_negative_int(limit_start, 0, MAX_LIMIT_START)
    length = parse_positive_int(page_length, DEFAULT_PAGE_LENGTH, MAX_PAGE_LENGTH)
    return start, length, length + 1


def normalize_bucket(value: Any) -> str:
    bucket = clean_text(value, 20).lower() or "all"
    if bucket not in FOLLOWUP_BUCKETS:
        raise ValueError("تبويب المتابعات غير صحيح")
    return bucket


def normalize_priority(value: Any, default: str = "Medium") -> str:
    priority = clean_text(value, 20) or default
    if priority not in PRIORITIES:
        raise ValueError("الأولوية يجب أن تكون High أو Medium أو Low")
    return priority


def normalize_priority_filter(value: Any) -> str:
    priority = clean_text(value, 20)
    if not priority:
        return ""
    return normalize_priority(priority)


def normalize_date(value: Any, label: str = "التاريخ") -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()

    text = clean_text(value)
    try:
        return date.fromisoformat(text).isoformat()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} يجب أن يكون بتاريخ صحيح بصيغة YYYY-MM-DD") from exc


def normalize_search(value: Any) -> str:
    return clean_text(value, MAX_SEARCH_LENGTH)


def plain_text(value: Any, max_length: int | None = None) -> str:
    text = "" if value is None else str(value)
    text = HTML_TAG_PATTERN.sub(" ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_length] if max_length is not None else text


def todo_filters(
    bucket: Any,
    user: Any,
    today: Any,
    priority: Any = "",
) -> dict[str, Any]:
    normalized_bucket = normalize_bucket(bucket)
    allocated_to = require_text(user, "المستخدم", MAX_REFERENCE_LENGTH)
    current_date = normalize_date(today, "تاريخ اليوم")

    filters: dict[str, Any] = {"allocated_to": allocated_to}
    normalized_priority = normalize_priority_filter(priority)
    if normalized_priority:
        filters["priority"] = normalized_priority
    if normalized_bucket == "recent":
        filters["status"] = "Closed"
        return filters

    filters["status"] = "Open"
    if normalized_bucket == "overdue":
        filters["date"] = ["<", current_date]
    elif normalized_bucket == "today":
        filters["date"] = current_date
    elif normalized_bucket == "upcoming":
        filters["date"] = [">", current_date]
    return filters


def classify_followup(status: Any, due_date: Any, today: Any) -> str:
    normalized_status = clean_text(status, 20)
    if normalized_status == "Closed":
        return "recent"
    if normalized_status != "Open":
        return "other"

    if not clean_text(due_date):
        return "open"
    due = normalize_date(due_date, "تاريخ الاستحقاق")
    current_date = normalize_date(today, "تاريخ اليوم")
    if due < current_date:
        return "overdue"
    if due == current_date:
        return "today"
    return "upcoming"


def validate_owned_todo(
    todo: Mapping[str, Any],
    current_user: Any,
    *,
    require_open: bool,
) -> None:
    user = require_text(current_user, "المستخدم", MAX_REFERENCE_LENGTH)
    if clean_text(todo.get("allocated_to"), MAX_REFERENCE_LENGTH) != user:
        raise PermissionError("لا يمكنك إدارة متابعة مخصصة لمستخدم آخر")
    if require_open and clean_text(todo.get("status"), 20) != "Open":
        raise ValueError("هذه المتابعة ليست مفتوحة")


def timeline_target(todo: Mapping[str, Any]) -> tuple[str, str]:
    reference_type = clean_text(todo.get("reference_type"), MAX_REFERENCE_LENGTH)
    reference_name = clean_text(todo.get("reference_name"), MAX_REFERENCE_LENGTH)
    if reference_type and reference_name:
        return reference_type, reference_name
    return "ToDo", require_text(todo.get("name"), "اسم المتابعة", MAX_REFERENCE_LENGTH)


def exact_close_args(todo: Mapping[str, Any]) -> dict[str, Any]:
    todo_name = require_text(todo.get("name"), "اسم المتابعة", MAX_REFERENCE_LENGTH)
    reference_type = require_text(
        todo.get("reference_type"),
        "نوع المستند المرجعي",
        MAX_REFERENCE_LENGTH,
    )
    reference_name = require_text(
        todo.get("reference_name"),
        "المستند المرجعي",
        MAX_REFERENCE_LENGTH,
    )
    allocated_to = require_text(
        todo.get("allocated_to"),
        "المستخدم المكلف",
        MAX_REFERENCE_LENGTH,
    )
    return {
        "doctype": reference_type,
        "name": reference_name,
        "todo": todo_name,
        "assign_to": allocated_to,
        "status": "Closed",
        "ignore_permissions": False,
    }


def assignment_args(
    *,
    reference_type: Any,
    reference_name: Any,
    description: Any,
    due_date: Any,
    priority: Any,
    allocated_to: Any,
    assigned_by: Any,
) -> dict[str, Any]:
    assignee = require_text(allocated_to, "المستخدم المكلف", MAX_REFERENCE_LENGTH)
    return {
        "doctype": require_text(
            reference_type,
            "نوع المستند المرجعي",
            MAX_REFERENCE_LENGTH,
        ),
        "name": require_text(reference_name, "المستند المرجعي", MAX_REFERENCE_LENGTH),
        "description": require_text(description, "وصف المتابعة", MAX_DESCRIPTION_LENGTH),
        "date": normalize_date(due_date, "تاريخ الاستحقاق"),
        "priority": normalize_priority(priority),
        "assign_to": json.dumps([assignee], ensure_ascii=False),
        "assigned_by": require_text(assigned_by, "منشئ المتابعة", MAX_REFERENCE_LENGTH),
    }


def timeline_comment(label: Any, value: Any, max_length: int) -> str:
    safe_label = html.escape(require_text(label, "عنوان التعليق", 140))
    safe_value = html.escape(require_text(value, safe_label, max_length)).replace("\n", "<br>")
    return f"<p><strong>{safe_label}:</strong></p><p>{safe_value}</p>"


def pagination(items: list[Any], limit_start: int, page_length: int) -> dict[str, Any]:
    has_more = len(items) > page_length
    visible_items = items[:page_length]
    return {
        "items": visible_items,
        "limit_start": limit_start,
        "page_length": page_length,
        "has_more": has_more,
        "next_start": limit_start + len(visible_items) if has_more else None,
    }
