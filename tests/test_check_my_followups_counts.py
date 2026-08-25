from __future__ import annotations

import importlib.util
import json
import stat
import sys
from argparse import Namespace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "check_my_followups_counts.py"
)


def load_script():
    spec = importlib.util.spec_from_file_location("check_my_followups_counts_test", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("تعذر تحميل أداة التحقق")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


class FakeResponse:
    def __init__(self, message: dict, *, secret: str):
        self.status_code = 200
        self._payload = {"message": message}

    def json(self):
        return self._payload


class FakeSession:
    def __init__(
        self,
        counts: list[int],
        *,
        secret: str,
        followups_overdue: int = 3,
        attention_followups: int | None = None,
    ):
        self.headers: dict[str, str] = {}
        self.counts = list(counts)
        self.secret = secret
        self.followups_overdue = followups_overdue
        self.attention_followups = (
            followups_overdue if attention_followups is None else attention_followups
        )
        self.calls: list[dict] = []
        self.source_counts: list[int] = []

    def get(self, url, *, params, timeout, allow_redirects):
        self.calls.append(
            {
                "method": "GET",
                "url": url,
                "params": dict(params),
                "timeout": timeout,
                "allow_redirects": allow_redirects,
            }
        )
        if url.endswith("get_my_followups_counts"):
            mentions, followups, approvals = self.source_counts
            message = {
                "counts": {
                    "mentions": mentions,
                    "followups": followups,
                    "approvals": approvals,
                    "total": mentions + followups + approvals,
                },
                "attention_counts": {
                    "mentions": mentions,
                    "followups": self.attention_followups,
                    "approvals": approvals,
                    "total": mentions + self.attention_followups + approvals,
                }
            }
        else:
            count = self.counts.pop(0)
            self.source_counts.append(count)
            source_counts = {"open": count}
            if url.endswith("get_followups"):
                source_counts["overdue"] = self.followups_overdue
            message = {
                "counts": source_counts,
                "items": [{"description": self.secret, "content": self.secret}],
            }
        return FakeResponse(message, secret=self.secret)


class ConfigGuardTestCase(unittest.TestCase):
    def setUp(self):
        self.module = load_script()
        self.env = {
            "FRAPPE_TEST_SITE": "https://test.example.com",
            "FRAPPE_TEST_TOKEN": "key:top-secret",
            "FRAPPE_PROD_SITE": "https://erp.namar.net",
        }

    @staticmethod
    def args(**overrides):
        values = {
            "confirm_site": "test.example.com",
            "test_site": "",
            "timeout": 30,
        }
        values.update(overrides)
        return Namespace(**values)

    def test_matching_test_host_is_accepted(self):
        config = self.module.validate_run_config(self.args(), self.env)

        self.assertEqual(config.base_url, "https://test.example.com")
        self.assertEqual(config.host, "test.example.com")
        self.assertEqual(config.token, "token key:top-secret")

    def test_mismatched_or_production_hosts_are_rejected(self):
        with self.assertRaisesRegex(self.module.CheckFailure, "يطابق"):
            self.module.validate_run_config(
                self.args(confirm_site="other.example.com"),
                self.env,
            )

        production_env = dict(self.env)
        production_env["FRAPPE_TEST_SITE"] = "https://erp.namar.net"
        with self.assertRaisesRegex(self.module.CheckFailure, "موقع إنتاج"):
            self.module.validate_run_config(
                self.args(confirm_site="erp.namar.net"),
                production_env,
            )


class ReadOnlyContractTestCase(unittest.TestCase):
    def setUp(self):
        self.module = load_script()

    def test_only_four_gets_are_sent_and_transcript_is_redacted(self):
        token = "token key:never-persist-this"
        content_secret = "PRIVATE-DOCUMENT-CONTENT"
        config = self.module.RunConfig(
            base_url="https://test.example.com",
            host="test.example.com",
            token=token,
            timeout=20,
        )
        session = FakeSession([2, 4, 6], secret=content_secret)
        checker = self.module.CountsChecker(config, session=session)
        transcript = self.module.new_transcript(config)

        counts = checker.check(transcript)

        self.assertEqual(
            counts,
            {
                "counts": {
                    "mentions": 2,
                    "followups": 4,
                    "approvals": 6,
                    "total": 12,
                },
                "attention_counts": {
                    "mentions": 2,
                    "followups": 3,
                    "approvals": 6,
                    "total": 11,
                },
            },
        )
        self.assertEqual(len(session.calls), 4)
        self.assertTrue(all(call["method"] == "GET" for call in session.calls))
        self.assertTrue(all(call["allow_redirects"] is False for call in session.calls))
        self.assertTrue(all(call["params"]["page_length"] == 1 for call in session.calls[:3]))
        self.assertEqual(session.calls[3]["params"], {})
        self.assertEqual(
            [call["url"].removeprefix(config.base_url) for call in session.calls],
            [path for _, path, _ in self.module.ENDPOINTS],
        )
        serialized = json.dumps(transcript, ensure_ascii=False)
        self.assertNotIn(token, serialized)
        self.assertNotIn("never-persist-this", serialized)
        self.assertNotIn(content_secret, serialized)
        self.assertNotIn("items", serialized)
        self.assertTrue(all(row["contract"] == "ok" for row in transcript["requests"]))
        self.assertEqual(transcript["requests"][1]["counts_overdue"], 3)

    def test_counts_open_must_be_a_non_negative_integer(self):
        for value in (-1, True, "3", None):
            with self.subTest(value=value):
                with self.assertRaises(self.module.CheckFailure):
                    self.module.validate_open_count("approvals", {"counts": {"open": value}})

    def test_unified_count_contract_requires_exact_sum(self):
        with self.assertRaises(self.module.CheckFailure):
            self.module.validate_unified_counts(
                {
                    "counts": {
                        "mentions": 2,
                        "followups": 4,
                        "approvals": 6,
                        "total": 11,
                    },
                    "attention_counts": {
                        "mentions": 2,
                        "followups": 3,
                        "approvals": 6,
                        "total": 11,
                    },
                }
            )

    def test_unified_count_contract_requires_attention_counts(self):
        with self.assertRaisesRegex(self.module.CheckFailure, "attention_counts"):
            self.module.validate_unified_counts(
                {
                    "counts": {
                        "mentions": 2,
                        "followups": 4,
                        "approvals": 6,
                        "total": 12,
                    }
                }
            )

    def test_checker_rejects_attention_followup_that_differs_from_overdue(self):
        config = self.module.RunConfig(
            base_url="https://test.example.com",
            host="test.example.com",
            token="token key:redacted",
            timeout=20,
        )
        session = FakeSession(
            [2, 4, 6],
            secret="PRIVATE",
            followups_overdue=3,
            attention_followups=4,
        )
        checker = self.module.CountsChecker(config, session=session)

        with self.assertRaisesRegex(self.module.CheckFailure, "عدادات الانتباه"):
            checker.check(self.module.new_transcript(config))

    def test_transcript_is_written_mode_0600_outside_repository(self):
        transcript = {
            "status": "passed",
            "contains_secrets": False,
            "requests": [],
        }
        with TemporaryDirectory() as temporary:
            path = self.module.write_transcript(Path(temporary) / "state", transcript)

            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), transcript)


if __name__ == "__main__":
    unittest.main()
