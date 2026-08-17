from __future__ import annotations

import unittest

from namar_test.followups.logic import (
    mention_event_key,
    mention_reply_event_key,
    mention_state_event_key,
    mention_thread_key,
    normalize_mention_bucket,
    normalize_request_id,
    normalize_seen,
    validate_expected_mention_event_key,
)


class MentionInboxLogicTestCase(unittest.TestCase):
    def test_thread_key_is_stable_and_scoped_to_recipient_and_reference(self):
        first = mention_thread_key("user@example.com", "Material Request", "MREQ-1")
        self.assertEqual(
            first,
            mention_thread_key("user@example.com", "Material Request", "MREQ-1"),
        )
        self.assertNotEqual(
            first,
            mention_thread_key("other@example.com", "Material Request", "MREQ-1"),
        )
        self.assertNotEqual(
            first,
            mention_thread_key("user@example.com", "Material Request", "MREQ-2"),
        )

    def test_mention_event_key_is_idempotent_for_exact_comment_snapshot(self):
        first = mention_event_key(
            "user@example.com",
            "COMMENT-1",
            "2026-08-17 10:30:00.000001",
            "<p>النص الأول</p>",
        )
        self.assertEqual(
            first,
            mention_event_key(
                "user@example.com",
                "COMMENT-1",
                "2026-08-17 10:30:00.000001",
                "<p>النص الأول</p>",
            ),
        )
        self.assertNotEqual(
            first,
            mention_event_key(
                "user@example.com",
                "COMMENT-1",
                "2026-08-17 10:31:00.000001",
                "<p>النص المعدل</p>",
            ),
        )

    def test_reply_event_key_uses_client_request_id_for_idempotency(self):
        first = mention_reply_event_key("THREAD-1", "user@example.com", "request-1")
        self.assertEqual(
            first,
            mention_reply_event_key("THREAD-1", "user@example.com", "request-1"),
        )
        self.assertNotEqual(
            first,
            mention_reply_event_key("THREAD-1", "user@example.com", "request-2"),
        )

    def test_state_event_key_rejects_unknown_event_types(self):
        self.assertTrue(
            mention_state_event_key(
                "Closed",
                "THREAD-1",
                "user@example.com",
                "2026-08-17 11:00:00.000001",
            )
        )
        with self.assertRaises(ValueError):
            mention_state_event_key(
                "Deleted",
                "THREAD-1",
                "user@example.com",
                "2026-08-17 11:00:00.000001",
            )

    def test_bucket_seen_and_request_id_inputs_are_strict(self):
        self.assertEqual(normalize_mention_bucket(" UNREAD "), "unread")
        self.assertEqual(normalize_mention_bucket(""), "open")
        self.assertTrue(normalize_seen("1"))
        self.assertFalse(normalize_seen("false"))
        self.assertEqual(normalize_request_id("reply:123-abc"), "reply:123-abc")
        with self.assertRaises(ValueError):
            normalize_mention_bucket("all")
        with self.assertRaises(ValueError):
            normalize_seen("sometimes")
        with self.assertRaises(ValueError):
            normalize_request_id("contains spaces")

    def test_expected_event_key_rejects_stale_or_malformed_versions(self):
        event_key = mention_event_key(
            "user@example.com",
            "COMMENT-1",
            "2026-08-17 10:30:00.000001",
            "<p>النص الأول</p>",
        )
        self.assertEqual(
            validate_expected_mention_event_key(event_key, event_key),
            event_key,
        )
        with self.assertRaisesRegex(ValueError, "تم تحديث هذه الإشارة منذ عرضها"):
            validate_expected_mention_event_key(event_key, "f" * 64)
        with self.assertRaisesRegex(ValueError, "رمز نسخة الإشارة غير صحيح"):
            validate_expected_mention_event_key("not-a-key", event_key)


if __name__ == "__main__":
    unittest.main()
