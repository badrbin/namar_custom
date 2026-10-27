"""Behavior and migration parity for the live cutting-results report.

All records below are synthetic. The reference hash binds the preserved body
to the reviewed live Server Script without storing customer records in Git.
"""
from copy import deepcopy
import hashlib
import importlib.util
import inspect
import json
from pathlib import Path
import sys
import textwrap
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "namar_kdt/namar_kdt/report/نتائج_التخصيم/نتائج_التخصيم.py"
LIVE_BODY_SHA256 = "d7926cecbd037b7891b5781f8474b3e9dcdac82431d97945f17e29b1acb06f6e"


def flt(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def source_row(name="ROW-1", request="MR-TEST-1", **values):
    return {
        "طلب_المواد": request, "row_name": name, "row_idx": 1,
        "التاريخ": "2026-01-01", "الحالة": "Ready",
        "كود_الصنف": "W-DOOR-1", "اسم_الصنف": "Synthetic white door",
        "الكمية": 2, "width_input": 100, "height_input": 210,
        "wall_width_input": 12, "leaf_count_input": 1,
        **values,
    }


def template(**values):
    return {
        "parent": "SYNTHETIC-FLAT", "leaf_w": -8, "leaf_h": -5,
        "u_w": 0, "u_h": 0, "panel_w": -8, "panel_h": -5,
        "custom_show_leaf_result": 1, "custom_show_u_result": 0,
        "custom_show_panel_result": 1, "custom_u_fallback": "none",
        "custom_panel_fallback": "leaf", "default_leaf_count": 1,
        **values,
    }


def component_range(component="بانل", **values):
    return {
        "component": component, "item": "SYNTHETIC-RAW",
        "qty": 1, "custom_qty_calculation": "ثابت",
        "custom_net_leaf_behavior": "يظهر", **values,
    }


class FakeDB:
    def __init__(self, rows=None, cutting=None, ranges=None):
        self.rows = deepcopy(rows if rows is not None else [source_row()])
        self.cutting = deepcopy(cutting if cutting is not None else [template()])
        self.ranges = deepcopy(ranges if ranges is not None else [component_range()])
        self.component_meta = [
            {"component_name": "بانل", "label_ar": "بنل", "custom_has_color": 1},
            {"component_name": "مفصلات", "label_ar": "مفصلات", "custom_has_color": 0},
        ]
        self.exclusion_groups = {}
        self.queries = []

    @staticmethod
    def escape(value):
        return "'" + str(value).replace("'", "''") + "'"

    def get_value(self, doctype, name, field):
        if doctype == "Item" and field == "item_group":
            return "Synthetic Doors"
        if doctype == "Store Component Exclusion Group" and field == "is_active":
            return int(name in self.exclusion_groups)
        raise AssertionError((doctype, name, field))

    def sql(self, query, values=None, as_dict=False):
        self.queries.append((query, deepcopy(values)))
        self.assert_read_query(query)
        if "FROM `tabMaterial Request Item` mri" in query:
            if "1 = 0" in query:
                return []
            rows = self.rows
            if "IFNULL(mri.custom_is_cancelled_row, 0) = 0" in query:
                rows = [row for row in rows if not row.get("cancelled")]
            return deepcopy(rows)
        if "FROM `tabStore Component`" in query:
            return deepcopy(self.component_meta)
        if "FROM `tabWorkflow State`" in query:
            return [{"name": "Ready", "style": "Success"}]
        if "FROM `tabCutting Template Item`" in query:
            return deepcopy(self.cutting)
        if "FROM `tabCutting Wall Range`" in query:
            return deepcopy(self.ranges)
        if "FROM `tabMaterial Request Item Excluded Store Component`" in query:
            return [{"store_component": value} for value in self.exclusion_groups.get(values, [])]
        raise AssertionError("Unexpected database query: " + query)

    @staticmethod
    def assert_read_query(query):
        if not query.split(None, 1)[0].upper() == "SELECT":
            raise AssertionError("Report attempted a database mutation")


def load_report(db):
    frappe = ModuleType("frappe")
    frappe.db = db
    frappe.utils = SimpleNamespace(flt=flt)
    frappe.get_meta = lambda _doctype: SimpleNamespace(has_field=lambda name: name == "custom_is_cancelled_row")
    spec = importlib.util.spec_from_file_location("kdt_report_under_test", REPORT)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"frappe": frappe}):
        spec.loader.exec_module(module)
    return module, frappe


