"""Activation and API-boundary tests with a transaction-aware fake Frappe.

Only synthetic documents are used. The request context models Frappe's POST
commit/rollback boundary; it does not imply that the app commits independently.
"""

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
import hashlib
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
TEST_SITE = "testnamar.u.frappe.cloud"
CLIENT_NAME = "حساب التخصيم"
SERVER_NAME = "get_cutting_values_bulk_exclusions_test"


class ValidationError(Exception):
    pass


class PermissionError(ValidationError):
    pass


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class FakeDB:
    def __init__(self):
        self.records = {
            ("Client Script", CLIENT_NAME): {
                "doctype": "Client Script", "name": CLIENT_NAME,
                "script": "synthetic client source", "enabled": 1,
            },
            ("Server Script", SERVER_NAME): {
                "doctype": "Server Script", "name": SERVER_NAME,
                "script": "synthetic server source", "disabled": 0,
            },
            ("Demo Settings", "Demo Settings"): {
                "doctype": "Demo Settings", "name": "Demo Settings", "enabled": 0,
            },
        }
        self.single_cache = {}
        self.locks = []
        self.saves = []
        self.fail_save = None
        self.explicit_commits = 0
        self.request_commits = 0
        self.request_rollbacks = 0

    def sql(self, query, params):
        # Activation SQL is allowed only to acquire row locks, in fixed order.
        if query not in (
            "SELECT name FROM `tabClient Script` WHERE name=%s FOR UPDATE",
            "SELECT name FROM `tabServer Script` WHERE name=%s FOR UPDATE",
        ):
            raise AssertionError(f"Unexpected SQL: {query}")
        self.locks.append((query, params))
        return [(params,)]

    def get_single_value(self, doctype, fieldname):
        key = (doctype, fieldname)
        if key not in self.single_cache:
            self.single_cache[key] = self.records[(doctype, doctype)].get(fieldname)
        return self.single_cache[key]

    def commit(self):
        self.explicit_commits += 1
        raise AssertionError("The app must leave the transaction to Frappe's request boundary")

    @contextmanager
    def post_request(self):
        before = deepcopy(self.records)
        self.single_cache.clear()
        try:
            yield
        except Exception:
            self.records = before
            self.single_cache.clear()
            self.request_rollbacks += 1
            raise
        else:
            self.request_commits += 1


class FakeDocument:
    def __init__(self, framework, data):
        self._framework = framework
        self.__dict__.update(deepcopy(data))

    def save(self, ignore_permissions=False):
        framework = self._framework
        if self.doctype == "Demo Settings":
            if not ignore_permissions:
                raise PermissionError("Settings is read-only to users")
        else:
            framework.only_for("System Manager")
        if self.doctype == "Server Script":
            # Frappe 15 ServerScript.validate has this additional role guard.
            framework.only_for("Script Manager", True)
        if framework.db.fail_save == self.doctype:
            raise ValidationError(f"Synthetic save failure: {self.doctype}")
        data = {key: deepcopy(value) for key, value in self.__dict__.items() if not key.startswith("_")}
        framework.db.records[(self.doctype, self.name)] = data
        framework.db.saves.append((self.doctype, ignore_permissions))
        # Document.update_single invalidates the per-request Single value cache.
        for key in list(framework.db.single_cache):
            if key[0] == self.doctype:
                del framework.db.single_cache[key]
        return self

    def execute_method(self):
        framework = self._framework
        framework.legacy_calls.append(deepcopy(framework.form_dict))
        framework.response["message"] = {"values": {"legacy": framework.form_dict["item_codes"]}}
        return {}


