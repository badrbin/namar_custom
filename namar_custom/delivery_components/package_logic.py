from __future__ import annotations


TRACKING_STATUS_PENDING = "غير جاهز"
TRACKING_STATUS_READY = "جاهز"
TRACKING_STATUS_LOADED = "محمل"
TRACKING_STATUS_DELIVERED = "تم التوريد"
TRACKING_STATUS_CANCELLED = "ملغي"

TRACKING_ACTION_READY = "ready"
TRACKING_ACTION_LOAD = "load"
TRACKING_ACTION_DELIVER = "deliver"
TRACKING_ACTION_REOPEN = "reopen"

PACKAGE_EVENT_ACTION_REGISTERED = "تسجيل"


TRACKING_ROUTE_BARCODE = "تصنيع مستقل - باركود"
TRACKING_ROUTE_WITH_DOOR = "مع الباب"
TRACKING_ROUTE_DELIVERY_ONLY = "توريد فقط - بدون باركود"
TRACKING_ROUTE_EXCLUDED = "مستبعد"

TRACKING_ROUTE_ALIASES = {
    "تصنيع وتغليف": TRACKING_ROUTE_BARCODE,
    "تجهيز فقط": TRACKING_ROUTE_DELIVERY_ONLY,
    "لا يتتبع": TRACKING_ROUTE_EXCLUDED,
}


def normalize_tracking_route(
    value: str | None,
    default: str = TRACKING_ROUTE_DELIVERY_ONLY,
) -> str:
    route = (value or "").strip()
    route = TRACKING_ROUTE_ALIASES.get(route, route)
    allowed = {
        TRACKING_ROUTE_BARCODE,
        TRACKING_ROUTE_WITH_DOOR,
        TRACKING_ROUTE_DELIVERY_ONLY,
        TRACKING_ROUTE_EXCLUDED,
    }
    return route if route in allowed else default


def is_barcode_tracking_route(value: str | None) -> bool:
    return normalize_tracking_route(value) == TRACKING_ROUTE_BARCODE


COMPONENT_COLOR_NAMES = {
    "B": "أسود",
    "C": "بني",
    "CG": "بني محروق",
    "DG": "فيراني",
    "DGZ": "دارك جراي",
    "F": "فلاور",
    "G": "رصاصي",
    "H": "عسلي",
    "N": "بدون لون",
    "NS": "نيو سيلفر",
    "P": "خشبي",
    "S": "سيلفر",
    "T": "تك",
    "W": "أبيض",
    "WS": "قشر الجوز",
    "WT": "وايت تك",
    "Z": "بيج",
    "011-": "بني 011",
    "022-": "وايت تك 022",
}


def component_color_from_item_code(item_code: str | None) -> str:
    code = (item_code or "").strip()
    if not code:
        return ""
    if "-" in code:
        color_code = code.split("-", 1)[0].strip()
        if color_code in ("011", "022"):
            color_code += "-"
    else:
        prefix_chars: list[str] = []
        for char in code:
            if char.isdigit():
                break
            prefix_chars.append(char)
        color_code = "".join(prefix_chars).strip()
    if color_code in COMPONENT_COLOR_NAMES:
        return COMPONENT_COLOR_NAMES[color_code]
    return color_code if color_code and len(color_code) <= 3 else ""


def normalize_component_color(value: str | None) -> str:
    color = (value or "").strip()
    return COMPONENT_COLOR_NAMES.get(color, color)


def component_package_key(
    component: str | None,
    color: str | None,
    item_code: str | None,
    package_no: int | str | None,
) -> str:
    return "||".join(
        [
            (component or "").strip(),
            (color or "").strip(),
            (item_code or "").strip(),
            str(max(int(package_no or 0), 1)),
        ]
    )


def legacy_component_package_key(
    component: str | None,
    item_code: str | None,
    package_no: int | str | None,
) -> str:
    return "||".join(
        [
            (component or "").strip(),
            (item_code or "").strip(),
            str(max(int(package_no or 0), 1)),
        ]
    )


