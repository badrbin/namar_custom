from __future__ import annotations

import unittest

from namar_custom.delivery_components.supply_manifest import build_delivery_supply_manifest


class DeliverySupplyManifestTestCase(unittest.TestCase):
    def test_manifest_includes_request_items_and_every_supply_route(self):
        manifest = build_delivery_supply_manifest(
            {
                "name": "MREQ-TEST",
                "modified": "2026-08-17 18:00:00",
                "items": [
                    {"item_code": "DOOR-1", "item_name": "باب", "qty": 2, "stock_uom": "Nos"},
                    {"item_code": "DOOR-1", "item_name": "باب", "qty": 3, "stock_uom": "Nos"},
                ],
                "custom_delivery_component_packages": [
                    {
                        "component": "حلق 20",
                        "color": "تك",
                        "package_qty": 4,
                        "package_label": "كرتون",
                        "tracking_route": "تصنيع مستقل - باركود",
                        "active": 1,
                    },
                    {
                        "component": "فوم",
                        "package_qty": 5,
                        "package_label": "حزمة",
                        "tracking_route": "توريد فقط - بدون باركود",
                        "active": 1,
                    },
                    {
                        "component": "مفصلات",
                        "package_qty": 6,
                        "package_label": "كرتون",
                        "tracking_route": "مع الباب",
                        "active": 1,
                    },
                ],
            }
        )

        self.assertEqual(manifest["request_items"][0]["quantity"], 5)
        self.assertEqual(manifest["request_item_count"], 1)
        self.assertEqual(manifest["component_count"], 3)
        self.assertEqual(manifest["package_count"], 3)
        self.assertEqual(manifest["component_total_qty"], 15)
        self.assertEqual(
            {row["category"] for row in manifest["components"]},
            {"قطاعات وديكورات", "مكونات مع الباب", "بقية مكونات التوريد"},
        )

    def test_manifest_omits_only_inactive_excluded_and_zero_packages(self):
        manifest = build_delivery_supply_manifest(
            {
                "name": "MREQ-TEST",
                "items": [],
                "custom_delivery_component_packages": [
                    {"component": "نشط", "package_qty": 1, "active": 1},
                    {"component": "معطل", "package_qty": 1, "active": 0},
                    {"component": "مستبعد", "package_qty": 1, "tracking_route": "مستبعد"},
                    {"component": "مستبعد من التوريد", "package_qty": 1, "exclude_from_delivery": 1},
                    {"component": "صفر", "package_qty": 0, "active": 1},
                ],
            }
        )

        self.assertEqual([row["component"] for row in manifest["components"]], ["نشط"])

    def test_manifest_groups_packages_by_component_color_and_store_item(self):
        manifest = build_delivery_supply_manifest(
            {
                "name": "MREQ-TEST",
                "items": [],
                "custom_delivery_component_packages": [
                    {
                        "component": "برواز 8X2.6",
                        "color": "أبيض",
                        "item_code": "FRAME-W",
                        "package_qty": 6,
                        "package_label": "كرتون",
                    },
                    {
                        "component": "برواز 8X2.6",
                        "color": "أبيض",
                        "item_code": "FRAME-W",
                        "package_qty": 2,
                        "package_label": "كرتون ناقص",
                    },
                    {
                        "component": "برواز 8X2.6",
                        "color": "تك",
                        "item_code": "FRAME-T",
                        "package_qty": 3,
                        "package_label": "كرتون ناقص",
                    },
                ],
            }
        )

        self.assertEqual(len(manifest["components"]), 2)
        white = next(row for row in manifest["components"] if row["color"] == "أبيض")
        self.assertEqual(white["package_count"], 2)
        self.assertEqual(white["total_qty"], 8)
        self.assertEqual(white["package_label"], "كرتون، كرتون ناقص")


if __name__ == "__main__":
    unittest.main()
