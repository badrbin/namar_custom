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
        self.assertIn("حزم القطاعات المصنعة", source)
        self.assertIn("تصنيع القطاعات", source)
        self.assertIn("interface_version: 3", source)

    def test_blank_tracking_route_is_not_treated_as_barcode(self):
        source = (
            APP_ROOT / "public/js/delivery_components/material_request_delivery_components.js"
        ).read_text(encoding="utf-8")
        self.assertIn('return aliases[route] || route || "توريد فقط - بدون باركود";', source)

    def test_sector_print_has_a_live_server_gate(self):
        hooks = (APP_ROOT / "hooks.py").read_text(encoding="utf-8")
        helper = (APP_ROOT / "delivery_components/printing.py").read_text(encoding="utf-8")
        self.assertIn("delivery_components.printing.sector_print_status", hooks)
        self.assertIn("packages_need_sync", helper)
        self.assertIn("printable_package_ids", helper)

    def test_package_events_keep_immutable_sector_snapshots(self):
        source = (APP_ROOT / "delivery_components/service.py").read_text(encoding="utf-8")
        for fieldname in (
            "component_label",
            "color",
            "item_name",
            "package_label",
            "package_qty",
            "tracking_route",
        ):
            self.assertIn('"%s"' % fieldname, source)

    def test_sector_print_uses_the_dedicated_format(self):
        source = (
            APP_ROOT / "public/js/delivery_components/material_request_delivery_components.js"
        ).read_text(encoding="utf-8")
        self.assertIn("ملصق قطاعات التصنيع 4x3", source)
        self.assertIn("طباعة باركود القطاعات", source)
        self.assertNotIn("طباعة باركود المكونات", source)

    def test_snapshot_backfill_is_production_scoped_and_admin_only(self):
        api_source = (APP_ROOT / "delivery_components/api.py").read_text(encoding="utf-8")
        maintenance_source = (APP_ROOT / "delivery_components/maintenance.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("backfill_component_package_event_snapshots", api_source)
        self.assertIn('frappe.only_for("System Manager")', api_source)
        self.assertIn('PRODUCTION_APP = "namar_custom"', maintenance_source)
        self.assertIn("NAMAR_CUSTOM_EVENT_SNAPSHOT_BACKFILL_20260813", maintenance_source)
        self.assertNotIn("NAMAR_TEST", maintenance_source)
        self.assertIn("update_modified=False", maintenance_source)

    def test_legacy_route_adoption_is_production_scoped_and_admin_only(self):
        api_source = (APP_ROOT / "delivery_components/api.py").read_text(encoding="utf-8")
        maintenance_source = (APP_ROOT / "delivery_components/maintenance.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("adopt_legacy_started_barcode_route", api_source)
        self.assertIn('frappe.only_for("System Manager")', api_source)
        self.assertIn("NAMAR_CUSTOM_LEGACY_ROUTE_ADOPTION_20260813", maintenance_source)
        self.assertIn("مطابقة الحزمة التاريخية غير أحادية", maintenance_source)
        self.assertIn("frappe.db.set_value", maintenance_source)


if __name__ == "__main__":
    unittest.main()