def clean_count(value: float | int | str | None) -> int | float:
    number = float(value or 0)
    if abs(number - int(number)) < 0.000001:
        return int(number)
    return round(number, 3)


def should_rotate_unregistered_barcodes(
    previous_source_hash: str | None,
    current_source_hash: str | None,
    *,
    has_existing_packages: bool,
) -> bool:
    return bool(
        has_existing_packages
        and (previous_source_hash or "").strip() != (current_source_hash or "").strip()
    )


def package_status(package_qty: float | int | str | None, ready_qty: float | int | str | None) -> str:
    package_qty = float(package_qty or 0)
    ready_qty = float(ready_qty or 0)
    if package_qty and ready_qty >= package_qty:
        return "جاهز"
    if ready_qty > 0:
        return "جزئي"
    return "غير جاهز"


def normalize_tracking_status(
    value: str | None,
    package_qty: float | int | str | None = None,
    ready_qty: float | int | str | None = None,
) -> str:
    status = (value or "").strip()
    allowed = {
        TRACKING_STATUS_PENDING,
        TRACKING_STATUS_READY,
        TRACKING_STATUS_LOADED,
        TRACKING_STATUS_DELIVERED,
        TRACKING_STATUS_CANCELLED,
    }
    if status == TRACKING_STATUS_PENDING and package_status(package_qty, ready_qty) == TRACKING_STATUS_READY:
        return TRACKING_STATUS_READY
    if status in allowed:
        return status
    return (
        TRACKING_STATUS_READY
        if package_status(package_qty, ready_qty) == TRACKING_STATUS_READY
        else TRACKING_STATUS_PENDING
    )


def next_tracking_status(
    current_status: str | None,
    action: str | None,
    *,
    package_is_ready: bool,
) -> str:
    current = normalize_tracking_status(current_status)
    action = (action or "").strip().lower()

    if action == TRACKING_ACTION_READY:
        if current in (TRACKING_STATUS_LOADED, TRACKING_STATUS_DELIVERED):
            return current
        return TRACKING_STATUS_READY if package_is_ready else TRACKING_STATUS_PENDING
    if action == TRACKING_ACTION_LOAD:
        if current == TRACKING_STATUS_DELIVERED:
            return current
        if current not in (TRACKING_STATUS_READY, TRACKING_STATUS_LOADED):
            raise ValueError("لا يمكن تحميل الحزمة قبل تسجيلها جاهزة")
        return TRACKING_STATUS_LOADED
    if action == TRACKING_ACTION_DELIVER:
        if current not in (TRACKING_STATUS_LOADED, TRACKING_STATUS_DELIVERED):
            raise ValueError("لا يمكن توريد الحزمة قبل تسجيل تحميلها")
        return TRACKING_STATUS_DELIVERED
    if action == TRACKING_ACTION_REOPEN:
        return TRACKING_STATUS_PENDING
    raise ValueError("إجراء حزمة المكونات غير معروف")


