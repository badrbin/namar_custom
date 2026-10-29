"""Exercise clone boundaries and real SQL effects without a Frappe site."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sqlite3
import types
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
TARGET = "namar15-falkenstein.frappe.cloud"


class Database:
    instances = []

    def __init__(self):
        self.connection = sqlite3.connect(":memory:")
        self.instances.append(self)
        self.inspections = []
        self.writes = []
        self.commit = Mock(side_effect=AssertionError("hook must not commit"))

    def table_exists(self, doctype, *, cached=True):
        self.inspections.append(("table", doctype, cached))
        return self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", ("tab" + doctype,),
        ).fetchone() is not None

    def get_table_columns(self, doctype):
        self.inspections.append(("columns", doctype))
        return [row[1] for row in self.connection.execute(f"PRAGMA table_info(`tab{doctype}`)")]

    def sql(self, query, values):
        self.writes.append((query, values))
        return self.connection.execute(query.replace("%s", "?"), values).fetchall()

    def rows(self, doctype):
        return self.connection.execute(f"SELECT * FROM `tab{doctype}` ORDER BY name").fetchall()


def load_hook(*, site=TARGET, flag=1, db=None):
    frappe = types.ModuleType("frappe")
    frappe.local = types.SimpleNamespace(site=site)
    frappe.conf = {} if flag is None else {"namar_falkenstein_clone_isolation": flag}
    frappe.db = db if db is not None else Database()
    frappe.clear_cache = Mock()
    spec = importlib.util.spec_from_file_location("clone_isolation_under_test", ROOT / "namar_custom/clone_isolation.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict("sys.modules", {"frappe": frappe}):
        spec.loader.exec_module(module)
    return module, frappe


def populated_database():
    db = Database()
    db.connection.executescript("""
        CREATE TABLE `tabCompany` (name TEXT, custom_zatca_invoice_enabled INTEGER, tax_id TEXT, modified TEXT);
        INSERT INTO `tabCompany` VALUES ('A',1,'tax-a','unchanged'), ('B',0,'tax-b','unchanged');
        CREATE TABLE `tabEmail Account` (name TEXT, enable_outgoing INTEGER, enable_incoming INTEGER, email_id TEXT);
        INSERT INTO `tabEmail Account` VALUES ('mail',1,1,'clone@example.invalid'), ('off',0,0,'off@example.invalid');
        CREATE TABLE `tabNotification` (name TEXT, enabled INTEGER, subject TEXT);
        INSERT INTO `tabNotification` VALUES ('N',1,'Keep this content');
        CREATE TABLE `tabWebhook` (name TEXT, enabled INTEGER, request_url TEXT);
        INSERT INTO `tabWebhook` VALUES ('W',1,'https://example.invalid/keep');
        CREATE TABLE `tabSales Invoice` (name TEXT, docstatus INTEGER, grand_total INTEGER);
        INSERT INTO `tabSales Invoice` VALUES ('invoice',1,500);
    """)
    return db


class CloneIsolationTests(unittest.TestCase):
    def tearDown(self):
        for database in Database.instances:
            database.connection.close()
        Database.instances.clear()

    def test_other_sites_are_untouched_even_with_flag_enabled(self):
        for site in ("erp.namar.net", "zawaya7.frappe.cloud", "testnamar.u.frappe.cloud", "namar16-trial.k.frappe.cloud", "namar15-frankfurt.f.frappe.cloud", "namar15-falkenstein.fsn.frappe.cloud", "namar15-falkenstein.k.frappe.cloud", "other.f.frappe.cloud", None):
            with self.subTest(site=site):
                hook, frappe = load_hook(site=site, db=Mock())
                self.assertEqual(hook.disable_copied_integrations()["reason"], "different_site")
                self.assertEqual(frappe.db.mock_calls, [])
                frappe.clear_cache.assert_not_called()

    def test_frankfurt_flag_cannot_enable_this_clone(self):
        hook, frappe = load_hook(flag=None, db=Mock())
        frappe.conf["namar_frankfurt_clone_isolation"] = 1
        self.assertEqual(hook.disable_copied_integrations()["reason"], "flag_disabled")
        self.assertEqual(frappe.db.mock_calls, [])
        frappe.clear_cache.assert_not_called()

    def test_missing_or_disabled_flag_does_not_read_or_write_database(self):
        for flag in (None, False, 0, "0", "false", "true", "yes", 2, "", {}):
            with self.subTest(flag=flag):
                hook, frappe = load_hook(flag=flag, db=Mock())
                self.assertEqual(hook.disable_copied_integrations()["reason"], "flag_disabled")
                self.assertEqual(frappe.db.mock_calls, [])
                frappe.clear_cache.assert_not_called()

    def test_explicit_enabled_forms_disable_only_allowed_flags(self):
        for flag in (True, 1, "1"):
            with self.subTest(flag=flag):
                db = populated_database()
                hook, frappe = load_hook(flag=flag, db=db)
                outcome = hook.disable_copied_integrations()
                self.assertEqual(outcome["state"], "isolated")
                self.assertEqual(db.rows("Company"), [('A',0,'tax-a','unchanged'), ('B',0,'tax-b','unchanged')])
                self.assertEqual(db.rows("Email Account"), [('mail',0,0,'clone@example.invalid'), ('off',0,0,'off@example.invalid')])
                self.assertEqual(db.rows("Notification"), [('N',0,'Keep this content')])
                self.assertEqual(db.rows("Webhook"), [('W',0,'https://example.invalid/keep')])
                self.assertEqual(db.rows("Sales Invoice"), [('invoice',1,500)])
                self.assertEqual(len(db.writes), 4)
                frappe.clear_cache.assert_called_once_with()
                db.commit.assert_not_called()

    def test_repeat_run_does_not_change_rows_already_disabled(self):
        db = populated_database()
        hook, _ = load_hook(db=db)
        hook.disable_copied_integrations()
        changes = db.connection.total_changes
        hook.disable_copied_integrations()
        self.assertEqual(db.connection.total_changes, changes)
        db.commit.assert_not_called()

    def test_new_site_without_tables_is_safe(self):
        hook, frappe = load_hook()
        self.assertEqual(hook.disable_copied_integrations(), {"state": "isolated", "guarded_fields": {}})
        self.assertEqual(frappe.db.writes, [])
        self.assertTrue(all(item[2] is False for item in frappe.db.inspections))
        frappe.clear_cache.assert_not_called()

    def test_partial_schema_updates_only_available_columns(self):
        db = Database()
        db.connection.executescript("""
            CREATE TABLE `tabCompany` (name TEXT, tax_id TEXT);
            INSERT INTO `tabCompany` VALUES ('A','tax-a');
            CREATE TABLE `tabEmail Account` (name TEXT, enable_outgoing INTEGER, email_id TEXT);
            INSERT INTO `tabEmail Account` VALUES ('mail',1,'clone@example.invalid');
        """)
        hook, _ = load_hook(db=db)
        result = hook.disable_copied_integrations()
        self.assertEqual(result["guarded_fields"], {"Email Account": ["enable_outgoing"]})
        self.assertEqual(db.rows("Company"), [('A','tax-a')])
        self.assertEqual(db.rows("Email Account"), [('mail',0,'clone@example.invalid')])

    def test_database_error_aborts_instead_of_claiming_isolation(self):
        db = populated_database()
        db.sql = Mock(side_effect=RuntimeError("write failed"))
        hook, frappe = load_hook(db=db)
        with self.assertRaisesRegex(RuntimeError, "write failed"):
            hook.disable_copied_integrations()
        db.sql.assert_called_once()
        frappe.clear_cache.assert_not_called()
        db.commit.assert_not_called()

    def test_hook_is_before_migrate_and_existing_after_hook_is_preserved(self):
        namespace = {}
        exec((ROOT / "namar_custom/hooks.py").read_text(), namespace)
        self.assertIn("namar_custom.clone_isolation.disable_copied_integrations", namespace["before_migrate"])
        self.assertEqual(namespace["after_migrate"], [
            "namar_custom.clone_isolation.disable_copied_integrations",
            "namar_custom.followups.approval_index.invalidate_after_migrate",
        ])


if __name__ == "__main__":
    unittest.main()
