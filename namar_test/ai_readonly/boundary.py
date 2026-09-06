"""Authenticate first, then route protected users through reviewed read operations.

This module never grants permission. Its role ceiling intersects Frappe's normal
permissions; other users and all stored role/share configuration are untouched.
"""
from __future__ import annotations

from pathlib import Path

import frappe

from .policy import (COMMON_FIELDS, DEFAULT_ROLE, SETTINGS, Denied,
                     checked_filters, field_names, fingerprint, parse_json, route,
                     truthy, validate_policy, validate_request)

PLAN_KEY = "ai_readonly_request_plan"


def _deny():
    frappe.throw("هذا الحساب مخصص للقراءة فقط؛ العملية خارج سياسة الوصول المعتمدة.", frappe.PermissionError)


def _settings():
    if not frappe.db.exists("DocType", SETTINGS):
        return DEFAULT_ROLE, None
    settings = frappe.get_cached_doc(SETTINGS)
    return settings.protected_role or DEFAULT_ROLE, settings.policy_json


def _start_read_only():
    """Protect existing connections and prevent a new unguarded replica swap."""
    frappe.flags.read_only = True
    frappe.flags.ignore_user_permissions_for_doctype = None
    # Site configuration is copied into frappe.local.conf by frappe.init; this
    # flag is request-local and never saves a change to site_config.json.
    frappe.conf.read_from_replica = False
    primary = getattr(frappe.local, "primary_db", None)
    connections = [getattr(frappe.local, "db", None) or frappe.db, primary,
                   getattr(frappe.local, "replica_db", None)]
    seen = set()
    for connection in connections:
        if connection is None or id(connection) in seen:
            continue
        seen.add(id(connection))
        connection.rollback()
        connection.begin(read_only=True)
    if primary is not None:
        frappe.local.db = primary


def _check_dispatch_route():
    # execute_cmd resolves overrides and API Server Scripts before importing
    # Python. Neither may shadow the trusted dispatcher after authentication.
    from frappe.core.doctype.server_script.server_script_utils import get_server_script_map
    method = __name__ + ".dispatch"
    if frappe.override_whitelisted_method(method) != method or get_server_script_map().get("_api", {}).get(method):
        raise Denied("The internal dispatcher is shadowed")


def enforce_request():
    """auth_hooks runs after API/OAuth/session authentication, before dispatch."""
    user = frappe.session.user
    if user in (None, "", "Guest", "Administrator"):
        return
    role, policy = _settings()
    if role not in frappe.get_roles(user):
        return
    try:
        # Protect subsequent error/after-request paths too, including denial due
        # to malformed policy or a blocked operation.
        _start_read_only()
        policy = validate_policy(parse_json(policy))
        plan = validate_request(route(frappe.request.path, frappe.request.method,
                                      frappe.form_dict, policy["methods"]), user)
        plan.update({"role": role, "policy": policy, "user": user})
        # Rollback before starting READ ONLY, rather than implicitly committing a
        # transaction with START TRANSACTION. The read_only flag also survives the
        # standard Database.commit/rollback methods and their next transaction.
        _check_app_revisions(plan)
        if permission_source() != policy["permission_review"]["sha256"]:
            raise Denied("Permission query sources changed since review")
        _check_dispatch_route()
        setattr(frappe.local, PLAN_KEY, plan)
        # app.application gives cmd precedence over REST. Only our internal
        # dispatcher sees the saved plan; caller-supplied arguments are discarded.
        frappe.form_dict.clear()
        frappe.form_dict.cmd = __name__ + ".dispatch"
    except (Denied, TypeError, ValueError):
        _deny()


def _meta(doctype):
    if not isinstance(doctype, str) or not doctype:
        raise Denied("Missing DocType")
    meta = frappe.get_meta(doctype)
    if meta.istable or meta.is_virtual:
        raise Denied("Direct child/virtual access requires a reviewed adapter")
    return meta


def _role_rows(meta, role, flag="read"):
    return [r for r in meta.permissions if r.role == role and int(r.get(flag) or 0)]


