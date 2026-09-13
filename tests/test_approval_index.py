"""Fast structural/transaction tests; live Frappe tests cover actual SQL/hooks."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import MagicMock, patch


def load_engine():
    frappe = types.ModuleType("frappe")
    frappe.whitelist = lambda **kwargs: lambda fn: fn
    frappe.conf = {"followup_approval_index_enabled": True}
    frappe.session = types.SimpleNamespace(user="employee@example.com")
    frappe.local = types.SimpleNamespace()
    frappe.flags = types.SimpleNamespace()
    frappe.PermissionError = PermissionError
    frappe.ValidationError = ValueError
    frappe.throw = lambda message, error: (_ for _ in ()).throw(error(message))
    frappe.db = MagicMock()
    frappe.enqueue = MagicMock()
    frappe.publish_realtime = MagicMock()
    frappe.log_error = MagicMock()
    frappe.only_for = MagicMock()
    frappe.cache = MagicMock()
    spec = importlib.util.spec_from_file_location(
        "approval_index_under_test", Path(__file__).parents[1] / "namar_custom/followups/approval_index.py",
    )
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"frappe": frappe}):
        spec.loader.exec_module(module)
    return module, frappe


class ApprovalIndexTests(unittest.TestCase):
    def setUp(self):
        self.index, self.frappe = load_engine()

    def ready_sql(self, query, values=None, as_dict=False):
        self.assertTrue(query.lstrip().startswith("SELECT"), query)
        if "SELECT * FROM `tabNamar Approval Index Control`" in query:
            return [{"name": "current", "epoch": 4, "scan_complete": 1}]
        if "SELECT epoch,scan_complete FROM" in query:
            return [{"epoch": 4, "scan_complete": 1}]
        if "WHERE state=" in query:
            return []
        if "COUNT(*)" in query:
            return [(3,)]
        if "a.projection" in query:
            return [{"name": "WA-1", "reference_name": "MR-1", "routing": '{"mode":"Targets"}', "projection": '{"title":"A"}'}]
        raise AssertionError(query)

    def test_count_read_never_enqueues_or_hydrates(self):
        self.frappe.db.sql.side_effect = self.ready_sql
        self.assertEqual(self.index.read_counts("employee@example.com"), {
            "state": "ready", "generation": 4, "message": "", "open": 3,
        })
        self.frappe.enqueue.assert_not_called()
        self.frappe.db.commit.assert_not_called()
        self.assertEqual(len(self.frappe.db.sql.call_args_list), 5)
        query = self.frappe.db.sql.call_args_list[-2][0][0]
        self.assertIn("r.for_user=%(user)s", query)
        self.assertIn("w.status='Open'", query)
        self.assertIn("r.revision=a.built_revision", query)
        self.assertIn("LOCK IN SHARE MODE", self.frappe.db.sql.call_args_list[-1][0][0])

    def test_page_limits_and_search_are_parameterized_read_only(self):
        self.frappe.db.sql.side_effect = self.ready_sql
        result = self.index.read_page("employee@example.com", "x%' OR 1=1", "document", -3, 1000)
        self.assertEqual(result["items"][0]["reference_title"], "A")
        self.assertEqual(result["items"][0]["routing"]["mode"], "Targets")
        query, params = self.frappe.db.sql.call_args_list[-1][0]
        self.assertEqual(params["length"], 101)
        self.assertEqual(params["start"], 0)
        self.assertNotIn("OR 1=1", query)
        self.assertIn("w.`reference_name` LIKE %(search)s", query)
        self.frappe.enqueue.assert_not_called()

    def test_guest_and_cross_user_never_query_private_projection(self):
        for user in ("Guest", "different@example.com", ""):
            with self.assertRaises(PermissionError):
                self.index.read_counts(user)
        self.frappe.db.sql.assert_not_called()

    def test_disabled_has_no_sql_work(self):
        self.frappe.conf = {}
        result = self.index.read_counts("employee@example.com")
        self.assertIsNone(result["open"])
        self.assertEqual(result["state"], "disabled")
        self.frappe.db.sql.assert_not_called()

    def test_shadow_build_does_not_enable_serving_flag(self):
        self.frappe.conf = {"followup_approval_index_build_enabled": True}
        self.assertTrue(self.index.build_enabled())
        self.assertFalse(self.index.enabled())

    def test_shadow_build_never_broadcasts_refresh_to_employees(self):
        self.frappe.conf = {"followup_approval_index_build_enabled": True}
        self.index._emit_changed()
        self.index._dispatch_after_commit()
        self.frappe.enqueue.assert_called_once()
        self.frappe.publish_realtime.assert_not_called()

    def test_pending_cohort_uses_indexed_native_membership_not_other_users(self):
        clause, values = self.index._cohort_clause("employee@example.com")
        self.assertEqual(values, ("employee@example.com",) * 3)
        self.assertIn("old_r.action_name=a.name", clause)
        self.assertIn("`tabWorkflow Action Permitted Role`", clause)
        self.assertIn("h.parent=%s AND h.role=p.role", clause)
        self.assertEqual(self.index._cohort_clause("Administrator"), ("", ()))
        self.frappe.db.sql.assert_not_called()

    def test_repeated_global_changes_in_one_transaction_increment_epoch_once(self):
        self.frappe.db.sql.return_value = [{"epoch": 2}]
        self.index.request_rebuild("one")
        self.index.request_rebuild("two")
        writes = [call for call in self.frappe.db.sql.call_args_list if call[0][0].startswith("UPDATE")]
        self.assertEqual(len(writes), 1)
        self.index._reset_dispatch()
        self.index.request_rebuild("next_transaction")
        writes = [call for call in self.frappe.db.sql.call_args_list if call[0][0].startswith("UPDATE")]
        self.assertEqual(len(writes), 2)

    def test_rename_signature_accepts_native_old_new_merge_arguments(self):
        doc = types.SimpleNamespace(doctype="Material Request", name="MR-new")
        with patch.object(self.index, "request_rebuild") as rebuild:
            self.index.on_document_rename(doc, "after_rename", "MR-old", "MR-new", False)
            rebuild.assert_called_once_with("reference_renamed")

    def test_pending_or_error_never_returns_a_zero_or_cached_items(self):
        for state in ("Pending", "Error"):
            with self.subTest(state=state):
                def sql(query, values=None, as_dict=False):
                    if "SELECT *" in query:
                        return [{"epoch": 8, "scan_complete": 1}]
                    return [("WA-pending",)] if f"state='{state}'" in query else []
                self.frappe.db.sql.side_effect = sql
                result = self.index.read_page("employee@example.com")
                self.assertIsNone(result["open"])
                self.assertEqual(result["items"], [])
                self.assertEqual(result["state"], "updating" if state == "Pending" else "error")

    def test_generation_seed_is_not_ready_even_when_no_pending_rows(self):
        self.frappe.db.sql.return_value = [{"epoch": 9, "scan_complete": 0}]
        self.assertEqual(self.index.read_counts("employee@example.com")["state"], "updating")
        self.assertEqual(self.frappe.db.sql.call_count, 1)

    def test_count_revoked_epoch_mid_read_returns_unknown_not_stale_count(self):
        def sql(query, values=None, as_dict=False):
            if "LOCK IN SHARE MODE" in query:
                return [{"epoch": 5, "scan_complete": 0}]
            return self.ready_sql(query, values, as_dict)
        self.frappe.db.sql.side_effect = sql
        result = self.index.read_counts("employee@example.com")
        self.assertEqual(result["state"], "updating")
        self.assertIsNone(result["open"])

    def test_page_initial_read_does_not_lock_control_during_hydration(self):
        self.frappe.db.sql.side_effect = self.ready_sql
        self.index.read_page("employee@example.com")
        self.assertFalse(any("LOCK IN SHARE MODE" in call[0][0] for call in self.frappe.db.sql.call_args_list))

    def test_current_fence_rejects_changed_closed_missing_or_pending_action(self):
        row = {"name": "WA-1", "epoch": 4, "state": "Ready", "built_revision": 3, "requested_revision": 3, "native_status": "Open"}
        for candidate in (row, {**row, "epoch": 5}, {**row, "requested_revision": 4}, {**row, "native_status": "Completed"}, {**row, "state": "Pending"}, None):
            with self.subTest(candidate=candidate):
                self.frappe.db.sql.reset_mock()
                self.frappe.db.sql.side_effect = [[{"epoch": 4, "scan_complete": 1}], [candidate] if candidate else []]
                result = self.index.verify_snapshot(4, {"WA-1": 3})
                self.assertEqual(result, candidate == row)
                for call in self.frappe.db.sql.call_args_list:
                    self.assertIn("LOCK IN SHARE MODE", call[0][0])
                self.assertEqual(self.frappe.db.sql.call_args_list[1][0][1], ("WA-1",))

    def test_current_fence_is_bounded_and_rejects_invalid_tokens_without_queries(self):
        self.assertFalse(self.index.verify_snapshot(None))
        self.assertFalse(self.index.verify_snapshot(1, {str(n): 1 for n in range(102)}))
        self.assertFalse(self.index.verify_snapshot(1, {"WA-1": 0}))
        self.frappe.db.sql.assert_not_called()

    def test_cas_rejects_changed_policy_source_and_deleted_action(self):
        snapshot = {"epoch": 5, "requested_revision": 7}
        current = dict(snapshot)
        self.assertTrue(self.index.publication_matches({"epoch": 5}, current, snapshot))
        self.assertFalse(self.index.publication_matches({"epoch": 6}, current, snapshot))
        self.assertFalse(self.index.publication_matches({"epoch": 5}, {**current, "requested_revision": 8}, snapshot))
        self.assertFalse(self.index.publication_matches({"epoch": 5}, None, snapshot))
        self.assertFalse(self.index.publication_matches(None, current, snapshot))

    def test_stale_publish_makes_no_recipient_writes(self):
        self.frappe.db.sql.side_effect = [[{"epoch": 6}], [{"epoch": 5, "requested_revision": 7}]]
        snapshot = {"name": "WA-1", "epoch": 5, "requested_revision": 7}
        self.assertFalse(self.index._publish(snapshot, {"state": "ready", "recipients": ["a@example.com"]}))
        self.assertEqual(self.frappe.db.sql.call_count, 2)
        for call in self.frappe.db.sql.call_args_list:
            self.assertTrue(call[0][0].startswith("SELECT"))

    def test_publish_deduplicates_users_and_replaces_atomically(self):
        self.frappe.db.sql.side_effect = [[{"epoch": 5}], [{"epoch": 5, "requested_revision": 7}], [], [], []]
        snapshot = {"name": "WA-1", "epoch": 5, "requested_revision": 7}
        self.assertTrue(self.index._publish(snapshot, {"state": "ready", "recipients": ["a@example.com", "a@example.com"], "title": "A"}))
        queries = self.frappe.db.sql.call_args_list
        self.assertIn("DELETE FROM", queries[2][0][0])
        self.assertIn("INSERT INTO", queries[3][0][0])
        self.assertEqual(len(queries[3][0][1]), 5)
        self.assertEqual(queries[4][0][1][0], "Ready")
        self.frappe.db.commit.assert_not_called()

    def test_no_recipient_exception_settles_without_broad_fallback(self):
        self.frappe.db.sql.side_effect = [[{"epoch": 5}], [{"epoch": 5, "requested_revision": 7}], [], []]
        snapshot = {"name": "WA-1", "epoch": 5, "requested_revision": 7}
        self.index._publish(snapshot, {"state": "error", "reason": "no_eligible_recipients"})
        self.assertEqual(self.frappe.db.sql.call_args_list[-1][0][1][0], "Excluded")
        self.assertFalse(any("INSERT INTO" in call[0][0] for call in self.frappe.db.sql.call_args_list))

    def test_recipient_key_is_stable_and_collision_safe_at_field_boundary(self):
        self.assertEqual(self.index.recipient_key("A", "B"), self.index.recipient_key("A", "B"))
        self.assertNotEqual(self.index.recipient_key("AB", "C"), self.index.recipient_key("A", "BC"))

    def test_reference_event_never_bumps_global_epoch(self):
        doc = types.SimpleNamespace(doctype="Material Request", name="MR-1")
        with patch.object(self.index, "_dirty_reference") as dirty, patch.object(self.index, "request_rebuild") as rebuild:
            self.index.on_document_change(doc, "on_change")
            dirty.assert_called_once_with("Material Request", "MR-1")
            rebuild.assert_not_called()

    def test_auto_share_only_invalidates_shared_reference(self):
        doc = types.SimpleNamespace(doctype="DocShare", name="share-1", get=lambda key: {"share_doctype": "Material Request", "share_name": "MR-2"}.get(key))
        with patch.object(self.index, "_dirty_reference") as dirty, patch.object(self.index, "request_rebuild") as rebuild:
            self.index.on_document_change(doc, "after_delete")
            dirty.assert_called_once_with("Material Request", "MR-2")
            rebuild.assert_not_called()

    def test_deletion_purges_without_upserting_or_recreating(self):
        doc = types.SimpleNamespace(doctype="Material Request", name="MR-1")
        with patch.object(self.index, "_purge_reference", return_value=True) as purge, patch.object(self.index, "_schedule") as schedule, patch.object(self.index, "_dirty_reference") as dirty:
            self.index.on_document_change(doc, "after_delete")
            purge.assert_called_once_with("Material Request", "MR-1")
            schedule.assert_called_once()
            dirty.assert_not_called()

    def test_dispatch_is_after_commit_and_coalesced(self):
        self.index._schedule()
        self.index._schedule()
        self.assertEqual(self.frappe.db.after_commit.add.call_count, 1)
        self.frappe.enqueue.assert_not_called()
        self.frappe.db.after_commit.add.call_args[0][0]()
        self.frappe.enqueue.assert_called_once()
        self.assertFalse(self.frappe.local.namar_approval_index_dispatch)

    def test_queue_outage_does_not_raise_after_committed_business_save(self):
        self.frappe.enqueue.side_effect = ConnectionError("offline")
        self.index._dispatch_after_commit()
        self.frappe.log_error.assert_called_once()
        self.frappe.db.sql.assert_not_called()

    def test_worker_never_waits_for_another_evaluator(self):
        self.frappe.cache.return_value.lock.return_value.acquire.return_value = False
        result = self.index.process_pending()
        self.assertEqual(result["state"], "busy")
        self.frappe.db.sql.assert_not_called()
        self.frappe.cache.return_value.lock.return_value.acquire.assert_called_once_with(blocking=False)

    def test_scheduler_only_enqueues_durable_pending_no_evaluation(self):
        with patch.object(self.index, "_has_pending_work", return_value=True), patch.object(self.index, "_enqueue") as enqueue:
            self.index.recover_pending()
            enqueue.assert_called_once()
        self.frappe.db.sql.assert_not_called()


if __name__ == "__main__":
    unittest.main()
