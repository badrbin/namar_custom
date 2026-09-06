from __future__ import annotations

import importlib
import json
from contextlib import nullcontext
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from namar_custom.ai_readonly.policy import (DEFAULT_ROLE, METHODS, Denied, checked_filters,
    field_names, route, validate_policy, validate_request)
from namar_custom.ai_readonly.print_resources import _fetch_image, inline_images, render_pdf, validate_html


class Row(dict):
    def __getattr__(self, key):
        return self.get(key)

    def __setattr__(self, key, value):
        self[key] = value


def policy():
    return {"version": 1, "methods": list(METHODS), "reports": {}, "print_formats": {}, "max_rows": 1000,
            "app_revisions": {"frappe": "a" * 7},
            "permission_review": {"sha256": "b" * 64, "reviewed_no_business_mutations": True}}


def make_boundary(roles=None, args=None, path="/api/resource/Customer", verb="GET", settings_policy=None):
    calls = []
    fake = ModuleType("frappe")
    fake.session = Row(user="ai@example.com")
    fake.local = SimpleNamespace()
    fake.flags = Row(read_only=False)
    fake.conf = Row(read_from_replica=False)
    fake.form_dict = Row(args or {})
    fake.request = Row(path=path, method=verb)
    fake.response = Row()
    fake.as_json = lambda value: json.dumps(value, ensure_ascii=False)
    fake.PermissionError = type("PermissionError", (Exception,), {})
    fake.whitelist = lambda **kwargs: lambda fn: fn
    fake.get_roles = lambda user: roles if roles is not None else [DEFAULT_ROLE, "All", "Guest", "Desk User"]
    fake.get_cached_doc = lambda name: Row(protected_role=DEFAULT_ROLE, policy_json=json.dumps(settings_policy or policy()))
    fake.db = SimpleNamespace(exists=lambda *args: True,
        rollback=lambda: calls.append(("rollback", fake.flags.read_only)),
        begin=lambda **kwargs: calls.append(("begin", kwargs)))
    fake.throw = lambda msg, exc: (_ for _ in ()).throw(exc(msg))
    meta = Row(name="Customer", istable=0, is_virtual=0, issingle=0,
        permissions=[Row(role=DEFAULT_ROLE, permlevel=0, read=1, select=1, export=1, print=1, report=1, if_owner=0)],
        fields=[Row(fieldname="customer_name", fieldtype="Data", permlevel=0),
                Row(fieldname="secret_amount", fieldtype="Currency", permlevel=1),
                Row(fieldname="api_secret", fieldtype="Password", permlevel=0)])
    meta.get_table_fields = lambda: []
    meta.get_field = lambda name: next((f for f in meta.fields if f.fieldname == name), None)
    fake.get_meta = lambda name: meta
    fake.get_list = lambda *args, **kwargs: calls.append(("list", args, kwargs)) or [{"name": "C-1"}]
    sys.modules.pop("namar_custom.ai_readonly.boundary", None)
    sys.modules.pop("namar_custom.ai_readonly.builder", None)
    with patch.dict(sys.modules, {"frappe": fake}):
        boundary = importlib.import_module("namar_custom.ai_readonly.boundary")
    sys.modules["namar_custom.ai_readonly.boundary"] = boundary
    boundary._actual_check_app_revisions = boundary._check_app_revisions
    boundary._check_app_revisions = lambda plan: None
    boundary._actual_check_dispatch_route = boundary._check_dispatch_route
    boundary._check_dispatch_route = lambda: None
    boundary.permission_source = lambda: "b" * 64
    return boundary, fake, meta, calls


