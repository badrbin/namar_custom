from __future__ import annotations

import unittest

from namar_custom.delivery_components.identifier_repair import (
    plan_package_loading_code_repairs,
)


class DeliveryComponentIdentifierRepairTestCase(unittest.TestCase):
    def test_reassigns_only_unstarted_rows_that_collide_with_history(self):
        updates = plan_package_loading_code_repairs(
            [
                {"name": "OLD-1", "idx": 1, "loading_code": "AF-01", "active": 0, "started": True},
                {"name": "OLD-2", "idx": 2, "loading_code": "AF-02", "active": 0, "started": True},
                {"name": "NEW-1", "idx": 3, "loading_code": "AF-01", "active": 1, "started": False},
                {"name": "NEW-2", "idx": 4, "loading_code": "", "active": 1, "started": False},
            ],
            "AF",
        )

        self.assertEqual(
            updates,
            [
                {"name": "NEW-1", "old_loading_code": "AF-01", "loading_code": "AF-03"},
                {"name": "NEW-2", "old_loading_code": "", "loading_code": "AF-04"},
            ],
        )

    def test_blocks_duplicate_codes_between_started_packages(self):
        with self.assertRaisesRegex(ValueError, "حزم محمية"):
            plan_package_loading_code_repairs(
                [
                    {"name": "OLD", "idx": 1, "loading_code": "AF-01", "active": 0, "started": True},
                    {"name": "STARTED", "idx": 2, "loading_code": "AF-01", "active": 1, "started": True},
                ],
                "AF",
            )

    def test_blocks_started_package_without_loading_code(self):
        with self.assertRaisesRegex(ValueError, "مسجلة بلا رمز"):
            plan_package_loading_code_repairs(
                [{"name": "STARTED", "idx": 1, "loading_code": "", "active": 1, "started": True}],
                "AF",
            )


if __name__ == "__main__":
    unittest.main()
