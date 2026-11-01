"""Native manufacturing exclusion and lifecycle regressions; no site connection.

The memory adapter retains the source Server Script's schema and transaction
checks. It does not model MariaDB contention; current child-row lock queries are
asserted, while independent request state runs through the real native dispatch.
"""
from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
from threading import Barrier
import types
import unittest

from test_engine import fake_frappe, load_engine, request, rule

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "namar_kdt/reference/schema.json").read_text())


class UserError(Exception):
    pass

class Record(dict):
    def __getattr__(self, name):
        return self.get(name)

    def __setattr__(self, name, value):
        self[name] = value

    def set(self, name, value):
        self[name] = value

def records(value):
    if isinstance(value, dict):
        return Record({key: records(item) for key, item in value.items()})
    if isinstance(value, list):
        return [records(item) for item in value]
    return value

class Document(Record):
    def __init__(self, fixture, values):
        super().__init__(records(copy.deepcopy(values)))
        object.__setattr__(self, "fixture", fixture)

    def check_permission(self, ptype):
        if not self.has_permission(ptype):
            raise UserError("مصدر غير مصرح")

    def has_permission(self, ptype="read"):
        return self.fixture.permission_allowed(self.doctype, ptype=ptype, doc=self.name)

    def append(self, fieldname, value):
        row = records(value)
        self.setdefault(fieldname, []).append(row)
        return row

    def insert(self, **kwargs):
        return self.fixture.persist(self, insert=True)

    def save(self, **kwargs):
        return self.fixture.persist(self, insert=False)

class MemoryDB:
    def __init__(self, fixture):
        self.fixture = fixture
        self.snapshot = {}
        self.queries = []
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.snapshot = copy.deepcopy(self.fixture.store)
        self.commits += 1

    def rollback(self):
        self.fixture.store = copy.deepcopy(self.snapshot)
        self.rollbacks += 1

    def exists(self, doctype, name):
        return name if (doctype, name) in self.fixture.store else None

    def get_value(self, doctype, name, fieldname):
        return self.fixture.store.get((doctype, name), {}).get(fieldname)

    def sql(self, query, values=(), as_dict=False):
        self.queries.append((query, tuple(values)))
        if "FROM `tabMaterial Request`" in query:
            row = self.fixture.store.get(("Material Request", values[0]))
            result = [Record(name=values[0])] if row else []
        elif "FROM `tabMaterial Request Item`" in query:
            assert "FOR UPDATE" in query
            selected = query.split(" FROM ", 1)[0].split("SELECT ", 1)[1]
            fields = [field.strip().strip("`") for field in selected.split(",")]
            assert set(fields) <= self.fixture.mri_fields, fields
            rows = [row for row in self.fixture.source_rows() if row["name"] == values[0]]
            if len(values) > 1:
                rows = [row for row in rows if row["parent"] == values[1]]
            result = [records({field: copy.deepcopy(row.get(field)) for field in fields}) for row in rows]
        elif "FROM `tabKDT Cut Command`" in query:
            row = self.fixture.store.get(("KDT Cut Command", values[0]))
            result = [records(copy.deepcopy(row))] if row else []
        elif "FROM `tabKDT Cut Order`" in query:
            row = self.fixture.store.get(("KDT Cut Order", values[0]))
            result = [records(copy.deepcopy(row))] if row else []
        elif "FROM `tabKDT Cut Piece`" in query:
            result = [records(copy.deepcopy(row)) for (dt, _), row in self.fixture.store.items()
                      if dt == "KDT Cut Piece" and row["piece_key"] in values]
            if "state IN ('reserved','completed')" in query:
                result = [row for row in result if row.state in ("reserved", "completed")]
        elif "FROM `tabKDT Cutting Batch Item`" in query:
            # Require the actual query's exclusion of cancelled and coating-only
            # batches; otherwise the fixture must not silently supply that filter.
            assert "b.docstatus < 2" in query
            assert "IN ('قص','تشغيل موحد')" in query
            active = [row for row in self.fixture.legacy if row.get("docstatus", 1) < 2
                      and (row.get("operation_mode") or "قص") in ("قص", "تشغيل موحد")]
            if "WHERE p.material_request=%s" in query:
                assert "p.material_request_item AS source_row" in query
                assert "p.parent AS current_order" in query
                result = [Record(source_row=row.get("material_request_item"), current_order=row["order_name"])
                          for row in active if row.get("material_request") == values[0]]
            else:
                assert "WHERE p.piece_key IN (" in query
                result = [records(copy.deepcopy(row)) for row in active if row["piece_key"] in values]
        else:
            raise AssertionError("Unexpected SQL: " + query)
        return result if as_dict else [(row.name,) for row in result]

