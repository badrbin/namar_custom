"""Offline safety and recovery contracts for the opt-in TEST volume harness."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import hashlib
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


def legacy_resume_data():
    source_batches = []
    for group in perf.source_groups(PREFIX, USER_B):
        for indices in perf.chunks(group["indices"], 200):
            source_batches.append({
                "doctype": group["doctype"], "actor": group["actor"], "owner": group["owner"],
                "names": [f"{group['doctype']} {index:05}" for index in indices],
                "created": True, "deleted": [],
            })
    return {
        "schema": "approval_visibility_performance_v1", "site": SITE, "prefix": PREFIX,
        "state": "awaiting_performance_review", "cleanup_complete": False, "inflight_mutations": {},
        "native_approval_unchanged": True, "completed_sources": [PREFIX + " Routed 00001"],
        "definitions": [], "source_batches": source_batches, "workflow_actions": {},
        "actors": {"A": "Administrator", "B": USER_B, "role": "Accounts User"},
        "baseline": {"A": {"core": 10, "routed": 10}, "B": {"core": 20, "routed": 20}},
        "real_workflows_before": [{"name": "Original business workflow", "state": "unchanged"}],
        "measurements": [{"scenario": f"scenario-{index}", "actor": "B", "passed": False,
                          "samples": {"page_25": [3.5] * 5, "counts": [3.4] * 5}} for index in range(14)],
        "performance_failures": [f"scenario-{index}" for index in range(14)],
        "correctness_passed": True, "performance_passed": False,
    }


class OfflineTestCase(unittest.TestCase):
    def setUp(self):
        self.network = self.enterContext(patch.object(
            requests.Session, "request", side_effect=AssertionError("Live network forbidden in unit tests")
        ))
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()

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


class SchemaDriftGuardTests(OfflineTestCase):
    def definition_fixture(self):
        runner = self.runner()
        permission = {"name": "isolated-permission", "role": "Accounts User",
                      "read": 1, "write": 1, "create": 1, "delete": 1,
                      "if_owner": 0, "permlevel": 0, "__unsaved": 1}
        document = {"name": runner.routed, "owner": "Administrator",
                    "fields": [{"fieldname": "title", "fieldtype": "Data"}],
                    "permissions": [{**permission, "set_user_permissions": 0}]}
        document["permissions"][0].pop("__unsaved")
        target = {"doctype": "DocType", "name": runner.routed,
                  "fingerprint": {"owner": "Administrator"},
                  "schema_fields": runner.schema_fields(document),
                  "schema_permissions": [permission]}
        runner.journal.data["definitions"] = [target]
        runner.admin.doc.return_value = document
        return runner, target, document

    def test_insert_response_and_db_read_normalization_does_not_rewrite_manifest(self):
        runner, target, document = self.definition_fixture()
        original_target, original_document = deepcopy(target), deepcopy(document)
        self.assertEqual(runner.assert_definition("DocType", runner.routed), (target, document))
        self.assertEqual(target, original_target)
        self.assertEqual(document, original_document)
        runner.admin.request.assert_not_called()

    def test_actual_permission_changes_still_block_resume_and_cleanup(self):
        changes = {"role": "System Manager", "read": 0, "write": 0, "create": 0,
                   "delete": 0, "if_owner": 1, "permlevel": 1, "name": "other",
                   "modified": "new timestamp", "unknown_permission": 1,
                   "set_user_permissions": 1}
        for key, value in changes.items():
            with self.subTest(key=key):
                runner, _, document = self.definition_fixture()
                document["permissions"][0][key] = value
                with self.assertRaises(perf.SmokeFailure):
                    runner.assert_definition("DocType", runner.routed)

    def test_null_legacy_permission_is_not_silently_normalized(self):
        runner, _, document = self.definition_fixture()
        document["permissions"][0]["set_user_permissions"] = None
        with self.assertRaises(perf.SmokeFailure):
            runner.assert_definition("DocType", runner.routed)

    def test_field_and_permission_row_drift_still_block(self):
        for kind in ("field", "added_permission", "removed_permission", "missing_grant"):
            with self.subTest(kind=kind):
                runner, _, document = self.definition_fixture()
                if kind == "field":
                    document["fields"][0]["fieldtype"] = "Link"
                elif kind == "added_permission":
                    document["permissions"].append({"role": "System Manager", "read": 1})
                elif kind == "removed_permission":
                    document["permissions"] = []
                else:
                    document["permissions"][0].pop("read")
                with self.assertRaises(perf.SmokeFailure):
                    runner.assert_definition("DocType", runner.routed)

    def test_permission_row_order_is_not_discarded(self):
        first, second = {"name": "one", "role": "Accounts User"}, {"name": "two", "role": "System Manager"}
        self.assertNotEqual(perf.PerformanceRunner.canonical_permissions([first, second]),
                            perf.PerformanceRunner.canonical_permissions([second, first]))


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

    def test_resume_requires_sha_runtime_stopped_evidence_and_pause(self):
        valid = {"resume_measurements": Path("manifest.json"), "pause_before_cleanup": True,
                 "expected_manifest_sha256": "a" * 64, "runtime_ref": "abcdef1234",
                 "stopped_run_evidence": "old process exited"}
        with patch.object(perf.sys.stdin, "isatty", return_value=True):
            self.assertEqual(self.config(**valid)["site"], SITE)
            for invalid in ({"expected_manifest_sha256": ""}, {"expected_manifest_sha256": "x" * 64},
                            {"runtime_ref": ""}, {"runtime_ref": "not-a-commit"},
                            {"stopped_run_evidence": " "}, {"pause_before_cleanup": False}):
                with self.subTest(invalid=invalid), self.assertRaises(perf.SmokeFailure):
                    self.config(**{**valid, **invalid})

    def test_resume_and_cleanup_modes_are_mutually_exclusive(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            perf.parse_args(["--cleanup-manifest", "a.json", "--resume-measurements", "a.json"])


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


class ResumeArchiveTests(OfflineTestCase):
    def test_legacy_run_is_archived_independently_with_runtime_and_cli_refs(self):
        data = legacy_resume_data()
        original = deepcopy(data)
        run_id = perf.archive_measurement_run(data, "abcdef1234", "f" * 64,
                                             cli_ref="fedcba12", stopped_evidence="old process exited")
        archive = data["measurement_history"][0]
        self.assertEqual(archive["runtime_ref"], "c08ec77")
        self.assertEqual(archive["cli_ref"], "68a5c66")
        self.assertEqual(archive["manifest_sha256"], "f" * 64)
        self.assertEqual(archive["snapshot"], original)
        self.assertEqual(len(archive["snapshot"]["measurements"]), 14)
        self.assertEqual(data["active_measurement_run"]["run_id"], run_id)
        self.assertEqual(data["active_measurement_run"]["runtime_ref"], "abcdef1234")
        self.assertEqual(data["measurements"], [])
        self.assertEqual(data["performance_failures"], [])
        self.assertEqual(data["baseline"], original["baseline"])
        self.assertEqual(data["real_workflows_before"], original["real_workflows_before"])
        data["baseline"]["B"]["core"] = 999
        self.assertEqual(archive["snapshot"]["baseline"]["B"]["core"], 20)

    def test_next_archive_does_not_recursively_embed_prior_archives(self):
        data = legacy_resume_data()
        perf.archive_measurement_run(data, "abcdef1234", "a" * 64, cli_ref="fedcba12")
        data["measurements"].append({"scenario": "interrupted-new-round"})
        perf.archive_measurement_run(data, "abcdef5678", "b" * 64, cli_ref="fedcba34")
        self.assertEqual(len(data["measurement_history"]), 2)
        latest = data["measurement_history"][1]
        self.assertEqual(latest["runtime_ref"], "abcdef1234")
        self.assertEqual(latest["cli_ref"], "fedcba12")
        self.assertNotIn("measurement_history", latest["snapshot"])
        self.assertEqual(latest["snapshot"]["measurements"], [{"scenario": "interrupted-new-round"}])

    def test_archive_keeps_existing_event_transcript_and_appends_new_events(self):
        journal = self.journal(**legacy_resume_data())
        journal.event("old_round_finished", samples=140)
        previous = journal.transcript.read_bytes()
        perf.archive_measurement_run(journal.data, "abcdef1234", "f" * 64)
        journal.flush()
        journal.event("resume_round_started")
        self.assertTrue(journal.transcript.read_bytes().startswith(previous))
        entries = [json.loads(line) for line in journal.transcript.read_text().splitlines()]
        self.assertEqual([entry["event"] for entry in entries], ["old_round_finished", "resume_round_started"])

    def test_expected_sha_mismatch_leaves_manifest_and_transcript_unmodified(self):
        journal = self.journal(**legacy_resume_data())
        journal.event("old_round_finished")
        before, events_before = journal.path.read_bytes(), journal.transcript.read_bytes()
        with patch.object(perf.PerfJournal, "flush", side_effect=AssertionError("No writes before SHA matches")), \
                self.assertRaises(perf.SmokeFailure):
            perf.load_existing_manifest(journal.path, self.directory, SITE, expected_sha256="0" * 64)
        self.assertEqual(journal.path.read_bytes(), before)
        self.assertEqual(journal.transcript.read_bytes(), events_before)
        expected = hashlib.sha256(before).hexdigest()
        data, actual = perf.load_existing_manifest(journal.path, self.directory, SITE, expected_sha256=expected)
        self.assertEqual(actual, expected)
        self.assertEqual(len(data["measurements"]), 14)
        self.network.assert_not_called()

    def test_exclusive_file_lock_blocks_second_owner_until_release(self):
        journal = self.journal()
        first = perf.ManifestFileLock(journal.path).acquire()
        second = perf.ManifestFileLock(journal.path)
        self.addCleanup(first.release)
        self.addCleanup(second.release)
        self.assertEqual(stat.S_IMODE(first.path.stat().st_mode), 0o600)
        with self.assertRaises(perf.SmokeFailure):
            second.acquire()
        journal.data["still_locked"] = True
        journal.flush()
        with self.assertRaises(perf.SmokeFailure):
            second.acquire()
        first.release()
        self.assertIs(second.acquire(), second)

    def test_resume_loader_rejects_other_site_and_wrong_schema_without_writes(self):
        for change in ({"site": "https://other.example.invalid"}, {"schema": "another-schema"}, {"prefix": "unrelated fixture"}):
            with self.subTest(change=change):
                journal = self.journal(**{**legacy_resume_data(), **change})
                before = journal.path.read_bytes()
                with self.assertRaises(perf.SmokeFailure):
                    perf.load_existing_manifest(journal.path, self.directory, SITE)
                self.assertEqual(journal.path.read_bytes(), before)

    def test_resume_loader_requires_private_regular_path_in_selected_directory(self):
        journal = self.journal(**legacy_resume_data())
        alias = self.directory / "alias.json"
        alias.symlink_to(journal.path)
        with self.assertRaises(perf.SmokeFailure):
            perf.load_existing_manifest(alias, self.directory, SITE)
        with self.assertRaises(perf.SmokeFailure):
            perf.load_existing_manifest(journal.path, self.directory / "different", SITE)
        journal.path.chmod(0o644)
        with self.assertRaises(perf.SmokeFailure):
            perf.load_existing_manifest(journal.path, self.directory, SITE)

    def test_resume_rejects_unknown_mutation_or_started_cleanup(self):
        changes = [
            {"mutation_outcome_unknown": True}, {"inflight_mutations": {"id": {"operation": "write"}}},
            {"cleanup_complete": True}, {"native_approval_unchanged": False}, {"completed_sources": []},
            {"completed_sources": ["first", "second"]}, {"baseline": {}}, {"real_workflows_before": []},
            {"definitions": [{"deleted": True}]}, {"workflow_actions": {"id": {"deleted": True}}},
            {"source_batches": [{"deleted": ["first"]}]}, {"measurements": []},
        ]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(perf.SmokeFailure):
                perf.validate_resume_manifest({**legacy_resume_data(), **change})

    def test_replacement_restores_open_volume_without_losing_completed_cleanup_target(self):
        runner = self.runner(**legacy_resume_data())
        replacement = runner.routed + " 04251"
        self.assertEqual(runner.open_fixture_count(), 7499)
        runner.journal.data["source_batches"].append({
            "doctype": runner.routed, "actor": "B", "owner": USER_B,
            "names": [replacement], "created": True, "deleted": [],
        })
        physical = sum(len(runner.owned_names(dt)) for dt in (runner.routed, runner.default))
        self.assertEqual((physical, runner.open_fixture_count(), len(runner.owned_open_names(runner.routed))),
                         (7501, 7500, 4250))
        self.assertEqual(physical * perf.CHILD_ROWS, 45006)
        self.assertIn(runner.routed + " 00001", runner.owned_names(runner.routed))
        self.assertNotIn(runner.routed + " 00001", runner.owned_open_names(runner.routed))
        b_owned = {name for batch in runner.journal.data["source_batches"]
                   if batch["doctype"] == runner.routed and batch["actor"] == "B" for name in batch["names"]}
        self.assertEqual(len(b_owned & runner.owned_open_names(runner.routed)), 2125)


class ResumeRunnerTests(OfflineTestCase):
    def resumed_runner(self, *, with_replacement=False):
        runner = self.runner(**legacy_resume_data())
        runner.user_b, runner.role = USER_B, "Accounts User"
        runner.baseline = runner.journal.data["baseline"]
        runner.performance_failures = []
        runner.args.role = ""
        runner.env = {"BROWSER_LOGIN_EMAIL": USER_B, "BROWSER_LOGIN_PASSWORD": "test-placeholder"}
        runner.clients["B"] = SimpleNamespace(label="B", last_status=None, call=Mock(), login=Mock(),
                                               request=Mock(), session=Mock())
        name = runner.routed + " 04251"
        if with_replacement:
            runner.journal.data["measurement_replacement"] = {
                "name": name, "owner": USER_B, "replaces": runner.routed + " 00001",
                "attempted": True, "created": True,
            }
            runner.journal.data["source_batches"].append({
                "doctype": runner.routed, "actor": "B", "owner": USER_B, "names": [name],
                "replacement": True, "created": True, "deleted": [],
            })
        return runner

    def replacement_document(self, runner):
        payload = perf.source_payload(PREFIX, runner.routed, 4251, runner.pending, USER_B)
        return {**payload, "name": payload["title"], "owner": USER_B,
                "lines": [{**line, "idx": index} for index, line in enumerate(payload["lines"], 1)]}

    def test_replacement_is_recorded_before_insert_and_created_only_once(self):
        runner = self.resumed_runner()
        document = self.replacement_document(runner)
        name = document["name"]
        runner.admin.doc.side_effect = [None, document, document]

        def insert(method, *, args, post):
            persisted = json.loads(runner.journal.path.read_text())
            self.assertTrue(persisted["measurement_replacement"]["attempted"])
            self.assertEqual(persisted["source_batches"][-1]["names"], [name])
            self.assertEqual(method, "frappe.client.insert_many")
            self.assertTrue(post)
            self.assertEqual([item["title"] for item in args["docs"]], [name])
            return [name]

        runner.clients["B"].call.side_effect = insert
        runner.ensure_replacement()
        runner.ensure_replacement()
        self.assertEqual(runner.clients["B"].call.call_count, 1)
        self.assertTrue(runner.journal.data["measurement_replacement"]["created"])
        self.assertEqual(sum(name in batch["names"] for batch in runner.journal.data["source_batches"]), 1)

    def test_missing_previously_attempted_replacement_is_never_recreated(self):
        runner = self.resumed_runner(with_replacement=True)
        runner.journal.data["measurement_replacement"]["created"] = False
        runner.admin.doc.return_value = None
        for allow_create in (True, False):
            with self.subTest(allow_create=allow_create), self.assertRaises(perf.SmokeFailure):
                runner.ensure_replacement(allow_create=allow_create)
        runner.clients["B"].call.assert_not_called()

    def test_unrecorded_live_replacement_is_not_adopted(self):
        runner = self.resumed_runner()
        runner.admin.doc.return_value = self.replacement_document(runner)
        with self.assertRaises(perf.SmokeFailure):
            runner.ensure_replacement()
        runner.clients["B"].call.assert_not_called()

    def test_replacement_timeout_preserves_attempt_and_prevents_automatic_retry(self):
        runner = self.resumed_runner()
        runner.admin.doc.return_value = None
        runner.clients["B"].call.side_effect = perf.SmokeFailure("simulated lost insert result")
        with self.assertRaises(perf.SmokeFailure):
            runner.ensure_replacement()
        self.assertTrue(runner.journal.data["mutation_outcome_unknown"])
        self.assertTrue(runner.journal.data["measurement_replacement"]["attempted"])
        with self.assertRaises(perf.SmokeFailure):
            runner.ensure_replacement()
        self.assertEqual(runner.clients["B"].call.call_count, 1)

    def test_read_only_replacement_check_does_not_create_when_absent(self):
        runner = self.resumed_runner()
        runner.admin.doc.return_value = None
        runner.ensure_replacement(allow_create=False)
        runner.clients["B"].call.assert_not_called()
        self.assertNotIn("measurement_replacement", runner.journal.data)

    def test_hidden_detail_probe_uses_open_action_instead_of_completed_action(self):
        runner = self.resumed_runner(with_replacement=True)
        runner.journal.data["workflow_actions"] = {
            "completed": {"name": "completed", "reference_doctype": runner.routed,
                          "reference_name": runner.routed + " 00001", "status": "Completed"},
            "open": {"name": "open", "reference_doctype": runner.routed,
                     "reference_name": runner.routed + " 00002", "status": "Open"},
        }
        expected = runner.baseline["B"]["routed"] + 3250
        runner.clients["B"].call.side_effect = [
            {"items": [], "counts": {"open": expected}, "has_more": False},
            {"counts": {"approvals": expected}, "attention_counts": {"approvals": expected}},
        ]
        runner.clients["B"].request.return_value = {"exc_type": "PermissionError"}
        runner.core_count = Mock(return_value=runner.baseline["B"]["core"] + 7500)
        runner.verify_actor("B", set())
        self.assertEqual(runner.clients["B"].request.call_args.kwargs["params"], {"action_name": "open"})

    def test_resume_exercise_keeps_probe_04250_and_does_not_repeat_native_approval(self):
        runner = self.resumed_runner(with_replacement=True)
        runner.scenario, runner.change_approver = Mock(), Mock()
        runner.verify_actor = Mock(side_effect=lambda actor, names: len(names) + 3250)
        runner.measure, runner.prepare_visual_qa = Mock(), Mock()
        runner.native_approval_proof = Mock(side_effect=AssertionError("Native proof must not repeat"))
        runner.exercise(resume=True)
        self.assertEqual(runner.scenario.call_count, 13)
        self.assertEqual(runner.measure.call_args.args[0], "condition_field_change_without_workflow_rename")
        self.assertEqual([call.args for call in runner.change_approver.call_args_list],
                         [(runner.routed + " 04250", "Administrator"), (runner.routed + " 04250", USER_B)])
        for call in runner.scenario.call_args_list:
            for names in call.args[2].values():
                self.assertNotIn(runner.routed + " 00001", names)
        runner.native_approval_proof.assert_not_called()
        runner.prepare_visual_qa.assert_called_once()

    def test_visual_qa_uses_7500_open_not_7501_physical_or_7499_old_count(self):
        runner = self.resumed_runner(with_replacement=True)
        runner.set_rule = Mock()
        targets = [{"type": "user", "user": "Administrator"}, {"type": "user", "user": USER_B}]
        runner.admin.doc.return_value = {"name": runner.workflows[runner.routed],
            "states": [{"state": runner.pending, perf.FIELD: json.dumps({"version": 1, "targets": targets}), perf.HIDE_FIELD: 0}]}
        for actor, client in runner.clients.items():
            expected = runner.baseline[actor]["routed"] + 7500
            client.call.side_effect = [{"counts": {"approvals": expected}},
                                       {"items": [{}] * 25, "counts": {"open": expected}}]
        runner.prepare_visual_qa()
        runner.set_rule.assert_called_once_with(targets)
        self.assertEqual(runner.journal.data["expected_open_fixture_actions"], 7500)
        self.assertEqual(runner.journal.data["qa_routing_snapshot"]["values"][perf.HIDE_FIELD], 0)

    def test_resume_preflight_preserves_original_baseline_and_business_workflow_snapshot(self):
        runner = self.resumed_runner()
        before = deepcopy(runner.journal.data)
        targets = {"version": 1, "targets": [{"type": "user", "user": "Administrator"}, {"type": "user", "user": USER_B}]}
        values = {perf.FIELD: json.dumps(targets), perf.HIDE_FIELD: 0}
        runner.journal.data["qa_routing_snapshot"] = {"values": values}
        workflow = {"states": [{"state": runner.pending, **values}], "transitions": [{"condition": ""}]}

        def read_doc(dt, name):
            if dt == "User":
                return {"enabled": 1, "user_type": "System User", "roles": [{"role": "Accounts User"}]}
            if name == runner.workflows[runner.routed]:
                return workflow
            return before["real_workflows_before"][0]

        runner.admin.call.return_value = "Administrator"
        runner.admin.doc.side_effect = read_doc
        runner.clients["B"].call.return_value = USER_B
        runner.rows = Mock(side_effect=[[{"name": "routing"}, {"name": "hide"}], [{"name": "patch", "skipped": 0}]])
        runner.verify_retained_inventory = Mock()
        runner.preflight = Mock(side_effect=AssertionError("Do not rebase"))
        runner.resume_preflight()
        self.assertEqual(runner.journal.data["baseline"], before["baseline"])
        self.assertEqual(runner.journal.data["real_workflows_before"], before["real_workflows_before"])
        runner.preflight.assert_not_called()
        runner.verify_retained_inventory.assert_called_once_with()


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
    def run_main(self, mode="normal", *, interrupt_pause=False, resume_data=None, settled_evidence=""):
        trace, instances = [], []
        original_manifest_bytes = None
        argv = ["--run", "--pause-before-cleanup"]
        if mode == "resume":
            old_journal = self.journal(**(legacy_resume_data() if resume_data is None else resume_data))
            old_journal.event("old_round_finished")
            original_manifest_bytes = old_journal.path.read_bytes()
            argv.extend(["--resume-measurements", str(old_journal.path),
                         "--expected-manifest-sha256", hashlib.sha256(original_manifest_bytes).hexdigest(),
                         "--runtime-ref", "abcdef1234", "--stopped-run-evidence", "old process exited"])
            if settled_evidence:
                argv.extend(["--settled-request-evidence", settled_evidence])

        class FakeRunner:
            def __init__(self, env, args, journal):
                self.journal = journal
                self.original_manifest_bytes = original_manifest_bytes
                self.routed = PREFIX + " Routed"
                self.workflows = {self.routed: self.routed + " Flow"}
                self.clients = {"A": SimpleNamespace(session=Mock()), "B": SimpleNamespace(session=Mock())}
                instances.append(self)

            def preflight(self):
                trace.append("preflight")

            def resume_preflight(self):
                trace.append("resume_preflight")

            def ensure_replacement(self):
                trace.append("ensure_replacement")

            def verify_retained_inventory(self, *, require_replacement=False):
                trace.append("verify_replacement_inventory" if require_replacement else "verify_inventory")

            def setup(self):
                trace.append("setup")
                if mode == "unknown":
                    self.journal.data["mutation_outcome_unknown"] = True
                    self.journal.data["inflight_mutations"] = {"request": {"outcome": "unknown"}}
                    raise perf.SmokeFailure("simulated gateway outcome unknown")
                if mode == "known_error":
                    raise perf.SmokeFailure("simulated known HTTP 403")

            def exercise(self, *, resume=False):
                trace.append("exercise_resume" if resume else "exercise")
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
                patch.object(perf.subprocess, "check_output", return_value="fedcba1234\n"), \
                patch("builtins.input", side_effect=KeyboardInterrupt if interrupt_pause else None, return_value=""), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            if interrupt_pause:
                with self.assertRaises(KeyboardInterrupt):
                    perf.main(argv)
                status = None
            else:
                status = perf.main(argv)
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

    def test_main_resume_does_not_run_original_setup_or_rebase_preflight(self):
        status, trace, runner, _, _ = self.run_main("resume")
        self.assertEqual(status, 0)
        self.assertEqual(trace, ["resume_preflight", "ensure_replacement", "verify_replacement_inventory",
                                 "exercise_resume", "pause", "cleanup", "verify_real_workflows"])
        self.assertEqual(len(runner.journal.data["measurement_history"][0]["snapshot"]["measurements"]), 14)
        self.assertEqual(runner.journal.data["baseline"], legacy_resume_data()["baseline"])
        self.assertEqual(runner.journal.data["active_measurement_run"]["runtime_ref"], "abcdef1234")
        self.assertEqual(runner.journal.data["active_measurement_run"]["cli_ref"], "fedcba1234")
        unlocked = perf.ManifestFileLock(runner.journal.path).acquire()
        unlocked.release()
        self.network.assert_not_called()

    def test_main_resume_archives_original_unknown_write_before_recording_settlement(self):
        original = legacy_resume_data()
        inflight = {"old-write": {"operation": "set_fixture_rule", "actor": "A",
                                  "outcome": "unknown", "http_status": 504}}
        original.update(mutation_outcome_unknown=True, inflight_mutations=deepcopy(inflight))
        evidence = "verified old request terminated before resume"
        status, trace, runner, _, _ = self.run_main("resume", resume_data=original, settled_evidence=evidence)
        self.assertEqual(status, 0)
        self.assertEqual(trace[0], "resume_preflight")
        current = runner.journal.data
        archived = current["measurement_history"][0]
        snapshot = archived["snapshot"]
        self.assertTrue(snapshot["mutation_outcome_unknown"])
        self.assertEqual(snapshot["inflight_mutations"], inflight)
        self.assertEqual(snapshot, json.loads(runner.original_manifest_bytes))
        self.assertEqual(json.dumps(snapshot, ensure_ascii=False, indent=2).encode(), runner.original_manifest_bytes)
        self.assertEqual(archived["manifest_sha256"], hashlib.sha256(runner.original_manifest_bytes).hexdigest())
        self.assertNotIn("settled_request_evidence", snapshot)
        self.assertFalse(current["mutation_outcome_unknown"])
        self.assertEqual(current["inflight_mutations"], {})
        self.assertEqual(current["settled_inflight_history"], inflight)
        self.assertEqual(current["settled_request_evidence"], evidence)
        self.network.assert_not_called()

    def test_resume_and_cleanup_entrypoints_reject_an_already_owned_manifest(self):
        journal = self.journal(**legacy_resume_data())
        before = journal.path.read_bytes()
        locked = perf.ManifestFileLock(journal.path).acquire()
        self.addCleanup(locked.release)
        for mode in ("--resume-measurements", "--cleanup-manifest"):
            with self.subTest(mode=mode), patch.object(perf, "config", return_value={"site": SITE}), \
                    patch.object(perf, "private_dir", return_value=self.directory), \
                    patch.object(perf, "PerformanceRunner") as runner, \
                    patch.object(perf.PerfJournal, "flush", side_effect=AssertionError("Locked manifest must not be changed")), \
                    redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(perf.main(["--run", mode, str(journal.path)]), 1)
                runner.assert_not_called()
                self.assertEqual(journal.path.read_bytes(), before)

    def test_resume_sha_failure_happens_before_journal_write_or_runner_creation(self):
        journal = self.journal(**legacy_resume_data())
        before = journal.path.read_bytes()
        with patch.object(perf, "config", return_value={"site": SITE}), \
                patch.object(perf, "private_dir", return_value=self.directory), \
                patch.object(perf, "PerformanceRunner") as runner, \
                patch.object(perf.PerfJournal, "flush", side_effect=AssertionError("SHA failure must not rewrite manifest")), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(perf.main(["--run", "--resume-measurements", str(journal.path),
                                       "--expected-manifest-sha256", "0" * 64]), 1)
            runner.assert_not_called()
        self.assertEqual(journal.path.read_bytes(), before)
        unlocked = perf.ManifestFileLock(journal.path).acquire()
        unlocked.release()


if __name__ == "__main__":
    unittest.main()
