#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
server = json.loads((ROOT / "namar_test" / "legacy_scripts" / "server_scripts_manifest.json").read_text(encoding="utf-8"))
client = json.loads((ROOT / "namar_test" / "legacy_scripts" / "client_scripts_manifest.json").read_text(encoding="utf-8"))
print(f"Server Scripts archived: {len(server)}")
print(f"Client Scripts archived: {len(client)}")
print(f"Active API wrappers: {sum(1 for s in server if s.get('script_type') == 'API' and not s.get('disabled') and s.get('api_method'))}")
print(f"Active DocType Event hooks: {sum(1 for s in server if s.get('script_type') == 'DocType Event' and not s.get('disabled') and s.get('event_hook'))}")
print(f"Active Scheduler hooks: {sum(1 for s in server if s.get('script_type') == 'Scheduler Event' and not s.get('disabled') and s.get('scheduler_hook'))}")
print(f"Active Client Script assets: {sum(1 for c in client if c.get('enabled'))}")
