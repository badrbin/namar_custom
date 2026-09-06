"""Pure policy validation and route parsing; no Frappe state or network access."""
from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import urlsplit

DEFAULT_ROLE = "الذكاء الاصطناعي"
SETTINGS = "AI Read Only Settings"
METHODS = {
    "frappe.auth.get_logged_user": "identity",
    "frappe.client.get": "read",
    "frappe.client.get_list": "list",
    "frappe.client.get_value": "value",
    "frappe.client.get_single_value": "single_value",
    "frappe.client.get_count": "count",
    "frappe.desk.search.search_link": "search",
    "frappe.desk.search.search_widget": "search",
    "frappe.desk.query_report.run": "report",
    "frappe.desk.query_report.export_query": "report_export",
    "frappe.core.doctype.data_export.exporter.export_data": "export",
    "frappe.utils.print_format.download_pdf": "print",
}
OPERATIONS = frozenset(METHODS.values())
COMMON_FIELDS = frozenset({"name", "owner", "creation", "modified", "modified_by", "docstatus", "idx"})
FIELD_RE = re.compile(r"^[^\W\d]\w*$", re.UNICODE)


class Denied(ValueError):
    pass


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), default=str).encode()).hexdigest()


def parse_json(value, default=None):
    if value in (None, ""):
        return default
    return json.loads(value) if isinstance(value, str) else value


def truthy(value):
    return value not in (None, False, 0, "", "0", "false", "False")


def validate_policy(value):
    if not isinstance(value, dict) or value.get("version") != 1:
        raise Denied("Policy version 1 is required")
    if set(value) - {"version", "methods", "reports", "print_formats", "print_resources", "max_rows", "app_revisions", "permission_review"}:
        raise Denied("Unknown policy keys")
    methods = value.get("methods", [])
    if not isinstance(methods, list) or not methods or len(set(methods)) != len(methods):
        raise Denied("Explicit, unique methods are required")
    if set(methods) - METHODS.keys():
        raise Denied("Only reviewed built-in read operations can be enabled")
    for key in ("reports", "print_formats"):
        records = value.get(key, {})
        if not isinstance(records, dict):
            raise Denied("Expected a fingerprint mapping")
        for name, record in records.items():
            if not isinstance(name, str) or not name or not isinstance(record, dict):
                raise Denied("Invalid reviewed source record")
            if set(record) != {"sha256", "reviewed_no_business_mutations", "reviewed_read_scope"}:
                raise Denied("A source hash and explicit business-mutation review are required")
            if not re.fullmatch(r"[a-f0-9]{64}", str(record["sha256"])):
                raise Denied("Invalid SHA256")
            if record["reviewed_no_business_mutations"] is not True:
                raise Denied("Code must be reviewed before it can run")
            if record["reviewed_read_scope"] is not True:
                raise Denied("Report and print output scope must be reviewed")
    revisions = value.get("app_revisions", {})
    if not isinstance(revisions, dict) or any(not isinstance(k, str) or not re.fullmatch(r"[a-f0-9]{7,40}", str(v)) for k, v in revisions.items()):
        raise Denied("Invalid application revision map")
    if not revisions:
        raise Denied("All read operations require reviewed application revisions")
    resources = value.get("print_resources", {})
    if not isinstance(resources, dict):
        raise Denied("Print resource origins must be a mapping")
    for origin, prefixes in resources.items():
        parsed = urlsplit(origin)
        if parsed.scheme != "https" or not parsed.netloc or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise Denied("Print resources require explicit HTTPS origins")
        if not isinstance(prefixes, list) or not prefixes or any(not isinstance(p, str) or not p.startswith("/")
            or any(part in (".", "..") for part in p.split("/")) or any(c in p for c in ("%", "?", "#", "\\")) for p in prefixes):
            raise Denied("Print resources require explicit path prefixes")
    review = value.get("permission_review", {})
    if not isinstance(review, dict) or set(review) != {"sha256", "reviewed_no_business_mutations"} or not re.fullmatch(r"[a-f0-9]{64}", str(review.get("sha256"))) or review.get("reviewed_no_business_mutations") is not True:
        raise Denied("Permission hooks and scripts require a source review")
    maximum = value.get("max_rows", 1000)
    if isinstance(maximum, bool) or not isinstance(maximum, int) or not 1 <= maximum <= 10000:
        raise Denied("max_rows must be between 1 and 10000")
    return value