def _ceiling(meta, plan, flag="read", owner=None):
    rows = _role_rows(meta, plan["role"], flag)
    base = [r for r in rows if int(r.permlevel or 0) == 0]
    if not base:
        raise Denied("The protected role does not grant this action")
    owner_only = not any(not int(r.if_owner or 0) for r in base)
    is_owner = owner is not None and str(owner).lower() == plan["user"].lower()
    if owner is not None and owner_only and not is_owner:
        raise Denied("Owner restriction")
    levels = {int(r.permlevel or 0) for r in _role_rows(meta, plan["role"], "read")
              if not int(r.if_owner or 0) or is_owner or owner_only}
    fields = set(COMMON_FIELDS)
    fields.update(f.fieldname for f in meta.fields
                  if int(f.permlevel or 0) in levels and f.fieldtype != "Password")
    return fields, owner_only


def _field_scopes(meta, plan):
    """Separate unconditional fields from fields available on owned documents."""
    general, owner_only = _ceiling(meta, plan)
    owned, _ = _ceiling(meta, plan, owner=plan["user"])
    return general, owned - general, owner_only


def _list(plan, *, count=False, force_dict=False):
    args = plan["args"]
    meta = _meta(args.get("doctype"))
    fields, conditional, owner_only = _field_scopes(meta, plan)
    available = fields | conditional
    requested = field_names(args.get("fields"))
    if requested == ["*"]:
        requested = sorted(available - {f.fieldname for f in meta.get_table_fields()})
    if not set(requested) <= available:
        raise Denied("Field permission ceiling")
    filters = checked_filters(args.get("filters"), available)
    or_filters = checked_filters(args.get("or_filters"), available)
    # Filtering/sorting on an owner-only value would reveal it indirectly. Such
    # queries stay useful for the caller's documents by adding the owner limit.
    owner_query = any(part[0] in conditional for part in filters + or_filters)
    order_by = args.get("order_by") or "modified desc"
    for term in order_by.split(","):
        parts = term.strip().split()
        if not parts or parts[0] not in available or len(parts) > 2 or (len(parts) == 2 and parts[1].lower() not in ("asc", "desc")):
            raise Denied("Order expression is outside the field ceiling")
        owner_query = owner_query or parts[0] in conditional
    if owner_only or owner_query:
        filters.append(["owner", "=", plan["user"]])
    maximum = plan["policy"].get("max_rows", 1000)
    limit = int(args.get("limit_page_length") or args.get("limit") or 20)
    start = int(args.get("limit_start") or args.get("start") or 0)
    if not 1 <= limit <= maximum or start < 0:
        raise Denied("Invalid pagination")
    if count:
        data = frappe.get_list(meta.name, filters=filters, or_filters=or_filters,
                               fields=["count(name) as count"], limit_page_length=1)
        return int(data[0]["count"]) if data else 0
    needs_owner = bool(set(requested) & conditional)
    fetched = requested + (["owner"] if needs_owner and "owner" not in requested else [])
    result = frappe.get_list(meta.name, fields=fetched, filters=filters,
                            or_filters=or_filters, order_by=order_by,
                            limit_start=start, limit_page_length=limit)
    for row in result:
        if needs_owner and str(row.get("owner")).lower() != plan["user"].lower():
            for name in conditional:
                row.pop(name, None)
        if "owner" not in requested:
            row.pop("owner", None)
    if not force_dict and not truthy(args.get("as_dict", True)):
        return [[row.get(name) for name in requested] for row in result]
    return result


def _filtered_document(doc, plan, parent_fields=None):
    # Child fields are governed by their own permlevels under the parent's role.
    meta = doc.meta
    if parent_fields is None:
        allowed, _ = _ceiling(meta, plan, owner=doc.owner)
        levels = {int(r.permlevel or 0) for r in _role_rows(meta, plan["role"], "read")
                  if not int(r.if_owner or 0) or str(doc.owner).lower() == plan["user"].lower()}
    else:
        levels = parent_fields
        allowed = set(COMMON_FIELDS) | {f.fieldname for f in meta.fields
                   if int(f.permlevel or 0) in levels and f.fieldtype != "Password"}
    result = {"doctype": doc.doctype}
    for key, value in doc.as_dict().items():
        if key not in allowed:
            continue
        field = meta.get_field(key)
        if field and field.fieldtype in ("Table", "Table MultiSelect"):
            result[key] = [_filtered_document(child, plan, levels) for child in (doc.get(key) or [])]
        else:
            result[key] = value
    return result


