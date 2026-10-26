"""Regression/parity tests for the TEST cutting engine, using synthetic records.

The legacy source is executed only here against a SELECT-only fake database.
No Frappe installation, business records, network access, or external dependency
is required. Expected domain assertions accompany comparison with the legacy
result so the suite does not merely compare two copies of an implementation.
"""

from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
LEGACY_SOURCE = (ROOT / "tests/fixtures/legacy_cutting_script.py").read_text()
LEGACY_CODE = compile(LEGACY_SOURCE, "legacy_cutting_script.py", "exec")


class Row(dict):
    """The attribute behavior used by frappe._dict query rows."""

    __getattr__ = dict.get


def flt(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def template(**changes):
    row = Row(
        parent="SYNTHETIC-TEMPLATE",
        item_code="DOOR-A",
        item_group="Doors",
        sliding_type="",
        leaf_w=-4,
        leaf_h=-5,
        u_w=-2,
        u_h=-3,
        panel_w=-6,
        panel_h=-7,
        type="Synthetic",
        max_width=300,
        max_height=400,
        default_leaf_count="1",
        default_split_type="",
        fixed_leaf_width=0,
        parquet_deduction=0,
        net_leaf_w_deduction=-1,
        net_leaf_h_deduction=-2,
        net_leaf_panel_w=-3,
        net_leaf_panel_h=-4,
        net_leaf_u_w=-5,
        net_leaf_u_h=-6,
    )
    row.update(changes)
    return row


def material_range(component="Leaf", item="MAT-LEAF", **changes):
    row = Row(
        parent="SYNTHETIC-TEMPLATE",
        component=component,
        item=item,
        item_code="",
        item_group="",
        sliding_type="",
        width_from=0,
        width_to=0,
        height_from=0,
        height_to=0,
        wall_width_from=0,
        wall_width_to=0,
        qty=2,
        per_leaf=0,
        taksiya_1=0,
        taksiya_2=0,
        no_qitaat=0,
    )
    row.update(changes)
    return row


class FakeDB:
    """Only the known read queries are implemented; all other SQL is rejected."""

    def __init__(self, templates=None, ranges=None, component_groups=None, exclusions=None):
        self.templates = deepcopy([template()] if templates is None else templates)
        self.ranges = deepcopy([material_range()] if ranges is None else ranges)
        self.component_groups = component_groups or {}
        self.exclusions = exclusions or {}
        self.calls = []

    def get_value(self, doctype, name, field):
        self.calls.append(("get_value", doctype, name, field))
        if doctype == "Item" and field == "item_group":
            return "Doors"
        if doctype == "Item" and field == "item_name":
            return "Synthetic " + name
        if doctype == "Store Component Exclusion Group" and field == "is_active":
            return self.exclusions.get(name, {}).get("active", 0)
        raise AssertionError((doctype, name, field))

    def sql(self, query, values=None, as_dict=False):
        normalized = " ".join(query.split())
        self.calls.append(("sql", normalized, values))
        assert as_dict is True
        assert normalized.startswith("SELECT "), "The engine must remain read-only"
        if "FROM `tabCutting Template Item`" in normalized:
            assert "IFNULL(ct.disabled, 0) = 0" in normalized
            field = "item_code" if "WHERE cti.item_code" in normalized else "item_group"
            return deepcopy([
                row for row in self.templates
                if row.get(field) == values and not row.get("_disabled")
            ])
        if "FROM `tabStore Component`" in normalized:
            return [Row(component_name=name, label_ar="Label " + name,
                        custom_cutting_option_group=self.component_groups.get(name, ""))
                    for name in dict.fromkeys(row.component for row in self.ranges)]
        if "FROM `tabCutting Wall Range`" in normalized:
            return deepcopy([row for row in self.ranges if row.parent == values])
        if "FROM `tabMaterial Request Item Excluded Store Component`" in normalized:
            return [Row(store_component=name)
                    for name in self.exclusions.get(values, {}).get("components", [])]
        raise AssertionError("Unexpected query: " + normalized)


def fake_frappe(db, params):
    return SimpleNamespace(
        db=db,
        form_dict=deepcopy(params),
        response={},
        utils=SimpleNamespace(flt=flt, cint=lambda value: int(flt(value))),
    )


def load_engine():
    spec = importlib.util.spec_from_file_location("demo_cutting_under_test", ROOT / "demo/cutting.py")
    engine = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"frappe": ModuleType("frappe")}):
        spec.loader.exec_module(engine)
    return engine


