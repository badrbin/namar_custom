"""Deployment-fingerprint and stale-worker fences, without live API writes."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import patch

from test_approval_index import load_engine


USER = "employee@example.com"


class ApprovalEngineRevisionTests(unittest.TestCase):
    def setUp(self):
        self.index, self.frappe = load_engine()

    def control(self, **values):
        return {"name": "current", "epoch": 4, "scan_complete": 1,
                "engine_revision": self.index.ENGINE_REVISION, **values}

    def ready_sql(self, query, values=None, as_dict=False):
        self.assertTrue(query.lstrip().startswith("SELECT"), query)
        if "tabNamar Approval Index Control" in query:
            return [self.control()]
        if "WHERE state=" in query:
            return []
        if "COUNT(*)" in query:
            return [(3,)]
        if "SELECT a.routing,a.built_revision" in query:
            return [{"routing": '{"mode":"Targets"}', "revision": 3}]
        if "a.projection" in query:
            return [{"name": "WA-1", "reference_name": "MR-1", "routing": '{"mode":"Targets"}',
                     "projection": '{"title":"A"}', "_index_revision": 3}]
        if "w.status AS native_status" in query:
            return [{"name": "WA-1", "epoch": 4, "state": "Ready", "built_revision": 3,
                     "requested_revision": 3, "native_status": "Open"}]
        raise AssertionError(query)

    def assert_read_only_without_dispatch(self):
        for call in self.frappe.db.sql.call_args_list:
            self.assertTrue(call.args[0].lstrip().startswith("SELECT"), call.args[0])
        self.frappe.enqueue.assert_not_called()
        self.frappe.db.commit.assert_not_called()
        self.frappe.db.after_commit.add.assert_not_called()

    def test_mismatched_or_unstamped_engine_returns_unknown_not_old_projection(self):
        for stamp in (None, "", "previous-code-version"):
            with self.subTest(stamp=stamp):
                self.frappe.db.sql.reset_mock()
                self.frappe.db.sql.return_value = [self.control(engine_revision=stamp)]
                counts = self.index.read_counts(USER)
                page = self.index.read_page(USER)
                self.assertEqual(counts["state"], "updating")
                self.assertIsNone(counts["open"])
                self.assertEqual(page["state"], "updating")
                self.assertIsNone(page["total"])
                self.assertEqual(page["items"], [])
                self.assertEqual(self.frappe.db.sql.call_count, 2)
                self.assertTrue(all("tabNamar Approval Index Control" in call.args[0]
                                    for call in self.frappe.db.sql.call_args_list))
                self.assert_read_only_without_dispatch()

    def test_revision_change_at_final_current_read_rejects_old_ready_count(self):
        def sql(query, values=None, as_dict=False):
            if "LOCK IN SHARE MODE" in query:
                return [self.control(engine_revision="newer-runtime")]
            return self.ready_sql(query, values, as_dict)

        self.frappe.db.sql.side_effect = sql
        result = self.index.read_counts(USER)
        self.assertEqual(result["state"], "updating")
        self.assertIsNone(result["open"])
        self.assert_read_only_without_dispatch()

    def test_current_fence_rejects_runtime_revision_before_action_read(self):
        self.frappe.db.sql.return_value = [self.control(engine_revision="different-runtime")]
        self.assertFalse(self.index.verify_snapshot(4, {"WA-1": 3}))
        self.assertEqual(self.frappe.db.sql.call_count, 1)
        self.assertIn("engine_revision", self.frappe.db.sql.call_args.args[0])
        self.assertIn("LOCK IN SHARE MODE", self.frappe.db.sql.call_args.args[0])
        self.assert_read_only_without_dispatch()

    def test_adoption_is_serialized_and_invalidates_generation_exactly_once(self):
        stored = self.control(engine_revision="old-runtime", scan_cursor="last-action")

        def sql(query, values=None, as_dict=False):
            if query.startswith("SELECT"):
                self.assertIn("FOR UPDATE", query)
                return [deepcopy(stored)]
            self.assertTrue(query.startswith("UPDATE"), query)
            self.assertIn("epoch=epoch+1", query)
            self.assertIn("scan_cursor='',scan_complete=0", query)
            self.assertEqual(values, (self.index.ENGINE_REVISION, "current"))
            stored.update(engine_revision=values[0], epoch=stored["epoch"] + 1, scan_cursor="", scan_complete=0)
            return []

        self.frappe.db.sql.side_effect = sql
        self.assertTrue(self.index._adopt_runtime_revision())
        self.assertFalse(self.index._adopt_runtime_revision())
        writes = [call for call in self.frappe.db.sql.call_args_list if call.args[0].startswith("UPDATE")]
        self.assertEqual(len(writes), 1)
        self.assertEqual(stored["epoch"], 5)
        self.assertEqual(stored["engine_revision"], self.index.ENGINE_REVISION)
        self.assertEqual(self.frappe.db.after_commit.add.call_count, 1)
        self.frappe.enqueue.assert_not_called()
        self.frappe.db.commit.assert_not_called()

    def test_scheduler_adopts_then_stops_without_inline_scan_or_evaluation(self):
        with (
            patch.object(self.index, "_adopt_runtime_revision", return_value=True) as adopt,
            patch.object(self.index, "_has_pending_work") as pending,
            patch.object(self.index, "_seed_batch") as seed,
            patch.object(self.index, "process_pending") as process,
            patch.object(self.index, "_enqueue") as enqueue,
        ):
            self.index.recover_pending()
        adopt.assert_called_once_with()
        pending.assert_not_called()
        seed.assert_not_called()
        process.assert_not_called()
        enqueue.assert_not_called()

    def test_stale_in_place_scheduler_cannot_adopt_old_stamp_over_new_disk(self):
        with patch.object(self.index, "_calculate_engine_revision", return_value="new-disk-source") as fingerprint:
            self.assertFalse(self.index._adopt_runtime_revision())
        fingerprint.assert_called_once_with()
        self.frappe.db.sql.assert_not_called()
        self.frappe.db.after_commit.add.assert_not_called()
        self.frappe.enqueue.assert_not_called()

    def test_paused_or_maintenance_scheduler_never_adopts_or_dispatches(self):
        for key in ("maintenance_mode", "pause_scheduler", "disable_scheduler"):
            for value in (True, 1, "1"):
                self.frappe.conf = {"followup_approval_index_enabled": True, key: value}
                with (
                    self.subTest(key=key, value=value),
                    patch.object(self.index, "_adopt_runtime_revision") as adopt,
                    patch.object(self.index, "_has_pending_work") as pending,
                    patch.object(self.index, "_enqueue") as enqueue,
                ):
                    self.index.recover_pending()
                adopt.assert_not_called()
                pending.assert_not_called()
                enqueue.assert_not_called()
        self.frappe.db.sql.assert_not_called()

    def test_explicit_admin_rebuild_adopts_without_second_epoch_invalidation(self):
        with (
            patch.object(self.index, "_adopt_runtime_revision", return_value=True) as adopt,
            patch.object(self.index, "_control", return_value=self.control(epoch=5, scan_complete=0)),
            patch.object(self.index, "request_rebuild") as rebuild,
        ):
            result = self.index.rebuild()
        self.frappe.only_for.assert_called_once_with("System Manager")
        adopt.assert_called_once_with()
        rebuild.assert_not_called()
        self.assertEqual(result["state"], "updating")
        self.assertEqual(result["generation"], 5)

    def test_explicit_same_revision_rebuild_retains_normal_admin_request(self):
        response = {"state": "updating", "generation": 6}
        with (
            patch.object(self.index, "_adopt_runtime_revision", return_value=False) as adopt,
            patch.object(self.index, "request_rebuild", return_value=response) as rebuild,
        ):
            result = self.index.rebuild()
        self.frappe.only_for.assert_called_once_with("System Manager")
        adopt.assert_called_once_with()
        rebuild.assert_called_once_with("administrator_requested")
        self.assertEqual(result, response)

    def test_matching_scheduler_keeps_ordinary_pending_recovery(self):
        with (
            patch.object(self.index, "_adopt_runtime_revision", return_value=False) as adopt,
            patch.object(self.index, "_has_pending_work", return_value=True),
            patch.object(self.index, "_enqueue") as enqueue,
        ):
            self.index.recover_pending()
        adopt.assert_called_once_with()
        enqueue.assert_called_once_with()

    def test_old_worker_cannot_adopt_or_start_evaluation(self):
        self.frappe.cache.return_value.lock.return_value.acquire.return_value = True
        self.frappe.db.sql.return_value = [self.control(engine_revision="newer-runtime")]
        with patch.object(self.index, "_adopt_runtime_revision") as adopt, patch.object(self.index, "_seed_batch") as seed:
            result = self.index.process_pending()
        self.assertEqual(result, {"state": "engine_revision_mismatch", "processed": 0})
        self.assertEqual(self.frappe.db.sql.call_count, 1)
        adopt.assert_not_called()
        seed.assert_not_called()
        self.frappe.cache.return_value.lock.return_value.release.assert_called_once_with()
        self.assert_read_only_without_dispatch()

    def test_publication_requires_snapshot_and_control_to_match_running_engine(self):
        snapshot = {"name": "WA-1", "epoch": 4, "requested_revision": 3, "engine_revision": self.index.ENGINE_REVISION}
        action = {"epoch": 4, "requested_revision": 3}
        self.assertTrue(self.index.publication_matches(self.control(), action, snapshot))
        for control_stamp, snapshot_stamp in (("newer-runtime", self.index.ENGINE_REVISION),
                                              (self.index.ENGINE_REVISION, "older-worker"),
                                              (None, self.index.ENGINE_REVISION),
                                              (self.index.ENGINE_REVISION, None)):
            with self.subTest(control_stamp=control_stamp, snapshot_stamp=snapshot_stamp):
                self.frappe.db.sql.reset_mock()
                self.frappe.db.sql.side_effect = [[self.control(engine_revision=control_stamp)], [action]]
                self.assertFalse(self.index._publish({**snapshot, "engine_revision": snapshot_stamp},
                                                    {"state": "ready", "recipients": [USER]}))
                self.assertEqual(self.frappe.db.sql.call_count, 2)
                self.assert_read_only_without_dispatch()

    def test_seed_cannot_publish_after_runtime_stamp_changes(self):
        original = self.control(scan_complete=0)
        self.frappe.db.sql.side_effect = [[{"name": "WA-1"}], [self.control(engine_revision="newer-runtime")]]
        with patch.object(self.index, "_dirty_where") as dirty:
            self.index._seed_batch(original)
        dirty.assert_not_called()
        self.assert_read_only_without_dispatch()

    def test_http_reads_never_hash_or_read_source_files_after_import(self):
        self.frappe.db.sql.side_effect = self.ready_sql
        with (
            patch.object(Path, "read_bytes", side_effect=AssertionError("No filesystem reads in HTTP")),
            patch.object(self.index, "_calculate_engine_revision", side_effect=AssertionError("No repeated hashing")),
        ):
            self.assertEqual(self.index.read_counts(USER)["open"], 3)
            self.assertEqual(len(self.index.read_page(USER)["items"]), 1)
            self.assertEqual(self.index.assert_visible(USER, "WA-1", with_snapshot=True)["revision"], 3)
            self.assertTrue(self.index.verify_snapshot(4, {"WA-1": 3}))
        self.assert_read_only_without_dispatch()

    def test_startup_hash_is_stable_and_reads_each_projection_source_once(self):
        seen = []

        def read_source(path):
            seen.append(path.name)
            return ("baseline:" + path.name).encode()

        with patch.object(Path, "read_bytes", read_source):
            first, _ = load_engine(frappe_version="15.120.1")
            second, _ = load_engine(frappe_version="15.120.1")
        self.assertEqual(first.ENGINE_REVISION, second.ENGINE_REVISION)
        self.assertEqual(len(first.ENGINE_REVISION), 64)
        self.assertEqual(seen, list(first.PROJECTION_SOURCE_FILES) * 2)

    def test_each_projection_file_change_changes_fingerprint_on_next_import(self):
        with patch.object(Path, "read_bytes", lambda path: ("baseline:" + path.name).encode()):
            original, _ = load_engine(frappe_version="15.120.1")
        original_revision = original.ENGINE_REVISION
        for changed_file in original.PROJECTION_SOURCE_FILES:
            with self.subTest(changed_file=changed_file), patch.object(
                Path, "read_bytes", lambda path: (("changed:" if path.name == changed_file else "baseline:") + path.name).encode()
            ):
                updated, _ = load_engine(frappe_version="15.120.1")
            self.assertNotEqual(updated.ENGINE_REVISION, original.ENGINE_REVISION)
            # Existing process tokens are immutable despite a later import.
            self.assertEqual(original.ENGINE_REVISION, original_revision)

    def test_frappe_version_change_changes_startup_fingerprint(self):
        with patch.object(Path, "read_bytes", lambda path: b"same-source"):
            before, _ = load_engine(frappe_version="15.120.1")
            after, _ = load_engine(frappe_version="15.121.0")
        self.assertNotEqual(before.ENGINE_REVISION, after.ENGINE_REVISION)


if __name__ == "__main__":
    unittest.main()