def _read(plan):
    args = plan["args"]
    meta = _meta(args.get("doctype"))
    if not args.get("name") and not meta.issingle:
        if "filters" not in args:
            raise Denied("Document reads require a name or filters")
        lookup = {**plan, "args": {**args, "fields": ["name"], "limit_page_length": 1}}
        names = _list(lookup, force_dict=True)
        if not names:
            raise Denied("No readable document matches")
        args = {**args, "name": names[0]["name"]}
    doc = frappe.get_doc(meta.name, args.get("name") or meta.name)
    _ceiling(meta, plan, owner=doc.owner)
    doc.check_permission("read")
    doc.apply_fieldlevel_read_permissions()
    return _filtered_document(doc, plan)


def _value(plan, single=False):
    args = dict(plan["args"])
    requested = field_names(args.get("field") if single else args.get("fieldname"))
    if requested == ["*"]:
        raise Denied("get_value requires explicit fields")
    meta = _meta(args.get("doctype"))
    if single and not meta.issingle:
        raise Denied("Single value requires a Single DocType")
    if meta.issingle:
        values = _read({**plan, "args": {"doctype": meta.name}})
        allowed, _ = _ceiling(meta, plan, owner=values.get("owner"))
        if not set(requested) <= allowed:
            raise Denied("Field permission ceiling")
        if args.get("filters"):
            filters = checked_filters(args["filters"], allowed)
            # Native single-value filter comparisons stay in Frappe's helper.
            rows = frappe.db.get_values_from_single(requested, filters, meta.name, as_dict=True)
            row = rows[0] if rows else {}
        else:
            row = {name: values.get(name) for name in requested}
    else:
        args["fields"] = requested
        if isinstance(args.get("filters"), str) and not args["filters"].startswith(("{", "[")):
            args["filters"] = {"name": args["filters"]}
        args["limit_page_length"] = 1
        rows = _list({**plan, "args": args}, force_dict=True)
        row = rows[0] if rows else {}
    if single:
        return row.get(requested[0])
    if not truthy(args.get("as_dict", True)):
        if not row:
            return None
        return row.get(requested[0]) if len(requested) == 1 else [row.get(name) for name in requested]
    return row


def report_payload(report_name, seen=None):
    """Private review payload; source may contain confidential business logic."""
    from frappe.modules import scrub
    seen = set(seen or ())
    if report_name in seen:
        raise Denied("Cyclic report reference")
    seen.add(report_name)
    report = frappe.get_doc("Report", report_name)
    # Columns (especially Link options), filters, snapshot configuration, custom
    # fields and execution flags are security inputs too. Hash the full document
    # rather than a short code-only subset; exclude only administrative stamps.
    payload = _definition(report.as_dict())
    if report.reference_report:
        payload["reference"] = report_payload(report.reference_report, seen)
    if report.is_standard == "Yes" and report.report_type == "Script Report":
        directory = Path(frappe.get_module_path(report.module)) / "report" / scrub(report.name)
        payload["files"] = {p.name: p.read_text() for p in sorted(directory.glob("*"))
                            if p.suffix in (".py", ".js", ".json", ".html") and p.is_file()}
        if not payload["files"]:
            raise Denied("Standard report source is unavailable")
    return payload


def report_source(report_name):
    """Hash all security-relevant dynamic definitions and executable source."""
    return fingerprint(report_payload(report_name))


def _definition(value):
    stamps = {"owner", "creation", "modified", "modified_by", "_user_tags", "_comments", "_assign", "_liked_by", "__onload"}
    if isinstance(value, dict):
        return {key: _definition(item) for key, item in value.items() if key not in stamps}
    if isinstance(value, (list, tuple)):
        return [_definition(item) for item in value]
    return value


