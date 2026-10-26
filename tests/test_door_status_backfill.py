from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from test_delivery_component_cancellation_race import MR, ValidationError, load_service


class DoorStatusBackfillTests(unittest.TestCase):
    def setUp(self):
        service, self.frappe, self.db = load_service()
        self.addCleanup(self.db.finish)
        self.frappe.whitelist = lambda **kwargs: lambda fn: fn
        self.frappe.only_for = Mock()
        self.frappe.local = SimpleNamespace(site="test.example")
        self.frappe.get_installed_apps = lambda: ["namar_test"]
        self.frappe.get_all.return_value = [MR]
        self.frappe.get_all.side_effect = None
        self.db.rollback = Mock()
        path = Path(__file__).resolve().parents[1] / "namar_test/delivery_components/door_status_backfill.py"
        spec = importlib.util.spec_from_file_location("_backfill_test", path)
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {
            "frappe": self.frappe, "frappe.utils": self.frappe.utils,
            "namar_test.delivery_components.service": service,
        }):
            spec.loader.exec_module(self.module)

    def test_preview_is_read_only_and_describes_exact_header_changes(self):
        plan = self.module.preview()
        self.assertEqual(plan["scanned"], 1)
        self.assertEqual(plan["next_cursor"], MR)
        self.assertEqual(plan["changes"][0]["after"], {
            "custom_manufacturing_status": "قيد التصنيع",
            "custom_manufacturing_total_count": 4,
            "custom_manufacturing_remaining_count": 3,
        })
        self.assertEqual(self.db.writes, [])
        self.assertEqual(self.db.commits, 0)

    def test_apply_changes_only_manufacturing_headers_without_fabricating_events(self):
        plan = self.module.preview()
        before = deepcopy(self.db.parent)
        packages = deepcopy(self.db.packages)
        result = self.module.apply(plan)
        self.assertEqual(result["updated"], 1)
        after = self.db.parent
        for key in before:
            if key not in self.module.FIELDS:
                self.assertEqual(before[key], after[key], key)
        self.assertEqual(self.db.packages, packages)
        self.assertEqual(self.db.inserted_events, [])
        self.assertEqual(set(self.db.writes[0][2]), set(self.module.FIELDS))
        self.assertEqual(self.db.commits, 0)
        self.db.rollback.assert_not_called()

    def test_scanner_change_since_preview_stops_apply(self):
        plan = self.module.preview()
        self.db.items[0].update(custom_manufactured_qty=2)
        with self.assertRaisesRegex(ValidationError, "تغير الطلب"):
            self.module.apply(plan)
        self.assertEqual(self.db.writes, [])
        self.db.rollback.assert_called_once()

    def test_cancelled_request_is_not_updated(self):
        plan = self.module.preview()
        self.db.parent.update(docstatus=2)
        with self.assertRaisesRegex(ValidationError, "تغير الطلب"):
            self.module.apply(plan)
        self.assertEqual(self.db.writes, [])

    def test_reviewed_plan_cannot_write_unrelated_fields(self):
        plan = self.module.preview()
        plan["changes"][0]["after"]["workflow_state"] = "مكتمل"
        with self.assertRaisesRegex(ValidationError, "تغير الطلب"):
            self.module.apply(plan)
        self.assertEqual(self.db.writes, [])

    def test_wrong_site_duplicate_requests_and_non_test_app_are_rejected(self):
        plan = self.module.preview()
        with self.assertRaises(ValidationError):
            self.module.apply({**plan, "site": "other.example"})
        with self.assertRaises(ValidationError):
            self.module.apply({**plan, "changes": plan["changes"] * 2})
        self.frappe.get_installed_apps = lambda: ["namar_custom"]
        with self.assertRaisesRegex(ValidationError, "للتجريبي"):
            self.module.apply(plan)
        self.assertEqual(self.db.writes, [])


if __name__ == "__main__":
    unittest.main()
