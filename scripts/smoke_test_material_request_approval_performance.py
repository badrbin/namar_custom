#!/usr/bin/env python3
"""Measure the real Material Request approval controller on TEST only.

Dry-run is the default. Live mode temporarily changes only routing/hide on
the completed Workflow state, then restores its original values. No Material
Request, user, role, or Workflow Action is written by this client. Standard
Workflow.save hooks still run: zero blank workflow states is checked before
every save, and real record fingerprints are compared after restoration.
Administrator handles setup/restoration only; timings and search use the
existing ordinary TEST login with that user's native permission boundary.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from urllib.parse import urlparse
from uuid import uuid4

from smoke_test_approval_routing import (
    API, FIELD, PROD_HOSTS, SmokeFailure, default_env_file, ensure, origin, private_dir, read_env,
)
from smoke_test_approval_visibility_performance import (
    HIDE_FIELD, MAX_SECONDS, PATCH, SAMPLES, UNCERTAIN_HTTP,
    PerfJournal, PerformanceRunner, TrackedClient, samples_pass,
)

STATE = "مكتمل"
DOCTYPE = "Material Request"
OWNER = json.dumps({"version": 1, "targets": [{"type": "owner"}]}, separators=(",", ":"))
METADATA = {"modified", "modified_by"}


def semantic(value):
    if isinstance(value, dict):
        return {key: semantic(item) for key, item in value.items() if key not in METADATA}
    if isinstance(value, list):
        return [semantic(item) for item in value]
    return value


def target_row(doc, row_name):
    rows = [row for row in doc.get("states", []) if row.get("name") == row_name and row.get("state") == STATE]
    ensure(len(rows) == 1, "تغير صف المرحلة المستهدف؛ لا كتابة")
    return rows[0]


def active_workflow_name(workflows):
    active = [row.get("name") for row in workflows if row.get("is_active")]
    ensure(len(active) == 1 and isinstance(active[0], str) and active[0],
           "يلزم Workflow نشط وحيد لطلبات المواد؛ لا نفترض اسمًا أو نختار بين أكثر من Workflow")
    return active[0]


def assert_canonical_settings(states):
    for row in states:
        value = row.get(FIELD)
        if not value:
            continue
        data = json.loads(value)
        ensure(isinstance(data, dict) and set(data) == {"version", "targets"} and type(data["version"]) is int and data["version"] == 1,
               "صيغة مستلمين غير معروفة؛ لا نعيد تنسيقها ضمن الاختبار")
        targets = data["targets"]
        ensure(isinstance(targets, list) and len(targets) <= 50, "قائمة مستلمين غير صالحة")
        seen, normalized = set(), []
        for target in targets:
            ensure(isinstance(target, dict) and target.get("type") in ("owner", "user", "role", "field"), "مستلم غير صالح")
            field = None if target["type"] == "owner" else target["type"]
            ensure(set(target) == ({"type", field} if field else {"type"}), "حقول مستلم غير قياسية")
            if field:
                ensure(isinstance(target[field], str) and target[field] and target[field] == target[field].strip(), "قيمة مستلم ستتغير عند الحفظ")
            key = json.dumps(target, sort_keys=True)
            ensure(key not in seen, "مستلم مكرر سيُحذف تلقائيًا عند الحفظ")
            seen.add(key)
            normalized.append({"type": target["type"], **({field: target[field]} if field else {})})
        canonical = json.dumps({"version": 1, "targets": normalized}, ensure_ascii=False, separators=(",", ":"))
        ensure(value == canonical, "إعداد مستلمين يحتاج إعادة تنسيق؛ لا نحفظه ضمن الاختبار")


def configuration(args):
    env = read_env(args.env_file)
    ensure(env.get("FRAPPE_TEST_SITE") and env.get("FRAPPE_TEST_TOKEN"), "بيانات TEST غير مكتملة")
    site = origin(env["FRAPPE_TEST_SITE"])
    denied = set(PROD_HOSTS)
    if env.get("FRAPPE_PROD_SITE"):
        denied.add(urlparse(origin(env["FRAPPE_PROD_SITE"])).hostname)
    ensure(urlparse(site).hostname not in denied, "هذا الاختبار ممنوع على الأساسي")
    ensure(args.confirm_site and origin(args.confirm_site) == site, "التأكيد لا يطابق TEST")
    ensure(5 <= args.timeout <= 120, "مهلة الطلب يجب أن تكون بين 5 و120 ثانية")
    ensure(all(env.get(key) for key in ("BROWSER_LOGIN_URL", "BROWSER_LOGIN_EMAIL", "BROWSER_LOGIN_PASSWORD")),
           "بيانات دخول مستخدم TEST العادي غير مكتملة")
    ensure(origin(env["BROWSER_LOGIN_URL"], allow_path=True) == site,
           "BROWSER_LOGIN_URL لا يطابق TEST؛ لم تُرسل بيانات الدخول")
    env["site"] = site
    return env


class ActualControllerRunner:
    def __init__(self, env, args, journal):
        self.env, self.args, self.journal = env, args, journal
        self.admin = TrackedClient(env["site"], "Administrator", journal, args.timeout, env["FRAPPE_TEST_TOKEN"])
        self.viewer = TrackedClient(env["site"], "B", journal, args.timeout)

    @property
    def workflow_name(self):
        name = self.journal.data.get("workflow_name")
        ensure(isinstance(name, str) and name, "لم يُثبت اسم Workflow المستهدف بالقراءة")
        return name

    def rows(self, doctype, fields, filters, length=2000):
        result, offset = [], 0
        while True:
            batch = self.admin.call("frappe.client.get_list", args={
                "doctype": doctype, "fields": json.dumps(fields), "filters": json.dumps(filters),
                "order_by": "name asc", "limit_start": offset, "limit_page_length": length,
            })
            ensure(isinstance(batch, list) and all(isinstance(row, dict) for row in batch), "قائمة القراءة غير صحيحة")
            result.extend(batch)
            if len(batch) < length:
                return result
            offset += len(batch)

    def count(self, doctype, filters, *, client=None):
        rows = (client or self.admin).call("frappe.client.get_list", args={
            "doctype": doctype, "fields": json.dumps(["count(name) as count"]),
            "filters": json.dumps(filters), "limit_page_length": 1,
        })
        ensure(isinstance(rows, list) and len(rows) == 1 and type(rows[0].get("count")) is int and rows[0]["count"] >= 0,
               "رد العد غير صالح؛ لا نفترض أن النتيجة صفر")
        return rows[0]["count"]

    def authenticate_viewer(self):
        ensure(origin(self.env["BROWSER_LOGIN_URL"], allow_path=True) == self.env["site"],
               "رابط الدخول لا يطابق TEST؛ لم تُرسل بيانات الدخول")
        email = self.env["BROWSER_LOGIN_EMAIL"]
        ensure(email not in ("Administrator", "Guest"), "قياسات الأداء تتطلب مستخدمًا عاديًا")
        self.viewer.login(email, self.env["BROWSER_LOGIN_PASSWORD"])
        identity = self.viewer.call("frappe.auth.get_logged_user")
        ensure(identity == email and identity not in ("Administrator", "Guest"), "هوية حساب القياس لا تطابق المستخدم العادي المطلوب")
        user = self.admin.doc("User", identity)
        ensure(user.get("enabled") == 1 and user.get("user_type") == "System User", "حساب القياس غير مؤهل")
        self.journal.data["actors"] = {"setup": "Administrator", "measurements": identity}
        self.journal.flush()

    def blank_guard(self):
        count = self.count(DOCTYPE, {self.journal.data["state_field"]: ["is", "not set"]})
        self.journal.event("blank_workflow_state_guard", count=count)
        ensure(count == 0, "توجد طلبات مواد بحالة فارغة؛ حفظ Workflow قد يغيرها، لذلك توقف الاختبار")

    def fingerprints(self):
        return {
            "material_requests": self.rows(DOCTYPE, ["name", "docstatus", self.journal.data["state_field"], "modified"], {}),
            "workflow_actions": self.rows("Workflow Action", ["name", "reference_name", "workflow_state", "status", "modified"],
                                          {"reference_doctype": DOCTYPE}),
        }

    def preflight(self):
        ensure(self.admin.call("frappe.auth.get_logged_user") == "Administrator", "يلزم توكن Administrator على TEST")
        self.authenticate_viewer()
        ensure(self.count("Workflow", {"name": ["like", "NAR Perf %"], "is_active": 1}) == 0,
               "اختبار الحجم ما زال نشطًا؛ لا نغير Workflow الأعمال أثناءه")
        ensure(self.count("Workflow Action", {"reference_doctype": ["like", "NAR Perf %"]}) == 0,
               "بقيت موافقات اختبار الحجم؛ أكمل تنظيفها أولًا")
        fields = self.rows("Custom Field", ["fieldname"], {"dt": "Workflow Document State", "fieldname": ["in", [FIELD, HIDE_FIELD]]})
        ensure({row["fieldname"] for row in fields} == {FIELD, HIDE_FIELD}, "حقلا التوجيه والإخفاء غير منشورين")
        patches = self.rows("Patch Log", ["name", "skipped"], {"patch": PATCH})
        ensure(len(patches) == 1 and not patches[0].get("skipped"), "patch الإخفاء لم يُطبق")
        workflows = self.rows("Workflow", ["name", "is_active"], {"document_type": DOCTYPE})
        self.journal.data["workflow_name"] = active_workflow_name(workflows)
        before = self.admin.doc("Workflow", self.workflow_name)
        ensure(before.get("is_active") == 1 and before.get("document_type") == DOCTYPE and before.get("modified"), "بصمة Workflow غير صالحة")
        states = [row for row in before.get("states", []) if row.get("state") == STATE]
        ensure(len(states) == 1 and states[0].get("name"), "لا يوجد صف مكتمل وحيد")
        # Workflow.validate normalizes routing JSON on every populated state.
        # Reject noncanonical values rather than silently rewrite unrelated rules.
        assert_canonical_settings(before.get("states", []))
        field = before.get("workflow_state_field")
        meta = self.admin.doc("DocType", DOCTYPE)
        names = {row["fieldname"] for row in meta.get("fields", [])}
        names.update(row["fieldname"] for row in self.rows("Custom Field", ["fieldname"], {"dt": DOCTYPE}))
        ensure(field and field in names, "حقل حالة طلب المواد غير موجود؛ حفظ Workflow قد ينشئه")
        self.journal.data.update({
            "row_name": states[0]["name"], "state_field": field,
            "before": before, "expected": before,
            "other_workflows": [self.admin.doc("Workflow", row["name"]) for row in workflows if row["name"] != self.workflow_name],
        })
        self.journal.flush()
        self.blank_guard()
        ensure(self.count(DOCTYPE, {field: STATE}) > 0 and self.count("Workflow Action", {
            "reference_doctype": DOCTYPE, "workflow_state": STATE, "status": "Open",
        }, client=self.viewer) > 0, "لا توجد طلبات مواد وموافقات مرحلة فعلية متاحة لحساب القياس")
        self.journal.data["viewer_core_before"] = self.count("Workflow Action", {"status": "Open"}, client=self.viewer)
        self.journal.data["fingerprints_before"] = self.fingerprints()
        self.journal.flush()
        self.journal.event("preflight_passed", workflow=self.workflow_name, state=STATE)

    def save_settings(self, values, phase):
        ensure(not self.journal.data.get("mutation_outcome_unknown") and not self.journal.data.get("read_request_may_be_running")
               and not self.journal.data.get("unsafe_drift"), "تعذر الحفظ أو الاستعادة الآلية؛ يلزم حسم طلب غير مؤكد أو تغيير متزامن")
        current = self.admin.doc("Workflow", self.workflow_name)
        expected = self.journal.data["expected"]
        ensure(current.get("modified") == expected.get("modified") and semantic(current) == semantic(expected),
               "تغير Workflow منذ آخر قراءة مؤكدة؛ لم نكتب فوق تعديل آخر")
        self.blank_guard()
        intended = deepcopy(current)
        target_row(intended, self.journal.data["row_name"]).update(values)
        if semantic(intended) == semantic(current):
            return
        self.journal.data["dirty"] = True
        self.journal.data["pending_intended"] = intended
        self.journal.flush()
        self.journal.event("mutation_before", phase=phase, before=current, intended=intended)

        def invoke():
            saved = self.admin.call("frappe.client.save", args={"doc": json.dumps(intended, ensure_ascii=False)}, post=True)
            ensure(isinstance(saved, dict) and saved.get("name") == self.workflow_name and saved.get("modified"), "رد الحفظ غير مؤكد")
            return saved

        try:
            saved = PerformanceRunner.mutate(self, self.admin, "save_material_request_workflow_" + phase, invoke)
        except BaseException:
            if self.admin.last_status == 200:
                self.journal.data["mutation_outcome_unknown"] = True
                self.journal.flush()
            raise
        self.journal.data["expected"] = saved
        self.journal.data.pop("pending_intended", None)
        self.journal.event("mutation_after", phase=phase, after=saved)
        if semantic(saved) != semantic(intended):
            self.journal.data["unsafe_drift"] = True
            self.journal.flush()
            raise SmokeFailure("الحفظ غيّر حقولًا خارج النطاق؛ أوقفت الكتابات للمراجعة")
        self.journal.flush()

    def approval_read(self, endpoint, args):
        try:
            return self.viewer.call(API + "." + endpoint, args=args)
        except BaseException:
            if self.viewer.last_status is None or self.viewer.last_status in UNCERTAIN_HTTP:
                self.journal.data["read_request_may_be_running"] = True
                self.journal.flush()
            raise

    def measure(self, phase):
        phase_count = None
        core_count = self.count("Workflow Action", {"status": "Open"}, client=self.viewer)
        ensure(core_count == self.journal.data["viewer_core_before"], "تغير العدد القياسي لحساب القياس قبل المرحلة")
        for sample in range(1, SAMPLES + 1):
            row = {"phase": phase, "sample": sample, "actor": self.journal.data["actors"]["measurements"], "core_count": core_count}
            for endpoint, args in (
                ("get_approvals", {"page_length": 25, "limit_start": 0, "search": "", "search_scope": "all"}),
                ("get_my_followups_counts", {}),
            ):
                start = time.monotonic()
                result = self.approval_read(endpoint, args)
                row[endpoint] = round(time.monotonic() - start, 4)
                count = (result.get("counts") or {}).get("open" if endpoint == "get_approvals" else "approvals") if isinstance(result, dict) else None
                ensure(type(count) is int and count >= 0, "رد العدادات غير صحيح")
                ensure(count <= core_count, "عداد متابعاتي تجاوز الموافقات القياسية المتاحة للمستخدم نفسه")
                row[endpoint + "_count"] = count
                if endpoint == "get_approvals":
                    ensure(isinstance(result.get("items"), list) and len(result["items"]) == min(25, count), "صفحة الموافقات غير صحيحة أو ناقصة")
                    if phase == "hidden":
                        ensure(not any(item.get("reference_doctype") == DOCTYPE and item.get("workflow_state") == STATE for item in result["items"]),
                               "ظهرت المرحلة المخفية في القائمة")
            ensure(row["get_approvals_count"] == row["get_my_followups_counts_count"], "عداد الصفحة لا يطابق العداد الموحد")
            if phase_count is None:
                phase_count = row["get_approvals_count"]
            ensure(row["get_approvals_count"] == phase_count, "تغير العدد خلال قياسات المرحلة؛ يلزم مراجعة حركة العمل المتزامنة")
            self.journal.data["measurements"].append(row)
            self.journal.flush()
            print(json.dumps(row, ensure_ascii=False), flush=True)
        return phase_count

    def capture_approval_snapshot(self, phase, expected_count):
        """Untimed, ordinary-user GET proof that hiding preserves every other ID."""
        ensure(type(expected_count) is int and expected_count >= 0, "لا يوجد عدد مؤكد لالتقاط الموافقات")
        seen, target_names, other_names = set(), set(), set()
        start = 0
        while True:
            result = self.approval_read("get_approvals", {
                "page_length": 100, "limit_start": start, "search": "", "search_scope": "all",
            })
            ensure(isinstance(result, dict) and isinstance(result.get("items"), list), "صفحة إثبات الموافقات غير صحيحة")
            count = (result.get("counts") or {}).get("open")
            ensure(type(count) is int and count == expected_count, "تغير عدد الموافقات أثناء إثبات الحالات الأخرى")
            items = result["items"]
            ensure(len(items) == min(100, expected_count - start), "صفحة إثبات الموافقات ناقصة أو زائدة")
            for row in items:
                ensure(isinstance(row, dict) and all(isinstance(row.get(field), str) and row[field]
                       for field in ("name", "reference_doctype", "workflow_state")), "موافقة بلا هوية أو حالة مؤكدة")
                name = row["name"]
                ensure(name not in seen, "تكررت موافقة بين الصفحات؛ لا نعتبر اللقطة مكتملة")
                seen.add(name)
                target = row["reference_doctype"] == DOCTYPE and row["workflow_state"] == STATE
                (target_names if target else other_names).add(name)
            has_more = start + len(items) < expected_count
            ensure(type(result.get("has_more")) is bool and result["has_more"] == has_more,
                   "مؤشر اكتمال صفحات الموافقات لا يطابق العدد")
            if not has_more:
                ensure(result.get("next_start") is None, "مؤشر نهاية الصفحات غير صحيح")
                break
            next_start = result.get("next_start")
            ensure(type(next_start) is int and next_start == start + len(items) and next_start > start,
                   "مؤشر صفحات الموافقات لا يتقدم بالعدد المتوقع")
            start = next_start
        ensure(len(seen) == expected_count, "مجموعة الموافقات لا تطابق العدد المؤكد")
        snapshot = {"count": expected_count, "target_names": sorted(target_names), "other_names": sorted(other_names)}
        self.journal.data.setdefault("approval_snapshots", {})[phase] = snapshot
        self.journal.flush()
        self.journal.event("approval_snapshot_verified", phase=phase, count=expected_count,
                           target_count=len(target_names), other_count=len(other_names))
        return snapshot

    def verify_hide_preserves_other_states(self, before, hidden):
        ensure(before["target_names"] and before["other_names"],
               "إثبات الإخفاء يتطلب موافقات مكتمل وموافقات أخرى مرئية قبل الإخفاء؛ لا تكفي نتيجة صفر")
        ensure(not hidden["target_names"], "بقيت موافقات مكتمل بعد الإخفاء")
        ensure(hidden["other_names"] == before["other_names"], "الإخفاء غيّر موافقات حالات أخرى؛ لم يجتز القبول")
        ensure(before["count"] - hidden["count"] == len(before["target_names"]),
               "انخفاض العداد لا يساوي موافقات مكتمل المخفية فقط")
        self.journal.data["other_state_ids_preserved"] = True
        self.journal.data["hidden_count_delta_verified"] = True
        self.journal.flush()
        self.journal.event("hide_scope_verified", hidden_count=len(before["target_names"]),
                           other_count=len(hidden["other_names"]))

    def verify_hidden_search(self):
        start = 0
        while True:
            result = self.approval_read("get_approvals", {"page_length": 100, "limit_start": start, "search_scope": "state", "search": STATE})
            ensure(isinstance(result, dict) and isinstance(result.get("items"), list), "نتيجة بحث المرحلة غير صحيحة")
            ensure(not any(row.get("reference_doctype") == DOCTYPE and row.get("workflow_state") == STATE for row in result["items"]),
                   "ظهرت المرحلة المخفية في نتائج البحث")
            ensure(type(result.get("has_more")) is bool, "تعذر إثبات اكتمال صفحات البحث")
            if not result["has_more"]:
                break
            next_start = result.get("next_start")
            ensure(type(next_start) is int and next_start > start, "مؤشر بحث المرحلة لا يتقدم")
            start = next_start
        self.journal.event("hidden_search_verified", last_offset=start)

    def restore(self):
        if not self.journal.data.get("before"):
            return
        before = self.journal.data["before"]
        row = target_row(before, self.journal.data["row_name"])
        self.save_settings({FIELD: row.get(FIELD), HIDE_FIELD: row.get(HIDE_FIELD)}, "restore")
        restored = self.admin.doc("Workflow", self.workflow_name)
        ensure(semantic(restored) == semantic(before), "لم يعد Workflow مطابقًا لكل حقول الأعمال الأصلية")
        for other in self.journal.data["other_workflows"]:
            ensure(semantic(self.admin.doc("Workflow", other["name"])) == semantic(other), "تغير Workflow آخر لطلبات المواد")
        after = self.fingerprints()
        self.journal.data["fingerprints_after"] = after
        self.journal.data["workflow_restored"] = True
        self.journal.data["dirty"] = False
        self.journal.data["business_fingerprints_unchanged"] = after == self.journal.data["fingerprints_before"]
        self.journal.flush()
        ensure(self.journal.data["business_fingerprints_unchanged"], "تغيرت بصمة طلبات المواد أو موافقاتها أثناء القياس؛ لم تُعدل لاستعادتها")
        core_after = self.count("Workflow Action", {"status": "Open"}, client=self.viewer)
        self.journal.data["viewer_core_after"] = core_after
        self.journal.flush()
        ensure(core_after == self.journal.data["viewer_core_before"], "تغير العدد القياسي للمستخدم العادي بعد استعادة Workflow")

    def run(self):
        try:
            self.preflight()
            self.save_settings({FIELD: OWNER, HIDE_FIELD: 0}, "owner_unhidden")
            owner_count = self.measure("owner_unhidden")
            owner_snapshot = self.capture_approval_snapshot("owner_unhidden", owner_count)
            self.save_settings({FIELD: OWNER, HIDE_FIELD: 1}, "hidden")
            hidden_count = self.measure("hidden")
            hidden_snapshot = self.capture_approval_snapshot("hidden", hidden_count)
            self.verify_hide_preserves_other_states(owner_snapshot, hidden_snapshot)
            self.verify_hidden_search()
        finally:
            if self.journal.data.get("fingerprints_before"):
                self.restore()
        rows = self.journal.data["measurements"]
        passed = all(samples_pass([row[method] for row in rows if row["phase"] == phase])
                     for phase in ("owner_unhidden", "hidden")
                     for method in ("get_approvals", "get_my_followups_counts"))
        self.journal.data["performance_passed"] = passed
        self.journal.flush()
        ensure(passed, "تجاوز قياس واحد أو أكثر حد 3 ثوانٍ؛ تمت استعادة Workflow")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--confirm-site", default="")
    parser.add_argument("--env-file", type=Path, default=default_env_file())
    parser.add_argument("--state-dir", type=Path, default=Path.home() / ".local/state/namar_test/material_request_approval_performance")
    parser.add_argument("--timeout", type=int, default=90)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if not args.run:
        print(json.dumps({"network": False, "writes": False, "document_type": DOCTYPE,
                          "workflow": "الوحيد النشط، يُحدد بالقراءة دون افتراض الاسم", "state": STATE,
                          "temporary_fields": [FIELD, HIDE_FIELD], "phases": ["owner_unhidden", "hidden", "restore"],
                          "setup_actor": "Administrator", "measurement_actor": "existing TEST browser login",
                          "samples_per_endpoint_per_phase": SAMPLES, "page_length": 25, "max_seconds": MAX_SECONDS}, ensure_ascii=False))
        return 0
    env = configuration(args)
    directory = private_dir(args.state_dir)
    path = directory / ("material-request-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8] + ".json")
    journal = PerfJournal(path, {"site": env["site"], "kind": "actual_material_request_approval_performance", "measurements": []})
    journal.flush()
    runner = ActualControllerRunner(env, args, journal)
    print(json.dumps({"manifest": str(path)}, ensure_ascii=False), flush=True)
    try:
        runner.run()
    except BaseException as exc:
        journal.data["failure"] = str(exc) if isinstance(exc, SmokeFailure) else type(exc).__name__
        journal.flush()
        print(json.dumps({"failure": journal.data["failure"], "manifest": str(path),
                          "workflow_restored": journal.data.get("workflow_restored", False)}, ensure_ascii=False), flush=True)
        return 1
    finally:
        runner.admin.session.close()
        runner.viewer.session.close()
    print(json.dumps({"performance_passed": True, "workflow_restored": True, "manifest": str(path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