def _check_reviewed(plan, key, name, actual):
    entry = plan["policy"].get(key, {}).get(name, {})
    if entry.get("reviewed_no_business_mutations") is not True or entry.get("reviewed_read_scope") is not True or entry.get("sha256") != actual:
        raise Denied("Source has not been reviewed or has changed")


def _check_app_revisions(plan):
    from frappe.utils.change_log import get_app_last_commit_ref
    expected = plan["policy"].get("app_revisions", {})
    apps = frappe.get_installed_apps()
    if set(expected) != set(apps):
        raise Denied("Installed application set changed")
    if any(get_app_last_commit_ref(app) != expected[app] for app in apps):
        raise Denied("Application source changed since code review")


def permission_payload():
    scripts = frappe.get_all("Server Script", filters={"disabled": 0, "script_type": "Permission Query"},
                              fields=["name", "reference_doctype", "script"], order_by="name")
    external_settings = {}
    if frappe.db.exists("DocType", "Currency Exchange Settings"):
        external_settings["Currency Exchange Settings"] = _definition(frappe.get_doc("Currency Exchange Settings").as_dict())
    return {"scripts": scripts, "external_read_settings": external_settings,
                        "conditions": frappe.get_hooks("permission_query_conditions"),
                        "has_permission": frappe.get_hooks("has_permission"),
                        "request_hooks": {key: frappe.get_hooks(key) for key in
                            ("auth_hooks", "before_request", "after_request", "on_login", "on_session_creation")}}


def permission_source():
    return fingerprint(permission_payload())


def _hook_files(payload):
    """Read hook modules without importing/calling the functions being reviewed."""
    def walk(value):
        if isinstance(value, dict):
            for item in value.values():
                yield from walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                yield from walk(item)
        elif isinstance(value, str) and "." in value and "\n" not in value:
            yield value
    files = {}
    apps = set(frappe.get_installed_apps())
    for method in walk(payload):
        parts = method.split(".")
        if parts[0] not in apps or any(not part.isidentifier() for part in parts):
            continue
        for end in range(len(parts) - 1, 0, -1):
            candidate = Path(frappe.get_app_path(parts[0], *parts[1:end])).with_suffix(".py")
            if candidate.is_file():
                files[".".join(parts[:end])] = candidate.read_text()
                break
    return files


def _run_report(report, name, filters, user, plan=None):
    from frappe.desk import query_report
    filters = parse_json(filters, {})
    query_report.validate_filters_permissions(name, filters, user)
    # A slow Script Report otherwise starts a Timer that writes prepared_report
    # through a new connection outside this request's read-only transaction.
    report.disable_prepared_report_automation = 1
    if report.report_type == "Report Builder":
        scope = None
        if plan is not None:
            from .builder import prepare
            scope = prepare(report, plan, filters)
        columns, rows = report.run_standard_report(filters, None, user)
        if scope is not None:
            from .builder import finish
            columns, rows = finish(columns, rows, scope)
        return {"columns": columns, "result": rows, "message": None, "chart": None,
                "report_summary": None, "skip_total_row": 0, "status": None,
                "add_total_row": False}
    result = query_report.generate_report_result(report, filters=filters, user=user)
    result["add_total_row"] = report.add_total_row and not result.get("skip_total_row", False)
    return result


