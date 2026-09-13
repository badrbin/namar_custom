from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch


PATH = Path(__file__).resolve().parents[1] / "namar_custom/followups/approval_index_permission_events.py"


class ApprovalIndexPermissionEventTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.callbacks = []
        self.frappe = types.ModuleType("frappe")
        self.frappe.clear_cache = Mock()
        self.frappe.db = types.SimpleNamespace(
            after_commit=types.SimpleNamespace(add=self.callbacks.append), commit=Mock(), rollback=Mock(),
        )

        def whitelist(**options):
            def decorate(function):
                function.whitelist_options = options
                return function
            return decorate

        self.frappe.whitelist = whitelist
        self.manager = types.ModuleType("frappe.core.page.permission_manager.permission_manager")
        self.user_permission = types.ModuleType("frappe.core.doctype.user_permission.user_permission")
        self.index = types.ModuleType("namar_custom.followups.approval_index")
        self.index.request_rebuild = Mock(side_effect=lambda reason: self.events.append(("invalidate", reason)))
        self.cases = (
            ("update_role_permission", self.manager, "update", {
                "doctype": "Material Request", "role": "Stock User", "permlevel": 0,
                "ptype": "read", "value": 0, "if_owner": 1,
            }, {"doctype": "Material Request"}),
            ("remove_role_permission", self.manager, "remove", {
                "doctype": "Material Request", "role": "Stock User", "permlevel": 0, "if_owner": 1,
            }, {"doctype": "Material Request"}),
            ("reset_role_permissions", self.manager, "reset", {
                "doctype": "Material Request",
            }, {"doctype": "Material Request"}),
            ("clear_user_permissions", self.user_permission, "clear_user_permissions", {
                "user": "fixture@example.invalid", "for_doctype": "Company",
            }, {"user": "fixture@example.invalid"}),
            ("update_user_permissions", self.user_permission, "add_user_permissions", {
                "data": {"user": "fixture@example.invalid", "applicable_doctypes": ["Material Request"]},
            }, {"user": "fixture@example.invalid"}),
        )
        for _, module, native_name, _, _ in self.cases:
            setattr(module, native_name, Mock())
        modules = {"frappe": self.frappe, "namar_custom.followups.approval_index": self.index}
        for name in (
            "frappe.core", "frappe.core.page", "frappe.core.page.permission_manager",
            "frappe.core.doctype", "frappe.core.doctype.user_permission",
        ):
            modules[name] = types.ModuleType(name)
        modules["frappe.core.page.permission_manager"].permission_manager = self.manager
        modules["frappe.core.doctype.user_permission"].user_permission = self.user_permission
        modules[self.manager.__name__] = self.manager
        modules[self.user_permission.__name__] = self.user_permission
        self.module_patch = patch.dict(sys.modules, modules)
        self.module_patch.start()
        spec = importlib.util.spec_from_file_location("approval_index_permission_events_under_test", PATH)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def tearDown(self):
        self.module_patch.stop()

    def test_native_arguments_result_and_transactional_invalidation(self):
        for wrapper, module, native_name, arguments, _ in self.cases:
            with self.subTest(endpoint=wrapper):
                self.events.clear()
                native = getattr(module, native_name)
                token = object()
                native.side_effect = lambda **kwargs: (self.events.append(("native", kwargs)), token)[1]
                result = getattr(self.module, wrapper)(**arguments)
                self.assertIs(result, token)
                native.assert_called_once_with(**arguments)
                self.assertEqual(self.events, [("native", arguments), ("invalidate", "native_permission_rpc_changed")])
        self.frappe.db.commit.assert_not_called()
        self.frappe.db.rollback.assert_not_called()

    def test_native_failure_never_invalidates_or_masks_exception(self):
        for wrapper, module, native_name, arguments, _ in self.cases:
            with self.subTest(endpoint=wrapper):
                failure = PermissionError("denied by native permission manager")
                getattr(module, native_name).side_effect = failure
                with self.assertRaises(PermissionError) as raised:
                    getattr(self.module, wrapper)(**arguments)
                self.assertIs(raised.exception, failure)
        self.index.request_rebuild.assert_not_called()
        self.assertEqual(self.callbacks, [])
        self.frappe.db.commit.assert_not_called()

    def test_invalidation_failure_propagates_for_request_rollback(self):
        self.index.request_rebuild.side_effect = RuntimeError("index generation unavailable")
        for wrapper, _, _, arguments, _ in self.cases:
            with self.subTest(endpoint=wrapper):
                with self.assertRaisesRegex(RuntimeError, "index generation unavailable"):
                    getattr(self.module, wrapper)(**arguments)
        self.frappe.db.commit.assert_not_called()

    def test_cache_refresh_is_targeted_and_after_commit(self):
        for wrapper, _, _, arguments, expected in self.cases:
            with self.subTest(endpoint=wrapper):
                self.callbacks.clear()
                self.frappe.clear_cache.reset_mock()
                getattr(self.module, wrapper)(**arguments)
                self.frappe.clear_cache.assert_not_called()
                self.assertEqual(len(self.callbacks), 1)
                self.callbacks[0]()
                self.frappe.clear_cache.assert_called_once_with(**expected)

    def test_json_payload_preserved_for_native(self):
        payload = json.dumps({"user": "fixture@example.invalid", "applicable_doctypes": []})
        self.module.update_user_permissions(payload)
        self.user_permission.add_user_permissions.assert_called_once_with(data=payload)
        self.callbacks[0]()
        self.frappe.clear_cache.assert_called_once_with(user="fixture@example.invalid")

    def test_all_mutating_overrides_are_post_only(self):
        for wrapper, _, _, _, _ in self.cases:
            with self.subTest(endpoint=wrapper):
                self.assertEqual(getattr(self.module, wrapper).whitelist_options, {"methods": ["POST"]})


if __name__ == "__main__":
    unittest.main(verbosity=2)
