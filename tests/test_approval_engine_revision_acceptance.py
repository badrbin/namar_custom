"""Offline scope/CAS/recovery guards for the TEST-only acceptance helper."""
from contextlib import redirect_stdout
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location("approval_engine_acceptance", Path(__file__).resolve().parents[1] / "scripts/verify_approval_engine_revision.py")
verify = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify)
ORIGINAL = "a" * 64
TAMPER = "test-stale-" + "b" * 32


def control(**values):
    return {"doctype": verify.CONTROL, "name": "current", "epoch": 4, "modified": "before",
            "engine_revision": ORIGINAL, "scan_complete": 1, "scan_cursor": "last", **values}


def status(**values):
    doc = control(**values)
    return {"state": "ready" if doc["scan_complete"] and doc["engine_revision"] == ORIGINAL else "updating",
            "control": doc, "generation": doc["epoch"], "serving_enabled": True, "build_enabled": True,
            "runtime_engine_revision": ORIGINAL, "stored_engine_revision": doc["engine_revision"]}


def counts(state="updating"):
    values = {"mentions": 1, "followups": 2, "approvals": None if state == "updating" else 3,
              "total": None if state == "updating" else 6}
    return {"approval_status": state, "counts": deepcopy(values), "attention_counts": deepcopy(values)}


def runner():
    result = verify.Runner.__new__(verify.Runner)
    result.args = SimpleNamespace(wait_seconds=30, poll_seconds=10)
    result.api, result.engine = "namar_test.followups.api", "namar_test.followups.approval_index"
    result.journal = SimpleNamespace(data={"events": [], "control_before": control(), "tamper_revision": TAMPER,
                                         "tamper_attempted": False, "restore_needed": False}, event=Mock(), flush=Mock())
    result.admin = SimpleNamespace(doc=Mock(return_value=control()), request=Mock(), call=Mock(return_value=status()))
    result.actor = SimpleNamespace(call=Mock())
    return result