def build_fulfillment_readiness(
    *,
    door_total: float | int | str | None,
    door_remaining: float | int | str | None,
    package_total: float | int | str | None,
    package_ready: float | int | str | None,
    package_loaded: float | int | str | None,
    package_delivered: float | int | str | None,
    package_load_total: float | int | str | None = None,
    packages_need_sync: bool = False,
) -> dict[str, int | float | str | bool]:
    door_total_number = max(float(door_total or 0), 0)
    door_remaining_number = min(max(float(door_remaining or 0), 0), door_total_number)
    door_ready_number = max(door_total_number - door_remaining_number, 0)

    package_total_number = max(float(package_total or 0), 0)
    package_load_total_number = (
        package_total_number
        if package_load_total is None
        else min(max(float(package_load_total or 0), 0), package_total_number)
    )
    package_ready_number = min(max(float(package_ready or 0), 0), package_total_number)
    package_loaded_number = min(max(float(package_loaded or 0), 0), package_total_number)
    package_delivered_number = min(max(float(package_delivered or 0), 0), package_total_number)

    has_trackable_rows = door_total_number > 0 or package_total_number > 0
    doors_complete = door_total_number <= 0 or door_remaining_number <= 0.000001
    packages_complete = (
        not packages_need_sync
        and (package_total_number <= 0 or package_ready_number >= package_total_number)
    )
    if packages_need_sync:
        status = "غير جاهز"
    elif has_trackable_rows and doors_complete and packages_complete:
        status = "جاهز بالكامل"
    elif door_ready_number > 0 or package_ready_number > 0:
        status = "جاهزية جزئية"
    else:
        status = "غير جاهز"

    summary = "الأبواب %s/%s | الحزم %s/%s" % (
        clean_count(door_ready_number),
        clean_count(door_total_number),
        clean_count(package_ready_number),
        clean_count(package_total_number),
    )
    if packages_need_sync:
        summary += " | الحزم تحتاج تحديث"

    return {
        "status": status,
        "is_ready": status == "جاهز بالكامل",
        "door_total": clean_count(door_total_number),
        "door_ready": clean_count(door_ready_number),
        "door_remaining": clean_count(door_remaining_number),
        "package_total": clean_count(package_total_number),
        "package_ready": clean_count(package_ready_number),
        "package_loaded": clean_count(package_loaded_number),
        "package_delivered": clean_count(package_delivered_number),
        "package_load_total": clean_count(package_load_total_number),
        "packages_need_sync": bool(packages_need_sync),
        "summary": summary,
    }


def combined_manufacturing_status(readiness: dict[str, object]) -> str:
    door_total = max(float(readiness.get("door_total") or 0), 0)
    door_ready = max(float(readiness.get("door_ready") or 0), 0)
    package_total = max(float(readiness.get("package_total") or 0), 0)
    package_ready = max(float(readiness.get("package_ready") or 0), 0)

    if door_total <= 0 and package_total <= 0:
        return "غير مصنع"
    if readiness.get("is_ready"):
        return "مصنع بالكامل"
    if door_ready > 0 or package_ready > 0:
        return "قيد التصنيع"
    return "غير مصنع"


def should_block_fulfillment_for_package_sync(
    packages_need_sync: bool,
    package_total: float | int | str | None,
    has_expected_barcode_components: bool,
) -> bool:
    return bool(
        packages_need_sync
        and (float(package_total or 0) > 0 or has_expected_barcode_components)
    )


