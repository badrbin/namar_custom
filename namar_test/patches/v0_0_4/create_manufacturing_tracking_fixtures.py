from __future__ import annotations

import json
from pathlib import Path

import frappe


METADATA_KEYS = {"creation", "docstatus", "idx", "modified", "modified_by", "owner"}


def _fixture(filename: str):
    path = Path(frappe.get_app_path("namar_test")) / "fixtures" / filename
    return json.loads(path.read_text(encoding="utf-8"))


def _clean_payload(payload: dict) -> dict:
    return {key: value for key, value in payload.items() if key not in METADATA_KEYS}


def _with_existing_child_names(payload: dict, current: dict | None) -> dict:
    if not current:
        return payload

    field_names = {
        field.get("fieldname"): field.get("name")
        for field in current.get("fields", [])
        if field.get("fieldname") and field.get("name")
    }
    permission_names = {
        (permission.get("role") or "", int(permission.get("permlevel") or 0)): permission.get("name")
        for permission in current.get("permissions", [])
        if permission.get("role") and permission.get("name")
    }

    prepared = dict(payload)
    prepared["fields"] = []
    for field in payload.get("fields", []):
        row = dict(field)
        existing_name = field_names.get(row.get("fieldname"))
        if existing_name:
            row["name"] = existing_name
        prepared["fields"].append(row)

    prepared["permissions"] = []
    for permission in payload.get("permissions", []):
        row = dict(permission)
        key = (row.get("role") or "", int(row.get("permlevel") or 0))
        existing_name = permission_names.get(key)
        if existing_name:
            row["name"] = existing_name
        prepared["permissions"].append(row)

    return prepared


def _upsert_doc(doctype: str, name: str, payload: dict) -> None:
    payload = _clean_payload(dict(payload))
    payload["doctype"] = doctype
    payload["name"] = name

    if frappe.db.exists(doctype, name):
        doc = frappe.get_doc(doctype, name)
        doc.update({key: value for key, value in payload.items() if key not in {"doctype", "name"}})
        doc.save(ignore_permissions=True)
        return

    frappe.get_doc(payload).insert(ignore_permissions=True)


def _upsert_doctype(payload: dict) -> None:
    name = payload["name"]
    current = frappe.get_doc("DocType", name).as_dict() if frappe.db.exists("DocType", name) else None
    prepared = _with_existing_child_names(payload, current)
    _upsert_doc("DocType", name, prepared)
    frappe.clear_cache(doctype=name)


def _upsert_custom_field(payload: dict) -> None:
    name = payload.get("name") or f"{payload['dt']}-{payload['fieldname']}"
    prepared = dict(payload)
    prepared["name"] = name
    _upsert_doc("Custom Field", name, prepared)
    frappe.clear_cache(doctype=payload["dt"])


def execute():
    for payload in _fixture("doctype.json"):
        _upsert_doctype(payload)

    for payload in _fixture("custom_field.json"):
        _upsert_custom_field(payload)