class MemoryFrappe:
    def __init__(self):
        self.store = {}
        self.parts = {}
        self.legacy = []
        self.denied = set()
        self.writes = []
        self.counter = 0
        self.session = Record(user="operator@example.test")
        self.utils = types.SimpleNamespace(now_datetime=lambda: "2026-10-14 10:00:00",
                                           cint=lambda value: int(float(value or 0)),
                                           flt=lambda value: float(str(value or 0).replace(",", "")))
        self.db = MemoryDB(self)
        self.form_dict = {}
        self.response = {}
        self.schema = {
            dt["name"]: dict(dt, fields=[field for field in SCHEMA["fields"] if field["parent"] == dt["name"]])
            for dt in SCHEMA["doctypes"]
        }
        self.mri_fields = {"name", "parent", "qty", "custom_is_cancelled_row",
                           "custom_manufactured_qty", "custom_is_manufactured"}

    @staticmethod
    def throw(message):
        raise UserError(message)

    def permission_allowed(self, doctype, ptype="read", doc=None):
        return (doctype, ptype, doc) not in self.denied and (doctype, ptype, None) not in self.denied

    def get_doc(self, doctype, name=None):
        if isinstance(doctype, dict):
            return Document(self, doctype)
        return Document(self, self.store[(doctype, name)])

    def new_doc(self, doctype):
        return self.get_doc({"doctype": doctype})

    def get_meta(self, doctype):
        assert doctype == "Material Request Item"
        return types.SimpleNamespace(has_field=lambda fieldname: fieldname in self.mri_fields)

    def persist(self, doc, insert):
        # An unexpected stock/manufacturing write is an immediate test failure.
        assert doc.doctype in self.schema and doc.doctype != "KDT Cutting Desk"
        schema = self.schema[doc.doctype]
        known = {field["fieldname"] for field in schema["fields"]}
        assert set(doc) <= known | {"name", "doctype", "modified", "creation"}, set(doc) - known
        if not doc.name:
            autoname = schema.get("autoname", "")
            self.counter += 1
            doc.name = doc.get(autoname[6:]) if autoname.startswith("field:") else "CUT-%05d" % self.counter
        key = (doc.doctype, doc.name)
        if insert and key in self.store:
            raise UserError("duplicate document")
        for field in schema["fields"]:
            if field.get("unique") and doc.get(field["fieldname"]):
                for other_key, row in self.store.items():
                    if other_key != key and other_key[0] == doc.doctype:
                        assert row.get(field["fieldname"]) != doc[field["fieldname"]]
        doc.modified = "2026-10-14 10:00:%02d" % (len(self.writes) % 60)
        doc.setdefault("creation", doc.modified)
        self.store[key] = copy.deepcopy(dict(doc))
        self.writes.append((doc.doctype, doc.name))
        return doc

    def get_all(self, doctype, filters=None, fields=None, **kwargs):
        if doctype == "Material Request Item":
            rows = self.source_rows()
            for field, condition in (filters or {}).items():
                assert condition[0] == "in"
                rows = [row for row in rows if row.get(field) in condition[1]]
            return [records({field: copy.deepcopy(row.get(field)) for field in fields})
                    for row in sorted(rows, key=lambda row: row["name"])]
        result = []
        for (dt, _), row in self.store.items():
            if dt != doctype or any(row.get(k) != v for k, v in (filters or {}).items()):
                continue
            result.append(records({field: copy.deepcopy(row.get(field)) for field in fields}))
        if kwargs.get("order_by", "").endswith("desc"):
            result.reverse()
        return result[:kwargs.get("limit_page_length") or len(result)]

    def source_rows(self):
        return [dict(item, parent=name) for (doctype, name), request in self.store.items()
                if doctype == "Material Request" for item in request.get("items") or []]

    def source(self, name="MR-A", quantity=4):
        self.store[("Material Request", name)] = {
            "doctype": "Material Request", "name": name, "docstatus": 1,
            "transaction_date": "2026-10-14", "title": "أبواب مشروع " + name,
            "workflow_state": "اعتماد التوريد",
            "items": [{"name": name + "-row", "qty": quantity,
                       "custom_manufactured_qty": 0, "custom_is_manufactured": 0}],
        }
        self.parts[name] = [
            {"piece_key": name + "-piece-" + str(unit), "material_request": name,
             "source_row": name + "-row", "door_row": 1, "unit_no": unit, "part_no": 1,
             "piece_id": name + "/1/" + str(unit), "color": "أبيض", "component": "PANEL-4",
             "height_mm": 2100, "width_mm": 900, "thickness_mm": 40,
             "source_signature": "initial-" + name + "-" + str(unit), "eligible": 1,
             "machine_payload": "P550*2100*900*40", "barcode_type": "QR"}
            for unit in range(1, quantity + 1)
        ]
        self.db.commit()
        return [part["piece_key"] for part in self.parts[name]]

def fixture_cut_parts(fixture, source, cancelled_source, manufacturing_for=None):
    rows = {row.name: row for row in source.get("items") or []}
    cancelled = {row.name for row in rows.values() if cancelled_source(row, source.name)}
    parts = copy.deepcopy([part for part in fixture.parts[source.name] if part["source_row"] not in cancelled])
    if manufacturing_for:
        for part in parts:
            row = rows.get(part["source_row"])
            if row is not None:
                part["manufacturing"] = manufacturing_for(row, source.name)
    return parts

def component_key(component, color="أبيض", raw_item="RAW-A"):
    return json.dumps([component, color or "", raw_item], ensure_ascii=False, separators=(",", ":"))

class ComponentFrappe(MemoryFrappe):
    def __init__(self):
        super().__init__()
        self.requirements = []
        self.report_calls = []
        self.report_override = None
        self.report_exception = None
        self.component_labels = {}
        self.rendered = []
        self.utils.sha256_hash = lambda value: hashlib.sha256(str(value).encode()).hexdigest()

    def source(self, name="MR-A", quantity=4):
        keys = super().source(name, quantity)
        self.store[("Material Request", name)]["items"][0].update({
            "qty": quantity, "idx": 1, "item_code": "DOOR-" + name,
            "custom_result_panel_h": 210, "custom_result_panel_w": 90,
            "custom_store_data": json.dumps([{"component": "PANEL-4", "component_ar": "بانل 210 X 90 X 4"}]),
        })
        item = self.store[("Material Request", name)]["items"][0]
        for part in self.parts[name]:
            part["item_code"] = item["item_code"]
            part["source_signature"] = json.dumps({"stores": json.loads(item["custom_store_data"]),
                                                    "source_row": item["name"], "qty": quantity})
        self.db.commit()
        return keys

    def requirement(self, request="MR-A", component="حلق 20", quantity=0.375125,
                    color="أبيض", raw_item="RAW-A", source_row=None, part_nos=None, part_count=1):
        source = {
            "material_request": request, "source_row": source_row or request + "-row",
            "component": component, "raw_item": raw_item, "color": color,
            "quantity": quantity, "part_nos": [1] if part_nos is None else part_nos, "part_count": part_count,
        }
        self.requirements.append(source)
        return source

    def call(self, method, **kwargs):
        self.report_calls.append((method, kwargs))
        assert method == "frappe.desk.query_report.run"
        assert kwargs["report_name"] == "نتائج التخصيم"
        assert kwargs["ignore_prepared_report"] == 1
        assert kwargs["are_default_filters"] is False
        if self.report_exception:
            raise self.report_exception
        if self.report_override is not None:
            return copy.deepcopy(self.report_override)
        report_filters = json.loads(kwargs["filters"])
        names = report_filters["material_requests"]
        selected_components = report_filters.get("component") or []
        assert names, "Never run the report with an unfiltered scope"
        grouped = {}
        for source in self.requirements:
            if source["material_request"] not in names:
                continue
            if selected_components and source["component"] not in selected_components:
                continue
            key = component_key(source["component"], source["color"], source["raw_item"])
            if key not in grouped:
                grouped[key] = {
                    "طلب_المواد": "ملخص", "الحالة": "ملخص المخازن",
                    "المكون": self.component_labels.get(source["component"], source["component"]),
                    "اللون": source["color"] or "-", "كود_الصنف": source["raw_item"] or "-", "الكمية": 0,
                    "_kdt_component_name": source["component"],
                    "_kdt_component_key": key, "_kdt_sources": [],
                }
            grouped[key]["الكمية"] += source["quantity"]
            grouped[key]["_kdt_sources"].append(copy.deepcopy(source))
        # The report rounds only its displayed summary cell to three decimals.
        # Allocation quantities must come from the unrounded provenance.
        for row in grouped.values():
            row["الكمية"] = round(row["الكمية"], 3)
        return {"result": list(grouped.values())}

    def render(self, name, parts):
        self.rendered.append((name, copy.deepcopy(parts)))
        return "LABELS:" + ",".join(part["piece_key"] for part in parts)


