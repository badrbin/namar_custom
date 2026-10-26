"""Read-only calculation plus an atomic, test-site-only activation switch."""
from __future__ import annotations

import hashlib

import frappe
from frappe.utils import cint, now_datetime

from demo.cutting import calculate

TEST_SITE = "testnamar.u.frappe.cloud"
CLIENT_NAME = "حساب التخصيم"
SERVER_NAME = "get_cutting_values_bulk_exclusions_test"
CLIENT_SHA256 = "74f5aa26d9436e9449a66cecbbe6511d34cf15affe7ec0b36954bded97117a0f"
SERVER_SHA256 = "9f7f571725f0626f3df3272133c47d59982974ac1648a2271d959ffcd39ec642"


def _require_test_site():
    if frappe.local.site != TEST_SITE:
        frappe.throw("تطبيق Demo مخصص لموقع التجربة المحدد فقط", frappe.PermissionError)


def is_enabled():
    return frappe.local.site == TEST_SITE and bool(cint(frappe.db.get_single_value("Demo Settings", "enabled")))


def _require_calculation_access():
    _require_test_site()
    if not (frappe.has_permission("Material Request", "read") or frappe.has_permission("Material Request", "create")):
        frappe.throw("ليس لديك صلاحية حساب التخصيم لطلبات المواد", frappe.PermissionError)


@frappe.whitelist(methods=["GET", "POST"])
def calculate_cutting(**kwargs):
    _require_calculation_access()
    if not is_enabled():
        frappe.only_for("System Manager")
    return calculate(kwargs)


@frappe.whitelist(methods=["GET", "POST"])
def legacy_cutting_values(**kwargs):
    _require_test_site()
    if is_enabled():
        _require_calculation_access()
        return calculate(kwargs)
    original = frappe.get_doc("Server Script", SERVER_NAME)
    if original.disabled:
        frappe.throw("الحساب السابق معطل وDemo غير مفعل؛ راجع حالة التفعيل")
    original.execute_method()
    return frappe.response.get("message")


def _digest(document):
    return hashlib.sha256((document.script or "").encode()).hexdigest()


def _load_originals():
    # Lock in a fixed order. Re-read hashes after acquiring locks so a concurrent
    # edit cannot be hidden by the activation switch.
    frappe.db.sql("SELECT name FROM `tabClient Script` WHERE name=%s FOR UPDATE", CLIENT_NAME)
    frappe.db.sql("SELECT name FROM `tabServer Script` WHERE name=%s FOR UPDATE", SERVER_NAME)
    client = frappe.get_doc("Client Script", CLIENT_NAME)
    server = frappe.get_doc("Server Script", SERVER_NAME)
    if _digest(client) != CLIENT_SHA256 or _digest(server) != SERVER_SHA256:
        frappe.throw("تغير أحد السكربتات منذ نسخة المقارنة؛ أوقف التبديل وراجع الاختلاف")
    return client, server


@frappe.whitelist(methods=["GET"])
def migration_status():
    _require_test_site()
    frappe.only_for("System Manager")
    client = frappe.get_doc("Client Script", CLIENT_NAME)
    server = frappe.get_doc("Server Script", SERVER_NAME)
    return {
        "site": frappe.local.site,
        "enabled": is_enabled(),
        "legacy_client_enabled": cint(client.enabled),
        "legacy_server_disabled": cint(server.disabled),
        "client_hash_matches": _digest(client) == CLIENT_SHA256,
        "server_hash_matches": _digest(server) == SERVER_SHA256,
        "calculator": "demo.api.calculate_cutting",
    }


@frappe.whitelist(methods=["POST"])
def activate():
    _require_test_site()
    frappe.only_for("System Manager")
    frappe.only_for("Script Manager")
    client, server = _load_originals()
    if is_enabled():
        if cint(client.enabled) or not cint(server.disabled):
            frappe.throw("حالة التفعيل غير متسقة؛ راجعها قبل أي تغيير")
        return migration_status()
    if not cint(client.enabled) or cint(server.disabled):
        frappe.throw("حالة السكربتات السابقة تختلف عن الحالة المعتمدة")
    client.enabled = 0
    client.save()
    server.disabled = 1
    server.save()
    settings = frappe.get_single("Demo Settings")
    settings.enabled = 1
    settings.activated_on = now_datetime()
    settings.client_sha256 = CLIENT_SHA256
    settings.server_sha256 = SERVER_SHA256
    settings.save(ignore_permissions=True)
    frappe.clear_cache(doctype="Material Request")
    # Framework POST transaction commits all three documents together.
    return migration_status()


@frappe.whitelist(methods=["POST"])
def deactivate():
    _require_test_site()
    frappe.only_for("System Manager")
    frappe.only_for("Script Manager")
    client, server = _load_originals()
    if not is_enabled():
        if not cint(client.enabled) or cint(server.disabled):
            frappe.throw("حالة الرجوع غير متسقة؛ راجعها قبل أي تغيير")
        return migration_status()
    if cint(client.enabled) or not cint(server.disabled):
        frappe.throw("حالة السكربتات تختلف عن التفعيل المعتمد")
    client.enabled = 1
    client.save()
    server.disabled = 0
    server.save()
    settings = frappe.get_single("Demo Settings")
    settings.enabled = 0
    settings.save(ignore_permissions=True)
    frappe.clear_cache(doctype="Material Request")
    return migration_status()
