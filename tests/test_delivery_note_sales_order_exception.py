"""Offline regression coverage for the narrowly scoped Delivery Note override.

The guard fixture is the existing production Server Script, not a permissive
mock. Its quantity SQL runs against SQLite with only parameter syntax adapted.
Frappe's document lifecycle and safe_exec sandbox require separate site testing.
"""

from __future__ import annotations

import importlib.util
import runpy
import sqlite3
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
OVERRIDE_PATH = ROOT / "namar_test" / "overrides" / "delivery_note.py"
GUARD_PATH = Path(__file__).parent / "fixtures" / "validate_delivery_note_sales_order_policy.py"
POLICY_NAME = "Validate Delivery Note Sales Order Policy"
RULE_DOCTYPE = "Material Request Scenario Rule"

# Verbatim ERPNext v15.121.2 DeliveryNote.so_required, apart from indentation.
# Keeping the real condition prevents a no-op fake parent from masking bypasses.
CORE_SO_REQUIRED = '''def so_required(self):
    """check in manage account if sales order required or not"""
    if frappe.db.get_single_value("Selling Settings", "so_required") == "Yes":
        for d in self.get("items"):
            if not d.against_sales_order:
                frappe.throw(_("Sales Order required for Item {0}").format(d.item_code))
'''


class AttrDict(dict):
    def __getattribute__(self, key):
        if not key.startswith("__") and key in self:
            return dict.__getitem__(self, key)
        return super().__getattribute__(key)

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc

    def __setattr__(self, key, value):
        self[key] = value


class ValidationError(Exception):
    pass


def throw(msg=None, *, title=None):
    raise ValidationError(msg)


class ReadOnlyDB:
    """Only the reads used by the override and existing policy are exposed."""

    def __init__(self):
        self.so_required = "Yes"
        self.schema_exists = True
        self.policy_exists = True
        self.request = AttrDict(
            docstatus=1,
            custom_request_scenario="نواقص",
            sales_order="SO-TEST",
            company="Company A",
        )
        self.order = AttrDict(docstatus=1, customer="Customer A", company="Company A")
        self.rules = {
            scenario: AttrDict(enabled=1, link_delivery_note_to_sales_order=value)
            for scenario, value in {
                "نواقص": 0, "استبدال": 0, "صيانة": 0, "تصنيع": 1, "قطاعات": 1
            }.items()
        }
        self.reads = []
        self.sql_queries = []
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript('''
            CREATE TABLE `tabMaterial Request Item`
                (parent TEXT, item_code TEXT, qty REAL, conversion_factor REAL);
            CREATE TABLE `tabDelivery Note`
                (name TEXT, custom_material_request TEXT, docstatus INTEGER);
            CREATE TABLE `tabDelivery Note Item`
                (parent TEXT, item_code TEXT, qty REAL, conversion_factor REAL);
        ''')
        self.add_requested("ITEM-A", 3)
        self.add_requested("ITEM-B", 5)

    def add_requested(self, item_code, qty, conversion_factor=1, parent="MREQ-TEST"):
        self.connection.execute(
            "INSERT INTO `tabMaterial Request Item` VALUES (?, ?, ?, ?)",
            (parent, item_code, qty, conversion_factor),
        )

    def add_delivery(self, name, qty, conversion_factor=1, *, item_code="ITEM-A",
                     docstatus=1, material_request="MREQ-TEST"):
        self.connection.execute(
            "INSERT INTO `tabDelivery Note` VALUES (?, ?, ?)",
            (name, material_request, docstatus),
        )
        self.connection.execute(
            "INSERT INTO `tabDelivery Note Item` VALUES (?, ?, ?, ?)",
            (name, item_code, qty, conversion_factor),
        )

    def exists(self, doctype, name):
        self.reads.append(("exists", doctype, name))
        if (doctype, name) == ("DocType", RULE_DOCTYPE):
            return self.schema_exists
        if (doctype, name) == ("Server Script", POLICY_NAME):
            return self.policy_exists
        raise AssertionError((doctype, name))

    def get_value(self, doctype, name_or_filters, fields, as_dict=False):
        self.reads.append(("get_value", doctype, name_or_filters))
        if doctype == "Material Request":
            record = self.request if name_or_filters == "MREQ-TEST" else None
        elif doctype == "Sales Order":
            record = self.order if name_or_filters == "SO-TEST" else None
        elif doctype == RULE_DOCTYPE:
            assert name_or_filters.get("enabled") == 1
            record = self.rules.get(name_or_filters["scenario_name"])
            if record and not record.enabled:
                record = None
        else:
            raise AssertionError((doctype, name_or_filters, fields))
        if record is None:
            return None
        if as_dict:
            return AttrDict({field: record.get(field) for field in fields})
        return record.get(fields)

    def get_single_value(self, doctype, field):
        assert (doctype, field) == ("Selling Settings", "so_required")
        self.reads.append(("get_single_value", doctype, field))
        return self.so_required

    def sql(self, query, params, as_dict=False):
        assert query.split()[0].upper() == "SELECT", "Policy must be read-only"
        assert as_dict is True
        self.sql_queries.append((query, params))
        cursor = self.connection.execute(query.replace("%s", "?"), params)
        return [AttrDict(row) for row in cursor.fetchall()]


