from __future__ import annotations

import unittest
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "namar_test"
    / "legacy_scripts"
    / "server_scripts"
    / "make_dn_from_mr.py"
)


class AttrDict(dict):
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc

    def __setattr__(self, key, value):
        self[key] = value


class FakeUtils:
    @staticmethod
    def cint(value):
        return int(value or 0)

    @staticmethod
    def flt(value):
        return float(value or 0)


class FakeDeliveryNote:
    def __init__(self):
        self.items = []
        self.flags = AttrDict()

    def append(self, fieldname, values):
        assert fieldname == "items"
        row = AttrDict(rate=0)
        row.update(values)
        self.items.append(row)
        return row


class FakeDB:
    def __init__(self, scenario, link_to_sales_order):
        self.scenario = scenario
        self.link_to_sales_order = link_to_sales_order
        self.sql_queries = []

    def exists(self, doctype, name):
        return doctype == "DocType" and name == "Material Request Scenario Rule"

    def get_value(self, doctype, name_or_filters, fieldname, as_dict=False):
        if doctype == "Material Request":
            values = AttrDict(
                name="MREQ-TEST",
                docstatus=1,
                sales_order="SO-TEST",
                custom_request_scenario=self.scenario,
            )
            if isinstance(fieldname, list):
                return AttrDict({key: values.get(key) for key in fieldname})
            return values.get(fieldname)
        if doctype == "Material Request Scenario Rule":
            return self.link_to_sales_order
        if doctype == "Sales Order":
            return "CUSTOMER-TEST"
        if doctype == "Company":
            return AttrDict(default_currency="SAR")
        raise AssertionError((doctype, name_or_filters, fieldname, as_dict))

    def sql(self, query, params=None, as_dict=False):
        self.sql_queries.append(query)
        if "FROM `tabMaterial Request Item` mr_item" in query:
            return [AttrDict(item_code="FREE-ITEM", remaining=2)]
        if "FROM `tabMaterial Request Item`" in query:
            return [
                AttrDict(
                    name="MRI-1",
                    item_code="FREE-ITEM",
                    item_name="صنف مستقل",
                    description="صنف استبدال مستقل",
                    qty=2,
                    uom="Nos",
                    stock_uom="Nos",
                    conversion_factor=1,
                )
            ]
        if "FROM `tabSales Order Item`" in query and "ORDER BY idx" in query:
            return [
                AttrDict(
                    name="SOI-1",
                    item_code="FREE-ITEM",
                    qty=2,
                    delivered_qty=0,
                    rate=100,
                    warehouse="FINISHED - N",
                )
            ]
        if "FROM `tabSales Order Item`" in query:
            return [AttrDict(item_code="FREE-ITEM", remaining=2)]
        if "FROM `tabDelivery Note Item`" in query:
            return []
        raise AssertionError(query)

    @staticmethod
    def get_default(fieldname):
        assert fieldname == "Company"
        return "Namar"

    @staticmethod
    def get_single_value(doctype, fieldname):
        values = {
            ("Global Defaults", "default_company"): "Namar",
            ("Stock Settings", "default_warehouse"): "FINISHED - N",
            ("Selling Settings", "selling_price_list"): "Standard Selling",
        }
        return values.get((doctype, fieldname), "")


class FakeFrappe:
    def __init__(self, *, scenario, link_to_sales_order, form_dict):
        self.form_dict = AttrDict(form_dict)
        self.response = AttrDict()
        self.db = FakeDB(scenario, link_to_sales_order)
        self.utils = FakeUtils()

    @staticmethod
    def throw(message):
        raise RuntimeError(message)

    @staticmethod
    def new_doc(doctype):
        assert doctype == "Delivery Note"
        return FakeDeliveryNote()


def execute_script(*, scenario, link_to_sales_order, form_dict):
    fake_frappe = FakeFrappe(
        scenario=scenario,
        link_to_sales_order=link_to_sales_order,
        form_dict=form_dict,
    )
    namespace = {"frappe": fake_frappe}
    code = SCRIPT_PATH.read_text(encoding="utf-8")
    exec(compile(code, str(SCRIPT_PATH), "exec"), namespace)
    return fake_frappe


class MaterialRequestScenarioDeliveryTest(unittest.TestCase):
    def test_replacement_quantity_comes_from_material_request(self):
        frappe = execute_script(
            scenario="استبدال",
            link_to_sales_order=0,
            form_dict={"action": "get_qty", "material_request": "MREQ-TEST"},
        )

        self.assertEqual(frappe.response["message"], {"FREE-ITEM": 2.0})
        self.assertTrue(
            any("tabMaterial Request Item` mr_item" in query for query in frappe.db.sql_queries)
        )

    def test_replacement_and_shortage_items_are_not_linked_to_sales_order_rows(self):
        for scenario in ("استبدال", "نواقص"):
            with self.subTest(scenario=scenario):
                frappe = execute_script(
                    scenario=scenario,
                    link_to_sales_order=0,
                    form_dict={"source_name": "MREQ-TEST"},
                )

                delivery_note = frappe.response["message"]
                self.assertEqual(delivery_note.customer, "CUSTOMER-TEST")
                self.assertEqual(len(delivery_note.items), 1)
                self.assertEqual(delivery_note.items[0].item_code, "FREE-ITEM")
                self.assertEqual(delivery_note.items[0].qty, 2.0)
                self.assertNotIn("against_sales_order", delivery_note.items[0])
                self.assertNotIn("so_detail", delivery_note.items[0])

    def test_manufacturing_delivery_keeps_sales_order_link(self):
        frappe = execute_script(
            scenario="تصنيع",
            link_to_sales_order=1,
            form_dict={"source_name": "MREQ-TEST"},
        )

        delivery_note = frappe.response["message"]
        self.assertEqual(len(delivery_note.items), 1)
        self.assertEqual(delivery_note.items[0].against_sales_order, "SO-TEST")
        self.assertEqual(delivery_note.items[0].so_detail, "SOI-1")
        self.assertEqual(delivery_note.items[0].rate, 100.0)


if __name__ == "__main__":
    unittest.main()