ENGINE = load_engine()


class CuttingEngineTests(unittest.TestCase):
    def calculate(self, params=None, **db_data):
        payload = {"item_codes": "DOOR-A", "widths": "100", "heights": "200", "wall_widths": "15"}
        if params is not None:
            payload.update(params)
        before = deepcopy(payload)
        legacy_frappe = fake_frappe(FakeDB(**db_data), payload)
        # This test-only execution has no live Frappe, DB, or network capability.
        exec(LEGACY_CODE, {"frappe": legacy_frappe})
        new_frappe = fake_frappe(FakeDB(**db_data), payload)
        with patch.object(ENGINE, "frappe", new_frappe):
            result = ENGINE.calculate(payload)
        self.assertEqual(result, legacy_frappe.response["message"])
        self.assertEqual(new_frappe.db.calls, legacy_frappe.db.calls)
        self.assertEqual(payload, before, "The caller's payload must not be changed")
        self.assertEqual(new_frappe.response, {}, "The library must return its result, not mutate HTTP state")
        return result, new_frappe.db

    def entry(self, params=None, **db_data):
        result, db = self.calculate(params, **db_data)
        self.assertEqual(len(result["values"]), 1)
        return next(iter(result["values"].values())), db

    def test_empty_payload_has_no_database_access(self):
        result, db = self.calculate({"item_codes": ""})
        self.assertEqual(result, {"values": {}})
        self.assertEqual(db.calls, [])

    def test_cancelled_row_is_not_queried_or_calculated(self):
        result, db = self.calculate({"cancelled_rows": "1"})
        self.assertEqual(result, {"values": {}})
        self.assertEqual(db.calls, [])

    def test_basic_dimensions_and_material_quantity(self):
        row, _ = self.entry()
        self.assertEqual((row["result_leaf_w"], row["result_leaf_h"]), (96, 195))
        self.assertEqual((row["result_u_w"], row["result_panel_w"]), (98, 94))
        self.assertEqual(row["leaf_w_text"], "96")
        self.assertEqual(row["stores"][0]["qty"], 2)

    def test_missing_and_disabled_templates_do_not_produce_results(self):
        for templates in ([], [template(_disabled=True)]):
            with self.subTest(templates=templates):
                result, _ = self.calculate(templates=templates)
                self.assertEqual(result, {"values": {}})

    def test_item_template_precedes_item_group_fallback(self):
        data = [template(item_code="", item_group="Doors", leaf_w=-10), template(leaf_w=-4)]
        row, _ = self.entry(templates=data)
        self.assertEqual(row["result_leaf_w"], 96)
        group_row, _ = self.entry({"item_codes": "DOOR-B"}, templates=data)
        self.assertEqual(group_row["result_leaf_w"], 90)

    def test_fixed_split_preserves_relative_u_and_panel_dimensions(self):
        row, _ = self.entry({"leaf_counts": "2", "split_types": "ثابت", "fixed_leaf_widths": "40"},
                            ranges=[material_range(per_leaf=1)])
        self.assertEqual(row["leaf_w_text"], "40 + 56")
        self.assertEqual(row["u_w_text"], "42 + 58")
        self.assertEqual(row["panel_w_text"], "38 + 54")
        self.assertEqual(row["stores"][0]["qty"], 4)

    def test_dimension_repeat_changes_display_without_inventing_more_material(self):
        row, _ = self.entry(templates=[template(custom_dimension_display_mode="تكرار نفس المقاس",
                                                custom_dimension_repeat_count=3)])
        self.assertEqual(row["leaf_w_text"], "96 + 96 + 96")
        self.assertEqual(row["stores"][0]["qty"], 2)

    def test_formula_per_part_and_formula_validation(self):
        row, _ = self.entry({"leaf_counts": "2"}, ranges=[material_range(
            custom_qty_calculation="بالمعادلة لكل جزء", custom_size_reference="Leaf",
            custom_qty_formula="(W + H) / 100")])
        self.assertAlmostEqual(row["stores"][0]["qty"], 4.86)
        for formula in ("W / 0", "__import__('os')", "(W + H"):
            with self.subTest(formula=formula):
                row, _ = self.entry(ranges=[material_range(custom_qty_calculation="بالمعادلة على الإجمالي",
                                                           custom_qty_formula=formula)])
                self.assertEqual(row["stores"][0]["qty"], 0)

    def test_active_component_exclusion_groups_only(self):
        row, _ = self.entry({"component_exclusion_groups": "Skip Leaf|Inactive"},
                            ranges=[material_range(), material_range("Frame", "MAT-FRAME")],
                            exclusions={"Skip Leaf": {"active": 1, "components": ["Leaf", "Leaf"]},
                                        "Inactive": {"active": 0, "components": ["Frame"]}})
        self.assertEqual(row["excluded_components"], "Leaf")
        self.assertEqual([r["component"] for r in row["stores"]], ["Frame"])

    def test_explicit_option_group_replaces_sibling_default(self):
        ranges = [material_range("Leaf A", "DEFAULT"),
                  material_range("Leaf B", "SLIDING", sliding_type="سحاب"),
                  material_range("Hinge", "HINGE")]
        row, _ = self.entry({"sliding_types": "سحاب"}, ranges=ranges,
                            component_groups={"Leaf A": "leaf", "Leaf B": "leaf"})
        self.assertEqual({r["item"] for r in row["stores"]}, {"SLIDING", "HINGE"})

    def test_other_item_sibling_option_does_not_suppress_default(self):
        ranges = [material_range("Leaf A", "DEFAULT"),
                  material_range("Leaf B", "OTHER", sliding_type="سحاب", item_code="OTHER-DOOR")]
        row, _ = self.entry({"sliding_types": "سحاب"}, ranges=ranges,
                            component_groups={"Leaf A": "leaf", "Leaf B": "leaf"})
        self.assertEqual([r["item"] for r in row["stores"]], ["DEFAULT"])

    def test_checkbox_and_dimension_specific_range_selection(self):
        ranges = [material_range(item="STANDARD"),
                  material_range(item="NO-QITAAT", no_qitaat=1, width_from=90, width_to=110)]
        ordinary, _ = self.entry(ranges=ranges)
        selected, _ = self.entry({"no_qitaats": "1"}, ranges=ranges)
        self.assertEqual(ordinary["stores"][0]["item"], "STANDARD")
        self.assertEqual(selected["stores"][0]["item"], "NO-QITAAT")

    def test_net_leaf_inclusion_is_controlled_by_range_settings(self):
        row, _ = self.entry({"net_leafs": "1"}, ranges=[
            material_range("Visible", "YES", custom_net_leaf_behavior="يظهر"),
            material_range("Hidden", "NO", custom_net_leaf_behavior="لا يظهر"),
        ])
        self.assertEqual(row["result_leaf_w"], 99)
        self.assertEqual([r["item"] for r in row["stores"]], ["YES"])

    def test_batch_preserves_key_format_and_deduplicates_identical_rows(self):
        result, _ = self.calculate({"item_codes": "DOOR-A,DOOR-A,DOOR-A,DOOR-A",
                                   "widths": "100,100,100.0,100", "heights": "200,200,200,200",
                                   "wall_widths": "15,15,15,15", "cancelled_rows": "0,0,0,1"})
        self.assertEqual(len(result["values"]), 2)
        self.assertTrue(any("||100||" in key for key in result["values"]))
        self.assertTrue(any("||100.0||" in key for key in result["values"]))


if __name__ == "__main__":
    unittest.main()