class PolicyTests(unittest.TestCase):
    def test_print_images_are_rasterized_without_following_nested_svg(self):
        from io import BytesIO
        from PIL import Image
        output = BytesIO(); Image.new("RGB", (2, 2), "white").save(output, format="PNG")
        raster = output.getvalue()
        requested = []
        rendered = inline_images('<img src="/files/logo.png"><img src="https://quickchart.io/qr?text=x">'
            '<a href="https://maps.google.com/x">map</a>', "https://erp.example",
            {"https://quickchart.io": ["/qr"]}, load_local=lambda path: raster, fetch_remote=lambda url: raster)
        self.assertEqual(rendered.count('src="data:image/png;base64,'), 2)
        self.assertIn('href="https://maps.google.com/x"', rendered)
        inline_images('<img src="https://quickchart.io/qr?text=a%26b%0Ac">', "https://erp.example",
            {"https://quickchart.io": ["/qr"]}, fetch_remote=lambda url: requested.append(url) or raster)
        self.assertEqual(requested, ["https://quickchart.io/qr?text=a%26b%0Ac"])
        malicious_svg = b'<svg xmlns="http://www.w3.org/2000/svg"><image href="https://erp.example/api/unsafe"/></svg>'
        with self.assertRaises(Denied):
            inline_images('<img src="/files/pretend.png">', "https://erp.example", {}, load_local=lambda path: malicious_svg)

    def test_print_css_user_images_and_svg_image_are_inlined(self):
        from io import BytesIO
        from PIL import Image
        output = BytesIO(); Image.new("RGB", (1, 1)).save(output, format="PNG")
        rendered = inline_images('<style>.x{background:url(/files/logo.png)}</style>'
            '<svg><image href="/files/logo.png"/></svg>', "https://erp.example", {}, load_local=lambda path: output.getvalue())
        self.assertNotIn("/files/logo.png", rendered)
        self.assertEqual(rendered.count("data:image/png;base64,"), 2)

    def test_print_image_redirect_is_rejected_before_body_is_read(self):
        requests = ModuleType("requests")
        class Response:
            status_code = 302
            def __enter__(self): return self
            def __exit__(self, *args): return None
            def iter_content(self, size): raise AssertionError("Redirect content should never be read")
        def get(url, **kwargs):
            self.assertFalse(kwargs["allow_redirects"])
            self.assertNotIn("headers", kwargs)
            self.assertNotIn("cookies", kwargs)
            return Response()
        requests.get = get
        with patch.dict(sys.modules, {"requests": requests}), self.assertRaises(Denied):
            _fetch_image("https://quickchart.io/qr?text=x")

    def test_print_resource_policy_rejects_non_https_or_ambiguous_origins(self):
        for resources in ({"http://example.com": ["/qr"]}, {"https://example.com/api": ["/"]},
                          {"https://example.com": ["/assets/../api"]}):
            p = policy(); p["print_resources"] = resources
            with self.assertRaises(Denied): validate_policy(p)
        p = policy(); p["print_resources"] = {"https://quickchart.io": ["/qr"]}
        validate_policy(p)

    def test_print_allows_passive_assets_reviewed_qr_and_nonfetched_map_links(self):
        validate_html('<link rel="stylesheet" href="/assets/app/print.css"><img src="/files/logo.png">'
            '<img src="https://quickchart.io/qr?text=hello"><a href="https://maps.google.com/anything">Map</a>'
            '<style>.x{background:url(/assets/app/bg.png)}</style>',
            'https://erp.example', {'https://quickchart.io': ['/qr']})

    def test_print_rejects_embedded_api_navigation_and_remote_css(self):
        payloads = ['<img src="/api/method/unsafe">', '<img src="/assets/../api/method/unsafe">',
            '<img src="/%61pi/method/unsafe">', '<iframe src="/files/page.html"></iframe>',
            '<object data="/api/method/unsafe"></object>', '<meta http-equiv="refresh" content="0;url=/api/x">',
            '<style>@import "https://quickchart.io/qr";</style>', '<svg><image href="/api/x"/></svg>',
            r'<style>x{background:u\72l(/api/x)}</style>', '<img src="data:image/svg+xml;base64,AAAA">',
            '<link rel="stylesheet" href="/files/unreviewed.css">']
        for content in payloads:
            with self.subTest(content=content), self.assertRaises(Denied):
                validate_html(content, 'https://erp.example', {'https://quickchart.io': ['/qr']})

    def test_default_policy_valid(self):
        self.assertEqual(validate_policy(policy())["version"], 1)

    def test_arbitrary_method_cannot_be_enabled(self):
        p = policy(); p["methods"].append("frappe.client.insert")
        with self.assertRaises(Denied): validate_policy(p)

    def test_report_requires_explicit_review_and_revision(self):
        p = policy(); p["reports"] = {"X": {"sha256": "a" * 64, "reviewed_no_business_mutations": True, "reviewed_read_scope": True}}
        p["app_revisions"] = {}
        with self.assertRaises(Denied): validate_policy(p)
        p["app_revisions"] = {"frappe": "a" * 12}
        validate_policy(p)
        p["reports"]["X"]["reviewed_no_business_mutations"] = False
        with self.assertRaises(Denied): validate_policy(p)

    def test_mutation_verbs_rejected(self):
        for verb in ("PUT", "PATCH", "DELETE", "TRACE", "HEAD"):
            with self.subTest(verb=verb), self.assertRaises(Denied):
                route("/api/resource/Customer/C-1", verb, {}, METHODS)

    def test_post_read_rpc_allowed_but_post_resource_rejected(self):
        self.assertEqual(route("/api/method/frappe.client.get_list", "POST", {}, METHODS)["operation"], "list")
        with self.assertRaises(Denied): route("/api/resource/Customer", "POST", {}, METHODS)

    def test_cmd_precedence_cannot_hide_mutation_under_get(self):
        with self.assertRaises(Denied):
            route("/api/resource/Customer", "GET", {"cmd": "frappe.client.delete"}, METHODS)

    def test_rpc_aliases(self):
        for prefix in ("/api/method/", "/api/v1/method/", "/api/v2/method/"):
            self.assertEqual(route(prefix + "frappe.client.get", "GET", {}, METHODS)["operation"], "read")

    def test_rest_aliases_preserve_data_shape(self):
        for prefix in ("/api/resource/", "/api/v1/resource/", "/api/v2/document/"):
            p = route(prefix + "Customer/C/1", "GET", {}, METHODS)
            self.assertEqual(p["args"]["name"], "C/1")
            self.assertEqual(p["shape"], "rest")

    def test_internal_dispatch_is_not_public(self):
        with self.assertRaises(Denied):
            route("/api/method/namar_custom.ai_readonly.boundary.dispatch", "GET", {}, METHODS)

    def test_dangerous_parameters(self):
        for key, value in (("run_method", "save"), ("query", "unsafe.query"), ("ignore_user_permissions", 1),
                           ("ignore_permissions", "true"), ("export_in_background", 1), ("doc", {"doctype": "X"}),
                           ("parent", "User"), ("expand_links", "1"), ("group_by", "secret_amount"), ("user", "Administrator")):
            p = {"operation": "list", "args": {key: value}}
            with self.subTest(key=key), self.assertRaises(Denied): validate_request(p, "ai@example.com")

    def test_report_cannot_use_prepared_result(self):
        p = {"operation": "report", "args": {"filters": {"prepared_report_name": "other"}}}
        with self.assertRaises(Denied): validate_request(p, "ai@example.com")

    def test_report_always_synchronous(self):
        p = validate_request({"operation": "report", "args": {}}, "ai@example.com")
        self.assertEqual(p["args"]["ignore_prepared_report"], 1)

    def test_field_expression_rejected(self):
        for value in (["secret as name"], ["count(*)"], ["customer.name"], ["name", "*"]):
            with self.subTest(value=value), self.assertRaises(Denied): field_names(value)

    def test_plain_get_value_fieldname_supported(self):
        self.assertEqual(field_names("customer_name"), ["customer_name"])

    def test_arabic_field_names_supported_without_expressions(self):
        self.assertEqual(field_names("رقم_الإقامة"), ["رقم_الإقامة"])
        with self.assertRaises(Denied): field_names("رقم_الإقامة as name")

    def test_filter_ceiling_and_no_cross_doctype_join(self):
        with self.assertRaises(Denied): checked_filters({"secret": 5}, {"name"})
        with self.assertRaises(Denied): checked_filters([["Other", "name", "=", "x"]], {"name"})
        self.assertEqual(checked_filters({"name": ["in", ["a", "b"]]}, {"name"}), [["name", "in", ["a", "b"]]])


