"""Native legacy-submit protection using only synthetic source rows."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import re
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from test_engine import KDTError, Row, fake_frappe


ROOT = Path(__file__).resolve().parents[1]


class GuardFixture:
    def __init__(self, source=None, *, enabled=True, fields=None, held=None):
        self.frappe = fake_frappe()
        self.source = Row(source or {
            "name": "SYNTHETIC-ROW", "parent": "SYNTHETIC-MR", "qty": 5,
            "custom_manufactured_qty": 0, "custom_is_manufactured": 0,
        })
        self.fields = set(fields if fields is not None else self.source)
        self.held = held or []
        self.calls = []
        self.frappe.get_meta = lambda dt: SimpleNamespace(has_field=self.fields.__contains__)
        self.frappe.db.sql = self.sql
        migration = ModuleType("namar_kdt.migration")
        migration.is_enabled = lambda: enabled
        package = ModuleType("namar_kdt")
        package.__path__ = []
        spec = importlib.util.spec_from_file_location("_manufacturing_guard_test", ROOT / "namar_kdt/guards.py")
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"frappe": self.frappe, "namar_kdt": package,
                                    "namar_kdt.migration": migration}):
            spec.loader.exec_module(self.module)

    def sql(self, query, values=(), as_dict=False):
        self.calls.append((query, values))
        if "tabMaterial Request Item" in query:
            self.assert_projection(query)
            if (self.source.get("name"), self.source.get("parent")) != tuple(values):
                return []
            return [deepcopy(self.source)]
        if "tabKDT Cut Piece" in query:
            return self.held
        return []

    def assert_projection(self, query):
        projection = query.split(" FROM ", 1)[0].removeprefix("SELECT ")
        requested = re.findall(r"`([^`]+)`", projection)
        missing = set(requested) - self.fields
        if missing:
            raise AssertionError("Selected unavailable source columns: " + str(sorted(missing)))

    def doc(self, *, mode="قص", **overrides):
        piece = Row(material_request="SYNTHETIC-MR", material_request_item="SYNTHETIC-ROW",
                    piece_key="SYNTHETIC-ROW|U0001|P01", piece_id="SYNTHETIC-PIECE", **overrides)
        return Row(operation_mode=mode, pieces=[piece])


class ManufacturingLegacyGuardTests(unittest.TestCase):
    def test_partial_and_full_completion_block_submit_from_current_locked_row(self):
        for done, expected in ((2, "2 من 5"), (5, "5 من 5")):
            with self.subTest(done=done):
                fixture = GuardFixture()
                fixture.source["custom_manufactured_qty"] = done
                doc = fixture.doc(custom_manufactured_qty=0)
                with self.assertRaisesRegex(KDTError, expected):
                    fixture.module.guard_legacy_batch(doc)
                self.assertIn("tabMaterial Request`", fixture.calls[0][0])
                self.assertIn("tabMaterial Request Item", fixture.calls[1][0])
                self.assertTrue(all("FOR UPDATE" in query for query, _ in fixture.calls))
                self.assertFalse(any("tabKDT Cut Piece" in query for query, _ in fixture.calls))

    def test_positive_quantity_takes_precedence_over_legacy_boolean(self):
        fixture = GuardFixture()
        fixture.source.update(custom_manufactured_qty=2, custom_is_manufactured=1)
        with self.assertRaisesRegex(KDTError, "2 من 5.*المتبقي 3"):
            fixture.module.guard_legacy_batch(fixture.doc())

    def test_boolean_fallback_without_quantity_column_is_not_queried(self):
        source = {"name": "SYNTHETIC-ROW", "parent": "SYNTHETIC-MR", "qty": 5,
                  "custom_is_manufactured": 1}
        fixture = GuardFixture(source)
        with self.assertRaisesRegex(KDTError, "5 من 5"):
            fixture.module.guard_legacy_batch(fixture.doc())
        child_sql = [q for q, _ in fixture.calls if "tabMaterial Request Item" in q][0]
        self.assertNotIn("custom_manufactured_qty", child_sql)

    def test_absent_optional_columns_keep_unmanufactured_source_usable(self):
        fixture = GuardFixture({"name": "SYNTHETIC-ROW", "parent": "SYNTHETIC-MR", "qty": 5})
        fixture.module.guard_legacy_batch(fixture.doc())
        self.assertEqual(len(fixture.calls), 3)
        self.assertIn("tabKDT Cut Piece", fixture.calls[-1][0])

    def test_repeated_pieces_lock_one_source_without_bypassing_piece_ledger(self):
        fixture = GuardFixture()
        doc = fixture.doc()
        second = deepcopy(doc["pieces"][0])
        second["piece_key"] = "SYNTHETIC-ROW|U0002|P01"
        doc["pieces"].append(second)
        fixture.module.guard_legacy_batch(doc)
        self.assertEqual(sum("tabMaterial Request Item" in q for q, _ in fixture.calls), 1)
        self.assertEqual(sum("tabKDT Cut Piece" in q for q, _ in fixture.calls), 2)

    def test_existing_piece_claim_still_blocks_unmanufactured_source(self):
        fixture = GuardFixture(held=[Row(current_order="SYNTHETIC-CUT", state="reserved")])
        with self.assertRaisesRegex(KDTError, "SYNTHETIC-CUT"):
            fixture.module.guard_legacy_batch(fixture.doc())

    def test_missing_or_wrong_parent_source_cannot_submit(self):
        for source in ("", "SYNTHETIC-MISSING"):
            with self.subTest(source=source):
                fixture = GuardFixture()
                doc = fixture.doc()
                doc["pieces"][0]["material_request_item"] = source
                with self.assertRaises(KDTError):
                    fixture.module.guard_legacy_batch(doc)
        fixture = GuardFixture()
        fixture.source["parent"] = "SYNTHETIC-OTHER-MR"
        with self.assertRaises(KDTError):
            fixture.module.guard_legacy_batch(fixture.doc())

    def test_inactive_hook_does_not_read_or_lock_sources(self):
        fixture = GuardFixture(enabled=False)
        fixture.source.update(custom_manufactured_qty=5)
        fixture.module.guard_legacy_batch(fixture.doc())
        self.assertEqual(fixture.calls, [])

    def test_non_cutting_mode_keeps_legacy_behavior(self):
        fixture = GuardFixture()
        fixture.source.update(custom_manufactured_qty=5)
        fixture.module.guard_legacy_batch(fixture.doc(mode="تلبيس"))
        self.assertEqual(fixture.calls, [])


if __name__ == "__main__":
    unittest.main()
