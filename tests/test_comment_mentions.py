from __future__ import annotations

import re
import unittest

from namar_test.comment_mentions import get_new_mentions


def extract_test_mentions(content: str):
    return re.findall(r'class="mention"[^>]*data-id="([^"]+)"', content or "")


def mention(user: str) -> str:
    return f'<span class="mention" data-id="{user}">@ {user}</span>'


class CommentMentionDeltaTest(unittest.TestCase):
    def test_adds_only_new_mentions(self):
        old_content = f"<p>{mention('old@example.com')}</p>"
        new_content = f"<p>{mention('old@example.com')} {mention('new@example.com')}</p>"

        self.assertEqual(
            get_new_mentions(old_content, new_content, extractor=extract_test_mentions),
            ["new@example.com"],
        )

    def test_does_not_repeat_existing_mentions(self):
        old_content = f"<p>{mention('user@example.com')}</p>"
        new_content = f"<p>{mention('user@example.com')}</p><p>نص محدث فقط</p>"

        self.assertEqual(
            get_new_mentions(old_content, new_content, extractor=extract_test_mentions),
            [],
        )

    def test_deduplicates_new_mentions_preserving_order(self):
        new_content = (
            f"<p>{mention('first@example.com')} {mention('second@example.com')} "
            f"{mention('first@example.com')}</p>"
        )

        self.assertEqual(
            get_new_mentions("", new_content, extractor=extract_test_mentions),
            ["first@example.com", "second@example.com"],
        )

    def test_returns_empty_when_new_content_has_no_mentions(self):
        self.assertEqual(
            get_new_mentions("", "<p>لا يوجد منشن</p>", extractor=extract_test_mentions),
            [],
        )


if __name__ == "__main__":
    unittest.main()
