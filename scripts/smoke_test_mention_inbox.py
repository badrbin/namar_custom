#!/usr/bin/env python3
"""Smoke test آمن لصندوق الإشارات في بيئة Frappe التجريبية فقط.

الوضع الافتراضي خطة محلية بلا اتصال. التشغيل الحي يتطلب معًا::

    --run --confirm-site <FRAPPE_TEST_SITE host>

ينشئ الاختبار ToDo معزولًا وتعليقًا يذكر مستخدم التوكن نفسه فقط. لا يذكر
موظفًا آخر ولا يستخدم مستند أعمال. كل سجل يحمل بصمة تشغيل، ويحفظ اسمه في
manifest محلي قبل متابعة الاختبارات. التنظيف لا يحذف أي سجل ما لم تتطابق
البصمة الحية والمرجع المعزول مع الـmanifest.

لا تختبر الأداة الرد حيًا افتراضيًا. يمكن تفعيله صراحة عبر
``--include-self-reply``؛ عندها تختبر منشن مستخدم التوكن نفسه فقط، وidempotency
لـ``reply_mention`` واختلاف المستلمين، ثم ``reply_and_close`` وإعادة الفتح.
لا تُدخل الأداة بريد أي موظف آخر في حمولة الرد ولا ترسل إليه إشعارًا.

Thread وEvent نوعان داخليان بلا قراءة REST. تتحقق الأداة منهما عبر API المنتج،
وتحذف Thread المملوكة فقط بعد حذف Comments المعزولة؛ on_trash يحذف Events
المرتبطة كـcascade، ولا تحاول الأداة قراءة Event أو حذفها مباشرة.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import quote, urlparse
from uuid import uuid4

import requests


ROOT = Path(__file__).resolve().parents[1]
API_BASE = "/api/method/namar_test.mentions.api"
THREAD_DOCTYPE = "Namar Mention Thread"
EVENT_DOCTYPE = "Namar Mention Event"
ALLOWED_RESOURCE_TYPES = {"ToDo", "Comment", "Notification Log", THREAD_DOCTYPE}
KNOWN_PRODUCTION_HOSTS = {"erp.namar.net"}
MAX_RESOURCES = 20
POLL_ATTEMPTS = 15
POLL_INTERVAL_SECONDS = 1.0
MAX_HTTP_TRANSCRIPT_ENTRIES = 250
MAX_SEEN_OBSERVATIONS = 64

TRANSCRIPT_REQUEST_HEADERS = ("Cache-Control", "Pragma")
TRANSCRIPT_RESPONSE_HEADERS = (
    "X-Frappe-Request-Id",
    "Date",
    "Age",
    "Cache-Control",
    "ETag",
    "Last-Modified",
    "Vary",
    "X-Cache-Status",
    "X-Proxy-Upstream",
    "CF-Cache-Status",
    "Via",
    "Server",
)
TRANSCRIPT_SAFE_ARGUMENTS = {
    "bucket",
    "limit_start",
    "page_length",
    "thread_name",
    "expected_last_event_key",
    "seen",
}
MENTION_STATE_FIELDS = (
    "name",
    "last_event_key",
    "last_seen_event_key",
    "unread",
    "status",
    "modified",
)

THREAD_REQUIRED_FIELDS = {
    "thread_key",
    "for_user",
    "status",
    "reference_doctype",
    "reference_name",
    "latest_comment",
    "latest_from_user",
    "latest_preview_plain",
    "first_mentioned_at",
    "latest_mentioned_at",
    "mention_count",
    "last_event_key",
    "last_seen_event_key",
    "converted_to_todo",
}
EVENT_REQUIRED_FIELDS = {
    "event_key",
    "for_user",
    "thread",
    "event_type",
    "mentioned_at",
    "comment",
    "comment_modified",
    "from_user",
    "content_plain",
    "request_id",
}
MENTION_ENDPOINTS = (
    "get_mentions",
    "get_mention_detail",
    "search_reply_mentions",
    "mark_mention_seen",
    "close_mention",
    "reopen_mention",
    "convert_mention_to_followup",
)
OPTIONAL_REPLY_ENDPOINTS = ("reply_mention", "reply_and_close")


def default_env_file() -> Path:
    worktree_env = ROOT / ".env.local"
    if worktree_env.exists():
        return worktree_env
    sibling_main = ROOT.parent.parent / "erpnex_codex" / ".env.local"
    if sibling_main.exists():
        return sibling_main
    for ancestor in ROOT.parents:
        sibling_main = ancestor / "erpnex_codex" / ".env.local"
        if sibling_main.exists():
            return sibling_main
    return worktree_env


def default_state_dir() -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return base / "namar_test" / "mention_inbox_smoke"


class SmokeFailure(RuntimeError):
    pass


class HttpFailure(SmokeFailure):
    def __init__(self, method: str, path: str, status_code: int, message: str):
        self.method = method
        self.path = path
        self.status_code = status_code
        self.server_message = message
        super().__init__(f"{method} {path} رجع {status_code}: {message or 'بلا رسالة'}")


def normalize_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def normalize_token(value: str) -> str:
    token = normalize_text(value)
    if not token:
        return ""
    return token if token.lower().startswith("token ") else f"token {token}"


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


def normalize_site(value: str) -> tuple[str, str]:
    raw = normalize_text(value)
    if not raw:
        raise SmokeFailure("FRAPPE_TEST_SITE غير مضبوط")
    candidate = raw if "://" in raw else f"https://{raw}"
    parsed = urlparse(candidate)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise SmokeFailure("موقع التجريبي يجب أن يكون HTTPS صالحًا")
    if parsed.username or parsed.password:
        raise SmokeFailure("FRAPPE_TEST_SITE لا يقبل بيانات دخول داخل الرابط")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise SmokeFailure("FRAPPE_TEST_SITE يجب أن يكون أصل الموقع بلا مسار أو query")
    host = parsed.hostname.lower().rstrip(".")
    try:
        parsed_port = parsed.port
    except ValueError as exc:
        raise SmokeFailure("منفذ FRAPPE_TEST_SITE غير صالح") from exc
    port = f":{parsed_port}" if parsed_port else ""
    return f"https://{host}{port}", host


def host_from_value(value: str) -> str:
    raw = normalize_text(value)
    if not raw:
        return ""
    candidate = raw if "://" in raw else f"https://{raw}"
    return (urlparse(candidate).hostname or "").lower().rstrip(".")


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def selected_headers(
    headers: Any,
    allowed: Iterable[str],
) -> dict[str, str]:
    """Return only explicitly allowlisted, bounded HTTP headers."""

    selected: dict[str, str] = {}
    if not headers:
        return selected
    for name in allowed:
        value = headers.get(name)
        if value is not None:
            selected[name] = normalize_text(value)[:500]
    return selected


def safe_request_arguments(
    params: dict[str, Any] | None,
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    """Keep diagnostic request shape without persisting free text or credentials."""

    values = params if params is not None else payload or {}
    safe: dict[str, Any] = {}
    for key in sorted(TRANSCRIPT_SAFE_ARGUMENTS.intersection(values)):
        value = values.get(key)
        if value is None or isinstance(value, (bool, int, float)):
            safe[key] = value
        else:
            safe[key] = normalize_text(value)[:200]

    if "search" in values:
        raw_search = "" if values.get("search") is None else str(values.get("search"))
        normalized_search = raw_search.strip()
        safe["search"] = {
            "length": len(raw_search),
            "trailing_whitespace": len(raw_search) - len(raw_search.rstrip()),
            "sha256": digest(raw_search),
            "normalized_sha256": digest(normalized_search),
        }
    return safe


def mention_state(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    return {key: item.get(key) for key in MENTION_STATE_FIELDS}


def parse_server_message(response: requests.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return "استجابة غير JSON"
    if not isinstance(data, dict):
        return "استجابة غير متوقعة"

    messages = data.get("_server_messages")
    if messages:
        try:
            parsed = json.loads(messages)
            for raw in parsed:
                message = json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(message, dict) and message.get("message"):
                    return normalize_text(message["message"])[:500]
        except (TypeError, ValueError, json.JSONDecodeError):
            pass

    for key in ("message", "exc_type", "exception"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:500]
    return ""


@dataclass(frozen=True)
class SmokeConfig:
    base_url: str
    host: str
    token: str
    timeout: int
    expected_user: str = ""
    include_self_reply: bool = False


class FrappeClient:
    def __init__(self, config: SmokeConfig):
        self.config = config
        self.session = requests.Session()
        self._request_observer: Callable[[dict[str, Any]], None] | None = None
        self._request_seq = 0
        self.last_request_seq: int | None = None
        self.session.headers.update(
            {
                "Authorization": config.token,
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "namar-mention-inbox-smoke/1.0",
            }
        )

    def set_request_observer(
        self,
        observer: Callable[[dict[str, Any]], None],
        *,
        start_seq: int = 0,
    ) -> None:
        self._request_observer = observer
        self._request_seq = max(0, int(start_seq))
        self.last_request_seq = None

    def _finish_request_transcript(
        self,
        entry: dict[str, Any],
        *,
        started_monotonic: float,
    ) -> None:
        entry["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        entry["duration_ms"] = round((time.perf_counter() - started_monotonic) * 1000, 3)
        self.last_request_seq = int(entry["seq"])
        if self._request_observer:
            self._request_observer(entry)

    def raw_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> requests.Response:
        self._request_seq += 1
        started_monotonic = time.perf_counter()
        entry: dict[str, Any] = {
            "seq": self._request_seq,
            "started_at_utc": datetime.now(timezone.utc).isoformat(),
            "method": method.upper(),
            "url": self.config.base_url + path,
            "path": path,
            "query_keys": sorted((params or {}).keys()),
            "body_keys": sorted((payload or {}).keys()),
            "safe_args": safe_request_arguments(params, payload),
            # Authorization/Cookie and all inherited Session headers are deliberately absent.
            "request_headers": selected_headers(headers, TRANSCRIPT_REQUEST_HEADERS),
        }
        try:
            response = self.session.request(
                method,
                self.config.base_url + path,
                params=params,
                json=payload,
                headers=headers,
                timeout=self.config.timeout,
            )
        except requests.RequestException as exc:
            entry["error_type"] = type(exc).__name__
            self._finish_request_transcript(entry, started_monotonic=started_monotonic)
            raise

        entry["status"] = response.status_code
        effective_url = normalize_text(getattr(response.request, "url", ""))
        if effective_url:
            entry["effective_url_sha256"] = digest(effective_url)
        entry["response_headers"] = selected_headers(
            response.headers,
            TRANSCRIPT_RESPONSE_HEADERS,
        )
        self._finish_request_transcript(entry, started_monotonic=started_monotonic)
        return response

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        expected: Iterable[int] = (200,),
    ) -> dict[str, Any]:
        response = self.raw_request(
            method,
            path,
            params=params,
            payload=payload,
            headers=headers,
        )
        if response.status_code not in set(expected):
            raise HttpFailure(method, path, response.status_code, parse_server_message(response))
        if not response.content:
            return {}
        try:
            data = response.json()
        except ValueError as exc:
            raise SmokeFailure(f"استجابة {method} {path} ليست JSON") from exc
        if not isinstance(data, dict):
            raise SmokeFailure(f"استجابة {method} {path} ليست كائنًا")
        return data

    def call(
        self,
        method_name: str,
        *,
        http_method: str = "GET",
        args: dict[str, Any] | None = None,
        request_headers: dict[str, str] | None = None,
    ) -> Any:
        path = f"{API_BASE}.{method_name}"
        if http_method == "GET":
            data = self.request(
                "GET",
                path,
                params=args or {},
                headers=request_headers,
            )
        else:
            data = self.request(
                "POST",
                path,
                payload=args or {},
                headers=request_headers,
            )
        return data.get("message")

    def get_logged_user(self) -> str:
        data = self.request("GET", "/api/method/frappe.auth.get_logged_user")
        return normalize_text(data.get("message"))

    def get_doc(self, doctype: str, name: str) -> dict[str, Any] | None:
        path = f"/api/resource/{quote(doctype, safe='')}/{quote(str(name), safe='')}"
        response = self.raw_request("GET", path)
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise HttpFailure("GET", path, response.status_code, parse_server_message(response))
        data = response.json().get("data") or {}
        return data if isinstance(data, dict) else None

    def create_doc(self, doctype: str, payload: dict[str, Any]) -> dict[str, Any]:
        path = f"/api/resource/{quote(doctype, safe='')}"
        data = self.request("POST", path, payload=payload, expected=(200, 201))
        doc = data.get("data") or {}
        if not isinstance(doc, dict) or not doc.get("name"):
            raise SmokeFailure(f"لم يرجع إنشاء {doctype} اسم المستند")
        return self.get_doc(doctype, normalize_text(doc["name"])) or doc

    def delete_doc(self, doctype: str, name: str) -> None:
        path = f"/api/resource/{quote(doctype, safe='')}/{quote(str(name), safe='')}"
        response = self.raw_request("DELETE", path)
        if response.status_code == 404:
            return
        if response.status_code not in {200, 202}:
            raise HttpFailure("DELETE", path, response.status_code, parse_server_message(response))

    def list_docs(
        self,
        doctype: str,
        *,
        fields: list[str],
        filters: list[list[Any]],
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        path = f"/api/resource/{quote(doctype, safe='')}"
        data = self.request(
            "GET",
            path,
            params={
                "fields": json.dumps(fields, ensure_ascii=False, separators=(",", ":")),
                "filters": json.dumps(filters, ensure_ascii=False, separators=(",", ":")),
                "limit_page_length": min(max(int(limit), 1), 500),
            },
        )
        rows = data.get("data") or []
        return [row for row in rows if isinstance(row, dict)]

    def get_meta_bundle(self, doctype: str) -> list[dict[str, Any]]:
        data = self.request(
            "GET",
            "/api/method/frappe.desk.form.load.getdoctype",
            params={"doctype": doctype, "with_parent": 1},
        )
        docs = data.get("docs") or []
        return [doc for doc in docs if isinstance(doc, dict)]

    def has_permission(self, doctype: str, perm_type: str) -> bool:
        data = self.request(
            "GET",
            "/api/method/frappe.client.has_permission",
            params={"doctype": doctype, "docname": "", "perm_type": perm_type},
        )
        message = data.get("message") or {}
        return bool(isinstance(message, dict) and message.get("has_permission"))


def ensure(condition: Any, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


def validate_fingerprint_shape(fingerprint: Any) -> dict[str, str]:
    ensure(isinstance(fingerprint, dict), "fingerprint في manifest يجب أن يكون كائنًا")
    allowed_keys = {"field", "contains", "equals", "equals_sha256"}
    ensure(
        not (set(fingerprint) - allowed_keys),
        "fingerprint يحتوي مفاتيح غير مسموحة",
    )
    field = normalize_text(fingerprint.get("field"))
    ensure(field and field.replace("_", "").isalnum(), "fingerprint بلا field صالح")
    comparators = [key for key in ("contains", "equals", "equals_sha256") if key in fingerprint]
    ensure(len(comparators) == 1, "fingerprint يجب أن يحتوي comparator واحدًا فقط")
    comparator = comparators[0]
    expected = normalize_text(fingerprint.get(comparator))
    ensure(expected, "قيمة comparator في fingerprint لا يجوز أن تكون فارغة")
    if comparator == "equals_sha256":
        ensure(
            len(expected) == 64 and all(char in "0123456789abcdef" for char in expected.lower()),
            "equals_sha256 في fingerprint غير صالح",
        )
    return {"field": field, comparator: expected}


def validate_resource_fingerprints(
    fingerprints: Any,
    *,
    marker: str,
) -> list[dict[str, str]]:
    ensure(
        isinstance(fingerprints, list) and 1 <= len(fingerprints) <= 5,
        "resource في manifest يجب أن يحمل 1-5 fingerprints",
    )
    normalized = [validate_fingerprint_shape(fp) for fp in fingerprints]
    ensure(
        any(fp.get("contains") == marker for fp in normalized),
        "كل resource في manifest يجب أن يحمل بصمة RUN_ID",
    )
    return normalized


def response_items(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise SmokeFailure("استجابة get_mentions ليست كائنًا")
    items = payload.get("items") or []
    if not isinstance(items, list):
        raise SmokeFailure("items في get_mentions ليست قائمة")
    return [row for row in items if isinstance(row, dict)]


def find_item(payload: Any, name: str) -> dict[str, Any] | None:
    return next(
        (row for row in response_items(payload) if normalize_text(row.get("name")) == name),
        None,
    )


def detail_mention(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise SmokeFailure("استجابة get_mention_detail ليست كائنًا")
    mention = payload.get("mention") or payload
    if not isinstance(mention, dict):
        raise SmokeFailure("mention في تفاصيل الوارد ليست كائنًا")
    return mention


class SmokeRunner:
    def __init__(
        self,
        client: FrappeClient,
        config: SmokeConfig,
        *,
        manifest: dict[str, Any],
        manifest_path: Path,
        user: str,
    ):
        self.client = client
        self.config = config
        self.manifest = manifest
        self.manifest_path = manifest_path
        self.user = user
        self.run_id = normalize_text(manifest.get("run_id"))
        self.marker = normalize_text(manifest.get("marker"))
        self.reference_name = normalize_text(manifest.get("reference_name"))
        self.thread_name = normalize_text(manifest.get("thread_name"))
        transcript = self.manifest.setdefault("http_transcript", [])
        transcript_seq = max(
            (
                int(row.get("seq") or 0)
                for row in transcript
                if isinstance(row, dict)
            ),
            default=0,
        )
        self.client.set_request_observer(
            self.record_http_exchange,
            start_seq=transcript_seq,
        )

    @classmethod
    def new(
        cls,
        client: FrappeClient,
        config: SmokeConfig,
        user: str,
        state_dir: Path,
    ) -> "SmokeRunner":
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        run_id = f"MISMK-{timestamp}-{uuid4().hex[:6]}"
        marker = f"[MISMK:{run_id}]"
        state_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = state_dir / f"{run_id}.json"
        manifest = {
            "schema_version": 1,
            "environment": "test",
            "host": config.host,
            "user_sha256": digest(user.lower()),
            "run_id": run_id,
            "marker": marker,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "initializing",
            "resources": [],
            "checks": [],
            "warnings": [],
            "http_transcript": [],
            "seen_state_observations": [],
            "contains_secrets": False,
        }
        runner = cls(
            client,
            config,
            manifest=manifest,
            manifest_path=manifest_path,
            user=user,
        )
        runner.save_manifest()
        return runner

    def save_manifest(self) -> None:
        self.manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
        temporary = self.manifest_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(self.manifest, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        temporary.replace(self.manifest_path)

    def record_http_exchange(self, entry: dict[str, Any]) -> None:
        transcript = self.manifest.setdefault("http_transcript", [])
        if len(transcript) >= MAX_HTTP_TRANSCRIPT_ENTRIES:
            if not self.manifest.get("http_transcript_truncated"):
                self.manifest["http_transcript_truncated"] = True
                self.save_manifest()
            return
        persisted = dict(entry)
        persisted["run_status"] = self.manifest.get("status")
        transcript.append(persisted)
        self.save_manifest()

    def record_seen_observation(
        self,
        *,
        label: str,
        source: str,
        expected_event_key: str,
        attempt: int,
        item: dict[str, Any] | None = None,
        list_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        observations = self.manifest.setdefault("seen_state_observations", [])
        ensure(
            len(observations) < MAX_SEEN_OBSERVATIONS,
            "تجاوز transcript حالة القراءة الحد الآمن",
        )
        listed_item = (
            find_item(list_payload, self.thread_name)
            if isinstance(list_payload, dict)
            else None
        )
        observed_item = listed_item if list_payload is not None else item
        observation = {
            "seq": len(observations) + 1,
            "observed_at_utc": datetime.now(timezone.utc).isoformat(),
            "http_seq": self.client.last_request_seq,
            "label": label,
            "source": source,
            "attempt": attempt,
            "expected_event_key": expected_event_key,
            "listed_in_unread": bool(listed_item) if list_payload is not None else None,
            "item": mention_state(observed_item),
        }
        if list_payload is not None:
            response_search = "" if list_payload.get("search") is None else str(
                list_payload.get("search")
            )
            observation["list_bucket"] = list_payload.get("bucket")
            observation["list_response_search"] = {
                "length": len(response_search),
                "sha256": digest(response_search),
            }
            observation["list_total"] = list_payload.get("total")
            observation["list_count_unread"] = (list_payload.get("counts") or {}).get(
                "unread"
            )
        observations.append(observation)
        # Persist before validation or sleeping so the first discrepant response is retained.
        self.save_manifest()
        return observed_item

    def check(self, title: str, detail: str = "تم") -> None:
        self.manifest.setdefault("checks", []).append(
            {
                "title": title,
                "detail": detail,
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        self.save_manifest()
        print(f"  [OK] {title}: {detail}")

    def warn(self, message: str) -> None:
        self.manifest.setdefault("warnings", []).append(message)
        self.save_manifest()
        print(f"  [WARN] {message}")

    def record_resource(
        self,
        doctype: str,
        name: str,
        *,
        role: str,
        fingerprints: list[dict[str, str]],
    ) -> None:
        ensure(doctype in ALLOWED_RESOURCE_TYPES, f"نوع fixture غير مسموح: {doctype}")
        ensure(name, f"سجل {role} بلا اسم")
        ensure(fingerprints, f"سجل {role} بلا بصمة")
        normalized_fingerprints = validate_resource_fingerprints(
            fingerprints,
            marker=self.marker,
        )
        resources = self.manifest.setdefault("resources", [])
        if any(row.get("doctype") == doctype and row.get("name") == name for row in resources):
            return
        ensure(len(resources) < MAX_RESOURCES, "تجاوز عدد fixtures الحد الآمن")
        resources.append(
            {
                "doctype": doctype,
                "name": name,
                "role": role,
                "fingerprints": normalized_fingerprints,
            }
        )
        self.save_manifest()

    def mentions(
        self,
        bucket: str,
        *,
        page_length: int = 25,
        http_method: str = "POST",
        search: str | None = None,
        request_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        payload = self.client.call(
            "get_mentions",
            http_method=http_method,
            args={
                "bucket": bucket,
                "search": self.run_id if search is None else search,
                "limit_start": 0,
                "page_length": page_length,
            },
            request_headers=request_headers,
        )
        if not isinstance(payload, dict):
            raise SmokeFailure("get_mentions لم يرجع كائنًا")
        return payload

    def detail(self, thread_name: str) -> dict[str, Any]:
        payload = self.client.call(
            "get_mention_detail",
            http_method="POST",
            args={"thread_name": thread_name},
        )
        if not isinstance(payload, dict):
            raise SmokeFailure("get_mention_detail لم يرجع كائنًا")
        return payload

    def matching_thread_items(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for bucket in ("open", "converted", "closed"):
            for row in response_items(self.mentions(bucket)):
                name = normalize_text(row.get("name"))
                if not name or name in seen:
                    continue
                if (
                    row.get("reference_doctype") == "ToDo"
                    and normalize_text(row.get("reference_name")) == self.reference_name
                ):
                    items.append(row)
                    seen.add(name)
        return items

    def find_thread_item(self, thread_name: str) -> dict[str, Any] | None:
        return next(
            (
                row
                for row in self.matching_thread_items()
                if normalize_text(row.get("name")) == thread_name
            ),
            None,
        )

    def thread_doc_for_cleanup(self, thread_name: str) -> dict[str, Any] | None:
        item = self.find_thread_item(thread_name)
        if item:
            return item
        try:
            return detail_mention(self.detail(thread_name))
        except HttpFailure as exc:
            ensure(
                exc.status_code in {403, 404, 417},
                f"فشل غير متوقع عند فحص Thread قبل التنظيف: {exc}",
            )
            return None

    def assert_thread_absent(self, thread_name: str) -> None:
        ensure(
            not self.find_thread_item(thread_name),
            f"Thread المحذوفة ما زالت ظاهرة في get_mentions: {thread_name}",
        )
        try:
            self.detail(thread_name)
        except HttpFailure as exc:
            ensure(
                exc.status_code in {403, 404, 417},
                f"فشل غير متوقع عند التحقق من حذف Thread: {exc}",
            )
            return
        raise SmokeFailure(f"Thread المحذوفة ما زالت متاحة في get_mention_detail: {thread_name}")

    def assert_owned_thread(self, name: str | None = None) -> dict[str, Any]:
        thread_name = normalize_text(name or self.thread_name)
        ensure(thread_name, "لا يوجد اسم Thread في manifest")
        doc = detail_mention(self.detail(thread_name))
        ensure(doc.get("for_user") == self.user, "رفض المتابعة: Thread لا تخص مستخدم التوكن")
        ensure(doc.get("reference_doctype") == "ToDo", "رفض المتابعة: مرجع Thread ليس ToDo")
        ensure(
            normalize_text(doc.get("reference_name")) == self.reference_name,
            "رفض المتابعة: Thread لا ترتبط بمرجع التشغيل",
        )
        ensure(
            self.marker in normalize_text(doc.get("latest_preview_plain")),
            "رفض المتابعة: بصمة التشغيل غير موجودة في Thread",
        )
        return doc

    def create_isolated_reference_and_self_mention(self) -> None:
        today = date.today().isoformat()
        todo = self.client.create_doc(
            "ToDo",
            {
                "description": f"{self.marker} MENTION-REFERENCE",
                "status": "Open",
                "priority": "Low",
                "date": today,
                "allocated_to": self.user,
                "assigned_by": self.user,
            },
        )
        self.reference_name = normalize_text(todo.get("name"))
        ensure(self.reference_name, "تعذر إنشاء ToDo المرجعي")
        self.manifest["reference_name"] = self.reference_name
        self.record_resource(
            "ToDo",
            self.reference_name,
            role="REFERENCE",
            fingerprints=[{"field": "description", "contains": self.marker}],
        )
        self.check("إنشاء ToDo مرجعي معزول", self.reference_name)

        escaped_user = html.escape(self.user, quote=True)
        content = (
            f"<p>{html.escape(self.marker)} SELF-MENTION "
            f'<span class="mention" data-id="{escaped_user}" data-value="{escaped_user}">'
            f"@{escaped_user}</span></p>"
        )
        comment = self.client.create_doc(
            "Comment",
            {
                "comment_type": "Comment",
                "reference_doctype": "ToDo",
                "reference_name": self.reference_name,
                "comment_email": self.user,
                "content": content,
            },
        )
        comment_name = normalize_text(comment.get("name"))
        ensure(comment_name, "تعذر إنشاء تعليق self-mention")
        self.record_resource(
            "Comment",
            comment_name,
            role="SELF-MENTION-COMMENT",
            fingerprints=[
                {"field": "content", "contains": self.marker},
                {"field": "reference_name", "equals": self.reference_name},
            ],
        )
        self.manifest["source_comment"] = comment_name
        self.save_manifest()
        self.check("إنشاء self-mention بلا موظف خارجي", comment_name)

    def create_additional_self_mention(self, label: str) -> str:
        escaped_user = html.escape(self.user, quote=True)
        content = (
            f"<p>{html.escape(self.marker)} {html.escape(label)} "
            f'<span class="mention" data-id="{escaped_user}" data-value="{escaped_user}">'
            f"@{escaped_user}</span></p>"
        )
        comment = self.client.create_doc(
            "Comment",
            {
                "comment_type": "Comment",
                "reference_doctype": "ToDo",
                "reference_name": self.reference_name,
                "comment_email": self.user,
                "content": content,
            },
        )
        comment_name = normalize_text(comment.get("name"))
        ensure(comment_name, "تعذر إنشاء تعليق self-mention الإضافي")
        self.record_resource(
            "Comment",
            comment_name,
            role="STALE-VERSION-SELF-MENTION",
            fingerprints=[
                {"field": "content", "contains": self.marker},
                {"field": "reference_name", "equals": self.reference_name},
            ],
        )
        return comment_name

    def current_event_key(self) -> str:
        key = normalize_text(self.assert_owned_thread().get("last_event_key"))
        ensure(
            len(key) == 64 and all(char in "0123456789abcdef" for char in key.lower()),
            "last_event_key في Thread غير صالح",
        )
        return key

    def wait_for_event_change(self, previous_key: str) -> str:
        for attempt in range(1, POLL_ATTEMPTS + 1):
            current_key = self.current_event_key()
            if current_key != previous_key:
                self.check("تحديث last_event_key من self-mention ثانٍ", f"attempt={attempt}")
                return current_key
            if attempt < POLL_ATTEMPTS:
                time.sleep(POLL_INTERVAL_SECONDS)
        raise SmokeFailure("لم يتغير last_event_key ضمن مهلة polling لاختبار stale token")

    def assert_observed_event_key(
        self,
        item: dict[str, Any] | None,
        expected_event_key: str,
        *,
        label: str,
    ) -> None:
        if not item:
            return
        current_event_key = normalize_text(item.get("last_event_key"))
        if current_event_key != expected_event_key:
            raise SmokeFailure(
                "وصل حدث Mention جديد أثناء التحقق من القراءة؛ "
                f"source={label} expected={expected_event_key} current={current_event_key}"
            )

    def seen_state_is_consistent(
        self,
        detail_item: dict[str, Any],
        unread_payload: dict[str, Any],
        expected_event_key: str,
    ) -> bool:
        return bool(
            normalize_text(detail_item.get("last_event_key")) == expected_event_key
            and normalize_text(detail_item.get("last_seen_event_key"))
            == expected_event_key
            and not bool(detail_item.get("unread"))
            and not find_item(unread_payload, self.thread_name)
        )

    def unread_bucket_invariant_broken(self, payload: dict[str, Any]) -> bool:
        item = find_item(payload, self.thread_name)
        if not item:
            return False
        last_event_key = normalize_text(item.get("last_event_key"))
        last_seen_event_key = normalize_text(item.get("last_seen_event_key"))
        return bool(
            last_event_key
            and last_event_key == last_seen_event_key
            and not bool(item.get("unread"))
        )

    def observe_seen_detail(
        self,
        *,
        label: str,
        expected_event_key: str,
        attempt: int,
    ) -> dict[str, Any]:
        detail_item = detail_mention(self.detail(self.thread_name))
        self.record_seen_observation(
            label=label,
            source="detail",
            expected_event_key=expected_event_key,
            attempt=attempt,
            item=detail_item,
        )
        self.assert_observed_event_key(
            detail_item,
            expected_event_key,
            label=label,
        )
        return detail_item

    def observe_unread_list(
        self,
        *,
        label: str,
        expected_event_key: str,
        attempt: int,
        http_method: str = "POST",
        search: str | None = None,
        request_headers: dict[str, str] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        payload = self.mentions(
            "unread",
            http_method=http_method,
            search=search,
            request_headers=request_headers,
        )
        item = self.record_seen_observation(
            label=label,
            source="unread_list",
            expected_event_key=expected_event_key,
            attempt=attempt,
            list_payload=payload,
        )
        self.assert_observed_event_key(item, expected_event_key, label=label)
        return payload, item

    def wait_for_seen_state(self, expected_event_key: str) -> None:
        # First take three equivalent list reads with deliberately different HTTP cache keys.
        detail_item = self.observe_seen_detail(
            label="post_mark_post_detail",
            expected_event_key=expected_event_key,
            attempt=0,
        )

        exact_get, exact_item = self.observe_unread_list(
            label="post_mark_get_unread_exact",
            expected_event_key=expected_event_key,
            attempt=0,
            http_method="GET",
        )

        padded_get, padded_item = self.observe_unread_list(
            label="post_mark_get_unread_padded_no_cache",
            expected_event_key=expected_event_key,
            attempt=0,
            http_method="GET",
            search=f"{self.run_id} ",
            request_headers={"Cache-Control": "no-cache, no-store", "Pragma": "no-cache"},
        )

        post_list, post_item = self.observe_unread_list(
            label="post_mark_post_unread",
            expected_event_key=expected_event_key,
            attempt=0,
        )

        variants = {
            "get_exact": (exact_get, exact_item),
            "get_padded_no_cache": (padded_get, padded_item),
            "post": (post_list, post_item),
        }
        broken_variants = [
            label
            for label, (payload, _) in variants.items()
            if self.unread_bucket_invariant_broken(payload)
        ]
        self.manifest["seen_read_comparison"] = {
            "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
            "detail": mention_state(detail_item),
            "listed": {label: bool(item) for label, (_, item) in variants.items()},
            "items": {label: mention_state(item) for label, (_, item) in variants.items()},
            "broken_variants": broken_variants,
        }
        self.save_manifest()
        if broken_variants:
            raise SmokeFailure(
                "get_mentions(bucket=unread) أعاد عنصرًا unread=false ومفاتيحه متساوية؛ "
                f"variants={','.join(broken_variants)}"
            )
        if self.seen_state_is_consistent(detail_item, post_list, expected_event_key):
            self.check("mark_mention_seen", "attempt=0")
            return

        last_state: dict[str, Any] = {}
        for attempt in range(1, POLL_ATTEMPTS + 1):
            detail_item = self.observe_seen_detail(
                label="poll_post_detail",
                expected_event_key=expected_event_key,
                attempt=attempt,
            )

            unread_payload, unread_item = self.observe_unread_list(
                label="poll_post_unread",
                expected_event_key=expected_event_key,
                attempt=attempt,
            )
            if self.unread_bucket_invariant_broken(unread_payload):
                raise SmokeFailure(
                    "get_mentions(bucket=unread) كسر invariant أثناء polling: "
                    "العنصر unread=false ومفاتيحه متساوية"
                )

            last_state = {
                "detail": mention_state(detail_item),
                "listed_in_unread": bool(unread_item),
                "unread_list_item": mention_state(unread_item),
                "unread_list_total": unread_payload.get("total"),
                "unread_list_count": (unread_payload.get("counts") or {}).get(
                    "unread"
                ),
            }
            if self.seen_state_is_consistent(
                detail_item,
                unread_payload,
                expected_event_key,
            ):
                self.check("mark_mention_seen", f"attempt={attempt}")
                return
            if attempt < POLL_ATTEMPTS:
                time.sleep(POLL_INTERVAL_SECONDS)
        raise SmokeFailure(
            "لم تتسق حالة القراءة ضمن مهلة polling: "
            + json.dumps(last_state, ensure_ascii=False, sort_keys=True)
        )

    def marker_comment_names(self) -> set[str]:
        rows = self.client.list_docs(
            "Comment",
            fields=["name", "content"],
            filters=[
                ["reference_doctype", "=", "ToDo"],
                ["reference_name", "=", self.reference_name],
                ["content", "like", f"%{self.run_id}%"],
            ],
            limit=MAX_RESOURCES,
        )
        return {
            normalize_text(row.get("name"))
            for row in rows
            if self.marker in normalize_text(row.get("content")) and row.get("name")
        }

    def marker_todo_names(self) -> set[str]:
        rows = self.client.list_docs(
            "ToDo",
            fields=["name", "description"],
            filters=[["description", "like", f"%{self.run_id}%"]],
            limit=MAX_RESOURCES,
        )
        return {
            normalize_text(row.get("name"))
            for row in rows
            if self.marker in normalize_text(row.get("description")) and row.get("name")
        }

    def expect_stale_rejection(self, method_name: str, args: dict[str, Any]) -> None:
        try:
            self.client.call(method_name, http_method="POST", args=args)
        except HttpFailure as exc:
            ensure(400 <= exc.status_code < 500, f"{method_name} stale رجع خطأ خادم: {exc}")
            ensure(
                "تم تحديث هذه الإشارة" in exc.server_message,
                f"{method_name} لم يرجع رسالة stale المتوقعة: {exc.server_message}",
            )
            return
        raise SmokeFailure(f"{method_name} قبل stale token بدل رفض الطلب")

    def run_stale_version_guard(self) -> None:
        stale_key = self.current_event_key()
        self.create_additional_self_mention("STALE-VERSION")
        current_key = self.wait_for_event_change(stale_key)
        before = self.assert_owned_thread()
        before_comments = self.marker_comment_names()
        before_todos = self.marker_todo_names()
        stale_common = {
            "thread_name": self.thread_name,
            "expected_last_event_key": stale_key,
        }
        self.expect_stale_rejection(
            "mark_mention_seen",
            {**stale_common, "seen": 1},
        )
        self.expect_stale_rejection(
            "reply_mention",
            {
                **stale_common,
                "reply": f"{self.marker} STALE-REPLY",
                "request_id": f"{self.run_id}-STALE-REPLY",
            },
        )
        self.expect_stale_rejection(
            "reply_and_close",
            {
                **stale_common,
                "reply": f"{self.marker} STALE-REPLY-CLOSE",
                "request_id": f"{self.run_id}-STALE-REPLY-CLOSE",
            },
        )
        self.expect_stale_rejection("close_mention", stale_common)
        self.expect_stale_rejection("reopen_mention", stale_common)
        self.expect_stale_rejection(
            "convert_mention_to_followup",
            {
                **stale_common,
                "due_date": (date.today() + timedelta(days=1)).isoformat(),
                "priority": "Low",
                "description": f"{self.marker} STALE-CONVERT",
            },
        )
        after = self.assert_owned_thread()
        ensure(after.get("last_event_key") == current_key, "stale غيّر last_event_key")
        ensure(after.get("status") == before.get("status"), "stale غيّر حالة Thread")
        ensure(
            after.get("last_seen_event_key") == before.get("last_seen_event_key"),
            "stale غيّر حالة القراءة",
        )
        ensure(self.marker_comment_names() == before_comments, "stale أنشأ Comment")
        ensure(self.marker_todo_names() == before_todos, "stale أنشأ ToDo")
        self.check("stale token مرفوض بلا حالة/seen/Comment/ToDo")

    def wait_for_thread(self) -> dict[str, Any]:
        for attempt in range(1, POLL_ATTEMPTS + 1):
            payload = self.mentions("open")
            candidates = [
                row
                for row in response_items(payload)
                if row.get("reference_doctype") == "ToDo"
                and normalize_text(row.get("reference_name")) == self.reference_name
            ]
            if candidates:
                item = candidates[0]
                self.thread_name = normalize_text(item.get("name"))
                ensure(self.thread_name, "عنصر الوارد بلا اسم")
                thread = self.assert_owned_thread(self.thread_name)
                self.manifest["thread_name"] = self.thread_name
                self.record_resource(
                    THREAD_DOCTYPE,
                    self.thread_name,
                    role="MENTION-THREAD",
                    fingerprints=[
                        {"field": "for_user", "equals_sha256": digest(self.user.lower())},
                        {"field": "reference_name", "equals": self.reference_name},
                        {"field": "latest_preview_plain", "contains": self.marker},
                    ],
                )
                self.check("معالجة queue وإنشاء Thread", f"attempt={attempt}")
                return thread
            if attempt < POLL_ATTEMPTS:
                time.sleep(POLL_INTERVAL_SECONDS)
        raise SmokeFailure(
            "لم تُنشأ Thread من self-mention ضمن مهلة polling؛ سيتوقف الاختبار وينظف fixtures"
        )

    def record_marker_comments(self) -> None:
        if not self.reference_name:
            return
        rows = self.client.list_docs(
            "Comment",
            fields=["name", "content", "reference_doctype", "reference_name"],
            filters=[
                ["reference_doctype", "=", "ToDo"],
                ["reference_name", "=", self.reference_name],
                ["content", "like", f"%{self.run_id}%"],
            ],
            limit=MAX_RESOURCES,
        )
        for row in rows:
            name = normalize_text(row.get("name"))
            if not name or self.marker not in normalize_text(row.get("content")):
                continue
            self.record_resource(
                "Comment",
                name,
                role="DISCOVERED-COMMENT",
                fingerprints=[
                    {"field": "content", "contains": self.marker},
                    {"field": "reference_name", "equals": self.reference_name},
                ],
            )

    def record_marker_notifications(self) -> None:
        if not self.reference_name:
            return
        rows = self.client.list_docs(
            "Notification Log",
            fields=[
                "name",
                "type",
                "for_user",
                "document_type",
                "document_name",
                "email_content",
                "subject",
                "link",
            ],
            filters=[
                ["type", "=", "Mention"],
                ["for_user", "=", self.user],
                ["document_type", "=", "ToDo"],
                ["document_name", "=", self.reference_name],
            ],
            limit=MAX_RESOURCES,
        )
        for row in rows:
            searchable = " ".join(
                [normalize_text(row.get("email_content")), normalize_text(row.get("subject"))]
            )
            if self.marker not in searchable:
                continue
            name = normalize_text(row.get("name"))
            marker_field = (
                "email_content"
                if self.marker in normalize_text(row.get("email_content"))
                else "subject"
            )
            self.record_resource(
                "Notification Log",
                name,
                role="SELF-MENTION-NOTIFICATION",
                fingerprints=[
                    {"field": marker_field, "contains": self.marker},
                    {"field": "for_user", "equals_sha256": digest(self.user.lower())},
                    {"field": "document_name", "equals": self.reference_name},
                ],
            )

    def check_optional_notification_link(self) -> dict[str, Any] | None:
        expected_link = f"/app/my-followups?source=mentions&thread={self.thread_name}"
        rows = self.client.list_docs(
            "Notification Log",
            fields=[
                "name",
                "type",
                "for_user",
                "document_type",
                "document_name",
                "email_content",
                "subject",
                "link",
            ],
            filters=[
                ["type", "=", "Mention"],
                ["for_user", "=", self.user],
                ["document_type", "=", "ToDo"],
                ["document_name", "=", self.reference_name],
            ],
            limit=10,
        )
        for row in rows:
            searchable = " ".join(
                [normalize_text(row.get("email_content")), normalize_text(row.get("subject"))]
            )
            if self.marker not in searchable:
                continue
            link = normalize_text(row.get("link"))
            ensure(
                link == expected_link or link.endswith(expected_link),
                "Notification Log الذاتية لا تشير إلى سلسلة الوارد الصحيحة",
            )
            marker_field = (
                "email_content"
                if self.marker in normalize_text(row.get("email_content"))
                else "subject"
            )
            self.record_resource(
                "Notification Log",
                normalize_text(row.get("name")),
                role="SELF-MENTION-NOTIFICATION",
                fingerprints=[
                    {"field": marker_field, "contains": self.marker},
                    {"field": "for_user", "equals_sha256": digest(self.user.lower())},
                    {"field": "document_name", "equals": self.reference_name},
                ],
            )
            self.check("Notification Log الاختيارية تربط self-mention بالوارد")
            return row
        self.warn("لا توجد Notification Log ذاتية؛ هذا متوقع من سلوك Frappe القياسي")
        return None

    def run_optional_self_reply(self) -> None:
        if not self.config.include_self_reply:
            self.warn("تجاوز reply_mention/reply_and_close؛ استخدم --include-self-reply لاختبار منشن ذاتي معزول")
            return

        expected_event_key = self.current_event_key()
        request_id = f"{self.run_id}-REPLY"
        reply = f"{self.marker} SELF-REPLY @{self.user}"
        escaped_user = html.escape(self.user, quote=True)
        reply_html = (
            f"<p>{html.escape(self.marker)} SELF-REPLY "
            f'<span class="mention" data-id="{escaped_user}" '
            f'data-value="{escaped_user}" data-is-group="false">'
            f"@{escaped_user}</span></p>"
        )
        first_response = self.client.call(
            "reply_mention",
            http_method="POST",
            args={
                "thread_name": self.thread_name,
                "reply": reply,
                "reply_html": reply_html,
                "request_id": request_id,
                "expected_last_event_key": expected_event_key,
            },
        )
        ensure(isinstance(first_response, dict), "reply_mention لم يرجع كائنًا")
        first_reply = first_response.get("reply") or {}
        first_comment = first_response.get("comment") or {}
        ensure(first_reply.get("request_id") == request_id, "reply_mention فقد request_id")
        ensure(first_reply.get("event_type") == "Reply", "reply_mention لم ينشئ حدث Reply")
        first_comment_name = normalize_text(first_comment.get("name"))
        ensure(first_comment_name, "reply_mention لم يرجع Comment")
        stored_comment = self.client.get_doc("Comment", first_comment_name)
        ensure(stored_comment, "تعذر قراءة Comment الناتجة من الرد الذاتي")
        stored_content = normalize_text(stored_comment.get("content"))
        ensure(
            f'data-id="{escaped_user}"' in stored_content,
            "Comment الناتجة لا تحتوي منشن مستخدم التوكن",
        )
        ensure(
            'data-is-group="false"' in stored_content,
            "Comment الناتجة لم تثبت أن المنشن لمستخدم وليس مجموعة",
        )

        repeated_response = self.client.call(
            "reply_mention",
            http_method="POST",
            args={
                "thread_name": self.thread_name,
                "reply": reply,
                "reply_html": reply_html,
                "request_id": request_id,
                "expected_last_event_key": expected_event_key,
            },
        )
        ensure(isinstance(repeated_response, dict), "إعادة reply_mention لم ترجع كائنًا")
        repeated_reply = repeated_response.get("reply") or {}
        repeated_comment = repeated_response.get("comment") or {}
        ensure(
            repeated_reply.get("event_key") == first_reply.get("event_key"),
            "idempotency أنشأت event_key ثانية",
        )
        ensure(
            repeated_comment.get("name") == first_comment.get("name"),
            "idempotency أنشأت Comment ثانية",
        )
        detail = self.detail(self.thread_name)
        messages = detail.get("messages") or []
        ensure(
            sum(1 for row in messages if row.get("request_id") == request_id) == 1,
            "reply_mention idempotent يجب أن يظهر مرة واحدة فقط",
        )
        comments_before_mismatch = self.marker_comment_names()
        try:
            self.client.call(
                "reply_mention",
                http_method="POST",
                args={
                    "thread_name": self.thread_name,
                    "reply": reply,
                    "reply_html": f"<p>{html.escape(self.marker)} SELF-REPLY</p>",
                    "request_id": request_id,
                    "expected_last_event_key": expected_event_key,
                },
            )
        except HttpFailure as exc:
            ensure(
                exc.status_code == 417 and "مستلمين مختلفين" in exc.server_message,
                f"رفض اختلاف المستلمين لم يكن ValidationError المتوقع: {exc}",
            )
        else:
            raise SmokeFailure("قُبل request_id نفسه مع مستلمين مختلفين")
        ensure(
            self.marker_comment_names() == comments_before_mismatch,
            "اختلاف مستلمي idempotency أنشأ Comment إضافية",
        )
        self.check("reply_mention بمنشن ذاتي + idempotency + recipient mismatch", request_id)

        close_request_id = f"{self.run_id}-REPLY-CLOSE"
        close_reply = f"{self.marker} SELF-REPLY-CLOSE بلا mention"
        close_response = self.client.call(
            "reply_and_close",
            http_method="POST",
            args={
                "thread_name": self.thread_name,
                "reply": close_reply,
                "request_id": close_request_id,
                "expected_last_event_key": self.current_event_key(),
            },
        )
        ensure(isinstance(close_response, dict), "reply_and_close لم يرجع كائنًا")
        ensure(
            (close_response.get("reply") or {}).get("request_id") == close_request_id,
            "reply_and_close فقد request_id",
        )
        ensure(
            (close_response.get("mention") or {}).get("status") == "Closed",
            "reply_and_close لم يغلق Thread",
        )
        ensure(
            detail_mention(self.detail(self.thread_name)).get("status") == "Closed",
            "حالة Thread بعد reply_and_close ليست Closed",
        )
        self.record_marker_comments()
        self.check("reply_and_close ذاتي", close_request_id)

        self.client.call(
            "reopen_mention",
            http_method="POST",
            args={
                "thread_name": self.thread_name,
                "expected_last_event_key": self.current_event_key(),
            },
        )
        ensure(
            detail_mention(self.detail(self.thread_name)).get("status") == "Open",
            "تعذر إعادة الفتح بعد reply_and_close",
        )
        self.check("reopen بعد الرد والإغلاق")

    def run(self) -> dict[str, Any]:
        self.manifest["status"] = "running"
        self.save_manifest()
        print(f"\n=== Smoke صندوق الإشارات: {self.run_id} ===")
        print(f"الموقع التجريبي: {self.config.host}")

        initial = self.mentions("open", page_length=5)
        ensure(not response_items(initial), "توجد Thread سابقة تحمل RUN_ID نفسه")
        ensure(isinstance(initial.get("counts") or {}, dict), "get_mentions لا يعيد counts")
        self.check("عقد get_mentions والبداية النظيفة")

        self.create_isolated_reference_and_self_mention()
        self.wait_for_thread()
        candidates = self.client.call(
            "search_reply_mentions",
            http_method="POST",
            args={"thread_name": self.thread_name, "search_term": ""},
        )
        ensure(isinstance(candidates, list), "search_reply_mentions لم ترجع قائمة")
        ensure(len(candidates) <= 8, "search_reply_mentions تجاوزت الحد الآمن")
        ensure(
            all(
                isinstance(row, dict)
                and set(row) == {"id", "value", "is_group"}
                and row.get("is_group") is False
                for row in candidates
            ),
            "search_reply_mentions أعادت نتيجة مجموعة أو عقدًا غير متوقع",
        )
        self.check("search_reply_mentions محدودة وبلا مجموعات")
        self.check_optional_notification_link()
        self.run_stale_version_guard()

        open_payload = self.mentions("open")
        # Prime the exact GET cache key while the fixture is still unread.
        unread_payload = self.mentions("unread", http_method="GET")
        open_item = find_item(open_payload, self.thread_name)
        unread_item = find_item(unread_payload, self.thread_name)
        self.record_seen_observation(
            label="pre_mark_get_unread_exact",
            source="unread_list",
            expected_event_key=normalize_text((unread_item or {}).get("last_event_key")),
            attempt=0,
            list_payload=unread_payload,
        )
        ensure(open_item, "Thread لا تظهر في bucket=open")
        ensure(unread_item, "Thread الجديدة لا تظهر في bucket=unread")
        ensure(bool(open_item.get("unread")), "Thread الجديدة ليست غير مقروءة")
        ensure(int(open_item.get("mention_count") or 0) >= 1, "mention_count غير صحيح")
        ensure(open_item.get("reference_doctype") == "ToDo", "نوع المرجع غير صحيح")
        ensure(open_item.get("reference_name") == self.reference_name, "اسم المرجع غير صحيح")
        ensure(open_item.get("latest_preview_plain") is not None, "latest_preview_plain غير موجود")
        self.check("list/open + unread", self.thread_name)

        detail_payload = self.detail(self.thread_name)
        mention = detail_mention(detail_payload)
        messages = detail_payload.get("messages") or []
        permissions = detail_payload.get("permissions") or {}
        ensure(mention.get("name") == self.thread_name, "تفاصيل Thread لا تطابق الاسم")
        ensure(mention.get("reference_name") == self.reference_name, "تفاصيل Thread فقدت المرجع")
        ensure(messages and isinstance(messages, list), "تفاصيل Thread بلا messages")
        ensure(
            any(self.marker in normalize_text(row.get("content_plain")) for row in messages),
            "messages لا تحمل بصمة self-mention",
        )
        for permission in ("can_reply", "can_close", "can_convert"):
            ensure(permissions.get(permission) is True, f"الصلاحية {permission} ليست مفعلة")
        self.check("detail + messages + permissions")

        expected_event_key = normalize_text(mention.get("last_event_key"))
        ensure(
            len(expected_event_key) == 64
            and all(char in "0123456789abcdef" for char in expected_event_key.lower()),
            "last_event_key المعروض في التفاصيل غير صالح",
        )
        self.record_seen_observation(
            label="pre_mark_post_detail",
            source="detail",
            expected_event_key=expected_event_key,
            attempt=0,
            item=mention,
        )
        seen_response = self.client.call(
            "mark_mention_seen",
            http_method="POST",
            args={
                "thread_name": self.thread_name,
                "seen": 1,
                "expected_last_event_key": expected_event_key,
            },
        )
        ensure(isinstance(seen_response, dict), "mark_mention_seen لم يرجع كائنًا")
        seen_mention = seen_response.get("mention") or {}
        self.record_seen_observation(
            label="mark_once_response",
            source="mark_response",
            expected_event_key=expected_event_key,
            attempt=0,
            item=seen_mention,
        )
        ensure(
            normalize_text(seen_mention.get("last_event_key")) == expected_event_key,
            "mark_mention_seen غيّر last_event_key",
        )
        ensure(
            normalize_text(seen_mention.get("last_seen_event_key")) == expected_event_key,
            "mark_mention_seen لم يحفظ last_seen_event_key",
        )
        ensure(not bool(seen_mention.get("unread")), "mark_mention_seen لم يحدّث unread في الاستجابة")
        self.wait_for_seen_state(expected_event_key)

        self.run_optional_self_reply()

        expected_event_key = self.current_event_key()
        self.client.call(
            "close_mention",
            http_method="POST",
            args={
                "thread_name": self.thread_name,
                "expected_last_event_key": expected_event_key,
            },
        )
        closed_detail = detail_mention(self.detail(self.thread_name))
        ensure(closed_detail.get("status") == "Closed", "close_mention لم يغلق Thread")
        ensure(find_item(self.mentions("closed"), self.thread_name), "Thread لا تظهر في bucket=closed")
        ensure(not find_item(self.mentions("open"), self.thread_name), "Thread المغلقة بقيت في open")
        closed_permissions = (self.detail(self.thread_name).get("permissions") or {})
        ensure(closed_permissions.get("can_reopen") is True, "can_reopen غير مفعلة بعد الإغلاق")
        self.check("close_mention")

        expected_event_key = self.current_event_key()
        self.client.call(
            "reopen_mention",
            http_method="POST",
            args={
                "thread_name": self.thread_name,
                "expected_last_event_key": expected_event_key,
            },
        )
        reopened = detail_mention(self.detail(self.thread_name))
        ensure(reopened.get("status") == "Open", "reopen_mention لم يعد Thread إلى Open")
        ensure(find_item(self.mentions("open"), self.thread_name), "Thread المعاد فتحها لا تظهر في open")
        self.check("reopen_mention")

        expected_event_key = self.current_event_key()
        due_date = (date.today() + timedelta(days=1)).isoformat()
        converted = self.client.call(
            "convert_mention_to_followup",
            http_method="POST",
            args={
                "thread_name": self.thread_name,
                "due_date": due_date,
                "priority": "Low",
                "description": f"{self.marker} CONVERTED-FOLLOWUP",
                "expected_last_event_key": expected_event_key,
            },
        )
        ensure(isinstance(converted, dict), "convert_mention_to_followup لم يرجع كائنًا")
        followup = converted.get("followup") or {}
        todo_name = normalize_text(followup.get("name"))
        ensure(todo_name, "التحويل لم يرجع اسم ToDo")
        todo = self.client.get_doc("ToDo", todo_name)
        ensure(todo, "ToDo المحولة غير قابلة للقراءة")
        ensure(self.marker in normalize_text(todo.get("description")), "ToDo المحولة لا تحمل البصمة")
        ensure(todo.get("allocated_to") == self.user, "ToDo المحولة ليست لمستخدم التوكن")
        ensure(todo.get("reference_type") == "ToDo", "نوع مرجع ToDo المحولة غير صحيح")
        ensure(todo.get("reference_name") == self.reference_name, "مرجع ToDo المحولة غير صحيح")
        ensure(todo.get("date") == due_date, "تاريخ ToDo المحولة غير صحيح")
        self.record_resource(
            "ToDo",
            todo_name,
            role="CONVERTED-FOLLOWUP",
            fingerprints=[
                {"field": "description", "contains": self.marker},
                {"field": "reference_name", "equals": self.reference_name},
            ],
        )
        converted_detail = detail_mention(self.detail(self.thread_name))
        ensure(converted_detail.get("status") == "Converted", "Thread لم تصبح Converted")
        ensure(
            converted_detail.get("converted_to_todo") == todo_name,
            "converted_to_todo لا يطابق ToDo المنشأة",
        )
        ensure(find_item(self.mentions("converted"), self.thread_name), "Thread لا تظهر في converted")
        self.check("convert_mention_to_followup", todo_name)

        self.manifest["status"] = "checks_passed"
        self.save_manifest()
        return {
            "run_id": self.run_id,
            "checks": len(self.manifest.get("checks") or []),
            "warnings": len(self.manifest.get("warnings") or []),
            "manifest": str(self.manifest_path),
        }

    def fingerprint_matches(self, doc: dict[str, Any], fingerprint: dict[str, str]) -> bool:
        fingerprint = validate_fingerprint_shape(fingerprint)
        field = fingerprint["field"]
        value = normalize_text(doc.get(field))
        if "contains" in fingerprint:
            return normalize_text(fingerprint["contains"]) in value
        if "equals" in fingerprint:
            return value == normalize_text(fingerprint["equals"])
        if "equals_sha256" in fingerprint:
            return digest(value.lower()) == normalize_text(fingerprint["equals_sha256"])
        return False

    def verify_resource(self, row: dict[str, Any]) -> dict[str, Any] | None:
        doctype = normalize_text(row.get("doctype"))
        name = normalize_text(row.get("name"))
        ensure(doctype in ALLOWED_RESOURCE_TYPES, f"رفض تنظيف نوع غير مسموح: {doctype}")
        ensure(name, "رفض تنظيف fixture بلا اسم")
        doc = (
            self.thread_doc_for_cleanup(name)
            if doctype == THREAD_DOCTYPE
            else self.client.get_doc(doctype, name)
        )
        if not doc:
            return None
        normalized_fingerprints = validate_resource_fingerprints(
            row.get("fingerprints"),
            marker=self.marker,
        )
        ensure(
            all(self.fingerprint_matches(doc, fp) for fp in normalized_fingerprints),
            f"رفض حذف {doctype} {name}: البصمة الحية لا تطابق manifest",
        )
        return doc

    def discover_resources(self) -> None:
        if not self.reference_name:
            return
        for row in self.matching_thread_items():
            if self.marker not in normalize_text(row.get("latest_preview_plain")):
                continue
            name = normalize_text(row.get("name"))
            self.thread_name = name
            self.manifest["thread_name"] = name
            self.record_resource(
                THREAD_DOCTYPE,
                name,
                role="DISCOVERED-THREAD",
                fingerprints=[
                    {"field": "for_user", "equals_sha256": digest(self.user.lower())},
                    {"field": "reference_name", "equals": self.reference_name},
                    {"field": "latest_preview_plain", "contains": self.marker},
                ],
            )

        todo_rows = self.client.list_docs(
            "ToDo",
            fields=["name", "description", "reference_type", "reference_name", "allocated_to"],
            filters=[["description", "like", f"%{self.run_id}%"]],
            limit=MAX_RESOURCES,
        )
        for row in todo_rows:
            if self.marker not in normalize_text(row.get("description")):
                continue
            name = normalize_text(row.get("name"))
            fingerprints = [{"field": "description", "contains": self.marker}]
            if name != self.reference_name:
                fingerprints.append({"field": "reference_name", "equals": self.reference_name})
            self.record_resource(
                "ToDo",
                name,
                role="DISCOVERED-TODO",
                fingerprints=fingerprints,
            )

        self.record_marker_comments()
        self.record_marker_notifications()
        self.save_manifest()

    def cleanup(self) -> list[str]:
        errors: list[str] = []
        self.manifest["status"] = "cleanup_running"
        self.save_manifest()
        print("\n=== تنظيف fixtures ذات البصمة ===")

        blocked_by_dependency = False
        # تمريرات محدودة تلتقط Thread قد تنشأ من queue بين اكتشاف الموارد
        # وحذف Comment المصدر. بعد حذف Comment لن يقبل worker إنشاء Thread جديدة.
        for cleanup_pass in range(3):
            try:
                self.discover_resources()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"تعذر اكتشاف fixtures في تمريرة {cleanup_pass + 1}: {exc}")
                blocked_by_dependency = True
                break

            resources = list(self.manifest.get("resources") or [])

            def cleanup_rank(row: dict[str, Any]) -> tuple[int, int]:
                role = normalize_text(row.get("role"))
                doctype = normalize_text(row.get("doctype"))
                if doctype == "ToDo" and role != "REFERENCE":
                    rank = 0
                elif doctype == "Comment":
                    rank = 1
                elif doctype == "Notification Log":
                    rank = 2
                elif doctype == THREAD_DOCTYPE:
                    rank = 3
                elif role == "REFERENCE":
                    rank = 4
                else:
                    rank = 0
                return rank, -resources.index(row)

            for row in sorted(resources, key=cleanup_rank):
                doctype = normalize_text(row.get("doctype"))
                name = normalize_text(row.get("name"))
                if blocked_by_dependency:
                    self.warn(f"أُجّل حذف {doctype} {name} لأن fixture أسبق لم تُحذف بأمان")
                    continue
                try:
                    live = self.verify_resource(row)
                    if not live:
                        continue
                    self.client.delete_doc(doctype, name)
                    if doctype == THREAD_DOCTYPE:
                        self.assert_thread_absent(name)
                    print(f"  [OK] حُذف {doctype} المعزول: {name}")
                except HttpFailure as exc:
                    if doctype == "Notification Log" and exc.status_code in {401, 403}:
                        self.warn(
                            "تعذر حذف Notification Log مباشرة؛ سيحذفها Frappe عند حذف ToDo المصدر"
                        )
                        continue
                    errors.append(f"{doctype} {name}: {exc}")
                    blocked_by_dependency = True
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{doctype} {name}: {exc}")
                    blocked_by_dependency = True

            if blocked_by_dependency:
                break
            if cleanup_pass < 2:
                time.sleep(1)

        resources = list(self.manifest.get("resources") or [])
        remaining: list[str] = []
        for attempt in range(POLL_ATTEMPTS):
            remaining = []
            for row in resources:
                doctype = normalize_text(row.get("doctype"))
                name = normalize_text(row.get("name"))
                try:
                    if doctype == THREAD_DOCTYPE:
                        self.assert_thread_absent(name)
                    elif self.client.get_doc(doctype, name):
                        remaining.append(f"{doctype}:{name}")
                except Exception as exc:  # noqa: BLE001
                    remaining.append(f"{doctype}:{name}")
                    if attempt == POLL_ATTEMPTS - 1:
                        errors.append(f"فشل تحقق {doctype} {name}: {exc}")
            if not remaining:
                break
            if attempt < POLL_ATTEMPTS - 1:
                time.sleep(1)
        if remaining:
            errors.append("بقيت fixtures: " + ", ".join(remaining))

        self.manifest["cleanup_errors"] = errors
        self.manifest["cleaned_at"] = datetime.now(timezone.utc).isoformat()
        self.manifest["status"] = "cleanup_failed" if errors else "cleaned"
        self.save_manifest()
        if errors:
            for error in errors:
                print(f"  [FAILED] {error}")
        else:
            print("  [OK] لا توجد سجلات تشغيلية متبقية بأسماء manifest.")
        return errors


def meta_fieldnames(meta_doc: dict[str, Any]) -> set[str]:
    return {
        normalize_text(row.get("fieldname"))
        for row in (meta_doc.get("fields") or [])
        if isinstance(row, dict) and row.get("fieldname")
    }


def preflight_schema_and_permissions(
    client: FrappeClient,
    *,
    user: str,
    cleanup_only: bool = False,
) -> None:
    thread_bundle = client.get_meta_bundle(THREAD_DOCTYPE)
    event_bundle = client.get_meta_bundle(EVENT_DOCTYPE)
    thread_meta = next(
        (doc for doc in thread_bundle if normalize_text(doc.get("name")) == THREAD_DOCTYPE),
        None,
    )
    event_meta = next(
        (doc for doc in event_bundle if normalize_text(doc.get("name")) == EVENT_DOCTYPE),
        None,
    )
    ensure(thread_meta, f"DocType {THREAD_DOCTYPE} غير منشور")
    ensure(event_meta, f"DocType {EVENT_DOCTYPE} المستقل غير منشور")
    missing_thread = sorted(THREAD_REQUIRED_FIELDS - meta_fieldnames(thread_meta))
    missing_event = sorted(EVENT_REQUIRED_FIELDS - meta_fieldnames(event_meta))
    ensure(not missing_thread, "حقول Thread ناقصة: " + ", ".join(missing_thread))
    ensure(not missing_event, "حقول Event ناقصة: " + ", ".join(missing_event))
    ensure(
        "events" not in meta_fieldnames(thread_meta),
        "الـschema قديم: Thread ما زالت تحتوي child table باسم events",
    )
    ensure(
        normalize_text(event_meta.get("istable")).lower() in {"", "0", "false", "none"},
        "الـschema قديم: Namar Mention Event ما زالت Child Table",
    )
    ensure(
        normalize_text(event_meta.get("autoname")) == "field:event_key",
        "autoname لـNamar Mention Event يجب أن يعتمد event_key",
    )
    for internal_meta in (thread_meta, event_meta):
        ensure(
            not any(
                isinstance(row, dict) and row.get("read")
                for row in (internal_meta.get("permissions") or [])
            ),
            f"النوع الداخلي {internal_meta.get('name')} يحتوي DocPerm read",
        )
    ensure(user != "Administrator", "يرفض Smoke Test توكن Administrator")
    for internal_doctype in (THREAD_DOCTYPE, EVENT_DOCTYPE):
        ensure(
            not client.has_permission(internal_doctype, "read"),
            f"النوع الداخلي {internal_doctype} يمنح read قياسيًا؛ أوقف الاختبار",
        )
    ensure(
        not client.has_permission(EVENT_DOCTYPE, "delete"),
        "Namar Mention Event تمنح delete مباشرًا؛ أوقف الاختبار",
    )

    required_permissions = [
        ("ToDo", "read"),
        ("ToDo", "delete"),
        ("Comment", "read"),
        ("Comment", "delete"),
        (THREAD_DOCTYPE, "delete"),
    ]
    if not cleanup_only:
        required_permissions.extend(
            [
                ("ToDo", "create"),
                ("Comment", "create"),
            ]
        )
    missing_permissions = [
        f"{doctype}:{perm}"
        for doctype, perm in required_permissions
        if not client.has_permission(doctype, perm)
    ]
    ensure(
        not missing_permissions,
        "توقف قبل إنشاء fixtures؛ صلاحيات الإنشاء/التنظيف ناقصة: "
        + ", ".join(missing_permissions),
    )


def validate_manifest_path(path: Path, state_dir: Path) -> Path:
    resolved = path.expanduser().resolve()
    allowed_root = state_dir.expanduser().resolve()
    if not resolved.is_relative_to(allowed_root):
        raise SmokeFailure(f"manifest التنظيف يجب أن يكون داخل {allowed_root}")
    if not resolved.is_file():
        raise SmokeFailure(f"manifest غير موجود: {resolved}")
    return resolved


def load_manifest(
    path: Path,
    *,
    state_dir: Path,
    config: SmokeConfig,
    user: str,
) -> tuple[dict[str, Any], Path]:
    resolved = validate_manifest_path(path, state_dir)
    try:
        data = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SmokeFailure("تعذر قراءة manifest") from exc
    ensure(isinstance(data, dict) and data.get("schema_version") == 1, "manifest غير مدعوم")
    ensure(data.get("environment") == "test", "manifest لا يخص التجريبي")
    ensure(normalize_text(data.get("host")).lower() == config.host, "موقع manifest لا يطابق الموقع")
    ensure(data.get("user_sha256") == digest(user.lower()), "مستخدم manifest لا يطابق التوكن")
    run_id = normalize_text(data.get("run_id"))
    marker = normalize_text(data.get("marker"))
    ensure(run_id.startswith("MISMK-"), "RUN_ID في manifest غير صالح")
    ensure(marker == f"[MISMK:{run_id}]", "بصمة manifest غير صالحة")
    resources = data.get("resources") or []
    ensure(isinstance(resources, list) and len(resources) <= MAX_RESOURCES, "عدد fixtures غير آمن")
    thread_name = normalize_text(data.get("thread_name"))
    for row in resources:
        ensure(isinstance(row, dict), "صيغة resource في manifest غير صحيحة")
        doctype = normalize_text(row.get("doctype"))
        name = normalize_text(row.get("name"))
        ensure(doctype in ALLOWED_RESOURCE_TYPES, "manifest يحتوي نوعًا غير مسموح")
        ensure(name, "resource في manifest بلا اسم")
        validate_resource_fingerprints(
            row.get("fingerprints"),
            marker=marker,
        )
    reference_name = normalize_text(data.get("reference_name"))
    if resources:
        ensure(reference_name, "manifest يحتوي resources بلا reference_name")
        ensure(
            any(
                row.get("doctype") == "ToDo"
                and row.get("role") == "REFERENCE"
                and normalize_text(row.get("name")) == reference_name
                for row in resources
            ),
            "manifest لا يحتوي ToDo المصدر المطابقة لـreference_name",
        )
    if thread_name:
        ensure(
            any(
                row.get("doctype") == THREAD_DOCTYPE
                and normalize_text(row.get("name")) == thread_name
                for row in resources
            ),
            "thread_name في manifest لا تطابق resource مسجلة",
        )
    return data, resolved


def validate_run_config(args: argparse.Namespace) -> SmokeConfig:
    configured_site = normalize_text(os.environ.get("FRAPPE_TEST_SITE"))
    token = normalize_token(os.environ.get("FRAPPE_TEST_TOKEN", ""))
    base_url, host = normalize_site(configured_site)
    if args.test_site:
        requested_url, requested_host = normalize_site(args.test_site)
        ensure(
            requested_url == base_url and requested_host == host,
            "--test-site يجب أن يطابق FRAPPE_TEST_SITE؛ لا يسمح بتجاوز الموقع",
        )
    ensure(token, "FRAPPE_TEST_TOKEN غير مضبوط؛ لا يوجد fallback إلى توكن آخر")

    production_hosts = set(KNOWN_PRODUCTION_HOSTS)
    prod_host = host_from_value(os.environ.get("FRAPPE_PROD_SITE", ""))
    if prod_host:
        production_hosts.add(prod_host)
    ensure(host not in production_hosts, f"رفض التشغيل: {host} موقع إنتاج")

    confirmed_host = host_from_value(args.confirm_site)
    ensure(
        confirmed_host and confirmed_host == host,
        "--confirm-site يجب أن يطابق مضيف FRAPPE_TEST_SITE حرفيًا",
    )
    ensure(5 <= args.timeout <= 120, "--timeout يجب أن يكون بين 5 و120 ثانية")
    return SmokeConfig(
        base_url=base_url,
        host=host,
        token=token,
        timeout=args.timeout,
        expected_user=normalize_text(args.expected_user),
        include_self_reply=bool(args.include_self_reply),
    )


def dry_run_summary(args: argparse.Namespace, env_file: Path, state_dir: Path) -> dict[str, Any]:
    host = host_from_value(args.test_site or os.environ.get("FRAPPE_TEST_SITE", ""))
    return {
        "mode": "dry-run",
        "environment": "test-only",
        "network_requests": False,
        "configured_test_host": host or None,
        "test_token_configured": bool(normalize_text(os.environ.get("FRAPPE_TEST_TOKEN"))),
        "env_file_exists": env_file.exists(),
        "required_to_execute": ["--run", "--confirm-site <FRAPPE_TEST_SITE host>"],
        "api_base": API_BASE,
        "endpoints": list(MENTION_ENDPOINTS),
        "optional_reply_endpoints": list(OPTIONAL_REPLY_ENDPOINTS),
        "fixture": "ToDo معزول + Comment يذكر مستخدم التوكن نفسه",
        "direct_thread_fixture": False,
        "internal_event_cleanup": "Thread.on_trash cascade؛ بلا REST read/delete لـEvent",
        "include_self_reply": bool(args.include_self_reply),
        "self_reply_recipient_policy": "مستخدم FRAPPE_TEST_TOKEN فقط؛ لا موظف خارجي",
        "cleanup_manifest": str(args.cleanup_manifest) if args.cleanup_manifest else None,
        "state_dir": str(state_dir),
        "production_hosts_denied": sorted(KNOWN_PRODUCTION_HOSTS),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Smoke test دائم لصندوق الإشارات على التجريبي فقط؛ الافتراضي dry-run بلا اتصال.",
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="ينفذ الاختبار الحي وينشئ self-mention معزولًا ثم ينظفه؛ بدونه لا اتصال.",
    )
    parser.add_argument(
        "--confirm-site",
        default="",
        help="تأكيد إلزامي لمضيف FRAPPE_TEST_SITE عند استخدام --run.",
    )
    parser.add_argument(
        "--test-site",
        default="",
        help="تأكيد اختياري إضافي لموقع FRAPPE_TEST_SITE نفسه؛ لا يسمح بتغييره.",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=default_env_file(),
        help="ملف البيئة المحلي؛ لا يقرأ إلا FRAPPE_TEST_SITE/FRAPPE_TEST_TOKEN للتشغيل.",
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=default_state_dir(),
        help="مجلد manifests المحلي خارج Git.",
    )
    parser.add_argument(
        "--expected-user",
        default="",
        help="اختياري: يوقف التشغيل إذا لم يطابق مستخدم FRAPPE_TEST_TOKEN.",
    )
    parser.add_argument(
        "--include-self-reply",
        action="store_true",
        help="يختبر reply_mention بمنشن مستخدم التوكن نفسه فقط؛ لا يذكر موظفًا آخر.",
    )
    parser.add_argument(
        "--cleanup-manifest",
        type=Path,
        help="ينظف تشغيلًا سابقًا من manifest داخل --state-dir فقط.",
    )
    parser.add_argument("--timeout", type=int, default=40, help="مهلة طلب HTTP بالثواني (5-120).")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    env_file = args.env_file.expanduser().resolve()
    state_dir = args.state_dir.expanduser().resolve()
    load_env(env_file)
    if not args.run:
        print(json.dumps(dry_run_summary(args, env_file, state_dir), ensure_ascii=False, indent=2))
        print("\nDry-run محلي فقط: لم يُنشأ عميل HTTP ولم يحدث أي اتصال أو تعديل.")
        return 0

    config = validate_run_config(args)
    client = FrappeClient(config)
    user = client.get_logged_user()
    ensure(user and user != "Guest", "FRAPPE_TEST_TOKEN لا يمثل مستخدمًا مسجلًا")
    if config.expected_user:
        ensure(user.lower() == config.expected_user.lower(), "مستخدم التوكن لا يطابق --expected-user")

    preflight_schema_and_permissions(
        client,
        user=user,
        cleanup_only=bool(args.cleanup_manifest),
    )
    print("[OK] preflight: schema + صلاحيات إنشاء وتنظيف fixtures")

    if args.cleanup_manifest:
        manifest, manifest_path = load_manifest(
            args.cleanup_manifest,
            state_dir=state_dir,
            config=config,
            user=user,
        )
        runner = SmokeRunner(
            client,
            config,
            manifest=manifest,
            manifest_path=manifest_path,
            user=user,
        )
        return 1 if runner.cleanup() else 0

    runner = SmokeRunner.new(client, config, user, state_dir)
    smoke_error: Exception | None = None
    cleanup_errors: list[str] = []
    summary: dict[str, Any] | None = None
    try:
        summary = runner.run()
    except Exception as exc:  # noqa: BLE001
        smoke_error = exc
        runner.manifest["status"] = "checks_failed"
        runner.manifest["failure"] = str(exc)
        runner.save_manifest()
        print(f"\n[FAILED] الاختبار: {exc}")
    finally:
        cleanup_errors = runner.cleanup()

    print("\n=== النتيجة ===")
    if smoke_error or cleanup_errors:
        print("فشل Smoke Test أو تنظيفه؛ احتُفظ بالـmanifest للاستعادة الآمنة.")
        print(f"Manifest: {runner.manifest_path}")
        return 1
    print(json.dumps(summary or {}, ensure_ascii=False, indent=2))
    print("نجح Smoke Test ونُظفت جميع fixtures ذات البصمة.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nأُلغي التشغيل. استخدم --run --cleanup-manifest عند الحاجة.", file=sys.stderr)
        sys.exit(130)
    except SmokeFailure as exc:
        print(f"خطأ: {exc}", file=sys.stderr)
        sys.exit(1)
