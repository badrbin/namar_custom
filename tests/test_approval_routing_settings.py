from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PATH = ROOT / "namar_test" / "followups" / "approval_routing_settings.py"


class Row(dict):
    def __getattr__(self, key):
        return self.get(key)


class Meta(Row):
    def get_field(self, fieldname):
        return next((field for field in self.fields if field.fieldname == fieldname), None)


class ValidationError(Exception):
    pass


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.custom_fields = {}
        self.creations = []
        self.links = {("User", "one@example.com"), ("User", "disabled@example.com"), ("Role", "Accounts User")}
        self.state_meta = Meta(fields=[Row(fieldname="workflow_builder_id", fieldtype="Data")])
        self.document_meta = Meta(fields=[
            Row(fieldname="responsible", fieldtype="Link", options="User"),
            Row(fieldname="employee", fieldtype="Link", options="Employee"),
            Row(fieldname="text_user", fieldtype="Data", options="User"),
            Row(fieldname="children", fieldtype="Table", options="Child Row"),
        ])
        self.frappe = ModuleType("frappe")
        self.frappe.db = Row(exists=lambda doctype, filters: (doctype, filters["name"]) in self.links)
        self.frappe.get_meta = lambda doctype: (
            self.state_meta if doctype == "Workflow Document State" else self.document_meta
        )
        self.frappe.get_all = lambda doctype, **kwargs: [Row(value) for value in self.custom_fields.values()]

        def throw(message, **kwargs):
            raise ValidationError(message)

        self.frappe.throw = throw
        self.create_module = ModuleType("frappe.custom.doctype.custom_field.custom_field")

        def create_custom_fields(payload, update=True):
            self.assertFalse(update)
            self.creations.append(deepcopy(payload))
            for definition in payload["Workflow Document State"]:
                self.custom_fields[definition["fieldname"]] = Row(definition)
                self.state_meta.fields.append(Row(definition))

        self.create_module.create_custom_fields = create_custom_fields
        self.modules = patch.dict(sys.modules, {
            "frappe": self.frappe,
            "frappe.custom.doctype.custom_field.custom_field": self.create_module,
        })
        self.modules.start()
        self.addCleanup(self.modules.stop)
        spec = importlib.util.spec_from_file_location("approval_routing_settings_test", SETTINGS_PATH)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def config(self, targets):
        return json.dumps({"version": 1, "targets": targets})

    def workflow(self, targets=None, raw=None):
        row = Row(state="مراجعة", idx=1)
        if targets is not None or raw is not None:
            row[self.module.ROUTING_FIELD] = raw if raw is not None else self.config(targets)
        return Row(document_type="Material Request", states=[row], transitions=[Row(allowed="Accounts User")])

    def test_missing_configuration_uses_existing_role_default_without_mutation(self):
        doc = self.workflow()
        before = deepcopy(doc)
        self.module.validate_workflow_approval_routing(doc)
        self.assertEqual(doc, before)
        for value in (None, "", "  ", self.config([])):
            self.assertEqual(self.module.parse_routing_targets(value), ())

    def test_union_targets_round_trip_and_deduplicate_per_type(self):
        targets = [
            {"type": "user", "user": " one@example.com "},
            {"type": "owner"},
            {"type": "role", "role": "Accounts User"},
            {"type": "field", "field": "responsible"},
            {"type": "user", "user": "one@example.com"},
            {"type": "owner"},
        ]
        doc = self.workflow(targets)
        transitions = deepcopy(doc.transitions)
        self.module.validate_workflow_approval_routing(doc)
        stored = json.loads(doc.states[0][self.module.ROUTING_FIELD])
        self.assertEqual(stored["targets"], [
            {"type": "user", "user": "one@example.com"}, {"type": "owner"},
            {"type": "role", "role": "Accounts User"}, {"type": "field", "field": "responsible"},
        ])
        self.assertEqual(doc.transitions, transitions)

    def test_disabled_existing_user_is_retained_for_runtime_fallback(self):
        doc = self.workflow([{"type": "user", "user": "disabled@example.com"}])
        self.module.validate_workflow_approval_routing(doc)
        self.assertIn("disabled@example.com", doc.states[0][self.module.ROUTING_FIELD])

    def test_schema_rejects_invalid_shapes_versions_extra_keys_and_incomplete_rows(self):
        invalid = [
            "{bad", "[]", "null", {"version": True, "targets": []},
            {"version": 2, "targets": []}, {"version": 1, "targets": [], "other": 1},
            {"version": 1, "targets": {}}, {"version": 1, "targets": [None]},
            {"version": 1, "targets": [{"type": "employee", "user": "one@example.com"}]},
            {"version": 1, "targets": [{"type": "user"}]},
            {"version": 1, "targets": [{"type": "user", "user": ""}]},
            {"version": 1, "targets": [{"type": "user", "user": 123}]},
            {"version": 1, "targets": [{"type": "owner", "user": "one@example.com"}]},
            {"version": 1, "targets": [{"type": "owner"}] * 51},
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.module.parse_routing_targets(value)

    def test_only_direct_link_user_fields_are_allowed(self):
        self.assertEqual(self.module.get_user_link_fields("Material Request"), ("responsible",))
        for field in ("employee", "text_user", "children.user", "owner", "missing"):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                self.module.validate_workflow_approval_routing(self.workflow([{"type": "field", "field": field}]))

    def test_unknown_user_role_or_field_blocks_new_configuration(self):
        for target in (
            {"type": "user", "user": "missing@example.com"},
            {"type": "role", "role": "Missing Role"},
            {"type": "field", "field": "missing"},
        ):
            with self.subTest(target=target), self.assertRaises(ValidationError):
                self.module.validate_workflow_approval_routing(self.workflow([target]))

    def test_error_message_is_escaped_and_explicitly_rtl(self):
        doc = self.workflow(raw="{bad")
        doc.states[0]["state"] = "<img src=x onerror=alert(1)>"
        with self.assertRaises(ValidationError) as caught:
            self.module.validate_workflow_approval_routing(doc)
        self.assertIn('dir="rtl"', str(caught.exception))
        self.assertIn('text-align:right', str(caught.exception))
        self.assertIn("&lt;img", str(caught.exception))
        self.assertNotIn("<img", str(caught.exception))

    def test_migrator_is_idempotent_and_preserves_existing_customizations(self):
        self.module.configure_approval_routing_fields()
        self.assertEqual(len(self.creations), 1)
        self.assertEqual(len(self.custom_fields), 4)
        self.custom_fields["custom_followups_routing_edit"]["label"] = "تسمية محلية"
        before = deepcopy(self.custom_fields)
        self.module.configure_approval_routing_fields()
        self.assertEqual(len(self.creations), 1)
        self.assertEqual(self.custom_fields, before)

    def test_collision_raises_before_any_field_creation(self):
        name = self.module.ROUTING_FIELD
        self.custom_fields[name] = Row(fieldname=name, fieldtype="Data", hidden=1)
        with self.assertRaises(ValidationError):
            self.module.configure_approval_routing_fields()
        self.assertEqual(self.creations, [])
        self.custom_fields.clear()
        self.state_meta.fields.append(Row(fieldname=name, fieldtype="Long Text", hidden=1))
        with self.assertRaises(ValidationError):
            self.module.configure_approval_routing_fields()
        self.assertEqual(self.creations, [])

    def test_visibility_patch_adds_only_new_checkbox_and_keeps_v8_fields(self):
        self.module.configure_approval_routing_fields()
        before_v8 = deepcopy(self.custom_fields)
        self.assertNotIn(self.module.HIDE_FIELD, before_v8)
        self.module.configure_approval_visibility_fields()
        self.assertEqual(len(self.creations), 2)
        added = self.creations[-1]["Workflow Document State"]
        self.assertEqual([field["fieldname"] for field in added], [self.module.HIDE_FIELD])
        self.assertEqual(added[0]["fieldtype"], "Check")
        self.assertEqual(added[0]["default"], "0")
        self.assertEqual(added[0]["insert_after"], "custom_followups_routing_edit")
        self.assertEqual(added[0]["label"], "إخفاء من موافقات متابعاتي")
        self.assertIn('dir="rtl"', added[0]["description"])
        self.assertIn('text-align:right', added[0]["description"])
        self.assertEqual({key: self.custom_fields[key] for key in before_v8}, before_v8)
        before_repeat = deepcopy(self.custom_fields)
        self.module.configure_approval_visibility_fields()
        self.assertEqual(len(self.creations), 2)
        self.assertEqual(self.custom_fields, before_repeat)

    def test_visibility_collision_is_rejected_without_replacing_existing_field(self):
        name = self.module.HIDE_FIELD
        self.custom_fields[name] = Row(fieldname=name, fieldtype="Data")
        before = deepcopy(self.custom_fields)
        with self.assertRaises(ValidationError):
            self.module.configure_approval_visibility_fields()
        self.assertEqual(self.creations, [])
        self.assertEqual(self.custom_fields, before)

    def test_visibility_flag_retains_targets_and_native_workflow_behavior(self):
        targets = [{"type": "user", "user": "one@example.com"}, {"type": "owner"}]
        for flag in (False, 0, "0", True, 1, "1"):
            with self.subTest(flag=flag):
                doc = self.workflow(targets)
                doc.states[0][self.module.HIDE_FIELD] = flag
                doc.states[0]["is_optional_state"] = 0
                before_transitions = deepcopy(doc.transitions)
                self.module.validate_workflow_approval_routing(doc)
                self.assertEqual(doc.states[0][self.module.HIDE_FIELD], flag)
                self.assertEqual(json.loads(doc.states[0][self.module.ROUTING_FIELD])["targets"], targets)
                self.assertEqual(doc.states[0]["is_optional_state"], 0)
                self.assertEqual(doc.transitions, before_transitions)

    def test_hidden_stage_does_not_bypass_existing_settings_validation(self):
        doc = self.workflow(raw="{bad")
        doc.states[0][self.module.HIDE_FIELD] = 1
        with self.assertRaises(ValidationError):
            self.module.validate_workflow_approval_routing(doc)

    def test_schema_keeps_json_hidden_and_describes_visibility_in_arabic_rtl(self):
        definitions = {df["fieldname"]: df for df in self.module.get_custom_field_definitions()}
        self.assertEqual(definitions[self.module.ROUTING_FIELD]["hidden"], 1)
        self.assertEqual(definitions[self.module.ROUTING_FIELD]["fieldtype"], "Long Text")
        self.assertFalse(any(df["fieldtype"] == "Table" for df in definitions.values()))
        description = definitions["custom_followups_routing_section"]["description"]
        self.assertIn('dir="rtl"', description)
        self.assertIn('text-align:right', description)
        self.assertIn("صلاحيات الاعتماد", description)
        self.assertIn("تحديد جميع", description)


if __name__ == "__main__":
    unittest.main()