def _report(plan, *, export=False):
    from frappe.desk import query_report
    args = plan["args"]
    _check_app_revisions(plan)
    name = args.get("report_name")
    if name not in plan["policy"].get("reports", {}):
        raise Denied("Report is outside policy")
    _check_reviewed(plan, "reports", name, report_source(name))
    report = query_report.get_report_doc(name)
    filters = report.custom_filters or args.get("filters") or {}
    meta = _meta(report.ref_doctype)
    _, owner_only = _ceiling(meta, plan, "report")
    if owner_only:
        raise Denied("Reports must not discard owner restrictions")
    # The review checks parity with the source user's actual report behavior.
    # Native SQL/Script scope may be broader than a get_list of its input tables;
    # that existing source behavior is not an AI-introduced grant. Builder fields
    # are checked individually in its adapter rather than rejecting a whole
    # DocType merely because it contains an unrelated hidden field.
    _, read_owner_only = _ceiling(meta, plan)
    if read_owner_only:
        raise Denied("Reports must not discard read owner restrictions")
    if export:
        _, export_owner_only = _ceiling(meta, plan, "export")
        if export_owner_only:
            raise Denied("Report export must not discard export owner restrictions")
        from frappe.permissions import can_export
        from frappe.desk.utils import get_csv_bytes, provide_binary_file
        from frappe.utils.xlsxutils import make_xlsx
        can_export(meta.name, raise_exception=True)
        # Native export_query calls run() without ignore_prepared_report. Reuse
        # its serializers after our explicitly synchronous run, not that wrapper.
        data = frappe._dict(_run_report(report, name, filters, plan["user"], plan))
        data.filters = parse_json(filters, {})
        query_report.format_fields(data)
        rows, widths = query_report.build_xlsx_data(data, [], False)
        file_type = args.get("file_format_type") or "Excel"
        if file_type == "Excel":
            workbook = make_xlsx(rows, "التقرير", column_widths=widths)
            content, extension = workbook.getvalue(), "xlsx"
        elif file_type == "CSV":
            content, extension = get_csv_bytes(rows, {}), "csv"
        else:
            raise Denied("Unsupported report export format")
        return provide_binary_file(name, extension, content)
    return _run_report(report, name, filters, plan["user"], plan)


def print_payload(format_name, doctype):
    # Default Standard formats contain controller/Jinja behavior too. Pin both
    # explicit formats and before-print Server Scripts; administrators review the
    # app hooks/controllers referenced by these sources before enabling them.
    fmt = None if format_name == "Standard" else frappe.get_doc("Print Format", format_name)
    if fmt and fmt.doc_type != doctype:
        raise Denied("Print Format does not match document type")
    scripts = frappe.get_all("Server Script", filters={"disabled": 0, "script_type": "DocType Event",
                              "reference_doctype": doctype, "doctype_event": "Before Print"},
                              fields=["name", "script"], order_by="name")
    meta = frappe.get_meta(doctype)
    schemas = {doctype: meta.as_dict()}
    for table in meta.get_table_fields():
        schemas[table.options] = frappe.get_meta(table.options).as_dict()
    return {"format": _definition(fmt.as_dict()) if fmt else {"name": "Standard", "doc_type": doctype},
                        "before_print": scripts, "jinja_hooks": frappe.get_hooks("jinja"),
                        "doc_events": {key: frappe.get_hooks("doc_events").get(key, {}) for key in (doctype, "*")},
                        "pdf_body_html": frappe.get_hooks("pdf_body_html"),
                        "pdf_header_html": frappe.get_hooks("pdf_header_html"),
                        "pdf_footer_html": frappe.get_hooks("pdf_footer_html"),
                        "get_print_format_template": frappe.get_hooks("get_print_format_template"),
                        "letter_heads": frappe.get_all("Letter Head", fields=["*"], order_by="name"),
                        "print_settings": frappe.get_doc("Print Settings").as_dict(),
                        "print_styles": frappe.get_all("Print Style", fields=["*"], order_by="name"),
                        "schemas": schemas}


def print_source(format_name, doctype):
    return fingerprint(print_payload(format_name, doctype))


def _prune_print_document(doc, allowed, levels):
    """Before Print may refill fields; remove them again before rendering."""
    for field in doc.meta.fields:
        if field.fieldname not in allowed or field.fieldtype == "Password":
            doc.set(field.fieldname, None)
        elif field.fieldtype in ("Table", "Table MultiSelect"):
            for child in doc.get(field.fieldname) or []:
                child_allowed = set(COMMON_FIELDS) | {f.fieldname for f in child.meta.fields
                    if int(f.permlevel or 0) in levels and f.fieldtype != "Password"}
                _prune_print_document(child, child_allowed, levels)