class BoundaryTests(unittest.TestCase):
    def test_custom_report_export_preserves_fixed_filters(self):
        b, f, m, calls = make_boundary()
        query = ModuleType("frappe.desk.query_report")
        desk = ModuleType("frappe.desk"); desk.query_report = query
        report = Row(ref_doctype="Customer", custom_filters={"branch": "Fixed"})
        query.get_report_doc = lambda name: report
        query.format_fields = lambda data: self.assertEqual(data.filters, {"branch": "Fixed"})
        query.build_xlsx_data = lambda *args: ([], [])
        b.report_source = lambda name: "c" * 64
        def run(report, name, filters, user, plan):
            self.assertEqual(filters, {"branch": "Fixed"})
            return {"columns": [], "result": []}
        b._run_report = run; f._dict = Row
        permission = ModuleType("frappe.permissions"); permission.can_export = lambda *args, **kwargs: True
        utils = ModuleType("frappe.desk.utils")
        utils.get_csv_bytes = lambda *args: b"CSV"
        utils.provide_binary_file = lambda name, extension, content: content
        xlsx = ModuleType("frappe.utils.xlsxutils"); xlsx.make_xlsx = lambda *args, **kwargs: None
        configured = policy(); configured["reports"]["Custom"] = {"sha256": "c" * 64,
            "reviewed_no_business_mutations": True, "reviewed_read_scope": True}
        plan = {"policy": configured, "role": DEFAULT_ROLE, "user": f.session.user,
            "args": {"report_name": "Custom", "filters": {"branch": "Elsewhere"}, "file_format_type": "CSV"}}
        with patch.dict(sys.modules, {"frappe.desk": desk, "frappe.desk.query_report": query,
            "frappe.permissions": permission, "frappe.desk.utils": utils, "frappe.utils.xlsxutils": xlsx}):
            self.assertEqual(b._report(plan, export=True), b"CSV")

    def test_pdf_resource_validation_precedes_renderer(self):
        b, f, m, calls = make_boundary()
        f.local.response = Row()
        f.get_print = lambda *args, **kwargs: '<img src="/api/method/unsafe">'
        utils = ModuleType("frappe.utils"); utils.get_url = lambda **kwargs: "https://erp.example"
        pdf = ModuleType("frappe.utils.pdf"); pdf.get_pdf = lambda value: calls.append(("pdf",)) or b"PDF"
        fmt = ModuleType("frappe.utils.print_format")
        fmt.print_language = lambda language: nullcontext()
        fmt.validate_print_permission = lambda doc: None
        modules = {"frappe": f, "frappe.utils": utils, "frappe.utils.pdf": pdf, "frappe.utils.print_format": fmt}
        with patch.dict(sys.modules, modules):
            with self.assertRaises(Denied): render_pdf(Row(doctype="Customer", name="C-1"), "Standard", {}, {})
            self.assertNotIn(("pdf",), calls)
            f.get_print = lambda *args, **kwargs: calls.append(("get_print", kwargs)) or '<p>Safe print</p>'
            render_pdf(Row(doctype="Customer", name="C-1"), "Standard", {"letterhead": "Selected Letter Head", "no_letterhead": 1}, {})
        self.assertIn(("pdf",), calls)
        print_args = next(call[1] for call in calls if call[0] == "get_print")
        self.assertEqual(print_args["letterhead"], "Selected Letter Head")
        self.assertEqual(print_args["no_letterhead"], 1)
        self.assertEqual(f.local.response.filecontent, b"PDF")
        self.assertEqual(f.local.response.type, "pdf")

    def builder(self, frappe):
        with patch.dict(sys.modules, {"frappe": frappe}):
            return importlib.import_module("namar_custom.ai_readonly.builder")

    def test_builder_removes_hidden_columns_without_rejecting_doctype(self):
        b, f, m, calls = make_boundary()
        m.fields.append(Row(fieldname="الفرع", fieldtype="Data", permlevel=0))
        builder = self.builder(f)
        report = Row(ref_doctype="Customer", json=json.dumps({"fields": [["name", "Customer"],
            ["secret_amount", "Customer"], ["الفرع", "Customer"]],
            "filters": [["Customer", "creation", "Timespan", "next 14 days", False]],
            "order_by": "`tabCustomer`.`modified` desc"}))
        report.get_standard_report_columns = lambda params: params["fields"]
        builder.prepare(report, {"role": DEFAULT_ROLE, "user": f.session.user}, {})
        prepared = json.loads(report.json)
        self.assertEqual(prepared["fields"], [["name", "Customer"], ["الفرع", "Customer"]])
        self.assertEqual(prepared["filters"][0], ["Customer", "creation", "timespan", "next 14 days"])

    def test_builder_rejects_hidden_saved_and_caller_filters_and_sort(self):
        b, f, m, calls = make_boundary(); builder = self.builder(f)
        for addition, supplied in (({"filters": [["Customer", "secret_amount", ">", 2]]}, {}),
             ({}, {"secret_amount": 2}), ({"order_by": "`tabCustomer`.`secret_amount` desc"}, {})):
            params = {"fields": [["name", "Customer"]], "order_by": "`tabCustomer`.`modified` desc", **addition}
            report = Row(ref_doctype="Customer", json=json.dumps(params))
            report.get_standard_report_columns = lambda p: p["fields"]
            with self.assertRaises(Denied): builder.prepare(report, {"role": DEFAULT_ROLE, "user": f.session.user}, supplied)

    def test_builder_masks_conditional_fields_before_total_and_removes_aux_owner(self):
        b, f, m, calls = make_boundary(); builder = self.builder(f)
        m.permissions.append(Row(role=DEFAULT_ROLE, read=1, permlevel=1, if_owner=1))
        report = Row(ref_doctype="Customer", json=json.dumps({"fields": [["name", "Customer"],
            ["secret_amount", "Customer"]], "order_by": "`tabCustomer`.`modified` desc", "add_totals_row": 1}))
        report.get_standard_report_columns = lambda p: p["fields"]
        scope = builder.prepare(report, {"role": DEFAULT_ROLE, "user": f.session.user}, {})
        self.assertFalse(json.loads(report.json)["add_totals_row"])
        view = ModuleType("frappe.desk.reportview")
        def append(rows):
            self.assertEqual(rows, [["mine", 10], ["other", None]])
            return rows + [["Total", 10]]
        view.append_totals_row = append
        with patch.dict(sys.modules, {view.__name__: view}):
            columns, rows = builder.finish(["name", "secret_amount", "owner"],
                [["mine", 10, f.session.user], ["other", 999, "other@example.com"]], scope)
        self.assertEqual(columns, ["name", "secret_amount"])
        self.assertEqual(rows[-1], ["Total", 10])

    def test_builder_child_column_requires_unique_visible_parent_table(self):
        b, f, m, calls = make_boundary(); builder = self.builder(f)
        table = Row(fieldname="items", fieldtype="Table", options="Customer Item", permlevel=0)
        m.fields.append(table); m.get_table_fields = lambda: [table]
        child = Row(istable=1, fields=[Row(fieldname="item_code", permlevel=0, fieldtype="Data"),
             Row(fieldname="cost", permlevel=1, fieldtype="Currency")])
        child.get_field = lambda name: next((field for field in child.fields if field.fieldname == name), None)
        f.get_meta = lambda dt: m if dt == "Customer" else child
        report = Row(ref_doctype="Customer", json=json.dumps({"fields": [["item_code", "Customer Item"],
            ["cost", "Customer Item"]], "order_by": "`tabCustomer`.`modified` desc"}))
        report.get_standard_report_columns = lambda p: p["fields"]
        builder.prepare(report, {"role": DEFAULT_ROLE, "user": f.session.user}, {})
        self.assertEqual(json.loads(report.json)["fields"], [["item_code", "Customer Item"]])
        m.get_table_fields = lambda: [table, Row(fieldname="other_items", options="Customer Item", permlevel=1)]
        with self.assertRaises(Denied): builder.prepare(report, {"role": DEFAULT_ROLE, "user": f.session.user}, {})

    def test_internal_dispatch_cannot_be_shadowed_by_script_or_override(self):
        b, f, m, calls = make_boundary()
        utility = ModuleType("frappe.core.doctype.server_script.server_script_utils")
        utility.get_server_script_map = lambda: {}
        f.override_whitelisted_method = lambda method: method
        with patch.dict(sys.modules, {utility.__name__: utility}):
            b._actual_check_dispatch_route()
            f.override_whitelisted_method = lambda method: "unsafe.override"
            with self.assertRaises(Denied): b._actual_check_dispatch_route()
            f.override_whitelisted_method = lambda method: method
            utility.get_server_script_map = lambda: {"_api": {"namar_custom.ai_readonly.boundary.dispatch": "unsafe script"}}
            with self.assertRaises(Denied): b._actual_check_dispatch_route()

    def test_app_revision_pin_rejects_changed_or_extra_installed_app(self):
        b, f, m, calls = make_boundary()
        change_log = ModuleType("frappe.utils.change_log")
        f.get_installed_apps = lambda: ["frappe"]
        change_log.get_app_last_commit_ref = lambda app: "a" * 7
        with patch.dict(sys.modules, {"frappe.utils.change_log": change_log}):
            b._actual_check_app_revisions({"policy": policy()})
            change_log.get_app_last_commit_ref = lambda app: "c" * 7
            with self.assertRaises(Denied): b._actual_check_app_revisions({"policy": policy()})
            change_log.get_app_last_commit_ref = lambda app: "a" * 7
            f.get_installed_apps = lambda: ["frappe", "unexpected"]
            with self.assertRaises(Denied): b._actual_check_app_revisions({"policy": policy()})

    def test_print_prunes_child_fields_before_and_after_controller(self):
        b, f, m, calls = make_boundary(path="/api/method/frappe.utils.print_format.download_pdf",
            args={"doctype": "Customer", "name": "C-1"})
        table = Row(fieldname="items", fieldtype="Table", options="Customer Item", permlevel=0)
        m.fields.append(table); m.get_table_fields = lambda: [table]
        child_meta = Row(name="Customer Item", fields=[Row(fieldname="item_code", fieldtype="Data", permlevel=0),
            Row(fieldname="cost", fieldtype="Currency", permlevel=1)])
        child_meta.get_field = lambda name: next((r for r in child_meta.fields if r.fieldname == name), None)
        def document(values, meta):
            doc = Row(values); doc.meta = meta
            doc.set = lambda name, value: doc.__setitem__(name, value)
            doc.check_permission = lambda action: None
            doc.apply_fieldlevel_read_permissions = lambda: None
            doc.as_dict = lambda: {k: v for k, v in doc.items() if k != "meta" and not callable(v)}
            return doc
        child = document({"doctype": "Customer Item", "name": "i1", "item_code": "SKU", "cost": 999}, child_meta)
        original = document({"doctype": "Customer", "name": "C-1", "owner": "other@example.com", "items": [child]}, m)
        def get_doc(*args):
            if len(args) == 2:
                return original
            values = dict(args[0])
            values["items"] = [document(row, child_meta) for row in values["items"]]
            scoped = document(values, m)
            def before_print(method, *args, **kwargs):
                self.assertEqual(method, "before_print")
                scoped.secret_amount = 555
                scoped["items"][0].cost = 777
                scoped["items"].append(document({"doctype": "Customer Item", "name": "i2", "item_code": "Second", "cost": 888}, child_meta))
            scoped.run_method = before_print
            return scoped
        f.get_doc = get_doc
        source_hash = "c" * 64
        b.print_source = lambda *args: source_hash
        configured = policy(); configured["print_formats"]["Customer::Standard"] = {
            "sha256": source_hash, "reviewed_no_business_mutations": True, "reviewed_read_scope": True}
        f.get_cached_doc = lambda name: Row(protected_role=DEFAULT_ROLE, policy_json=json.dumps(configured))
        fmt = ModuleType("namar_custom.ai_readonly.print_resources")
        def render(scoped, format_name, args, resources):
            self.assertIsNot(scoped, original)
            self.assertIsNone(scoped["items"][0].cost)
            scoped.run_method("before_print", {})
            self.assertIsNone(scoped.secret_amount)
            self.assertTrue(all(row.cost is None for row in scoped["items"]))
            self.assertEqual(scoped["items"][1].item_code, "Second")
            return b"PDF"
        fmt.render_pdf = render
        with patch.dict(sys.modules, {fmt.__name__: fmt}):
            b.enforce_request(); self.assertEqual(b.dispatch(), b"PDF")

    def test_existing_primary_and_replica_both_read_only(self):
        b, f, m, calls = make_boundary()
        f.conf.read_from_replica = True
        primary = SimpleNamespace(rollback=lambda: calls.append(("primary rollback",)),
            begin=lambda **kwargs: calls.append(("primary begin", kwargs)))
        replica = SimpleNamespace(rollback=lambda: calls.append(("replica rollback",)),
            begin=lambda **kwargs: calls.append(("replica begin", kwargs)))
        f.local.primary_db = primary; f.local.replica_db = replica; f.local.db = replica
        b.enforce_request()
        self.assertFalse(f.conf.read_from_replica)
        self.assertIs(f.local.db, primary)
        self.assertIn(("primary begin", {"read_only": True}), calls)
        self.assertIn(("replica begin", {"read_only": True}), calls)

    def test_other_users_keep_replica_configuration(self):
        b, f, m, calls = make_boundary(roles=["Sales User"])
        f.conf.read_from_replica = True
        b.enforce_request()
        self.assertTrue(f.conf.read_from_replica)
        self.assertEqual(calls, [])

    def test_list_as_dict_false_masks_conditional_fields(self):
        b, f, m, calls = make_boundary(args={"fields": ["name", "secret_amount"], "as_dict": "0"})
        m.permissions.append(Row(role=DEFAULT_ROLE, read=1, permlevel=1, if_owner=1))
        f.get_list = lambda *args, **kwargs: [{"name": "other", "owner": "other@example.com", "secret_amount": 999}]
        b.enforce_request(); b.dispatch()
        self.assertEqual(f.response["data"], [["other", None]])

    def test_query_report_hash_does_not_require_renamed_source_folder(self):
        b, f, m, calls = make_boundary()
        report = Row(name="Item Balance (Simple)", is_standard="Yes", report_type="Query Report",
                     query="select name from tabItem", reference_report=None)
        report.as_dict = lambda: dict(report)
        f.get_doc = lambda *args: report
        module = ModuleType("frappe.modules"); module.scrub = lambda x: x.lower().replace(" ", "_")
        with patch.dict(sys.modules, {"frappe.modules": module}):
            first = b.report_source(report.name)
            report.query = "select name, item_name from tabItem"
            second = b.report_source(report.name)
        self.assertNotEqual(first, second)

    def test_conditional_search_keeps_other_owner_public_matches(self):
        b, f, m, calls = make_boundary(path="/api/method/frappe.desk.search.search_link",
            args={"doctype": "Customer", "txt": "C"})
        m.permissions.append(Row(role=DEFAULT_ROLE, read=1, permlevel=1, if_owner=1))
        m.title_field = "secret_amount"; m.search_fields = "customer_name"; m.show_title_field_in_link = 1
        def search(dt, **kwargs):
            calls.append(("search", kwargs))
            own = ["owner", "=", f.session.user] in kwargs["filters"]
            if own:
                self.assertIn("secret_amount", kwargs["fields"])
                return [{"name": "C-owned", "customer_name": "Own", "secret_amount": 123}]
            self.assertNotIn("secret_amount", kwargs["fields"])
            self.assertFalse(any(r[0] == "secret_amount" for r in kwargs["or_filters"]))
            return [{"name": "C-other", "customer_name": "Other"}]
        f.get_list = search
        b.enforce_request(); result = b.dispatch()
        self.assertEqual([r["value"] for r in result], ["C-other", "C-owned"])
        self.assertIsNone(result[0]["label"])
        self.assertEqual(result[1]["label"], 123)

    def test_single_get_value_uses_document_not_nonexistent_sql_table(self):
        b, f, m, calls = make_boundary(path="/api/method/frappe.client.get_value",
            args={"doctype": "Customer", "fieldname": "customer_name", "as_dict": False})
        m.issingle = 1
        doc = Row(doctype="Customer", name="Customer", owner="Administrator", meta=m)
        doc.check_permission = lambda action: calls.append(("permission", action))
        doc.apply_fieldlevel_read_permissions = lambda: None
        doc.as_dict = lambda: {"name": "Customer", "owner": "Administrator", "customer_name": "single value"}
        f.get_doc = lambda *args: doc
        b.enforce_request()
        self.assertEqual(b.dispatch(), "single value")
        self.assertFalse(any(c[0] == "list" for c in calls))

    def test_get_value_as_list_contract(self):
        b, f, m, calls = make_boundary(path="/api/method/frappe.client.get_value", args={"doctype": "Customer",
            "fieldname": '["name","customer_name"]', "filters": "C-1", "as_dict": "0"})
        f.get_list = lambda *args, **kwargs: [{"name": "C-1", "customer_name": "Visible"}]
        b.enforce_request()
        self.assertEqual(b.dispatch(), ["C-1", "Visible"])

    def test_script_report_disables_timer_on_local_document(self):
        b, f, m, calls = make_boundary()
        query = ModuleType("frappe.desk.query_report")
        desk = ModuleType("frappe.desk"); desk.query_report = query
        query.validate_filters_permissions = lambda *args: None
        report = Row(report_type="Script Report", add_total_row=0, disable_prepared_report_automation=0)
        def generate(doc, **kwargs):
            self.assertEqual(doc.disable_prepared_report_automation, 1)
            return {"columns": [], "result": []}
        query.generate_report_result = generate
        with patch.dict(sys.modules, {"frappe.desk": desk, "frappe.desk.query_report": query}):
            result = b._run_report(report, "Report", {}, f.session.user)
        self.assertEqual(result["result"], [])

    def test_report_builder_uses_standard_report_runner(self):
        b, f, m, calls = make_boundary()
        query = ModuleType("frappe.desk.query_report")
        desk = ModuleType("frappe.desk"); desk.query_report = query
        query.validate_filters_permissions = lambda *args: None
        report = Row(report_type="Report Builder")
        report.run_standard_report = lambda filters, limit, user: ([{"fieldname": "name"}], [["C-1"]])
        with patch.dict(sys.modules, {"frappe.desk": desk, "frappe.desk.query_report": query}):
            result = b._run_report(report, "Builder", {}, f.session.user)
        self.assertEqual(result["result"], [["C-1"]])

    def test_child_export_uses_scoped_documents_and_masks_owner_fields(self):
        b, f, m, calls = make_boundary(path="/api/method/frappe.core.doctype.data_export.exporter.export_data",
            args={"doctype": "Customer", "all_doctypes": True})
        m.permissions.append(Row(role=DEFAULT_ROLE, read=1, permlevel=1, if_owner=1))
        table = Row(fieldname="items", fieldtype="Table", options="Customer Item", permlevel=0)
        m.fields.append(table); m.get_table_fields = lambda: [table]
        child_meta = Row(name="Customer Item", fields=[
            Row(fieldname="item_code", fieldtype="Data", permlevel=0),
            Row(fieldname="cost", fieldtype="Currency", permlevel=1)])
        child_meta.get_field = lambda name: next((r for r in child_meta.fields if r.fieldname == name), None)
        f.get_meta = lambda name: m if name == "Customer" else child_meta
        def document(name, owner):
            child = Row(doctype="Customer Item", name=name + "-item", owner=owner, meta=child_meta,
                        item_code="SKU", cost=123)
            child.as_dict = lambda: {"doctype": child.doctype, "name": child.name, "item_code": child.item_code,
                                     "cost": child.cost, "owner": owner}
            doc = Row(doctype="Customer", name=name, owner=owner, meta=m, items=[child])
            doc.check_permission = lambda action: None
            doc.apply_fieldlevel_read_permissions = lambda: None
            doc.as_dict = lambda: {"name": name, "owner": owner, "items": [child.as_dict()]}
            return doc
        docs = {"mine": document("mine", f.session.user), "other": document("other", "other@example.com")}
        f.get_doc = lambda dt, name: docs[name]
        f.get_list = lambda *args, **kwargs: [{"name": "mine"}, {"name": "other"}]
        f._dict = Row
        exported = []
        exporter_module = ModuleType("frappe.core.doctype.data_export.exporter")
        class DataExporter:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs); self.prepare_args()
            def prepare_args(self):
                self.child_doctypes = [{"doctype": "Customer Item", "parentfield": "items"}]
            def build_response(self):
                self.writer = SimpleNamespace(writerow=lambda row: None); self.add_data()
            def add_data_row(self, rows, dt, parentfield, row, index):
                exported.append((dt, parentfield, dict(row)))
        exporter_module.DataExporter = DataExporter
        access = ModuleType("frappe.core.doctype.access_log.access_log")
        access.make_access_log = lambda **kwargs: calls.append(("access_log",))
        permissions = ModuleType("frappe.permissions")
        permissions.can_export = lambda *args, **kwargs: True
        with patch.dict(sys.modules, {"frappe.core.doctype.data_export.exporter": exporter_module,
             "frappe.core.doctype.access_log.access_log": access, "frappe.permissions": permissions}):
            b.enforce_request(); b.dispatch()
        child_rows = [row for dt, field, row in exported if dt == "Customer Item"]
        self.assertEqual(len(child_rows), 2)
        self.assertEqual(child_rows[0]["cost"], 123)
        self.assertNotIn("cost", child_rows[1])
        self.assertEqual(child_rows[1]["item_code"], "SKU")

    def test_bad_policy_does_not_break_other_users(self):
        b, f, m, calls = make_boundary(roles=["System Manager"])
        f.get_cached_doc = lambda name: Row(protected_role=DEFAULT_ROLE, policy_json="{broken")
        b.enforce_request()
        self.assertEqual(calls, [])
        self.assertFalse(f.flags.read_only)

    def test_missing_settings_does_not_break_other_users(self):
        b, f, m, calls = make_boundary(roles=["Sales User"])
        f.db.exists = lambda *args: False
        b.enforce_request()
        self.assertEqual(calls, [])

    def test_permission_query_change_denies_before_native_query(self):
        b, f, m, calls = make_boundary()
        b.permission_source = lambda: "c" * 64
        with self.assertRaises(f.PermissionError): b.enforce_request()
        self.assertFalse(any(c[0] == "list" for c in calls))

    def test_get_value_by_name_and_plain_field(self):
        b, f, m, calls = make_boundary(path="/api/method/frappe.client.get_value",
             args={"doctype": "Customer", "fieldname": "customer_name", "filters": "C-1"})
        b.enforce_request(); b.dispatch()
        self.assertEqual(calls[-1][2]["fields"], ["customer_name"])
        self.assertEqual(calls[-1][2]["filters"], [["name", "=", "C-1"]])

    def test_conditional_list_fields_only_returned_on_owned_rows(self):
        b, f, m, calls = make_boundary(args={"fields": '["name","secret_amount"]'})
        m.permissions.append(Row(role=DEFAULT_ROLE, read=1, permlevel=1, if_owner=1))
        f.get_list = lambda *args, **kwargs: [
            {"name": "mine", "owner": f.session.user, "secret_amount": 100},
            {"name": "other", "owner": "other@example.com", "secret_amount": 200}]
        b.enforce_request(); b.dispatch()
        self.assertEqual(f.response["data"], [{"name": "mine", "secret_amount": 100}, {"name": "other"}])

    def test_conditional_sort_and_filter_constrain_owner(self):
        b, f, m, calls = make_boundary(args={"filters": {"secret_amount": [">", 2]}, "order_by": "secret_amount desc"})
        m.permissions.append(Row(role=DEFAULT_ROLE, read=1, permlevel=1, if_owner=1))
        b.enforce_request(); b.dispatch()
        self.assertIn(["owner", "=", f.session.user], calls[-1][2]["filters"])

    def test_search_uses_permitted_metadata_label_and_searchfields(self):
        b, f, m, calls = make_boundary(path="/api/method/frappe.desk.search.search_link",
                                        args={"doctype": "Customer", "txt": "Example"})
        m.title_field = "customer_name"; m.show_title_field_in_link = 1
        m.search_fields = "customer_name,secret_amount"
        b.enforce_request(); b.dispatch()
        self.assertEqual(calls[-1][2]["fields"], ["name", "customer_name"])
        self.assertNotIn("secret_amount", str(calls[-1][2]["or_filters"]))

    def test_report_definition_includes_security_columns_filters_and_flags(self):
        b, f, m, calls = make_boundary()
        original = {"columns": [{"fieldtype": "Link", "options": "Account"}],
                    "filters": [{"fieldname": "company", "options": "Company"}],
                    "prepared_report": 0, "snapshot_report": 0, "doctype_to_sync": []}
        first = b.fingerprint(b._definition(original))
        for key, replacement in (("columns", [{"fieldtype": "Data"}]), ("filters", []),
                                 ("prepared_report", 1), ("snapshot_report", 1),
                                 ("doctype_to_sync", [{"doc_type": "Account"}])):
            altered = {**original, key: replacement}
            self.assertNotEqual(first, b.fingerprint(b._definition(altered)))

    def test_document_read_filters_fields_after_native_permission_check(self):
        b, f, m, calls = make_boundary(path="/api/resource/Customer/C-1")
        doc = Row(doctype="Customer", name="C-1", owner="other@example.com", meta=m)
        doc.check_permission = lambda action: calls.append(("permission", action))
        doc.apply_fieldlevel_read_permissions = lambda: calls.append(("native_field_permissions",))
        doc.as_dict = lambda: {"name": "C-1", "customer_name": "Customer", "secret_amount": 200,
                              "api_secret": "hidden", "owner": "other@example.com"}
        f.get_doc = lambda *args: doc
        b.enforce_request(); b.dispatch()
        self.assertIn(("permission", "read"), calls)
        self.assertNotIn("secret_amount", f.response["data"])
        self.assertNotIn("api_secret", f.response["data"])

    def test_document_share_cannot_override_owner_ceiling(self):
        b, f, m, calls = make_boundary(path="/api/resource/Customer/C-1")
        m.permissions[0].if_owner = 1
        f.get_doc = lambda *args: Row(doctype="Customer", name="C-1", owner="other@example.com", meta=m)
        b.enforce_request()
        with self.assertRaises(f.PermissionError): b.dispatch()

    def test_owner_only_field_is_kept_for_owned_document_only(self):
        b, f, m, calls = make_boundary()
        m.permissions.append(Row(role=DEFAULT_ROLE, read=1, permlevel=1, if_owner=1))
        plan = {"role": DEFAULT_ROLE, "user": f.session.user}
        own, _ = b._ceiling(m, plan, owner=f.session.user)
        other, _ = b._ceiling(m, plan, owner="other@example.com")
        self.assertIn("secret_amount", own)
        self.assertNotIn("secret_amount", other)

    def test_native_permission_denial_is_not_overridden(self):
        b, f, m, calls = make_boundary(path="/api/resource/Customer/C-1")
        doc = Row(doctype="Customer", name="C-1", owner="other@example.com", meta=m)
        doc.check_permission = lambda action: (_ for _ in ()).throw(f.PermissionError("Native denial"))
        f.get_doc = lambda *args: doc
        b.enforce_request()
        with self.assertRaisesRegex(f.PermissionError, "Native denial"): b.dispatch()

    def test_unreviewed_source_hash_denied(self):
        b, f, m, calls = make_boundary()
        plan = {"policy": {"reports": {"R": {"sha256": "a" * 64, "reviewed_no_business_mutations": True, "reviewed_read_scope": True}}}}
        with self.assertRaises(Denied): b._check_reviewed(plan, "reports", "R", "b" * 64)
        b._check_reviewed(plan, "reports", "R", "a" * 64)

    def test_other_user_not_changed(self):
        b, f, m, calls = make_boundary(roles=["System Manager"])
        before = dict(f.form_dict)
        b.enforce_request()
        self.assertEqual(dict(f.form_dict), before)
        self.assertEqual(calls, [])
        self.assertFalse(f.flags.read_only)

    def test_even_additional_privileged_role_remains_protected(self):
        b, f, m, calls = make_boundary(roles=[DEFAULT_ROLE, "System Manager"], verb="DELETE")
        with self.assertRaises(f.PermissionError): b.enforce_request()
        self.assertEqual(calls, [("rollback", True), ("begin", {"read_only": True})])

    def test_missing_settings_fails_closed_for_protected_role(self):
        b, f, m, calls = make_boundary()
        f.db.exists = lambda *args: False
        with self.assertRaises(f.PermissionError): b.enforce_request()
        self.assertEqual(calls, [("rollback", True), ("begin", {"read_only": True})])

    def test_read_only_before_rollback_and_dispatch_arguments_cleared(self):
        b, f, m, calls = make_boundary(args={"fields": '["name"]'})
        b.enforce_request()
        self.assertEqual(calls, [("rollback", True), ("begin", {"read_only": True})])
        self.assertEqual(dict(f.form_dict), {"cmd": "namar_custom.ai_readonly.boundary.dispatch"})

    def test_forged_direct_dispatch_without_plan_rejected(self):
        b, f, m, calls = make_boundary()
        with self.assertRaises(f.PermissionError): b.dispatch()

    def test_read_uses_native_get_list_and_keeps_rest_shape(self):
        b, f, m, calls = make_boundary(args={"fields": '["name","customer_name"]'})
        b.enforce_request()
        self.assertIsNone(b.dispatch())
        self.assertEqual(f.response["data"], [{"name": "C-1"}])
        self.assertNotIn("ignore_permissions", calls[-1][2])

    def test_automatic_role_does_not_expand_fields(self):
        b, f, m, calls = make_boundary(args={"fields": '["secret_amount"]'})
        m.permissions.append(Row(role="All", read=1, permlevel=1, if_owner=0))
        b.enforce_request()
        with self.assertRaises(f.PermissionError): b.dispatch()
        self.assertFalse(any(c[0] == "list" for c in calls))

    def test_share_does_not_add_role_read_grant(self):
        b, f, m, calls = make_boundary()
        m.permissions = [Row(role="All", read=1, permlevel=0, if_owner=0)]
        b.enforce_request()
        with self.assertRaises(f.PermissionError): b.dispatch()

    def test_owner_filter_is_and_not_or(self):
        b, f, m, calls = make_boundary(args={"or_filters": '[ ["name", "=", "other"] ]'})
        m.permissions[0].if_owner = 1
        b.enforce_request(); b.dispatch()
        self.assertIn(["owner", "=", "ai@example.com"], calls[-1][2]["filters"])

    def test_password_and_hidden_level_excluded_from_star(self):
        b, f, m, calls = make_boundary(args={"fields": '["*"]'})
        b.enforce_request(); b.dispatch()
        self.assertNotIn("api_secret", calls[-1][2]["fields"])
        self.assertNotIn("secret_amount", calls[-1][2]["fields"])

    def test_large_page_denied(self):
        b, f, m, calls = make_boundary(args={"limit_page_length": 10001})
        b.enforce_request()
        with self.assertRaises(f.PermissionError): b.dispatch()

    def test_unsafe_sort_denied(self):
        b, f, m, calls = make_boundary(args={"order_by": "secret_amount desc"})
        b.enforce_request()
        with self.assertRaises(f.PermissionError): b.dispatch()

    def test_select_only_search_does_not_return_extra_fields(self):
        b, f, m, calls = make_boundary(path="/api/method/frappe.desk.search.search_link", args={"doctype": "Customer", "txt": "C"})
        m.permissions[0].read = 0
        b.enforce_request()
        self.assertEqual(b.dispatch(), [{"value": "C-1", "description": ""}])
        self.assertEqual(calls[-1][2]["fields"], ["name"])

    def test_settings_schema_is_system_manager_only(self):
        root = Path(__file__).resolve().parents[1]
        schema = json.loads((root / "namar_custom/namar_custom/doctype/ai_read_only_settings/ai_read_only_settings.json").read_text())
        self.assertEqual([r["role"] for r in schema["permissions"]], ["System Manager"])
        self.assertTrue(schema["issingle"])


if __name__ == "__main__":
    unittest.main()
