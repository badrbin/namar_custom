from __future__ import annotations

import json
import math
from datetime import date, datetime, timezone
from typing import Any, Iterable, Mapping


DEFAULT_PAGE_LENGTH = 20
MAX_PAGE_LENGTH = 100
MAX_PAGE = 1000
MAX_QUANTITY = 1_000_000_000
MAX_ITEMS = 500


def clean_text(value: Any, max_length: int | None = None) -> str:
    text = "" if value is None else str(value).strip()
    if max_length is not None:
        text = text[:max_length]
    return text


def parse_mapping(value: Any, field_label: str = "البيانات") -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field_label} ليست JSON صحيحة") from exc
        if isinstance(parsed, Mapping):
            return dict(parsed)
    raise ValueError(f"{field_label} يجب أن تكون كائنًا")


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


def page_window(page: Any, page_length: Any) -> tuple[int, int, int, int]:
    safe_page = parse_positive_int(page, 1, MAX_PAGE)
    safe_length = parse_positive_int(page_length, DEFAULT_PAGE_LENGTH, MAX_PAGE_LENGTH)
    return safe_page, safe_length, (safe_page - 1) * safe_length, safe_length + 1


def canonical_timestamp(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        parsed = value
    else:
        text = clean_text(value)
        if not text:
            return ""
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return text.replace("T", " ")

    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc)
        return parsed.isoformat(sep=" ", timespec="microseconds")
    return parsed.isoformat(sep=" ", timespec="microseconds")


def timestamps_match(expected: Any, actual: Any) -> bool:
    expected_timestamp = canonical_timestamp(expected)
    return bool(expected_timestamp) and expected_timestamp == canonical_timestamp(actual)


def date_not_before(value: Any, minimum: Any) -> str:
    minimum_date = date.fromisoformat(clean_text(minimum, 10))
    try:
        candidate = date.fromisoformat(clean_text(value, 10))
    except ValueError:
        candidate = minimum_date
    return max(candidate, minimum_date).isoformat()


def source_link_is_allowed(actual: Any, incoming: Any, *, existing_row: bool) -> bool:
    actual_link = clean_text(actual, 140)
    incoming_link = clean_text(incoming, 140)
    if not existing_row:
        return not incoming_link
    return not incoming_link or incoming_link == actual_link


def sanitize_fields(payload: Mapping[str, Any], allowed_fields: Iterable[str]) -> dict[str, Any]:
    allowed = set(allowed_fields)
    return {fieldname: value for fieldname, value in payload.items() if fieldname in allowed}


def normalize_item_payloads(
    value: Any,
    allowed_fields: Iterable[str],
    existing_names: Iterable[str] = (),
    allow_existing_names: bool = False,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("بنود الطلب يجب أن تكون قائمة")
    if not value:
        raise ValueError("يجب إضافة صنف واحد على الأقل")
    if len(value) > MAX_ITEMS:
        raise ValueError(f"الحد الأعلى لبنود الطلب هو {MAX_ITEMS}")

    allowed = set(allowed_fields)
    known_names = set(existing_names)
    used_names: set[str] = set()
    rows: list[dict[str, Any]] = []

    for index, raw_row in enumerate(value, start=1):
        if not isinstance(raw_row, Mapping):
            raise ValueError(f"بند الطلب رقم {index} غير صحيح")

        row = sanitize_fields(raw_row, allowed)
        item_code = clean_text(row.get("item_code"), 140)
        if not item_code:
            raise ValueError(f"الصنف مطلوب في البند رقم {index}")
        row["item_code"] = item_code

        try:
            quantity = float(row.get("qty"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"الكمية غير صحيحة في البند رقم {index}") from exc
        if not math.isfinite(quantity) or quantity <= 0:
            raise ValueError(
                f"الكمية يجب أن تكون أكبر من صفر في البند رقم {index}"
            )
        if quantity > MAX_QUANTITY:
            raise ValueError(f"الكمية تتجاوز الحد الأعلى في البند رقم {index}")
        row["qty"] = quantity

        required_text_fields = (
            ("uom", "وحدة القياس", 140),
            ("warehouse", "المستودع", 140),
            ("schedule_date", "تاريخ الاحتياج", 10),
        )
        for fieldname, label, max_length in required_text_fields:
            field_value = clean_text(row.get(fieldname), max_length)
            if not field_value:
                raise ValueError(f"{label} مطلوب في البند رقم {index}")
            row[fieldname] = field_value

        row_name = clean_text(raw_row.get("name"), 140)
        if row_name and allow_existing_names:
            if row_name not in known_names:
                raise ValueError(f"بند الطلب رقم {index} لا يتبع هذا الطلب")
            if row_name in used_names:
                raise ValueError(f"بند الطلب رقم {index} مكرر")
            row["name"] = row_name
            used_names.add(row_name)

        source_row = clean_text(raw_row.get("sales_order_item"), 140)
        if source_row:
            row["sales_order_item"] = source_row
        rows.append(row)

    return rows


def role_can_edit(allow_edit: Any, roles: Iterable[str], is_administrator: bool = False) -> bool:
    if is_administrator:
        return True
    allowed_role = clean_text(allow_edit)
    if not allowed_role:
        return True
    return allowed_role in set(roles)
