"""Atomic TEST-only switch, retaining the original scripts and report for rollback."""
from __future__ import annotations

import hashlib
import importlib
import json
from functools import lru_cache
from pathlib import Path

import frappe
from frappe.utils import cint, now_datetime

TEST_SITE = "testnamar.u.frappe.cloud"
SETTINGS = "Namar KDT Settings"
REPORT = "نتائج التخصيم"
MODULE = "Namar KDT"


@lru_cache(maxsize=2)
def _reference(name="migration"):
    return json.loads((Path(__file__).parent / "reference" / (name + ".json")).read_text())


def _digest(value):
    return hashlib.sha256((value or "").encode()).hexdigest()


def _require_admin():
    if frappe.local.site != TEST_SITE:
        frappe.throw("Namar KDT مخصص لموقع التجربة المعتمد فقط", frappe.PermissionError)
    frappe.only_for("System Manager")


def is_enabled():
    return (frappe.local.site == TEST_SITE
            and bool(frappe.db.exists("DocType", SETTINGS))
            and bool(cint(frappe.db.get_single_value(SETTINGS, "enabled"))))


def _records(lock=False):
    records = []
    for reference in sorted(_reference()["records"], key=lambda row: (row["doctype"], row["name"])):
        if lock:
            # Table identifiers are constants from the packaged manifest, never request input.
            frappe.db.sql("SELECT name FROM `tab%s` WHERE name=%%s FOR UPDATE" % reference["doctype"],
                          (reference["name"],))
        records.append((reference, frappe.get_doc(reference["doctype"], reference["name"])))
    return records


def _expected_state(reference, enabled):
    state = dict(reference["state"])
    if enabled:
        if reference["doctype"] == "Client Script":
            state["enabled"] = 0
        elif reference["doctype"] == "Server Script":
            state["disabled"] = 1
        else:
            state.update(module=MODULE, is_standard="Yes")
    return state


def _record_status(reference, doc, enabled):
    expected = _expected_state(reference, enabled)
    return {
        "doctype": reference["doctype"], "name": reference["name"],
        "hashes_match": all(_digest(doc.get(field)) == digest for field, digest in reference["hashes"].items()),
        "state_matches": all(doc.get(field) == value for field, value in expected.items()),
    }


def _schema_errors():
    schema = _reference("schema")
    errors = []
    field_keys = ("fieldname", "fieldtype", "options", "reqd", "unique", "read_only", "default")
    permission_keys = ("role", "permlevel", "read", "write", "create", "delete", "submit", "cancel", "amend", "if_owner")
    for expected in schema["doctypes"]:
        name = expected["name"]
        if not frappe.db.exists("DocType", name):
            errors.append(name + ": missing")
            continue
        doc = frappe.get_doc("DocType", name)
        if any(doc.get(key) != value for key, value in expected.items()):
            errors.append(name + ": definition changed")
        fields = {row.fieldname: row for row in doc.fields}
        expected_fields = [row for row in schema["fields"] if row["parent"] == name]
        if set(fields) != {row["fieldname"] for row in expected_fields}:
            errors.append(name + ": fields changed")
        for row in expected_fields:
            actual = fields.get(row["fieldname"])
            if actual is None or any(actual.get(key) != row.get(key) for key in field_keys):
                errors.append(name + ": " + row["fieldname"])
        wanted = sorted([tuple(row.get(key) for key in permission_keys)
                         for row in schema["permissions"] if row["parent"] == name], key=str)
        actual = sorted([tuple(row.get(key) for key in permission_keys) for row in doc.permissions], key=str)
        if wanted != actual:
            errors.append(name + ": permissions changed")
    return errors


@frappe.whitelist(methods=["GET"])
def migration_status():
    _require_admin()
    enabled = is_enabled()
    return {"site": frappe.local.site, "enabled": enabled,
            "records": [_record_status(reference, doc, enabled) for reference, doc in _records()],
            "schema_errors": _schema_errors(),
            "endpoint": "namar_kdt.api.call", "report": REPORT}


