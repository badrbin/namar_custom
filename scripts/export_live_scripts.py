#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import requests

ROOT = Path(__file__).resolve().parents[1]


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


def slugify(text: str, fallback: str) -> str:
    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    normalized = re.sub(r"[^a-z0-9_]+", "_", normalized.lower()).strip("_")
    if not normalized:
        normalized = fallback
    if normalized[0].isdigit():
        normalized = "_" + normalized
    return normalized


def unique_slug(text: str, used: set[str], fallback: str) -> str:
    base = slugify(text, fallback)
    candidate = base
    index = 2
    while candidate in used:
        candidate = f"{base}_{index}"
        index += 1
    used.add(candidate)
    return candidate


class FrappeClient:
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

    def list_names(self, doctype: str) -> list[str]:
        response = self.session.get(
            f"{self.base}/api/resource/{quote(doctype, safe='')}",
            params={
                "fields": json.dumps(["name"]),
                "limit_page_length": 500,
                "order_by": "name asc",
            },
            timeout=90,
        )
        if not response.ok:
            raise RuntimeError(f"LIST {doctype} failed: {response.status_code} {response.text}")
        return [row["name"] for row in response.json().get("data") or []]

    def get_doc(self, doctype: str, name: str) -> dict:
        response = self.session.get(
            f"{self.base}/api/resource/{quote(doctype, safe='')}/{quote(name, safe='')}",
            timeout=90,
        )
        if not response.ok:
            raise RuntimeError(f"GET {doctype} {name} failed: {response.status_code} {response.text}")
        return response.json().get("data") or {}


def export_doctype(client: FrappeClient, doctype: str, out_dir: Path) -> list[dict]:
    names = client.list_names(doctype)
    used: set[str] = set()
    manifest = []
    code_ext = ".py" if doctype == "Server Script" else ".js"
    kind_dir = out_dir / doctype.lower().replace(" ", "_")
    kind_dir.mkdir(parents=True, exist_ok=True)
    for index, name in enumerate(names, start=1):
        doc = client.get_doc(doctype, name)
        slug = unique_slug(name, used, f"script_{index:03d}")
        code_file = f"{slug}{code_ext}"
        meta_file = f"{slug}.meta.json"
        script = doc.pop("script", "") or ""
        (kind_dir / code_file).write_text(script.rstrip() + "\n", encoding="utf-8")
        (kind_dir / meta_file).write_text(
            json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        manifest.append({"name": name, "code_file": code_file, "meta_file": meta_file})
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Export live Server Script and Client Script records.")
    parser.add_argument("--env", choices=["test", "prod"], default="test")
    args = parser.parse_args()

    if args.env != "test":
        raise SystemExit("This exporter is intentionally limited to test for this migration.")

    client = FrappeClient(args.env)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = ROOT / "exports" / f"live_scripts_{args.env}_{timestamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "env": args.env,
        "created_at": timestamp,
        "server_scripts": export_doctype(client, "Server Script", out_dir),
        "client_scripts": export_doctype(client, "Client Script", out_dir),
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Exported Server Scripts: {len(manifest['server_scripts'])}")
    print(f"Exported Client Scripts: {len(manifest['client_scripts'])}")
    print(f"Output: {out_dir}")


if __name__ == "__main__":
    main()