class FakeFrappe(ModuleType):
    PermissionError = PermissionError

    def __init__(self):
        super().__init__("frappe")
        self.local = SimpleNamespace(site=TEST_SITE)
        self.db = FakeDB()
        self.roles = {"System Manager", "Script Manager"}
        self.permissions = {("Material Request", "read")}
        self.response = {}
        self.form_dict = {}
        self.legacy_calls = []
        self.calculation_calls = []
        self.cache_clears = []
        self.reads = []

    @staticmethod
    def whitelist(*, methods):
        def decorator(function):
            function.allowed_methods = tuple(methods)
            return function
        return decorator

    @staticmethod
    def throw(message, exception=ValidationError):
        raise exception(message)

    def only_for(self, role, *_args):
        if role not in self.roles:
            raise PermissionError(role)

    def has_permission(self, doctype, permission):
        return (doctype, permission) in self.permissions

    def get_doc(self, doctype, name):
        self.reads.append((doctype, name))
        return FakeDocument(self, self.db.records[(doctype, name)])

    def get_single(self, doctype):
        return self.get_doc(doctype, doctype)

    def clear_cache(self, **kwargs):
        self.cache_clears.append(kwargs)

    def calculate(self, payload):
        self.calculation_calls.append(deepcopy(payload))
        return {"values": {"synthetic": payload.get("item_codes", "")}}


def load_api(framework):
    utils = ModuleType("frappe.utils")
    utils.cint = lambda value: int(value or 0)
    utils.now_datetime = lambda: datetime(2026, 1, 1, 12, 0)
    cutting = ModuleType("demo.cutting")
    cutting.calculate = framework.calculate
    package = ModuleType("demo")
    package.__path__ = [str(ROOT / "demo")]
    spec = importlib.util.spec_from_file_location("demo.api", ROOT / "demo/api.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {
        "frappe": framework, "frappe.utils": utils,
        "demo": package, "demo.cutting": cutting, "demo.api": module,
    }):
        spec.loader.exec_module(module)
    # Exercise drift guards with small, non-business fixtures.
    module.CLIENT_SHA256 = digest("synthetic client source")
    module.SERVER_SHA256 = digest("synthetic server source")
    return module


class IntegrationTestCase(unittest.TestCase):
    def setUp(self):
        self.frappe = FakeFrappe()
        self.api = load_api(self.frappe)

    def record(self, doctype):
        name = {"Client Script": CLIENT_NAME, "Server Script": SERVER_NAME}.get(doctype, doctype)
        return self.frappe.db.records[(doctype, name)]

    def assert_state(self, enabled):
        self.assertEqual(self.record("Demo Settings")["enabled"], int(enabled))
        self.assertEqual(self.record("Client Script")["enabled"], int(not enabled))
        self.assertEqual(self.record("Server Script")["disabled"], int(enabled))


