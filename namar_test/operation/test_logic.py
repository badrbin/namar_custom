from __future__ import annotations

import unittest

from namar_test.operation.logic import (
    MAX_PAGE,
    MAX_PAGE_LENGTH,
    MAX_QUANTITY,
    normalize_item_payloads,
    page_window,
    parse_mapping,
    role_can_edit,
    sanitize_fields,
    timestamps_match,
)


def item_row(**values):
    row = {
        "item_code": "ITEM-1",
        "qty": 1,
        "uom": "Nos",
        "warehouse": "Stores - N",
        "schedule_date": "2026-08-05",
    }
    row.update(values)
    return row


class OperationLogicTestCase(unittest.TestCase):
    def test_page_window_is_bounded(self):
        self.assertEqual(page_window("2", "500"), (2, MAX_PAGE_LENGTH, MAX_PAGE_LENGTH, MAX_PAGE_LENGTH + 1))
        self.assertEqual(page_window("bad", "bad"), (1, 20, 0, 21))
        self.assertEqual(
            page_window(MAX_PAGE + 1, 20),
            (MAX_PAGE, 20, (MAX_PAGE - 1) * 20, 21),
        )

    def test_parse_mapping_accepts_dict_and_json(self):
        self.assertEqual(parse_mapping({"name": "MREQ-1"}), {"name": "MREQ-1"})
        self.assertEqual(parse_mapping('{"name":"MREQ-2"}'), {"name": "MREQ-2"})
        with self.assertRaises(ValueError):
            parse_mapping("[]")

    def test_timestamps_match_equivalent_formats(self):
        self.assertTrue(timestamps_match("2026-08-05T10:00:00", "2026-08-05 10:00:00.000000"))
        self.assertFalse(timestamps_match("2026-08-05 10:00:00", "2026-08-05 10:00:01"))
        self.assertFalse(timestamps_match("", "2026-08-05 10:00:01"))

    def test_sanitize_fields_uses_allowlist(self):
        self.assertEqual(
            sanitize_fields({"qty": 2, "workflow_state": "مكتمل"}, {"qty"}),
            {"qty": 2},
        )

    def test_items_allow_duplicate_item_codes(self):
        rows = normalize_item_payloads(
            [
                item_row(qty=1),
                item_row(qty=2),
            ],
            {"item_code", "qty", "uom", "warehouse", "schedule_date"},
        )
        self.assertEqual([row["qty"] for row in rows], [1.0, 2.0])

    def test_items_reject_unknown_or_duplicate_child_names(self):
        with self.assertRaisesRegex(ValueError, "لا يتبع"):
            normalize_item_payloads(
                [item_row(name="ROW-X")],
                {"item_code", "qty", "uom", "warehouse", "schedule_date"},
                existing_names={"ROW-1"},
                allow_existing_names=True,
            )
        with self.assertRaisesRegex(ValueError, "مكرر"):
            normalize_item_payloads(
                [
                    item_row(name="ROW-1", qty=1),
                    item_row(name="ROW-1", qty=2),
                ],
                {"item_code", "qty", "uom", "warehouse", "schedule_date"},
                existing_names={"ROW-1"},
                allow_existing_names=True,
            )

    def test_items_require_positive_quantity(self):
        with self.assertRaisesRegex(ValueError, "أكبر من صفر"):
            normalize_item_payloads(
                [item_row(qty=0)],
                {"item_code", "qty", "uom", "warehouse", "schedule_date"},
            )

    def test_items_reject_non_finite_or_excessive_quantity(self):
        allowed = {"item_code", "qty", "uom", "warehouse", "schedule_date"}
        for quantity in (float("nan"), float("inf"), MAX_QUANTITY + 1):
            with self.assertRaisesRegex(ValueError, "الكمية"):
                normalize_item_payloads([item_row(qty=quantity)], allowed)

    def test_items_require_operational_fields(self):
        allowed = {"item_code", "qty", "uom", "warehouse", "schedule_date"}
        for fieldname in ("uom", "warehouse", "schedule_date"):
            with self.assertRaisesRegex(ValueError, "مطلوب"):
                normalize_item_payloads([item_row(**{fieldname: ""})], allowed)

    def test_workflow_edit_role(self):
        self.assertTrue(role_can_edit("قسم الإنتاج", {"قسم الإنتاج"}))
        self.assertFalse(role_can_edit("قسم الإنتاج", {"Sales User"}))
        self.assertTrue(role_can_edit("قسم الإنتاج", set(), is_administrator=True))


if __name__ == "__main__":
    unittest.main()
