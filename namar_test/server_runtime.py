from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

import frappe


def _legacy_dir(kind: str):
    return files("namar_test.legacy_scripts") / kind


def _load_manifest(kind: str) -> list[dict[str, Any]]:
    manifest_name = f"{kind}_manifest.json"
    return json.loads((files("namar_test.legacy_scripts") / manifest_name).read_text(encoding="utf-8"))


_SERVER_BY_NAME: dict[str, dict[str, Any]] | None = None


def _server_entry(script_name: str) -> dict[str, Any]:
    global _SERVER_BY_NAME
    if _SERVER_BY_NAME is None:
        _SERVER_BY_NAME = {entry["name"]: entry for entry in _load_manifest("server_scripts")}
    try:
        return _SERVER_BY_NAME[script_name]
    except KeyError as exc:
        raise frappe.DoesNotExistError(f"Migrated Server Script not found: {script_name}") from exc


def _script_code(script_name: str) -> str:
    entry = _server_entry(script_name)
    return (_legacy_dir("server_scripts") / entry["code_file"]).read_text(encoding="utf-8")


def _is_legacy_server_script_enabled(script_name: str) -> bool:
    try:
        return bool(frappe.db.get_value("Server Script", script_name, "disabled") == 0)
    except Exception:
        return False


def _namespace(doc=None, method=None) -> dict[str, Any]:
    namespace: dict[str, Any] = {
        "frappe": frappe,
        "_": frappe._,
        "json": json,
    }
    if doc is not None:
        namespace["doc"] = doc
    if method is not None:
        namespace["method"] = method
    return namespace


def _ensure_form_dict(kwargs: dict[str, Any] | None) -> None:
    if not hasattr(frappe.local, "form_dict") or frappe.local.form_dict is None:
        frappe.local.form_dict = frappe._dict()
    if kwargs:
        frappe.local.form_dict.update(kwargs)


def _execute(script_name: str, *, doc=None, method=None, kwargs: dict[str, Any] | None = None) -> dict[str, Any]:
    code = _script_code(script_name)
    _ensure_form_dict(kwargs)
    namespace = _namespace(doc=doc, method=method)
    exec(compile(code, f"namar_test/legacy_server_scripts/{script_name}.py", "exec"), namespace)
    return namespace


def run_api_script(script_name: str, kwargs: dict[str, Any] | None = None):
    before = None
    if hasattr(frappe.local, "response") and isinstance(frappe.local.response, dict):
        before = frappe.local.response.get("message")
    namespace = _execute(script_name, kwargs=kwargs)
    response = getattr(frappe.local, "response", None)
    if isinstance(response, dict) and "message" in response and response.get("message") is not before:
        return response.get("message")
    if "result" in namespace:
        return namespace["result"]
    if "message" in namespace:
        return namespace["message"]
    return None


def run_event_script(script_name: str, doc, method=None):
    # Prevent duplicate side effects during the migration window. Once the old
    # Server Script record is disabled or deleted, the app hook becomes active.
    if _is_legacy_server_script_enabled(script_name):
        return None
    return _execute(script_name, doc=doc, method=method)


def run_scheduler_script(script_name: str):
    if _is_legacy_server_script_enabled(script_name):
        return None
    return _execute(script_name)
