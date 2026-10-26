"""Exercise production manufacturing summaries and migration without a bench."""
from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
MR = "MREQ-DOOR-TEST"


class ValidationError(Exception):
    pass


def request(name=MR, **values):
    row = {
        "name": name, "docstatus": 1, "workflow_state": "اعتماد التوريد",
        "custom_manufacturing_status": "قيد التصنيع",
        "custom_manufacturing_total_count": 9,
        "custom_manufacturing_remaining_count": 0,
        "custom_manufacturing_completed_at": None,
        "custom_manufacturing_completed_by": "",
        "custom_fulfillment_ready": 0,
        "modified": "2026-10-24 12:00:00",
    }
    row.update(values)
    return row


def package(ready_qty=0):
    return {
        "name": "FRAME-PACKAGE", "parent": MR,
        "tracking_route": "تصنيع مستقل - باركود",
        "active": 1, "package_qty": 16, "ready_qty": ready_qty,
        "tracking_status": "غير جاهز",
    }


class MemoryDatabase:
    """Only the database operations used by the actual summary/backfill paths."""

    def __init__(self):
        self.rows = {MR: request()}
        self.writes = []
        self.locks = []
        self.commits = 0
        self.rollbacks = 0
        self.transaction_before = None

    def sql(self, query, values, as_dict=False):
        if "FROM `tabMaterial Request`" not in query or "FOR UPDATE" not in query:
            raise AssertionError("Backfill must lock the current request before writing")
        if self.transaction_before is None:
            self.transaction_before = deepcopy(self.rows)
        name = values[0]
        self.locks.append(name)
        return [deepcopy(self.rows[name])] if name in self.rows else []

    def set_value(self, doctype, name, values, *, update_modified):
        if doctype != "Material Request" or update_modified:
            raise AssertionError("Unexpected write outside manufacturing header reconciliation")
        self.writes.append((name, deepcopy(values)))
        self.rows[name].update(deepcopy(values))

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1
        if self.transaction_before is not None:
            self.rows = deepcopy(self.transaction_before)


def load_modules():
    db = MemoryDatabase()
    frappe = ModuleType("frappe")
    frappe.db = db
    frappe.session = SimpleNamespace(user="reviewer@example.test")
    frappe.local = SimpleNamespace(site="production.example")
    frappe.whitelist = lambda **kwargs: lambda function: function
    frappe.only_for = Mock()
    frappe.get_installed_apps = lambda: ["namar_custom"]
    frappe.parse_json = json.loads
    frappe.get_meta = lambda doctype: SimpleNamespace(has_field=lambda fieldname: True)
    frappe.get_doc = Mock(side_effect=lambda doctype, name: deepcopy(db.rows[name]))

    def get_all(doctype, *, filters, fields, order_by, limit_page_length):
        if doctype != "Material Request" or order_by != "name asc":
            raise AssertionError("Unexpected backfill query")
        if filters["docstatus"] != ["!=", 2]:
            raise AssertionError("Cancelled requests must not enter the preview")
        cursor = filters["name"][1]
        return [
            {field: deepcopy(row.get(field)) for field in fields}
            for name, row in sorted(db.rows.items())
            if name > cursor and row["docstatus"] != 2
        ][:limit_page_length]

    frappe.get_all = Mock(side_effect=get_all)

    def throw(message, error=None, **kwargs):
        raise (error or ValidationError)(message)

    frappe.throw = throw
    utils = ModuleType("frappe.utils")
    utils.cint = lambda value=0: int(float(value or 0))
    utils.flt = lambda value=0: float(value or 0)
    utils.now_datetime = Mock(return_value="2026-10-26 15:00:00")
    frappe.utils = utils
    tracking = ModuleType("namar_custom.delivery_components.tracking_codes")
    tracking.ensure_material_request_tracking_code = Mock(return_value="ABC")
    modules = []
    for basename in ("service", "door_status_backfill"):
        path = ROOT / "namar_custom/delivery_components" / (basename + ".py")
        name = "_door_manufacturing_%s_%s" % (basename, id(db))
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {
            "frappe": frappe, "frappe.utils": utils,
            "namar_custom.delivery_components.tracking_codes": tracking,
        }):
            spec.loader.exec_module(module)
        modules.append(module)
    return modules[0], modules[1], frappe, db