def build_package_specs(
    required_qty: float | int | str | None,
    full_pack_qty: float | int | str | None,
    full_label: str = "حزمة",
    remainder_one_label: str = "مغلف منفرد",
    remainder_multi_label: str = "كرتون ناقص",
) -> list[dict[str, int | float | str]]:
    required = float(required_qty or 0)
    full_qty = float(full_pack_qty or 0)
    if required <= 0:
        return []

    full_label = (full_label or "حزمة").strip() or "حزمة"
    remainder_one_label = (remainder_one_label or "مغلف منفرد").strip() or "مغلف منفرد"
    remainder_multi_label = (remainder_multi_label or "كرتون ناقص").strip() or "كرتون ناقص"

    if full_qty <= 0:
        return [{"package_label": full_label, "package_qty": clean_count(required)}]

    specs: list[dict[str, int | float | str]] = []
    remaining = required
    full_count = int(required // full_qty)
    for _ in range(full_count):
        specs.append({"package_label": full_label, "package_qty": clean_count(full_qty)})
        remaining -= full_qty

    if remaining > 0.000001:
        label = remainder_one_label if remaining <= 1.000001 else remainder_multi_label
        specs.append({"package_label": label, "package_qty": clean_count(remaining)})
    return specs


def package_key_number(value: str | None) -> int:
    try:
        return max(int((value or "").rsplit("||", 1)[1]), 1)
    except (IndexError, TypeError, ValueError):
        return 1


def package_row_started(row: dict | None) -> bool:
    row = row or {}
    package_qty = float(row.get("package_qty") or 0)
    ready_qty = float(row.get("ready_qty") or 0)
    status = normalize_tracking_status(row.get("tracking_status"), package_qty, ready_qty)
    return (
        ready_qty > 0.000001
        or status != TRACKING_STATUS_PENDING
        or int(row.get("tracking_revision") or 0) > 0
    )


def legacy_color_split_has_started_rows(
    colors: set[str] | list[str] | tuple[str, ...],
    rows: list[dict] | tuple[dict, ...],
) -> bool:
    distinct_colors = {(color or "").strip() for color in colors}
    return len(distinct_colors) > 1 and any(package_row_started(row) for row in rows)


def build_reconciled_package_specs(
    *,
    required_qty: float | int | str | None,
    full_pack_qty: float | int | str | None,
    full_label: str,
    remainder_one_label: str,
    remainder_multi_label: str,
    existing_rows: list[dict] | None = None,
) -> list[dict[str, int | float | str | bool]]:
    required = max(float(required_qty or 0), 0)
    started_rows = [dict(row) for row in (existing_rows or []) if package_row_started(row)]
    started_rows.sort(
        key=lambda row: (
            package_key_number(row.get("package_key")),
            row.get("package_key") or "",
        )
    )
    started_qty = sum(max(float(row.get("package_qty") or 0), 0) for row in started_rows)
    if started_qty > required + 0.000001:
        raise ValueError(
            "الكمية الحالية أقل من كمية حزم بدأ تتبعها: %s مقابل %s"
            % (clean_count(required), clean_count(started_qty))
        )

    specs: list[dict[str, int | float | str | bool]] = []
    used_numbers: set[int] = set()
    for row in started_rows:
        package_no = package_key_number(row.get("package_key"))
        while package_no in used_numbers:
            package_no += 1
        used_numbers.add(package_no)
        specs.append(
            {
                "package_key": row.get("package_key") or "",
                "package_no": package_no,
                "package_label": row.get("package_label") or full_label or "حزمة",
                "package_qty": clean_count(row.get("package_qty") or 0),
                "legacy_started": True,
            }
        )

    remaining_specs = build_package_specs(
        required_qty=max(required - started_qty, 0),
        full_pack_qty=full_pack_qty,
        full_label=full_label,
        remainder_one_label=remainder_one_label,
        remainder_multi_label=remainder_multi_label,
    )
    next_number = 1
    for row in remaining_specs:
        while next_number in used_numbers:
            next_number += 1
        used_numbers.add(next_number)
        specs.append({**row, "package_no": next_number, "legacy_started": False})
        next_number += 1

    return sorted(specs, key=lambda row: int(row.get("package_no") or 0))


def assign_stable_loading_codes(
    package_rows: list[dict],
    loading_prefix: str | None,
    fieldname: str = "loading_code",
    reserved_codes: list[str] | tuple[str, ...] | set[str] | None = None,
) -> list[dict]:
    prefix = (loading_prefix or "").strip().upper()
    if not prefix:
        return package_rows

    used = {
        (row.get(fieldname) or "").strip().upper()
        for row in package_rows
        if (row.get(fieldname) or "").strip()
    }
    used.update(
        (code or "").strip().upper()
        for code in (reserved_codes or [])
        if (code or "").strip()
    )
    next_number = 1
    for row in package_rows:
        existing = (row.get(fieldname) or "").strip().upper()
        if existing:
            row[fieldname] = existing
            continue
        while "%s-%s" % (prefix, str(next_number).zfill(2)) in used:
            next_number += 1
        code = "%s-%s" % (prefix, str(next_number).zfill(2))
        row[fieldname] = code
        used.add(code)
        next_number += 1
    return package_rows


def is_valid_loading_prefix(value: str | None) -> bool:
    text = (value or "").strip().upper()
    return len(text) == 2 and "A" <= text[0] <= "Z" and "A" <= text[1] <= "Z"


def loading_prefix_from_index(index: int | str | None) -> str:
    number = int(index or 0) % 676
    first = int(number / 26)
    second = number % 26
    return chr(65 + first) + chr(65 + second)