def _switch(enabled):
    _require_admin()
    frappe.only_for("Script Manager")
    records = _records(lock=True)
    current = is_enabled()
    states = [_record_status(reference, doc, current) for reference, doc in records]
    if any(not state["hashes_match"] or not state["state_matches"] for state in states):
        frappe.throw("تغيرت النسخة المرجعية أو حالة السكربتات والتقرير؛ أوقف التبديل وراجع المقارنة")
    errors = _schema_errors()
    if errors:
        frappe.throw("تغيرت بنية سجلات القص؛ راجع المقارنة قبل التبديل: " + ", ".join(errors))
    if current == enabled:
        return migration_status()
    for reference, doc in records:
        if reference["doctype"] == "Client Script":
            doc.enabled = 0 if enabled else reference["state"]["enabled"]
            doc.save()
        elif reference["doctype"] == "Server Script":
            doc.disabled = 1 if enabled else reference["state"]["disabled"]
            doc.save()
        else:
            # This is an explicit schema migration, not a Report editor save:
            # Report.validate forbids changing a standard report back to custom.
            # Preserve scripts, letterhead, roles, filters and all other fields.
            frappe.db.set_value("Report", REPORT, {
                "module": MODULE if enabled else reference["state"]["module"],
                "is_standard": "Yes" if enabled else reference["state"]["is_standard"],
            })
    settings = frappe.get_single(SETTINGS)
    settings.enabled = int(enabled)
    if enabled:
        settings.activated_on = now_datetime()
        settings.reference_sha256 = _digest(json.dumps(_reference(), sort_keys=True, ensure_ascii=False))
    settings.save(ignore_permissions=True)
    frappe.clear_cache(doctype="KDT Cutting Desk")
    frappe.clear_cache(doctype="KDT Cutting Batch")
    frappe.clear_cache(doctype="Report")
    frappe.clear_document_cache("Report", REPORT)
    # No commit: the framework POST transaction switches all records atomically.
    return migration_status()


@frappe.whitelist(methods=["POST"])
def activate():
    return _switch(True)


@frappe.whitelist(methods=["POST"])
def deactivate():
    return _switch(False)


@frappe.whitelist(methods=["GET"])
def compare_report(filters=None):
    """Compare retained original code with native code without changing report metadata."""
    _require_admin()
    doc = frappe.get_doc("Report", REPORT)
    doc.check_permission("read")
    if not doc.is_permitted() or not frappe.has_permission("Material Request", "read"):
        frappe.throw("ليس لديك صلاحية قراءة التقرير", frappe.PermissionError)
    filters = json.loads(filters) if isinstance(filters, str) else (filters or {})
    if not isinstance(filters, dict):
        frappe.throw("فلاتر التقرير غير صالحة")
    reference = next(row for row in _reference()["records"] if row["doctype"] == "Report")
    if any(_digest(doc.get(field)) != digest for field, digest in reference["hashes"].items()):
        frappe.throw("تغير التقرير المرجعي؛ أوقف المقارنة")
    legacy = doc.execute_script(frappe._dict(filters))
    native = importlib.import_module("namar_kdt.namar_kdt.report.نتائج_التخصيم.نتائج_التخصيم").execute(frappe._dict(filters))
    def digest_result(value):
        return _digest(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str))
    legacy_hash, native_hash = digest_result(legacy), digest_result(native)
    return {"equal": legacy_hash == native_hash, "legacy_sha256": legacy_hash,
            "native_sha256": native_hash, "rows": len(native[1]), "columns": len(native[0])}


def before_uninstall():
    if is_enabled():
        frappe.throw("أعد تفعيل المسار السابق عبر deactivate قبل إزالة Namar KDT")
    if frappe.db.exists("Report", REPORT):
        doc = frappe.get_doc("Report", REPORT)
        reference = next(row for row in _reference()["records"] if row["doctype"] == "Report")
        if any(doc.get(field) != reference["state"][field] for field in ("module", "is_standard")):
            frappe.throw("أعد ملكية التقرير إلى النسخة السابقة قبل إزالة التطبيق لحماية التقرير")
