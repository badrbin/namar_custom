from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


SPEC = importlib.util.spec_from_file_location("approval_index_smoke", Path(__file__).resolve().parents[1] / "scripts/smoke_test_approval_index.py")
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


class ApprovalIndexSmokeGuardTests(unittest.TestCase):
    def test_default_is_offline_and_does_not_read_environment(self):
        output = io.StringIO()
        with patch.object(smoke, "config", side_effect=AssertionError("must not read secrets")), redirect_stdout(output):
            self.assertEqual(smoke.main([]), 0)
        self.assertFalse(json.loads(output.getvalue())["network"])

    def test_rejects_non_https_credentials_and_paths(self):
        for value in ("http://test.example", "https://user:secret@test.example", "https://test.example/app", "https://test.example:8443"):
            with self.subTest(value=value), self.assertRaises(smoke.Failure):
                smoke.origin(value)

    def test_rejects_dedicated_test_variable_pointing_to_production(self):
        args = SimpleNamespace(env_file=Path("unused"), confirm_site="https://erp.namar.net", timeout=45, wait_seconds=60, namespace="namar_test")
        content = "FRAPPE_TEST_SITE=https://erp.namar.net\nFRAPPE_TEST_TOKEN=unused\n"
        with patch.object(Path, "read_text", return_value=content), self.assertRaises(smoke.Failure):
            smoke.config(args)

    def test_rejects_prod_namespace_even_for_test_origin(self):
        args = SimpleNamespace(env_file=Path("unused"), confirm_site="https://test.example", timeout=45, wait_seconds=60, namespace="namar_custom")
        with patch.object(Path, "read_text", return_value="FRAPPE_TEST_SITE=https://test.example\nFRAPPE_TEST_TOKEN=unused"), self.assertRaises(smoke.Failure):
            smoke.config(args)

    def test_redacts_nested_credentials(self):
        self.assertEqual(smoke.redact({"new_password": "secret", "nested": [{"api_secret": "secret", "name": "fixture"}]}),
                         {"new_password": "[REDACTED]", "nested": [{"api_secret": "[REDACTED]", "name": "fixture"}]})

    def test_journal_is_private_and_contains_no_password(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "receipt.json"
            journal = smoke.Journal(path, {"events": []})
            journal.event("fixture_create", intended={"new_password": "test-secret"})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertNotIn("test-secret", path.read_text())

    def test_fixture_scope_refuses_real_documents_and_users(self):
        journal = SimpleNamespace(data={"prefix": "NAI Smoke 20260913100000 deadbeef"})
        runner = smoke.Runner({"site": "https://test.example", "token": ""}, SimpleNamespace(namespace="namar_test"), journal)
        try:
            self.assertTrue(runner.allowed("User", "index-20260913100000-deadbeef-a@example.invalid"))
            self.assertFalse(runner.allowed("User", "Administrator"))
            self.assertFalse(runner.allowed("Material Request", "MREQ-123"))
            self.assertFalse(runner.allowed("Workflow", "طلب مواد"))
            self.assertTrue(runner.allowed("Workflow", runner.prefix + " Flow"))
        finally:
            for client in (runner.admin, *runner.clients.values()):
                client.session.close()


class ApprovalIndexSmokeReadinessTests(unittest.TestCase):
    @staticmethod
    def counts(state="ready"):
        if state == "ready":
            values = {"mentions": 0, "followups": 0, "approvals": 3, "total": 3}
        else:
            values = {"mentions": 0, "followups": 0, "approvals": None, "total": None}
        return {"approval_status": state, "counts": dict(values), "attention_counts": dict(values)}

    def runner(self, administrative_state="ready", actor_states=None):
        runner = smoke.Runner.__new__(smoke.Runner)
        runner.args = SimpleNamespace(wait_seconds=10)
        runner.api, runner.engine = "api", "engine"
        runner.admin = SimpleNamespace(call=Mock(return_value={"state": administrative_state, "generation": 7, "serving_enabled": True}))
        runner.clients = {actor: SimpleNamespace(call=Mock(return_value=self.counts((actor_states or {}).get(actor, "ready"))))
                          for actor in "ABC"}
        runner.journal = SimpleNamespace(event=Mock())
        return runner

    def test_unrelated_administrative_error_does_not_block_ready_actors(self):
        runner = self.runner("error")
        state = runner.wait_ready()
        self.assertEqual(state["state"], "error")  # Diagnostic is preserved, not rewritten as ready.
        self.assertEqual(state["actor_states"], {actor: "ready" for actor in "ABC"})
        runner.admin.call.assert_called_once_with("engine.status")
        runner.journal.event.assert_called_once()

    def test_actor_error_is_not_treated_as_unrelated_or_transient(self):
        runner = self.runner("ready", {"B": "error"})
        with patch.object(smoke.time, "sleep") as sleep, self.assertRaisesRegex(smoke.Failure, "B: index reported error"):
            runner.wait_ready()
        sleep.assert_not_called()
        runner.admin.call.assert_called_once()

    def test_actor_updating_null_waits_and_then_becomes_ready(self):
        runner = self.runner("error")
        runner.clients["A"].call.side_effect = [self.counts("updating"), self.counts()]
        with patch.object(smoke.time, "sleep") as sleep:
            self.assertEqual(runner.wait_ready()["actor_states"]["A"], "ready")
        sleep.assert_called_once_with(2)

    def test_updating_fake_zero_missing_null_and_ready_null_fail(self):
        for state, section, field, value in (("updating", "counts", "approvals", 0),
                                             ("updating", "attention_counts", "total", 0),
                                             ("ready", "counts", "approvals", None),
                                             ("ready", "attention_counts", "approvals", True)):
            with self.subTest(state=state, section=section, field=field):
                payload = self.counts(state)
                payload[section][field] = value
                with self.assertRaises(smoke.Failure):
                    smoke.Runner.count_state(payload, "A")
        payload = self.counts("updating")
        del payload["counts"]["total"]
        with self.assertRaises(smoke.Failure):
            smoke.Runner.count_state(payload, "A")

    def test_only_valid_empty_updating_page_is_transient(self):
        with self.assertRaises(smoke.TransientIndexUpdate):
            smoke.Runner.require_page_ready({"status": "updating", "items": [], "counts": {"open": None}}, "A")
        for payload in ({"status": "error", "items": [], "counts": {"open": None}},
                        {"status": "updating", "items": [], "counts": {"open": 0}},
                        {"status": "updating", "items": [{"name": "private"}], "counts": {"open": None}}):
            with self.subTest(payload=payload), self.assertRaises(smoke.Failure) as failure:
                smoke.Runner.require_page_ready(payload, "A")
            self.assertNotIsInstance(failure.exception, smoke.TransientIndexUpdate)

    def test_updating_detail_with_document_information_is_not_retried(self):
        with self.assertRaises(smoke.TransientIndexUpdate):
            smoke.Runner.require_detail_ready({"status": "updating", "message": "wait"}, "A")
        for payload in ({"status": "error"}, {"status": "updating", "approval": {"name": "private"}}):
            with self.subTest(payload=payload), self.assertRaises(smoke.Failure) as failure:
                smoke.Runner.require_detail_ready(payload, "A")
            self.assertNotIsInstance(failure.exception, smoke.TransientIndexUpdate)

    def test_transient_read_retries_once_without_repeating_writes(self):
        runner = self.runner()
        runner.check_ready = Mock(side_effect=[smoke.TransientIndexUpdate("changed"), "passed"])
        runner.update = Mock(side_effect=AssertionError("read retry cannot mutate"))
        self.assertEqual(runner.check("scenario", {}), "passed")
        self.assertEqual(runner.check_ready.call_count, 2)
        runner.update.assert_not_called()

    def test_real_failure_is_never_retried(self):
        runner = self.runner()
        runner.check_ready = Mock(side_effect=smoke.Failure("visibility mismatch"))
        with self.assertRaisesRegex(smoke.Failure, "visibility mismatch"):
            runner.check("scenario", {})
        runner.check_ready.assert_called_once()

    def test_transient_retries_are_bounded_to_two_read_attempts(self):
        runner = self.runner()
        runner.check_ready = Mock(side_effect=smoke.TransientIndexUpdate("changed"))
        with self.assertRaisesRegex(smoke.Failure, "both bounded"):
            runner.check("scenario", {})
        self.assertEqual(runner.check_ready.call_count, 2)


if __name__ == "__main__":
    unittest.main()
