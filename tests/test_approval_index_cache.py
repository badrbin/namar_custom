from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from namar_custom.followups.approval_index_cache import fresh_permission_cache


class FreshPermissionCacheTests(unittest.TestCase):
    def setUp(self):
        self.backend = Mock()
        self.backend.hget.return_value = "stale-shared-grant"
        self.backend.get_value.return_value = "stale-shared-document"
        self.original_roles = {"previous": "roles"}
        self.original_values = {("User", "revoked", "enabled"): 1}
        self.original_settings = {"apply_strict_user_permissions": 0}
        self.frappe = SimpleNamespace(
            cache=self.backend, request=None,
            local=SimpleNamespace(role_permissions=self.original_roles, system_settings=self.original_settings),
            db=SimpleNamespace(value_cache=self.original_values),
        )

    def test_security_hashes_start_fresh_and_generated_values_never_reach_redis(self):
        with fresh_permission_cache(self.frappe):
            for name in ("roles", "user_permissions", "doctype_meta", "user_doc"):
                self.assertIsNone(self.frappe.cache.hget(name, "employee"))
                self.assertEqual(self.frappe.cache.hget(name, "employee", lambda: ["fresh"]), ["fresh"])
                self.assertEqual(self.frappe.cache.hget(name, "employee", lambda: self.fail("must reuse local")), ["fresh"])
                self.frappe.cache.hset(name, "other", [])
                self.assertTrue(self.frappe.cache.hexists(name, "other"))
                self.frappe.cache.hdel(name, "employee")
                self.assertIsNone(self.frappe.cache.hget(name, "employee"))
            self.assertEqual(self.frappe.local.role_permissions, {})
            self.assertIsNone(self.frappe.local.system_settings)
            self.assertEqual(self.frappe.db.value_cache, {})
        self.backend.hget.assert_not_called()
        self.backend.hset.assert_not_called()
        self.backend.hdel.assert_not_called()

    def test_user_and_system_settings_documents_ignore_old_cache_and_stay_local(self):
        with fresh_permission_cache(self.frappe):
            for key in ("document_cache::User::employee", "document_cache::System Settings::System Settings", "active_domains"):
                self.assertIsNone(self.frappe.cache.get_value(key))
                self.frappe.cache.set_value(key, {"fresh": True})
                self.assertEqual(self.frappe.cache.get_value(key), {"fresh": True})
        self.backend.get_value.assert_not_called()
        self.backend.set_value.assert_not_called()

    def test_unrelated_caches_and_distributed_locks_keep_native_backend(self):
        with fresh_permission_cache(self.frappe):
            self.assertEqual(self.frappe.cache.hget("workflow", "Material Request"), "stale-shared-grant")
            self.frappe.cache.get_value("unrelated-key")
            self.frappe.cache.lock("index-worker")
            self.assertIs(self.frappe.cache(), self.frappe.cache)
        self.backend.hget.assert_called_once_with("workflow", "Material Request", generator=None, shared=False)
        self.backend.get_value.assert_called_once()
        self.backend.lock.assert_called_once_with("index-worker")

    def test_restores_objects_and_missing_attributes_even_on_failure(self):
        with self.assertRaisesRegex(RuntimeError, "worker failed"):
            with fresh_permission_cache(self.frappe):
                raise RuntimeError("worker failed")
        self.assertIs(self.frappe.cache, self.backend)
        self.assertIs(self.frappe.local.role_permissions, self.original_roles)
        self.assertIs(self.frappe.local.system_settings, self.original_settings)
        self.assertIs(self.frappe.db.value_cache, self.original_values)
        self.assertFalse(hasattr(self.frappe.local, "meta_cache"))
        self.assertFalse(hasattr(self.frappe.local, "user_permissions"))
        self.backend.flushall.assert_not_called()
        self.backend.flushdb.assert_not_called()
        self.backend.delete_keys.assert_not_called()

    def test_each_worker_slice_reads_fresh_again(self):
        with fresh_permission_cache(self.frappe):
            self.frappe.cache.hset("roles", "employee", ["old slice"])
        with fresh_permission_cache(self.frappe):
            self.assertIsNone(self.frappe.cache.hget("roles", "employee"))

    def test_http_request_cannot_replace_global_cache(self):
        self.frappe.request = object()
        with self.assertRaisesRegex(RuntimeError, "background workers"):
            with fresh_permission_cache(self.frappe):
                self.fail("must not enter")
        self.assertIs(self.frappe.cache, self.backend)
        self.assertIs(self.frappe.local.role_permissions, self.original_roles)


if __name__ == "__main__":
    unittest.main()