def _print(plan):
    from .print_resources import render_pdf
    args = plan["args"]
    _check_app_revisions(plan)
    doc = frappe.get_doc(args.get("doctype"), args.get("name"))
    _ceiling(doc.meta, plan, "print", doc.owner)
    _ceiling(doc.meta, plan, "read", doc.owner)
    doc.check_permission("read")
    fmt = args.get("format") or doc.meta.default_print_format or "Standard"
    policy_key = doc.doctype + "::Standard" if fmt == "Standard" else fmt
    if policy_key not in plan["policy"].get("print_formats", {}):
        raise Denied("Print Format is outside policy")
    _check_reviewed(plan, "print_formats", policy_key, print_source(fmt, doc.doctype))
    if fmt != "Standard" and frappe.get_cached_value("Print Format", fmt, "print_format_builder_beta"):
        raise Denied("Beta renderer reloads the raw document; a scoped adapter is required")
    doc.apply_fieldlevel_read_permissions()
    scoped_doc = frappe.get_doc(_filtered_document(doc, plan))
    allowed, _ = _ceiling(doc.meta, plan, owner=doc.owner)
    levels = {int(r.permlevel or 0) for r in _role_rows(doc.meta, plan["role"], "read")
              if not int(r.if_owner or 0) or str(doc.owner).lower() == plan["user"].lower()}
    original_run_method = scoped_doc.run_method

    def scoped_run_method(method, *args, **kwargs):
        result = original_run_method(method, *args, **kwargs)
        if method == "before_print":
            _prune_print_document(scoped_doc, allowed, levels)
        return result

    # Instance-local interception, never a module/class monkey patch. The ceiling
    # is captured from the original owner before reviewed controllers run.
    scoped_doc.run_method = scoped_run_method
    return render_pdf(scoped_doc, fmt, args, plan["policy"].get("print_resources", {}))


def _export(plan):
    from frappe.core.doctype.data_export.exporter import DataExporter
    from frappe.core.doctype.access_log.access_log import make_access_log
    from frappe.permissions import can_export
    args = plan["args"]
    meta = _meta(args.get("doctype"))
    _, export_owner_only = _ceiling(meta, plan, "export")
    general, conditional, read_owner_only = _field_scopes(meta, plan)
    allowed = general | conditional
    filters = checked_filters(args.get("filters"), allowed)
    if export_owner_only or read_owner_only or any(f[0] in conditional for f in filters):
        filters.append(["owner", "=", plan["user"]])
    tables = [f for f in meta.get_table_fields() if f.fieldname in allowed]
    columns = {meta.name: sorted(allowed - {f.fieldname for f in tables})}
    levels = {int(r.permlevel or 0) for r in _role_rows(meta, plan["role"], "read")}
    with_children = truthy(args.get("all_doctypes", True))
    if with_children:
        for field in tables:
            child = frappe.get_meta(field.options)
            columns[field.options] = sorted(set(COMMON_FIELDS) | {f.fieldname for f in child.fields
                if int(f.permlevel or 0) in levels and f.fieldtype != "Password"})
    selected = parse_json(args.get("select_columns"), columns)
    if not isinstance(selected, dict) or set(selected) - columns.keys() or any(
        not isinstance(names, list) or not set(names) <= set(columns[dt]) for dt, names in selected.items()
    ):
        raise Denied("Export columns exceed the role ceiling")
    file_type = args.get("file_type") or "Excel"
    if file_type not in ("Excel", "CSV"):
        raise Denied("Unsupported export format")
    can_export(meta.name, raise_exception=True)

    class ScopedExporter(DataExporter):
        def prepare_args(self):
            super().prepare_args()
            if self.all_doctypes:
                self.child_doctypes = [r for r in self.child_doctypes if r["parentfield"] in allowed
                                      and r["doctype"] in selected]

        def add_data(self):
            # Reuse native output/column serializers, but load each permitted
            # parent document with Frappe's proper parenttype-aware child loading.
            # Native DataExporter queries child tables using parent+parentfield
            # without parenttype and cannot express per-parent owner field rules.
            self.data = frappe.get_list(meta.name, fields=["name"], filters=self.filters,
                                        limit_page_length=None)
            for entry in self.data:
                doc = frappe.get_doc(meta.name, entry["name"])
                doc.check_permission("read")
                doc.apply_fieldlevel_read_permissions()
                values = _filtered_document(doc, plan)
                rows = []
                self.add_data_row(rows, meta.name, None, frappe._dict(values), 0)
                if self.all_doctypes:
                    for child in self.child_doctypes:
                        for index, row in enumerate(values.get(child["parentfield"]) or []):
                            self.add_data_row(rows, child["doctype"], child["parentfield"], frappe._dict(row), index)
                for row in rows:
                    self.writer.writerow(row)

    make_access_log(doctype=meta.name, file_type=file_type, columns=selected, filters=filters)
    exporter = ScopedExporter(doctype=meta.name, parent_doctype=meta.name, all_doctypes=with_children,
                               with_data=True, select_columns=selected, file_type=file_type, filters=filters,
                               export_without_column_meta=True)
    return exporter.build_response()


