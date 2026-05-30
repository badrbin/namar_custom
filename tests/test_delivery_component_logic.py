import unittest

from namar_test.delivery_components.package_logic import (
    build_package_specs,
    clean_count,
    is_valid_loading_prefix,
    loading_prefix_from_index,
    package_status,
)


class DeliveryComponentLogicTest(unittest.TestCase):
    def test_full_pack_exact(self):
        self.assertEqual(
            build_package_specs(4, 4, "كرتون", "مغلف منفرد", "كرتون ناقص"),
            [{"package_label": "كرتون", "package_qty": 4}],
        )

    def test_full_pack_with_single_remainder(self):
        self.assertEqual(
            build_package_specs(5, 4, "كرتون", "مغلف منفرد", "كرتون ناقص"),
            [
                {"package_label": "كرتون", "package_qty": 4},
                {"package_label": "مغلف منفرد", "package_qty": 1},
            ],
        )

    def test_full_pack_with_multi_remainder(self):
        self.assertEqual(
            build_package_specs(10, 4, "كرتون", "مغلف منفرد", "كرتون ناقص"),
            [
                {"package_label": "كرتون", "package_qty": 4},
                {"package_label": "كرتون", "package_qty": 4},
                {"package_label": "كرتون ناقص", "package_qty": 2},
            ],
        )

    def test_single_package_without_full_pack_qty(self):
        self.assertEqual(build_package_specs(7.5, 0, "حزمة"), [{"package_label": "حزمة", "package_qty": 7.5}])

    def test_status_and_loading_prefix(self):
        self.assertEqual(package_status(4, 0), "غير جاهز")
        self.assertEqual(package_status(4, 2), "جزئي")
        self.assertEqual(package_status(4, 4), "جاهز")
        self.assertTrue(is_valid_loading_prefix("AA"))
        self.assertEqual(loading_prefix_from_index(0), "AA")
        self.assertEqual(loading_prefix_from_index(675), "ZZ")
        self.assertEqual(loading_prefix_from_index(676), "AA")
        self.assertEqual(clean_count(4.0), 4)


if __name__ == "__main__":
    unittest.main()