def native_fixture_engine(fixture):
    fixture.utils.sha256_hash = lambda value: hashlib.sha256(str(value).encode()).hexdigest()
    engine = load_engine(fixture)
    # Physical machine identities are deterministic inputs to lifecycle tests.
    # Native builder and renderer invariants are tested separately below.
    engine.build_cut_parts = lambda source: fixture_cut_parts(
        fixture, source, engine.kdt_source_is_cancelled, engine.kdt_source_manufacturing)
    return engine



class NativeComponentManufacturingTests(unittest.TestCase):
    def setUp(self):
        self.frappe = ComponentFrappe()
        self.physical_keys = self.frappe.source(quantity=4)
        self.requirement = self.frappe.requirement()
        self.engine = native_fixture_engine(self.frappe)
        self.service = vars(self.engine)
        self.command_no = 0

    def call(self, action, **payload):
        if action in ("approve", "component_approve", "component_create_available", "component_revise",
                      "component_complete", "component_correct", "component_recut", "component_cancel"):
            self.command_no += 1
            payload.setdefault("request_id", "component-command-%s" % self.command_no)
        return self.engine.dispatch(action, payload)


    def preview(self, names=None, **extra):
        names = names or ["MR-A"]
        extra.setdefault("component_keys", list(dict.fromkeys(
            component_key(source["component"], source["color"], source["raw_item"])
            for source in self.frappe.requirements if source["material_request"] in names)))
        return self.call("component_preview", request_names=names, **extra)

    def approve(self, preview=None, names=None, keys=None, **extra):
        names = names or ["MR-A"]
        preview = preview if preview is not None else self.preview(names)
        if keys is None:
            keys = preview["component_scope"]["component_keys"]
        elif keys != preview["component_scope"]["component_keys"]:
            preview = self.preview(names, component_keys=keys)
        return self.call("component_approve", request_names=names, component_keys=keys,
                         preview_signature=preview["preview_signature"], **extra)

    def mutate(self, action, order, **extra):
        return self.call("component_" + action, name=order["name"], revision=order["revision"], **extra)

    def allocations(self, order):
        return [row["piece_key"] for row in order["items"]]

    def history(self, order):
        return self.call("history", name=order["name"])["events"]

    def report_scope(self, names=None, keys=None, filters=None):
        names = names or ["MR-A"]
        if keys is None:
            keys = list(dict.fromkeys(component_key(source["component"], source["color"], source["raw_item"])
                                     for source in self.frappe.requirements if source["material_request"] in names))
        return {"request_names": names, "component_keys": keys, "filters": filters or {}}

    def report_status(self, names=None, keys=None, filters=None):
        return self.call("report_status", **self.report_scope(names, keys, filters))

    def create_available(self, status=None, names=None, keys=None, filters=None, **extra):
        scope = self.report_scope(names, keys, filters)
        if status is None:
            status = self.call("report_status", **scope)
        return self.call("component_create_available", **scope,
                         report_signature=status["report_signature"],
                         status_signature=status["status_signature"], **extra)

    def assert_rejected_without_writes(self, action, **payload):
        before = copy.deepcopy(self.frappe.store)
        with self.assertRaises(UserError):
            self.call(action, **payload)
        self.assertEqual(self.frappe.store, before)

    def set_source_manufacturing(self, quantity, request="MR-A", source_row=None, flag=0):
        """Change fixture input only; no manufacturing endpoint is invoked."""
        source_row = source_row or request + "-row"
        document = self.frappe.store[("Material Request", request)]
        item = next(row for row in document["items"] if row["name"] == source_row)
        item["custom_manufactured_qty"] = quantity
        item["custom_is_manufactured"] = flag
        self.frappe.db.commit()
        return item

    def test_manufactured_doors_exclude_all_components_without_inventing_cut_orders(self):
        self.frappe.requirement(component="مفصلات", quantity=8, color="", raw_item="HINGE")
        original = self.report_status()
        self.set_source_manufacturing(4)
        before = copy.deepcopy(self.frappe.store)
        status = self.report_status()
        self.assertFalse(status["can_create"])
        self.assertEqual(status["counts"]["manufactured"], 2)
        self.assertEqual(status["counts"]["available"], 0)
        self.assertEqual(status["counts"]["blocked"], 0)
        self.assertEqual(status["report_signature"], original["report_signature"])
        self.assertNotEqual(status["status_signature"], original["status_signature"])
        self.assertEqual(len(status["rows"]), 2)
        for row in status["rows"]:
            self.assertEqual(row["creation_state"], "manufactured")
            self.assertEqual(row["manufactured_quantity"], row["quantity"])
            self.assertEqual(row["available_quantity"], 0)
            self.assertEqual(row["orders"], [])
            self.assertEqual(len(row["manufacturing_sources"]), 1)
            source = row["sources"][0]
            self.assertEqual(source["creation_state"], "manufactured")
            self.assertEqual(source["orders"], [])
            manufacturing = source["manufacturing"]
            self.assertEqual(manufacturing["state"], "completed")
            self.assertEqual(manufacturing["required_qty"], 4)
            self.assertEqual(manufacturing["manufactured_qty"], 4)
            self.assertEqual(manufacturing["remaining_qty"], 0)
            self.assertTrue(manufacturing["excluded"])
            self.assertEqual(manufacturing["source_row"], "MR-A-row")
            self.assertEqual(manufacturing["material_request"], "MR-A")
        self.assertEqual(self.frappe.store, before)
        self.assertEqual(self.frappe.writes, [])

    def test_partial_manufacturing_skips_whole_source_but_keeps_other_rows_in_same_request(self):
        self.physical_keys = self.frappe.source(quantity=5)
        self.frappe.requirement(component="مفصلات", quantity=10, color="", raw_item="HINGE")
        self.frappe.store[("Material Request", "MR-A")]["items"].append({
            "name": "MR-A-unmade", "qty": 3, "idx": 2, "item_code": "DOOR-MR-A",
            "custom_manufactured_qty": 0, "custom_is_manufactured": 0,
        })
        self.frappe.requirement(source_row="MR-A-unmade", quantity=0.625125)
        self.set_source_manufacturing(2, flag=1)
        before_source = copy.deepcopy(self.frappe.store[("Material Request", "MR-A")])
        status = self.report_status()
        self.assertTrue(status["can_create"])
        self.assertEqual(status["counts"]["manufactured"], 2)
        self.assertEqual(status["counts"]["available"], 1)
        sources = [source for row in status["rows"] for source in row["sources"]]
        excluded = [source for source in sources if source["source_row"] == "MR-A-row"]
        self.assertTrue(all(source["creation_state"] == "manufactured" for source in excluded))
        for source in excluded:
            # A legacy true flag must not turn a positive partial counter into full completion.
            manufacturing = source["manufacturing"]
            self.assertEqual((manufacturing["state"], manufacturing["required_qty"],
                              manufacturing["manufactured_qty"], manufacturing["remaining_qty"]),
                             ("partial", 5, 2, 3))
        result = self.create_available(status)
        self.assertTrue(result["created"])
        self.assertEqual(len(result["skipped_sources"]), 2)
        self.assertTrue(all(source["creation_state"] == "manufactured" for source in result["skipped_sources"]))
        order = result["order"]
        self.assertEqual(len(order["items"]), 1)
        self.assertEqual(order["items"][0]["source_row"], "MR-A-unmade")
        self.assertAlmostEqual(order["items"][0]["quantity"], 0.625125, places=12)
        self.assertEqual(order["component_scope"]["allocation_keys"], self.allocations(order))
        self.assertEqual(order["machine_items"], [])
        self.assertFalse(any(("KDT Cut Piece", key) in self.frappe.store for key in self.physical_keys))
        self.assertEqual(self.frappe.store[("Material Request", "MR-A")], before_source)

    def test_manufacturing_boolean_fallback_and_fractional_progress_do_not_prorate_component_quantity(self):
        cases = [
            (4, 0, 1, "completed", 4, 0),
            (4, None, 1, "completed", 4, 0),
            (5, "2", 1, "partial", 2, 3),
            (0.5, 0.1, 0, "partial", 0.1, 0.4),
            (4, 99, 0, "completed", 4, 0),
            (4, -2, 0, "none", 0, 4),
        ]
        for quantity, manufactured, flag, expected_state, done, remaining in cases:
            with self.subTest(quantity=quantity, manufactured=manufactured, flag=flag):
                item = self.frappe.store[("Material Request", "MR-A")]["items"][0]
                item["qty"] = quantity
                self.set_source_manufacturing(manufactured, flag=flag)
                status = self.report_status()
                row = status["rows"][0]
                source = row["sources"][0]
                info = source["manufacturing"]
                self.assertEqual(info["state"], expected_state)
                self.assertAlmostEqual(info["manufactured_qty"], done)
                self.assertAlmostEqual(info["remaining_qty"], remaining)
                self.assertAlmostEqual(source["quantity"], 0.375125, places=12)
                self.assertEqual(row["quantity"], 0.375)
                self.assertEqual(source["creation_state"], "manufactured" if done else "available")
                self.assertEqual(status["can_create"], not bool(done))

    def test_request_status_and_independent_component_manufacturing_are_not_door_completion(self):
        source = self.frappe.store[("Material Request", "MR-A")]
        source["workflow_state"] = "تم التصنيع"
        source["items"][0].update(custom_manufactured_qty=0, custom_is_manufactured=0)
        self.frappe.store[("Manufacturing Detail", "COMPONENT-ONLY")] = {
            "doctype": "Manufacturing Detail", "name": "COMPONENT-ONLY",
            "parent": "MR-A", "material_request": "MR-A", "material_request_item": "MR-A-row",
            "line_type": "مكون", "component": "حلق 20", "manufactured_qty": 10, "status": "تم التصنيع",
        }
        self.frappe.db.commit()
        status = self.report_status()
        self.assertTrue(status["can_create"])
        self.assertEqual(status["counts"].get("manufactured", 0), 0)
        self.assertEqual(status["rows"][0]["sources"][0]["creation_state"], "available")
        result = self.create_available(status)
        self.assertTrue(result["created"])
        self.assertEqual(len(result["order"]["items"]), 1)

    def test_manufacturing_race_uses_locked_child_state_even_with_stale_parent_snapshot(self):
        self.frappe.source("MR-B")
        self.frappe.requirement("MR-B", quantity=0.125125)
        names = ["MR-A", "MR-B"]
        status = self.report_status(names)
        # Preserve Frappe child-document attribute access in the stale snapshot.
        stale_items = copy.deepcopy(self.frappe.get_doc("Material Request", "MR-A").get("items"))
        self.set_source_manufacturing(1)
        original_get_doc = self.service["frappe"].get_doc

        def stale_get_doc(doctype, name=None):
            document = original_get_doc(doctype, name)
            if doctype == "Material Request" and name == "MR-A":
                document["items"] = copy.deepcopy(stale_items)
            return document

        self.service["frappe"].get_doc = stale_get_doc
        result = self.create_available(status, names=names)
        self.assertTrue(result["created"])
        self.assertTrue(result["availability_changed"])
        self.assertEqual({item["material_request"] for item in result["order"]["items"]}, {"MR-B"})
        self.assertEqual({source["material_request"] for source in result["skipped_sources"]}, {"MR-A"})
        skipped = result["skipped_sources"][0]
        self.assertEqual(skipped["creation_state"], "manufactured")
        self.assertEqual(skipped["manufacturing"]["manufactured_qty"], 1)
        locks = [(query, values) for query, values in self.frappe.db.queries
                 if "FROM " + chr(96) + "tabMaterial Request Item" + chr(96) in query and "FOR UPDATE" in query]
        self.assertTrue(locks)
        self.assertTrue(any("custom_manufactured_qty" in query for query, _ in locks))
        self.assertEqual(self.frappe.store[("Material Request", "MR-A")]["items"][0]["custom_manufactured_qty"], 1)

    def test_all_manufactured_noop_is_idempotent_without_fake_orders_or_events(self):
        self.set_source_manufacturing(1)
        status = self.report_status()
        before = copy.deepcopy(self.frappe.store)
        result = self.create_available(status, request_id="manufactured-noop-once")
        self.assertFalse(result["created"])
        self.assertIsNone(result["order"])
        self.assertEqual(len(result["skipped_sources"]), 1)
        self.assertEqual(result["skipped_sources"][0]["creation_state"], "manufactured")
        self.assertEqual(result["skipped_sources"][0]["orders"], [])
        self.assertTrue(result["message"])
        self.assertFalse(any(doctype in ("KDT Cut Order", "KDT Cut Piece", "KDT Cut Event")
                             for doctype, _ in self.frappe.store))
        self.assertEqual([doctype for doctype, _ in self.frappe.writes], ["KDT Cut Command"])
        after = copy.deepcopy(self.frappe.store)
        self.assertEqual(self.create_available(status, request_id="manufactured-noop-once"), result)
        self.assertEqual(self.frappe.store, after)
        self.frappe.denied.add(("Material Request", "read", "MR-A"))
        with self.assertRaises(UserError):
            self.create_available(status, request_id="manufactured-noop-once")
        self.assertEqual(self.frappe.store, after)
        self.assertEqual(before[("Material Request", "MR-A")], after[("Material Request", "MR-A")])

    def test_current_manufacturing_counter_controls_availability_not_old_ledger_presence(self):
        self.set_source_manufacturing(1)
        self.assertFalse(self.report_status()["can_create"])
        self.frappe.store[("Manufacturing Ledger", "OLD-SCAN")] = {
            "doctype": "Manufacturing Ledger", "name": "OLD-SCAN",
            "material_request": "MR-A", "material_request_item": "MR-A-row",
            "door_sequence": 1, "qty": 1, "source": "QR",
        }
        self.set_source_manufacturing(0, flag=0)
        status = self.report_status()
        self.assertTrue(status["can_create"])
        self.assertEqual(status["rows"][0]["sources"][0]["manufacturing"]["state"], "none")

    def test_component_approve_and_revision_addition_cannot_bypass_manufacturing_guard(self):
        stale_preview = self.preview()
        self.set_source_manufacturing(1)
        self.assert_rejected_without_writes(
            "component_approve", request_names=["MR-A"], component_keys=stale_preview["component_scope"]["component_keys"],
            preview_signature=stale_preview["preview_signature"])
        self.assertFalse(self.preview()["can_approve"])
        self.set_source_manufacturing(0)
        order = self.approve()
        self.frappe.source("MR-B")
        self.frappe.requirement("MR-B", quantity=0.125125)
        names = ["MR-A", "MR-B"]
        preview = self.preview(names, name=order["name"], revision=order["revision"])
        self.assertTrue(preview["can_approve"])
        self.set_source_manufacturing(1, request="MR-B")
        self.assert_rejected_without_writes(
            "component_revise", name=order["name"], revision=order["revision"], request_names=names,
            component_keys=preview["component_scope"]["component_keys"],
            preview_signature=preview["preview_signature"], reason="إضافة مصدر")
        self.assertFalse(self.preview(names, name=order["name"], revision=order["revision"])["can_approve"])

    def test_manufactured_owned_snapshot_and_history_survive_revision_of_other_source(self):
        self.frappe.source("MR-B")
        second = self.frappe.requirement("MR-B", quantity=0.125125)
        names = ["MR-A", "MR-B"]
        order = self.create_available(names=names)["order"]
        owned = next(item for item in order["items"] if item["material_request"] == "MR-A")
        events = [event for event in self.history(order) if event["piece_key"] == owned["piece_key"]]
        self.set_source_manufacturing(1)
        second["quantity"] = 0.250125
        preview = self.preview(names, name=order["name"], revision=order["revision"])
        self.assertTrue(preview["can_approve"])
        revised = self.mutate("revise", order, request_names=names,
                              component_keys=preview["component_scope"]["component_keys"],
                              preview_signature=preview["preview_signature"], reason="تعديل المصدر الآخر")
        kept = next(item for item in revised["items"] if item["material_request"] == "MR-A")
        self.assertEqual(kept, owned)
        self.assertEqual(kept["state"], "reserved")
        self.assertEqual([event for event in self.history(revised) if event["piece_key"] == owned["piece_key"]], events)
        printed = self.mutate("print_data", revised)
        self.assertEqual(printed["items"], revised["items"])
        self.assertEqual(printed["component_rows"], revised["component_rows"])

    def test_manufactured_owned_source_cannot_be_removed_and_reintroduced_by_revision(self):
        self.frappe.source("MR-B")
        self.frappe.requirement("MR-B", component="برواز 12", quantity=0.125125)
        names = ["MR-A", "MR-B"]
        order = self.create_available(names=names)["order"]
        self.set_source_manufacturing(1)
        keys = [component_key("برواز 12")]
        preview = self.preview(names, name=order["name"], revision=order["revision"], component_keys=keys)
        self.assertFalse(preview["can_approve"])
        self.assert_rejected_without_writes(
            "component_revise", name=order["name"], revision=order["revision"], request_names=names,
            component_keys=keys, preview_signature=preview["preview_signature"], reason="استبعاد مصدر مصنّع")

    def test_manufacturing_excludes_invalid_saved_panel_without_blocking_unmade_components(self):
        self.requirement.update(component="PANEL-4", quantity=1.25)
        for part in self.frappe.parts["MR-A"]:
            part.update(eligible=0, width_mm=0, machine_payload="", blocker="مقاس البنل غير مكتمل")
        self.set_source_manufacturing(4)
        self.frappe.source("MR-B")
        self.frappe.requirement("MR-B", quantity=0.125125)
        names = ["MR-A", "MR-B"]
        status = self.report_status(names)
        self.assertTrue(status["can_create"])
        manufactured = next(row for row in status["rows"] if row["component"] == "PANEL-4")
        self.assertEqual(manufactured["manufactured_quantity"], 1.25)
        self.assertEqual(manufactured["blocked_quantity"], 0)
        result = self.create_available(status, names=names)
        self.assertEqual({item["material_request"] for item in result["order"]["items"]}, {"MR-B"})
        self.assertEqual({source["material_request"] for source in result["skipped_sources"]}, {"MR-A"})

    def test_manufactured_old_allocation_orphan_does_not_block_unmade_request(self):
        prior = self.approve()
        self.set_source_manufacturing(1)
        self.requirement["raw_item"] = "RAW-CHANGED"
        self.frappe.source("MR-B")
        self.frappe.requirement("MR-B", quantity=0.125125)
        names = ["MR-A", "MR-B"]
        before_order = copy.deepcopy(self.frappe.store[("KDT Cut Order", prior["name"])])
        before_events = self.history(prior)
        status = self.report_status(names)
        self.assertTrue(status["can_create"])
        sources = [source for row in status["rows"] for source in row["sources"]]
        manufactured = next(source for source in sources if source["material_request"] == "MR-A")
        self.assertEqual(manufactured["creation_state"], "manufactured")
        result = self.create_available(status, names=names)
        self.assertTrue(result["created"])
        self.assertEqual({item["material_request"] for item in result["order"]["items"]}, {"MR-B"})
        self.assertEqual(self.frappe.store[("KDT Cut Order", prior["name"])], before_order)
        self.assertEqual(self.history(prior), before_events)

    def test_manufacturing_keeps_existing_panel_barcodes_printable_and_forbids_recut(self):
        self.requirement.update(component="PANEL-4", quantity=1.25)
        order = self.approve()
        order = self.mutate("complete", order, allocation_keys=self.allocations(order))
        expected = self.mutate("print_data", order)
        self.set_source_manufacturing(4)
        before = copy.deepcopy(self.frappe.store)
        history = self.history(order)
        status = self.report_status()
        self.assertFalse(status["can_create"])
        source = status["rows"][0]["sources"][0]
        self.assertEqual(source["creation_state"], "manufactured")
        self.assertEqual({reference["name"] for reference in source["orders"]}, {order["name"]})
        printed = self.mutate("print_data", order)
        self.assertEqual(printed["html"], expected["html"])
        self.assertEqual(printed["machine_items"], expected["machine_items"])
        self.assertEqual(printed["items"], expected["items"])
        self.assertEqual(self.call("print_data", name=order["name"], revision=order["revision"])["html"], expected["html"])
        self.assertEqual(self.history(order), history)
        self.assertEqual(self.frappe.store, before)
        self.assert_rejected_without_writes(
            "component_recut", name=order["name"], revision=order["revision"],
            allocation_keys=self.allocations(order), reason="إعادة قص مصدر مصنّع")


