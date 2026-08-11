from __future__ import annotations

import unittest

from namar_test.delivery_components.package_logic import (
    PACKAGE_EVENT_ACTION_REGISTERED,
    TRACKING_STATUS_DELIVERED,
    TRACKING_STATUS_LOADED,
    TRACKING_STATUS_PENDING,
    TRACKING_STATUS_READY,
    TRACKING_ROUTE_BARCODE,
    TRACKING_ROUTE_DELIVERY_ONLY,
    TRACKING_ROUTE_EXCLUDED,
    TRACKING_ROUTE_WITH_DOOR,
    assign_stable_loading_codes,
    build_fulfillment_readiness,
    build_package_specs,
    build_reconciled_package_specs,
    component_color_from_item_code,
    component_package_key,
    combined_manufacturing_status,
    legacy_component_package_key,
    legacy_color_split_has_started_rows,
    next_tracking_status,
    normalize_component_color,
    normalize_tracking_route,
    normalize_tracking_status,
    should_rotate_unregistered_barcodes,
)
from namar_test.delivery_components.tracking_code_logic import (
    is_valid_request_tracking_code,
    package_tracking_code,
    split_package_tracking_code,
    tracking_code_from_sequence,
)


class DeliveryComponentPackageLogicTestCase(unittest.TestCase):
    def test_completed_package_uses_supported_event_action(self):
        self.assertEqual(PACKAGE_EVENT_ACTION_REGISTERED, "تسجيل")

    def test_tracking_routes_keep_legacy_values_compatible(self):
        self.assertEqual(normalize_tracking_route("تصنيع وتغليف"), TRACKING_ROUTE_BARCODE)
        self.assertEqual(normalize_tracking_route("تجهيز فقط"), TRACKING_ROUTE_DELIVERY_ONLY)
        self.assertEqual(normalize_tracking_route("لا يتتبع"), TRACKING_ROUTE_EXCLUDED)

    def test_tracking_routes_accept_new_data_driven_values(self):
        self.assertEqual(normalize_tracking_route(TRACKING_ROUTE_BARCODE), TRACKING_ROUTE_BARCODE)
        self.assertEqual(normalize_tracking_route(TRACKING_ROUTE_WITH_DOOR), TRACKING_ROUTE_WITH_DOOR)
        self.assertEqual(
            normalize_tracking_route(TRACKING_ROUTE_DELIVERY_ONLY),
            TRACKING_ROUTE_DELIVERY_ONLY,
        )
        self.assertEqual(normalize_tracking_route(TRACKING_ROUTE_EXCLUDED), TRACKING_ROUTE_EXCLUDED)

    def test_unknown_tracking_route_is_delivery_only_by_default(self):
        self.assertEqual(normalize_tracking_route(""), TRACKING_ROUTE_DELIVERY_ONLY)
        self.assertEqual(normalize_tracking_route("غير معروف"), TRACKING_ROUTE_DELIVERY_ONLY)

    def test_twenty_frames_make_five_full_cartons(self):
        specs = build_package_specs(20, 4, "كرتون")
        self.assertEqual(len(specs), 5)
        self.assertEqual([row["package_qty"] for row in specs], [4, 4, 4, 4, 4])

    def test_twenty_two_frames_keep_remainder_carton(self):
        specs = build_package_specs(22, 4, "كرتون", "مغلف منفرد", "كرتون ناقص")
        self.assertEqual(len(specs), 6)
        self.assertEqual(specs[-1], {"package_label": "كرتون ناقص", "package_qty": 2})

    def test_one_hundred_frames_make_twenty_five_cartons(self):
        specs = build_package_specs(100, 4, "كرتون")
        self.assertEqual(len(specs), 25)
        self.assertEqual({row["package_qty"] for row in specs}, {4})

    def test_five_hundred_frames_make_eighty_four_cartons(self):
        specs = build_package_specs(500, 6, "كرتون", "قطعة", "كرتون ناقص")
        self.assertEqual(len(specs), 84)
        self.assertEqual([row["package_qty"] for row in specs[:83]], [6] * 83)
        self.assertEqual(specs[-1], {"package_label": "كرتون ناقص", "package_qty": 2})

    def test_decimal_quantity_makes_one_partial_carton(self):
        specs = build_package_specs(2.5, 4, "كرتون", "قطعة", "كرتون ناقص")
        self.assertEqual(
            specs,
            [{"package_label": "كرتون ناقص", "package_qty": 2.5}],
        )

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

    def test_combined_manufacturing_waits_for_tracked_packages(self):
        readiness = build_fulfillment_readiness(
            door_total=4,
            door_remaining=0,
            package_total=2,
            package_ready=1,
            package_loaded=0,
            package_delivered=0,
        )
        self.assertEqual(combined_manufacturing_status(readiness), "قيد التصنيع")

    def test_combined_manufacturing_completes_after_doors_and_packages(self):
        readiness = build_fulfillment_readiness(
            door_total=4,
            door_remaining=0,
            package_total=2,
            package_ready=2,
            package_loaded=0,
            package_delivered=0,
        )
        self.assertEqual(combined_manufacturing_status(readiness), "مصنع بالكامل")

    def test_combined_manufacturing_supports_package_only_requests(self):
        readiness = build_fulfillment_readiness(
            door_total=0,
            door_remaining=0,
            package_total=3,
            package_ready=3,
            package_loaded=0,
            package_delivered=0,
        )
        self.assertEqual(combined_manufacturing_status(readiness), "مصنع بالكامل")

    def test_combined_manufacturing_without_trackable_units_is_not_manufactured(self):
        readiness = build_fulfillment_readiness(
            door_total=0,
            door_remaining=0,
            package_total=0,
            package_ready=0,
            package_loaded=0,
            package_delivered=0,
        )
        self.assertEqual(combined_manufacturing_status(readiness), "غير مصنع")

    def test_legacy_ready_package_is_not_reset_by_new_default(self):
        self.assertEqual(normalize_tracking_status("غير جاهز", 4, 4), TRACKING_STATUS_READY)

    def test_started_legacy_package_is_frozen_when_new_rule_would_split_it(self):
        specs = build_reconciled_package_specs(
            required_qty=4,
            full_pack_qty=1,
            full_label="باب",
            remainder_one_label="باب",
            remainder_multi_label="حزمة ناقصة",
            existing_rows=[
                {
                    "package_key": "بانل 92X4.5||||1",
                    "package_qty": 4,
                    "package_label": "حزمة",
                    "ready_qty": 4,
                    "tracking_status": "جاهز",
                }
            ],
        )

        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0]["package_qty"], 4)
        self.assertEqual(specs[0]["package_label"], "حزمة")
        self.assertTrue(specs[0]["legacy_started"])

    def test_only_remaining_quantity_uses_current_packaging_rule(self):
        specs = build_reconciled_package_specs(
            required_qty=15,
            full_pack_qty=6,
            full_label="كرتون",
            remainder_one_label="قطعة",
            remainder_multi_label="كرتون ناقص",
            existing_rows=[
                {
                    "package_key": "برواز 8X2.6||||1",
                    "package_qty": 6,
                    "package_label": "كرتون",
                    "ready_qty": 6,
                    "tracking_status": "جاهز",
                },
                {
                    "package_key": "برواز 8X2.6||||2",
                    "package_qty": 6,
                    "package_label": "كرتون",
                    "ready_qty": 0,
                    "tracking_status": "غير جاهز",
                },
            ],
        )

        self.assertEqual([row["package_qty"] for row in specs], [6, 6, 3])
        self.assertEqual([row["package_no"] for row in specs], [1, 2, 3])
        self.assertEqual([row["legacy_started"] for row in specs], [True, False, False])

    def test_sync_is_blocked_if_source_drops_below_started_packages(self):
        with self.assertRaisesRegex(ValueError, "أقل من كمية حزم بدأ تتبعها"):
            build_reconciled_package_specs(
                required_qty=3,
                full_pack_qty=1,
                full_label="باب",
                remainder_one_label="باب",
                remainder_multi_label="حزمة ناقصة",
                existing_rows=[
                    {
                        "package_key": "بانل 92X4.5||||1",
                        "package_qty": 4,
                        "ready_qty": 4,
                        "tracking_status": "جاهز",
                    }
                ],
            )

    def test_loading_codes_preserve_existing_and_fill_gaps(self):
        rows = [
            {"loading_code": "AC-03"},
            {},
            {"loading_code": "AC-01"},
            {},
        ]

        assigned = assign_stable_loading_codes(rows, "AC")

        self.assertEqual(
            [row["loading_code"] for row in assigned],
            ["AC-03", "AC-02", "AC-01", "AC-04"],
        )

    def test_tracking_codes_use_unambiguous_three_character_alphabet(self):
        self.assertEqual(tracking_code_from_sequence(1), "AAA")
        self.assertNotIn("I", tracking_code_from_sequence(999))
        self.assertNotIn("O", tracking_code_from_sequence(999))
        self.assertTrue(is_valid_request_tracking_code("A7K"))
        self.assertTrue(is_valid_request_tracking_code("AD"))
        self.assertFalse(is_valid_request_tracking_code("A1O"))

    def test_tracking_code_expands_after_three_character_capacity(self):
        from namar_test.delivery_components.tracking_code_logic import TRACKING_ALPHABET

        self.assertEqual(len(tracking_code_from_sequence(len(TRACKING_ALPHABET) ** 3)), 3)
        self.assertEqual(len(tracking_code_from_sequence(len(TRACKING_ALPHABET) ** 3 + 1)), 4)

    def test_package_tracking_code_supports_more_than_ninety_nine_packages(self):
        self.assertEqual(package_tracking_code("A7K", 1), "A7K-01")
        self.assertEqual(package_tracking_code("A7K", 100), "A7K-100")
        self.assertEqual(split_package_tracking_code("A7K-100"), ("A7K", 100))
        self.assertEqual(split_package_tracking_code("AD-01"), ("AD", 1))

    def test_component_color_matches_cutting_report_prefixes(self):
        self.assertEqual(component_color_from_item_code("T5D1"), "تك")
        self.assertEqual(component_color_from_item_code("WS5D1"), "قشر الجوز")
        self.assertEqual(component_color_from_item_code("011-H1"), "بني 011")
        self.assertEqual(component_color_from_item_code("UNKNOWN"), "")

    def test_explicit_color_can_be_code_or_label(self):
        self.assertEqual(normalize_component_color("T"), "تك")
        self.assertEqual(normalize_component_color("قشر الجوز"), "قشر الجوز")

    def test_color_is_part_of_package_identity(self):
        self.assertEqual(
            component_package_key("حلق 20", "تك", "", 2),
            "حلق 20||تك||||2",
        )
        self.assertEqual(
            legacy_component_package_key("حلق 20", "", 2),
            "حلق 20||||2",
        )

    def test_scanned_legacy_package_blocks_ambiguous_color_split(self):
        self.assertTrue(
            legacy_color_split_has_started_rows(
                {"تك", "قشر الجوز"},
                [{"package_qty": 4, "ready_qty": 4, "tracking_status": "جاهز"}],
            )
        )
        self.assertFalse(
            legacy_color_split_has_started_rows(
                {"تك", "قشر الجوز"},
                [{"package_qty": 4, "ready_qty": 0, "tracking_status": "غير جاهز"}],
            )
        )

    def test_existing_legacy_packages_rotate_when_source_hash_is_missing(self):
        self.assertTrue(
            should_rotate_unregistered_barcodes(
                "",
                "new-source-hash",
                has_existing_packages=True,
            )
        )

    def test_new_or_unchanged_packages_do_not_rotate(self):
        self.assertFalse(
            should_rotate_unregistered_barcodes(
                "",
                "new-source-hash",
                has_existing_packages=False,
            )
        )
        self.assertFalse(
            should_rotate_unregistered_barcodes(
                "same-source-hash",
                "same-source-hash",
                has_existing_packages=True,
            )
        )


if __name__ == "__main__":
    unittest.main()
