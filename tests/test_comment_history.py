from __future__ import annotations

import json
import unittest

from namar_test.comment_history import build_comment_histories, extract_content_change


def version(
    name,
    comment,
    owner,
    creation,
    old_content,
    new_content,
    *,
    extra_changes=None,
    audit_fields=None,
):
    changed = list(extra_changes or [])
    changed.append(["content", old_content, new_content])
    data = {"changed": changed}
    data.update(audit_fields or {})
    return {
        "name": name,
        "docname": comment,
        "owner": owner,
        "creation": creation,
        "data": json.dumps(data),
    }


class CommentHistoryTest(unittest.TestCase):
    def test_extracts_content_change_and_ignores_other_fields(self):
        self.assertEqual(
            extract_content_change(
                json.dumps({"changed": [["published", 0, 1], ["content", "قديم", "جديد"]]})
            ),
            ("قديم", "جديد"),
        )
        self.assertIsNone(extract_content_change(json.dumps({"changed": [["published", 0, 1]]})))
        self.assertIsNone(extract_content_change("not-json"))

    def test_builds_newest_first_history_and_marks_original(self):
        comments = [{"name": "COMMENT-1"}, {"name": "COMMENT-WITHOUT-EDITS"}]
        versions = [
            version(
                "VERSION-2",
                "COMMENT-1",
                "second@example.com",
                "2026-08-15 11:00:00",
                "النص الثاني",
                "النص الحالي",
            ),
            version(
                "VERSION-1",
                "COMMENT-1",
                "first@example.com",
                "2026-08-15 10:00:00",
                "النص الأصلي",
                "النص الثاني",
                audit_fields={"impersonated_by": "admin@example.com"},
            ),
        ]

        histories = build_comment_histories(
            comments,
            versions,
            full_names={
                "first@example.com": "المستخدم الأول",
                "second@example.com": "المستخدم الثاني",
                "admin@example.com": "مدير النظام",
            },
            sanitize=lambda content: f"clean:{content}",
        )

        self.assertNotIn("COMMENT-WITHOUT-EDITS", histories)
        history = histories["COMMENT-1"]
        self.assertEqual(history["edit_count"], 2)
        self.assertNotIn("version", history["revisions"][0])
        self.assertEqual(history["last_edited_at"], "2026-08-15 11:00:00")
        self.assertEqual(history["last_edited_by_full_name"], "المستخدم الثاني")
        self.assertEqual(
            [revision["edit_number"] for revision in history["revisions"]],
            [2, 1],
        )
        self.assertEqual(history["revisions"][0]["before_content"], "clean:النص الثاني")
        self.assertEqual(history["revisions"][0]["after_content"], "clean:النص الحالي")
        self.assertFalse(history["revisions"][0]["is_earliest_recorded"])
        self.assertEqual(history["revisions"][1]["before_content"], "clean:النص الأصلي")
        self.assertEqual(history["revisions"][1]["after_content"], "clean:النص الثاني")
        self.assertTrue(history["revisions"][1]["is_earliest_recorded"])
        self.assertEqual(history["revisions"][1]["impersonated_by"], "admin@example.com")
        self.assertEqual(history["revisions"][1]["impersonated_by_full_name"], "مدير النظام")

    def test_excludes_versions_for_comments_outside_the_parent_document(self):
        histories = build_comment_histories(
            [{"name": "COMMENT-1"}],
            [
                version(
                    "VERSION-OTHER",
                    "COMMENT-OTHER",
                    "user@example.com",
                    "2026-08-15 10:00:00",
                    "قديم",
                    "جديد",
                )
            ],
        )

        self.assertEqual(histories, {})


if __name__ == "__main__":
    unittest.main()
