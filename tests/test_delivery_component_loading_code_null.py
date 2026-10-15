"""Exercise actual package persistence against a nullable UNIQUE loading code.

SQLite enforces the relevant MariaDB constraint: several NULL values are valid,
but repeating an empty string is not. No Frappe bench or site data is required.
"""
from copy import deepcopy
import sqlite3
import unittest
from unittest.mock import patch

from test_delivery_component_cancellation_race import (
    Document, MR, PACKAGE, load_service, package,
)


class LoadingCodePersistenceTests(unittest.TestCase):
    def setUp(self):
        self.service, self.frappe, self.db = load_service()
        self.sql = sqlite3.connect(":memory:")
        self.addCleanup(self.sql.close)
        self.addCleanup(self.db.finish)
        self.sql.execute("CREATE TABLE packages (name TEXT PRIMARY KEY, loading_code TEXT UNIQUE)")
        self.saved_values = []
        self.inserted_values = []

        def set_value(doctype, name, values, value=None, **kwargs):
            self.assertEqual(doctype, PACKAGE)
            values = values if isinstance(values, dict) else {values: value}
            self.saved_values.append((name, deepcopy(values)))
            if "loading_code" in values:
                self.sql.execute("UPDATE packages SET loading_code = ? WHERE name = ?",
                                 (values["loading_code"], name))

        def get_doc(values):
            self.assertEqual(values["doctype"], PACKAGE)
            doc = Document(**values, name=f"NEW-{len(self.inserted_values) + 1}")

            def db_insert():
                self.inserted_values.append(deepcopy(values))
                self.sql.execute("INSERT INTO packages VALUES (?, ?)",
                                 (doc.name, values.get("loading_code")))

            doc.db_insert = db_insert
            return doc

        self.db.set_value = set_value
        self.frappe.get_doc.side_effect = get_doc
        self.enterContext(patch.object(self.service, "load_rules", return_value=[]))
        self.enterContext(patch.object(self.service, "ensure_package_event_baseline"))

    def nonbarcode(self, index):
        return package(name=f"P-{index}", package_key=f"foam||white||FOAM||{index}",
                       barcode_key=f"existing-token-{index}", loading_code=None,
                       tracking_route=self.service.TRACKING_ROUTE_DELIVERY_ONLY,
                       tracking_revision=0)

    def seed(self, rows):
        self.sql.executemany("INSERT INTO packages VALUES (?, ?)",
                             [(row["name"], row.get("loading_code")) for row in rows])

    def codes(self):
        return dict(self.sql.execute("SELECT name, loading_code FROM packages"))

    def test_repeated_updates_of_multiple_nonbarcode_packages_keep_null_unique_values(self):
        existing = [self.nonbarcode(index) for index in range(1, 4)]
        self.seed(existing)
        desired = [{**row, "loading_code": ""} for row in existing]
        original = deepcopy(desired)
        for _ in range(2):
            result = self.service.upsert_package_rows(MR, desired, existing_rows=existing)
            self.assertEqual([row["loading_code"] for row in result], [None, None, None])
            self.assertEqual([row["barcode_key"] for row in result],
                             [row["barcode_key"] for row in existing])
        self.assertEqual(self.codes(), {"P-1": None, "P-2": None, "P-3": None})
        self.assertEqual(desired, original)
        self.assertEqual(len(self.saved_values), 6)

    def test_new_nonbarcode_packages_store_null_before_direct_insert(self):
        desired = [{**self.nonbarcode(index), "loading_code": ""} for index in range(1, 4)]
        for row in desired:
            row.pop("name")
        result = self.service.upsert_package_rows(MR, desired, existing_rows=[])
        self.assertEqual(list(self.codes().values()), [None, None, None])
        self.assertEqual([row["loading_code"] for row in self.inserted_values], [None] * 3)
        self.assertEqual([row["barcode_key"] for row in result],
                         [row["barcode_key"] for row in desired])

    def test_empty_variants_use_null_for_each_nonbarcode_route(self):
        routes = [self.service.TRACKING_ROUTE_DELIVERY_ONLY,
                  self.service.TRACKING_ROUTE_WITH_DOOR, self.service.TRACKING_ROUTE_EXCLUDED]
        desired = [{**self.nonbarcode(index), "tracking_route": route, "loading_code": value}
                   for index, (route, value) in enumerate(zip(routes, [None, "", " \t"]), 1)]
        self.seed(desired)
        result = self.service.upsert_package_rows(MR, desired, existing_rows=desired)
        self.assertEqual([row["loading_code"] for row in result], [None] * 3)
        self.assertEqual(list(self.codes().values()), [None] * 3)

    def test_valid_nonbarcode_code_is_preserved_and_reserved_for_barcode_assignment(self):
        existing = [{**self.nonbarcode(1), "loading_code": "ABC-01"}, self.nonbarcode(2),
                    package(name="BARCODE", package_key="frame||white||FRAME||1",
                            loading_code=None, tracking_revision=0)]
        self.seed(existing)
        desired = [{**row, "loading_code": row.get("loading_code") or ""} for row in existing]
        self.service.assign_loading_codes(desired, "ABC")
        result = self.service.upsert_package_rows(MR, desired, existing_rows=existing)
        self.assertEqual([row["loading_code"] for row in result], ["ABC-01", None, "ABC-02"])
        self.assertEqual(self.codes(), {"P-1": "ABC-01", "P-2": None, "BARCODE": "ABC-02"})

    def test_barcode_route_blank_values_and_existing_tokens_are_unchanged(self):
        existing = [package(tracking_revision=0, loading_code=None)]
        self.seed(existing)
        desired = [{**existing[0], "loading_code": ""}]
        result = self.service.upsert_package_rows(MR, desired, existing_rows=existing)
        self.assertEqual(result[0]["loading_code"], "")
        self.assertEqual(self.codes(), {"PACKAGE-1": ""})
        self.assertEqual(result[0]["barcode_key"], "barcode-old")

    def test_real_duplicate_codes_still_fail_unique_constraint(self):
        existing = [self.nonbarcode(1), self.nonbarcode(2)]
        self.seed(existing)
        desired = [{**row, "loading_code": "ABC-01"} for row in existing]
        with self.assertRaises(sqlite3.IntegrityError):
            self.service.upsert_package_rows(MR, desired, existing_rows=existing)


if __name__ == "__main__":
    unittest.main()
