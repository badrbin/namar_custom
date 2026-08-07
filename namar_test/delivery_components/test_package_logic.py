from __future__ import annotations

import unittest

from namar_test.delivery_components.package_logic import (
    TRACKING_STATUS_DELIVERED,
    TRACKING_STATUS_LOADED,
    TRACKING_STATUS_PENDING,
    TRACKING_STATUS_READY,
    build_fulfillment_readiness,
    build_package_specs,
    next_tracking_status,
    normalize_tracking_status,
)


class DeliveryComponentPackageLogicTestCase(unittest.TestCase):
    def test_twenty_frames_make_five_full_cartons(self):
        specs = build_package_specs(20, 4, "كرتون")
        self.assertEqual(len(specs), 5)
        self.assertEqual([row["package_qty"] for row in specs], [4, 4, 4, 4, 4])

    def test_twenty_two_frames_keep_remainder_carton(self):
        specs = build_package_specs(22, 4, "كرتون", "مغلف منفرد", "كرتون ناقص")
        self.assertEqual(len(specs), 6)
        self.assertEqual(specs[-1], {"package_label": "كرتون ناقص", "package_qty": 2})

    def test_package_stages_are_ordered(self):
        ready = next_tracking_status(TRACKING_STATUS_PENDING, "ready", package_is_ready=True)
        loaded = next_tracking_status(ready, "load", package_is_ready=True)
        delivered = next_tracking_status(loaded, "deliver", package_is_ready=True)
        self.assertEqual(ready, TRACKING_STATUS_READY)
        self.assertEqual(loaded, TRACKING_STATUS_LOADED)
        self.assertEqual(delivered, TRACKING_STATUS_DELIVERED)

    def test_package_cannot_load_before_ready(self):
        with self.assertRaisesRegex(ValueError, "قبل تسجيلها جاهزة"):
            next_tracking_status(TRACKING_STATUS_PENDING, "load", package_is_ready=False)

    def test_overall_ready_requires_doors_and_packages(self):
        result = build_fulfillment_readiness(
            door_total=20,
            door_remaining=0,
            package_total=5,
            package_ready=5,
            package_loaded=0,
            package_delivered=0,
        )
        self.assertEqual(result["status"], "جاهز بالكامل")
        self.assertTrue(result["is_ready"])

    def test_unsynced_packages_block_overall_readiness(self):
        result = build_fulfillment_readiness(
            door_total=20,
            door_remaining=0,
            package_total=0,
            package_ready=0,
            package_loaded=0,
            package_delivered=0,
            packages_need_sync=True,
        )
        self.assertEqual(result["status"], "غير جاهز")
        self.assertFalse(result["is_ready"])

    def test_ready_only_packages_do_not_require_loading(self):
        result = build_fulfillment_readiness(
            door_total=4,
            door_remaining=0,
            package_total=2,
            package_ready=2,
            package_loaded=0,
            package_delivered=0,
            package_load_total=0,
        )
        self.assertEqual(result["status"], "جاهز بالكامل")
        self.assertTrue(result["is_ready"])

    def test_legacy_ready_package_is_not_reset_by_new_default(self):
        self.assertEqual(normalize_tracking_status("غير جاهز", 4, 4), TRACKING_STATUS_READY)


if __name__ == "__main__":
    unittest.main()
