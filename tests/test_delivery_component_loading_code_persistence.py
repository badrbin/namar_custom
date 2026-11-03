"""Exercise real package writes against a nullable UNIQUE loading-code column.

SQLite reproduces the relevant index rule: duplicate empty strings fail while
multiple NULLs are valid. No Frappe bench or business records are required.
"""
from copy import deepcopy
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_delivery_component_cancellation_race import Document, MR, PACKAGE, load_service


class UniqueCodeDatabase:
    def __init__(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.execute("CREATE TABLE packages (name TEXT PRIMARY KEY, loading_code TEXT UNIQUE)")

    def exists(self, doctype, name):
        assert doctype == "DocType" and name == PACKAGE
        return True

    def insert(self, name, values):
        self.connection.execute("INSERT INTO packages VALUES (?, ?)", (name, values.get("loading_code")))

    def set_value(self, doctype, name, values, value=None, **kwargs):
        assert doctype == PACKAGE
        values = values if isinstance(values, dict) else {values: value}
        if "loading_code" in values:
            self.connection.execute("UPDATE packages SET loading_code=? WHERE name=?", (values["loading_code"], name))

    def codes(self):
        return [code for (code,) in self.connection.execute("SELECT loading_code FROM packages ORDER BY name")]


class LoadingCodePersistenceTests(unittest.TestCase):
    def setUp(self):
        self.service, self.frappe, _ = load_service()
        self.db = UniqueCodeDatabase()
        self.addCleanup(self.db.connection.close)
        self.frappe.db = self.db
        self.frappe.get_doc = self.new_document
        for name in ("load_rules", "load_component_sort_order", "ensure_package_event_baseline"):
            stub = patch.object(self.service, name, return_value={} if name == "load_component_sort_order" else [])
            stub.start()
            self.addCleanup(stub.stop)

    def new_document(self, values):
        self.assertEqual(values["doctype"], PACKAGE)
        count = self.db.connection.execute("SELECT COUNT(*) FROM packages").fetchone()[0]
        doc = Document(**{**values, "name": "PACKAGE-%s" % (count + 1)})
        doc.db_insert = lambda: self.db.insert(doc.name, values)
        return doc

    def desired(self, index, code, route):
        return {"package_key": "component-%s||||ITEM||1" % index,
                "package_qty": 2, "ready_qty": 0, "active": 1,
                "loading_code": code, "tracking_route": route}

    def persist(self, desired, mode, prior_codes=None):
        self.db.connection.execute("DELETE FROM packages")
        before = deepcopy(desired)
        existing = []
        if mode == "update":
            for index, row in enumerate(desired, 1):
                previous = {**row, "name": "PACKAGE-%s" % index,
                            "loading_code": prior_codes[index - 1] if prior_codes is not None else "OLD-%s" % index}
                existing.append(previous)
                self.db.insert(previous["name"], previous)
        result = self.service.upsert_package_rows(MR, desired, existing_rows=existing)
        self.assertEqual(desired, before, "Persistence must not mutate the caller's package rows")
        return result

    def test_two_nonbarcode_blank_codes_use_nullable_unique_index_on_insert_and_update(self):
        for mode in ("insert", "update"):
            for route in (self.service.TRACKING_ROUTE_WITH_DOOR, self.service.TRACKING_ROUTE_DELIVERY_ONLY):
                for code in ("", None, " \t "):
                    with self.subTest(mode=mode, route=route, code=code):
                        result = self.persist([self.desired(i, code, route) for i in (1, 2)], mode)
                        self.assertEqual(self.db.codes(), [None, None])
                        self.assertEqual([row["loading_code"] for row in result], [None, None])

    def test_nonempty_nonbarcode_codes_are_preserved_exactly(self):
        codes = ["  AbC-07  ", "Legacy-Code-08"]
        for mode in ("insert", "update"):
            with self.subTest(mode=mode):
                rows = [self.desired(i, code, self.service.TRACKING_ROUTE_WITH_DOOR)
                        for i, code in enumerate(codes, 1)]
                result = self.persist(rows, mode)
                self.assertEqual(self.db.codes(), codes)
                self.assertEqual([row["loading_code"] for row in result], codes)

    def test_barcode_blank_values_are_not_normalized(self):
        for mode in ("insert", "update"):
            for code in ("", None, " \t "):
                with self.subTest(mode=mode, code=code):
                    result = self.persist([self.desired(1, code, self.service.TRACKING_ROUTE_BARCODE)], mode)
                    self.assertEqual(self.db.codes(), [code])
                    self.assertEqual(result[0]["loading_code"], code)

    def test_barcode_duplicate_empty_codes_still_hit_unique_constraint(self):
        for mode in ("insert", "update"):
            with self.subTest(mode=mode), self.assertRaises(sqlite3.IntegrityError):
                self.persist([self.desired(i, "", self.service.TRACKING_ROUTE_BARCODE) for i in (1, 2)], mode)

    def test_omitted_loading_code_does_not_clear_existing_code_or_add_a_key(self):
        desired = self.desired(1, "unused", self.service.TRACKING_ROUTE_WITH_DOOR)
        del desired["loading_code"]
        result = self.persist([desired], "update", prior_codes=["KEEP-01"])
        self.assertEqual(self.db.codes(), ["KEEP-01"])
        self.assertNotIn("loading_code", result[0])

    def test_build_without_optional_request_prefix_persists_two_blank_codes(self):
        components = [{"component": name, "item_code": "ITEM", "required_qty": 2}
                      for name in ("frame", "panel")]
        rules = [{"name": "rule-" + row["component"], "component": row["component"], "match_mode": "مطابقة مكون",
                  "tracking_route": self.service.TRACKING_ROUTE_WITH_DOOR}
                 for row in components]
        mr = Document(name=MR)
        with patch.object(self.service, "load_rules", return_value=rules):
            desired = self.service.build_package_rows(mr, components, existing_packages=[])
            self.assertEqual([row["loading_code"] for row in desired], ["", ""])
            original_meta = self.frappe.get_meta
            with patch.object(self.frappe, "get_meta", side_effect=lambda doctype:
                              SimpleNamespace(has_field=lambda field: field != self.service.MR_LOADING_CODE_FIELD)
                              if doctype == "Material Request" else original_meta(doctype)):
                prefix = self.service.get_or_make_loading_prefix(mr, dry_run=False)
                self.assertEqual(prefix, "")
                assigned = self.service.assign_loading_codes(desired, prefix)
            self.assertEqual([row["loading_code"] for row in assigned], ["", ""])
            for mode in ("insert", "update"):
                with self.subTest(mode=mode):
                    self.persist(assigned, mode, prior_codes=[None, None])
                    self.assertEqual(self.db.codes(), [None, None])

    def test_normal_prod_all_route_allocation_preserves_reserved_codes(self):
        routes = [self.service.TRACKING_ROUTE_WITH_DOOR, self.service.TRACKING_ROUTE_DELIVERY_ONLY,
                  self.service.TRACKING_ROUTE_BARCODE]
        desired = [self.desired(i, code, route)
                   for i, (code, route) in enumerate(zip(["", "", "ABC-07"], routes), 1)]
        reserved = self.service.get_reserved_loading_codes(MR, [
            {"loading_code": "ABC-01", "active": 0}, {"loading_code": "ABC-03", "active": 1}])
        assigned = self.service.assign_loading_codes(desired, "ABC", reserved_loading_codes=reserved)
        expected = ["ABC-02", "ABC-04", "ABC-07"]
        self.assertEqual([row["loading_code"] for row in assigned], expected)
        for mode in ("insert", "update"):
            with self.subTest(mode=mode):
                self.persist(assigned, mode)
                self.assertEqual(self.db.codes(), expected)


if __name__ == "__main__":
    unittest.main()
