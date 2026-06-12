from __future__ import annotations

import re
import unittest

from namar_custom.comment_mentions import (
    get_mentions_to_notify_on_update,
    is_comment_edit_mentions_enabled,
)


def extract_test_mentions(content: str):
    return re.findall(r'class="mention"[^>]*data-id="([^"]+)"', content or "")


def mention(user: str) -> str:
    return f'<span class="mention" data-id="{user}">@ {user}</span>'


class CommentMentionUpdateNotificationTest(unittest.TestCase):
    def test_notifies_all_current_mentions_when_adding_a_mention(self):
        old_content = f"<p>{mention('old@example.com')}</p>"
        new_content = f"<p>{mention('old@example.com')} {mention('new@example.com')}</p>"

        self.assertEqual(
            get_mentions_to_notify_on_update(old_content, new_content, extractor=extract_test_mentions),
            ["old@example.com", "new@example.com"],
        )

    def test_notifies_existing_mentions_on_text_only_edit(self):
        old_content = f"<p>{mention('user@example.com')}</p>"
        new_content = f"<p>{mention('user@example.com')}</p><p>نص محدث فقط</p>"

        self.assertEqual(
            get_mentions_to_notify_on_update(old_content, new_content, extractor=extract_test_mentions),
            ["user@example.com"],
        )

    def test_deduplicates_current_mentions_preserving_order(self):
        new_content = (
            f"<p>{mention('first@example.com')} {mention('second@example.com')} "
            f"{mention('first@example.com')}</p>"
        )

        self.assertEqual(
            get_mentions_to_notify_on_update("", new_content, extractor=extract_test_mentions),
            ["first@example.com", "second@example.com"],
        )

    def test_returns_empty_when_new_content_has_no_mentions(self):
        self.assertEqual(
            get_mentions_to_notify_on_update("", "<p>لا يوجد منشن</p>", extractor=extract_test_mentions),
            [],
        )

    def test_does_not_notify_removed_mentions(self):
        old_content = f"<p>{mention('removed@example.com')} {mention('kept@example.com')}</p>"
        new_content = f"<p>{mention('kept@example.com')} — تم تعديل النص</p>"

        self.assertEqual(
            get_mentions_to_notify_on_update(old_content, new_content, extractor=extract_test_mentions),
            ["kept@example.com"],
        )

    def test_comment_edit_mentions_enabled_by_default(self):
        self.assertTrue(is_comment_edit_mentions_enabled(lambda key, default=None: default))

    def test_comment_edit_mentions_can_be_disabled_by_config(self):
        disabled_values = [0, False, "0", "false", "False", "no", "off", "disabled"]
        for disabled_value in disabled_values:
            with self.subTest(disabled_value=disabled_value):
                self.assertFalse(
                    is_comment_edit_mentions_enabled(lambda key, default=None, value=disabled_value: value)
                )


if __name__ == "__main__":
    unittest.main()