def _search(plan):
    args = plan["args"]
    meta = _meta(args.get("doctype"))
    # Link searches expose names and the same metadata-defined label/search
    # fields as Frappe, intersected with role field levels. Never dispatch a
    # caller-supplied query or standard_queries hook.
    try:
        _, owner_only = _ceiling(meta, plan, "select")
    except Denied:
        _, owner_only = _ceiling(meta, plan, "read")
    has_read = bool(_role_rows(meta, plan["role"], "read"))
    if has_read:
        general, conditional, read_owner_only = _field_scopes(meta, plan)
        allowed = general | conditional
    else:
        allowed = {"name"} | {f.fieldname for f in meta.fields if int(f.permlevel or 0) == 0
                    and f.fieldtype not in ("Password", "Table", "Table MultiSelect")}
        conditional = set()
        read_owner_only = False
    searchfields = ["name"]
    configured = [meta.title_field] if meta.title_field else []
    configured += [name.strip() for name in (meta.search_fields or "").split(",") if name.strip()]
    for name in configured:
        if name in allowed and name not in searchfields:
            searchfields.append(name)
    requested = args.get("searchfield") or "name"
    if requested not in searchfields:
        raise Denied("Search field is not metadata-defined and permitted")
    filters = checked_filters(args.get("filters"), allowed)
    search_filters = [[name, "like", "%" + str(args.get("txt") or "") + "%"] for name in searchfields]
    if any(f[0] in conditional for f in filters):
        owner_only = True
    owner_only = owner_only or read_owner_only
    for field, value, operator in (("enabled", 1, "="), ("disabled", 1, "!=")):
        if meta.get_field(field) and field in allowed:
            filters.append([field, operator, value])
    if owner_only:
        filters.append(["owner", "=", plan["user"]])
    limit = int(args.get("page_length") or 10)
    if not 1 <= limit <= plan["policy"].get("max_rows", 1000):
        raise Denied("Invalid search pagination")
    start = int(args.get("start") or 0)
    if start < 0:
        raise Denied("Invalid search offset")
    if not owner_only and set(searchfields) & conditional:
        # An optional owner-only title/search field must not hide other readable
        # documents whose name/public fields match. Split disjoint owner sets,
        # then merge the bounded pages; never search/fetch private fields for the
        # other-owner branch. A stable name order makes pagination repeatable.
        values = []
        for own in (True, False):
            branch_fields = searchfields if own else [name for name in searchfields if name not in conditional]
            branch_filters = [condition for condition in search_filters if condition[0] in branch_fields]
            values.extend(frappe.get_list(meta.name, fields=branch_fields,
                filters=filters + [["owner", "=" if own else "!=", plan["user"]]],
                or_filters=branch_filters, order_by="name asc",
                limit_page_length=start + limit, limit_start=0))
        values = sorted(values, key=lambda row: (str(row["name"]).casefold(), str(row["name"])))
        values = values[start:start + limit]
    else:
        values = frappe.get_list(meta.name, fields=searchfields, filters=filters, or_filters=search_filters,
                                 order_by="name asc", limit_page_length=limit, limit_start=start)
    if plan["method"].endswith("search_widget"):
        return [[row.get(field) for field in searchfields] for row in values]
    return [{"value": row["name"], "description": ", ".join(str(row[field]) for field in searchfields[1:] if row.get(field)),
             **({"label": row.get(meta.title_field)} if meta.show_title_field_in_link and meta.title_field in searchfields else {})}
            for row in values]