class ReportTests(unittest.TestCase):
    def run_report(self, db=None, filters=None):
        db = db or FakeDB()
        module, frappe = load_report(db)
        filters = {"material_requests": ["MR-TEST-1"]} if filters is None else filters
        result = module.execute(deepcopy(filters))
        # Same saved script executed with its historical globals. This catches
        # scope/default-argument regressions introduced by moving into execute.
        legacy_body = textwrap.dedent(inspect.getsource(module.execute).split("\n", 1)[1])
        legacy_body = legacy_body.rsplit("\nreturn data\n", 1)[0]
        legacy_db = deepcopy(db)
        legacy_db.queries = []
        _, legacy_frappe = load_report(legacy_db)
        namespace = {"frappe": legacy_frappe, "json": json, "filters": deepcopy(filters)}
        exec(compile(legacy_body, "approved_live_report", "exec"), namespace)
        self.assertEqual(result, namespace["data"])
        # Wrapping the original body indents SQL string lines by four spaces;
        # SQL tokens and bound arguments must remain identical.
        normalize = lambda queries: [(" ".join(sql.split()), values) for sql, values in queries]
        self.assertEqual(normalize(db.queries), normalize(legacy_db.queries))
        return result

    @staticmethod
    def details(result):
        return [row for row in result[1] if row.get("_kdt_material_request_item")]

    @staticmethod
    def summaries(result):
        return [row for row in result[1] if row.get("_kdt_component_key")]

    def test_preserved_body_matches_approved_live_reference(self):
        module, _ = load_report(FakeDB())
        body = textwrap.dedent(inspect.getsource(module.execute).split("\n", 1)[1])
        body = body.rsplit("\nreturn data\n", 1)[0]
        self.assertEqual(hashlib.sha256(body.encode()).hexdigest(), LIVE_BODY_SHA256)

    def test_empty_filters_return_prompt_without_requests(self):
        db = FakeDB()
        result = self.run_report(db, {})
        self.assertEqual(result[1], [])
        self.assertIn("اختر فلترًا", result[2])
        self.assertIn("1 = 0", db.queries[0][0])

    def test_normal_dimensions_quantity_and_source_identity(self):
        result = self.run_report()
        row = self.details(result)[0]
        self.assertEqual((row["leaf_w"], row["leaf_h"], row["panel_w"], row["panel_h"]),
                         ("92", "205", "92", "205"))
        self.assertEqual((row["u_w"], row["u_h"]), ("-", "-"))
        summary = self.summaries(result)[0]
        self.assertEqual(summary["الكمية"], 2)
        self.assertEqual(json.loads(summary["_kdt_component_key"]), ["بانل", "أبيض", "SYNTHETIC-RAW"])
        self.assertEqual(summary["_kdt_sources"], [{
            "material_request": "MR-TEST-1", "source_row": "ROW-1", "component": "بانل",
            "raw_item": "SYNTHETIC-RAW", "color": "أبيض", "quantity": 2.0,
            "part_nos": [], "part_count": 1,
        }])

    def test_net_leaf_zero_panel_uses_leaf_fallback(self):
        result = self.run_report(FakeDB(rows=[source_row(net_leaf_input=1)], cutting=[template(
            net_leaf_w_deduction=-3, net_leaf_h_deduction=-2,
            net_leaf_panel_w=0, net_leaf_panel_h=0,
        )]))
        row = self.details(result)[0]
        self.assertEqual((row["panel_w"], row["panel_h"]), ("97", "208"))
        self.assertEqual(self.summaries(result)[0]["الكمية"], 2)

    def test_fixed_two_leaf_split_keeps_exact_part_scope(self):
        result = self.run_report(FakeDB(
            rows=[source_row(leaf_count_input=2, split_type_input="ثابت", fixed_leaf_width_input=30)],
            ranges=[component_range(qty=2, custom_qty_calculation="لكل جزء")],
        ))
        row = self.details(result)[0]
        self.assertEqual(row["leaf_w"], "30 + 62")
        self.assertEqual(row["panel_w"], "30 + 62")
        source = self.summaries(result)[0]["_kdt_sources"][0]
        self.assertEqual((source["quantity"], source["part_nos"], source["part_count"]), (8.0, [1, 2], 2))

    def test_aggregation_preserves_each_request_source(self):
        result = self.run_report(FakeDB(rows=[source_row(), source_row("ROW-2", "MR-TEST-2", الكمية=3)]))
        summary = self.summaries(result)[0]
        self.assertEqual(summary["الكمية"], 5)
        self.assertEqual([(source["source_row"], source["quantity"]) for source in summary["_kdt_sources"]],
                         [("ROW-1", 2.0), ("ROW-2", 3.0)])

    def test_product_color_filter_also_scopes_colorless_components(self):
        db = FakeDB(rows=[source_row(), source_row("ROW-2", كود_الصنف="B-DOOR-2")],
                    ranges=[component_range("مفصلات", qty=3)])
        result = self.run_report(db, {"colors": ["أبيض"]})
        self.assertEqual(len(self.details(result)), 1)
        summary = self.summaries(result)[0]
        self.assertEqual(summary["الكمية"], 6)
        self.assertEqual(json.loads(summary["_kdt_component_key"])[1], "")
        self.assertEqual(summary["_kdt_sources"][0]["source_row"], "ROW-1")

    def test_checkbox_exclusion_removes_component_and_identity(self):
        db = FakeDB(rows=[source_row(exception_component_1_input=1)],
                    ranges=[component_range(), component_range("مفصلات", qty=3)])
        db.exclusion_groups["exception component 1"] = ["بانل"]
        result = self.run_report(db)
        self.assertEqual([row["_kdt_component_name"] for row in self.summaries(result)], ["مفصلات"])
        self.assertEqual(self.details(result)[0]["_kdt_component_names"], "مفصلات")

    def test_cancelled_row_never_contributes_to_cutting(self):
        db = FakeDB(rows=[source_row(), source_row("ROW-CANCELLED", cancelled=True, الكمية=99)])
        result = self.run_report(db)
        self.assertEqual(self.summaries(result)[0]["الكمية"], 2)
        self.assertIn("IFNULL(mri.custom_is_cancelled_row, 0) = 0", db.queries[0][0])

    def test_formula_quantity_uses_configured_panel_dimensions(self):
        result = self.run_report(FakeDB(ranges=[component_range(
            custom_qty_calculation="بالمعادلة على الإجمالي", custom_size_reference="Panel",
            custom_qty_formula="W * H / 10000",
        )]))
        # 92 × 205 cm / 10,000 × two doors; identities retain unrounded amount.
        self.assertEqual(self.summaries(result)[0]["الكمية"], 3.772)
        self.assertEqual(self.summaries(result)[0]["_kdt_sources"][0]["quantity"], 3.772)

    def test_existing_saved_display_text_is_retained(self):
        result = self.run_report(FakeDB(rows=[source_row(leaf_w_text_input="40 + 52", panel_w_text_input="40 + 52")]))
        row = self.details(result)[0]
        self.assertEqual((row["leaf_w"], row["panel_w"]), ("40 + 52", "40 + 52"))

    def test_request_caches_do_not_survive_template_change(self):
        db = FakeDB()
        report, _ = load_report(db)
        filters = {"material_requests": ["MR-TEST-1"]}
        self.assertEqual(self.details(report.execute(filters))[0]["leaf_w"], "92")
        db.cutting[0]["leaf_w"] = -4
        self.assertEqual(self.details(report.execute(filters))[0]["leaf_w"], "96")


if __name__ == "__main__":
    unittest.main()
