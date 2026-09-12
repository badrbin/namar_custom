#!/usr/bin/env python3
"""اختبار حذف مراجع متابعاتي على testnamar فقط؛ لا اتصال دون --run.

التشغيل الحي الصريح يحتاج --run --confirm-site=testnamar.u.frappe.cloud
و--state-dir لمسار جديد خاص. ينشئ ثلاثة مصادر ToDo مستقلة للحالات المفتوحة
والمغلقة والمحوّلة، وشاهدًا رابعًا يبقى إلى نهاية الفحص. يذكر مستخدم التوكن
نفسه فقط. لا يُنشئ مستند أعمال أو Server Script، ولا يغير حالة قراءة أخرى.

كل كتابة تسبقها نية محفوظة في manifest خاص، وكل حذف REST يسبقه تحقق بصمة
المصدر واسمه ومالكه. بعد الحذف مباشرةً يقرأ DB بمعاملة READ ONLY وTLS متحقق؛
لا ينتظر أي job لتنظيف البقايا. عند أي فشل يتوقف ويحفظ manifest للتنظيف
المحدد لاحقًا؛ لا يعيد الكتابة أو يحاول إصلاح نتائج الفشل تلقائيًا.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import ssl
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from smoke_test_mention_inbox import (
    API_BASE,
    DirectDBProbeFailure,
    DirectTestDBConfig,
    FrappeClient,
    HttpFailure,
    SmokeFailure,
    default_env_file,
    detail_mention,
    digest,
    ensure,
    load_env,
    normalize_text,
    response_items,
    validate_direct_test_db_config,
    validate_run_config,
)

TEST_HOST = "testnamar.u.frappe.cloud"
BUCKETS = ("open", "unread", "converted", "closed")
SOURCES = ("open", "closed", "converted", "control")
POLL_ATTEMPTS = 30
FOLLOWUP_COUNT_KEYS = ("threads", "events", "linked_todos", "notifications", "workflow_actions")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReferenceReadProbe:
    """Only fixed SELECT statements; one verified connection per observation."""

    def __init__(self, config: DirectTestDBConfig):
        self.config = config

    def snapshot(self, name: str, thread_names: list[str]) -> dict[str, Any]:
        ensure(bool(name), "قراءة DB تتطلب اسم مصدر محددًا")
        ensure(len(thread_names) <= 4, "عدد Threads غير متوقع")
        try:
            pymysql = __import__("pymysql")
        except ImportError:
            raise DirectDBProbeFailure("dependency_missing", "مكتبة PyMySQL غير متاحة") from None
        connection = None
        failed = False
        result: dict[str, Any] = {}
        try:
            tls = ssl.create_default_context()
            tls.minimum_version = ssl.TLSVersion.TLSv1_2
            ensure(tls.check_hostname and tls.verify_mode == ssl.CERT_REQUIRED,
                   "تعذر تفعيل تحقق TLS")
            connection = pymysql.connect(
                host=self.config.host, port=self.config.port,
                user=self.config.user, password=self.config.password,
                database=self.config.database, charset="utf8mb4", autocommit=False,
                connect_timeout=self.config.timeout, read_timeout=self.config.timeout,
                write_timeout=self.config.timeout, ssl=tls,
                cursorclass=pymysql.cursors.DictCursor,
            )
            with connection.cursor() as cursor:
                cursor.execute("START TRANSACTION READ ONLY")
                cursor.execute(
                    "SELECT name, description, allocated_to, assigned_by, reference_type, "
                    "reference_name, status FROM `tabToDo` WHERE name=%s LIMIT 1", (name,),
                )
                result["source"] = cursor.fetchone()
                cursor.execute(
                    "SELECT name, for_user, reference_doctype, reference_name, status, "
                    "last_event_key, last_seen_event_key, converted_to_todo "
                    "FROM `tabNamar Mention Thread` WHERE reference_doctype=%s "
                    "AND reference_name=%s ORDER BY name", ("ToDo", name),
                )
                result["threads"] = list(cursor.fetchall())
                names = sorted(set(thread_names + [row["name"] for row in result["threads"]]))
                # Names are bound values, never interpolated SQL identifiers.
                placeholders = ",".join(["%s"] * len(names)) or "%s"
                cursor.execute(
                    "SELECT COUNT(*) AS n FROM `tabNamar Mention Event` "
                    f"WHERE thread IN ({placeholders})", tuple(names or [""]),
                )
                counts = {"threads": len(result["threads"]), "events": int(cursor.fetchone()["n"])}
                for key, statement in (
                    ("linked_todos", "SELECT COUNT(*) AS n FROM `tabToDo` WHERE reference_type=%s AND reference_name=%s"),
                    ("notifications", "SELECT COUNT(*) AS n FROM `tabNotification Log` WHERE document_type=%s AND document_name=%s"),
                    ("workflow_actions", "SELECT COUNT(*) AS n FROM `tabWorkflow Action` WHERE reference_doctype=%s AND reference_name=%s"),
                    ("comments", "SELECT COUNT(*) AS n FROM `tabComment` WHERE reference_doctype=%s AND reference_name=%s"),
                ):
                    cursor.execute(statement, ("ToDo", name))
                    counts[key] = int(cursor.fetchone()["n"])
                result["counts"] = counts
                result["observed_at"] = utc_now()
        except Exception:
            failed = True
        finally:
            if connection is not None:
                try:
                    connection.rollback()
                except Exception:
                    failed = True
                try:
                    connection.close()
                except Exception:
                    failed = True
        if failed:
            raise DirectDBProbeFailure(
                "reference_read_failed", "فشل مجس DB التجريبية؛ حُجبت الأسرار وتفاصيل الاتصال الخام",
            ) from None
        return result


class CleanupSmoke:
    def __init__(self, client: FrappeClient, probe: ReferenceReadProbe, state_dir: Path, user: str):
        self.client, self.probe, self.user = client, probe, user
        # Creating an existing directory is always rejected; no implicit reruns.
        state_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
        os.chmod(state_dir, 0o700)
        self.path = state_dir / "manifest.json"
        self.marker = f"[RFCSMK:{uuid4().hex}]"
        self.manifest: dict[str, Any] = {
            "schema_version": 1, "host": TEST_HOST, "mode": "test-only",
            "marker": self.marker, "created_at": utc_now(), "user_sha256": digest(user.lower()),
            "status": "preflight", "sources": {}, "operations": [], "checks": [],
            "http_transcript": [], "db_observations": [], "credentials_persisted": False,
            "comment_cleanup": "ينفذ Frappe حذف التعليقات القياسي asynchronously؛ أعدادها للرصد فقط",
        }
        self.save()
        self.client.set_request_observer(self.record_http)

    def save(self) -> None:
        self.manifest["updated_at"] = utc_now()
        temporary = self.path.with_suffix(".tmp")
        # O_EXCL and mode=600 avoid briefly creating world-readable state.
        with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as stream:
            json.dump(self.manifest, stream, ensure_ascii=False, indent=2, default=str)
            stream.write("\n")
        temporary.replace(self.path)

    def record_http(self, entry: dict[str, Any]) -> None:
        self.manifest["http_transcript"].append(entry)
        self.save()

    def check(self, message: str) -> None:
        self.manifest["checks"].append({"at": utc_now(), "result": "نجح", "message": message})
        self.save()
        print(f"نجح: {message}", flush=True)

    def begin(self, action: str, role: str, **identity: Any) -> dict[str, Any]:
        operation = {"at": utc_now(), "action": action, "role": role,
                     "status": "pending", "marker": self.marker, **identity}
        self.manifest["operations"].append(operation)
        self.save()
        return operation

    def finish(self, operation: dict[str, Any], **values: Any) -> None:
        operation.update(status="completed", finished_at=utc_now(), **values)
        self.save()

    @staticmethod
    def no_messages(payload: dict[str, Any]) -> None:
        ensure(not payload.get("_server_messages"), "أعاد الخادم _server_messages أثناء الاختبار")

    def call(self, method: str, args: dict[str, Any], *, write: bool = False) -> Any:
        path = f"{API_BASE}.{method}"
        payload = self.client.request("POST" if write else "GET", path,
                                      payload=args if write else None,
                                      params=None if write else args)
        self.no_messages(payload)
        return payload.get("message")

    def mentions(self, bucket: str) -> dict[str, Any]:
        return self.call("get_mentions", {"bucket": bucket, "search": self.marker,
                                         "search_scope": "all", "page_length": 100, "limit_start": 0})

    def create(self, role: str, doctype: str, payload: dict[str, Any]) -> dict[str, Any]:
        operation = self.begin("create", role, doctype=doctype,
                               reference_name=payload.get("reference_name"))
        result = self.client.request("POST", f"/api/resource/{quote(doctype, safe='')}",
                                     payload=payload, expected=(200, 201))
        doc = result.get("data") or {}
        ensure(isinstance(doc, dict) and doc.get("name"), "استجابة الإنشاء بلا اسم؛ راجع العملية المعلّقة في manifest")
        # Record returned identity before any subsequent read or validation.
        self.finish(operation, name=doc["name"])
        self.manifest["sources"][role]["source_name" if doctype == "ToDo" else "comment_name"] = doc["name"]
        self.save()
        self.no_messages(result)
        return doc

    def source_doc(self, role: str) -> dict[str, Any]:
        source = self.manifest["sources"][role]
        name = source["source_name"]
        doc = self.client.get_doc("ToDo", name)
        ensure(doc is not None, "المصدر المسجل اختفى قبل الحذف؛ توقف دون إعادة الحذف")
        ensure(doc.get("name") == name and doc.get("description") == source["description"],
               "رفض الحذف: اسم المصدر أو بصمته لا يطابق manifest")
        ensure(doc.get("allocated_to") == self.user and doc.get("assigned_by") == self.user,
               "رفض الحذف: المصدر لا يخص مستخدم التوكن")
        ensure(not doc.get("reference_type") and not doc.get("reference_name"),
               "رفض الحذف: المصدر مرتبط بمستند أعمال أو مصدر آخر")
        return doc

    def sample(self, role: str, label: str) -> dict[str, Any]:
        source = self.manifest["sources"][role]
        snapshot = self.probe.snapshot(source["source_name"], [source["thread_name"]] if source.get("thread_name") else [])
        self.manifest["db_observations"].append({"role": role, "label": label, **snapshot})
        self.save()
        return snapshot

    def seed(self, role: str) -> None:
        description = f"{self.marker} SOURCE-{role.upper()}"
        self.manifest["sources"][role] = {"description": description, "role": role}
        self.save()
        todo = self.create(role, "ToDo", {
            "description": description, "status": "Open", "priority": "Low",
            "date": date.today().isoformat(), "allocated_to": self.user, "assigned_by": self.user,
        })
        self.source_doc(role)
        initial = self.sample(role, "source_created")
        ensure(initial["source"] and initial["source"]["description"] == description
               and initial["source"]["allocated_to"] == self.user,
               "تعارض هوية الموقع وDB: المصدر نفسه غير موجود في قاعدة التجريبي")
        ensure(not initial["threads"], "مصدر جديد يحتوي Threads غير متوقعة")
        escaped_user = html.escape(self.user, quote=True)
        self.create(role, "Comment", {
            "comment_type": "Comment", "reference_doctype": "ToDo", "reference_name": todo["name"],
            "comment_email": self.user,
            "content": f'<p>{html.escape(description)} SELF-MENTION <span class="mention" '
                       f'data-id="{escaped_user}" data-value="{escaped_user}">@{escaped_user}</span></p>',
        })
        for attempt in range(POLL_ATTEMPTS):
            matches = [row for row in response_items(self.mentions("open"))
                       if row.get("reference_doctype") == "ToDo" and row.get("reference_name") == todo["name"]]
            if matches:
                ensure(len(matches) == 1, "أكثر من Thread لمرجع الاختبار ومستخدم التوكن")
                thread = detail_mention(self.call("get_mention_detail", {"thread_name": matches[0]["name"]}))
                ensure(thread.get("for_user") == self.user and self.marker in thread.get("latest_preview_plain", "")
                       and thread.get("reference_doctype") == "ToDo" and thread.get("reference_name") == todo["name"],
                       "Thread لا تطابق بصمة المصدر ومستخدم التوكن")
                self.manifest["sources"][role].update(thread_name=thread["name"], event_key=thread["last_event_key"])
                self.save()
                break
            if attempt < POLL_ATTEMPTS - 1:
                time.sleep(1)
        else:
            raise SmokeFailure("انتهت مهلة التقاط self-mention؛ fixtures باقية في manifest")
        if role in {"closed", "converted"}:
            self.source_doc(role)
            args = {"thread_name": thread["name"], "expected_last_event_key": thread["last_event_key"]}
            method = "close_mention" if role == "closed" else "convert_mention_to_followup"
            if role == "converted":
                args.update(due_date=(date.today() + timedelta(days=1)).isoformat(), priority="Low",
                            description=f"{self.marker} LINKED-FOLLOWUP")
            operation = self.begin(method, role, thread_name=thread["name"])
            result = self.call(method, args, write=True)
            if role == "converted":
                linked_name = (result.get("followup") or {}).get("name")
                self.manifest["sources"][role]["linked_todo"] = linked_name
                self.save()
                linked = self.client.get_doc("ToDo", linked_name) if linked_name else None
                ensure(linked and linked.get("reference_type") == "ToDo"
                       and linked.get("reference_name") == todo["name"] and linked.get("allocated_to") == self.user
                       and self.marker in linked.get("description", ""), "المتابعة المحوّلة لا تطابق بصمة المصدر")
            self.finish(operation)
        expected = {"open": "Open", "closed": "Closed", "converted": "Converted", "control": "Open"}[role]
        snapshot = self.sample(role, "ready")
        ensure(len(snapshot["threads"]) == 1 and snapshot["threads"][0]["status"] == expected,
               f"حالة fixture {role} غير صحيحة")
        ensure(snapshot["counts"]["events"] >= 1, "Fixture بلا Events؛ اختبار الحذف غير مكتمل")
        if role == "converted":
            ensure(snapshot["counts"]["linked_todos"] == 1, "Fixture المحوّلة بلا ToDo تابع واحد")
        self.check(f"تجهيز مصدر مستقل {role} وself-mention صالح")

    def delete_source(self, role: str) -> None:
        self.source_doc(role)
        source = self.manifest["sources"][role]
        operation = self.begin("delete_source_rest", role, doctype="ToDo", name=source["source_name"])
        self.client.delete_doc("ToDo", source["source_name"])
        # No polling, sleep, or product API call between DELETE and this DB read.
        snapshot = self.sample(role, "immediately_after_delete")
        self.finish(operation)
        remaining = {key: snapshot["counts"][key] for key in FOLLOWUP_COUNT_KEYS}
        ensure(snapshot["source"] is None and not any(remaining.values()),
               f"بقايا متابعاتي بعد حذف مصدر {role}: {remaining}")
        source["deleted_and_verified"] = True
        self.save()
        for bucket in BUCKETS:
            rows = response_items(self.mentions(bucket))
            ensure(not any(row.get("reference_doctype") == "ToDo"
                           and row.get("reference_name") == source["source_name"] for row in rows),
                   f"المصدر المحذوف ما زال في bucket {bucket}")
        self.check(f"حذف {role}: صفر مشتقات متابعاتي فورًا وجميع قوائم الوارد بلا رسائل خطأ")

    @staticmethod
    def stable_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in snapshot.items() if key != "observed_at"}

    def run(self) -> None:
        # Mandatory connectivity/schema preflight before creating any fixture.
        self.probe.snapshot(self.marker, [])
        ensure(self.user and self.user != "Guest", "يتطلب توكن مستخدم تجريبي مسجّل")
        actor = self.client.get_doc("User", self.user) or {}
        ensure(actor.get("enabled") and actor.get("user_type") == "System User"
               and actor.get("allowed_in_mentions"),
               "حساب الاختبار الحالي غير مفعّل لاستقبال المنشن")
        for doctype, permission in (("ToDo", "read"), ("ToDo", "create"), ("ToDo", "delete"), ("Comment", "create")):
            ensure(self.client.has_permission(doctype, permission), f"صلاحية التجريبي ناقصة: {doctype}:{permission}")
        self.manifest["status"] = "running"
        self.save()
        # Seed the control first, so it witnesses every subsequent mutation.
        for role in ("control", "open", "closed", "converted"):
            self.seed(role)
        control = self.stable_snapshot(self.sample("control", "control_before_deletions"))
        for role in SOURCES[:-1]:
            self.delete_source(role)
            current = self.stable_snapshot(self.sample("control", f"control_after_{role}"))
            ensure(current == control, "تغيّر الشاهد أو حالة قراءته عند حذف مصدر مستقل")
            self.check(f"بقاء الشاهد وقراءته بلا تغير بعد حذف {role}")
        # The surviving source is deleted only here, using its recorded fingerprint.
        self.delete_source("control")
        # One bounded delayed read catches fixtures reintroduced by already queued jobs.
        time.sleep(2)
        for role in SOURCES:
            after = self.sample(role, "final_cleanup_verification")
            ensure(after["source"] is None
                   and not any(after["counts"][key] for key in FOLLOWUP_COUNT_KEYS),
                   "عادت بقايا بعد نجاح الحذف الفوري؛ راجع manifest")
        self.manifest["remaining_comments_observed"] = {
            item["role"]: item["counts"]["comments"]
            for item in self.manifest["db_observations"]
            if item["label"] == "final_cleanup_verification"
        }
        self.manifest["status"] = "passed_and_cleaned"
        self.check("نجح الاختبار ونُظفت مصادره الأربعة وحدها؛ لا موظف آخر ولا مستند أعمال")


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--confirm-site", default="")
    parser.add_argument("--env-file", type=Path, default=default_env_file())
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--timeout", type=int, default=30)
    # Explicit fields consumed by the shared test-only guard; no site override.
    parser.set_defaults(test_site="", expected_user="", include_self_reply=False)
    return parser.parse_args()


def main() -> int:
    args = arguments()
    if not args.run:
        print(json.dumps({
            "الوضع": "dry-run؛ لم يُقرأ ملف البيئة ولم يحدث اتصال أو كتابة",
            "الموقع الوحيد": TEST_HOST,
            "التشغيل الصريح": "--run --confirm-site=testnamar.u.frappe.cloud --state-dir <مسار جديد>",
            "المصادر": "3 ToDo مستقلة: Open/Closed/Converted + ToDo شاهد",
            "المستلم": "مستخدم FRAPPE_TEST_TOKEN نفسه فقط",
            "التحقق": "DB عبر FRAPPE_TEST_DB_* فقط، TLS متحقق وSTART TRANSACTION READ ONLY",
            "بعد الحذف": "صفر Thread/Event/ToDo تابع/Notification Log/Workflow Action فورًا",
            "التعليقات": "رصد read-only فقط؛ حذف Frappe القياسي asynchronous خارج شرط الصفر الفوري",
            "القوائم": list(BUCKETS), "mark_seen": False, "Server Script مؤقت": False,
            "عند الفشل": "توقف بلا إعادة تشغيل أو تنظيف تلقائي؛ manifest خاص باقٍ",
        }, ensure_ascii=False, indent=2))
        return 0
    runner = None
    try:
        ensure(args.state_dir is not None, "التشغيل الحي يتطلب --state-dir لمسار جديد")
        state_dir = args.state_dir.expanduser().resolve()
        ensure(not state_dir.exists(), "رفض إعادة التشغيل: --state-dir موجود؛ لا يستخدم الاختبار حالة قديمة")
        load_env(args.env_file.expanduser().resolve())
        config = validate_run_config(args)
        ensure(config.host == TEST_HOST and config.base_url == f"https://{TEST_HOST}",
               "رفض التشغيل: هذا الاختبار محصور في testnamar.u.frappe.cloud بلا منفذ بديل")
        probe = ReferenceReadProbe(validate_direct_test_db_config(config.timeout))
        client = FrappeClient(config)
        user = client.get_logged_user()
        runner = CleanupSmoke(client, probe, state_dir, user)
        runner.run()
        print(json.dumps({"النتيجة": "نجح ونُظف", "manifest": str(runner.path)}, ensure_ascii=False))
        return 0
    except BaseException as exc:
        # No raw HTTP/driver response or traceback may reveal credentials/private payloads.
        message = str(exc) if isinstance(exc, SmokeFailure) and not isinstance(exc, HttpFailure) else "توقف الاختبار؛ حُجبت تفاصيل الخطأ الخام"
        if runner is not None:
            runner.manifest["status"] = "failed_fixtures_retained"
            runner.manifest["failure"] = {"at": utc_now(), "category": type(exc).__name__, "message": message}
            try:
                runner.save()
            except Exception:
                pass
        print(json.dumps({"النتيجة": "فشل وتوقف دون إعادة كتابة",
                          "السبب": message, "نوع الخطأ": type(exc).__name__,
                          "manifest": str(runner.path) if runner else None,
                          "الإجراء": "احتفظ بالـmanifest؛ أي تنظيف لاحق يجب أن يطابق الأسماء والبصمات المسجلة"}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
