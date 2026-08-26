from __future__ import annotations

import unittest
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]


class DeliveryComponentUiLabelsTestCase(unittest.TestCase):
    def test_manufacturing_dashboard_uses_manufacturing_labels(self):
        source = (
            APP_ROOT / "public/js/delivery_components/material_request_delivery_components.js"
        ).read_text(encoding="utf-8")
        self.assertIn("function sectorPackageIsManufactured", source)
        self.assertIn('statusText = isCompleted ? "تم تصنيعه" : "متبقي"', source)
        self.assertIn("تصنيع القطاعات", source)
        self.assertIn("interface_version: 5", source)

    def test_dashboard_renders_again_after_frappe_requests_finish(self):
        source = (
            APP_ROOT / "public/js/delivery_components/material_request_delivery_components.js"
        ).read_text(encoding="utf-8")

        self.assertIn("function scheduleDashboardRenderAfterRequests", source)
        self.assertIn('typeof frappe.after_ajax === "function"', source)
        self.assertIn("frappe.after_ajax(renderAfterModelSettles);", source)
        self.assertIn("scheduleDashboardRenderAfterRequests(frm);", source)

    def test_sector_dashboard_splits_pending_and_completed_packages(self):
        source = (
            APP_ROOT / "public/js/delivery_components/material_request_delivery_components.js"
        ).read_text(encoding="utf-8")
        unified_dashboard = source.split("function renderUnifiedDashboard", 1)[1].split(
            "function refreshFulfillment", 1
        )[0]

        self.assertIn("var packageRows = rows(frm).filter(isBarcodeRoute);", unified_dashboard)
        self.assertIn("completedPackageRows", unified_dashboard)
        self.assertIn("pendingPackageRows", unified_dashboard)
        self.assertIn(
            'renderSectorManufacturingTable("المتبقي للتصنيع"', unified_dashboard
        )
        self.assertIn('renderSectorManufacturingTable("تم تصنيعه"', unified_dashboard)
        self.assertIn('statCard("المتبقي"', unified_dashboard)
        self.assertIn('statCard("تم تصنيعه"', unified_dashboard)
        self.assertIn('statCard("الإجمالي"', unified_dashboard)
        self.assertIn("نسبة الإنجاز", unified_dashboard)
        self.assertNotIn("مكونات توريد فقط", unified_dashboard)
        self.assertNotIn("مكونات مع الباب", unified_dashboard)

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

    def test_snapshot_backfill_is_test_only_and_admin_only(self):
        api_source = (APP_ROOT / "delivery_components/api.py").read_text(encoding="utf-8")
        maintenance_source = (APP_ROOT / "delivery_components/maintenance.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("backfill_component_package_event_snapshots", api_source)
        self.assertIn('frappe.only_for("System Manager")', api_source)
        self.assertIn('TEST_APP = "namar_test"', maintenance_source)
        self.assertIn("update_modified=False", maintenance_source)

    def test_unified_dashboard_keeps_sector_print_button(self):
        source = (
            APP_ROOT / "public/js/delivery_components/material_request_delivery_components.js"
        ).read_text(encoding="utf-8")
        unified_dashboard = source.split("function renderUnifiedDashboard", 1)[1].split(
            "function refreshFulfillment", 1
        )[0]

        self.assertIn("delivery-component-print-btn", unified_dashboard)
        self.assertIn("طباعة باركود القطاعات", unified_dashboard)


if __name__ == "__main__":
    unittest.main()