class NativePieceManufacturingTests(unittest.TestCase):
    def setUp(self):
        self.frappe = MemoryFrappe()
        self.keys = self.frappe.source()
        self.engine = native_fixture_engine(self.frappe)
        self.service = vars(self.engine)
        self.command_no = 0

    def call(self, action, **payload):
        if action in ("approve", "revise", "complete", "correct", "recut", "cancel"):
            self.command_no += 1
            payload.setdefault("request_id", "command-%s" % self.command_no)
        return self.engine.dispatch(action, payload)


    def approve(self, keys=None, **extra):
        return self.call("approve", request_names=["MR-A"], piece_keys=keys or self.keys[:2], **extra)

    def mutate(self, action, order, keys=None, **extra):
        return self.call(action, name=order["name"], revision=order["revision"],
                         piece_keys=keys or [], **extra)

    def piece(self, key):
        return self.frappe.store[("KDT Cut Piece", key)]

    def history(self, order):
        return self.call("history", name=order["name"])["events"]

    def test_manufacturing_quantity_normalization_matches_legacy_fallback(self):
        normalize = self.service["kdt_normalize_manufacturing"]
        cases = [
            ({"qty": 5, "custom_manufactured_qty": 2, "custom_is_manufactured": 1}, 5, 2, 3, "partial"),
            ({"qty": "5", "custom_manufactured_qty": "2", "custom_is_manufactured": "1"}, 5, 2, 3, "partial"),
            ({"qty": 5, "custom_manufactured_qty": 0, "custom_is_manufactured": 1}, 5, 5, 0, "completed"),
            ({"qty": 5, "custom_is_manufactured": 1}, 5, 5, 0, "completed"),
            ({"qty": 5, "custom_manufactured_qty": -2, "custom_is_manufactured": 0}, 5, 0, 5, "none"),
            ({"qty": 5, "custom_manufactured_qty": -2, "custom_is_manufactured": 1}, 5, 5, 0, "completed"),
            ({"qty": "5", "custom_manufactured_qty": "0", "custom_is_manufactured": "0"}, 5, 0, 5, "none"),
            ({"qty": 5, "custom_manufactured_qty": 12}, 5, 5, 0, "completed"),
            ({"qty": 2.5, "custom_manufactured_qty": 1.25}, 2.5, 1.25, 1.25, "partial"),
            ({"qty": 0, "custom_is_manufactured": 1}, 1, 1, 0, "completed"),
            ({"qty": 5, "custom_manufactured_qty": None, "custom_is_manufactured": 0}, 5, 0, 5, "none"),
        ]
        for row, required, manufactured, remaining, state in cases:
            with self.subTest(row=row):
                info = normalize(records(row))
                self.assertEqual((info["required_qty"], info["manufactured_qty"], info["remaining_qty"]),
                                 (required, manufactured, remaining))
                self.assertEqual(info["state"], state)
                self.assertEqual(info["excluded"], manufactured > 0)

    def test_partial_and_complete_manufacturing_block_every_piece_without_changing_barcode_validity(self):
        for done, complete in ((1, 0), (4, 1), (0, 1)):
            with self.subTest(done=done, complete=complete):
                row = self.frappe.store[("Material Request", "MR-A")]["items"][0]
                row.update(custom_manufactured_qty=done, custom_is_manufactured=complete)
                before = copy.deepcopy(self.frappe.store)
                preview = self.call("preview", request_names=["MR-A"])
                parts = [part for group in preview["groups"] for part in group["parts"]]
                self.assertEqual(len(parts), len(self.keys))
                self.assertEqual(preview["summary"]["available"], 0)
                self.assertTrue(all(part["manufacturing"]["excluded"] for part in parts))
                self.assertTrue(all(part["eligible"] for part in parts))
                self.assertEqual([part["source_signature"] for part in parts],
                                 [part["source_signature"] for part in self.frappe.parts["MR-A"]])
                # A different unit/part key cannot bypass the source-level rule.
                for key in self.keys:
                    with self.subTest(key=key), self.assertRaises(UserError):
                        self.approve([key])
                self.assertEqual(self.frappe.store, before)
                self.assertEqual(self.frappe.writes, [])

    def test_manufacturing_applies_to_one_source_row_not_the_whole_request(self):
        request = self.frappe.store[("Material Request", "MR-A")]
        request["items"][0].update(custom_manufactured_qty=1)
        request["items"].append({"name": "MR-A-second-row", "qty": 2,
                                 "custom_manufactured_qty": 0, "custom_is_manufactured": 0})
        for part in self.frappe.parts["MR-A"][2:]:
            part.update(source_row="MR-A-second-row", door_row=2)
        before_source = copy.deepcopy(request)
        order = self.approve(self.keys[2:])
        self.assertEqual({part["piece_key"] for part in order["items"]}, set(self.keys[2:]))
        self.assertEqual(self.frappe.store[("Material Request", "MR-A")], before_source)
        with self.assertRaises(UserError):
            self.approve([self.keys[0]])

    def test_manufacturing_reader_uses_current_locked_child_over_stale_document(self):
        stale = self.frappe.get_doc("Material Request", "MR-A")
        row = self.frappe.store[("Material Request", "MR-A")]["items"][0]
        row.update(custom_manufactured_qty=2, custom_is_manufactured=1)
        self.service["kdt_lock_request_sources"](["MR-A"])
        info = self.service["kdt_source_manufacturing"](stale.get("items")[0], "MR-A")
        self.assertEqual((info["required_qty"], info["manufactured_qty"], info["remaining_qty"]),
                         (4, 2, 2))
        self.assertEqual(info["state"], "partial")
        parts = self.service["build_cut_parts"](stale)
        self.assertTrue(all(part["manufacturing"]["manufactured_qty"] == 2 for part in parts))
        child_queries = [query for query, _ in self.frappe.db.queries
                         if "tabMaterial Request Item" in query]
        self.assertTrue(child_queries)
        for column in ("qty", "custom_manufactured_qty", "custom_is_manufactured"):
            self.assertIn(column, child_queries[-1].split(" FROM ", 1)[0])
        self.assertTrue(all("FOR UPDATE" in query for query in child_queries))
        self.assertEqual(self.frappe.writes, [])

    def test_manufacturing_committed_between_preview_and_approve_prevents_allocation(self):
        self.assertEqual(self.call("preview", request_names=["MR-A"])["summary"]["available"], 4)
        stale = self.frappe.get_doc("Material Request", "MR-A")
        original_get_doc = self.service["frappe"].get_doc
        def stale_document(doctype, name=None):
            if doctype == "Material Request" and name == "MR-A":
                return Document(self.frappe, dict(stale))
            return original_get_doc(doctype, name)
        self.service["frappe"].get_doc = stale_document
        self.frappe.store[("Material Request", "MR-A")]["items"][0]["custom_manufactured_qty"] = 1
        before = copy.deepcopy(self.frappe.store)
        with self.assertRaises(UserError):
            self.approve([self.keys[-1]])
        self.assertEqual(self.frappe.store, before)
        self.assertEqual(self.frappe.writes, [])

    def test_revision_retains_manufactured_snapshot_and_cannot_add_another_unit_from_it(self):
        order = self.approve([self.keys[0]])
        other = self.frappe.source("MR-B", quantity=2)
        self.frappe.store[("Material Request", "MR-A")]["items"][0]["custom_manufactured_qty"] = 1
        kept = copy.deepcopy(order["items"][0])
        revised = self.mutate("revise", order, [self.keys[0], other[0]],
                              request_names=["MR-A", "MR-B"], reason="إضافة مصدر غير مصنع")
        self.assertEqual(next(part for part in revised["items"] if part["piece_key"] == self.keys[0]), kept)
        before = copy.deepcopy(self.frappe.store)
        with self.assertRaises(UserError):
            self.mutate("revise", revised, [other[0]], reason="استبعاد المصدر المصنع")
        self.assertEqual(self.frappe.store, before)
        with self.assertRaises(UserError):
            self.mutate("revise", revised, [self.keys[0], self.keys[1], other[0]],
                        reason="إضافة وحدة من سطر مصنع جزئيًا")
        self.assertEqual(self.frappe.store, before)
        # A newly changed measurement must not overwrite a manufactured snapshot.
        self.frappe.parts["MR-A"][0].update(width_mm=700, source_signature="later-change")
        retained = self.mutate("revise", revised, [self.keys[0], other[0], other[1]],
                               request_names=["MR-A", "MR-B"], reason="إضافة المصدر الآخر")
        self.assertEqual(next(part for part in retained["items"] if part["piece_key"] == self.keys[0]), kept)

    def test_manufacturing_prevents_recut_but_preserves_explicit_correction_and_cancel_paths(self):
        order = self.mutate("complete", self.approve(), [self.keys[0]])
        self.frappe.store[("Material Request", "MR-A")]["items"][0]["custom_manufactured_qty"] = 1
        before = copy.deepcopy(self.frappe.store)
        with self.assertRaises(UserError):
            self.mutate("recut", order, [self.keys[0]], reason="لا يعاد قص المصدر المصنع")
        self.assertEqual(self.frappe.store, before)
        corrected = self.mutate("correct", order, [self.keys[0]], reason="تصحيح التسجيل فقط")
        self.assertEqual(corrected["completed"], 0)
        cancelled = self.mutate("cancel", corrected, reason="إلغاء الأمر لا يلغي التصنيع")
        self.assertEqual(cancelled["status"], "ملغى")
        with self.assertRaises(UserError):
            self.approve([self.keys[0]])