class ManufacturingSummaryTests(unittest.TestCase):
    def setUp(self):
        self.service, self.backfill, self.frappe, self.db = load_modules()

    def summary(self, *, ready_qty=0, needs_sync=False):
        context = {
            "needs_sync": needs_sync, "current_hash": "current-source",
            "has_expected_barcode_components": True,
        }
        with patch.object(self.service, "package_sync_context", return_value=context):
            return self.service.update_material_request_summary(MR, [package(ready_qty)])

    def test_finished_doors_are_filterable_as_complete_while_frames_are_unfinished(self):
        result = self.summary()
        self.assertEqual(result["manufacturing_status"], "مصنع بالكامل")
        self.assertEqual(self.db.rows[MR]["custom_manufacturing_status"], "مصنع بالكامل")
        self.assertEqual(result["overall"]["door_ready"], 9)
        self.assertEqual(result["overall"]["door_total"], 9)
        self.assertEqual(result["overall"]["status"], "جاهزية جزئية")
        self.assertFalse(result["overall"]["is_ready"])
        self.assertEqual(self.db.rows[MR]["custom_fulfillment_ready"], 0)

    def test_resync_and_reopened_packages_do_not_reverse_completed_door_status(self):
        for ready_qty, needs_sync, is_ready in (
            (16, False, True), (16, True, False), (0, False, False),
        ):
            with self.subTest(ready_qty=ready_qty, needs_sync=needs_sync):
                result = self.summary(ready_qty=ready_qty, needs_sync=needs_sync)
                self.assertEqual(result["manufacturing_status"], "مصنع بالكامل")
                self.assertEqual(self.db.rows[MR]["custom_manufacturing_status"], "مصنع بالكامل")
                self.assertEqual(result["overall"]["is_ready"], is_ready)
                self.assertEqual(bool(self.db.rows[MR]["custom_fulfillment_ready"]), is_ready)
                if needs_sync:
                    self.assertEqual(result["overall"]["status"], "غير جاهز")
                    self.assertIn("الحزم تحتاج تحديث", result["overall"]["summary"])

    def test_refresh_does_not_attribute_older_door_completion_to_current_user(self):
        result = self.summary(ready_qty=16, needs_sync=True)
        self.assertIsNone(result["manufacturing_completed_at"])
        self.assertEqual(result["manufacturing_completed_by"], "")
        self.assertIsNone(self.db.rows[MR]["custom_manufacturing_completed_at"])
        self.assertEqual(self.db.rows[MR]["custom_manufacturing_completed_by"], "")
        self.frappe.utils.now_datetime.assert_not_called()
        self.assertEqual(self.db.commits, 0)

    def test_existing_scanner_completion_metadata_is_kept_with_unfinished_packages(self):
        self.db.rows[MR].update(
            custom_manufacturing_completed_at="2026-10-20 09:15:00",
            custom_manufacturing_completed_by="scanner@example.test",
        )
        result = self.summary()
        self.assertEqual(result["manufacturing_completed_at"], "2026-10-20 09:15:00")
        self.assertEqual(result["manufacturing_completed_by"], "scanner@example.test")
        self.frappe.utils.now_datetime.assert_not_called()

    def test_ready_packages_do_not_start_or_finish_pending_doors(self):
        for remaining, expected in ((9, "غير مصنع"), (5, "قيد التصنيع")):
            with self.subTest(remaining=remaining):
                self.db.rows[MR].update(custom_manufacturing_remaining_count=remaining)
                result = self.summary(ready_qty=16)
                self.assertEqual(result["manufacturing_status"], expected)
                self.assertEqual(self.db.rows[MR]["custom_manufacturing_status"], expected)
                self.assertFalse(result["overall"]["is_ready"])
                self.assertEqual(self.db.rows[MR]["custom_manufacturing_total_count"], 9)
                self.assertEqual(self.db.rows[MR]["custom_manufacturing_remaining_count"], remaining)

    def test_package_only_readiness_is_independent_of_door_manufacturing(self):
        self.db.rows[MR].update(custom_manufacturing_total_count=0,
                                custom_manufacturing_remaining_count=0)
        result = self.summary(ready_qty=16)
        self.assertEqual(result["manufacturing_status"], "غير مصنع")
        self.assertEqual(self.db.rows[MR]["custom_manufacturing_status"], "غير مصنع")
        self.assertTrue(result["overall"]["is_ready"])
        self.assertEqual(result["overall"]["door_total"], 0)


