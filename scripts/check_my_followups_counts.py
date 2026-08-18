#!/usr/bin/env python3
"""تحقق قراءة فقط لعقد عدادات صفحة متابعاتي على الموقع التجريبي.

الوضع الافتراضي dry-run محلي بلا قراءة لملف البيئة وبلا اتصال. التشغيل الحي
يتطلب ``--run`` و``--confirm-site`` مطابقًا لـ ``FRAPPE_TEST_SITE``، ويستدعي
ثلاث دوال قراءة فقط بصفحة طولها عنصر واحد. لا يحفظ transcript التوكن أو
محتوى العناصر أو نص الاستجابة.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import requests


ROOT = Path(__file__).resolve().parents[1]
KNOWN_PRODUCTION_HOSTS = {"erp.namar.net"}
ENDPOINTS = (
    (
        "mentions",
        "/api/method/namar_test.mentions.api.get_mentions",
        {"bucket": "open", "search": "", "limit_start": 0, "page_length": 1},
    ),
    (
        "followups",
        "/api/method/namar_test.followups.api.get_followups",
        {"bucket": "all", "search": "", "limit_start": 0, "page_length": 1},
    ),
    (
        "approvals",
        "/api/method/namar_test.followups.api.get_approvals",
        {"search": "", "limit_start": 0, "page_length": 1},
    ),
)


class CheckFailure(RuntimeError):
    pass


@dataclass(frozen=True)
class RunConfig:
    base_url: str
    host: str
    token: str
    timeout: int


def default_env_file() -> Path:
    local = ROOT / ".env.local"
    if local.exists():
        return local
    main = ROOT.parent.parent / "erpnex_codex" / ".env.local"
    return main if main.exists() else local


def default_state_dir() -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return base / "namar_test" / "my_followups_counts"


def normalize_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def read_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise CheckFailure(f"ملف البيئة غير موجود: {path}")
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def normalize_site(value: str, label: str) -> tuple[str, str]:
    raw = normalize_text(value)
    if not raw:
        raise CheckFailure(f"{label} غير مضبوط")
    candidate = raw if "://" in raw else f"https://{raw}"
    parsed = urlparse(candidate)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise CheckFailure(f"{label} يجب أن يكون موقع HTTPS صالحًا")
    if parsed.username or parsed.password:
        raise CheckFailure(f"{label} لا يقبل بيانات دخول داخل الرابط")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise CheckFailure(f"{label} يجب أن يكون أصل الموقع بلا مسار أو query")
    try:
        port = parsed.port
    except ValueError as exc:
        raise CheckFailure(f"منفذ {label} غير صالح") from exc
    host = parsed.hostname.lower().rstrip(".")
    suffix = f":{port}" if port else ""
    return f"https://{host}{suffix}", host


def normalize_token(value: str) -> str:
    token = normalize_text(value)
    if not token:
        raise CheckFailure("FRAPPE_TEST_TOKEN غير مضبوط في ملف البيئة")
    return token if token.lower().startswith("token ") else f"token {token}"


def validate_run_config(args: argparse.Namespace, env: dict[str, str]) -> RunConfig:
    base_url, host = normalize_site(env.get("FRAPPE_TEST_SITE", ""), "FRAPPE_TEST_SITE")
    confirmed_url, confirmed_host = normalize_site(args.confirm_site, "--confirm-site")
    if confirmed_url != base_url or confirmed_host != host:
        raise CheckFailure("--confirm-site يجب أن يطابق FRAPPE_TEST_SITE حرفيًا")

    if args.test_site:
        requested_url, requested_host = normalize_site(args.test_site, "--test-site")
        if requested_url != base_url or requested_host != host:
            raise CheckFailure("--test-site يجب أن يطابق FRAPPE_TEST_SITE؛ لا يسمح بتجاوز الموقع")

    production_hosts = set(KNOWN_PRODUCTION_HOSTS)
    configured_prod = normalize_text(env.get("FRAPPE_PROD_SITE"))
    if configured_prod:
        _, production_host = normalize_site(configured_prod, "FRAPPE_PROD_SITE")
        production_hosts.add(production_host)
    if host in production_hosts:
        raise CheckFailure(f"رفض التشغيل: {host} موقع إنتاج")
    if not 5 <= int(args.timeout) <= 120:
        raise CheckFailure("--timeout يجب أن يكون بين 5 و120 ثانية")
    return RunConfig(
        base_url=base_url,
        host=host,
        token=normalize_token(env.get("FRAPPE_TEST_TOKEN", "")),
        timeout=int(args.timeout),
    )


def validate_open_count(endpoint: str, payload: Any) -> int:
    if not isinstance(payload, dict):
        raise CheckFailure(f"عقد {endpoint} لا يعيد كائنًا")
    counts = payload.get("counts")
    value = counts.get("open") if isinstance(counts, dict) else None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CheckFailure(f"counts.open في {endpoint} يجب أن يكون عددًا صحيحًا غير سالب")
    return value


class CountsChecker:
    def __init__(
        self,
        config: RunConfig,
        *,
        session: requests.Session | None = None,
    ):
        self.config = config
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Authorization": config.token,
                "Accept": "application/json",
                "User-Agent": "namar-my-followups-counts-check/1.0",
            }
        )

    def check(self, transcript: dict[str, Any]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for name, path, params in ENDPOINTS:
            entry: dict[str, Any] = {
                "endpoint": name,
                "method": "GET",
                "path": path,
                "argument_keys": sorted(params),
                "page_length": 1,
                "started_at_utc": datetime.now(timezone.utc).isoformat(),
            }
            transcript["requests"].append(entry)
            started = time.perf_counter()
            try:
                response = self.session.get(
                    self.config.base_url + path,
                    params=params,
                    timeout=self.config.timeout,
                    allow_redirects=False,
                )
            except requests.RequestException as exc:
                entry["error_category"] = "network"
                entry["error_type"] = type(exc).__name__
                raise CheckFailure(f"تعذر اتصال القراءة بنقطة {name}") from None
            finally:
                entry["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)

            entry["status"] = int(response.status_code)
            if response.status_code != 200:
                entry["error_category"] = "http"
                raise CheckFailure(f"نقطة {name} أعادت HTTP {response.status_code}")
            try:
                envelope = response.json()
            except ValueError:
                entry["error_category"] = "invalid_json"
                raise CheckFailure(f"نقطة {name} لم تعد JSON صالحًا") from None
            if not isinstance(envelope, dict) or "message" not in envelope:
                entry["error_category"] = "invalid_envelope"
                raise CheckFailure(f"غلاف استجابة {name} غير صحيح")
            try:
                open_count = validate_open_count(name, envelope["message"])
            except CheckFailure:
                entry["error_category"] = "invalid_counts_contract"
                raise
            entry["counts_open"] = open_count
            entry["contract"] = "ok"
            counts[name] = open_count
        return counts


def new_transcript(config: RunConfig) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "mode": "read-only",
        "environment": "test",
        "host": config.host,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "running",
        "contains_secrets": False,
        "response_contents_persisted": False,
        "requests": [],
    }


def validate_state_dir(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    repository = ROOT.resolve()
    if resolved == repository or repository in resolved.parents:
        raise CheckFailure("يجب حفظ transcript خارج المستودع في local state أو tmp غير متتبع")
    return resolved


def write_transcript(state_dir: Path, transcript: dict[str, Any]) -> Path:
    destination = validate_state_dir(state_dir)
    destination.mkdir(parents=True, exist_ok=True)
    os.chmod(destination, 0o700)
    filename = datetime.now(timezone.utc).strftime("counts-%Y%m%dT%H%M%SZ-") + uuid4().hex[:8]
    final_path = destination / f"{filename}.json"
    temporary = destination / f".{filename}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(temporary, flags, stat.S_IRUSR | stat.S_IWUSR)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(transcript, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.chmod(temporary, 0o600)
        temporary.replace(final_path)
        os.chmod(final_path, 0o600)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return final_path


def dry_run_summary(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "mode": "dry-run",
        "network_requests": False,
        "env_file_read": False,
        "transcript_written": False,
        "env_file": str(args.env_file.expanduser()),
        "required_to_execute": ["--run", "--confirm-site <FRAPPE_TEST_SITE>"],
        "endpoints": [name for name, _, _ in ENDPOINTS],
        "http_method": "GET",
        "page_length": 1,
        "contract": "counts.open integer >= 0",
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="تحقق قراءة فقط من عدادات متابعاتي؛ الافتراضي dry-run بلا اتصال.",
    )
    parser.add_argument("--run", action="store_true", help="ينفذ استدعاءات القراءة الثلاثة.")
    parser.add_argument(
        "--confirm-site",
        default="",
        help="تأكيد إلزامي مطابق لـFRAPPE_TEST_SITE عند --run.",
    )
    parser.add_argument(
        "--test-site",
        default="",
        help="قيد اختياري إضافي؛ يجب أن يطابق FRAPPE_TEST_SITE.",
    )
    parser.add_argument("--env-file", type=Path, default=default_env_file())
    parser.add_argument("--state-dir", type=Path, default=default_state_dir())
    parser.add_argument("--timeout", type=int, default=30)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.run:
        print(json.dumps(dry_run_summary(args), ensure_ascii=False, indent=2))
        print("\nDry-run محلي فقط: لم يُقرأ ملف البيئة ولم يحدث اتصال أو كتابة.")
        return 0

    try:
        env = read_env_file(args.env_file.expanduser().resolve())
        config = validate_run_config(args, env)
        state_dir = validate_state_dir(args.state_dir)
    except CheckFailure as exc:
        print(f"[رفض آمن] {exc}", file=sys.stderr)
        return 2

    transcript = new_transcript(config)
    checker = CountsChecker(config)
    try:
        counts = checker.check(transcript)
    except CheckFailure as exc:
        transcript["status"] = "failed"
        transcript["failure_category"] = "contract_or_read_failure"
        transcript_path = write_transcript(state_dir, transcript)
        print(f"[فشل] {exc}", file=sys.stderr)
        print(f"transcript آمن: {transcript_path}", file=sys.stderr)
        return 1

    transcript["status"] = "passed"
    transcript["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    transcript_path = write_transcript(state_dir, transcript)
    print(json.dumps({"status": "passed", "counts": counts}, ensure_ascii=False, indent=2))
    print(f"transcript آمن: {transcript_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
