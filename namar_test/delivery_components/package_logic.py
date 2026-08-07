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


def clean_count(value: float | int | str | None) -> int | float:
    number = float(value or 0)
    if abs(number - int(number)) < 0.000001:
        return int(number)
    return round(number, 3)


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
    packages_loaded = packages_complete and (
        package_load_total_number <= 0 or package_loaded_number >= package_load_total_number
    )
    packages_delivered = packages_loaded and (
        package_load_total_number <= 0 or package_delivered_number >= package_load_total_number
    )

    if packages_need_sync:
        status = "غير جاهز"
    elif has_trackable_rows and doors_complete and packages_delivered and package_load_total_number > 0:
        status = "تم التوريد بالكامل"
    elif has_trackable_rows and doors_complete and packages_loaded and package_load_total_number > 0:
        status = "تم التحميل بالكامل"
    elif has_trackable_rows and doors_complete and packages_complete:
        status = "جاهز بالكامل"
    elif door_ready_number > 0 or package_ready_number > 0:
        status = "جاهزية جزئية"
    else:
        status = "غير جاهز"

    summary = "الأبواب %s/%s | الحزم %s/%s | محمل %s/%s | مورد %s/%s" % (
        clean_count(door_ready_number),
        clean_count(door_total_number),
        clean_count(package_ready_number),
        clean_count(package_total_number),
        clean_count(package_loaded_number),
        clean_count(package_load_total_number),
        clean_count(package_delivered_number),
        clean_count(package_load_total_number),
    )
    if packages_need_sync:
        summary += " | الحزم تحتاج تحديث"

    return {
        "status": status,
        "is_ready": status in ("جاهز بالكامل", "تم التحميل بالكامل", "تم التوريد بالكامل"),
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


def is_valid_loading_prefix(value: str | None) -> bool:
    text = (value or "").strip().upper()
    return len(text) == 2 and "A" <= text[0] <= "Z" and "A" <= text[1] <= "Z"


def loading_prefix_from_index(index: int | str | None) -> str:
    number = int(index or 0) % 676
    first = int(number / 26)
    second = number % 26
    return chr(65 + first) + chr(65 + second)
