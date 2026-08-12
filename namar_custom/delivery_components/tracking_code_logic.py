from __future__ import annotations

import re


# Avoid I, L, O, 0 and 1 so printed codes remain easy to read.
TRACKING_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
MIN_TRACKING_WIDTH = 3
LEGACY_CODE_RE = re.compile(r"^[A-Z]{2}$")
PACKAGE_NUMBER_RE = re.compile(r"^([A-Z2-9]{2,})-(\d+)$")


def normalize_tracking_code(value: str | None) -> str:
    return "".join((value or "").strip().upper().split())


def tracking_code_from_sequence(sequence: int | str | None) -> str:
    number = int(sequence or 0)
    if number < 1:
        raise ValueError("رقم تسلسل رمز التتبع يجب أن يكون أكبر من صفر")

    base = len(TRACKING_ALPHABET)
    value = number - 1
    width = MIN_TRACKING_WIDTH
    while value >= base**width:
        width += 1

    chars = [TRACKING_ALPHABET[0]] * width
    for index in range(width - 1, -1, -1):
        value, remainder = divmod(value, base)
        chars[index] = TRACKING_ALPHABET[remainder]
    return "".join(chars)


def is_valid_request_tracking_code(value: str | None) -> bool:
    code = normalize_tracking_code(value)
    if LEGACY_CODE_RE.fullmatch(code):
        return True
    return len(code) >= MIN_TRACKING_WIDTH and all(char in TRACKING_ALPHABET for char in code)


def package_tracking_code(request_code: str | None, package_no: int | str | None) -> str:
    code = normalize_tracking_code(request_code)
    number = int(package_no or 0)
    if not is_valid_request_tracking_code(code) or number < 1:
        return ""
    return "%s-%s" % (code, str(number).zfill(2))


def split_package_tracking_code(value: str | None) -> tuple[str, int] | None:
    code = normalize_tracking_code(value)
    match = PACKAGE_NUMBER_RE.fullmatch(code)
    if not match or not is_valid_request_tracking_code(match.group(1)):
        return None
    number = int(match.group(2) or 0)
    if number < 1:
        return None
    return match.group(1), number
