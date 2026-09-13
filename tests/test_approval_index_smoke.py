from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


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


if __name__ == "__main__":
    unittest.main()