class PolicyScript(AttrDict):
    def __init__(self, frappe):
        super().__init__(
            disabled=0, script_type="DocType Event", reference_doctype="Delivery Note",
            script=GUARD_PATH.read_text(encoding="utf-8"),
        )
        self.frappe = frappe
        self.calls = []
        self.error = None

    def execute_doc(self, doc):
        self.calls.append((doc.get("_action"), doc.get("docstatus")))
        if self.error:
            raise self.error
        namespace = {"frappe": self.frappe, "doc": doc}
        exec(compile(self.script, str(GUARD_PATH), "exec"), namespace)


class ScenarioDeliveryNoteTests(unittest.TestCase):
    def setUp(self):
        self.db = ReadOnlyDB()
        self.addCleanup(self.db.connection.close)
        frappe = types.ModuleType("frappe")
        frappe.db = self.db
        frappe.throw = throw
        frappe.utils = types.ModuleType("frappe.utils")
        frappe.utils.cint = lambda value: int(value or 0)
        frappe.utils.flt = lambda value: float(value or 0)
        self.policy = PolicyScript(frappe)

        def get_doc(doctype, name):
            self.assertEqual((doctype, name), ("Server Script", POLICY_NAME))
            return self.policy

        frappe.get_doc = get_doc
        core_namespace = {"frappe": frappe, "_": lambda value: value}
        exec(CORE_SO_REQUIRED, core_namespace)
        self.core_calls = []

        def counted_core(doc):
            self.core_calls.append(doc.get("_action"))
            return core_namespace["so_required"](doc)

        parent = type("DeliveryNote", (AttrDict,), {"so_required": counted_core})
        erpnext_path = "erpnext.stock.doctype.delivery_note.delivery_note"
        erpnext_module = types.ModuleType(erpnext_path)
        erpnext_module.DeliveryNote = parent
        injected = {"frappe": frappe, "frappe.utils": frappe.utils, erpnext_path: erpnext_module}
        # An explicit source load prevents test order or installed Frappe modules
        # from selecting a cached class with another test's globals.
        spec = importlib.util.spec_from_file_location("scenario_delivery_note_test", OVERRIDE_PATH)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, injected):
            spec.loader.exec_module(module)
        self.note = module.ScenarioDeliveryNote(
            doctype="Delivery Note", name="DN-CURRENT", custom_material_request="MREQ-TEST",
            customer="Customer A", company="Company A", docstatus=0, _action="save",
            items=[self.item("ITEM-A", 3), self.item("ITEM-B", 5, idx=2)],
            flags=AttrDict(),
        )

    @staticmethod
    def item(item_code, qty, *, idx=1, conversion_factor=1, **fields):
        return AttrDict(item_code=item_code, qty=qty, idx=idx,
                        conversion_factor=conversion_factor, against_sales_order=None,
                        so_detail=None, **fields)

    def assert_core_rejects(self):
        previous = len(self.core_calls)
        with self.assertRaisesRegex(ValidationError, "Sales Order required for Item"):
            self.note.so_required()
        self.assertEqual(len(self.core_calls), previous + 1)
        self.assertEqual(self.policy.calls, [])
        self.assertEqual(self.db.so_required, "Yes")

    def assert_policy_rejects(self, message):
        with self.assertRaisesRegex(ValidationError, message):
            self.note.so_required()
        self.assertEqual(self.core_calls, [])

    def test_configured_exceptions_pass_with_global_requirement_enabled(self):
        for scenario in ("نواقص", "استبدال", "صيانة"):
            for action, docstatus in (("save", 0), ("submit", 1)):
                with self.subTest(scenario=scenario, action=action):
                    self.db.request.custom_request_scenario = scenario
                    self.note._action, self.note.docstatus = action, docstatus
                    before = len(self.policy.calls)
                    self.note.so_required()
                    self.assertEqual(self.policy.calls[before:], [(action, docstatus)])
                    self.assertEqual(self.db.so_required, "Yes")
                    self.assertEqual(self.note.flags, {})
                    self.assertTrue(all(not row.against_sales_order for row in self.note.items))
        self.assertEqual(self.core_calls, [])

    def test_normal_unknown_and_blank_scenarios_fall_back_to_core(self):
        for scenario in ("تصنيع", "قطاعات", "UNKNOWN", "", "   ", None):
            with self.subTest(scenario=scenario):
                self.db.request.custom_request_scenario = scenario
                self.assert_core_rejects()

    def test_missing_disabled_and_unconfigured_rules_fall_back_to_core(self):
        for rule in (None, AttrDict(enabled=0, link_delivery_note_to_sales_order=0),
                     AttrDict(enabled=1, link_delivery_note_to_sales_order=None)):
            with self.subTest(rule=rule):
                self.db.rules["نواقص"] = rule
                self.assert_core_rejects()

    def test_missing_rule_schema_falls_back_to_core(self):
        self.db.schema_exists = False
        self.assert_core_rejects()

    def test_blank_or_missing_material_request_falls_back_to_core(self):
        for material_request in (None, "", "  ", "MREQ-MISSING"):
            with self.subTest(material_request=material_request):
                self.note.custom_material_request = material_request
                self.assert_core_rejects()

    def test_draft_or_cancelled_material_request_falls_back_to_core(self):
        for docstatus in (0, 2):
            with self.subTest(docstatus=docstatus):
                self.db.request.docstatus = docstatus
                self.assert_core_rejects()

    def test_missing_policy_falls_back_to_core(self):
        self.db.policy_exists = False
        self.assert_core_rejects()

    def test_disabled_empty_or_inapplicable_policy_falls_back_to_core(self):
        cases = (("disabled", 1), ("disabled", "1"), ("script", ""),
                 ("script", "   "), ("reference_doctype", "Sales Invoice"),
                 ("script_type", "API"))
        for field, value in cases:
            with self.subTest(field=field, value=value):
                original = self.policy[field]
                self.policy[field] = value
                self.assert_core_rejects()
                self.policy[field] = original

    def test_core_checks_every_row_and_preserves_normal_linked_delivery(self):
        self.db.request.custom_request_scenario = "تصنيع"
        self.note.items[0].against_sales_order = "SO-TEST"
        with self.assertRaisesRegex(ValidationError, "Sales Order required for Item ITEM-B"):
            self.note.so_required()
        self.note.items[1].against_sales_order = "SO-TEST"
        self.note.so_required()
        self.assertEqual(self.core_calls, ["save", "save"])
        self.assertEqual(self.policy.calls, [])

    def test_core_respects_setting_when_no_exception_applies(self):
        self.db.request.custom_request_scenario = "تصنيع"
        self.db.so_required = "No"
        self.note.so_required()
        self.assertEqual(self.core_calls, ["save"])
        self.assertEqual(self.db.so_required, "No")

    def test_policy_exception_propagates_on_save_and_submit(self):
        error = ValidationError("guard denied this document")
        self.policy.error = error
        for action, docstatus in (("save", 0), ("submit", 1)):
            with self.subTest(action=action):
                self.note._action, self.note.docstatus = action, docstatus
                with self.assertRaises(ValidationError) as raised:
                    self.note.so_required()
                self.assertIs(raised.exception, error)
        self.assertEqual(self.policy.calls, [("save", 0), ("submit", 1)])
        self.assertEqual(self.core_calls, [])

    def test_actual_guard_rejects_items_outside_material_request(self):
        self.note.items = [self.item("OTHER-ITEM", 1)]
        self.assert_policy_rejects("غير موجود في طلب المواد")

    def test_actual_guard_rejects_quantity_above_material_request(self):
        self.note.items[0].qty = 4
        self.assert_policy_rejects("تتجاوز المتبقي")

    def test_actual_guard_aggregates_repeated_current_rows(self):
        self.note.items = [self.item("ITEM-A", 2), self.item("ITEM-A", 2, idx=2)]
        self.assert_policy_rejects("تتجاوز المتبقي")

    def test_actual_guard_applies_requested_delivered_and_current_conversions(self):
        self.db.connection.execute("DELETE FROM `tabMaterial Request Item`")
        self.db.add_requested("ITEM-A", 2, conversion_factor=3)
        self.db.add_requested("ITEM-A", 2, conversion_factor=2)
        self.db.add_delivery("DN-PREVIOUS", 2, conversion_factor=2)
        self.note.items = [self.item("ITEM-A", 2, conversion_factor=3)]
        self.note.so_required()  # Requested 10 stock units minus 4 delivered = 6.
        self.note.items[0].qty = 3
        self.assert_policy_rejects("تتجاوز المتبقي 6.0")

    def test_actual_guard_ignores_drafts_cancelled_other_requests_and_current_note(self):
        self.db.add_delivery("DN-DRAFT", 100, docstatus=0)
        self.db.add_delivery("DN-CANCELLED", 100, docstatus=2)
        self.db.add_delivery("DN-OTHER", 100, material_request="MREQ-OTHER")
        self.db.add_delivery("DN-CURRENT", 100)
        self.note.so_required()
        self.assertEqual(len(self.policy.calls), 1)

    def test_actual_guard_rechecks_remaining_quantity_at_submission(self):
        self.note.so_required()
        self.db.add_delivery("DN-INTERVENING", 1)
        self.note._action, self.note.docstatus = "submit", 1
        self.assert_policy_rejects("تتجاوز المتبقي 2.0")
        self.assertEqual(self.policy.calls, [("save", 0), ("submit", 1)])

    def test_actual_guard_uses_one_for_zero_or_null_conversion(self):
        self.db.connection.execute("DELETE FROM `tabMaterial Request Item`")
        self.db.add_requested("ITEM-A", 2, conversion_factor=0)
        self.db.add_requested("ITEM-A", 2, conversion_factor=None)
        self.db.add_delivery("DN-PREVIOUS", 1, conversion_factor=0)
        self.note.items = [self.item("ITEM-A", 3, conversion_factor=0)]
        self.note.so_required()
        self.note.items[0].qty = 4
        self.assert_policy_rejects("تتجاوز المتبقي 3.0")

    def test_actual_guard_rejects_zero_and_negative_quantities(self):
        for qty in (0, -1):
            with self.subTest(qty=qty):
                self.note.items[0].qty = qty
                self.assert_policy_rejects("يجب أن تكون أكبر من صفر")

    def test_actual_guard_keeps_stock_quantity_rounding_tolerance(self):
        self.note.items[0].qty = 3.0000005
        self.note.so_required()
        self.note.items[0].qty = 3.000002
        self.assert_policy_rejects("تتجاوز المتبقي")

    def test_actual_guard_requires_reference_order_on_material_request(self):
        self.db.request.sales_order = ""
        self.assert_policy_rejects("لا يحتوي أمر بيع مرجعيًا")

    def test_actual_guard_requires_existing_submitted_reference_order(self):
        for docstatus in (None, 0, 2):
            with self.subTest(docstatus=docstatus):
                self.db.order = None if docstatus is None else AttrDict(
                    docstatus=docstatus, customer="Customer A", company="Company A"
                )
                self.assert_policy_rejects("أمر البيع المرجعي غير معتمد")

    def test_actual_guard_checks_reference_order_customer_and_company(self):
        for field, value, message in (
            ("customer", "Customer B", "عميل سند التسليم لا يطابق"),
            ("company", "Company B", "شركة سند التسليم لا تطابق"),
        ):
            with self.subTest(field=field):
                original = self.note[field]
                self.note[field] = value
                self.assert_policy_rejects(message)
                self.note[field] = original

    def test_actual_guard_rejects_either_sales_order_link_on_item(self):
        for field in ("against_sales_order", "so_detail"):
            with self.subTest(field=field):
                self.note.items[0][field] = "LINKED-ROW"
                self.assert_policy_rejects("لا يجب ربطه بأمر البيع على مستوى الصنف")
                self.note.items[0][field] = None

    def test_setting_stays_enabled_and_class_override_is_delivery_note_only(self):
        self.note.so_required()
        self.assertEqual(self.db.so_required, "Yes")
        hooks = runpy.run_path(str(ROOT / "namar_test" / "hooks.py"))
        self.assertEqual(hooks["override_doctype_class"], {
            "Delivery Note": "namar_test.overrides.delivery_note.ScenarioDeliveryNote"
        })
        self.assertNotIn("Sales Invoice", hooks["override_doctype_class"])
        # Only approved read methods exist on the fake DB. A settings write in
        # the override or guard would fail instead of being silently accepted.
        self.assertTrue(self.db.sql_queries)
        self.assertTrue(all(query.split()[0].upper() == "SELECT"
                            for query, _params in self.db.sql_queries))


if __name__ == "__main__":
    unittest.main()