def route(path, http_method, params, enabled_methods):
    """Resolve exactly the route Frappe will execute, including legacy cmd precedence."""
    if http_method not in ("GET", "POST"):
        raise Denied("Mutation HTTP method")
    if not isinstance(path, str) or "\x00" in path or "\\" in path or "//" in path:
        raise Denied("Noncanonical request path")
    args = dict(params)
    command = args.pop("cmd", None)
    shape = "rpc"
    if not command:
        for prefix in ("/api/method/", "/api/v1/method/", "/api/v2/method/"):
            if path.startswith(prefix):
                command = path[len(prefix):]
                break
    if command:
        if command not in enabled_methods or command not in METHODS:
            raise Denied("RPC method is not enabled")
        return {"operation": METHODS[command], "method": command, "args": args, "shape": shape}
    if http_method != "GET":
        raise Denied("REST mutations are prohibited")
    for prefix in ("/api/resource/", "/api/v1/resource/", "/api/v2/document/"):
        if path.startswith(prefix):
            parts = path[len(prefix):].split("/", 1)
            if not parts[0] or (len(parts) == 2 and not parts[1]):
                raise Denied("Invalid document path")
            args["doctype"] = parts[0]
            op = "read" if len(parts) == 2 else "list"
            method = "frappe.client.get" if op == "read" else "frappe.client.get_list"
            if method not in enabled_methods:
                raise Denied("Read operation is not enabled")
            if len(parts) == 2:
                args["name"] = parts[1]
            return {"operation": op, "method": method, "args": args, "shape": "rest"}
    raise Denied("Request route is outside the read API")


def validate_request(plan, user):
    args = plan["args"]
    for key in ("run_method", "method", "query", "doc", "docs", "data", "flags", "parent",
                "parent_doctype", "expand", "expand_links", "custom_columns", "group_by", "reference_doctype"):
        if key in args and args[key] not in (None, "", False, [], {}):
            raise Denied("Unsupported dispatch or document parameter: " + key)
    for key in ("ignore_permissions", "ignore_user_permissions", "export_in_background", "as_list"):
        if truthy(args.get(key)):
            raise Denied("Permission bypass or background work is prohibited")
    if args.get("user") not in (None, "", user):
        raise Denied("A caller cannot select another user")
    if plan["operation"] in ("report", "report_export"):
        args["ignore_prepared_report"] = 1
        args["export_in_background"] = 0
        filters = parse_json(args.get("filters"), {})
        if not isinstance(filters, dict) or filters.get("prepared_report_name"):
            raise Denied("Prepared report selection is prohibited")
    return plan


def field_names(value):
    try:
        names = parse_json(value, ["name"])
    except ValueError:
        names = value
    if isinstance(names, str):
        names = [names]
    if not isinstance(names, list) or not names or any(not isinstance(n, str) for n in names):
        raise Denied("Expected explicit field names")
    if names == ["*"]:
        return names
    if any(not FIELD_RE.fullmatch(n) for n in names):
        raise Denied("Expressions, joins and aliases are outside the read boundary")
    return names


def checked_filters(value, allowed_fields):
    filters = parse_json(value, [])
    if isinstance(filters, dict):
        filters = [[key, "=", val] if not isinstance(val, list) else [key, *val]
                   for key, val in filters.items()]
    if not isinstance(filters, list):
        raise Denied("Invalid filters")
    result = []
    for part in filters:
        if not isinstance(part, (list, tuple)) or len(part) != 3:
            raise Denied("Cross-document filters are prohibited")
        name, operator, value = part
        operator = str(operator).lower()
        if name not in allowed_fields or operator not in ("=", "!=", ">", "<", ">=", "<=", "like", "not like", "in", "not in", "between", "is", "timespan"):
            raise Denied("Filter is outside the allowed fields or operators")
        result.append([name, operator, value])
    return result