class NativeManufacturingRequestStateTests(unittest.TestCase):
    def setUp(self):
        self.frappe = ComponentFrappe()
        self.frappe.source()
        self.engine = native_fixture_engine(self.frappe)

    def test_each_dispatch_relocks_current_manufacturing_after_success_or_failure(self):
        stale = self.frappe.get_doc("Material Request", "MR-A")["items"][0]
        observed_initial_states = []

        def probe(action, payload):
            rows, locks = self.engine._cancellation_state()
            observed_initial_states.append((dict(rows), dict(locks)))
            self.engine.kdt_lock_request_sources(["MR-A"])
            info = self.engine.kdt_source_manufacturing(stale, "MR-A")
            if payload.get("fail"):
                self.assertEqual(info["manufactured_qty"], 2)
                raise UserError("request failed after locking")
            return info

        self.engine.kd_dispatch = probe
        source = self.frappe.store[("Material Request", "MR-A")]["items"][0]
        source["custom_manufactured_qty"] = 1
        self.assertTrue(self.engine.dispatch("probe", {})["excluded"])
        self.assertIsNone(self.engine._CANCELLATION_STATE.get())
        source["custom_manufactured_qty"] = 0
        self.assertFalse(self.engine.dispatch("probe", {})["excluded"])
        source["custom_manufactured_qty"] = 2
        with self.assertRaisesRegex(UserError, "request failed"):
            self.engine.dispatch("probe", {"fail": True})
        self.assertIsNone(self.engine._CANCELLATION_STATE.get())
        source["custom_manufactured_qty"] = 0
        self.assertEqual(self.engine.dispatch("probe", {})["state"], "none")
        self.assertEqual(observed_initial_states, [({}, {})] * 4)
        locks = [query for query, _ in self.frappe.db.queries if "tabMaterial Request Item" in query]
        self.assertEqual(len(locks), 4)
        self.assertTrue(all("FOR UPDATE" in query for query in locks))
        self.assertEqual(self.frappe.writes, [])

    def test_nested_dispatch_restores_outer_locked_manufacturing_even_on_error(self):
        source = self.frappe.store[("Material Request", "MR-A")]["items"][0]
        source["custom_manufactured_qty"] = 1
        stale = records(copy.deepcopy(source))

        def probe(action, payload):
            self.assertEqual(self.engine._cancellation_state(), ({}, {}))
            self.engine.kdt_lock_request_sources(["MR-A"])
            if payload.get("inner"):
                self.assertFalse(self.engine.kdt_source_manufacturing(stale, "MR-A")["excluded"])
                raise UserError("nested failure")
            outer_rows, outer_locks = self.engine._cancellation_state()
            source["custom_manufactured_qty"] = 0
            with self.assertRaisesRegex(UserError, "nested failure"):
                self.engine.dispatch("probe", {"inner": True})
            restored_rows, restored_locks = self.engine._cancellation_state()
            self.assertIs(restored_rows, outer_rows)
            self.assertIs(restored_locks, outer_locks)
            return self.engine.kdt_source_manufacturing(stale, "MR-A")

        self.engine.kd_dispatch = probe
        self.assertEqual(self.engine.dispatch("probe", {})["manufactured_qty"], 1)
        self.assertIsNone(self.engine._CANCELLATION_STATE.get())

    def test_concurrent_request_contexts_do_not_share_manufacturing_cache(self):
        barrier = Barrier(2)
        source = {"name": "ROW", "parent": "MR", "qty": 4, "custom_manufactured_qty": 0}

        def probe(action, payload):
            rows, locks = self.engine._cancellation_state()
            self.assertEqual((rows, locks), ({}, {}))
            rows["ROW"] = dict(source, custom_manufactured_qty=payload["done"])
            locks["MR"] = 1
            # Both requests must have their caches populated before either reads.
            barrier.wait(timeout=5)
            return self.engine.kdt_source_manufacturing(source, "MR")

        self.engine.kd_dispatch = probe
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(self.engine.dispatch, "probe", {"done": done}) for done in (0, 2)]
            results = [future.result(timeout=10) for future in futures]
        self.assertEqual([result["manufactured_qty"] for result in results], [0, 2])
        self.assertEqual([result["excluded"] for result in results], [False, True])
        self.assertIsNone(self.engine._CANCELLATION_STATE.get())


