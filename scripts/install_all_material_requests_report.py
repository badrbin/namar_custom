#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.parse import quote

import requests


ROOT = Path(__file__).resolve().parents[1]
REPORT_NAME = "كل طلبات المواد"
REPORT_DOCTYPE = "Material Request"
REPORT_MODULE = "Namar Test"
REPORT_JS = (
    ROOT
    / "namar_test"
    / "namar_test"
    / "report"
    / "كل_طلبات_المواد"
    / "كل_طلبات_المواد.js"
)

REPORT_SCRIPT = r'''
filters = filters or {}
data = frappe.call("namar_test.material_requests.execute_all_material_requests_report", filters=filters)
'''


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
    def __init__(self) -> None:
        load_env(ROOT.parent / "erpnex_codex" / ".env.local")
        site = first_env("FRAPPE_TEST_SITE", "FRAPPE_SITE")
        token = first_env("FRAPPE_TEST_TOKEN", "FRAPPE_TOKEN")
        if not site or not token:
            raise SystemExit("Missing Frappe test credentials.")
        self.base = site_url(site)
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": token if token.startswith("token ") else "token " + token,
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )

    def get_report(self) -> dict | None:
        response = self.session.get(
            f"{self.base}/api/resource/Report/{quote(REPORT_NAME, safe='')}",
            timeout=90,
        )
        if response.status_code == 404:
            return None
        if not response.ok:
            raise RuntimeError(f"GET Report failed: {response.status_code} {response.text}")
        return response.json().get("data") or {}

    def upsert_report(self, doc: dict) -> dict:
        existing = self.get_report()
        if existing:
            response = self.session.put(
                f"{self.base}/api/resource/Report/{quote(REPORT_NAME, safe='')}",
                json=doc,
                timeout=120,
            )
        else:
            response = self.session.post(f"{self.base}/api/resource/Report", json=doc, timeout=120)
        if not response.ok:
            raise RuntimeError(f"UPSERT Report failed: {response.status_code} {response.text}")
        return response.json().get("data") or {}


def build_report_doc() -> dict:
    javascript = REPORT_JS.read_text(encoding="utf-8")
    return {
        "doctype": "Report",
        "name": REPORT_NAME,
        "report_name": REPORT_NAME,
        "ref_doctype": REPORT_DOCTYPE,
        "report_type": "Script Report",
        "is_standard": "No",
        "module": REPORT_MODULE,
        "add_total_row": 0,
        "disabled": 0,
        "prepared_report": 0,
        "report_script": REPORT_SCRIPT.strip() + "\n",
        "javascript": javascript,
        "roles": [
            {"role": "System Manager"},
            {"role": "Stock User"},
            {"role": "Stock Manager"},
            {"role": "Sales User"},
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Install the all-material-requests Script Report on test.")
    parser.add_argument("--yes", action="store_true", help="Confirm the live test-site write.")
    args = parser.parse_args()
    if not args.yes:
        raise SystemExit("Pass --yes to update the test site Report record.")

    client = Client()
    doc = client.upsert_report(build_report_doc())
    print(json.dumps({"name": doc.get("name"), "is_standard": doc.get("is_standard")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
