"""Authenticated TEST endpoints for the existing KDT cutting workflow."""
from __future__ import annotations

import frappe

from namar_kdt import engine

TEST_SITE = "testnamar.u.frappe.cloud"
LEGACY_SCRIPT = "kdt_cutting_desk_api"
READ_ACTIONS = frozenset({
    "search", "preview", "orders", "order", "history", "report_status",
    "component_preview", "print_data", "component_print_data",
})


def _require_test_site():
    if frappe.local.site != TEST_SITE:
        frappe.throw("تطبيق Namar KDT مخصص لموقع التجربة المحدد فقط", frappe.PermissionError)


def _action(value):
    return str(value or "search").strip()


def _require_post_for_write(action):
    # Frappe validates the whitelist's HTTP methods; action-specific checking
    # additionally prevents a read URL from approving, changing or cancelling.
    if action not in READ_ACTIONS:
        request = getattr(frappe.local, "request", None)
        if getattr(request, "method", "").upper() != "POST":
            frappe.throw("تعديل أوامر القص يتطلب طلب POST", frappe.PermissionError)


def _enabled():
    # Imported lazily: migration owns the atomic activation/rollback switch.
    from namar_kdt.migration import is_enabled

    return is_enabled()


@frappe.whitelist(methods=["GET", "POST"])
def call(action="search", payload=None):
    """Native service; inactive trials permit administrator reads only."""
    _require_test_site()
    action = _action(action)
    _require_post_for_write(action)
    if not _enabled():
        frappe.only_for("System Manager")
        if action not in READ_ACTIONS:
            frappe.throw("فعّل Namar KDT قبل تغيير أوامر القص", frappe.PermissionError)
    return engine.dispatch(action, payload)


@frappe.whitelist(methods=["GET", "POST"])
def legacy_call(action="search", payload=None):
    """Keep existing callers working on either side of the activation switch."""
    _require_test_site()
    action = _action(action)
    _require_post_for_write(action)
    if _enabled():
        return engine.dispatch(action, payload)
    original = frappe.get_doc("Server Script", LEGACY_SCRIPT)
    if original.disabled:
        frappe.throw("السكربت السابق معطل وNamar KDT غير مفعل؛ راجع حالة التفعيل")
    original.execute_method()
    return frappe.response.get("message")