class ApprovalEngineAcceptanceGuardTests(unittest.TestCase):
    def test_default_is_offline_without_loading_credentials_or_runner(self):
        output = io.StringIO()
        with patch.object(verify.real, "load_config", side_effect=AssertionError("offline")), \
                patch.object(verify, "Runner", side_effect=AssertionError("offline")), redirect_stdout(output):
            self.assertEqual(verify.main([]), 0)
        plan = json.loads(output.getvalue())
        self.assertFalse(plan["network"])
        self.assertFalse(plan["writes"])
        self.assertFalse(plan["manual_rebuild_or_recovery_calls"])
        self.assertEqual(plan["deliberate_mutations"], 1)

    def test_reuses_test_only_origin_and_namespace_denial_before_network(self):
        for site, namespace in (("https://erp.namar.net", "namar_test"),
                                ("https://zawaya7.frappe.cloud", "namar_test"),
                                ("https://test.example", "namar_custom")):
            args = SimpleNamespace(env_file=Path("unused"), confirm_site=site, timeout=45,
                                   wait_seconds=900, namespace=namespace)
            env = f"FRAPPE_TEST_SITE={site}\nFRAPPE_TEST_TOKEN=dummy"
            with self.subTest(site=site, namespace=namespace), patch.object(Path, "read_text", return_value=env):
                with self.assertRaises(verify.Failure):
                    verify.real.load_config(args)

    def test_control_target_must_be_exact(self):
        for changed in ({"doctype": "Workflow"}, {"name": "another"}, {"epoch": 0}, {"modified": ""}):
            with self.subTest(changed=changed), self.assertRaises(verify.Failure):
                verify.validate_control(control(**changed))

    def test_schema_requires_private_engine_revision_field(self):
        trial = runner()
        metadata = {"name": verify.CONTROL, "fields": [{"fieldname": "engine_revision", "fieldtype": "Data", "hidden": 1, "read_only": 1}]}
        trial.admin.doc.return_value = metadata
        trial.check_schema()
        for field, value in (("fieldtype", "Int"), ("hidden", 0), ("read_only", 0)):
            bad = deepcopy(metadata)
            bad["fields"][0][field] = value
            trial.admin.doc.return_value = bad
            with self.subTest(field=field), self.assertRaises(verify.Failure):
                trial.check_schema()
        trial.admin.request.assert_not_called()

    def test_status_requires_enabled_flags_and_exact_runtime_control_metadata(self):
        for changed in ({"serving_enabled": False}, {"build_enabled": False}, {"runtime_engine_revision": ""},
                        {"stored_engine_revision": "another"}, {"generation": 8}):
            with self.subTest(changed=changed), self.assertRaises(verify.Failure):
                verify.validate_status({**status(), **changed})

    def test_only_one_stamp_put_with_modified_cas_and_no_other_fields(self):
        trial = runner()
        trial.admin.request.return_value = {"data": control(engine_revision=TAMPER, modified="after")}
        trial.tamper_once()
        trial.admin.request.assert_called_once_with("PUT", verify.CONTROL_PATH,
                                                   body={"modified": "before", "engine_revision": TAMPER})
        self.assertTrue(trial.journal.data["restore_needed"])
        self.assertTrue(trial.journal.data["tamper_attempted"])
        with self.assertRaisesRegex(verify.Failure, "only once"):
            trial.tamper_once()
        self.assertEqual(trial.admin.request.call_count, 1)

    def test_compare_and_swap_refuses_any_preexisting_control_change(self):
        trial = runner()
        trial.admin.doc.return_value = control(modified="someone-updated")
        with self.assertRaisesRegex(verify.Failure, "Control changed"):
            trial.tamper_once()
        trial.admin.request.assert_not_called()

    def test_uncertain_tamper_is_recorded_before_write_and_not_retried(self):
        trial = runner()
        trial.admin.request.side_effect = verify.Failure("uncertain")
        with self.assertRaisesRegex(verify.Failure, "uncertain"):
            trial.tamper_once()
        self.assertTrue(trial.journal.data["restore_needed"])
        self.assertTrue(trial.journal.data["tamper_attempted"])
        self.assertEqual(trial.journal.event.call_args_list[0].args, ("mutation_before",))
        with self.assertRaisesRegex(verify.Failure, "only once"):
            trial.tamper_once()
        trial.admin.request.assert_called_once()

    def test_valid_fail_closed_window_accepts_scheduler_already_adopted(self):
        trial = runner()
        trial.admin.call.return_value = status(epoch=5, scan_complete=0)
        trial.actor.call.side_effect = [counts(), {"status": "updating", "items": [], "counts": {"open": None}}]
        trial.observe_fail_closed()
        self.assertTrue(trial.journal.data["fail_closed_observed"])
        event = trial.journal.event.call_args
        self.assertTrue(event.kwargs["scheduler_already_adopted"])
        trial.admin.request.assert_not_called()

    def test_immediate_unknown_must_not_be_fake_zero_or_old_items(self):
        for payload, page in ((counts("ready"), {"status": "updating", "items": [], "counts": {"open": None}}),
                              (counts(), {"status": "updating", "items": [{"name": "old"}], "counts": {"open": None}}),
                              (counts(), {"status": "updating", "items": [], "counts": {"open": 0}})):
            trial = runner()
            trial.actor.call.side_effect = [payload, page]
            with self.subTest(payload=payload, page=page), self.assertRaises(verify.Failure):
                trial.observe_fail_closed()
            trial.admin.request.assert_not_called()

    def test_scheduler_recovery_polls_only_status_and_requires_exactly_one_epoch(self):
        trial = runner()
        trial.admin.call.side_effect = [status(engine_revision=TAMPER), status(epoch=5, scan_complete=0), status(epoch=5)]
        with patch.object(verify.time, "sleep") as sleep:
            trial.await_automatic_recovery()
        self.assertEqual(sleep.call_count, 2)
        self.assertTrue(all(call.args == (10,) for call in sleep.call_args_list))
        self.assertTrue(all(call.args == (trial.engine + ".status",) for call in trial.admin.call.call_args_list))
        trial.admin.request.assert_not_called()
        self.assertEqual(trial.journal.data["recovered_epoch"], 5)
        self.assertTrue(trial.journal.data["automatic_recovery"])

    def test_second_epoch_or_changed_application_fails_without_rebuild_call(self):
        for response in (status(epoch=6), {**status(epoch=5), "runtime_engine_revision": "c" * 64}):
            trial = runner()
            trial.admin.call.return_value = response
            with self.subTest(response=response), self.assertRaises(verify.Failure):
                trial.await_automatic_recovery()
            trial.admin.request.assert_not_called()
            trial.admin.call.assert_called_once_with(trial.engine + ".status")

    def test_wait_timeout_is_bounded_and_never_invokes_manual_recovery(self):
        trial = runner()
        trial.admin.call.return_value = status(engine_revision=TAMPER)
        with patch.object(verify.time, "monotonic", side_effect=[0, 31]), \
                patch.object(verify.time, "sleep") as sleep, self.assertRaises(verify.Failure):
            trial.await_automatic_recovery()
        sleep.assert_not_called()
        trial.admin.call.assert_called_once_with(trial.engine + ".status")
        trial.admin.request.assert_not_called()

    def test_failure_restores_only_own_present_stamp_with_fresh_modified(self):
        trial = runner()
        trial.journal.data.update(tamper_attempted=True, restore_needed=True)
        trial.admin.doc.return_value = control(engine_revision=TAMPER, modified="tampered-time")
        trial.admin.request.return_value = {"data": control(modified="restored-time")}
        trial.restore()
        trial.admin.request.assert_called_once_with("PUT", verify.CONTROL_PATH,
                                                   body={"modified": "tampered-time", "engine_revision": ORIGINAL})
        self.assertFalse(trial.journal.data["restore_needed"])
        self.assertTrue(trial.journal.data["restoration_complete"])

    def test_already_adopted_or_unrelated_stamp_is_never_overwritten(self):
        for stamp in (ORIGINAL, "unrelated-change"):
            trial = runner()
            trial.journal.data.update(tamper_attempted=True, restore_needed=True)
            trial.admin.doc.return_value = control(engine_revision=stamp, epoch=5)
            with self.subTest(stamp=stamp):
                trial.restore()
            trial.admin.request.assert_not_called()
            trial.admin.call.assert_not_called()
            self.assertFalse(trial.journal.data["restore_needed"])

    def test_uncertain_restoration_is_never_repeated(self):
        trial = runner()
        trial.journal.data.update(tamper_attempted=True, restore_needed=True, restore_attempted=True)
        trial.admin.doc.return_value = control(engine_revision=TAMPER)
        with self.assertRaisesRegex(verify.Failure, "never retried"):
            trial.restore()
        trial.admin.request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
