#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import requests

ROOT = Path(__file__).resolve().parents[1]
SERVER_MANIFEST = ROOT / "namar_test" / "legacy_scripts" / "server_scripts_manifest.json"
CLIENT_MANIFEST = ROOT / "namar_test" / "legacy_scripts" / "client_scripts_manifest.json"


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
        self.session.headers.update({
            "Authorization": token if token.startswith("token ") else "token " + token,
            "Accept": "application/json",
            "Content-Type": "application/json",
        })

    def get_doc(self, doctype: str, name: str) -> dict | None:
        response = self.session.get(f"{self.base}/api/resource/{quote(doctype, safe='')}/{quote(name, safe='')}", timeout=90)
        if response.status_code == 404:
            return None
        if not response.ok:
            raise RuntimeError(f"GET {doctype} {name} failed: {response.status_code} {response.text}")
        return response.json().get("data") or {}

    def put_doc(self, doctype: str, name: str, payload: dict) -> None:
        response = self.session.put(f"{self.base}/api/resource/{quote(doctype, safe='')}/{quote(name, safe='')}", json=payload, timeout=90)
        if not response.ok:
            raise RuntimeError(f"PUT {doctype} {name} failed: {response.status_code} {response.text}")

    def delete_doc(self, doctype: str, name: str) -> None:
        response = self.session.delete(f"{self.base}/api/resource/{quote(doctype, safe='')}/{quote(name, safe='')}", timeout=90)
        if not response.ok and response.status_code != 404:
            raise RuntimeError(f"DELETE {doctype} {name} failed: {response.status_code} {response.text}")


def load_names() -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for entry in json.loads(SERVER_MANIFEST.read_text(encoding="utf-8")):
        items.append(("Server Script", entry["name"]))
    for entry in json.loads(CLIENT_MANIFEST.read_text(encoding="utf-8")):
        items.append(("Client Script", entry["name"]))
    return items


def main() -> None:
    parser = argparse.ArgumentParser(description="Disable or delete migrated legacy Server/Client Scripts.")
    parser.add_argument("--env", choices=["test", "prod"], default="test")
    parser.add_argument("--action", choices=["disable", "delete", "count"], default="count")
    parser.add_argument("--execute", action="store_true", help="Actually mutate Frappe records. Default is dry-run.")
    args = parser.parse_args()

    if args.env != "test":
        raise SystemExit("This migration helper is intentionally limited to test for now.")

    client = Client(args.env)
    names = load_names()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = ROOT / "backups" / f"legacy_scripts_{args.env}_{timestamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    existing = []
    missing = []
    for doctype, name in names:
        doc = client.get_doc(doctype, name)
        if doc is None:
            missing.append((doctype, name))
            continue
        existing.append((doctype, name, doc))
        safe = name.replace("/", "_")
        (backup_dir / f"{doctype.replace(' ', '_')}__{safe}.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Existing: {len(existing)} | Missing: {len(missing)} | Backup: {backup_dir}")
    if args.action == "count":
        return
    if not args.execute:
        print(f"Dry-run only. Would {args.action} {len(existing)} records.")
        return
    for doctype, name, _doc in existing:
        if args.action == "disable":
            field = "disabled" if doctype == "Server Script" else "enabled"
            payload = {field: 1 if doctype == "Server Script" else 0}
            client.put_doc(doctype, name, payload)
            print(f"Disabled {doctype}: {name}")
        else:
            client.delete_doc(doctype, name)
            print(f"Deleted {doctype}: {name}")


if __name__ == "__main__":
    main()