class DoorStatusBackfillTests(unittest.TestCase):
    def setUp(self):
        self.service, self.backfill, self.frappe, self.db = load_modules()

    def test_preview_has_no_writes_and_proposes_only_the_filter_status(self):
        before = deepcopy(self.db.rows)
        plan = self.backfill.preview()
        self.assertEqual(plan["scanned"], 1)
        self.assertEqual(plan["next_cursor"], MR)
        self.assertEqual(plan["review"], [])
        self.assertEqual(plan["changes"][0]["after"], {"custom_manufacturing_status": "مصنع بالكامل"})
        self.assertEqual(plan["changes"][0]["before"]["custom_manufacturing_total_count"], 9)
        self.assertEqual(plan["changes"][0]["before"]["custom_manufacturing_remaining_count"], 0)
        self.assertEqual(self.db.rows, before)
        self.assertEqual(self.db.writes, [])
        self.assertEqual(self.db.locks, [])
        self.assertEqual(self.db.commits, 0)

    def test_apply_preserves_counters_metadata_and_workflow_and_does_not_commit(self):
        plan = self.backfill.preview()
        before = deepcopy(self.db.rows[MR])
        result = self.backfill.apply(plan)
        self.assertEqual(result["updated"], 1)
        self.assertEqual(self.db.rows[MR], {**before, "custom_manufacturing_status": "مصنع بالكامل"})
        self.assertEqual(self.db.writes, [(MR, {"custom_manufacturing_status": "مصنع بالكامل"})])
        self.assertEqual(self.db.locks, [MR])
        self.assertEqual(self.db.commits, 0)
        self.assertEqual(self.db.rollbacks, 0)

    def test_changed_counter_or_cancellation_invalidates_reviewed_plan(self):
        for change in (
            {"custom_manufacturing_total_count": 10},
            {"custom_manufacturing_remaining_count": 1},
            {"docstatus": 2},
        ):
            with self.subTest(change=change):
                _service, backfill, _frappe, db = load_modules()
                plan = backfill.preview()
                db.rows[MR].update(change)
                with self.assertRaisesRegex(ValidationError, "تغير الطلب"):
                    backfill.apply(plan)
                self.assertEqual(db.writes, [])
                self.assertEqual(db.rollbacks, 1)

    def test_plan_cannot_smuggle_other_fields_into_apply(self):
        plan = self.backfill.preview()
        plan["changes"][0]["after"]["workflow_state"] = "مكتمل"
        with self.assertRaisesRegex(ValidationError, "تغير الطلب"):
            self.backfill.apply(plan)
        self.assertEqual(self.db.writes, [])
        self.assertEqual(self.db.rows[MR]["workflow_state"], "اعتماد التوريد")

    def test_invalid_counters_are_reported_without_guessing_or_writing(self):
        invalid = (
            (None, 0), (9, None), ("not-a-number", 0),
            (float("inf"), 0), (9, float("nan")), (-1, 0), (9, -1), (9, 10),
        )
        self.db.rows = {
            "MREQ-%05d" % index: request(
                "MREQ-%05d" % index,
                custom_manufacturing_total_count=total,
                custom_manufacturing_remaining_count=remaining,
            )
            for index, (total, remaining) in enumerate(invalid)
        }
        plan = self.backfill.preview()
        self.assertEqual(plan["changes"], [])
        self.assertEqual({entry["name"] for entry in plan["review"]}, set(self.db.rows))
        self.assertEqual(self.db.writes, [])
        self.assertEqual(self.db.commits, 0)

    def test_counter_becoming_invalid_after_preview_is_rejected(self):
        plan = self.backfill.preview()
        self.db.rows[MR]["custom_manufacturing_remaining_count"] = None
        with self.assertRaisesRegex(ValidationError, "تغير الطلب"):
            self.backfill.apply(plan)
        self.assertEqual(self.db.writes, [])

    def test_later_conflict_rolls_back_earlier_changes_in_the_batch(self):
        second = MR + "-NEXT"
        self.db.rows[second] = request(second)
        plan = self.backfill.preview()
        self.db.rows[second]["custom_manufacturing_remaining_count"] = 1
        before = deepcopy(self.db.rows)
        with self.assertRaisesRegex(ValidationError, "تغير الطلب"):
            self.backfill.apply(plan)
        self.assertEqual(self.db.locks, [MR, second])
        self.assertEqual(len(self.db.writes), 1)
        self.assertEqual(self.db.rows, before)
        self.assertEqual(self.db.rollbacks, 1)
        self.assertEqual(self.db.commits, 0)

    def test_cancelled_and_unchanged_requests_do_not_enter_the_plan(self):
        self.db.rows["MREQ-CANCELLED"] = request("MREQ-CANCELLED", docstatus=2)
        self.db.rows[MR]["custom_manufacturing_status"] = "مصنع بالكامل"
        plan = self.backfill.preview()
        self.assertEqual(plan["scanned"], 1)
        self.assertEqual(plan["changes"], [])
        self.assertEqual(plan["review"], [])


if __name__ == "__main__":
    unittest.main()
