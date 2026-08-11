from __future__ import annotations

import unittest
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]


class DeliveryComponentUiLabelsTestCase(unittest.TestCase):
    def test_manufacturing_dashboard_uses_manufacturing_labels(self):
        source = (
            APP_ROOT / "public/js/delivery_components/material_request_delivery_components.js"
        ).read_text(encoding="utf-8")
        self.assertIn('registered ? "تم التصنيع" : "غير مصنع"', source)
        self.assertIn("حزم المكونات المصنعة", source)
        self.assertIn("تصنيع حزم المكونات", source)
        self.assertIn("interface_version: 3", source)

    def test_sector_print_uses_the_dedicated_format(self):
        source = (
            APP_ROOT / "public/js/delivery_components/material_request_delivery_components.js"
        ).read_text(encoding="utf-8")
        self.assertIn("ملصق قطاعات التصنيع 4x3", source)
        self.assertIn("طباعة باركود القطاعات", source)
        self.assertNotIn("طباعة باركود المكونات", source)


if __name__ == "__main__":
    unittest.main()
