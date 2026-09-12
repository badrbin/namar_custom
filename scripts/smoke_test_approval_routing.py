#!/usr/bin/env python3
"""اختبار حي اختياري لتوجيه الموافقات المتعدد، على التجريبي فقط.

الافتراضي خطة محلية بلا اتصال أو قراءة أسرار. التشغيل يتطلب --run مع
--confirm-site مطابقًا لـFRAPPE_TEST_SITE. يستخدم Administrator بالتوكن
ومستخدمًا قائمًا بدخول عادي؛ لا ينشئ مستخدمين أو مفاتيح ولا يغير أدوارهم.
ينشئ DocType وWorkflow ومستندات معزولة، ويسجل كل تعديل قبل/بعد تنفيذه في
manifest خاص 0600 خارج Git. التنظيف في finally، ويمكن استكماله صراحة بواسطة
--cleanup-manifest عند توقف العملية. لا يعاد أي تعديل تلقائيًا بعد فشله.
يمكن تمرير --pause-before-cleanup داخل PTY لإبقاء fixtures بعد نجاح الاختبار
حتى ينتهي الوكيل من مراجعة المحرر البصرية؛ Enter يستأنف التنظيف. Ctrl+C
يمر بمسار finally نفسه. هذا الخيار معطل افتراضيًا.
التنظيف يحذف الموارد النشطة عبر REST؛ يحتفظ Frappe بسجل Deleted Document
وبجدول DocType الفيزيائي الفارغ وفق سلوكه القياسي. الأداة لا تنفذ DROP أو SQL.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse
from uuid import uuid4

import requests


ROOT = Path(__file__).resolve().parents[1]
FIELD = "custom_followups_routing_targets"
API = "namar_test.followups.api"
PREFIX_RE = re.compile(r"^NAR Smoke [0-9]{14} [a-f0-9]{8}$")
PROD_HOSTS = {"erp.namar.net", "zawaya7.frappe.cloud"}
AUTO_ROLES = {"All", "Guest", "Desk User", "Administrator"}
MAX_TARGETS = 12


class SmokeFailure(RuntimeError):
    pass


def ensure(condition: Any, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_env_file() -> Path:
    for candidate in (ROOT / ".env.local", ROOT.parent.parent / "erpnex_codex/.env.local"):
        if candidate.is_file():
            return candidate
    return ROOT / ".env.local"


def read_env(path: Path) -> dict[str, str]:
    ensure(path.is_file(), "ملف البيئة غير موجود")
    env = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:]
        if "=" in line:
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def origin(value: str, *, allow_path: bool = False) -> str:
    parsed = urlparse(value if "://" in value else "https://" + value)
    ensure(parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password,
           "الموقع يجب أن يكون HTTPS بلا بيانات دخول في الرابط")
    ensure(not parsed.query and not parsed.fragment, "الموقع لا يقبل query أو fragment")
    ensure(allow_path or parsed.path in ("", "/"), "الموقع يجب أن يكون أصلًا بلا مسار")
    ensure(parsed.port in (None, 443), "يسمح بمنفذ HTTPS القياسي فقط")
    return "https://" + parsed.hostname.lower().rstrip(".")


def run_config(args) -> dict[str, str]:
    env = read_env(args.env_file)
    ensure(env.get("FRAPPE_TEST_SITE") and env.get("FRAPPE_TEST_TOKEN"), "بيانات التجريبي غير مكتملة")
    site = origin(env["FRAPPE_TEST_SITE"])
    ensure(args.confirm_site and origin(args.confirm_site) == site, "--confirm-site يجب أن يطابق التجريبي")
    denied = set(PROD_HOSTS)
    if env.get("FRAPPE_PROD_SITE"):
        denied.add(urlparse(origin(env["FRAPPE_PROD_SITE"])).hostname)
    ensure(urlparse(site).hostname not in denied, "رُفض التشغيل على موقع أساسي")
    ensure(5 <= args.timeout <= 120, "مهلة الطلب يجب أن تكون بين 5 و120 ثانية")
    if args.pause_before_cleanup:
        ensure(not args.cleanup_manifest, "خيار الوقفة البصرية لا يُستخدم مع استكمال التنظيف")
        ensure(sys.stdin.isatty(), "--pause-before-cleanup يتطلب PTY تفاعليًا قبل إنشاء الموارد")
    env["site"] = site
    if not args.cleanup_manifest:
        ensure(all(env.get(key) for key in ("BROWSER_LOGIN_EMAIL", "BROWSER_LOGIN_PASSWORD", "BROWSER_LOGIN_URL")),
               "بيانات الدخول العادي للمستخدم الثاني غير مكتملة")
        ensure(origin(env["BROWSER_LOGIN_URL"], allow_path=True) == site,
               "BROWSER_LOGIN_URL يجب أن يشير إلى التجريبي نفسه؛ لم تُرسل بيانات الدخول")
    return env


def private_dir(path: Path) -> Path:
    ensure(not path.is_symlink(), "مجلد السجل لا يقبل رابطًا رمزيًا")
    path = path.expanduser().resolve()
    # State and backups must stay outside every Git working tree.
    ensure(not any((parent / ".git").exists() for parent in (path, *path.parents)),
           "ضع manifest خارج أي مستودع Git")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    ensure(stat.S_IMODE(path.stat().st_mode) & 0o077 == 0, "مجلد السجل يجب أن يكون خاصًا 0700")
    return path


class Journal:
    def __init__(self, path: Path, data: dict):
        self.path, self.data = path, data

    def flush(self):
        # Replacing a complete private file keeps the last recovery manifest
        # intact if the process stops while writing the next observation.
        temporary = self.path.with_name(self.path.name + ".pending-" + uuid4().hex)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(temporary, flags, 0o600)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self.data, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def event(self, event: str, **details) -> dict:
        entry = {"event": event, "at": now(), **details}
        self.data["events"].append(entry)
        self.flush()
        return entry


class Client:
    def __init__(self, site: str, label: str, journal: Journal, timeout: int, token: str = ""):
        self.site, self.label, self.journal, self.timeout = site, label, journal, timeout
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json", "User-Agent": "namar-approval-routing-smoke/1"})
        if token:
            self.session.headers["Authorization"] = token if token.lower().startswith("token ") else "token " + token

    def request(self, method: str, path: str, *, params=None, body=None, expected=(200,), html=False):
        # Deliberately record no cookie/token/header/body values here. Fixture
        # writes are backed up by Runner; login credentials never reach disk.
        entry = self.journal.event("http_before", actor=self.label, method=method, path=path,
                                   query_keys=sorted((params or {}).keys()), body_keys=sorted((body or {}).keys()))
        try:
            response = self.session.request(method, self.site + path, params=params, json=body,
                                            timeout=self.timeout, allow_redirects=False)
        except requests.RequestException as exc:
            self.journal.event("http_failed", request_at=entry["at"], error=type(exc).__name__)
            raise SmokeFailure(f"فشل اتصال {self.label}؛ لم يُكرر الطلب") from None
        self.journal.event("http_after", request_at=entry["at"], status=response.status_code)
        if response.status_code not in expected:
            # Raw server errors may echo sensitive login arguments.
            raise SmokeFailure(f"{self.label}: {method} {path} رجع HTTP {response.status_code}")
        if html:
            return response.text
        if not response.content:
            return {}
        try:
            data = response.json()
        except ValueError:
            raise SmokeFailure("استجابة الخادم ليست JSON") from None
        ensure(isinstance(data, dict), "استجابة الخادم ليست كائنًا")
        return data

    def call(self, method: str, *, args=None, post=False):
        path = "/api/method/" + method
        result = self.request("POST" if post else "GET", path,
                              **({"body": args} if post else {"params": args}))
        return result.get("message")

    def doc(self, doctype: str, name: str, *, missing=False):
        envelope = self.request("GET", "/api/resource/" + quote(doctype, safe="") + "/" + quote(name, safe=""),
                                expected=(200, 404) if missing else (200,))
        return envelope.get("data")

    def rows(self, doctype: str, *, fields=None, filters=None, start=0, length=100):
        envelope = self.request("GET", "/api/resource/" + quote(doctype, safe=""), params={
            "fields": json.dumps(fields or ["name"]), "filters": json.dumps(filters or {}),
            "limit_start": start, "limit_page_length": length, "order_by": "name asc",
        })
        rows = envelope.get("data")
        ensure(isinstance(rows, list), "استجابة قائمة REST غير صحيحة")
        return rows

    def login(self, email: str, password: str):
        self.request("POST", "/api/method/login", body={"usr": email, "pwd": password})
        ensure(self.call("frappe.auth.get_logged_user") == email, "الدخول العادي لا يطابق المستخدم المطلوب")
        page = self.request("GET", "/app", html=True)
        match = re.search(r'frappe\.csrf_token\s*=\s*[\"\x27]([^\"\x27]+)', page)
        ensure(match and match.group(1) not in ("None", "undefined"), "تعذر الحصول على CSRF للجلسة العادية")
        self.session.headers["X-Frappe-CSRF-Token"] = match.group(1)


class Runner:
    def __init__(self, env, args, journal):
        self.env, self.args, self.journal = env, args, journal
        self.prefix = journal.data["prefix"]
        self.fixture = self.prefix
        self.workflow = self.prefix + " Flow"
        self.pending, self.approved = self.prefix + " Pending", self.prefix + " Approved"
        self.action = self.prefix + " Approve"
        self.a = Client(env["site"], "A", journal, args.timeout, env["FRAPPE_TEST_TOKEN"])
        self.b = Client(env["site"], "B", journal, args.timeout)
        self.clients = {"A": self.a, "B": self.b}
        self.documents: dict[str, dict] = {}
        self.actions: dict[str, str] = {}

    def target(self, doctype: str, name: str, fingerprint: dict):
        ensure(len(self.journal.data["targets"]) < MAX_TARGETS, "تجاوز حد الموارد المعزولة")
        entry = {"doctype": doctype, "name": name, "fingerprint": fingerprint, "deleted": False}
        self.journal.data["targets"].append(entry)
        self.journal.flush()
        return entry

    def create(self, doctype: str, name: str, payload: dict, fingerprint: dict, *, client=None):
        client = client or self.a
        ensure(self.a.doc(doctype, name, missing=True) is None, "اسم fixture موجود مسبقًا؛ لم يُستبدل")
        self.target(doctype, name, fingerprint)
        self.journal.event("mutation_before", operation="create", doctype=doctype, name=name, actor=client.label,
                           before=None, intended=payload)
        doc = client.request("POST", "/api/resource/" + quote(doctype, safe=""), body=payload,
                             expected=(200, 201)).get("data")
        ensure(isinstance(doc, dict) and doc.get("name") == name, "اسم السجل المنشأ لا يطابق الاسم المعتمد")
        self.journal.event("mutation_after", operation="create", doctype=doctype, name=name, actor=client.label, after=doc)
        return doc

    def update(self, doctype: str, name: str, values: dict):
        self.assert_target(doctype, name)
        before = self.a.doc(doctype, name)
        self.journal.event("mutation_before", operation="update", doctype=doctype, name=name, before=before, intended=values)
        doc = self.a.request("PUT", "/api/resource/" + quote(doctype, safe="") + "/" + quote(name, safe=""),
                             body=values).get("data")
        self.journal.event("mutation_after", operation="update", doctype=doctype, name=name, after=doc)
        return doc

    def assert_target(self, doctype: str, name: str):
        entry = next((item for item in self.journal.data["targets"]
                      if item["doctype"] == doctype and item["name"] == name), None)
        ensure(entry and not entry.get("deleted"), "المورد ليس ضمن manifest الحي")
        ensure(name == self.prefix or name.startswith(self.prefix + " "), "المورد خارج بصمة التشغيل")
        allowed = {"DocType", "Workflow", "Workflow State", "Workflow Action Master", self.fixture}
        ensure(doctype in allowed, "نوع المورد غير مسموح للتعديل")
        doc = self.a.doc(doctype, name, missing=True)
        if doc:
            ensure(all(doc.get(key) == value for key, value in entry["fingerprint"].items()),
                   "بصمة المورد الحية لا تطابق manifest؛ لم يُعدّل")
        return entry, doc

    def preflight(self):
        ensure(self.a.call("frappe.auth.get_logged_user") == "Administrator", "توكن التجريبي يجب أن يعود إلى Administrator")
        self.b.login(self.env["BROWSER_LOGIN_EMAIL"], self.env["BROWSER_LOGIN_PASSWORD"])
        self.user_b = self.b.call("frappe.auth.get_logged_user")
        ensure(self.user_b not in ("Administrator", "Guest"), "يلزم مستخدم نظام عادي مختلف عن Administrator")
        user = self.a.doc("User", self.user_b)
        ensure(user.get("enabled") == 1 and user.get("user_type") == "System User", "المستخدم الثاني غير مؤهل")
        roles = {row["role"] for row in user.get("roles", [])} - AUTO_ROLES
        ensure(roles, "لا يوجد دور صريح للمستخدم الثاني؛ توقف قبل إنشاء الموارد")
        role = self.args.role or ("Accounts User" if "Accounts User" in roles else sorted(roles)[0])
        ensure(role in roles, "الدور المحدد ليس من أدوار المستخدم الثاني")
        role_doc = self.a.doc("Role", role)
        ensure(role_doc and not role_doc.get("disabled"), "الدور المشترك معطل أو غير موجود")
        self.role = role
        meta = self.a.doc("DocType", "Workflow Document State")
        fields = {row["fieldname"] for row in meta.get("fields", [])}
        # Custom fields are returned by getdoctype, not necessarily DocType REST.
        if FIELD not in fields:
            custom = self.a.rows("Custom Field", filters={"dt": "Workflow Document State", "fieldname": FIELD})
            ensure(len(custom) == 1, "لم يُنشر حقل مستلمي الموافقات بعد")
        workflows = self.a.rows("Workflow", fields=["name"], filters={"is_active": 1}, length=500)
        ensure(len(workflows) < 500, "تجاوزت Workflows حد الفحص المسبق")
        for row in workflows:
            doc = self.a.doc("Workflow", row["name"])
            for state in doc.get("states", []):
                raw = state.get(FIELD)
                if raw:
                    try:
                        targets = json.loads(raw).get("targets", [])
                    except (ValueError, AttributeError):
                        raise SmokeFailure("يوجد إعداد توجيه حي غير سليم خارج الاختبار؛ توقف قبل الكتابة") from None
                    ensure(not targets, "توجد قواعد توجيه حية أخرى؛ لا يمكن نسب فرق العداد إلى fixtures وحدها")
        # Check the deployed API under both actual identities before mutation.
        for label, client in self.clients.items():
            core = self.core_count(client)
            payload = client.call(API + ".get_approvals", args={"page_length": 1})
            ensure(payload["counts"]["open"] == core, "العداد الأساسي لا يطابق permission-aware Workflow Action")
        self.journal.data["actors"] = {"A": "Administrator", "B": self.user_b, "shared_role": role}
        self.journal.event("preflight_passed", role=role)

    def setup(self):
        self.create("DocType", self.fixture, {
            "doctype": "DocType", "name": self.fixture, "custom": 1, "module": "Custom",
            "description": self.prefix, "autoname": "field:title", "title_field": "title", "track_changes": 0,
            "fields": [
                {"fieldname": "title", "label": "Smoke Title", "fieldtype": "Data", "reqd": 1},
                {"fieldname": "approver", "label": "Smoke Approver", "fieldtype": "Link", "options": "User"},
                {"fieldname": "workflow_state", "label": "Workflow State", "fieldtype": "Link", "options": "Workflow State"},
            ],
            "permissions": [{"role": self.role, "read": 1, "write": 1, "create": 1, "delete": 1}],
        }, {"custom": 1, "description": self.prefix})
        for state in (self.pending, self.approved):
            self.create("Workflow State", state, {"doctype": "Workflow State", "workflow_state_name": state},
                        {"workflow_state_name": state})
        self.create("Workflow Action Master", self.action,
                    {"doctype": "Workflow Action Master", "workflow_action_name": self.action},
                    {"workflow_action_name": self.action})
        self.create("Workflow", self.workflow, {
            "doctype": "Workflow", "workflow_name": self.workflow, "document_type": self.fixture,
            "is_active": 1, "send_email_alert": 0, "workflow_state_field": "workflow_state",
            "states": [{"state": state, "doc_status": "0", "allow_edit": self.role, "send_email": 0}
                       for state in (self.pending, self.approved)],
            "transitions": [{"state": self.pending, "action": self.action, "next_state": self.approved,
                             "allowed": self.role, "allow_self_approval": 1}],
        }, {"document_type": self.fixture, "workflow_name": self.workflow, "send_email_alert": 0})
        for index in range(1, 4):
            name = f"{self.prefix} Source {index}"
            creator, owner = (self.a, "Administrator") if index == 3 else (self.b, self.user_b)
            self.documents[name] = self.create(self.fixture, name, {
                "doctype": self.fixture, "title": name, "approver": self.user_b,
                "workflow_state": self.pending,
            }, {"title": name, "owner": owner}, client=creator)
            ensure(self.documents[name].get("owner") == owner, "مالك المصدر لا يطابق جلسة إنشائه")
        rows = self.a.rows("Workflow Action", fields=["name", "reference_name"],
                           filters={"reference_doctype": self.fixture, "status": "Open"}, length=10)
        ensure(len(rows) == 3, "لم ينشئ المسار القياسي موافقة واحدة لكل مصدر")
        self.actions = {row["reference_name"]: row["name"] for row in rows}
        ensure(set(self.actions) == set(self.documents), "مراجع الموافقات غير مطابقة للمصادر")
        self.journal.data["workflow_actions"] = self.actions
        self.journal.data["source_owners"] = {name: doc["owner"] for name, doc in self.documents.items()}
        self.journal.flush()

    def set_targets(self, targets: list[dict]):
        doc = self.a.doc("Workflow", self.workflow)
        for row in doc["states"]:
            if row["state"] == self.pending:
                row[FIELD] = json.dumps({"version": 1, "targets": targets}, ensure_ascii=False)
        self.update("Workflow", self.workflow, {"states": doc["states"]})

    def core_count(self, client: Client) -> int:
        rows = client.rows("Workflow Action", fields=["count(name) as count"], filters={"status": "Open"}, length=1)
        return int(rows[0]["count"] if rows else 0)

    def fixture_list(self, client, start=0, length=10):
        return client.call(API + ".get_approvals", args={
            "search": self.fixture, "search_scope": "doctype", "limit_start": start, "page_length": length,
        })

    def check(self, label: str, expected: dict[str, set[str]], *, fallback=False, modes=None):
        observation = {}
        for actor, client in self.clients.items():
            payload = self.fixture_list(client)
            items = payload.get("items")
            ensure(isinstance(items, list), "عقد قائمة الموافقات لا يعيد items")
            actual = {row["reference_name"] for row in items}
            ensure(actual == expected[actor] and len(items) == len(actual), f"{label}: القائمة أو إزالة التكرار غير صحيحة للمستخدم {actor}")
            for row in items:
                routing = row.get("routing") or {}
                ensure(routing.get("fallback") is fallback, f"{label}: fallback غير صحيح")
                responsible = [person.get("user") for person in routing.get("responsible_users", [])]
                ensure(len(responsible) == len(set(responsible)), "تكرر نفس الموظف في تفاصيل المستلمين")
                if modes is not None:
                    ensure({target["type"] for target in routing.get("targets", [])} == set(modes),
                           f"{label}: أنواع المستلمين غير مكتملة")
                detail = client.call(API + ".get_approval_detail", args={"action_name": row["name"]})
                ensure(detail["approval"]["routing"] == routing, "تفاصيل الموافقة تختلف عن القائمة")
            # Counts are global. Compare all current core-permitted actions,
            # subtracting only the isolated excluded sources, not a fixed seed.
            core_before = self.core_count(client)
            counts = client.call(API + ".get_my_followups_counts")
            listing = self.fixture_list(client)
            core_after = self.core_count(client)
            ensure(core_before == core_after, "تغيرت الموافقات خارجيًا أثناء التحقق؛ أُوقف الاختبار دون تكرار التعديلات")
            hidden = len(self.documents) - len(expected[actor])
            count = core_before - hidden
            ensure(listing["counts"]["open"] == count, f"{label}: عداد القائمة غير مطابق")
            ensure(counts["counts"]["approvals"] == count and counts["attention_counts"]["approvals"] == count,
                   f"{label}: العداد الموحد أو الشارة غير مطابق")
            ensure(counts["counts"]["total"] == sum(counts["counts"][key] for key in ("mentions", "followups", "approvals")),
                   "إجمالي العدادات غير مطابق")
            observation[actor] = {"visible_sources": sorted(actual), "core_count": core_before, "expected_count": count}
        self.journal.event("assertion_passed", scenario=label, observation=observation)

    def exercise(self):
        names = set(self.documents)
        both = {"A": names, "B": names}
        only_a, only_b = {"A": names, "B": set()}, {"A": set(), "B": names}
        admin_owned = {name for name, doc in self.documents.items() if doc["owner"] == "Administrator"}
        b_owned = names - admin_owned
        ensure(admin_owned == {f"{self.prefix} Source 3"} and len(b_owned) == 2,
               "بيانات ملكية المصادر المعزولة لا تطابق سيناريو OR")
        owner_and_role = {"A": admin_owned, "B": names}
        self.check("default_workflow_role", both)
        self.set_targets([{"type": "user", "user": "Administrator"}])
        self.check("single_user_hides_other_role_holder", only_a, modes={"user"})
        for action_name in self.actions.values():
            data = self.b.request("GET", "/api/method/" + API + ".get_approval_detail",
                                  params={"action_name": action_name}, expected=(403,))
            ensure(data.get("exc_type") == "PermissionError", "الموافقة الموجهة لغيره لم تُرفض بصلاحية المنتج")
        self.set_targets([{"type": "user", "user": "Administrator"}, {"type": "user", "user": self.user_b},
                          {"type": "user", "user": self.user_b}])
        self.check("multiple_users_union_deduplicated", both, modes={"user"})
        for client in self.clients.values():
            pages = [self.fixture_list(client, start=index, length=1) for index in range(3)]
            paged = [page["items"][0]["reference_name"] for page in pages if len(page["items"]) == 1]
            ensure(len(paged) == 3 and set(paged) == names, "pagination لا يعيد كل عنصر مرة واحدة")
            first_name = sorted(names)[0]
            search = client.call(API + ".get_approvals", args={"search_scope": "document", "search": first_name})
            ensure([row["reference_name"] for row in search["items"]] == [first_name], "البحث برقم المستند غير مطابق")
        self.journal.event("assertion_passed", scenario="search_and_pagination_after_union")
        self.set_targets([{"type": "role", "role": self.role}])
        self.check("specific_role_uses_actual_members_not_implicit_administrator", only_b, modes={"role"})
        self.set_targets([{"type": "owner"}, {"type": "role", "role": self.role}])
        self.check("owner_or_actual_role_member", owner_and_role, modes={"owner", "role"})
        self.set_targets([{"type": "owner"}, {"type": "field", "field": "approver"},
                          {"type": "role", "role": self.role}])
        self.check("owner_field_and_role_union", owner_and_role, modes={"owner", "field", "role"})
        self.set_targets([{"type": "field", "field": "approver"}])
        self.check("direct_user_field", only_b, modes={"field"})
        first = sorted(names)[0]
        ensure(first in b_owned, "المصدر المختار للاعتماد القياسي يجب أن يكون منشأً بجلسة المستخدم B")
        # Hide the first actual approval row, so an implementation that applies
        # offset/limit before routing cannot pass by hiding only the last row.
        field_changed = self.fixture_list(self.b, start=0, length=1)["items"][0]["reference_name"]
        ensure(field_changed in names, "المصدر الأول ليس من مصادر الاختبار")
        self.update(self.fixture, field_changed, {"approver": "Administrator"})
        self.check("field_change_takes_effect", {"A": {field_changed}, "B": names - {field_changed}}, modes={"field"})
        partial_pages = [self.fixture_list(self.b, start=index, length=1) for index in range(2)]
        ensure(all(len(page["items"]) == 1 for page in partial_pages), "صفحة ناقصة بعد حجب موافقة مستخدم آخر")
        ensure({page["items"][0]["reference_name"] for page in partial_pages} == names - {field_changed},
               "pagination الجزئي تخطى أو كرر مصدرًا مرئيًا")
        ensure(partial_pages[0]["has_more"] is True and partial_pages[0]["next_start"] == 1
               and partial_pages[1]["has_more"] is False and partial_pages[1]["next_start"] is None,
               "حدود الصفحات لا تعكس القائمة بعد التوجيه")
        self.journal.event("assertion_passed", scenario="pagination_after_excluding_other_recipient",
                           excluded_first_approval_source=field_changed)
        self.update(self.fixture, field_changed, {"approver": self.user_b})
        self.set_targets([{"type": "user", "user": "Guest"}, {"type": "user", "user": self.user_b}])
        self.check("partial_invalid_target_does_not_broaden", only_b, modes={"user"})
        self.set_targets([{"type": "user", "user": "Guest"}])
        self.check("all_invalid_targets_fall_back_to_role", both, fallback=True)
        for name in sorted(names):
            self.update(self.fixture, name, {"approver": None})
        self.set_targets([{"type": "field", "field": "approver"}])
        self.check("empty_fields_fall_back_to_role", both, fallback=True)
        self.set_targets([{"type": "owner"}])
        self.check("owner_uses_each_source_creator", {"A": admin_owned, "B": b_owned}, modes={"owner"})
        self.set_targets([{"type": "user", "user": "Administrator"}])
        self.check("changed_rule_takes_effect", only_a, modes={"user"})
        source = self.b.doc(self.fixture, first)
        self.journal.event("mutation_before", operation="standard_apply_workflow", doctype=self.fixture,
                           name=first, before=source, actor="B", action=self.action)
        changed = self.b.call("frappe.model.workflow.apply_workflow", args={"doc": json.dumps(source), "action": self.action}, post=True)
        self.journal.event("mutation_after", operation="standard_apply_workflow", doctype=self.fixture,
                           name=first, after=changed)
        ensure(changed.get("workflow_state") == self.approved, "صاحب الدور الآخر لم يستطع الاعتماد من المسار القياسي")
        action = self.a.doc("Workflow Action", self.actions[first])
        ensure(action["status"] == "Completed", "لم تنته الموافقة القياسية بعد تنفيذ الانتقال")
        self.documents.pop(first)
        self.check("other_role_holder_can_apply_and_closed_action_disappears", {"A": names - {first}, "B": set()}, modes={"user"})

    def cleanup(self):
        errors = []
        # Delete source parents while their workflow still exists, so Frappe's
        # normal on_trash path removes Workflow Actions. Then delete definitions.
        order = {self.fixture: 0, "Workflow": 1, "Workflow State": 2, "Workflow Action Master": 3, "DocType": 4}
        targets = sorted(self.journal.data["targets"], key=lambda item: order.get(item["doctype"], 99))
        for target in targets:
            if target.get("deleted"):
                continue
            doctype, name = target["doctype"], target["name"]
            try:
                entry, doc = self.assert_target(doctype, name)
                if doc:
                    self.journal.event("mutation_before", operation="delete", doctype=doctype, name=name, before=doc)
                    self.a.request("DELETE", "/api/resource/" + quote(doctype, safe="") + "/" + quote(name, safe=""),
                                   expected=(200, 202))
                    ensure(self.a.doc(doctype, name, missing=True) is None, "بقي المورد بعد الحذف")
                    self.journal.event("mutation_after", operation="delete", doctype=doctype, name=name, after=None)
                entry["deleted"] = True
                self.journal.flush()
            except Exception as exc:
                errors.append({"doctype": doctype, "name": name, "error": str(exc) if isinstance(exc, SmokeFailure) else type(exc).__name__})
                # Preserve dependencies of a failed cleanup step. In particular,
                # never delete the source table while any fixture is still live.
                break
        try:
            remaining = self.a.rows("Workflow Action", filters={"reference_doctype": self.fixture}, length=10)
            if remaining:
                errors.append({"doctype": "Workflow Action", "error": "بقيت موافقات خاصة بمصادر الاختبار"})
        except Exception as exc:
            errors.append({"doctype": "Workflow Action", "error": str(exc) if isinstance(exc, SmokeFailure) else type(exc).__name__})
        self.journal.data["cleanup_errors"] = errors
        self.journal.data["cleanup_scope"] = "active_fixture_documents_workflow_definitions_and_actions"
        self.journal.data["standard_frappe_retention"] = {
            "deleted_document_recovery_records": True,
            "empty_custom_doctype_database_table": True,
            "sql_drop_invoked": False,
        }
        self.journal.data["cleanup_complete"] = not errors and all(row["deleted"] for row in self.journal.data["targets"])
        self.journal.data["state"] = "cleanup_complete" if self.journal.data["cleanup_complete"] else "cleanup_incomplete"
        self.journal.flush()
        ensure(self.journal.data["cleanup_complete"], "تنظيف غير مكتمل؛ استعمل --cleanup-manifest بعد المراجعة")

    def pause_for_visual_qa(self):
        self.journal.data["state"] = "awaiting_visual_qa"
        self.journal.event("awaiting_visual_qa", workflow=self.workflow)
        print(json.dumps({"state": "awaiting_visual_qa", "workflow": self.workflow,
                          "manifest": str(self.journal.path)}, ensure_ascii=False, indent=2), flush=True)
        input("مراجعة الوكيل البصرية: اضغط Enter لاستئناف تنظيف موارد الاختبار. ")
        self.journal.data["state"] = "visual_qa_pause_released"
        self.journal.event("visual_qa_pause_released")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="تمكين الدخول وإنشاء fixtures المعزولة على التجريبي")
    parser.add_argument("--confirm-site", default="")
    parser.add_argument("--env-file", type=Path, default=default_env_file())
    parser.add_argument("--state-dir", type=Path, default=Path.home() / ".local/state/namar_test/approval_routing_smoke")
    parser.add_argument("--cleanup-manifest", type=Path)
    parser.add_argument("--pause-before-cleanup", action="store_true",
                        help="وقفة اختيارية داخل PTY بعد نجاح الاختبارات لمراجعة الوكيل البصرية؛ Enter ينظف fixtures")
    parser.add_argument("--role", default="", help="دور قائم لدى المستخدم الثاني؛ Accounts User مفضل افتراضيًا")
    parser.add_argument("--timeout", type=int, default=45)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if not args.run:
        print(json.dumps({"mode": "dry_run", "network": False, "writes": False,
                          "guards": "--run --confirm-site مطابق للتجريبي؛ لا أسرار في manifest",
                          "actors": "Administrator بالتوكن ومستخدم قائم بدخول عادي، بلا تعديل أدوار",
                          "fixtures": "DocType مخصص وWorkflow واحد؛ مصدران أنشأهما المستخدم الثاني وثالث أنشأه Administrator",
                          "scenarios": ["عدة مستخدمين", "المالك أو عضو الدور الفعلي", "المالك والحقل والدور معًا", "إزالة التكرار", "صلاحيات الاعتماد الأصلية",
                                        "مصدر صالح مع غير صالح", "fallback للجميع غير المؤهلين", "تحديث القاعدة والحقل", "البحث والصفحات والعدادات"],
                          "cleanup": "finally مع تحقق بصمة كل مورد؛ الاستكمال الصريح بـ--cleanup-manifest",
                          "pause_before_cleanup": bool(args.pause_before_cleanup),
                          "retained_by_frappe": "سجل Deleted Document وجدول DocType الفيزيائي الفارغ؛ لا SQL/DROP"}, ensure_ascii=False, indent=2))
        return 0
    journal = runner = None
    try:
        env = run_config(args)
        state_dir = private_dir(args.state_dir)
        if args.cleanup_manifest:
            path = args.cleanup_manifest.expanduser().resolve()
            ensure(path.parent == state_dir and not args.cleanup_manifest.is_symlink(), "manifest التنظيف يجب أن يكون داخل مجلد السجل الخاص")
            ensure(stat.S_IMODE(path.stat().st_mode) == 0o600, "manifest يجب أن يكون خاصًا 0600")
            data = json.loads(path.read_text(encoding="utf-8"))
            ensure(data.get("schema_version") == 1 and data.get("site") == env["site"] and PREFIX_RE.fullmatch(data.get("prefix", "")),
                   "manifest غير صالح أو يخص موقعًا آخر")
            ensure(isinstance(data.get("targets"), list) and len(data["targets"]) <= MAX_TARGETS, "موارد manifest غير صالحة")
        else:
            prefix = "NAR Smoke " + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + " " + uuid4().hex[:8]
            path = state_dir / (prefix.replace(" ", "-") + ".json")
            ensure(not path.exists(), "مسار manifest موجود مسبقًا")
            data = {"schema_version": 1, "site": env["site"], "prefix": prefix, "created_at": now(),
                    "targets": [], "events": [], "cleanup_complete": False}
        journal = Journal(path, data)
        journal.flush()
        runner = Runner(env, args, journal)
        if args.cleanup_manifest:
            ensure(runner.a.call("frappe.auth.get_logged_user") == "Administrator", "التنظيف يتطلب Administrator على التجريبي")
            runner.cleanup()
        else:
            runner.preflight()
            try:
                runner.setup()
                runner.exercise()
                journal.data["tests_passed"] = True
                journal.flush()
                if args.pause_before_cleanup:
                    runner.pause_for_visual_qa()
            except Exception as exc:
                journal.event("exercise_failed", error=str(exc) if isinstance(exc, SmokeFailure) else type(exc).__name__)
                raise
            finally:
                runner.cleanup()
        print(json.dumps({"status": "passed", "manifest": str(path), "tests_passed": data.get("tests_passed", False),
                          "cleanup_complete": data["cleanup_complete"],
                          "cleanup_scope": data["cleanup_scope"],
                          "standard_frappe_retention": data["standard_frappe_retention"]}, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        error = str(exc) if isinstance(exc, SmokeFailure) else type(exc).__name__
        if journal:
            journal.data["failure"] = error
            journal.flush()
        print(json.dumps({"status": "failed", "error": error, "manifest": str(journal.path) if journal else None}, ensure_ascii=False), file=sys.stderr)
        return 1
    finally:
        if runner:
            runner.a.session.close()
            runner.b.session.close()


if __name__ == "__main__":
    raise SystemExit(main())
