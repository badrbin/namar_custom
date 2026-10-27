"""Synthetic business cases for the native KDT migration; no site connection."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class KDTError(Exception):
    pass


class PermissionDenied(KDTError):
    pass


class Row(dict):
    def __getattr__(self, key):
        return self.get(key)


def fake_frappe():
    framework = ModuleType("frappe")
    framework.local = SimpleNamespace(site="testnamar.u.frappe.cloud", request=SimpleNamespace(method="GET"))
    framework.session = SimpleNamespace(user="test.operator")
    framework.response = {}
    framework.PermissionError = PermissionDenied
    framework.roles = {"System Manager"}
    framework.allowed_permissions = {"read", "print", "write", "submit", "amend", "cancel"}

    def throw(message, exception=KDTError):
        raise exception(message)

    def only_for(role):
        if role not in framework.roles:
            raise PermissionDenied(role)

    framework.throw = throw
    framework.only_for = only_for
    framework.whitelist = lambda **kwargs: lambda function: function
    framework.new_doc = lambda dt: SimpleNamespace(has_permission=lambda p: p in framework.allowed_permissions)
    framework.utils = SimpleNamespace(
        cint=lambda value: int(value or 0),
        sha256_hash=lambda value: hashlib.sha256(value.encode()).hexdigest(),
    )
    framework.db = SimpleNamespace(
        get_value=lambda *args, **kwargs: "Door Group",
        exists=lambda *args, **kwargs: False,
    )
    framework.get_all = lambda *args, **kwargs: []
    return framework


def load_engine(framework):
    spec = importlib.util.spec_from_file_location("_namar_kdt_engine_test", ROOT / "namar_kdt" / "engine.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"frappe": framework}):
        spec.loader.exec_module(module)
    return module


def load_api(framework, engine):
    package = ModuleType("namar_kdt")
    package.engine = engine
    spec = importlib.util.spec_from_file_location("_namar_kdt_api_test", ROOT / "namar_kdt" / "api.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"frappe": framework, "namar_kdt": package}):
        spec.loader.exec_module(module)
    return module


def rule(station="KDT 550", **overrides):
    return {
        "name": station + " rule", "station": station,
        "operation_mode": "إرسال كود", "program_code": "P1",
        "barcode_type": "QR", **overrides,
    }


def request(**item_overrides):
    item = {
        "name": "SYNTHETIC-ROW", "idx": 1, "parent": "SYNTHETIC-MR",
        "item_code": "W-D100", "item_name": "باب اختبار", "qty": 2,
        "custom_store_data": json.dumps([{"component": "بانل 100×240×4"}], ensure_ascii=False),
        "custom_result_panel_h": 235, "custom_result_panel_w": 90,
        "custom_result_leaf_h": 236, "custom_result_leaf_w": 91,
        **item_overrides,
    }
    return {"name": "SYNTHETIC-MR", "docstatus": 1, "items": [item]}


def allocation(key="C1", **overrides):
    return {
        "piece_key": key, "component_key": "GROUP1", "component": "FRAME",
        "component_label": "حلق اختبار", "group_color": "أبيض", "raw_item": "RAW",
        "quantity": 1.25, "linked_piece_keys": [], "eligible": True,
        "material_request": "SYNTHETIC-MR", "source_row": key + "-ROW",
        "source_signature": "saved-source", **overrides,
    }


class EngineBusinessTests(unittest.TestCase):
    def setUp(self):
        self.frappe = fake_frappe()
        self.engine = load_engine(self.frappe)

    def test_machine_defaults_preserve_different_panel_and_leaf_sources(self):
        cut = self.engine.cut_part_machine(rule(), "KDT 550", 235, 90, 236, 91, 40, 0)
        lock = self.engine.cut_part_machine(rule("KDT 660"), "KDT 660", 235, 90, 236, 91, 40, 0)
        self.assertEqual(cut["payload"], "P1*2350*900*40")
        self.assertEqual(lock["payload"], "P1*2360*910*40")
        self.assertEqual((cut["blocked"], lock["blocked"]), (0, 0))

    def test_machine_program_and_configured_separator_are_not_normalized(self):
        output = self.engine.cut_part_machine(rule(
            program_code=" P2 ", payload_separator=";", dimension_source="الدرفة",
            thickness_source="قيمة ثابتة", output_thickness_mm=38,
        ), "KDT 550", 235, 90, 236, 91, 40, 0)
        self.assertEqual(output["payload"], " P2 ;2360;910;38")

    def test_incomplete_dimensions_block_machine_output(self):
        output = self.engine.cut_part_machine(rule(), "KDT 550", 235, 0, 236, 91, 40, 0)
        self.assertEqual(output["payload"], "")
        self.assertEqual(output["blocked"], 1)

    def test_explicit_station_skip_is_not_a_blocker(self):
        output = self.engine.cut_part_machine(rule("KDT 660", operation_mode="تخطي المحطة"),
                                             "KDT 660", 235, 90, 236, 91, 40, 1)
        self.assertEqual(output["state"], "بدون كالون")
        self.assertEqual(output["payload"], "")
        self.assertEqual(output["blocked"], 0)

    def test_split_widths_produce_stable_piece_keys_and_machine_values(self):
        self.frappe.get_all = lambda *args, **kwargs: [rule(), rule("KDT 660")]
        pieces = self.engine.build_cut_parts(request(
            custom_result_panel_w_text="40 + 50", custom_result_leaf_w_text="41 + 51"))
        self.assertEqual([p["piece_key"] for p in pieces], [
            "SYNTHETIC-ROW|U0001|P01", "SYNTHETIC-ROW|U0001|P02",
            "SYNTHETIC-ROW|U0002|P01", "SYNTHETIC-ROW|U0002|P02",
        ])
        self.assertEqual([p["machine_payload"] for p in pieces],
                         ["P1*2350*400*40", "P1*2350*500*40"] * 2)
        self.assertEqual([p["kalon_payload"] for p in pieces],
                         ["P1*2360*410*40", "P1*2360*510*40"] * 2)
        self.assertTrue(all(p["eligible"] for p in pieces))
        self.assertFalse(any("SYNTHETIC" in p["machine_payload"] for p in pieces))

    def test_cancelled_request_rows_are_excluded(self):
        self.assertEqual(self.engine.build_cut_parts(request(custom_is_cancelled_row=1)), [])

    def test_fractional_door_quantity_stays_visible_but_ineligible(self):
        self.frappe.get_all = lambda *args, **kwargs: [rule(), rule("KDT 660")]
        pieces = self.engine.build_cut_parts(request(qty=1.5))
        self.assertTrue(pieces)
        self.assertFalse(any(p["eligible"] for p in pieces))
        self.assertIn("كمية الباب", pieces[0]["blocker"])

    def test_rule_edit_changes_source_signature(self):
        self.frappe.get_all = lambda *args, **kwargs: [rule(), rule("KDT 660")]
        before = self.engine.build_cut_parts(request())[0]
        self.frappe.get_all = lambda *args, **kwargs: [rule(program_code="P2"), rule("KDT 660")]
        after = self.engine.build_cut_parts(request())[0]
        self.assertNotEqual(before["source_signature"], after["source_signature"])
        self.assertEqual(before["piece_key"], after["piece_key"])

    def test_shared_machine_label_requires_matching_payload_and_barcode_type(self):
        piece = allocation(machine_payload="P1*100*200*40", kalon_payload="P1*100*200*40",
                           barcode_type="QR", kalon_barcode_type="QR")
        combined = self.engine.render_cut_labels("SYNTHETIC", [piece])
        self.assertIn('data-label-count="1"', combined)
        separate = self.engine.render_cut_labels("SYNTHETIC", [{**piece, "kalon_barcode_type": "Code128"}])
        self.assertIn('data-label-count="2"', separate)

    def test_printed_order_name_is_html_escaped(self):
        html = self.engine.render_cut_labels('<script>alert("x")</script>', [])
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)

    def test_scope_uses_verified_request_names_instead_of_forged_filter(self):
        scope = self.engine.kc_scope({"request_names": ["B", "A", "B"], "filters": {
            "material_request": "OTHER", "material_requests": ["OTHER"], "colors": ["أبيض"]}})
        self.assertEqual(scope["request_names"], ["A", "B"])
        self.assertEqual(scope["filters"], {"material_requests": ["A", "B"], "colors": ["أبيض"]})

    def test_scope_rejects_missing_names_and_overlarge_request_sets(self):
        for names in ([], list(map(str, range(41)))):
            with self.subTest(names=len(names)), self.assertRaises(KDTError):
                self.engine.kc_scope({"request_names": names})

    def test_create_scope_must_cover_the_whole_filtered_report(self):
        build = {"groups": [{"component_key": "A"}, {"component_key": "B"}], "allocations": {}}
        with self.assertRaisesRegex(KDTError, "جميع المكونات"):
            self.engine.kc_report_keys({"component_keys": ["A"]}, build)

    def test_partial_prior_claim_excludes_full_source_quantity(self):
        source = allocation(linked_piece_keys=["P1", "P2"], quantity=4)
        build = {"groups": [{"component_key": "GROUP1"}], "allocations": {"C1": source},
                 "panels": {"P1": {}, "P2": {}}}
        scope = {"request_names": ["SYNTHETIC-MR"], "component_keys": ["GROUP1"]}
        result = self.engine.kc_report_data(scope, build, {"P1": Row(state="reserved", current_order="OLD")}, {})
        row = result["rows"][0]
        self.assertEqual(row["already_created_quantity"], 4)
        self.assertEqual(row["available_quantity"], 0)
        self.assertEqual(row["sources"][0]["coverage"], "partial")
        self.assertFalse(result["can_create"])

    def test_one_blocked_source_prevents_partial_creation(self):
        sources = {"C1": allocation(), "C2": allocation("C2", eligible=False, blocker="بيانات ناقصة")}
        build = {"groups": [{"component_key": "GROUP1"}], "allocations": sources, "panels": {}}
        scope = {"request_names": ["SYNTHETIC-MR"], "component_keys": ["GROUP1"]}
        result = self.engine.kc_report_data(scope, build, {}, {})
        self.assertEqual(result["counts"], {"available": 1, "already_created": 0, "blocked": 1})
        self.assertFalse(result["can_create"])

    def test_component_grouping_keeps_precision_and_completed_sources_noneditable(self):
        result = self.engine.kc_rows([allocation(quantity=0.125), allocation("C2", quantity=0.225, state="completed")])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["quantity"], 0.35)
        self.assertEqual(result[0]["state"], "reserved")
        self.assertFalse(result[0]["editable"])

    def test_repeated_command_returns_saved_result_without_a_write(self):
        payload = {"request_id": "synthetic-request", "request_names": ["SYNTHETIC-MR"]}
        fingerprint = self.engine.kd_dump({"action": "component_create_available", "payload": payload,
                                           "user": self.frappe.session.user})
        command = Row(user=self.frappe.session.user, fingerprint=fingerprint,
                      response='{"changed":false,"order":{"name":"EXISTING"}}')
        self.frappe.db.exists = lambda *args: True
        checks = []
        self.frappe.get_doc = lambda dt, name: (command if dt == "KDT Cut Command" else
                                                SimpleNamespace(check_permission=lambda p: checks.append((name, p))))
        result = self.engine.kc_mutate("create_available", payload)
        self.assertEqual(result, {"changed": False, "order": {"name": "EXISTING"}})
        self.assertEqual(checks, [("SYNTHETIC-MR", "read")])

    def test_reused_command_id_with_changed_payload_or_user_is_rejected(self):
        command = Row(user="another.operator", fingerprint="old", response='{}')
        with self.assertRaisesRegex(KDTError, "مستخدم لطلب مختلف"):
            self.engine.kc_command_result(command, "new", {})

    def test_guest_or_missing_submit_permission_cannot_approve(self):
        self.frappe.session.user = "Guest"
        with self.assertRaisesRegex(KDTError, "سجّل الدخول"):
            self.engine.kd_permission("approve")
        self.frappe.session.user = "test.operator"
        self.frappe.allowed_permissions.remove("submit")
        with self.assertRaisesRegex(KDTError, "صلاحية"):
            self.engine.kd_permission("approve")

    def test_dispatch_validates_json_object(self):
        for value in ("not json", "[]", [1]):
            with self.subTest(value=value), self.assertRaises(KDTError):
                self.engine.dispatch("search", value)

    def test_cancellation_locks_do_not_leak_between_requests(self):
        observations = []

        def dispatch_probe(action, payload):
            rows, locks = self.engine._cancellation_state()
            observations.append((dict(rows), dict(locks)))
            rows["ROW"] = {"custom_is_cancelled_row": 1}
            locks["MR"] = 1
            if payload.get("fail"):
                raise KDTError("rollback")
            return self.engine.kdt_source_is_cancelled({"name": "ROW", "parent": "MR"})

        self.engine.kd_dispatch = dispatch_probe
        self.assertTrue(self.engine.dispatch("search", {}))
        with self.assertRaises(KDTError):
            self.engine.dispatch("search", {"fail": True})
        self.assertTrue(self.engine.dispatch("search", {}))
        self.assertEqual(observations, [({}, {})] * 3)


class APIBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.frappe = fake_frappe()
        self.calls = []
        self.engine = SimpleNamespace(dispatch=lambda action, payload: self.calls.append((action, payload)) or {"ok": True})
        self.api = load_api(self.frappe, self.engine)
        self.enabled = True
        self.api._enabled = lambda: self.enabled

    def test_wrong_site_is_rejected_before_dispatch(self):
        self.frappe.local.site = "erp.namar.net"
        with self.assertRaises(PermissionDenied):
            self.api.call()
        self.assertEqual(self.calls, [])

    def test_every_write_action_requires_post(self):
        actions = ["approve", "revise", "complete", "correct", "recut", "cancel"]
        actions += ["component_" + action for action in actions] + ["component_create_available"]
        for action in actions:
            with self.subTest(action=action), self.assertRaises(PermissionDenied):
                self.api.call(action, {})
        self.assertEqual(self.calls, [])

    def test_active_post_mutation_reaches_existing_service(self):
        self.frappe.local.request.method = "POST"
        self.assertEqual(self.api.call("component_create_available", {"request_id": "TEST"}), {"ok": True})
        self.assertEqual(self.calls, [("component_create_available", {"request_id": "TEST"})])

    def test_inactive_service_allows_administrator_reads_only(self):
        self.enabled = False
        self.assertEqual(self.api.call("report_status", {}), {"ok": True})
        self.frappe.local.request.method = "POST"
        with self.assertRaises(PermissionDenied):
            self.api.call("component_create_available", {})
        self.frappe.roles.clear()
        with self.assertRaises(PermissionDenied):
            self.api.call("report_status", {})
        self.assertEqual(self.calls, [("report_status", {})])

    def test_inactive_alias_runs_existing_script_once(self):
        self.enabled = False
        executions = []

        def execute():
            executions.append(True)
            self.frappe.response["message"] = {"legacy": True}

        self.frappe.get_doc = lambda *args: SimpleNamespace(disabled=0, execute_method=execute)
        self.assertEqual(self.api.legacy_call("search", {}), {"legacy": True})
        self.assertEqual(executions, [True])
        self.assertEqual(self.calls, [])

    def test_disabled_legacy_script_is_not_silently_executed(self):
        self.enabled = False
        self.frappe.get_doc = lambda *args: SimpleNamespace(disabled=1)
        with self.assertRaises(KDTError):
            self.api.legacy_call()


if __name__ == "__main__":
    unittest.main()
