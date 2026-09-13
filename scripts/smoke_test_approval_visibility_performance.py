#!/usr/bin/env python3
"""اختبار حجم حقيقي لتوجيه الموافقات، اختياري وعلى TEST فقط.

ينشئ 7500 مصدر معزول (4250 موجهًا +3250 افتراضيًا)، مع 6 صفوف أطفال لكل
مصدر؛ تولد Workflow Actions عبر hooks القياسية. insert_many لا يتجاوز 200.
التنظيف يستخدم delete_items بحد 10، فيبقى متزامنًا؛ Frappe يـcommit لكل عنصر
وقد يعيد محاولة عناصر فشلت داخل الدفعة نفسها. العميل لا يعيد دفعة فاشلة.
الافتراضي dry-run. التشغيل الحي يتطلب --run --confirm-site، وسجلًا خاصًا
خارج Git. لا يعدل حسابات المستخدمين أو Workflows الأعمال أو يرسل بريدًا.
تظل Deleted Document والجداول الفارغة ضمن احتفاظ Frappe القياسي، دون SQL.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import threading
import time
from urllib.parse import quote, urlparse
from uuid import uuid4

from smoke_test_approval_routing import (
    AUTO_ROLES, API, Client, FIELD, Journal, PROD_HOSTS, SmokeFailure,
    default_env_file, ensure, origin, private_dir, read_env,
)


TOTAL = 7500
CONFIGURED = 4250
CHILD_ROWS = 6
SAMPLES = 5
MAX_SECONDS = 3.0
PREFIX_RE = re.compile(r"^NAR Perf [0-9]{14} [a-f0-9]{8}$")
CONDITION = "doc.lines[0].qty > 0 and frappe.session.user == doc.approver"
HIDE_FIELD = "custom_followups_hide_from_approvals"
UNCERTAIN_HTTP = {408, 502, 503, 504, 520, 521, 522, 523, 524}
PATCH = "namar_test.patches.v0_0_9.configure_approval_visibility"


def digest(value):
    raw = value if isinstance(value, str) else json.dumps(value, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def chunks(values, size):
    values = list(values)
    return [values[start:start + size] for start in range(0, len(values), size)]


def source_groups(prefix, user_b, total=TOTAL, configured=CONFIGURED):
    ensure(0 < configured < total and configured % 2 == 0, "يلزم عدد موجه زوجي أصغر من الإجمالي")
    configured_type, baseline_type = prefix + " Routed", prefix + " Default"
    half = configured // 2
    return [
        {"doctype": configured_type, "actor": "B", "owner": user_b, "indices": range(1, half + 1)},
        {"doctype": configured_type, "actor": "A", "owner": "Administrator", "indices": range(half + 1, configured + 1)},
        {"doctype": baseline_type, "actor": "B", "owner": user_b, "indices": range(1, total - configured + 1)},
    ]


def source_payload(prefix, doctype, index, pending, approver, child_rows=CHILD_ROWS):
    name = f"{doctype} {index:05}"
    return {"doctype": doctype, "title": name, "smoke_marker": prefix,
            "approver": approver, "workflow_state": pending,
            "lines": [{"item_code": f"SMOKE-ITEM-{line:02}", "description": "بند اختبار معزول",
                       "qty": line + 1, "rate": 10 + line, "amount": (line + 1) * (10 + line),
                       "warehouse": "SMOKE Warehouse", "uom": "Nos"}
                      for line in range(child_rows)]}


def samples_pass(values, budget=MAX_SECONDS):
    return len(values) == SAMPLES and all(0 <= value <= budget for value in values)


class PerfJournal(Journal):
    """Small atomic recovery manifest plus append-only private transcript.

    Avoid rewriting thousands of synthetic backups inside every timed GET.
    Deterministic payload parameters and each intended source name are saved
    before insert_many; source and definition mutations retain before/after.
    """
    def __init__(self, path, data):
        super().__init__(path, data)
        self.lock = threading.RLock()
        self.transport = threading.local()
        self.transcript = path.with_suffix(".events.jsonl")

    def flush(self):
        with self.lock:
            super().flush()

    def event(self, event, **details):
        if event == "http_before":
            self.transport.last_status = None
        elif event == "http_after":
            self.transport.last_status = details.get("status")
        entry = {"event": event, "at": datetime.now(timezone.utc).isoformat(), **details}
        with self.lock:
            flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            fd = os.open(self.transcript, flags, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        return entry


class TrackedClient(Client):
    def request(self, *args, **kwargs):
        self.last_status = None
        try:
            return super().request(*args, **kwargs)
        finally:
            self.last_status = getattr(self.journal.transport, "last_status", None)

    def doc(self, doctype, name, *, missing=False):
        result = super().doc(doctype, name, missing=missing)
        if self.last_status == 404:
            ensure(missing, "المستند غير موجود")
            return None
        ensure(isinstance(result, dict) and result.get("name") == name, "GET200 لا يحتوي مستندًا صحيحًا؛ لا يُعامل كحذف")
        return result


def config(args):
    env = read_env(args.env_file)
    ensure(env.get("FRAPPE_TEST_SITE") and env.get("FRAPPE_TEST_TOKEN"), "بيانات TEST غير مكتملة")
    site = origin(env["FRAPPE_TEST_SITE"])
    ensure(args.confirm_site and origin(args.confirm_site) == site, "التأكيد لا يطابق TEST")
    denied = set(PROD_HOSTS)
    if env.get("FRAPPE_PROD_SITE"):
        denied.add(urlparse(origin(env["FRAPPE_PROD_SITE"])).hostname)
    ensure(urlparse(site).hostname not in denied, "التشغيل على PROD ممنوع")
    ensure(1 <= args.insert_batch <= 200, "دفعة الإنشاء يجب أن تكون بين 1 و200")
    ensure(1 <= args.cleanup_workers <= 2, "يسمح بعمليتي تنظيف متزامنتين فقط")
    ensure(5 <= args.timeout <= 120, "مهلة HTTP يجب أن تكون بين 5 و120 ثانية")
    if args.pause_before_cleanup:
        ensure(not args.cleanup_manifest and sys.stdin.isatty(), "الوقفة تتطلب PTY ولا تُجمع مع استكمال التنظيف")
    env["site"] = site
    if not args.cleanup_manifest:
        ensure(all(env.get(k) for k in ("BROWSER_LOGIN_URL", "BROWSER_LOGIN_EMAIL", "BROWSER_LOGIN_PASSWORD")),
               "بيانات الدخول العادي لـTEST غير مكتملة")
        ensure(origin(env["BROWSER_LOGIN_URL"], allow_path=True) == site,
               "لا تُرسل بيانات الدخول: BROWSER_LOGIN_URL لا يشير إلى TEST")
    return env


class PerformanceRunner:
    def __init__(self, env, args, journal):
        self.env, self.args, self.journal = env, args, journal
        self.prefix = journal.data["prefix"]
        self.routed, self.default = self.prefix + " Routed", self.prefix + " Default"
        self.child = self.prefix + " Line"
        self.pending, self.approved = self.prefix + " Pending", self.prefix + " Approved"
        self.action = self.prefix + " Approve"
        self.workflows = {dt: dt + " Flow" for dt in (self.routed, self.default)}
        self.clients = {"A": self.new_admin(), "B": TrackedClient(env["site"], "B", journal, args.timeout)}
        self.baseline = journal.data.get("baseline", {})
        self.performance_failures = []

    def new_admin(self):
        return TrackedClient(self.env["site"], "A", self.journal, self.args.timeout, self.env["FRAPPE_TEST_TOKEN"])

    @property
    def admin(self):
        return self.clients["A"]

    def rows(self, client, dt, fields, filters, *, length=1000, parent=None):
        args = {"doctype": dt, "fields": json.dumps(fields), "filters": json.dumps(filters),
                "limit_page_length": length, "order_by": "name asc"}
        if parent:
            args["parent"] = parent
        result = client.call("frappe.client.get_list", args=args)
        ensure(isinstance(result, list) and all(isinstance(row, dict) for row in result), "قائمة GET غير صحيحة؛ لا يُعامل null كقائمة فارغة")
        return result

    def count(self, client, dt, filters, *, parent=None):
        rows = self.rows(client, dt, ["count(name) as count"], filters, length=1, parent=parent)
        ensure(len(rows) == 1 and type(rows[0].get("count")) is int and rows[0]["count"] >= 0,
               "رد العد غير صحيح؛ يلزم count صريح بدل افتراض الصفر")
        return rows[0]["count"]

    def core_count(self, client):
        return self.count(client, "Workflow Action", {"status": "Open"})

    def owned_names(self, dt):
        return {name for batch in self.journal.data["source_batches"] if batch["doctype"] == dt for name in batch["names"]}

    def definition(self, dt, name):
        return next((row for row in self.journal.data["definitions"] if row["doctype"] == dt and row["name"] == name), None)

    def mutate(self, client, operation, invoke):
        """Never clean up underneath an HTTP mutation that may still commit.

        Timeout/gateway ambiguity stays in the recovery manifest. A subsequent
        GET returning no record does not clear it. The operator must establish
        request termination before explicitly resuming cleanup with evidence.
        """
        mutation_id = uuid4().hex
        with self.journal.lock:
            ensure(not self.journal.data.get("mutation_outcome_unknown"), "أوقفت الكتابات: توجد معالجة قد تظل قيد التنفيذ")
            pending = self.journal.data.setdefault("inflight_mutations", {})
            pending[mutation_id] = {"operation": operation, "started_at": datetime.now(timezone.utc).isoformat(), "actor": client.label}
            self.journal.flush()
        client.last_status = None
        try:
            value = invoke()
        except BaseException:
            with self.journal.lock:
                if client.last_status is None or client.last_status in UNCERTAIN_HTTP:
                    pending[mutation_id]["outcome"] = "unknown"
                    pending[mutation_id]["http_status"] = client.last_status
                    self.journal.data["mutation_outcome_unknown"] = True
                else:
                    pending.pop(mutation_id, None)
                self.journal.flush()
            raise
        else:
            with self.journal.lock:
                pending.pop(mutation_id, None)
                self.journal.flush()
            return value

    def assert_definition(self, dt, name, *, missing=False):
        target = self.definition(dt, name)
        ensure(target and name.startswith(self.prefix + " "), "تعريف خارج manifest")
        ensure(dt in ("DocType", "Workflow", "Workflow State", "Workflow Action Master"), "نوع تعريف ممنوع")
        doc = self.admin.doc(dt, name, missing=missing)
        if doc:
            ensure(all(doc.get(k) == v for k, v in target["fingerprint"].items()), "بصمة التعريف الحي لا تطابق الاختبار")
            if dt == "DocType" and target.get("schema_fields") is not None:
                ensure(self.schema_fields(doc) == target["schema_fields"] and doc.get("permissions", []) == target["schema_permissions"],
                       "تغير مخطط أو صلاحيات DocType المعزول؛ توقف التنظيف للمراجعة")
        return target, doc

    @staticmethod
    def schema_fields(doc):
        keys = ("fieldname", "fieldtype", "options", "reqd", "hidden", "read_only", "default")
        return [{key: row.get(key) for key in keys} for row in doc.get("fields", [])]

    def create_definition(self, dt, name, payload, fingerprint):
        ensure(self.admin.doc(dt, name, missing=True) is None, "اسم تعريف موجود؛ لن يُستبدل")
        target = {"doctype": dt, "name": name, "fingerprint": {**fingerprint, "owner": "Administrator"}, "deleted": False}
        self.journal.data["definitions"].append(target)
        self.journal.flush()
        self.journal.event("mutation_before", operation="create_definition", doctype=dt, name=name, before=None, intended=payload)
        doc = self.mutate(self.admin, "create_definition", lambda: self.admin.request("POST", "/api/resource/" + quote(dt, safe=""), body=payload, expected=(200, 201)))["data"]
        self.journal.event("mutation_after", operation="create_definition", doctype=dt, name=name, after=doc)
        ensure(doc["name"] == name and doc.get("owner") == "Administrator", "اسم أو مالك التعريف المنشأ غير مطابق")
        if dt == "DocType":
            target["schema_fields"] = self.schema_fields(doc)
            target["schema_permissions"] = doc.get("permissions", [])
            self.journal.flush()

    def preflight(self):
        ensure(self.admin.call("frappe.auth.get_logged_user") == "Administrator", "يلزم توكن Administrator على TEST")
        self.clients["B"].login(self.env["BROWSER_LOGIN_EMAIL"], self.env["BROWSER_LOGIN_PASSWORD"])
        self.user_b = self.clients["B"].call("frappe.auth.get_logged_user")
        ensure(self.user_b not in ("Administrator", "Guest"), "يلزم مستخدم عادي قائم")
        user = self.admin.doc("User", self.user_b)
        ensure(user.get("enabled") == 1 and user.get("user_type") == "System User", "المستخدم العادي غير مؤهل")
        roles = {row["role"] for row in user.get("roles", [])} - AUTO_ROLES
        ensure(roles, "لا يوجد دور قائم مناسب؛ توقف قبل الكتابة")
        self.role = self.args.role or ("Accounts User" if "Accounts User" in roles else sorted(roles)[0])
        ensure(self.role in roles and not self.admin.doc("Role", self.role).get("disabled"), "الدور المختار غير مؤهل")
        fields = self.rows(self.admin, "Custom Field", ["name"], {"dt": "Workflow Document State", "fieldname": ["in", [FIELD, HIDE_FIELD]]})
        ensure(len(fields) == 2, "حقلا التوجيه والإخفاء غير منشورين")
        patches = self.rows(self.admin, "Patch Log", ["name", "patch", "skipped"], {"patch": PATCH})
        ensure(len(patches) == 1 and int(patches[0].get("skipped") or 0) == 0, "ترحيل v9 غير مكتمل أو تم تخطيه")
        self.journal.data["actors"] = {"A": "Administrator", "B": self.user_b, "role": self.role}
        workflow_names = self.rows(self.admin, "Workflow", ["name"], {"is_active": 1})
        ensure(len(workflow_names) < 1000, "يلزم فحص Workflows دون اقتطاع")
        self.journal.data["real_workflows_before"] = [self.admin.doc("Workflow", row["name"]) for row in workflow_names]
        for actor, client in self.clients.items():
            counts = client.call(API + ".get_my_followups_counts")
            self.baseline[actor] = {"core": self.core_count(client), "routed": counts["counts"]["approvals"]}
        self.journal.data["baseline"] = self.baseline
        self.journal.flush()
        self.journal.event("preflight_passed", baseline=self.baseline, shared_role=self.role)

    def setup(self):
        self.create_definition("DocType", self.child, {
            "doctype": "DocType", "name": self.child, "custom": 1, "istable": 1, "module": "Custom", "description": self.prefix,
            "fields": [{"fieldname": name, "label": name.replace("_", " ").title(), "fieldtype": kind}
                       for name, kind in (("item_code", "Data"), ("description", "Small Text"), ("qty", "Float"),
                                          ("rate", "Currency"), ("amount", "Currency"), ("warehouse", "Data"), ("uom", "Data"))],
        }, {"custom": 1, "istable": 1, "description": self.prefix})
        for dt in (self.routed, self.default):
            self.create_definition("DocType", dt, {
                "doctype": "DocType", "name": dt, "custom": 1, "module": "Custom", "description": self.prefix,
                "autoname": "field:title", "title_field": "title", "track_changes": 0,
                "fields": [{"fieldname": "title", "label": "Title", "fieldtype": "Data", "reqd": 1},
                           {"fieldname": "smoke_marker", "label": "Smoke Marker", "fieldtype": "Data", "reqd": 1},
                           {"fieldname": "approver", "label": "Approver", "fieldtype": "Link", "options": "User"},
                           {"fieldname": "workflow_state", "label": "Workflow State", "fieldtype": "Link", "options": "Workflow State"},
                           {"fieldname": "lines", "label": "Lines", "fieldtype": "Table", "options": self.child}],
                "permissions": [{"role": self.role, "read": 1, "write": 1, "create": 1, "delete": 1}],
            }, {"custom": 1, "description": self.prefix})
        for state in (self.pending, self.approved):
            self.create_definition("Workflow State", state, {"doctype": "Workflow State", "workflow_state_name": state}, {"workflow_state_name": state})
        self.create_definition("Workflow Action Master", self.action, {"doctype": "Workflow Action Master", "workflow_action_name": self.action}, {"workflow_action_name": self.action})
        for dt, name in self.workflows.items():
            self.create_definition("Workflow", name, {"doctype": "Workflow", "workflow_name": name,
                "document_type": dt, "is_active": 1, "send_email_alert": 0, "workflow_state_field": "workflow_state",
                "states": [{"state": state, "doc_status": "0", "allow_edit": self.role, "send_email": 0} for state in (self.pending, self.approved)],
                "transitions": [{"state": self.pending, "action": self.action, "next_state": self.approved, "allowed": self.role, "allow_self_approval": 1}],
            }, {"document_type": dt, "workflow_name": name, "send_email_alert": 0})
        started = time.monotonic()
        for group in source_groups(self.prefix, self.user_b):
            for indices in chunks(group["indices"], self.args.insert_batch):
                payloads = [source_payload(self.prefix, group["doctype"], index, self.pending, self.user_b) for index in indices]
                names = [doc["title"] for doc in payloads]
                batch = {"doctype": group["doctype"], "actor": group["actor"], "owner": group["owner"], "names": names,
                         "created": False, "deleted": [], "payload_sha256": digest(payloads)}
                self.journal.data["source_batches"].append(batch)
                self.journal.flush()
                self.journal.event("mutation_before", operation="insert_many", before=None, intended=batch)
                tick = time.monotonic()
                creator = self.clients[group["actor"]]
                inserted = self.mutate(creator, "insert_many", lambda: creator.call("frappe.client.insert_many", args={"docs": payloads}, post=True))
                self.journal.event("mutation_after", operation="insert_many", names=inserted, elapsed_seconds=time.monotonic() - tick)
                ensure(inserted == names, "أسماء insert_many لا تطابق الدفعة المعتمدة")
                batch["created"] = True
                self.journal.flush()
        self.journal.data["setup_seconds"] = time.monotonic() - started
        self.verify_population()

    def verify_population(self):
        evidence = {}
        for dt, count in ((self.routed, CONFIGURED), (self.default, TOTAL - CONFIGURED)):
            actual = self.count(self.admin, dt, {"smoke_marker": self.prefix})
            actions = self.count(self.admin, "Workflow Action", {"reference_doctype": dt, "status": "Open"})
            child_count = self.count(self.admin, self.child, {"parenttype": dt}, parent=dt)
            ensure(actual == actions == count and child_count == count * CHILD_ROWS, "حجم المصادر أو الموافقات أو الأطفال غير مطابق")
            evidence[dt] = {"sources": actual, "open_actions": actions, "child_rows": child_count}
        for actor, client in self.clients.items():
            ensure(self.core_count(client) == self.baseline[actor]["core"] + TOTAL, "تغيرت الموافقات غير المعزولة أو الصلاحيات أثناء الإنشاء")
        self.journal.data["population"] = evidence
        self.remember_actions()
        actions = self.journal.data["workflow_actions"]
        ensure(len(actions) == TOTAL and len({(r["reference_doctype"], r["reference_name"]) for r in actions.values()}) == TOTAL,
               "يلزم موافقة قياسية واحدة لكل مصدر دون تكرار أو نقص")
        self.journal.flush()
        self.journal.event("population_verified", evidence=evidence)

    def set_rule(self, targets, *, condition="", hidden=False):
        _, before = self.assert_definition("Workflow", self.workflows[self.routed])
        states, transitions = deepcopy(before["states"]), deepcopy(before["transitions"])
        for row in states:
            if row["state"] == self.pending:
                row[FIELD] = json.dumps({"version": 1, "targets": targets})
                row[HIDE_FIELD] = int(hidden)
        for row in transitions:
            row["condition"] = condition
        intended = {"states": states, "transitions": transitions}
        self.journal.event("mutation_before", operation="set_fixture_rule", before=before, intended=intended)
        after = self.mutate(self.admin, "set_fixture_rule", lambda: self.admin.request("PUT", "/api/resource/Workflow/" + quote(self.workflows[self.routed], safe=""), body=intended))["data"]
        self.journal.event("mutation_after", operation="set_fixture_rule", after=after)

    def change_approver(self, name, approver):
        ensure(name in self.owned_names(self.routed), "مصدر تعديل خارج manifest")
        before = self.admin.doc(self.routed, name)
        ensure(before.get("smoke_marker") == self.prefix and before.get("title") == name, "بصمة مصدر التعديل غير مطابقة")
        self.journal.event("mutation_before", operation="change_fixture_approver", before=before, intended={"approver": approver})
        after = self.mutate(self.admin, "change_fixture_approver", lambda: self.admin.request("PUT", "/api/resource/" + quote(self.routed, safe="") + "/" + quote(name, safe=""), body={"approver": approver}))["data"]
        self.journal.event("mutation_after", operation="change_fixture_approver", after=after)
        self.journal.data.setdefault("source_overrides", {})[name] = {"approver": approver}
        self.journal.flush()

    def verify_actor(self, actor, expected, *, fallback=False):
        client = self.clients[actor]
        count = self.baseline[actor]["routed"] + TOTAL - CONFIGURED + len(expected)
        page = client.call(API + ".get_approvals", args={"search": self.routed, "search_scope": "doctype", "page_length": 25})
        names = [row["reference_name"] for row in page["items"]]
        ensure(len(names) == min(25, len(expected)) and len(names) == len(set(names)) and set(names) <= expected,
               f"القائمة الموجهة أو إزالة التكرار غير صحيحة للمستخدم {actor}")
        ensure(page["counts"]["open"] == count, "عداد القائمة لا يساوي خط الأساس الحقيقي مع العناصر المرئية")
        ensure(page["has_more"] is (len(expected) > 25), "has_more لا يعكس التوجيه قبل pagination")
        for item in page["items"]:
            ensure(item["routing"]["fallback"] is fallback, "fallback لا يطابق المستلمين الفعليين")
        if not expected:
            action = next(row for row in self.journal.data["workflow_actions"].values() if row["reference_doctype"] == self.routed)
            refused = client.request("GET", "/api/method/" + API + ".get_approval_detail",
                                     params={"action_name": action["name"]}, expected=(403,))
            ensure(refused.get("exc_type") == "PermissionError", "تفاصيل الموافقة المخفية لم تُرفض بالهوية الحقيقية")
        if expected:
            sample = sorted(expected)[0]
            search = client.call(API + ".get_approvals", args={"search": sample, "search_scope": "document", "page_length": 25})
            ensure([row["reference_name"] for row in search["items"]] == [sample], "البحث المحدد لا يعكس المستلمين")
            if len(expected) > 25:
                second = client.call(API + ".get_approvals", args={"search": self.routed, "search_scope": "doctype", "limit_start": 25, "page_length": 25})
                second_names = [row["reference_name"] for row in second["items"]]
                ensure(len(second_names) == min(25, len(expected) - 25) and set(second_names) <= expected
                       and not set(names) & set(second_names), "تداخل أو نقص عند الصفحة الثانية بعد التوجيه")
        counts = client.call(API + ".get_my_followups_counts")
        ensure(counts["counts"]["approvals"] == count == counts["attention_counts"]["approvals"], "العداد والشارة غير مطابقين")
        ensure(self.core_count(client) == self.baseline[actor]["core"] + TOTAL, "تغير نطاق الموافقات غير المعزولة أثناء القياس")
        return count

    def measure(self, label, expected):
        client = self.clients["B"]
        measurements = {"page_25": [], "counts": []}
        for index in range(SAMPLES):
            for endpoint, method, args in (("page_25", ".get_approvals", {"page_length": 25}), ("counts", ".get_my_followups_counts", {})):
                start = time.perf_counter()
                payload = client.call(API + method, args=args)
                elapsed = time.perf_counter() - start
                measurements[endpoint].append(elapsed)
                value = payload["counts"]["open"] if endpoint == "page_25" else payload["counts"]["approvals"]
                ensure(value == expected, "تغير العداد بين قياسات الأداء")
                if endpoint == "page_25":
                    ensure(len(payload["items"]) == 25, "صفحة الأداء لا تحتوي 25 عنصرًا")
                self.journal.event("performance_sample", scenario=label, actor="B", endpoint=endpoint, sample=index + 1,
                                   elapsed_seconds=elapsed, budget_seconds=MAX_SECONDS, passed=elapsed <= MAX_SECONDS)
        passed = all(samples_pass(values) for values in measurements.values())
        self.journal.data["measurements"].append({"scenario": label, "actor": "B", "samples": measurements, "passed": passed})
        if not passed:
            self.performance_failures.append(label)
        self.journal.flush()

    def scenario(self, label, targets, visible, *, fallback=False, condition="", hidden=False):
        self.set_rule(targets, condition=condition, hidden=hidden)
        expected_counts = {actor: self.verify_actor(actor, expected, fallback=fallback) for actor, expected in visible.items()}
        self.measure(label, expected_counts["B"])
        self.journal.event("scenario_verified", scenario=label, visible_counts={k: len(v) for k, v in visible.items()})

    def exercise(self):
        all_names = self.owned_names(self.routed)
        b_owned = {name for batch in self.journal.data["source_batches"] if batch["doctype"] == self.routed and batch["actor"] == "B" for name in batch["names"]}
        a_owned = all_names - b_owned
        both = {"A": all_names, "B": all_names}
        a_only, b_only = {"A": all_names, "B": set()}, {"A": set(), "B": all_names}
        user_a, user_b = {"type": "user", "user": "Administrator"}, {"type": "user", "user": self.user_b}
        self.scenario("stage_hidden_for_both", [], {"A": set(), "B": set()}, hidden=True)
        self.scenario("stage_unhidden_same_workflow", [], both)
        self.scenario("user_hides_from_B", [user_a], a_only)
        self.scenario("user_unhides_for_B", [user_b], b_only)
        self.scenario("two_users_union", [user_a, user_b, user_b], both)
        self.scenario("owner_split", [{"type": "owner"}], {"A": a_owned, "B": b_owned})
        self.scenario("actual_role_members", [{"type": "role", "role": self.role}], b_only)
        self.scenario("owner_or_role", [{"type": "owner"}, {"type": "role", "role": self.role}], {"A": a_owned, "B": all_names})
        self.scenario("invalid_with_valid_no_broad_fallback", [{"type": "user", "user": "Guest"}, user_b], b_only)
        self.scenario("all_invalid_role_fallback", [{"type": "user", "user": "Guest"}], both, fallback=True)
        self.scenario("field_all_B", [{"type": "field", "field": "approver"}], b_only)
        # Conditions load genuine child rows and evaluate each candidate's own
        # identity. No real user permissions or workflow transitions are changed.
        self.scenario("children_and_candidate_session_condition", [user_a, user_b], b_only, condition=CONDITION)
        sample = sorted(all_names)[-1]
        self.change_approver(sample, "Administrator")
        changed = {"A": {sample}, "B": all_names - {sample}}
        expected = {actor: self.verify_actor(actor, names) for actor, names in changed.items()}
        self.measure("condition_field_change_without_workflow_rename", expected["B"])
        self.change_approver(sample, self.user_b)
        self.scenario("restored_rule_same_workflow_names", [user_b], b_only)
        self.native_approval_proof(sorted(b_owned)[0])
        self.journal.data["correctness_passed"] = True
        self.journal.data["performance_passed"] = not self.performance_failures
        self.journal.data["performance_failures"] = self.performance_failures
        self.journal.flush()

    def native_approval_proof(self, source_name):
        """Run after all 7500-action timings; keep one terminal fixture for QA.

        Hiding My Followups must not revoke the native workflow permission.
        This source is never reopened and remains in the cleanup manifest.
        """
        action = next(row for row in self.journal.data["workflow_actions"].values()
                      if row["reference_doctype"] == self.routed and row["reference_name"] == source_name)
        self.set_rule([], hidden=True)
        client = self.clients["B"]
        refused = client.request("GET", "/api/method/" + API + ".get_approval_detail",
                                 params={"action_name": action["name"]}, expected=(403,))
        ensure(refused.get("exc_type") == "PermissionError", "تفاصيل العنصر ليست مخفية قبل الاعتماد القياسي")
        before = client.doc(self.routed, source_name)
        ensure(before.get("owner") == self.user_b and before.get("smoke_marker") == self.prefix,
               "مصدر الاعتماد القياسي ليس المصدر المعزول المملوك للمستخدم B")
        before_count = client.call(API + ".get_my_followups_counts")["counts"]["approvals"]
        self.journal.event("mutation_before", operation="native_fixture_workflow_approval", before=before, action=self.action)
        after = self.mutate(client, "native_fixture_workflow_approval", lambda: client.call(
            "frappe.model.workflow.apply_workflow", args={"doc": json.dumps(before), "action": self.action}, post=True))
        self.journal.event("mutation_after", operation="native_fixture_workflow_approval", after=after)
        ensure(after.get("workflow_state") == self.approved, "الإخفاء عطل صلاحية الاعتماد القياسية")
        completed = self.admin.doc("Workflow Action", action["name"])
        ensure(completed.get("status") == "Completed", "لم تنته الموافقة بعد الانتقال القياسي")
        self.journal.data["completed_sources"] = [source_name]
        self.journal.data["expected_open_fixture_actions"] = TOTAL - 1
        self.journal.flush()
        ensure(self.core_count(client) == self.baseline["B"]["core"] + TOTAL - 1, "العدد القياسي لم ينقص بعد الاعتماد")
        ensure(client.call(API + ".get_my_followups_counts")["counts"]["approvals"] == before_count,
               "العنصر المخفي انتهاؤه غيّر عداد العناصر المرئية")
        self.set_rule([{"type": "user", "user": "Administrator"}, {"type": "user", "user": self.user_b}])
        for actor, connection in self.clients.items():
            expected = self.baseline[actor]["routed"] + TOTAL - 1
            ensure(connection.call(API + ".get_my_followups_counts")["counts"]["approvals"] == expected,
                   "إظهار القاعدة بعد الاعتماد أعاد الموافقة المنتهية إلى العدد")
            page = connection.call(API + ".get_approvals", args={"search": self.routed, "search_scope": "doctype", "page_length": 25})
            ensure(len(page["items"]) == 25 and page["counts"]["open"] == expected,
                   "قائمة تهيئة المراجعة لا تطابق الحسابين")
        workflow = self.admin.doc("Workflow", self.workflows[self.routed])
        state = next(row for row in workflow["states"] if row["state"] == self.pending)
        snapshot = {FIELD: state.get(FIELD), HIDE_FIELD: int(state.get(HIDE_FIELD) or 0)}
        self.journal.data["qa_routing_snapshot"] = {"workflow": workflow["name"], "state": self.pending,
                                                     "values": snapshot, "sha256": digest(snapshot)}
        self.journal.data["native_approval_unchanged"] = True
        self.journal.flush()

    def delete_source_batch(self, client, dt, names):
        ensure(0 < len(names) <= 10 and dt in (self.routed, self.default), "دفعة تنظيف خارج الحدود")
        ensure(set(names) <= self.owned_names(dt), "أسماء تنظيف خارج manifest")
        rows = self.rows(client, dt, ["*"], {"name": ["in", names]}, length=10)
        owners = {name: batch["owner"] for batch in self.journal.data["source_batches"] if batch["doctype"] == dt for name in batch["names"]}
        children = self.rows(client, self.child, ["*"], {"parenttype": dt, "parent": ["in", names]},
                             length=1000, parent=dt)
        for row in rows:
            expected_state = self.approved if row["name"] in self.journal.data.get("completed_sources", []) else self.pending
            expected_approver = self.journal.data.get("source_overrides", {}).get(row["name"], {}).get("approver", self.journal.data["actors"]["B"])
            expected_payload = source_payload(self.prefix, dt, int(row["name"].rsplit(" ", 1)[1]), expected_state, expected_approver)
            ensure(row["name"] in names and row["title"] == row["name"] and row["owner"] == owners[row["name"]]
                   and row["smoke_marker"] == self.prefix and row.get("workflow_state") == expected_state
                   and row.get("approver") == expected_payload["approver"], "بصمة مصدر التنظيف لا تطابق manifest")
            actual_children = sorted((child for child in children if child["parent"] == row["name"]), key=lambda child: child["idx"])
            ensure(len(actual_children) == CHILD_ROWS and all(
                child.get("parentfield") == "lines" and all(child.get(key) == value for key, value in expected.items())
                for child, expected in zip(actual_children, expected_payload["lines"])),
                "تغيرت بيانات أطفال المصدر؛ محفوظة ولم تُحذف")
        existing = [row["name"] for row in rows]
        if existing:
            self.journal.event("mutation_before", operation="delete_items", doctype=dt, names=existing,
                               before=rows, child_rows=children, native_per_item_commit=True, native_partial_retry=True)
            undeleted = self.mutate(client, "delete_items", lambda: client.call("frappe.desk.reportview.delete_items", args={"doctype": dt, "items": json.dumps(existing)}, post=True))
            self.journal.event("mutation_after", operation="delete_items", doctype=dt, attempted=existing, undeleted=undeleted)
            ensure(undeleted in (None, []), "فشلت عناصر من دفعة التنظيف؛ لن يعيدها العميل")
            remaining = self.rows(client, dt, ["name"], {"name": ["in", existing]}, length=10)
            ensure(not remaining, "نجح HTTP لكن بقيت مصادر من الدفعة")
        with self.journal.lock:
            for batch in self.journal.data["source_batches"]:
                if batch["doctype"] == dt:
                    batch["deleted"] = sorted(set(batch["deleted"]) | (set(batch["names"]) & set(names)))
            self.journal.flush()

    def remember_actions(self):
        """Capture exact action IDs while parents still exist, including an
        insert batch whose HTTP result was lost. Never accept unrelated refs.
        """
        actions = self.journal.data.setdefault("workflow_actions", {})
        offset = 0
        while True:
            page = self.admin.call("frappe.client.get_list", args={"doctype": "Workflow Action",
                "fields": json.dumps(["name", "reference_doctype", "reference_name"]),
                "filters": json.dumps({"reference_doctype": ["in", [self.routed, self.default]]}),
                "limit_start": offset, "limit_page_length": 1000, "order_by": "name asc"})
            ensure(isinstance(page, list) and all(isinstance(row, dict) and all(
                isinstance(row.get(key), str) and row[key] for key in ("name", "reference_doctype", "reference_name")) for row in page),
                "قراءة معرفات الموافقات غير صحيحة؛ لن يبدأ تنظيف المصادر")
            for row in page:
                ensure(row["reference_name"] in self.owned_names(row["reference_doctype"]), "وجدت موافقة خارج مصادر manifest")
                if row["name"] not in actions:
                    actions[row["name"]] = {**row, "deleted": False}
            self.journal.flush()
            if len(page) < 1000:
                break
            offset += len(page)

    def delete_action_batch(self, client, names):
        ensure(0 < len(names) <= 10, "لا يسمح بتجديل حذف الموافقات في الخلفية")
        records = self.journal.data["workflow_actions"]
        ensure(set(names) <= records.keys(), "موافقة خارج manifest")
        rows = self.rows(client, "Workflow Action", ["name", "reference_doctype", "reference_name"], {"name": ["in", names]}, length=10)
        for row in rows:
            expected = records[row["name"]]
            ensure(expected["reference_doctype"] in (self.routed, self.default)
                   and expected["reference_name"] in self.owned_names(expected["reference_doctype"])
                   and row["reference_doctype"] == expected["reference_doctype"] and row["reference_name"] == expected["reference_name"],
                   "بصمة موافقة التنظيف لا تطابق المصدر")
        children = self.rows(client, "Workflow Action Permitted Role", ["name", "parent", "role"],
                             {"parent": ["in", names]}, length=1000, parent="Workflow Action")
        existing = [row["name"] for row in rows]
        if existing:
            self.journal.event("mutation_before", operation="delete_workflow_actions", before=rows, child_rows=children,
                               native_per_item_commit=True, native_partial_retry=True)
            failed = self.mutate(client, "delete_workflow_actions", lambda: client.call("frappe.desk.reportview.delete_items", args={"doctype": "Workflow Action", "items": json.dumps(existing)}, post=True))
            self.journal.event("mutation_after", operation="delete_workflow_actions", attempted=existing, undeleted=failed)
            ensure(failed in (None, []), "بقيت موافقات لم تُحذف؛ لا إعادة تلقائية من العميل")
        ensure(not self.rows(client, "Workflow Action", ["name"], {"name": ["in", names]}, length=10), "بقيت موافقات بعد طلب الحذف")
        ensure(not self.rows(client, "Workflow Action Permitted Role", ["name"], {"parent": ["in", names]}, length=1000, parent="Workflow Action"),
               "بقيت صفوف أدوار موافقات يتيمة؛ لم يبدأ حذف المصادر")
        with self.journal.lock:
            for name in names:
                records[name]["deleted"] = True
            self.journal.flush()

    def cleanup_tasks(self, tasks, callback, errors):
        clients = [self.new_admin() for _ in range(self.args.cleanup_workers)]
        try:
            with ThreadPoolExecutor(max_workers=self.args.cleanup_workers) as pool:
                for start in range(0, len(tasks), self.args.cleanup_workers):
                    current = tasks[start:start + self.args.cleanup_workers]
                    futures = [pool.submit(callback, client, *task) for client, task in zip(clients, current)]
                    for future in futures:
                        try:
                            future.result()
                        except Exception as exc:
                            errors.append(str(exc) if isinstance(exc, SmokeFailure) else type(exc).__name__)
                    if errors:
                        break
        finally:
            for client in clients:
                client.session.close()

    def cleanup(self):
        ensure(not self.journal.data.get("mutation_outcome_unknown") and not self.journal.data.get("inflight_mutations"),
               "أُجّل التنظيف: يلزم إثبات انتهاء الطلب الملتبس؛ الغياب المؤقت في GET لا يثبت rollback")
        errors = []
        for definition in self.journal.data["definitions"]:
            if definition["doctype"] == "DocType" and not definition.get("deleted"):
                self.assert_definition("DocType", definition["name"], missing=True)
        # Delete actions first via Document deletion, which removes the child
        # permitted_roles rows. Source on_trash otherwise uses db.delete on the
        # action parent only, making an action-count-zero check insufficient.
        self.remember_actions()
        action_names = sorted(name for name, row in self.journal.data["workflow_actions"].items() if not row["deleted"])
        self.cleanup_tasks([(batch,) for batch in chunks(action_names, 10)], self.delete_action_batch, errors)
        tasks = []
        for dt in (self.routed, self.default):
            # A failed/uncertain insert batch still has pre-recorded intended
            # names. Nonexistent names are observed, not inserted or retried.
            names = sorted({name for batch in self.journal.data["source_batches"] if batch["doctype"] == dt
                            for name in set(batch["names"]) - set(batch["deleted"])})
            tasks.extend((dt, group) for group in chunks(names, 10))
        if not errors:
            self.cleanup_tasks(tasks, self.delete_source_batch, errors)
        if not errors:
            for dt in (self.routed, self.default):
                if self.definition("DocType", dt) and not self.definition("DocType", dt).get("deleted") and self.admin.doc("DocType", dt, missing=True):
                    ensure(self.count(self.admin, dt, {}) == 0, "بقيت مصادر؛ لا يُحذف تعريفها")
                    ensure(self.count(self.admin, "Workflow Action", {"reference_doctype": dt}) == 0, "بقيت موافقات الاختبار")
                    ensure(self.count(self.admin, self.child, {"parenttype": dt}, parent=dt) == 0, "بقيت صفوف أطفال المصادر")
            order = {"Workflow": 0, "Workflow State": 1, "Workflow Action Master": 2, "DocType": 3}
            definitions = sorted(self.journal.data["definitions"], key=lambda row: (order[row["doctype"]], row["name"] == self.child))
            for definition in definitions:
                if definition.get("deleted"):
                    continue
                dt, name = definition["doctype"], definition["name"]
                try:
                    _, before = self.assert_definition(dt, name, missing=True)
                    if before:
                        self.journal.event("mutation_before", operation="delete_definition", doctype=dt, name=name, before=before)
                        self.mutate(self.admin, "delete_definition", lambda: self.admin.request("DELETE", "/api/resource/" + quote(dt, safe="") + "/" + quote(name, safe=""), expected=(200, 202)))
                        ensure(self.admin.doc(dt, name, missing=True) is None, "بقي تعريف بعد حذفه")
                        self.journal.event("mutation_after", operation="delete_definition", doctype=dt, name=name, after=None)
                    definition["deleted"] = True
                    self.journal.flush()
                except Exception as exc:
                    errors.append(str(exc) if isinstance(exc, SmokeFailure) else type(exc).__name__)
                    break
        self.journal.data["cleanup_errors"] = errors
        self.journal.data["cleanup_complete"] = not errors and all(row.get("deleted") for row in self.journal.data["definitions"])
        self.journal.data["retained_by_frappe"] = ["Deleted Document recovery records", "empty custom DocType database tables"]
        self.journal.flush()
        ensure(self.journal.data["cleanup_complete"], "تنظيف غير مكتمل؛ راجع manifest قبل استكماله صراحة")

    def verify_real_workflows(self):
        differences = []
        for before in self.journal.data.get("real_workflows_before", []):
            after = self.admin.doc("Workflow", before["name"])
            if after != before:
                differences.append({"name": before["name"], "before_sha256": digest(before), "after_sha256": digest(after)})
        self.journal.data["real_workflows_unchanged"] = not differences
        self.journal.data["external_workflow_changes"] = differences
        self.journal.flush()
        if differences:
            self.journal.event("external_workflow_change_warning", changes=differences, restored=False)

    def pause_for_review(self):
        ensure(not self.journal.data.get("mutation_outcome_unknown"), "لا وقفة مراجعة أثناء طلب التعديل الملتبس")
        state = "awaiting_visual_qa" if self.journal.data.get("performance_passed") else "awaiting_performance_review"
        self.journal.data["state"] = state
        self.journal.flush()
        self.journal.event(state, workflow=self.workflows[self.routed])
        print(json.dumps({"state": state, "workflow": self.workflows[self.routed], "manifest": str(self.journal.path),
                          "performance_failures": self.journal.data.get("performance_failures", []),
                          "read_request_may_be_running": self.journal.data.get("read_request_may_be_running", False),
                          "cleanup_pending": True}, ensure_ascii=False, indent=2), flush=True)
        if self.journal.data.get("read_request_may_be_running"):
            print("انقطع طلب قراءة أو انتهت مهلة رصده. لا تضغط Enter حتى تتحقق من انتهاء طلب الخادم؛ غياب المخرجات أو GET404 وحده لا يثبت ذلك.", flush=True)
        input("مراجعة الوكيل: اضغط Enter لبدء تنظيف مصادر وموافقات الاختبار. ")
        self.journal.data["state"] = "review_released"
        self.journal.flush()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--confirm-site", default="")
    parser.add_argument("--env-file", type=Path, default=default_env_file())
    parser.add_argument("--state-dir", type=Path, default=Path.home() / ".local/state/namar_test/approval_visibility_performance")
    parser.add_argument("--cleanup-manifest", type=Path)
    parser.add_argument("--pause-before-cleanup", action="store_true",
                        help="وقفة PTY بعد القياسات للمراجعة البصرية/الأداء؛ تبقي fixtures حتى Enter")
    parser.add_argument("--settled-request-evidence", default="",
                        help="مرجع فحص يؤكد انتهاء الطلب الملتبس قبل استكمال التنظيف؛ ليس مجرد GET404 أو مهلة مفترضة")
    parser.add_argument("--insert-batch", type=int, default=200)
    parser.add_argument("--cleanup-workers", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--role", default="")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if not args.run:
        print(json.dumps({"mode": "dry_run", "network": False, "writes": False, "actions": TOTAL,
                          "configured_sources": CONFIGURED, "default_sources": TOTAL - CONFIGURED,
                          "child_rows_per_source": CHILD_ROWS, "total_child_rows": TOTAL * CHILD_ROWS,
                          "measurements_per_endpoint_per_scenario": SAMPLES, "page_length": 25, "max_seconds_each": MAX_SECONDS,
                          "normal_account": "existing TEST browser login", "cleanup_batch": 10,
                          "pause_before_cleanup": args.pause_before_cleanup,
                          "cleanup_workers": args.cleanup_workers, "no_sql_or_public_test_endpoint": True}, ensure_ascii=False, indent=2))
        return 0
    runner = journal = None
    try:
        env = config(args)
        directory = private_dir(args.state_dir)
        if args.cleanup_manifest:
            path = args.cleanup_manifest.expanduser().resolve()
            ensure(path.parent == directory and not args.cleanup_manifest.is_symlink() and stat.S_IMODE(path.stat().st_mode) == 0o600,
                   "manifest التنظيف يجب أن يكون خاصًا داخل مجلد السجل")
            data = json.loads(path.read_text())
            ensure(data.get("schema") == "approval_visibility_performance_v1" and data.get("site") == env["site"]
                   and PREFIX_RE.fullmatch(data.get("prefix", "")), "manifest غير صالح للموقع")
            if data.get("mutation_outcome_unknown") or data.get("inflight_mutations"):
                ensure(args.settled_request_evidence.strip(),
                       "لا يُستكمل التنظيف قبل إثبات انتهاء الطلب؛ مرر مرجع التحقق في --settled-request-evidence")
                data["settled_request_evidence"] = args.settled_request_evidence.strip()
                data["settled_inflight_history"] = deepcopy(data.get("inflight_mutations", {}))
                data["inflight_mutations"] = {}
                data["mutation_outcome_unknown"] = False
        else:
            prefix = "NAR Perf " + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + " " + uuid4().hex[:8]
            path = directory / (prefix.replace(" ", "-") + ".json")
            ensure(not path.exists(), "manifest موجود مسبقًا")
            data = {"schema": "approval_visibility_performance_v1", "site": env["site"], "prefix": prefix,
                    "definitions": [], "source_batches": [], "baseline": {}, "measurements": [],
                    "payload_generator": {"version": 1, "total": TOTAL, "configured": CONFIGURED, "child_rows": CHILD_ROWS},
                    "cleanup_complete": False}
        journal = PerfJournal(path, data)
        journal.flush()
        runner = PerformanceRunner(env, args, journal)
        if args.cleanup_manifest:
            ensure(runner.admin.call("frappe.auth.get_logged_user") == "Administrator", "يتطلب التنظيف Administrator على TEST")
            runner.cleanup()
            runner.verify_real_workflows()
        else:
            runner.preflight()
            setup_complete = False
            try:
                runner.setup()
                setup_complete = True
                runner.exercise()
                if args.pause_before_cleanup:
                    runner.pause_for_review()
            except Exception as exc:
                journal.event("exercise_failed", error=str(exc) if isinstance(exc, SmokeFailure) else type(exc).__name__)
                if setup_complete and args.pause_before_cleanup and not data.get("mutation_outcome_unknown") and not data.get("inflight_mutations"):
                    data["correctness_passed"] = False
                    data["performance_passed"] = False
                    data["review_error"] = str(exc) if isinstance(exc, SmokeFailure) else type(exc).__name__
                    data["read_request_may_be_running"] = any(
                        getattr(connection, "last_status", None) is None or connection.last_status in UNCERTAIN_HTTP
                        for connection in runner.clients.values()
                    )
                    journal.flush()
                    runner.pause_for_review()
                raise
            finally:
                if data.get("mutation_outcome_unknown") or data.get("inflight_mutations"):
                    journal.event("cleanup_deferred_unknown_mutation", inflight=data.get("inflight_mutations"), automatic_retry=False)
                else:
                    runner.cleanup()
                    runner.verify_real_workflows()
        passed = data.get("cleanup_complete") and (args.cleanup_manifest or data.get("correctness_passed") and data.get("performance_passed"))
        print(json.dumps({"status": "passed" if passed else "failed", "manifest": str(path),
                          "correctness_passed": data.get("correctness_passed"), "performance_passed": data.get("performance_passed"),
                          "performance_failures": data.get("performance_failures", []), "cleanup_complete": data.get("cleanup_complete")}, ensure_ascii=False, indent=2))
        return 0 if passed else 1
    except Exception as exc:
        error = str(exc) if isinstance(exc, SmokeFailure) else type(exc).__name__
        if journal:
            journal.data["failure"] = error
            journal.flush()
        print(json.dumps({"status": "failed", "error": error, "manifest": str(journal.path) if journal else None}, ensure_ascii=False), file=sys.stderr)
        return 1
    finally:
        if runner:
            for client in runner.clients.values():
                client.session.close()


if __name__ == "__main__":
    raise SystemExit(main())
