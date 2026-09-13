"""Offline safety and recovery contracts for the opt-in TEST volume harness."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import stat
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import requests


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
SPEC = importlib.util.spec_from_file_location(
    "approval_visibility_performance_under_test",
    SCRIPTS / "smoke_test_approval_visibility_performance.py",
)
perf = importlib.util.module_from_spec(SPEC)
with patch.object(sys, "path", [str(SCRIPTS), *sys.path]):
    SPEC.loader.exec_module(perf)

PREFIX = "NAR Perf 20260913010101 abcdef12"
USER_B = "fixture-user@example.invalid"
SITE = "https://test.example.invalid"
REAL_RUNNER = perf.PerformanceRunner


class OfflineTestCase(unittest.TestCase):
    def setUp(self):
        self.network = self.enterContext(patch.object(
            requests.Session, "request", side_effect=AssertionError("Live network forbidden in unit tests")
        ))
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def journal(self, **updates):
        data = {
            "prefix": PREFIX, "definitions": [], "source_batches": [],
            "workflow_actions": {}, "measurements": [], "cleanup_complete": False,
            **updates,
        }
        journal = perf.PerfJournal(self.directory / "manifest.json", data)
        journal.flush()
        return journal

    def runner(self, **updates):
        runner = REAL_RUNNER.__new__(REAL_RUNNER)
        runner.prefix = PREFIX
        runner.routed, runner.default, runner.child = (
            PREFIX + " Routed", PREFIX + " Default", PREFIX + " Line"
        )
        runner.pending, runner.approved = PREFIX + " Pending", PREFIX + " Approved"
        runner.workflows = {runner.routed: runner.routed + " Flow"}
        runner.journal = self.journal(**updates)
        runner.args = SimpleNamespace(cleanup_workers=2)
        runner.clients = {
            "A": SimpleNamespace(label="A", last_status=None, call=Mock(), doc=Mock(),
                                 request=Mock(), session=Mock()),
        }
        return runner


class VolumeAndOfflineModeTests(OfflineTestCase):
    def test_full_volume_has_unique_names_owner_halves_and_real_children(self):
        groups = perf.source_groups(PREFIX, USER_B)
        self.assertEqual([len(group["indices"]) for group in groups], [2125, 2125, 3250])
        self.assertEqual([group["owner"] for group in groups], [USER_B, "Administrator", USER_B])
        self.assertEqual([group["actor"] for group in groups], ["B", "A", "B"])
        names, child_count, routed_count = set(), 0, 0
        for group in groups:
            self.assertLessEqual(len(group["doctype"]), 61)
            for index in group["indices"]:
                payload = perf.source_payload(PREFIX, group["doctype"], index, "Pending", USER_B)
                self.assertNotIn(payload["title"], names)
                names.add(payload["title"])
                self.assertEqual(payload["smoke_marker"], PREFIX)
                self.assertEqual(payload["approver"], USER_B)
                self.assertEqual(len(payload["lines"]), 6)
                self.assertGreater(payload["lines"][0]["qty"], 0)
                child_count += len(payload["lines"])
                routed_count += group["doctype"].endswith(" Routed")
        self.assertEqual((len(names), routed_count, child_count), (7500, 4250, 45000))

    def test_batch_partition_neither_duplicates_nor_omits_sources(self):
        values = list(range(7500))
        batches = perf.chunks(values, 200)
        self.assertTrue(all(1 <= len(batch) <= 200 for batch in batches))
        self.assertEqual([value for batch in batches for value in batch], values)

    def test_bad_owner_split_is_rejected(self):
        for total, configured in ((10, 5), (10, 10), (10, 0)):
            with self.subTest(total=total, configured=configured), self.assertRaises(perf.SmokeFailure):
                perf.source_groups(PREFIX, USER_B, total=total, configured=configured)

    def test_dry_run_does_not_read_environment_create_runner_or_use_network(self):
        output = io.StringIO()
        with patch.object(perf, "read_env", side_effect=AssertionError("No environment reads")) as env_read, \
                patch.object(perf, "PerformanceRunner", side_effect=AssertionError("No live runner")) as runner, \
                patch.object(perf, "private_dir", side_effect=AssertionError("No state writes")), \
                redirect_stdout(output):
            self.assertEqual(perf.main(["--env-file", "/nonexistent/private.env"]), 0)
        result = json.loads(output.getvalue())
        self.assertEqual((result["actions"], result["configured_sources"], result["total_child_rows"]),
                         (7500, 4250, 45000))
        self.assertFalse(result["network"])
        self.assertFalse(result["writes"])
        env_read.assert_not_called()
        runner.assert_not_called()
        self.network.assert_not_called()

    def test_each_performance_sample_must_meet_budget(self):
        self.assertTrue(perf.samples_pass([0, 1, 2, 2.9, 3]))
        for values in ([1] * 4, [1] * 6, [1, 1, 1, 1, 3.01], [-1, 1, 1, 1, 1]):
            self.assertFalse(perf.samples_pass(values))


class ConfigurationGuardsTests(OfflineTestCase):
    def config(self, *, environment=None, **arguments):
        env = {
            "FRAPPE_TEST_SITE": SITE, "FRAPPE_TEST_TOKEN": "unit-test-placeholder",
            "BROWSER_LOGIN_URL": SITE + "/login", "BROWSER_LOGIN_EMAIL": USER_B,
            "BROWSER_LOGIN_PASSWORD": "unit-test-placeholder",
        }
        env.update(environment or {})
        args = perf.parse_args([])
        args.confirm_site = SITE
        for key, value in arguments.items():
            setattr(args, key, value)
        with patch.object(perf, "read_env", return_value=env):
            return perf.config(args)

    def test_production_hosts_are_rejected_even_when_confirmation_matches(self):
        for site in ("https://erp.namar.net", "https://zawaya7.frappe.cloud", "https://other-prod.example.invalid"):
            with self.subTest(site=site), self.assertRaises(perf.SmokeFailure):
                self.config(environment={"FRAPPE_TEST_SITE": site, "FRAPPE_PROD_SITE": site}, confirm_site=site)

    def test_test_confirmation_and_secure_origin_are_required(self):
        cases = [
            ({}, {"confirm_site": "https://wrong.example.invalid"}),
            ({}, {"confirm_site": ""}),
            ({"FRAPPE_TEST_SITE": "http://test.example.invalid"}, {}),
            ({"FRAPPE_TEST_SITE": "https://user:secret@test.example.invalid"}, {}),
            ({"FRAPPE_TEST_SITE": "https://test.example.invalid:8443"}, {}),
            ({"FRAPPE_TEST_TOKEN": ""}, {}),
        ]
        for environment, arguments in cases:
            with self.subTest(environment=environment, arguments=arguments), self.assertRaises(perf.SmokeFailure):
                self.config(environment=environment, **arguments)
        self.network.assert_not_called()

    def test_login_destination_cannot_differ_from_test(self):
        for login in ("https://erp.namar.net/login", "https://wrong.example.invalid/login", "http://test.example.invalid/login"):
            with self.subTest(login=login), self.assertRaises(perf.SmokeFailure):
                self.config(environment={"BROWSER_LOGIN_URL": login})
        self.network.assert_not_called()

    def test_batch_worker_and_timeout_boundaries(self):
        for field, values in (("insert_batch", (0, 201)), ("cleanup_workers", (0, 3)), ("timeout", (4, 121))):
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaises(perf.SmokeFailure):
                    self.config(**{field: value})
        for batch, workers, timeout in ((1, 1, 5), (200, 2, 120)):
            self.assertEqual(self.config(insert_batch=batch, cleanup_workers=workers, timeout=timeout)["site"], SITE)

    def test_pause_requires_tty_and_cannot_combine_with_cleanup(self):
        with patch.object(perf.sys.stdin, "isatty", return_value=False), self.assertRaises(perf.SmokeFailure):
            self.config(pause_before_cleanup=True)
        with patch.object(perf.sys.stdin, "isatty", return_value=True):
            self.assertEqual(self.config(pause_before_cleanup=True)["site"], SITE)
            with self.assertRaises(perf.SmokeFailure):
                self.config(pause_before_cleanup=True, cleanup_manifest=Path("manifest.json"))

    def test_cleanup_does_not_require_normal_user_login(self):
        result = self.config(environment={"BROWSER_LOGIN_URL": "", "BROWSER_LOGIN_EMAIL": "", "BROWSER_LOGIN_PASSWORD": ""},
                             cleanup_manifest=Path("manifest.json"))
        self.assertEqual(result["site"], SITE)


class PrivateJournalTests(OfflineTestCase):
    def test_manifest_and_transcript_are_private(self):
        journal = self.journal(counter=0)
        journal.event("fixture_check", value="اختبار")
        for path in (journal.path, journal.transcript):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(json.loads(journal.path.read_text())["counter"], 0)
        self.assertEqual(json.loads(journal.transcript.read_text())["value"], "اختبار")

    def test_failed_atomic_replace_preserves_last_recovery_manifest(self):
        journal = self.journal(counter=1)
        before = journal.path.read_bytes()
        journal.data["counter"] = 2
        with patch.object(perf.os, "replace", side_effect=OSError("simulated interrupted replacement")), self.assertRaises(OSError):
            journal.flush()
        self.assertEqual(journal.path.read_bytes(), before)
        self.assertEqual(list(self.directory.glob("*.pending-*")), [])

    def test_concurrent_journal_writes_and_transport_status_are_isolated(self):
        journal = self.journal(counter=0)
        barrier = threading.Barrier(4)

        def worker(index):
            journal.event("http_before", worker=index)
            barrier.wait(timeout=5)
            journal.event("http_after", status=200 + index, worker=index)
            barrier.wait(timeout=5)
            observed = journal.transport.last_status
            for item in range(10):
                with journal.lock:
                    journal.data["counter"] += 1
                    journal.flush()
                journal.event("thread_item", worker=index, item=item)
            return observed

        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(list(pool.map(worker, range(4))), [200, 201, 202, 203])
        self.assertEqual(json.loads(journal.path.read_text())["counter"], 40)
        entries = [json.loads(line) for line in journal.transcript.read_text().splitlines()]
        items = [(entry["worker"], entry["item"]) for entry in entries if entry["event"] == "thread_item"]
        self.assertEqual(len(items), 40)
        self.assertEqual(len(set(items)), 40)


class MutationOutcomeTests(OfflineTestCase):
    def test_timeout_and_uncertain_http_block_further_writes_and_cleanup(self):
        for status in (None, *sorted(perf.UNCERTAIN_HTTP)):
            with self.subTest(status=status):
                runner = self.runner()
                client = perf.TrackedClient(SITE, "A", runner.journal, 5)
                self.addCleanup(client.session.close)
                if status is None:
                    self.network.side_effect = requests.exceptions.Timeout("simulated")
                else:
                    response = requests.Response()
                    response.status_code = status
                    response._content = b'{"exc_type":"Error"}'
                    self.network.side_effect = None
                    self.network.return_value = response
                with self.assertRaises(perf.SmokeFailure):
                    runner.mutate(client, "insert_many", lambda: client.call("frappe.client.insert_many", args={"docs": []}, post=True))
                self.assertTrue(runner.journal.data["mutation_outcome_unknown"])
                pending = list(runner.journal.data["inflight_mutations"].values())
                self.assertEqual(len(pending), 1)
                self.assertEqual(pending[0]["http_status"], status)
                write = Mock()
                with self.assertRaises(perf.SmokeFailure):
                    runner.mutate(client, "must_not_run", write)
                write.assert_not_called()
                runner.remember_actions = Mock()
                with self.assertRaises(perf.SmokeFailure):
                    runner.cleanup()
                runner.remember_actions.assert_not_called()

    def test_known_permission_error_does_not_mark_mutation_unknown(self):
        runner = self.runner()
        client = perf.TrackedClient(SITE, "A", runner.journal, 5)
        self.addCleanup(client.session.close)
        response = requests.Response()
        response.status_code = 403
        response._content = b'{"exc_type":"PermissionError"}'
        self.network.side_effect = None
        self.network.return_value = response
        with self.assertRaises(perf.SmokeFailure):
            runner.mutate(client, "insert_many", lambda: client.call("frappe.client.insert_many", args={"docs": []}, post=True))
        self.assertFalse(runner.journal.data.get("mutation_outcome_unknown", False))
        self.assertEqual(runner.journal.data["inflight_mutations"], {})
        self.assertEqual(runner.mutate(client, "known_next_step", lambda: "done"), "done")

    def test_keyboard_interrupt_during_write_is_unknown(self):
        runner = self.runner()
        with self.assertRaises(KeyboardInterrupt):
            runner.mutate(runner.admin, "insert_many", Mock(side_effect=KeyboardInterrupt))
        self.assertTrue(runner.journal.data["mutation_outcome_unknown"])


class CleanupEvidenceTests(OfflineTestCase):
    def source_runner(self):
        runner = self.runner()
        name = runner.routed + " 00001"
        runner.journal.data["source_batches"] = [{
            "doctype": runner.routed, "owner": USER_B, "names": [name], "deleted": [],
        }]
        runner.journal.data["actors"] = {"A": "Administrator", "B": USER_B}
        payload = perf.source_payload(PREFIX, runner.routed, 1, runner.pending, USER_B)
        lines = payload.pop("lines")
        row = {**payload, "name": name, "owner": USER_B}
        children = [{**line, "parent": name, "parenttype": runner.routed, "parentfield": "lines", "idx": index}
                    for index, line in enumerate(lines, start=1)]
        return runner, name, row, children

    def test_none_delete_response_is_accepted_only_after_parent_is_absent(self):
        for remaining in ([], [{"name": PREFIX + " Routed 00001"}]):
            with self.subTest(remaining=bool(remaining)):
                runner, name, row, children = self.source_runner()
                runner.admin.call.return_value = None
                runner.rows = Mock(side_effect=[[row], children, remaining])
                if remaining:
                    with self.assertRaises(perf.SmokeFailure):
                        runner.delete_source_batch(runner.admin, runner.routed, [name])
                else:
                    runner.delete_source_batch(runner.admin, runner.routed, [name])
                self.assertEqual(runner.journal.data["source_batches"][0]["deleted"], [] if remaining else [name])
                self.assertEqual(runner.rows.call_count, 3)
                self.assertEqual(runner.admin.call.call_count, 1)

    def test_source_fingerprint_failure_prevents_delete(self):
        runner, name, row, children = self.source_runner()
        row["owner"] = "unexpected@example.invalid"
        runner.rows = Mock(side_effect=[[row], children])
        with self.assertRaises(perf.SmokeFailure):
            runner.delete_source_batch(runner.admin, runner.routed, [name])
        runner.admin.call.assert_not_called()

    def test_undeleted_source_list_is_not_retried_or_marked_deleted(self):
        runner, name, row, children = self.source_runner()
        runner.rows = Mock(side_effect=[[row], children])
        runner.admin.call.return_value = [name]
        with self.assertRaises(perf.SmokeFailure):
            runner.delete_source_batch(runner.admin, runner.routed, [name])
        self.assertEqual(runner.admin.call.call_count, 1)
        self.assertEqual(runner.journal.data["source_batches"][0]["deleted"], [])

    def test_changed_source_child_values_prevent_deletion(self):
        runner, name, row, children = self.source_runner()
        children[0]["qty"] = 999
        runner.rows = Mock(side_effect=[[row], children])
        with self.assertRaises(perf.SmokeFailure):
            runner.delete_source_batch(runner.admin, runner.routed, [name])
        runner.admin.call.assert_not_called()

    def test_none_action_delete_requires_both_action_and_child_absence(self):
        for parent_remains, child_remains in ((False, False), (True, False), (False, True)):
            with self.subTest(parent_remains=parent_remains, child_remains=child_remains):
                runner = self.runner()
                name = "isolated-action-1"
                record = {"name": name, "reference_doctype": runner.routed, "reference_name": runner.routed + " 00001"}
                runner.journal.data["workflow_actions"] = {name: {**record, "deleted": False}}
                runner.journal.data["source_batches"] = [{
                    "doctype": runner.routed, "owner": USER_B,
                    "names": [record["reference_name"]], "deleted": [],
                }]
                children = [{"name": "role-child-1", "parent": name, "role": "Accounts User"}]
                runner.rows = Mock(side_effect=[[record], children, [record] if parent_remains else [], children if child_remains else []])
                runner.admin.call.return_value = None
                if parent_remains or child_remains:
                    with self.assertRaises(perf.SmokeFailure):
                        runner.delete_action_batch(runner.admin, [name])
                else:
                    runner.delete_action_batch(runner.admin, [name])
                self.assertEqual(runner.journal.data["workflow_actions"][name]["deleted"], not (parent_remains or child_remains))
                self.assertEqual(runner.admin.call.call_count, 1)
                if not parent_remains:
                    self.assertEqual(runner.rows.call_args.args[1], "Workflow Action Permitted Role")

    def test_cleanup_does_not_delete_definitions_while_sources_actions_or_children_remain(self):
        for category in ("sources", "actions", "children"):
            with self.subTest(category=category):
                runner = self.runner()
                runner.journal.data["definitions"] = [{
                    "doctype": "DocType", "name": runner.routed, "deleted": False,
                    "fingerprint": {"name": runner.routed},
                }]
                runner.remember_actions = Mock()
                runner.cleanup_tasks = Mock()
                runner.admin.doc.return_value = {"name": runner.routed}
                counts = {runner.routed: int(category == "sources"), "Workflow Action": int(category == "actions"), runner.child: int(category == "children")}
                runner.count = Mock(side_effect=lambda client, dt, filters, **kwargs: counts[dt])
                with self.assertRaises(perf.SmokeFailure):
                    runner.cleanup()
                runner.admin.request.assert_not_called()
                self.assertFalse(runner.journal.data["cleanup_complete"])


class StrictReadContractsTests(OfflineTestCase):
    def response(self, status, envelope):
        response = requests.Response()
        response.status_code = status
        response._content = json.dumps(envelope).encode()
        self.network.side_effect = None
        self.network.return_value = response

    def client(self):
        client = perf.TrackedClient(SITE, "A", self.journal(), 5)
        self.addCleanup(client.session.close)
        return client

    def test_http_200_null_message_is_not_an_empty_row_list(self):
        runner, client = self.runner(), self.client()
        for envelope in ({}, {"message": None}, {"message": {}}, {"message": [None]}):
            with self.subTest(envelope=envelope):
                self.response(200, envelope)
                with self.assertRaises(perf.SmokeFailure):
                    runner.rows(client, runner.routed, ["name"], {})
        self.response(200, {"message": []})
        self.assertEqual(runner.rows(client, runner.routed, ["name"], {}), [])

    def test_count_requires_one_explicit_nonnegative_integer(self):
        runner = self.runner()
        for value in (None, {}, [], [{}], [{"count": None}], [{"count": "0"}],
                      [{"count": False}], [{"count": -1}], [{"count": 0.0}],
                      [{"count": 0}, {"count": 0}]):
            with self.subTest(value=value):
                runner.admin.call.return_value = value
                with self.assertRaises(perf.SmokeFailure):
                    runner.count(runner.admin, runner.routed, {})
        for count in (0, 7500):
            runner.admin.call.return_value = [{"count": count}]
            self.assertEqual(runner.count(runner.admin, runner.routed, {}), count)

    def test_http_200_without_matching_document_is_not_missing(self):
        client = self.client()
        for envelope in ({}, {"data": None}, {"data": []}, {"data": {}}, {"data": {"name": "different"}}):
            with self.subTest(envelope=envelope):
                self.response(200, envelope)
                with self.assertRaises(perf.SmokeFailure):
                    client.doc("DocType", "fixture", missing=True)
        self.response(200, {"data": {"name": "fixture"}})
        self.assertEqual(client.doc("DocType", "fixture", missing=True), {"name": "fixture"})

    def test_only_explicit_404_is_an_allowed_missing_document(self):
        client = self.client()
        self.response(404, {"exc_type": "DoesNotExistError"})
        self.assertIsNone(client.doc("DocType", "fixture", missing=True))
        with self.assertRaises(perf.SmokeFailure):
            client.doc("DocType", "fixture")

    def test_malformed_action_inventory_blocks_cleanup_before_writes(self):
        for page in ({}, None, [None], [{}], [{"name": None, "reference_doctype": "x", "reference_name": "y"}]):
            with self.subTest(page=page):
                runner = self.runner()
                runner.admin.call.return_value = page
                runner.cleanup_tasks = Mock()
                with self.assertRaises(perf.SmokeFailure):
                    runner.cleanup()
                runner.cleanup_tasks.assert_not_called()
                runner.admin.request.assert_not_called()
                self.assertFalse(runner.journal.data["cleanup_complete"])


class MainLifecycleTests(OfflineTestCase):
    def run_main(self, mode="normal", *, interrupt_pause=False):
        trace, instances = [], []

        class FakeRunner:
            def __init__(self, env, args, journal):
                self.journal = journal
                self.routed = PREFIX + " Routed"
                self.workflows = {self.routed: self.routed + " Flow"}
                self.clients = {"A": SimpleNamespace(session=Mock()), "B": SimpleNamespace(session=Mock())}
                instances.append(self)

            def preflight(self):
                trace.append("preflight")

            def setup(self):
                trace.append("setup")
                if mode == "unknown":
                    self.journal.data["mutation_outcome_unknown"] = True
                    self.journal.data["inflight_mutations"] = {"request": {"outcome": "unknown"}}
                    raise perf.SmokeFailure("simulated gateway outcome unknown")
                if mode == "known_error":
                    raise perf.SmokeFailure("simulated known HTTP 403")

            def exercise(self):
                trace.append("exercise")
                if mode == "read_timeout":
                    self.clients["B"].last_status = None
                    raise perf.SmokeFailure("simulated GET timeout after setup")
                self.journal.data.update(correctness_passed=True, performance_passed=True)

            def pause_for_review(self):
                trace.append("pause")
                if self.journal.data.get("read_request_may_be_running"):
                    trace.append("review_waits_for_read")
                REAL_RUNNER.pause_for_review(self)

            def cleanup(self):
                trace.append("cleanup")
                self.journal.data["cleanup_complete"] = True

            def verify_real_workflows(self):
                trace.append("verify_real_workflows")

        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(perf, "config", return_value={"site": SITE}), \
                patch.object(perf, "PerformanceRunner", FakeRunner), \
                patch.object(perf, "private_dir", return_value=self.directory), \
                patch("builtins.input", side_effect=KeyboardInterrupt if interrupt_pause else None, return_value=""), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            if interrupt_pause:
                with self.assertRaises(KeyboardInterrupt):
                    perf.main(["--run", "--pause-before-cleanup"])
                status = None
            else:
                status = perf.main(["--run", "--pause-before-cleanup"])
        return status, trace, instances[0], stdout.getvalue(), stderr.getvalue()

    def test_enter_releases_pause_then_cleans_and_verifies(self):
        status, trace, runner, output, _ = self.run_main()
        self.assertEqual(status, 0)
        self.assertEqual(trace, ["preflight", "setup", "exercise", "pause", "cleanup", "verify_real_workflows"])
        self.assertIn('"awaiting_visual_qa"', output)
        self.assertEqual(runner.journal.data["state"], "review_released")
        for client in runner.clients.values():
            client.session.close.assert_called_once()
        self.network.assert_not_called()

    def test_keyboard_interrupt_at_pause_still_cleans_and_closes_sessions(self):
        _, trace, runner, _, _ = self.run_main(interrupt_pause=True)
        self.assertEqual(trace[-2:], ["cleanup", "verify_real_workflows"])
        for client in runner.clients.values():
            client.session.close.assert_called_once()

    def test_unknown_setup_defers_automatic_cleanup(self):
        status, trace, runner, _, _ = self.run_main("unknown")
        self.assertEqual(status, 1)
        self.assertEqual(trace, ["preflight", "setup"])
        entries = [json.loads(line) for line in runner.journal.transcript.read_text().splitlines()]
        deferred = [entry for entry in entries if entry["event"] == "cleanup_deferred_unknown_mutation"]
        self.assertEqual(len(deferred), 1)
        self.assertFalse(deferred[0]["automatic_retry"])
        self.assertFalse(runner.journal.data["cleanup_complete"])
        for client in runner.clients.values():
            client.session.close.assert_called_once()

    def test_known_setup_error_still_cleans(self):
        status, trace, runner, _, _ = self.run_main("known_error")
        self.assertEqual(status, 1)
        self.assertEqual(trace, ["preflight", "setup", "cleanup", "verify_real_workflows"])
        self.assertTrue(runner.journal.data["cleanup_complete"])

    def test_read_timeout_after_setup_reaches_review_before_cleanup(self):
        status, trace, runner, _, _ = self.run_main("read_timeout")
        self.assertEqual(status, 1)
        self.assertEqual(trace, ["preflight", "setup", "exercise", "pause", "review_waits_for_read", "cleanup", "verify_real_workflows"])
        self.assertTrue(runner.journal.data["read_request_may_be_running"])
        self.assertFalse(runner.journal.data["correctness_passed"])


if __name__ == "__main__":
    unittest.main()