class ActivationTests(IntegrationTestCase):
    def test_activation_and_rollback_preserve_source_and_switch_three_documents(self):
        before = deepcopy(self.frappe.db.records)
        with self.frappe.db.post_request():
            status = self.api.activate()
            self.assertTrue(status["enabled"], "Single cache must reflect settings.save in this request")
        self.assert_state(True)
        self.assertEqual(self.frappe.db.saves, [
            ("Client Script", False), ("Server Script", False), ("Demo Settings", True),
        ])
        self.assertEqual([lock[1] for lock in self.frappe.db.locks], [CLIENT_NAME, SERVER_NAME])
        self.assertEqual(self.record("Demo Settings")["client_sha256"], self.api.CLIENT_SHA256)
        self.assertEqual(self.record("Demo Settings")["server_sha256"], self.api.SERVER_SHA256)
        self.assertIn("activated_on", self.record("Demo Settings"))
        self.assertEqual(self.frappe.cache_clears, [{"doctype": "Material Request"}])
        self.assertEqual(self.frappe.db.explicit_commits, 0)
        with self.frappe.db.post_request():
            status = self.api.deactivate()
            self.assertFalse(status["enabled"])
        self.assert_state(False)
        for doctype, name in [("Client Script", CLIENT_NAME), ("Server Script", SERVER_NAME)]:
            self.assertEqual(self.record(doctype)["script"], before[(doctype, name)]["script"])
        self.assertEqual(self.frappe.db.request_commits, 2)

    def test_repeating_consistent_activation_or_deactivation_does_not_resave(self):
        self.api.deactivate()
        self.assertEqual(self.frappe.db.saves, [])
        self.api.activate()
        saves = list(self.frappe.db.saves)
        self.api.activate()
        self.assertEqual(self.frappe.db.saves, saves)
        self.api.deactivate()
        saves = list(self.frappe.db.saves)
        self.api.deactivate()
        self.assertEqual(self.frappe.db.saves, saves)

    def test_wrong_site_and_non_manager_cannot_read_or_change_activation_state(self):
        for method in ("activate", "deactivate", "migration_status"):
            for site, roles in [("production.example", {"System Manager"}), (TEST_SITE, set())]:
                with self.subTest(method=method, site=site, roles=roles):
                    self.frappe.local.site = site
                    self.frappe.roles = roles
                    with self.assertRaises(PermissionError):
                        getattr(self.api, method)()
                    self.assertEqual(self.frappe.reads, [])
                    self.assertEqual(self.frappe.db.locks, [])
                    self.assertEqual(self.frappe.db.saves, [])
        self.frappe.local.site = "production.example"
        self.assertFalse(self.api.is_enabled())

    def test_hash_drift_blocks_activation_and_rollback_before_any_write(self):
        for doctype in ("Client Script", "Server Script"):
            for active in (False, True):
                with self.subTest(doctype=doctype, active=active):
                    self.setUp()
                    if active:
                        self.api.activate()
                        self.frappe.db.saves.clear()
                    self.record(doctype)["script"] += " changed"
                    before = deepcopy(self.frappe.db.records)
                    with self.assertRaises(ValidationError):
                        (self.api.deactivate if active else self.api.activate)()
                    self.assertEqual(self.frappe.db.records, before)
                    self.assertEqual(self.frappe.db.saves, [])

    def test_inconsistent_legacy_state_is_never_overwritten(self):
        for active in (False, True):
            for doctype, field in (("Client Script", "enabled"), ("Server Script", "disabled")):
                for method in ("activate", "deactivate"):
                    with self.subTest(active=active, doctype=doctype, method=method):
                        self.setUp()
                        if active:
                            self.api.activate()
                            self.frappe.db.saves.clear()
                        self.record(doctype)[field] = 1 - self.record(doctype)[field]
                        before = deepcopy(self.frappe.db.records)
                        with self.assertRaises(ValidationError):
                            getattr(self.api, method)()
                        self.assertEqual(self.frappe.db.records, before)
                        self.assertEqual(self.frappe.db.saves, [])

    def test_framework_transaction_rolls_back_mid_activation_and_mid_deactivation(self):
        for active in (False, True):
            for failed_doctype in ("Server Script", "Demo Settings"):
                with self.subTest(active=active, failed_doctype=failed_doctype):
                    self.setUp()
                    if active:
                        self.api.activate()
                    before = deepcopy(self.frappe.db.records)
                    self.frappe.db.fail_save = failed_doctype
                    with self.assertRaises(ValidationError), self.frappe.db.post_request():
                        (self.api.deactivate if active else self.api.activate)()
                    self.assertEqual(self.frappe.db.records, before)
                    self.assert_state(active)
                    self.assertEqual(self.frappe.db.request_rollbacks, 1)
                    self.assertEqual(self.frappe.db.explicit_commits, 0)

    def test_server_script_manager_requirement_fails_without_partial_activation(self):
        self.frappe.roles = {"System Manager"}
        with self.assertRaises(PermissionError), self.frappe.db.post_request():
            self.api.activate()
        self.assert_state(False)
        self.assertEqual(self.frappe.db.explicit_commits, 0)

    def test_status_reports_hash_drift_without_writing(self):
        self.record("Client Script")["script"] += " drift"
        status = self.api.migration_status()
        self.assertFalse(status["client_hash_matches"])
        self.assertTrue(status["server_hash_matches"])
        self.assertFalse(status["enabled"])
        self.assertEqual(status["calculator"], "demo.api.calculate_cutting")
        self.assertEqual(self.frappe.db.saves, [])

    def test_whitelisted_switches_accept_post_only(self):
        for method in ("activate", "deactivate"):
            self.assertEqual(getattr(self.api, method).allowed_methods, ("POST",))
        self.assertEqual(self.api.migration_status.allowed_methods, ("GET",))
        self.assertEqual(self.api.calculate_cutting.allowed_methods, ("GET", "POST"))
        self.assertEqual(self.api.legacy_cutting_values.allowed_methods, ("GET", "POST"))


