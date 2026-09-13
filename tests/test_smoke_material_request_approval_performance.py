"""Offline safety gates for the real-controller TEST-only performance probe."""
from copy import deepcopy
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import requests

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
SPEC = importlib.util.spec_from_file_location("actual_controller_under_test", SCRIPTS / "smoke_test_material_request_approval_performance.py")
actual = importlib.util.module_from_spec(SPEC)
with patch.object(sys, "path", [str(SCRIPTS), *sys.path]):
    SPEC.loader.exec_module(actual)


class ActualControllerTests(unittest.TestCase):
    def setUp(self):
        self.network = self.enterContext(patch.object(requests.Session, "request", side_effect=AssertionError("Live network forbidden")))
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.original = {"doctype": "Workflow", "name": "طلب مواد", "document_type": actual.DOCTYPE,
                         "is_active": 1, "owner": "creator@example.invalid", "creation": "old", "modified": "v1", "modified_by": "old-user",
                         "states": [{"name": "state-1", "state": actual.STATE, actual.FIELD: None, actual.HIDE_FIELD: 0,
                                     "allow_edit": "Approver", "modified": "v1"},
                                    {"name": "state-2", "state": "Other", "allow_edit": "Other Role"}],
                         "transitions": [{"name": "transition-1", "allowed": "Approver", "condition": "doc.qty>0"}]}
        self.current = deepcopy(self.original)
        self.journal = actual.PerfJournal(self.directory / "manifest.json", {
            "before": deepcopy(self.original), "expected": deepcopy(self.original), "row_name": "state-1",
            "workflow_name": self.original["name"],
            "state_field": "workflow_state", "other_workflows": [], "measurements": [],
            "fingerprints_before": {"material_requests": [{"name": "MR-1", "workflow_state": actual.STATE}], "workflow_actions": []},
            "actors": {"setup": "Administrator", "measurements": "badr@example.invalid"}, "viewer_core_before": 10,
        })
        self.journal.flush()
        self.runner = actual.ActualControllerRunner.__new__(actual.ActualControllerRunner)
        self.runner.journal = self.journal
        self.runner.env = {"site": "https://test.example.invalid", "BROWSER_LOGIN_URL": "https://test.example.invalid/login",
                           "BROWSER_LOGIN_EMAIL": "badr@example.invalid", "BROWSER_LOGIN_PASSWORD": "test-placeholder"}
        self.runner.admin = SimpleNamespace(label="Administrator", last_status=None, doc=Mock(side_effect=lambda *a: deepcopy(self.current)), call=Mock())
        self.runner.viewer = SimpleNamespace(label="B", last_status=None, call=Mock(), login=Mock())
        self.runner.count = Mock(side_effect=lambda *args, **kwargs: 10 if kwargs.get("client") is self.runner.viewer else 0)
        self.runner.fingerprints = Mock(return_value=deepcopy(self.journal.data["fingerprints_before"]))

        def save(method, *, args, post):
            self.assertEqual(method, "frappe.client.save")
            self.assertTrue(post)
            candidate = json.loads(args["doc"])
            self.assertEqual(candidate["modified"], self.current["modified"])
            candidate["modified"] += "-saved"
            candidate["modified_by"] = "Administrator"
            self.current = candidate
            self.runner.admin.last_status = 200
            return deepcopy(candidate)
        self.runner.admin.call.side_effect = save

    def test_dry_run_has_no_secrets_network_or_state_writes(self):
        output = io.StringIO()
        with patch.object(actual, "read_env", side_effect=AssertionError("No secret read")), \
                patch.object(actual, "private_dir", side_effect=AssertionError("No state writes")), redirect_stdout(output):
            self.assertEqual(actual.main([]), 0)
        result = json.loads(output.getvalue())
        self.assertFalse(result["network"])
        self.assertFalse(result["writes"])
        self.assertEqual(result["samples_per_endpoint_per_phase"], 5)
        self.network.assert_not_called()

    def test_production_is_rejected_even_when_confirmation_matches(self):
        args = actual.parse_args(["--confirm-site", "https://erp.namar.net"])
        with patch.object(actual, "read_env", return_value={"FRAPPE_TEST_SITE": "https://erp.namar.net", "FRAPPE_TEST_TOKEN": "placeholder"}), self.assertRaises(actual.SmokeFailure):
            actual.configuration(args)

    def test_active_workflow_is_discovered_without_singular_plural_assumption(self):
        for name in ("طلب مواد", "طلبات المواد", "Material Request Approval"):
            self.assertEqual(actual.active_workflow_name([
                {"name": "Inactive", "is_active": 0}, {"name": name, "is_active": 1},
            ]), name)
        for workflows in ([], [{"name": "Inactive", "is_active": 0}], [{"name": None, "is_active": 1}],
                          [{"name": "One", "is_active": 1}, {"name": "Two", "is_active": 1}]):
            with self.subTest(workflows=workflows), self.assertRaises(actual.SmokeFailure):
                actual.active_workflow_name(workflows)

    def test_saved_workflow_name_is_pinned_for_changes_and_restore(self):
        self.journal.data["workflow_name"] = "طلبات المواد"
        for document in (self.current, self.original, self.journal.data["before"], self.journal.data["expected"]):
            document["name"] = "طلبات المواد"
        self.runner.save_settings({actual.FIELD: actual.OWNER}, "owner_unhidden")
        self.runner.restore()
        self.assertTrue(all(call.args == ("Workflow", "طلبات المواد") for call in self.runner.admin.doc.call_args_list))
        self.assertTrue(all(json.loads(call.kwargs["args"]["doc"])["name"] == "طلبات المواد"
                            for call in self.runner.admin.call.call_args_list))
        del self.journal.data["workflow_name"]
        self.runner.admin.call.reset_mock()
        with self.assertRaises(actual.SmokeFailure):
            self.runner.save_settings({actual.FIELD: actual.OWNER}, "unknown_name")
        self.runner.admin.call.assert_not_called()

    def test_standard_save_sends_current_modified_and_restores_only_two_fields(self):
        self.runner.save_settings({actual.FIELD: actual.OWNER, actual.HIDE_FIELD: 0}, "owner_unhidden")
        self.runner.save_settings({actual.FIELD: actual.OWNER, actual.HIDE_FIELD: 1}, "hidden")
        self.runner.restore()
        self.assertEqual(actual.semantic(self.current), actual.semantic(self.original))
        self.assertNotEqual(self.current["modified"], self.original["modified"])
        self.assertEqual(self.current["owner"], self.original["owner"])
        self.assertEqual(self.runner.admin.call.call_count, 3)
        self.assertTrue(self.journal.data["workflow_restored"])
        self.assertTrue(self.journal.data["business_fingerprints_unchanged"])

    def test_blank_state_blocks_first_save_and_restoration(self):
        self.runner.count.side_effect = None
        self.runner.count.return_value = 1
        with self.assertRaises(actual.SmokeFailure):
            self.runner.save_settings({actual.FIELD: actual.OWNER}, "owner_unhidden")
        with self.assertRaises(actual.SmokeFailure):
            self.runner.restore()
        self.runner.admin.call.assert_not_called()

    def test_modified_or_semantic_drift_blocks_overwrite(self):
        for mutation in (lambda d: d.update(modified="concurrent"), lambda d: d["transitions"][0].update(allowed="Other")):
            self.current = deepcopy(self.original)
            mutation(self.current)
            with self.assertRaises(actual.SmokeFailure):
                self.runner.save_settings({actual.FIELD: actual.OWNER}, "owner_unhidden")
        self.runner.admin.call.assert_not_called()

    def test_wrong_row_does_not_save(self):
        self.journal.data["row_name"] = "missing"
        with self.assertRaises(actual.SmokeFailure):
            self.runner.save_settings({actual.FIELD: actual.OWNER}, "owner_unhidden")
        self.runner.admin.call.assert_not_called()

    def test_uncertain_mutation_and_malformed_http200_block_restore(self):
        for status in (None, 504, 200):
            self.runner.admin.call.reset_mock()
            self.journal.data.pop("mutation_outcome_unknown", None)
            self.journal.data["inflight_mutations"] = {}
            def failure(*args, **kwargs):
                self.runner.admin.last_status = status
                if status == 200:
                    return None
                raise actual.SmokeFailure("transport outcome unknown")
            self.runner.admin.call.side_effect = failure
            with self.assertRaises(actual.SmokeFailure):
                self.runner.save_settings({actual.FIELD: actual.OWNER}, "owner_unhidden")
            self.assertTrue(self.journal.data["mutation_outcome_unknown"])
            with self.assertRaises(actual.SmokeFailure):
                self.runner.restore()
            self.assertEqual(self.runner.admin.call.call_count, 1)

    def test_read_timeout_blocks_restore_under_running_request(self):
        def failure(*args, **kwargs):
            self.runner.viewer.last_status = None
            raise actual.SmokeFailure("read timed out")
        self.runner.viewer.call.side_effect = failure
        with self.assertRaises(actual.SmokeFailure):
            self.runner.measure("owner_unhidden")
        self.assertTrue(self.journal.data["read_request_may_be_running"])
        with self.assertRaises(actual.SmokeFailure):
            self.runner.restore()
        self.assertEqual(self.runner.viewer.call.call_count, 1)
        self.runner.admin.call.assert_not_called()

    def test_business_fingerprint_change_is_reported_without_business_write(self):
        self.runner.fingerprints.return_value["material_requests"][0]["workflow_state"] = "Concurrent change"
        with self.assertRaises(actual.SmokeFailure):
            self.runner.restore()
        self.assertTrue(self.journal.data["workflow_restored"])
        self.assertFalse(self.journal.data["business_fingerprints_unchanged"])
        self.runner.admin.call.assert_not_called()

    def test_canonical_gate_rejects_whitespace_duplicate_or_extra_target_keys(self):
        for value in ('{ "version":1,"targets":[]}', '{"version":1,"targets":[{"type":"owner"},{"type":"owner"}]}',
                      '{"version":1,"targets":[{"type":"user","user":"person ","role":"x"}]}',
                      '{"targets":[],"version":1}', '{"version":1,"targets":[{"user":"person","type":"user"}]}',
                      '{"version":1,"targets":[{"type":"user","user":" person"}]}'):
            with self.subTest(value=value), self.assertRaises(actual.SmokeFailure):
                actual.assert_canonical_settings([{actual.FIELD: value}])
        actual.assert_canonical_settings([{actual.FIELD: actual.OWNER}, {actual.FIELD: None}])

    def test_server_side_unrelated_change_stops_further_restoration_write(self):
        original_save = self.runner.admin.call.side_effect
        def changed(*args, **kwargs):
            saved = original_save(*args, **kwargs)
            saved["transitions"][0]["allowed"] = "Unexpected Role"
            self.current = deepcopy(saved)
            return saved
        self.runner.admin.call.side_effect = changed
        with self.assertRaises(actual.SmokeFailure):
            self.runner.save_settings({actual.FIELD: actual.OWNER}, "owner_unhidden")
        self.assertTrue(self.journal.data["unsafe_drift"])
        with self.assertRaises(actual.SmokeFailure):
            self.runner.restore()
        self.assertEqual(self.runner.admin.call.call_count, 1)

    def test_slow_sample_is_failure_after_successful_restore(self):
        self.runner.preflight = Mock()
        self.runner.verify_hidden_search = Mock()
        self.runner.capture_approval_snapshot = Mock(side_effect=[
            {"count": 2, "target_names": ["target"], "other_names": ["other"]},
            {"count": 1, "target_names": [], "other_names": ["other"]},
        ])
        def measure(phase):
            self.journal.data["measurements"].extend([
                {"phase": phase, "get_approvals": 3.1, "get_my_followups_counts": 1.0} for _ in range(5)
            ])
        self.runner.measure = measure
        with self.assertRaises(actual.SmokeFailure):
            self.runner.run()
        self.assertTrue(self.journal.data["workflow_restored"])
        self.assertFalse(self.journal.data["performance_passed"])
        self.assertEqual(actual.semantic(self.current), actual.semantic(self.original))

    def test_approval_snapshot_reads_all_pages_as_viewer_and_keeps_exact_scope(self):
        rows = [{"name": f"WA-{i:03}", "reference_doctype": actual.DOCTYPE, "workflow_state": actual.STATE}
                for i in range(105)]
        rows[-1]["reference_doctype"] = "Other DocType"  # The same state label is not the target.
        rows[-2]["workflow_state"] = "Other State"
        self.runner.approval_read = Mock(side_effect=[
            {"items": rows[:100], "counts": {"open": 105}, "has_more": True, "next_start": 100},
            {"items": rows[100:], "counts": {"open": 105}, "has_more": False, "next_start": None},
        ])
        snapshot = self.runner.capture_approval_snapshot("owner_unhidden", 105)
        self.assertEqual(snapshot["target_names"], [f"WA-{i:03}" for i in range(103)])
        self.assertEqual(snapshot["other_names"], ["WA-103", "WA-104"])
        self.assertEqual(self.journal.data["approval_snapshots"]["owner_unhidden"], snapshot)
        self.assertEqual([call.args[1]["limit_start"] for call in self.runner.approval_read.call_args_list], [0, 100])
        self.assertTrue(all(call.args[0] == "get_approvals" and call.args[1]["page_length"] == 100
                            and call.args[1]["search"] == "" for call in self.runner.approval_read.call_args_list))
        self.runner.admin.call.assert_not_called()

    def test_approval_snapshot_rejects_count_drift_duplicates_and_incomplete_pagination(self):
        row = {"name": "WA-1", "reference_doctype": actual.DOCTYPE, "workflow_state": actual.STATE}
        cases = [
            {"items": [row], "counts": {"open": 2}, "has_more": False},
            {"items": [row], "counts": {"open": 1}},
            {"items": [row], "counts": {"open": 1}, "has_more": False, "next_start": 100},
            {"items": [], "counts": {"open": 1}, "has_more": False},
            {"items": [{"name": "WA-1"}], "counts": {"open": 1}, "has_more": False},
        ]
        for result in cases:
            with self.subTest(result=result):
                self.runner.approval_read = Mock(return_value=result)
                with self.assertRaises(actual.SmokeFailure):
                    self.runner.capture_approval_snapshot("owner_unhidden", 1)
        self.runner.approval_read = Mock(return_value={
            "items": [row, row], "counts": {"open": 2}, "has_more": False,
        })
        with self.assertRaises(actual.SmokeFailure):
            self.runner.capture_approval_snapshot("owner_unhidden", 2)
        self.assertNotIn("approval_snapshots", self.journal.data)

    def test_hide_scope_requires_nonempty_target_and_other_states_then_exact_delta(self):
        before = {"count": 3, "target_names": ["target"], "other_names": ["other-1", "other-2"]}
        hidden = {"count": 2, "target_names": [], "other_names": ["other-1", "other-2"]}
        self.runner.verify_hide_preserves_other_states(before, hidden)
        self.assertTrue(self.journal.data["other_state_ids_preserved"])
        self.assertTrue(self.journal.data["hidden_count_delta_verified"])
        for left, right in (
            ({"count": 0, "target_names": [], "other_names": []}, {"count": 0, "target_names": [], "other_names": []}),
            ({"count": 1, "target_names": ["target"], "other_names": []}, {"count": 0, "target_names": [], "other_names": []}),
            (before, {**hidden, "target_names": ["target"]}),
            (before, {**hidden, "other_names": ["other-1"]}),
            (before, {**hidden, "other_names": ["other-1", "unrelated-new"]}),
            (before, {**hidden, "count": 1}),
        ):
            with self.subTest(before=left, hidden=right), self.assertRaises(actual.SmokeFailure):
                self.runner.verify_hide_preserves_other_states(left, right)

    def test_approval_snapshot_timeout_still_blocks_restore(self):
        def failure(*args, **kwargs):
            self.runner.viewer.last_status = None
            raise actual.SmokeFailure("read timed out")
        self.runner.viewer.call.side_effect = failure
        with self.assertRaises(actual.SmokeFailure):
            self.runner.capture_approval_snapshot("owner_unhidden", 1)
        self.assertTrue(self.journal.data["read_request_may_be_running"])
        with self.assertRaises(actual.SmokeFailure):
            self.runner.restore()
        self.runner.admin.call.assert_not_called()

    def test_incomplete_page_and_changing_phase_counts_are_rejected(self):
        self.runner.approval_read = Mock(return_value={"counts": {"open": 26}, "items": []})
        with self.assertRaises(actual.SmokeFailure):
            self.runner.measure("owner_unhidden")
        items = [{"name": "WA-1"}]
        self.runner.approval_read = Mock(side_effect=[
            {"counts": {"open": 1}, "items": items}, {"counts": {"approvals": 1}},
            {"counts": {"open": 0}, "items": []}, {"counts": {"approvals": 0}},
        ])
        with redirect_stdout(io.StringIO()), self.assertRaises(actual.SmokeFailure):
            self.runner.measure("owner_unhidden")

    def test_hidden_search_checks_later_pages_and_rejects_missing_pagination(self):
        self.runner.approval_read = Mock(side_effect=[
            {"items": [{"reference_doctype": "Other", "workflow_state": actual.STATE}], "has_more": True, "next_start": 100},
            {"items": [{"reference_doctype": actual.DOCTYPE, "workflow_state": actual.STATE}], "has_more": False},
        ])
        with self.assertRaises(actual.SmokeFailure):
            self.runner.verify_hidden_search()
        self.assertEqual(self.runner.approval_read.call_count, 2)
        self.runner.approval_read = Mock(return_value={"items": []})
        with self.assertRaises(actual.SmokeFailure):
            self.runner.verify_hidden_search()

    def test_metadata_exclusions_do_not_hide_owner_or_transition_changes(self):
        changed = deepcopy(self.original)
        changed["modified"] = "later"
        changed["states"][0]["modified_by"] = "Administrator"
        self.assertEqual(actual.semantic(changed), actual.semantic(self.original))
        changed["owner"] = "someone-else"
        self.assertNotEqual(actual.semantic(changed), actual.semantic(self.original))

    def test_login_target_must_match_test_before_credentials_are_used(self):
        args = actual.parse_args(["--confirm-site", "https://test.example.invalid"])
        env = {"FRAPPE_TEST_SITE": "https://test.example.invalid", "FRAPPE_TEST_TOKEN": "placeholder", **self.runner.env}
        for login in ("https://erp.namar.net/login", "https://other-test.example.invalid/login", "http://test.example.invalid/login"):
            with self.subTest(login=login), patch.object(actual, "read_env", return_value={**env, "BROWSER_LOGIN_URL": login}), self.assertRaises(actual.SmokeFailure):
                actual.configuration(args)
        self.runner.env["BROWSER_LOGIN_URL"] = "https://other-test.example.invalid/login"
        with self.assertRaises(actual.SmokeFailure):
            self.runner.authenticate_viewer()
        self.runner.viewer.login.assert_not_called()

    def test_measurement_actor_identity_is_verified_and_not_admin(self):
        self.runner.admin.doc = Mock(return_value={"enabled": 1, "user_type": "System User"})
        for identity in ("Administrator", "Guest", "different@example.invalid"):
            self.runner.viewer.call.return_value = identity
            with self.subTest(identity=identity), self.assertRaises(actual.SmokeFailure):
                self.runner.authenticate_viewer()
        self.runner.viewer.call.return_value = "badr@example.invalid"
        self.runner.authenticate_viewer()
        self.assertEqual(self.journal.data["actors"]["measurements"], "badr@example.invalid")
        self.assertNotIn("BROWSER_LOGIN_PASSWORD", json.dumps(self.journal.data))
        self.assertNotIn("test-placeholder", json.dumps(self.journal.data))

    def test_native_core_count_uses_viewer_permission_context(self):
        self.runner.viewer.call.return_value = [{"count": 7}]
        self.assertEqual(actual.ActualControllerRunner.count(self.runner, "Workflow Action", {"status": "Open"}, client=self.runner.viewer), 7)
        method, = self.runner.viewer.call.call_args.args
        self.assertEqual(method, "frappe.client.get_list")
        self.assertEqual(json.loads(self.runner.viewer.call.call_args.kwargs["args"]["filters"]), {"status": "Open"})
        self.runner.admin.call.assert_not_called()

    def test_measurements_and_hidden_search_are_viewer_get_only(self):
        def read(method, *, args):
            self.runner.viewer.last_status = 200
            if args.get("search_scope") == "state":
                return {"items": [], "has_more": False}
            if method.endswith("get_approvals"):
                return {"counts": {"open": 1}, "items": [{"name": "WA-1"}]}
            return {"counts": {"approvals": 1}}
        self.runner.viewer.call.side_effect = read
        with redirect_stdout(io.StringIO()):
            self.runner.measure("owner_unhidden")
        self.runner.verify_hidden_search()
        self.assertEqual(self.runner.viewer.call.call_count, 11)
        self.assertTrue(all("post" not in call.kwargs for call in self.runner.viewer.call.call_args_list))
        self.assertTrue(all(row["actor"] == "badr@example.invalid" and row["core_count"] == 10 for row in self.journal.data["measurements"]))
        self.runner.count.assert_called_with("Workflow Action", {"status": "Open"}, client=self.runner.viewer)
        self.runner.admin.call.assert_not_called()

    def test_routed_count_cannot_exceed_viewers_native_core_count(self):
        self.runner.approval_read = Mock(return_value={"counts": {"open": 11}, "items": [{"name": str(i)} for i in range(11)]})
        with self.assertRaises(actual.SmokeFailure):
            self.runner.measure("owner_unhidden")


if __name__ == "__main__":
    unittest.main()
