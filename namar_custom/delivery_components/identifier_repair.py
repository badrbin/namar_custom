from __future__ import annotations

from collections import defaultdict
from typing import Any

from namar_custom.delivery_components.tracking_code_logic import (
    is_valid_request_tracking_code,
    normalize_tracking_code,
    package_tracking_code,
    split_package_tracking_code,
)


def _row_key(row: dict[str, Any]) -> tuple[int, str]:
    return int(row.get("idx") or 0), str(row.get("name") or "")


def plan_package_loading_code_repairs(
    package_rows: list[dict[str, Any]],
    request_code: str | None,
) -> list[dict[str, str]]:
    prefix = normalize_tracking_code(request_code)
    if not is_valid_request_tracking_code(prefix):
        raise ValueError("رمز تتبع طلب المواد غير صالح")

    rows = [dict(row) for row in sorted(package_rows, key=_row_key)]
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    mutable_names: set[str] = set()

    for row in rows:
        code = normalize_tracking_code(row.get("loading_code"))
        row["loading_code"] = code
        if not code:
            if row.get("started"):
                raise ValueError("حزمة مسجلة بلا رمز: %s" % (row.get("name") or ""))
            mutable_names.add(str(row.get("name") or ""))
            continue
        parsed = split_package_tracking_code(code)
        if not parsed or parsed[0] != prefix:
            raise ValueError("رمز حزمة غير صالح: %s" % code)
        groups[code].append(row)

    for code, duplicates in groups.items():
        if len(duplicates) < 2:
            continue
        protected = [
            row
            for row in duplicates
            if row.get("started") or not bool(int(row.get("active") or 0))
        ]
        mutable = [row for row in duplicates if row not in protected]
        if len(protected) > 1 or not mutable:
            raise ValueError("رمز مكرر بين حزم محمية: %s" % code)
        if not protected:
            mutable = mutable[1:]
        mutable_names.update(str(row.get("name") or "") for row in mutable)

    used_codes = {
        row.get("loading_code")
        for row in rows
        if row.get("loading_code")
        and str(row.get("name") or "") not in mutable_names
    }
    next_number = 1
    updates: list[dict[str, str]] = []
    for row in rows:
        name = str(row.get("name") or "")
        if name not in mutable_names:
            continue
        candidate = package_tracking_code(prefix, next_number)
        while candidate in used_codes:
            next_number += 1
            candidate = package_tracking_code(prefix, next_number)
        updates.append(
            {
                "name": name,
                "old_loading_code": row.get("loading_code") or "",
                "loading_code": candidate,
            }
        )
        used_codes.add(candidate)
        next_number += 1
    return updates
