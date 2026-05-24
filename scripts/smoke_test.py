#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.parse import quote

import requests

ROOT = Path(__file__).resolve().parents[1]
SERVER_MANIFEST = ROOT / "namar_test" / "legacy_scripts" / "server_scripts_manifest.json"
CLIENT_MANIFEST = ROOT / "namar_test" / "legacy_scripts" / "client_scripts_manifest.json"

SAFE_APP_METHODS = [
    "namar_test.api.get_workflow_transitions",
    "namar_test.api.get_cutting_values_bulk",
    "namar_test.api.get_mr_full_data",
    "namar_test.api.get_sales_dashboard",
    "namar_test.api.get_purchase_dashboard",
    "namar_test.api.get_customer_summary",
    "namar_test.api.get_supplier_summary",
    "namar_test.api.get_related_items",
]


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def first_env(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def site_url(value: str) -> str:
    text = value.rstrip("/")
    if text.startswith(("http://", "https://")):
        return text
    return "https://" + text


class Client:
    def __init__(self, env: str):
        load_env(ROOT.parent / "erpnex_codex" / ".env.local")
        if env == "prod":
            site = first_env("FRAPPE_PROD_SITE")
            token = first_env("FRAPPE_PROD_TOKEN")
        else:
            site = first_env("FRAPPE_TEST_SITE", "FRAPPE_SITE")
            token = first_env("FRAPPE_TEST_TOKEN", "FRAPPE_TOKEN")
        if not site or not token:
            raise SystemExit(f"Missing Frappe credentials for {env}")
        self.base = site_url(site)
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": token if token.startswith("token ") else "token " + token,
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )

    def get_doc(self, doctype: str, name: str) -> dict | None:
        response = self.session.get(
            f"{self.base}/api/resource/{quote(doctype, safe='')}/{quote(name, safe='')}",
            timeout=90,
        )
        if response.status_code == 404:
            return None
        if not response.ok:
            raise RuntimeError(f"GET {doctype} {name} failed: {response.status_code} {response.text}")
        return response.json().get("data") or {}

    def call_method(self, method: str, payload: dict | None = None) -> object:
        response = self.session.post(
            f"{self.base}/api/method/{quote(method, safe='.')}",
            json=payload or {},
            timeout=120,
        )
        if not response.ok:
            raise RuntimeError(f"CALL {method} failed: {response.status_code} {response.text}")
        body = response.json()
        return body.get("message")


def load_names() -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for entry in json.loads(SERVER_MANIFEST.read_text(encoding="utf-8")):
        items.append(("Server Script", entry["name"]))
    for entry in json.loads(CLIENT_MANIFEST.read_text(encoding="utf-8")):
        items.append(("Client Script", entry["name"]))
    return items


def legacy_status(client: Client) -> dict[str, list[tuple[str, str]]]:
    status = {"enabled": [], "disabled": [], "missing": []}
    for doctype, name in load_names():
        doc = client.get_doc(doctype, name)
        if doc is None:
            status["missing"].append((doctype, name))
            continue
        if doctype == "Server Script":
            is_enabled = int(doc.get("disabled") or 0) == 0
        else:
            is_enabled = int(doc.get("enabled") or 0) == 1
        status["enabled" if is_enabled else "disabled"].append((doctype, name))
    return status


def assert_legacy(status: dict[str, list[tuple[str, str]]], expected: str) -> None:
    total = sum(len(rows) for rows in status.values())
    print(
        "Legacy scripts: "
        f"total={total} enabled={len(status['enabled'])} "
        f"disabled={len(status['disabled'])} missing={len(status['missing'])}"
    )
    if expected == "any":
        if total != 83 or status["missing"]:
            raise SystemExit("Expected all 83 legacy scripts to exist.")
    elif expected == "enabled":
        if len(status["enabled"]) != 83 or status["disabled"] or status["missing"]:
            raise SystemExit("Expected all 83 legacy scripts to be enabled.")
    elif expected == "disabled":
        if len(status["disabled"]) != 83 or status["enabled"] or status["missing"]:
            raise SystemExit("Expected all 83 legacy scripts to be disabled.")
    elif expected == "deleted":
        if status["enabled"] or status["disabled"] or len(status["missing"]) != 83:
            raise SystemExit("Expected all 83 legacy scripts to be deleted.")


def run_app_smoke(client: Client) -> None:
    for method in SAFE_APP_METHODS:
        message = client.call_method(method)
        kind = type(message).__name__
        print(f"App method OK: {method} -> {kind}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only smoke tests for the namar_test migration.")
    parser.add_argument("--env", choices=["test", "prod"], default="test")
    parser.add_argument("--expect-legacy", choices=["any", "enabled", "disabled", "deleted"], default="any")
    parser.add_argument("--app-installed", action="store_true", help="Call safe namespaced app APIs.")
    args = parser.parse_args()

    if args.env != "test":
        raise SystemExit("Smoke tests are intentionally limited to test for this migration.")

    client = Client(args.env)
    status = legacy_status(client)
    assert_legacy(status, args.expect_legacy)
    if args.app_installed:
        run_app_smoke(client)


if __name__ == "__main__":
    main()
