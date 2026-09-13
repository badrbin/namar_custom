from contextlib import redirect_stdout
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location("real_material_request_index", Path(__file__).resolve().parents[1] / "scripts/verify_real_material_request_index.py")
real = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(real)
FAKE_ROW = "test-completed-row"


def workflow():
    return {"doctype": "Workflow", "name": real.WORKFLOW, "document_type": real.DOCTYPE, "is_active": 1,
            "workflow_state_field": "workflow_state", "modified": "before", "modified_by": "Administrator",
            "states": [{"name": FAKE_ROW, "state": real.COMPLETED, real.FIELD: None, real.HIDE: 0},
                       {"name": "other", "state": "Other", real.FIELD: None, real.HIDE: 0}],
            "transitions": [{"state": real.COMPLETED, "action": "Next", "allowed": "User"}]}


class RealMaterialRequestGuardTests(unittest.TestCase):
    def test_default_is_offline_and_does_not_load_credentials(self):
        output = io.StringIO()
        with patch.object(real, "load_config", side_effect=AssertionError("offline")), redirect_stdout(output):
            self.assertEqual(real.main([]), 0)
        self.assertFalse(json.loads(output.getvalue())["network"])

    def test_reuses_dedicated_test_and_production_denial_guard(self):
        args = SimpleNamespace(env_file=Path("unused"), confirm_site="https://erp.namar.net", timeout=45, wait_seconds=900, namespace="namar_test")
        with patch.object(Path, "read_text", return_value="FRAPPE_TEST_SITE=https://erp.namar.net\nFRAPPE_TEST_TOKEN=unused"), self.assertRaises(real.Failure):
            real.load_config(args)

    def test_ordinary_login_must_match_test(self):
        args = SimpleNamespace(env_file=Path("unused"))
        with patch.object(real.common, "config", return_value={"site": "https://test.example"}), \
                patch.object(Path, "read_text", return_value="BROWSER_LOGIN_URL=https://erp.namar.net/login\nBROWSER_LOGIN_EMAIL=x@example.invalid\nBROWSER_LOGIN_PASSWORD=secret"), self.assertRaises(real.Failure):
            real.load_config(args)

    def test_target_is_exact_and_unique(self):
        for field, value in (("name", "other-workflow"), ("document_type", "Sales Order"), ("is_active", 0)):
            doc = workflow()
            doc[field] = value
            with self.subTest(field=field), self.assertRaises(real.Failure):
                real.target_row(doc, FAKE_ROW)
        doc = workflow()
        doc["states"].append({"name": "duplicate", "state": real.COMPLETED})
        with self.assertRaises(real.Failure):
            real.target_row(doc, FAKE_ROW)

    def test_semantic_ignores_only_automatic_modification_and_json_format(self):
        before = real.planned(workflow(), real.OWNER, 0, FAKE_ROW)
        after = deepcopy(before)
        after.update(modified="after", modified_by="someone")
        after["states"][0][real.FIELD] = json.dumps(json.loads(real.OWNER), indent=2)
        self.assertEqual(real.semantic(before), real.semantic(after))
        after["transitions"][0]["allowed"] = "Other role"
        self.assertNotEqual(real.semantic(before), real.semantic(after))

    def test_only_target_settings_are_allowed_to_change(self):
        before = workflow()
        planned = real.planned(before, real.OWNER, 1, FAKE_ROW)
        self.assertEqual(real.without_target_settings(before, FAKE_ROW), real.without_target_settings(planned, FAKE_ROW))
        self.assertIsNone(before["states"][0][real.FIELD])
        planned["states"][1][real.HIDE] = 1
        self.assertNotEqual(real.without_target_settings(before, FAKE_ROW), real.without_target_settings(planned, FAKE_ROW))

    def test_owner_and_hide_remove_only_completed_not_other_doctypes_or_states(self):
        actions = {
            "owned": {"reference_doctype": real.DOCTYPE, "workflow_state": real.COMPLETED, "reference_name": "MR1"},
            "other_owner": {"reference_doctype": real.DOCTYPE, "workflow_state": real.COMPLETED, "reference_name": "MR2"},
            "other_state": {"reference_doctype": real.DOCTYPE, "workflow_state": "Review", "reference_name": "MR3"},
            "other_type": {"reference_doctype": "Sales Order", "workflow_state": real.COMPLETED, "reference_name": "SO1"},
        }
        result = real.expected_sets(set(actions), actions, {"MR1": "viewer", "MR2": "other"}, "viewer")
        self.assertEqual(result["owner"], {"owned", "other_state", "other_type"})
        self.assertEqual(result["hidden"], {"other_state", "other_type"})

    def test_blank_material_request_blocks_workflow_save(self):
        runner = real.Runner.__new__(real.Runner)
        runner.rows = Mock(return_value=[{"name": "MR1", "workflow_state": ""}])
        with self.assertRaisesRegex(real.Failure, "Blank Material Request"):
            runner.business_snapshot()
        runner.rows.assert_called_once()

    def test_compare_and_swap_refuses_unrelated_workflow_change_before_write(self):
        runner = real.Runner.__new__(real.Runner)
        live = workflow()
        live["transitions"][0]["allowed"] = "Changed"
        runner.admin = SimpleNamespace(doc=Mock(return_value=live), request=Mock())
        with self.assertRaisesRegex(real.Failure, "Workflow changed"):
            runner.save(workflow(), real.planned(workflow(), real.OWNER, 0, FAKE_ROW), "owner")
        runner.admin.request.assert_not_called()

    def test_cleanup_refuses_unknown_new_settings(self):
        runner = real.Runner.__new__(real.Runner)
        runner.args = SimpleNamespace(state_row=FAKE_ROW)
        baseline = workflow()
        runner.journal = SimpleNamespace(data={"workflow_before": baseline, "allowed_workflows": [baseline]})
        runner.admin = SimpleNamespace(doc=Mock(return_value=real.planned(baseline, real.OWNER, 0, FAKE_ROW)), request=Mock())
        with self.assertRaisesRegex(real.Failure, "externally"):
            runner.restore()
        runner.admin.request.assert_not_called()

    def test_network_uncertainty_is_journalled_before_the_only_write(self):
        runner = real.Runner.__new__(real.Runner)
        runner.args = SimpleNamespace(state_row=FAKE_ROW)
        baseline = workflow()
        runner.journal = SimpleNamespace(data={"workflow_before": baseline, "allowed_workflows": [baseline]}, event=Mock())
        runner.admin = SimpleNamespace(doc=Mock(return_value=baseline), request=Mock(side_effect=real.Failure("uncertain")))
        runner.assert_business_unchanged = Mock()
        desired = real.planned(baseline, real.OWNER, 0, FAKE_ROW)
        with self.assertRaisesRegex(real.Failure, "uncertain"):
            runner.save(baseline, desired, "owner")
        self.assertTrue(runner.journal.data["restore_needed"])
        self.assertEqual(runner.journal.data["allowed_workflows"][-1], desired)
        runner.admin.request.assert_called_once()
        method, path = runner.admin.request.call_args.args
        self.assertEqual(method, "PUT")
        self.assertIn("/api/resource/Workflow/", path)
        self.assertEqual(set(runner.admin.request.call_args.kwargs["body"]), {"modified", "states"})


if __name__ == "__main__":
    unittest.main()
