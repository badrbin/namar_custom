from __future__ import annotations

import importlib.util
import sys
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


SERVICE_PATH = (
    Path(__file__).resolve().parents[1]
    / "namar_custom"
    / "mentions"
    / "service.py"
)


class FakeDatabase:
    def __init__(self, users: dict[str, dict] | None = None):
        self.users = users or {}
        self.savepoints: list[str] = []
        self.rollbacks: list[str] = []
        self.releases: list[str] = []

    def get_value(self, doctype, filters, fieldname, **kwargs):
        if doctype != "User" or not isinstance(filters, dict):
            return None
        name = filters.get("name")
        user = self.users.get(name)
        if not user:
            return None
        for key, expected in filters.items():
            if key == "name":
                continue
            if user.get(key) != expected:
                return None
        return name if fieldname == "name" else user.get(fieldname)

    def exists(self, doctype, name):
        return doctype == "Comment" and bool(name)

    def savepoint(self, name):
        self.savepoints.append(name)

    def rollback(self, *, save_point):
        self.rollbacks.append(save_point)

    def release_savepoint(self, name):
        self.releases.append(name)


def load_service(
    *,
    user: str = "me@example.com",
    users: dict[str, dict] | None = None,
    user_rows: list[SimpleNamespace] | None = None,
):
    fake_frappe = ModuleType("frappe")
    fake_frappe.session = SimpleNamespace(user=user)
    fake_frappe.db = FakeDatabase(users)
    fake_frappe.ValidationError = type("ValidationError", (Exception,), {})
    fake_frappe.PermissionError = type("PermissionError", (Exception,), {})
    fake_frappe.DoesNotExistError = type("DoesNotExistError", (Exception,), {})
    fake_frappe._get_all_calls = []

    def throw(message, exc_type=Exception):
        raise exc_type(message)

    def get_all(doctype, **kwargs):
        fake_frappe._get_all_calls.append((doctype, kwargs))
        return list(user_rows or [])

    fake_frappe.throw = throw
    fake_frappe.get_all = get_all
    fake_frappe.get_cached_value = lambda doctype, name, field: (
        (users or {}).get(name, {}).get(field)
    )
    fake_frappe.has_permission = lambda *args, **kwargs: True
    fake_frappe.get_doc = lambda *args, **kwargs: None

    fake_utils = ModuleType("frappe.utils")
    fake_utils.get_absolute_url = lambda doctype, name: f"/app/{doctype}/{name}"
    fake_utils.now_datetime = lambda: "2026-08-18 12:00:00"
    # The renderer under test must still discard scripts, attributes, and
    # unsupported markup even if sanitization returns the input unchanged.
    fake_utils.sanitize_html = lambda value, **kwargs: value
    fake_frappe.utils = fake_utils

    fake_followup_service = ModuleType("namar_custom.followups.service")
    fake_followup_service._readable_reference_title = lambda *args: None
    fake_followup_service._reference_summary = lambda doc: {}
    fake_followup_service._serialize_comment = lambda doc: {}
    fake_followup_service._serialize_todo = lambda doc: {}

    module_name = f"_namar_reply_mentions_test_{id(fake_frappe)}"
    spec = importlib.util.spec_from_file_location(module_name, SERVICE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("تعذر تحميل خدمة وارد الإشارات")
    module = importlib.util.module_from_spec(spec)
    runtime_modules = {
        "frappe": fake_frappe,
        "frappe.utils": fake_utils,
        "namar_custom.followups.service": fake_followup_service,
        module_name: module,
    }
    with patch.dict(sys.modules, runtime_modules):
        spec.loader.exec_module(module)
    module._test_runtime_modules = runtime_modules
    return module, fake_frappe


def call_service(module, function_name: str, *args, **kwargs):
    """Call a loaded service while its lazy Frappe imports are stubbed."""

    with patch.dict(sys.modules, module._test_runtime_modules):
        return getattr(module, function_name)(*args, **kwargs)


def enabled_user(full_name: str) -> dict[str, object]:
    return {
        "enabled": 1,
        "user_type": "System User",
        "allowed_in_mentions": 1,
        "full_name": full_name,
    }


def mention(user: str, label: str | None = None, *, group: bool = False) -> str:
    return (
        f'<span class="mention forged" data-id="{user}" '
        f'data-is-group="{str(group).lower()}" onclick="steal()">'
        f'@{label or user}</span>'
    )


class MentionReplySearchTestCase(unittest.TestCase):
    def test_search_is_bounded_and_never_returns_groups(self):
        rows = [
            SimpleNamespace(name=f"user{index:02d}@example.com", full_name=f"موظف {index:02d}")
            for index in range(20)
        ]
        module, fake_frappe = load_service(user_rows=rows)
        thread = SimpleNamespace(latest_from_user="user05@example.com")
        module._get_owned_thread = lambda thread_name: thread
        module._get_reference_doc = lambda owned_thread: SimpleNamespace(name="DOC-1")
        module._eligible_mention_user = lambda owned_thread, candidate: candidate

        result = module.search_reply_mentions("THREAD-1", "")

        self.assertEqual(len(result), module.MAX_REPLY_MENTION_CANDIDATES)
        self.assertEqual(result[0]["id"], "user05@example.com")
        self.assertTrue(all(row["is_group"] is False for row in result))
        self.assertEqual(
            {frozenset(row) for row in result},
            {frozenset({"id", "value", "is_group"})},
        )
        self.assertEqual(len(fake_frappe._get_all_calls), 1)
        doctype, options = fake_frappe._get_all_calls[0]
        self.assertEqual(doctype, "User")
        self.assertEqual(options["filters"]["user_type"], "System User")
        self.assertEqual(options["filters"]["allowed_in_mentions"], 1)
        self.assertGreater(options["limit_page_length"], 0)
        self.assertLessEqual(options["limit_page_length"], 50)


class MentionReplyMarkupTestCase(unittest.TestCase):
    def test_html_is_flattened_and_mentions_are_rebuilt_server_side(self):
        users = {"allowed@example.com": enabled_user("الموظف المسموح")}
        module, _ = load_service(users=users)
        module._can_read_reference = lambda thread, user: True
        raw = (
            '<div onclick="bad()">مرحبًا <strong>بك</strong>'
            '<script>alert(1)</script><img src=x onerror="bad()">'
            + mention("allowed@example.com", "اسم مزيف")
            + "</div>"
        )

        reply_text, safe_markup, explicit_users = call_service(
            module,
            "_normalize_reply_markup",
            SimpleNamespace(),
            "نص احتياطي يجب تجاهله",
            raw,
        )

        self.assertEqual(explicit_users, ["allowed@example.com"])
        self.assertIn("مرحبًا بك", reply_text)
        self.assertIn("@الموظف المسموح", reply_text)
        self.assertNotIn("alert", reply_text)
        for forbidden in ("script", "onclick", "onerror", "<img", "<strong", "اسم مزيف"):
            self.assertNotIn(forbidden, safe_markup)
        self.assertEqual(safe_markup.count('class="mention"'), 1)
        self.assertIn('data-id="allowed@example.com"', safe_markup)
        self.assertIn('data-is-group="false"', safe_markup)

    def test_valid_explicit_mention_deduplicates_automatic_reply_target(self):
        users = {"sender@example.com": enabled_user("مرسل الإشارة")}
        module, _ = load_service(users=users)
        module._can_read_reference = lambda thread, user: True
        thread = SimpleNamespace(latest_from_user="sender@example.com")

        reply_text, reply_markup, explicit_users = call_service(
            module,
            "_normalize_reply_markup",
            thread,
            "",
            f"<p>{mention('sender@example.com')} تم</p>",
        )
        comment_html, recipients = call_service(
            module,
            "_compose_reply_comment",
            thread,
            reply_markup,
            explicit_users,
            "me@example.com",
        )

        self.assertEqual(reply_text, "@مرسل الإشارة تم")
        self.assertEqual(recipients, ["sender@example.com"])
        self.assertEqual(comment_html.count('data-id="sender@example.com"'), 1)

    def test_invalid_disabled_or_unreadable_user_is_rejected(self):
        users = {
            "disabled@example.com": {
                **enabled_user("موظف معطل"),
                "enabled": 0,
            },
            "no-read@example.com": enabled_user("بلا صلاحية"),
        }
        module, fake_frappe = load_service(users=users)
        module._can_read_reference = lambda thread, user: user != "no-read@example.com"

        for candidate in (
            "missing@example.com",
            "disabled@example.com",
            "no-read@example.com",
        ):
            with self.subTest(candidate=candidate):
                with self.assertRaisesRegex(
                    fake_frappe.PermissionError,
                    "غير متاح لهذا المستند",
                ):
                    call_service(
                        module,
                        "_normalize_reply_markup",
                        SimpleNamespace(),
                        "",
                        f"<p>{mention(candidate)}</p>",
                    )

    def test_group_mentions_are_rejected(self):
        module, fake_frappe = load_service()
        with self.assertRaisesRegex(fake_frappe.ValidationError, "المجموعات"):
            call_service(
                module,
                "_normalize_reply_markup",
                SimpleNamespace(),
                "",
                f"<p>{mention('Sales Team', group=True)}</p>",
            )

    def test_reply_has_a_hard_recipient_limit(self):
        users = {
            f"user{index}@example.com": enabled_user(f"موظف {index}")
            for index in range(1, 7)
        }
        module, fake_frappe = load_service(users=users)
        module._can_read_reference = lambda thread, user: True
        markup = "<p>" + " ".join(
            mention(f"user{index}@example.com") for index in range(1, 7)
        ) + "</p>"

        with self.assertRaisesRegex(fake_frappe.ValidationError, "كحد أقصى"):
            call_service(
                module,
                "_normalize_reply_markup",
                SimpleNamespace(),
                "",
                markup,
            )

    def test_automatic_target_is_included_in_the_total_recipient_limit(self):
        users = {
            "target@example.com": enabled_user("المرسل"),
            **{
                f"user{index}@example.com": enabled_user(f"موظف {index}")
                for index in range(1, 6)
            },
        }
        module, fake_frappe = load_service(users=users)
        module._can_read_reference = lambda thread, user: True
        thread = SimpleNamespace(latest_from_user="target@example.com")
        markup = "<p>" + " ".join(
            mention(f"user{index}@example.com") for index in range(1, 6)
        ) + "</p>"
        _, reply_markup, explicit_users = call_service(
            module,
            "_normalize_reply_markup",
            thread,
            "",
            markup,
        )

        with self.assertRaisesRegex(fake_frappe.ValidationError, "كحد أقصى"):
            call_service(
                module,
                "_compose_reply_comment",
                thread,
                reply_markup,
                explicit_users,
                "me@example.com",
            )

    def test_idempotency_rejects_same_request_for_different_recipients(self):
        module, fake_frappe = load_service()
        thread = SimpleNamespace(
            name="THREAD-1",
            for_user="me@example.com",
            latest_from_user=None,
        )
        existing = SimpleNamespace(
            content_plain="نفس الرد",
            comment="COMMENT-1",
        )
        previous_comment = SimpleNamespace(
            name="COMMENT-1",
            content='<p><span class="mention" data-id="first@example.com">@First</span></p>',
        )
        fake_frappe.get_doc = lambda doctype, name: previous_comment
        module._get_owned_thread = lambda *args, **kwargs: thread
        module._get_reference_doc = lambda owned_thread: SimpleNamespace()
        module._assert_expected_event = lambda owned_thread, expected: expected
        module._existing_reply_event = lambda owned_thread, event_key: existing
        module._normalize_reply_markup = lambda *args, **kwargs: (
            "نفس الرد",
            "<p>نفس الرد</p>",
            ["second@example.com"],
        )
        module._compose_reply_comment = lambda *args, **kwargs: (
            '<p><span class="mention" data-id="second@example.com">@Second</span></p>',
            ["second@example.com"],
        )
        module._comment_mentions = lambda comment: ["first@example.com"]

        with self.assertRaisesRegex(
            fake_frappe.ValidationError,
            "لمستلمين مختلفين",
        ):
            call_service(
                module,
                "reply_mention",
                "THREAD-1",
                "نفس الرد",
                "same-request-id",
                "a" * 64,
                reply_html="<p>نفس الرد</p>",
            )


if __name__ == "__main__":
    unittest.main()
