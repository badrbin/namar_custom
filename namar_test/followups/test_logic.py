from __future__ import annotations

import unittest
from datetime import date, datetime

from namar_test.followups.logic import (
    MAX_PAGE_LENGTH,
    assignment_args,
    classify_followup,
    exact_close_args,
    normalize_bucket,
    normalize_date,
    normalize_priority,
    page_window,
    pagination,
    plain_text,
    timeline_comment,
    timeline_target,
    todo_filters,
    validate_owned_todo,
)


class FollowupsLogicTestCase(unittest.TestCase):
    def test_page_window_is_bounded_and_fetches_sentinel_row(self):
        self.assertEqual(page_window("10", "500"), (10, MAX_PAGE_LENGTH, MAX_PAGE_LENGTH + 1))
        self.assertEqual(page_window("bad", "bad"), (0, 50, 51))
        self.assertEqual(page_window(-1, 20), (0, 20, 21))

    def test_buckets_are_normalized_and_rejected(self):
        self.assertEqual(normalize_bucket(" TODAY "), "today")
        self.assertEqual(normalize_bucket(""), "all")
        with self.assertRaises(ValueError):
            normalize_bucket("archived")

    def test_todo_filters_always_scope_allocated_to_current_user(self):
        expected_user = "employee@example.com"
        self.assertEqual(
            todo_filters("all", expected_user, "2026-08-16"),
            {"allocated_to": expected_user, "status": "Open"},
        )
        self.assertEqual(
            todo_filters("overdue", expected_user, "2026-08-16")["date"],
            ["<", "2026-08-16"],
        )
        self.assertEqual(
            todo_filters("today", expected_user, "2026-08-16")["date"],
            "2026-08-16",
        )
        self.assertEqual(
            todo_filters("upcoming", expected_user, "2026-08-16")["date"],
            [">", "2026-08-16"],
        )
        self.assertEqual(
            todo_filters("recent", expected_user, "2026-08-16"),
            {"allocated_to": expected_user, "status": "Closed"},
        )

    def test_followup_classification(self):
        today = "2026-08-16"
        self.assertEqual(classify_followup("Open", "2026-08-15", today), "overdue")
        self.assertEqual(classify_followup("Open", today, today), "today")
        self.assertEqual(classify_followup("Open", "2026-08-17", today), "upcoming")
        self.assertEqual(classify_followup("Open", None, today), "open")
        self.assertEqual(classify_followup("Closed", today, today), "recent")
        self.assertEqual(classify_followup("Cancelled", today, today), "other")

    def test_normalize_date_accepts_date_objects_and_rejects_invalid_values(self):
        self.assertEqual(normalize_date(date(2026, 8, 16)), "2026-08-16")
        self.assertEqual(normalize_date(datetime(2026, 8, 16, 10, 30)), "2026-08-16")
        with self.assertRaises(ValueError):
            normalize_date("16/08/2026")
        with self.assertRaises(ValueError):
            normalize_date("2026-08-16-extra")

    def test_priority_validation(self):
        self.assertEqual(normalize_priority(None), "Medium")
        self.assertEqual(normalize_priority("High"), "High")
        with self.assertRaises(ValueError):
            normalize_priority("Urgent")

    def test_owned_todo_requires_exact_assignee_and_open_status_for_mutations(self):
        todo = {"allocated_to": "employee@example.com", "status": "Open"}
        validate_owned_todo(todo, "employee@example.com", require_open=True)
        with self.assertRaises(PermissionError):
            validate_owned_todo(todo, "other@example.com", require_open=True)
        with self.assertRaises(ValueError):
            validate_owned_todo(
                {**todo, "status": "Closed"},
                "employee@example.com",
                require_open=True,
            )

    def test_exact_close_args_identify_todo_not_just_shared_reference(self):
        first = {
            "name": "TODO-1",
            "reference_type": "Material Request",
            "reference_name": "MREQ-1",
            "allocated_to": "employee@example.com",
        }
        second = {**first, "name": "TODO-2"}
        first_args = exact_close_args(first)
        second_args = exact_close_args(second)

        self.assertEqual(first_args["todo"], "TODO-1")
        self.assertEqual(second_args["todo"], "TODO-2")
        self.assertEqual(first_args["name"], second_args["name"])
        self.assertFalse(first_args["ignore_permissions"])

    def test_assignment_args_are_validated_and_use_one_explicit_assignee(self):
        args = assignment_args(
            reference_type="Material Request",
            reference_name="MREQ-1",
            description="اتصل بالعميل",
            due_date="2026-08-20",
            priority="High",
            allocated_to="employee@example.com",
            assigned_by="manager@example.com",
        )
        self.assertEqual(args["assign_to"], '["employee@example.com"]')
        self.assertEqual(args["date"], "2026-08-20")
        self.assertEqual(args["priority"], "High")

    def test_timeline_comment_escapes_html_and_preserves_line_breaks(self):
        comment = timeline_comment("نتيجة المتابعة", "تم <script>x</script>\nبنجاح", 4000)
        self.assertNotIn("<script>", comment)
        self.assertIn("&lt;script&gt;", comment)
        self.assertIn("<br>", comment)

    def test_plain_text_removes_markup_and_decodes_entities(self):
        self.assertEqual(
            plain_text("<p>اتصال &amp; متابعة</p><script>alert(1)</script>"),
            "اتصال & متابعة alert(1)",
        )

    def test_unlinked_todo_uses_its_own_timeline(self):
        self.assertEqual(
            timeline_target({"name": "TODO-STANDALONE"}),
            ("ToDo", "TODO-STANDALONE"),
        )
        self.assertEqual(
            timeline_target(
                {
                    "name": "TODO-LINKED",
                    "reference_type": "Material Request",
                    "reference_name": "MREQ-1",
                }
            ),
            ("Material Request", "MREQ-1"),
        )

    def test_pagination_removes_sentinel_row(self):
        result = pagination([1, 2, 3], 0, 2)
        self.assertEqual(result["items"], [1, 2])
        self.assertTrue(result["has_more"])
        self.assertEqual(result["next_start"], 2)


if __name__ == "__main__":
    unittest.main()
