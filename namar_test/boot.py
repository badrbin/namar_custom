from __future__ import annotations

import json
from importlib.resources import files

import frappe


def _migrated_client_script_names() -> set[str]:
    try:
        manifest_path = files("namar_test.legacy_scripts") / "client_scripts_manifest.json"
        return {entry["name"] for entry in json.loads(manifest_path.read_text(encoding="utf-8"))}
    except Exception:
        return set()


MIGRATED_CLIENT_SCRIPT_NAMES = _migrated_client_script_names()


def boot_session(bootinfo):
    enabled_legacy_count = frappe.db.count(
        "Client Script",
        filters={"name": ["in", list(MIGRATED_CLIENT_SCRIPT_NAMES)], "enabled": 1},
    )
    bootinfo.namar_test_client_scripts_enabled = enabled_legacy_count == 0