@frappe.whitelist(methods=["GET", "POST"])
def dispatch():
    plan = getattr(frappe.local, PLAN_KEY, None)
    if not plan or plan["user"] != frappe.session.user or not frappe.flags.read_only:
        _deny()
    try:
        op = plan["operation"]
        if op == "identity":
            result = plan["user"]
        elif op == "read":
            result = _read(plan)
        elif op in ("list", "count"):
            result = _list(plan, count=op == "count")
        elif op in ("value", "single_value"):
            result = _value(plan, single=op == "single_value")
        elif op == "search":
            result = _search(plan)
        elif op in ("report", "report_export"):
            result = _report(plan, export=op == "report_export")
        elif op == "export":
            result = _export(plan)
        elif op == "print":
            result = _print(plan)
        else:
            raise Denied("Unknown operation")
    except (Denied, TypeError, ValueError, KeyError):
        _deny()
    if plan["shape"] == "rest":
        frappe.response["data"] = result
        return None
    return result


@frappe.whitelist(methods=["GET"])
def inspect_boundary():
    """Read-only deployment evidence; never returns keys, users or business data."""
    from frappe.utils.change_log import get_app_last_commit_ref
    frappe.only_for("System Manager")
    role, raw_policy = _settings()
    policy = parse_json(raw_policy)
    return {
        "boundary_version": 1,
        "namespace": __name__.split(".")[0],
        "auth_hook_registered": __name__ + ".enforce_request" in frappe.get_hooks("auth_hooks", []),
        "settings_present": policy is not None,
        "protected_role": role,
        "policy_sha256": fingerprint(policy) if policy else None,
        "source_sha256": fingerprint({p.name: p.read_text() for p in sorted(Path(__file__).parent.glob("*.py"))}),
        "app_revisions": {app: get_app_last_commit_ref(app) for app in frappe.get_installed_apps()},
        "permission_source_sha256": permission_source(),
        "request_hooks": permission_payload()["request_hooks"],
        "connection_configuration": {
            "read_from_replica": bool(frappe.conf.read_from_replica),
            "different_credentials_for_replica": bool(frappe.conf.different_credentials_for_replica),
            "primary_connection_present": getattr(frappe.local, "primary_db", None) is not None,
            "replica_connection_present": getattr(frappe.local, "replica_db", None) is not None,
        },
    }


@frappe.whitelist(methods=["GET"])
def inspect_review_sources(report_names=None, print_formats=None, include_sources=False):
    """Review only, never approve. Source responses must be saved privately."""
    frappe.only_for("System Manager")
    reports = parse_json(report_names, [])
    formats = parse_json(print_formats, [])
    if not isinstance(reports, list) or not isinstance(formats, list) or len(reports) + len(formats) > 500:
        raise Denied("Invalid review request")
    sources = {}
    for name in formats:
        if name.endswith("::Standard"):
            sources[name] = print_source("Standard", name.removesuffix("::Standard"))
        else:
            sources[name] = print_source(name, frappe.get_cached_value("Print Format", name, "doc_type"))
    result = {"reports": {name: report_source(name) for name in reports}, "print_formats": sources}
    if truthy(include_sources):
        permissions = permission_payload()
        result["source_payloads"] = {"reports": {name: report_payload(name) for name in reports},
            "print_formats": {name: print_payload("Standard", name.removesuffix("::Standard"))
                if name.endswith("::Standard") else print_payload(name, frappe.get_cached_value("Print Format", name, "doc_type"))
                for name in formats},
            "permissions": permissions, "hook_files": _hook_files(permissions)}
    return result
