from __future__ import annotations


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