class DispatchTests(IntegrationTestCase):
    def test_inactive_new_endpoint_requires_manager_and_material_request_permission(self):
        self.frappe.roles = set()
        with self.assertRaises(PermissionError):
            self.api.calculate_cutting(item_codes="SYNTHETIC")
        self.frappe.roles = {"System Manager"}
        self.frappe.permissions.clear()
        with self.assertRaises(PermissionError):
            self.api.calculate_cutting(item_codes="SYNTHETIC")
        self.assertEqual(self.frappe.calculation_calls, [])
        self.frappe.permissions.add(("Material Request", "create"))
        result = self.api.calculate_cutting(item_codes="SYNTHETIC")
        self.assertEqual(result, {"values": {"synthetic": "SYNTHETIC"}})
        self.assertEqual(self.frappe.db.saves, [])

    def test_active_new_and_legacy_endpoints_share_calculator_and_preserve_payload(self):
        self.api.activate()
        self.frappe.roles = set()
        payload = {"item_codes": "SYNTHETIC-A,SYNTHETIC-B", "widths": "100,90", "frame_components": "X;Y"}
        for method in (self.api.calculate_cutting, self.api.legacy_cutting_values):
            result = method(**payload)
            self.assertEqual(result, {"values": {"synthetic": payload["item_codes"]}})
        self.assertEqual(self.frappe.calculation_calls, [payload, payload])
        self.assertEqual(self.frappe.legacy_calls, [])

    def test_active_endpoints_deny_missing_material_request_permission(self):
        self.api.activate()
        self.frappe.permissions.clear()
        for method in (self.api.calculate_cutting, self.api.legacy_cutting_values):
            with self.assertRaises(PermissionError):
                method(item_codes="SYNTHETIC")
        self.assertEqual(self.frappe.calculation_calls, [])

    def test_inactive_legacy_endpoint_uses_unchanged_form_dict_and_response_shape(self):
        self.frappe.roles = set()
        payload = {"item_codes": "SYNTHETIC", "widths": "100"}
        self.frappe.form_dict.update(payload)
        result = self.api.legacy_cutting_values(**payload)
        self.assertEqual(result, {"values": {"legacy": "SYNTHETIC"}})
        self.assertEqual(self.frappe.legacy_calls, [payload])
        self.assertEqual(self.frappe.calculation_calls, [])
        self.assertEqual(self.frappe.db.saves, [])

    def test_inactive_legacy_endpoint_refuses_disabled_original(self):
        self.record("Server Script")["disabled"] = 1
        with self.assertRaises(ValidationError):
            self.api.legacy_cutting_values(item_codes="SYNTHETIC")
        self.assertEqual(self.frappe.legacy_calls, [])
        self.assertEqual(self.frappe.calculation_calls, [])

    def test_both_calculation_names_are_restricted_to_test_site(self):
        self.frappe.local.site = "production.example"
        for method in (self.api.calculate_cutting, self.api.legacy_cutting_values):
            with self.assertRaises(PermissionError):
                method(item_codes="SYNTHETIC")
        self.assertEqual(self.frappe.reads, [])
        self.assertEqual(self.frappe.calculation_calls, [])


class BootTests(unittest.TestCase):
    def test_boot_hook_refreshes_existing_cached_flag_in_both_directions(self):
        framework = FakeFrappe()
        api = load_api(framework)
        spec = importlib.util.spec_from_file_location("demo.boot", ROOT / "demo/boot.py")
        boot = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"demo.api": api}):
            spec.loader.exec_module(boot)
        bootinfo = SimpleNamespace(demo_cutting_enabled=True)
        boot.boot_session(bootinfo)
        self.assertFalse(bootinfo.demo_cutting_enabled)
        api.activate()
        boot.boot_session(bootinfo)
        self.assertTrue(bootinfo.demo_cutting_enabled)
        api.deactivate()
        boot.boot_session(bootinfo)
        self.assertFalse(bootinfo.demo_cutting_enabled)
        hooks = {}
        exec(compile((ROOT / "demo/hooks.py").read_text(), "demo/hooks.py", "exec"), hooks)
        self.assertEqual(hooks["extend_bootinfo"], "demo.boot.boot_session")


if __name__ == "__main__":
    unittest.main()