class NativeManufacturingBarcodeTests(unittest.TestCase):
    def test_real_builder_preserves_all_unit_and_part_barcodes_when_manufacturing_changes(self):
        framework = fake_frappe()
        framework.get_all = lambda *args, **kwargs: [rule(), rule("KDT 660")]
        engine = load_engine(framework)
        material_request = request(custom_result_panel_w_text="40 + 50", custom_result_leaf_w_text="41 + 51")
        before = engine.build_cut_parts(material_request)
        labels = engine.render_cut_labels("EXISTING-ORDER", before)
        material_request["items"][0].update(custom_manufactured_qty=1, custom_is_manufactured=1)
        after = engine.build_cut_parts(material_request)
        self.assertEqual(len(after), 4)
        self.assertEqual([part["piece_key"] for part in after], [
            "SYNTHETIC-ROW|U0001|P01", "SYNTHETIC-ROW|U0001|P02",
            "SYNTHETIC-ROW|U0002|P01", "SYNTHETIC-ROW|U0002|P02",
        ])
        for original, current in zip(before, after):
            self.assertEqual(current["manufacturing"]["state"], "partial")
            self.assertTrue(current["manufacturing"]["excluded"])
            self.assertTrue(current["eligible"])
            # Metadata must not invalidate saved source signatures or machine inputs.
            self.assertEqual({key: value for key, value in current.items() if key != "manufacturing"},
                             {key: value for key, value in original.items() if key != "manufacturing"})
        self.assertEqual(engine.render_cut_labels("EXISTING-ORDER", after), labels)
