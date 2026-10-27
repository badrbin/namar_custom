"""Synthetic transaction tests for the guarded KDT activation and rollback."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "namar_kdt"


class Blocked(Exception):
    pass


class AttrDict(dict):
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key) from None

    def __setattr__(self, key, value):
        self[key] = value


def record_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


class Harness:
    def __init__(self):
        self.roles = {"System Manager", "Script Manager"}
        self.calls = []
        self.fail_save = None
        self.reference = {"records": [
            {"doctype": "Client Script", "name": "KDT Cutting Desk Controls",
             "hashes": {"script": record_hash("synthetic desk")},
             "state": {"enabled": 1, "dt": "KDT Cutting Desk", "view": "Form"}},
            {"doctype": "Server Script", "name": "kdt_cutting_desk_api",
             "hashes": {"script": record_hash("synthetic api")},
             "state": {"disabled": 0, "api_method": "kdt_cutting_desk_api", "script_type": "API"}},
            {"doctype": "Server Script", "name": "kdt_cutting_desk_legacy_guard",
             "hashes": {"script": record_hash("synthetic guard")},
             "state": {"disabled": 0, "reference_doctype": "KDT Cutting Batch",
                       "script_type": "DocType Event", "doctype_event": "Before Submit"}},
            {"doctype": "Report", "name": "نتائج التخصيم",
             "hashes": {"javascript": record_hash("synthetic report js"),
                        "report_script": record_hash("synthetic report py")},
             "state": {"module": "Stock", "is_standard": "No", "report_type": "Script Report",
                       "ref_doctype": "Material Request", "disabled": 0, "prepared_report": 0,
                       "disable_prepared_report_automation": 1, "letter_head": "Keep letterhead"}},
        ]}
        self.schema = {
            "doctypes": [{"name": "KDT Cut Order", "module": "Stock", "custom": 1,
                          "istable": 0, "issingle": 0, "autoname": "format:KDT-{#####}"}],
            "fields": [{"parent": "KDT Cut Order", "fieldname": "revision", "fieldtype": "Int",
                        "options": None, "reqd": 0, "unique": 0, "read_only": 1, "default": "0"}],
            "permissions": [{"parent": "KDT Cut Order", "role": "System Manager", "permlevel": 0,
                             "read": 1, "write": 0, "create": 0, "delete": 0, "submit": 0,
                             "cancel": 0, "amend": 0, "if_owner": 0}],
        }
        self.documents = {}
        bodies = {
            "KDT Cutting Desk Controls": {"script": "synthetic desk"},
            "kdt_cutting_desk_api": {"script": "synthetic api"},
            "kdt_cutting_desk_legacy_guard": {"script": "synthetic guard"},
            "نتائج التخصيم": {"javascript": "synthetic report js", "report_script": "synthetic report py",
                              "roles": [{"role": "Manufacturing User"}],
                              "filters": [{"fieldname": "component", "fieldtype": "Data"}]},
        }
        for reference in self.reference["records"]:
            key = (reference["doctype"], reference["name"])
            self.documents[key] = {"doctype": key[0], "name": key[1],
                                   **deepcopy(reference["state"]), **bodies[key[1]]}
        self.documents[("Namar KDT Settings", "Namar KDT Settings")] = {
            "doctype": "Namar KDT Settings", "name": "Namar KDT Settings", "enabled": 0,
        }
        schema_doc = self.schema["doctypes"][0]
        self.documents[("DocType", schema_doc["name"])] = {
            "doctype": "DocType", **deepcopy(schema_doc),
            "fields": [AttrDict(deepcopy(row)) for row in self.schema["fields"]],
            "permissions": [AttrDict(deepcopy(row)) for row in self.schema["permissions"]],
        }
        self.documents[("DocType", "Namar KDT Settings")] = {"name": "Namar KDT Settings"}
        self.frappe = ModuleType("frappe")
        self.frappe.local = SimpleNamespace(site="testnamar.u.frappe.cloud")
        self.frappe.PermissionError = Blocked
        self.frappe.throw = self.throw
        self.frappe.only_for = self.only_for
        self.frappe.get_doc = self.get_doc
        self.frappe.get_single = lambda doctype: self.get_doc(doctype, doctype)
        self.frappe.clear_cache = lambda **kwargs: self.calls.append(("clear_cache", kwargs))
        self.frappe.clear_document_cache = lambda *args: self.calls.append(("clear_document_cache", args))
        self.frappe.whitelist = self.whitelist
        self.frappe.has_permission = lambda *_args: True
        self.frappe._dict = AttrDict
        self.utils = ModuleType("frappe.utils")
        self.utils.cint = lambda value: int(value or 0)
        self.utils.now_datetime = lambda: "2026-01-01 00:00:00"
        self.frappe.utils = self.utils
        self.frappe.db = SimpleNamespace(
            exists=lambda dt, name: (dt, name) in self.documents,
            get_single_value=lambda dt, field: self.documents[(dt, dt)].get(field),
            sql=self.sql, set_value=self.set_value,
            commit=lambda: self.calls.append(("COMMIT",)),
            rollback=lambda: self.calls.append(("ROLLBACK",)),
        )
        spec = importlib.util.spec_from_file_location("kdt_migration_under_test", PACKAGE / "migration.py")
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"frappe": self.frappe, "frappe.utils": self.utils}):
            spec.loader.exec_module(self.module)
        self.packaged_reference = self.module._reference
        self.module._reference = lambda name="migration": self.schema if name == "schema" else self.reference

    @staticmethod
    def whitelist(**options):
        def decorate(function):
            function.allowed_methods = options.get("methods")
            return function
        return decorate

    @staticmethod
    def throw(message, error_type=Blocked):
        raise error_type(message)

    def only_for(self, role):
        if role not in self.roles:
            raise Blocked(role)

    def get_doc(self, doctype, name):
        harness = self

        class Document(AttrDict):
            def save(self, **kwargs):
                harness.calls.append(("save", (doctype, name), kwargs))
                if harness.fail_save == name:
                    raise RuntimeError("synthetic save failure")
                harness.documents[(doctype, name)] = deepcopy(dict(self))
                return self

            def check_permission(self, *_args):
                pass

            def is_permitted(self):
                return True

        return Document(deepcopy(self.documents[(doctype, name)]))

    def sql(self, query, values):
        if not query.startswith("SELECT name FROM `tab") or not query.endswith("FOR UPDATE"):
            raise AssertionError("Unexpected migration SQL")
        self.calls.append(("lock", query, values))
        return []

    def set_value(self, doctype, name, values):
        self.calls.append(("set_value", (doctype, name), deepcopy(values)))
        self.documents[(doctype, name)].update(deepcopy(values))

    def post(self, function):
        """Model Frappe's request rollback on an escaping exception."""
        original = deepcopy(self.documents)
        try:
            return function()
        except Exception:
            self.documents = original
            raise


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.h = Harness()
        self.m = self.h.module

    def test_activation_switches_only_approved_fields_and_keeps_originals(self):
        before = deepcopy(self.h.documents)
        status = self.h.post(self.m.activate)
        self.assertTrue(status["enabled"])
        self.assertEqual(status["schema_errors"], [])
        self.assertTrue(all(row["hashes_match"] and row["state_matches"] for row in status["records"]))
        for reference in self.h.reference["records"]:
            key = reference["doctype"], reference["name"]
            expected = deepcopy(before[key])
            if key[0] == "Report":
                expected.update(module="Namar KDT", is_standard="Yes")
            elif key[0] == "Server Script":
                expected["disabled"] = 1
            else:
                expected["enabled"] = 0
            self.assertEqual(self.h.documents[key], expected)
        updates = [call for call in self.h.calls if call[0] == "set_value"]
        self.assertEqual(updates, [("set_value", ("Report", "نتائج التخصيم"),
                                    {"module": "Namar KDT", "is_standard": "Yes"})])
        self.assertFalse(any(call[0] in ("COMMIT", "ROLLBACK") for call in self.h.calls))

    def test_deactivation_restores_report_scripts_letterhead_roles_filters(self):
        before = deepcopy(self.h.documents)
        self.h.post(self.m.activate)
        status = self.h.post(self.m.deactivate)
        self.assertFalse(status["enabled"])
        for reference in self.h.reference["records"]:
            key = reference["doctype"], reference["name"]
            self.assertEqual(self.h.documents[key], before[key])

    def test_every_reference_is_locked_before_first_write_in_fixed_order(self):
        self.h.post(self.m.activate)
        locks = [call for call in self.h.calls if call[0] == "lock"]
        self.assertEqual(len(locks), len(self.h.reference["records"]))
        expected = sorted(self.h.reference["records"], key=lambda row: (row["doctype"], row["name"]))
        self.assertEqual([call[2][0] for call in locks], [row["name"] for row in expected])
        self.assertEqual([call[0] for call in self.h.calls[:len(locks)]], ["lock"] * len(locks))

    def test_consistent_repeat_activation_and_deactivation_are_read_only(self):
        self.h.post(self.m.deactivate)
        self.assertFalse(any(call[0] in ("save", "set_value") for call in self.h.calls))
        self.h.post(self.m.activate)
        self.h.calls.clear()
        self.h.post(self.m.activate)
        self.assertFalse(any(call[0] in ("save", "set_value") for call in self.h.calls))

    def test_code_drift_blocks_switch_without_writes(self):
        self.h.documents[("Server Script", "kdt_cutting_desk_api")]["script"] += " changed"
        before = deepcopy(self.h.documents)
        with self.assertRaises(Blocked):
            self.h.post(self.m.activate)
        self.assertEqual(self.h.documents, before)
        self.assertFalse(any(call[0] in ("save", "set_value") for call in self.h.calls))

    def test_disabled_flag_drift_blocks_activation(self):
        self.h.documents[("Client Script", "KDT Cutting Desk Controls")]["enabled"] = 0
        with self.assertRaises(Blocked):
            self.h.post(self.m.activate)
        self.assertFalse(any(call[0] in ("save", "set_value") for call in self.h.calls))

    def test_noop_does_not_hide_state_drift(self):
        self.h.post(self.m.activate)
        self.h.calls.clear()
        self.h.documents[("Report", "نتائج التخصيم")]["is_standard"] = "No"
        with self.assertRaises(Blocked):
            self.h.post(self.m.activate)
        self.assertFalse(any(call[0] in ("save", "set_value") for call in self.h.calls))

    def test_deactivation_refuses_to_overwrite_new_report_code(self):
        self.h.post(self.m.activate)
        self.h.documents[("Report", "نتائج التخصيم")]["report_script"] += " changed"
        before = deepcopy(self.h.documents)
        with self.assertRaises(Blocked):
            self.h.post(self.m.deactivate)
        self.assertEqual(self.h.documents, before)

    def test_schema_field_and_permission_drift_block_before_writes(self):
        mutations = (
            lambda doc: doc["fields"][0].update(fieldtype="Data"),
            lambda doc: doc["fields"].append(AttrDict(fieldname="unreviewed", fieldtype="Data")),
            lambda doc: doc["permissions"][0].update(write=1),
            lambda doc: doc.update(autoname="hash"),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                h = Harness()
                mutation(h.documents[("DocType", "KDT Cut Order")])
                with self.assertRaises(Blocked):
                    h.post(h.module.activate)
                self.assertFalse(any(call[0] in ("save", "set_value") for call in h.calls))

    def test_mid_switch_exception_propagates_for_framework_rollback(self):
        before = deepcopy(self.h.documents)
        self.h.fail_save = "kdt_cutting_desk_legacy_guard"
        with self.assertRaisesRegex(RuntimeError, "synthetic save failure"):
            self.h.post(self.m.activate)
        self.assertEqual(self.h.documents, before)
        self.assertFalse(any(call[0] == "COMMIT" for call in self.h.calls))

    def test_wrong_site_is_rejected_before_any_database_write(self):
        self.h.frappe.local.site = "unapproved.example.test"
        with self.assertRaises(Blocked):
            self.h.post(self.m.activate)
        self.assertEqual(self.h.calls, [])
        self.assertFalse(self.m.is_enabled())

    def test_both_admin_roles_are_required_for_switch(self):
        for missing in ("System Manager", "Script Manager"):
            with self.subTest(missing=missing):
                h = Harness()
                h.roles.remove(missing)
                with self.assertRaises(Blocked):
                    h.post(h.module.activate)
                self.assertEqual(h.calls, [])

    def test_switch_requires_post_while_status_is_get_only(self):
        self.assertEqual(self.m.activate.allowed_methods, ["POST"])
        self.assertEqual(self.m.deactivate.allowed_methods, ["POST"])
        self.assertEqual(self.m.migration_status.allowed_methods, ["GET"])
        self.assertEqual(self.m.compare_report.allowed_methods, ["GET"])

    def test_uninstall_is_blocked_until_deactivation(self):
        self.h.post(self.m.activate)
        with self.assertRaises(Blocked):
            self.m.before_uninstall()
        self.h.post(self.m.deactivate)
        self.m.before_uninstall()

    def test_uninstall_does_not_trust_disabled_flag_with_app_owned_report(self):
        for changed in ({"module": "Namar KDT"}, {"is_standard": "Yes"}):
            with self.subTest(changed=changed):
                h = Harness()
                h.documents[("Report", "نتائج التخصيم")].update(changed)
                self.assertFalse(h.module.is_enabled())
                with self.assertRaises(Blocked):
                    h.module.before_uninstall()

    def test_packaged_references_and_standard_module_paths_exist(self):
        # Check the actual shipping package, independent of synthetic records.
        manifest = self.h.packaged_reference()
        schema = self.h.packaged_reference("schema")
        self.assertEqual(manifest["site"], self.m.TEST_SITE)
        self.assertEqual(len(manifest["records"]), 4)
        self.assertEqual(len(schema["doctypes"]), 6)
        self.assertEqual((PACKAGE / "modules.txt").read_text().strip(), "Namar KDT")
        report = PACKAGE / "namar_kdt/report/نتائج_التخصيم"
        self.assertTrue((report / "نتائج_التخصيم.py").is_file())
        self.assertTrue((report / "نتائج_التخصيم.js").is_file())
        self.assertFalse((report / "نتائج_التخصيم.json").exists())
        settings = json.loads((PACKAGE / "namar_kdt/doctype/namar_kdt_settings/namar_kdt_settings.json").read_text())
        self.assertEqual((settings["module"], settings["issingle"], settings["custom"]), ("Namar KDT", 1, 0))
        self.assertEqual(next(field for field in settings["fields"] if field["fieldname"] == "enabled")["default"], "0")
        self.assertFalse((PACKAGE / "fixtures").exists())


if __name__ == "__main__":
    unittest.main()
