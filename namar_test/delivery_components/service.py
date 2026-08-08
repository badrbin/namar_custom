from __future__ import annotations

import hashlib
import json
from typing import Any

import frappe
from frappe.utils import cint, flt, now_datetime

from namar_test.delivery_components.package_logic import (
    TRACKING_ACTION_DELIVER,
    TRACKING_ACTION_LOAD,
    TRACKING_ACTION_READY,
    TRACKING_ACTION_REOPEN,
    TRACKING_STATUS_DELIVERED,
    TRACKING_STATUS_LOADED,
    TRACKING_STATUS_PENDING,
    TRACKING_STATUS_READY,
    assign_stable_loading_codes,
    build_fulfillment_readiness,
    build_reconciled_package_specs,
    clean_count,
    component_color_from_item_code,
    component_package_key,
    legacy_color_split_has_started_rows,
    legacy_component_package_key,
    next_tracking_status,
    normalize_component_color,
    normalize_tracking_status,
    package_status,
    should_rotate_unregistered_barcodes,
)
from namar_test.delivery_components.tracking_code_logic import (
    is_valid_request_tracking_code,
    normalize_tracking_code,
)
from namar_test.delivery_components.tracking_codes import ensure_material_request_tracking_code


PACKAGE_DOCTYPE = "Material Request Delivery Component Package"
PACKAGE_PARENTFIELD = "custom_delivery_component_packages"
RULE_DOCTYPE = "Delivery Component Packaging Rule"
EVENT_DOCTYPE = "Material Request Component Package Event"
MR_LOADING_CODE_FIELD = "custom_delivery_loading_code"
PACKAGE_LOADING_CODE_FIELD = "loading_code"
PACKAGE_COLOR_FIELD = "color"
MR_SOURCE_HASH_FIELD = "custom_delivery_component_source_hash"
REALTIME_EVENT = "delivery_component_package_changed"

TRACKING_ROUTE_FULL = "تصنيع وتغليف"
TRACKING_ROUTE_READY_ONLY = "تجهيز فقط"
TRACKING_ROUTE_NONE = "لا يتتبع"

PACKAGE_OPTIONAL_FIELDS = (
    PACKAGE_COLOR_FIELD,
    "packaging_rule",
    "tracking_route",
    "pack_size_snapshot",
    "tracking_status",
    "tracking_revision",
    "active",
    "loaded_at",
    "loaded_by",
    "delivered_at",
    "delivered_by",
)


def is_legacy_package_key(value: str | None) -> bool:
    return (value or "").count("||") == 2


def normalize_material_request(value: str | None) -> str:
    material_request = (value or "").strip()
    if material_request and not material_request.startswith("MREQ-"):
        material_request = "MREQ-" + material_request
    return material_request


def ensure_material_request_access(material_request: str, *, allow_guest: bool = False) -> Any:
    mr_doc = frappe.get_doc("Material Request", material_request)
    if allow_guest and frappe.session.user == "Guest":
        return mr_doc
    if not frappe.has_permission("Material Request", ptype="read", doc=mr_doc):
        frappe.throw("لا تملك صلاحية الوصول إلى طلب المواد", frappe.PermissionError)
    return mr_doc


def doctype_fields(doctype: str) -> set[str]:
    if not frappe.db.exists("DocType", doctype):
        return set()
    return {field.fieldname for field in frappe.get_meta(doctype).fields if field.fieldname}


def selected_fields(doctype: str, required: list[str], optional: tuple[str, ...] | list[str]) -> list[str]:
    available = doctype_fields(doctype)
    return required + [fieldname for fieldname in optional if fieldname in available]


def tracking_route(value: str | None) -> str:
    route = (value or TRACKING_ROUTE_FULL).strip()
    if route not in (TRACKING_ROUTE_FULL, TRACKING_ROUTE_READY_ONLY, TRACKING_ROUTE_NONE):
        return TRACKING_ROUTE_FULL
    return route


def source_value(value: str | None) -> str:
    source = (value or "QR").strip()
    return source if source in ("QR", "يدوي", "تزامن", "ترحيل") else "QR"


def parse_store_data(item_row: Any) -> list[dict[str, Any]]:
    raw_value = item_row.get("custom_store_data") or ""
    if not raw_value and item_row.get("name"):
        raw_value = frappe.db.get_value("Material Request Item", item_row.get("name"), "custom_store_data") or ""
    if not raw_value:
        return []
    try:
        parsed = json.loads(raw_value) or []
    except Exception:
        try:
            parsed = frappe.parse_json(raw_value) or []
        except Exception:
            return []
    if isinstance(parsed, dict):
        parsed = parsed.get("stores") or parsed.get("data") or []
    try:
        return list(parsed)
    except Exception:
        return []


def load_rules() -> list[dict[str, Any]]:
    if not frappe.db.exists("DocType", RULE_DOCTYPE):
        return []
    fields = [
        "name",
        "rule_name",
        "match_mode",
        "component",
        "component_contains",
        "full_pack_qty",
        "full_pack_label",
        "remainder_pack_label",
        "remainder_multi_pack_label",
        "required_for_delivery",
        "sort_order",
    ]
    rule_meta = frappe.get_meta(RULE_DOCTYPE)
    for optional_field in ("exclude_from_delivery", "tracking_route"):
        if rule_meta.has_field(optional_field):
            fields.append(optional_field)
    return frappe.get_all(
        RULE_DOCTYPE,
        filters={"enabled": 1},
        fields=fields,
        order_by="sort_order asc, name asc",
        limit_page_length=0,
    )


def rule_priority(rule: dict[str, Any]) -> int:
    mode = rule.get("match_mode") or ""
    if mode == "مطابقة مكون":
        return 1
    if mode == "يحتوي اسم المكون":
        return 2
    return 3


def find_rule(component: str | None, component_label: str | None, rules: list[dict[str, Any]]) -> dict[str, Any] | None:
    component = (component or "").strip()
    component_label = (component_label or "").strip()
    sorted_rules = sorted(
        rules,
        key=lambda rule: (rule_priority(rule), cint(rule.get("sort_order") or 0), rule.get("name") or ""),
    )
    for rule in sorted_rules:
        mode = rule.get("match_mode") or ""
        if mode == "افتراضي":
            continue
        if mode == "مطابقة مكون" and rule.get("component") and rule.get("component") in (component, component_label):
            return rule
        if mode == "يحتوي اسم المكون":
            needle = (rule.get("component_contains") or "").strip()
            if needle and (needle in component or needle in component_label):
                return rule
    return None


def load_component_sort_order() -> dict[str, int]:
    if not frappe.db.exists("DocType", "Store Component"):
        return {}

    meta = frappe.get_meta("Store Component")
    fields = ["name"]
    for fieldname in ("component_name", "label_ar", "custom_print_sort_order"):
        if meta.has_field(fieldname):
            fields.append(fieldname)

    sort_map: dict[str, int] = {}
    for row in frappe.get_all("Store Component", fields=fields, limit_page_length=0):
        sort_value = cint(row.get("custom_print_sort_order") or 0)
        if sort_value <= 0:
            continue
        for key in (row.get("name"), row.get("component_name"), row.get("label_ar")):
            text = (key or "").strip()
            if text and text not in sort_map:
                sort_map[text] = sort_value
    return sort_map


def load_component_color_flags() -> dict[str, int]:
    if not frappe.db.exists("DocType", "Store Component"):
        return {}

    meta = frappe.get_meta("Store Component")
    if not meta.has_field("custom_has_color"):
        return {}
    fields = ["name", "custom_has_color"]
    for fieldname in ("component_name", "label_ar"):
        if meta.has_field(fieldname):
            fields.append(fieldname)

    color_flags: dict[str, int] = {}
    for row in frappe.get_all("Store Component", fields=fields, limit_page_length=0):
        uses_color = 1 if cint(row.get("custom_has_color") or 0) else 0
        for key in (row.get("name"), row.get("component_name"), row.get("label_ar")):
            text = (key or "").strip()
            if text:
                color_flags[text] = uses_color
    return color_flags


def component_sort_key(component_row: dict[str, Any], sort_map: dict[str, int]) -> tuple[int, str, str, str]:
    component = (component_row.get("component") or "").strip()
    component_label = (component_row.get("component_label") or "").strip()
    color = (component_row.get(PACKAGE_COLOR_FIELD) or "").strip()
    item_code = (component_row.get("item_code") or "").strip()
    sort_value = sort_map.get(component) or sort_map.get(component_label) or 999999
    return (sort_value, component_label or component, color, item_code)


def missing_rule_label(component_row: dict[str, Any]) -> str:
    label = (component_row.get("component_label") or component_row.get("component") or "").strip()
    item_code = (component_row.get("item_code") or "").strip()
    if item_code:
        return "%s (%s)" % (label, item_code)
    return label


def throw_missing_delivery_rules(component_rows: list[dict[str, Any]]) -> None:
    labels: list[str] = []
    seen: dict[str, int] = {}
    for row in component_rows:
        label = missing_rule_label(row)
        if label and label not in seen:
            seen[label] = 1
            labels.append(label)
    frappe.throw(
        "لا توجد قاعدة توريد لهذه المكونات: %s. أضف قاعدة تغليف مكونات التوريد لكل مكون ثم أعد تحديث الحزم."
        % ", ".join(labels)
    )


def get_existing_packages(material_request: str) -> dict[str, dict[str, Any]]:
    fields = selected_fields(PACKAGE_DOCTYPE, [
        "name",
        "package_key",
        "component",
        "item_code",
        "package_no",
        "package_qty",
        "package_label",
        "ready_qty",
        "ready_at",
        "ready_by",
        "source",
        "status",
        "barcode_key",
    ], (PACKAGE_LOADING_CODE_FIELD,) + PACKAGE_OPTIONAL_FIELDS)
    filters: dict[str, Any] = {
        "parent": material_request,
        "parentfield": PACKAGE_PARENTFIELD,
    }
    if frappe.get_meta(PACKAGE_DOCTYPE).has_field("active"):
        filters["active"] = 1
    rows = frappe.get_all(
        PACKAGE_DOCTYPE,
        filters=filters,
        fields=fields,
        limit_page_length=0,
    )
    return {row.get("package_key"): row for row in rows if row.get("package_key")}


def get_packages_for_summary(material_request: str) -> list[dict[str, Any]]:
    fields = selected_fields(
        PACKAGE_DOCTYPE,
        ["name", "package_qty", "ready_qty", "required_for_delivery", "status"],
        ("tracking_route", "tracking_status", "active"),
    )
    filters: dict[str, Any] = {
        "parent": material_request,
        "parentfield": PACKAGE_PARENTFIELD,
    }
    if frappe.get_meta(PACKAGE_DOCTYPE).has_field("active"):
        filters["active"] = 1
    return frappe.get_all(
        PACKAGE_DOCTYPE,
        filters=filters,
        fields=fields,
        limit_page_length=0,
    )


def missing_color_label(row: dict[str, Any]) -> str:
    component = (row.get("component_label") or row.get("component") or "مكون").strip()
    row_idx = cint(row.get("row_idx") or 0)
    item_code = (row.get("source_item_code") or "").strip()
    details = []
    if row_idx:
        details.append("السطر %s" % row_idx)
    if item_code:
        details.append(item_code)
    return "%s (%s)" % (component, "، ".join(details)) if details else component


def throw_missing_component_colors(rows: list[dict[str, Any]]) -> None:
    labels: list[str] = []
    seen: set[str] = set()
    for row in rows:
        label = missing_color_label(row)
        if label and label not in seen:
            seen.add(label)
            labels.append(label)
    frappe.throw(
        "تعذر تحديث وطباعة حزم المكونات لأن اللون غير محدد للمكونات التي تعتمد على اللون:<br>"
        + "<br>".join("- " + label for label in labels)
        + "<br>حدد لون الصنف في سطر طلب المواد ثم أعد الطباعة."
    )


def aggregate_components(mr_doc: Any, *, validate_colors: bool = False) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    missing_colors: list[dict[str, Any]] = []
    color_flags = load_component_color_flags()
    for item_row in mr_doc.items:
        row_qty = flt(item_row.qty or 0)
        if row_qty <= 0:
            row_qty = 1
        stores = parse_store_data(item_row)
        for store in stores:
            component = (store.get("component") or store.get("component_name") or "").strip()
            component_label = (store.get("component_ar") or component).strip()
            item_code = (store.get("item") or store.get("item_code") or "").strip()
            item_name = (store.get("item_name") or item_code).strip()
            per_row_qty = flt(store.get("qty") or 0)
            if not component or per_row_qty <= 0:
                continue
            uses_color = 1 if cint(color_flags.get(component) or color_flags.get(component_label) or 0) else 0
            explicit_color = normalize_component_color(
                store.get("color")
                or store.get("component_color")
                or store.get("custom_color")
                or store.get("لون")
            )
            color = explicit_color or (component_color_from_item_code(item_row.item_code) if uses_color else "")
            if uses_color and not color:
                missing_colors.append(
                    {
                        "component": component,
                        "component_label": component_label,
                        "row_idx": item_row.idx,
                        "source_item_code": item_row.item_code,
                    }
                )
            key = component + "||" + color + "||" + item_code
            if key not in grouped:
                grouped[key] = {
                    "component": component,
                    "component_label": component_label,
                    PACKAGE_COLOR_FIELD: color,
                    "uses_color": uses_color,
                    "item_code": item_code,
                    "item_name": item_name,
                    "required_qty": 0,
                }
                order.append(key)
            grouped[key]["required_qty"] = flt(grouped[key].get("required_qty") or 0) + (per_row_qty * row_qty)
    if validate_colors and missing_colors:
        throw_missing_component_colors(missing_colors)
    return [grouped[key] for key in order]


def build_source_hash(
    mr_doc: Any,
    rules: list[dict[str, Any]] | None = None,
    component_rows: list[dict[str, Any]] | None = None,
) -> str:
    rules = rules if rules is not None else load_rules()
    payload: list[dict[str, Any]] = []
    component_rows = sorted(
        component_rows if component_rows is not None else aggregate_components(mr_doc),
        key=lambda row: (
            row.get("component") or "",
            row.get(PACKAGE_COLOR_FIELD) or "",
            row.get("item_code") or "",
        ),
    )
    for row in component_rows:
        rule = find_rule(row.get("component"), row.get("component_label"), rules) or {}
        payload.append(
            {
                "component": row.get("component") or "",
                "component_label": row.get("component_label") or "",
                PACKAGE_COLOR_FIELD: row.get(PACKAGE_COLOR_FIELD) or "",
                "item_code": row.get("item_code") or "",
                "required_qty": clean_count(row.get("required_qty") or 0),
                "rule": rule.get("name") or "",
                "full_pack_qty": clean_count(rule.get("full_pack_qty") or 0),
                "full_pack_label": rule.get("full_pack_label") or "",
                "remainder_pack_label": rule.get("remainder_pack_label") or "",
                "remainder_multi_pack_label": rule.get("remainder_multi_pack_label") or "",
                "required_for_delivery": cint(rule.get("required_for_delivery", 1)),
                "exclude_from_delivery": cint(rule.get("exclude_from_delivery") or 0),
                "tracking_route": tracking_route(rule.get("tracking_route")),
            }
        )
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def packages_need_sync(mr_doc: Any, packages: list[dict[str, Any]] | None = None) -> bool:
    mr_meta = frappe.get_meta("Material Request")
    current_hash = build_source_hash(mr_doc)
    saved_hash = (mr_doc.get(MR_SOURCE_HASH_FIELD) or "").strip() if mr_meta.has_field(MR_SOURCE_HASH_FIELD) else ""
    if saved_hash:
        return saved_hash != current_hash
    package_rows = packages if packages is not None else get_packages_for_summary(mr_doc.name)
    return bool(aggregate_components(mr_doc) or package_rows)


def get_or_make_loading_prefix(mr_doc: Any, dry_run: bool) -> str:
    mr_meta = frappe.get_meta("Material Request")
    if not mr_meta.has_field(MR_LOADING_CODE_FIELD):
        return ""

    existing_prefix = normalize_tracking_code(mr_doc.get(MR_LOADING_CODE_FIELD))
    if is_valid_request_tracking_code(existing_prefix):
        return existing_prefix
    if dry_run:
        return ""

    prefix = ensure_material_request_tracking_code(mr_doc)
    frappe.db.set_value("Material Request", mr_doc.name, MR_LOADING_CODE_FIELD, prefix, update_modified=False)
    return prefix


def assign_loading_codes(package_rows: list[dict[str, Any]], loading_prefix: str) -> list[dict[str, Any]]:
    if not loading_prefix or not frappe.get_meta(PACKAGE_DOCTYPE).has_field(PACKAGE_LOADING_CODE_FIELD):
        return package_rows
    return assign_stable_loading_codes(package_rows, loading_prefix, PACKAGE_LOADING_CODE_FIELD)


def build_package_rows(
    mr_doc: Any,
    component_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    rules = load_rules()
    component_rows = component_rows if component_rows is not None else aggregate_components(mr_doc, validate_colors=True)
    sort_map = load_component_sort_order()
    component_rows = sorted(component_rows, key=lambda row: component_sort_key(row, sort_map))
    existing = get_existing_packages(mr_doc.name)
    package_fields = doctype_fields(PACKAGE_DOCTYPE)
    has_loading_code_field = PACKAGE_LOADING_CODE_FIELD in package_fields
    package_rows: list[dict[str, Any]] = []
    missing_rule_rows: list[dict[str, Any]] = []

    colors_by_legacy_base: dict[str, set[str]] = {}
    for row in component_rows:
        legacy_base = legacy_component_package_key(
            row.get("component"), row.get("item_code"), 1
        ).rsplit("||", 1)[0]
        colors_by_legacy_base.setdefault(legacy_base, set()).add(
            (row.get(PACKAGE_COLOR_FIELD) or "").strip()
        )

    ambiguous_started: list[str] = []
    for legacy_base, colors in colors_by_legacy_base.items():
        if len(colors) <= 1:
            continue
        legacy_rows = [
            row
            for key, row in existing.items()
            if is_legacy_package_key(key) and (key or "").rsplit("||", 1)[0] == legacy_base
        ]
        if legacy_color_split_has_started_rows(colors, legacy_rows):
            for row in legacy_rows:
                if not package_started(row):
                    continue
                ambiguous_started.append(
                    "%s: الحزمة %s مسجلة سابقًا وأصبحت موزعة على أكثر من لون"
                    % (
                        row.get("component") or legacy_base.split("||", 1)[0] or "المكون",
                        row.get("package_key") or row.get("name"),
                    )
                )
    if ambiguous_started:
        frappe.throw(
            "تعذر تحديث حزم المكونات حفاظًا على سجل المسح:<br>"
            + "<br>".join("- " + row for row in ambiguous_started)
            + "<br>راجع ألوان الحزم القديمة قبل إعادة المزامنة."
        )

    for component_row in component_rows:
        required_qty = flt(component_row.get("required_qty") or 0)
        if required_qty <= 0:
            continue
        rule = find_rule(component_row.get("component"), component_row.get("component_label"), rules)
        if not rule:
            missing_rule_rows.append(component_row)
            continue
        rule_tracking_route = tracking_route(rule.get("tracking_route"))
        if cint(rule.get("exclude_from_delivery") or 0) or rule_tracking_route == TRACKING_ROUTE_NONE:
            continue

        component = component_row.get("component") or ""
        color = component_row.get(PACKAGE_COLOR_FIELD) or ""
        item_code = component_row.get("item_code") or ""
        package_base = component_package_key(component, color, item_code, 1).rsplit("||", 1)[0]
        legacy_base = legacy_component_package_key(component, item_code, 1).rsplit("||", 1)[0]
        exact_group = [
            row
            for key, row in existing.items()
            if not is_legacy_package_key(key) and (key or "").rsplit("||", 1)[0] == package_base
        ]
        can_reuse_legacy = len(colors_by_legacy_base.get(legacy_base) or set()) <= 1
        legacy_group = [
            row
            for key, row in existing.items()
            if can_reuse_legacy
            and is_legacy_package_key(key)
            and (key or "").rsplit("||", 1)[0] == legacy_base
        ]
        existing_group = exact_group + legacy_group
        try:
            package_specs = build_reconciled_package_specs(
                required_qty=required_qty,
                full_pack_qty=rule.get("full_pack_qty") or 0,
                full_label=(rule.get("full_pack_label") or "حزمة").strip(),
                remainder_one_label=(rule.get("remainder_pack_label") or "مغلف منفرد").strip(),
                remainder_multi_label=(rule.get("remainder_multi_pack_label") or "كرتون ناقص").strip(),
                existing_rows=existing_group,
            )
        except ValueError as exc:
            frappe.throw("تعذر تحديث حزم %s: %s" % (component_row.get("component") or "المكون", exc))
        required_for_delivery = 1 if cint(rule.get("required_for_delivery", 1)) else 0

        package_count = len(package_specs)
        for package_spec in package_specs:
            index = cint(package_spec.get("package_no") or 0)
            package_key = component_package_key(component, color, item_code, index)
            source_package_key = package_spec.get("package_key") if package_spec.get("legacy_started") else ""
            existing_row = existing.get(source_package_key) or existing.get(package_key) or {}
            if not existing_row and can_reuse_legacy:
                existing_row = existing.get(legacy_component_package_key(component, item_code, index)) or {}
            package_qty = flt(package_spec.get("package_qty") or 0)
            ready_qty = min(flt(existing_row.get("ready_qty") or 0), package_qty)
            remaining = max(package_qty - ready_qty, 0)
            package_tracking_status = normalize_tracking_status(
                existing_row.get("tracking_status"), package_qty, ready_qty
            )
            package_row = {
                "package_key": package_key,
                "component": component,
                "component_label": component_row.get("component_label") or component_row.get("component") or "",
                PACKAGE_COLOR_FIELD: color,
                "item_code": item_code,
                "item_name": component_row.get("item_name") or component_row.get("item_code") or "",
                "package_no": index,
                "package_count": package_count,
                "package_label": package_spec.get("package_label") or "حزمة",
                "package_qty": clean_count(package_qty),
                "ready_qty": clean_count(ready_qty),
                "remaining_qty": clean_count(remaining),
                "status": package_status(package_qty, ready_qty),
                "required_for_delivery": required_for_delivery,
                "ready_at": existing_row.get("ready_at"),
                "ready_by": existing_row.get("ready_by"),
                "source": existing_row.get("source") or "",
                "barcode_key": existing_row.get("barcode_key") or "",
            }
            if existing_row.get("name"):
                package_row["_existing_name"] = existing_row.get("name")
            optional_values = {
                "packaging_rule": rule.get("name") or "",
                "tracking_route": rule_tracking_route,
                "pack_size_snapshot": clean_count(
                    existing_row.get("pack_size_snapshot")
                    or (package_qty if package_spec.get("legacy_started") else rule.get("full_pack_qty") or 0)
                ),
                "tracking_status": package_tracking_status,
                "tracking_revision": cint(existing_row.get("tracking_revision") or 0),
                "active": 1,
                "loaded_at": existing_row.get("loaded_at"),
                "loaded_by": existing_row.get("loaded_by"),
                "delivered_at": existing_row.get("delivered_at"),
                "delivered_by": existing_row.get("delivered_by"),
            }
            for fieldname, value in optional_values.items():
                if fieldname in package_fields:
                    package_row[fieldname] = value
            if has_loading_code_field:
                package_row[PACKAGE_LOADING_CODE_FIELD] = existing_row.get(PACKAGE_LOADING_CODE_FIELD) or ""
            package_rows.append(package_row)

    if missing_rule_rows:
        throw_missing_delivery_rules(missing_rule_rows)
    return package_rows


def summarize_packages(package_rows: list[dict[str, Any]]) -> dict[str, Any]:
    total_required = 0
    ready_required = 0
    total_packages = 0
    remaining_packages = 0
    ready_packages = 0
    loaded_packages = 0
    delivered_packages = 0
    full_route_packages = 0
    for row in package_rows:
        active_value = row.get("active")
        if active_value not in (None, "") and not cint(active_value):
            continue
        if not cint(row.get("required_for_delivery")):
            continue
        route = tracking_route(row.get("tracking_route"))
        if route == TRACKING_ROUTE_NONE:
            continue
        total_packages += 1
        package_qty = flt(row.get("package_qty") or 0)
        ready_qty = flt(row.get("ready_qty") or 0)
        package_tracking_status = normalize_tracking_status(row.get("tracking_status"), package_qty, ready_qty)
        total_required += package_qty
        ready_required += ready_qty
        if package_tracking_status in (TRACKING_STATUS_READY, TRACKING_STATUS_LOADED, TRACKING_STATUS_DELIVERED):
            ready_packages += 1
        else:
            remaining_packages += 1
        if route == TRACKING_ROUTE_READY_ONLY:
            continue
        full_route_packages += 1
        if package_tracking_status in (TRACKING_STATUS_LOADED, TRACKING_STATUS_DELIVERED):
            loaded_packages += 1
        if package_tracking_status == TRACKING_STATUS_DELIVERED:
            delivered_packages += 1

    if total_packages <= 0:
        status = "لا توجد مكونات"
    elif remaining_packages <= 0:
        status = "مكتمل"
    elif ready_required > 0:
        status = "جزئي"
    else:
        status = "غير جاهز"

    summary = "حزم %s/%s | كمية %s/%s" % (
        clean_count(ready_packages),
        clean_count(total_packages),
        clean_count(ready_required),
        clean_count(total_required),
    )
    return {
        "status": status,
        "total_packages": clean_count(total_packages),
        "remaining_packages": clean_count(remaining_packages),
        "ready_packages": clean_count(ready_packages),
        "loaded_packages": clean_count(loaded_packages),
        "delivered_packages": clean_count(delivered_packages),
        "full_route_packages": clean_count(full_route_packages),
        "total_required_qty": clean_count(total_required),
        "ready_required_qty": clean_count(ready_required),
        "summary": summary,
    }


def door_manufacturing_counts(mr_doc: Any) -> tuple[float, float]:
    mr_meta = frappe.get_meta("Material Request")
    total = flt(mr_doc.get("custom_manufacturing_total_count") or 0) if mr_meta.has_field("custom_manufacturing_total_count") else 0
    remaining = flt(mr_doc.get("custom_manufacturing_remaining_count") or 0) if mr_meta.has_field("custom_manufacturing_remaining_count") else 0
    return total, remaining


def fulfillment_readiness(
    mr_doc: Any,
    package_summary: dict[str, Any],
    needs_sync: bool,
) -> dict[str, Any]:
    door_total, door_remaining = door_manufacturing_counts(mr_doc)
    return build_fulfillment_readiness(
        door_total=door_total,
        door_remaining=door_remaining,
        package_total=package_summary.get("total_packages"),
        package_ready=package_summary.get("ready_packages"),
        package_loaded=package_summary.get("loaded_packages"),
        package_delivered=package_summary.get("delivered_packages"),
        package_load_total=package_summary.get("full_route_packages"),
        packages_need_sync=needs_sync,
    )


def update_material_request_summary(
    material_request: str,
    package_rows: list[dict[str, Any]] | None = None,
    *,
    source_hash: str | None = None,
) -> dict[str, Any]:
    if package_rows is None:
        package_rows = get_packages_for_summary(material_request)
    summary = summarize_packages(package_rows)
    mr_doc = frappe.get_doc("Material Request", material_request)
    needs_sync = packages_need_sync(mr_doc, package_rows)
    if source_hash is not None:
        needs_sync = source_hash != build_source_hash(mr_doc)
    overall = fulfillment_readiness(mr_doc, summary, needs_sync)
    mr_meta = frappe.get_meta("Material Request")
    field_map = {
        "custom_delivery_component_status": summary.get("status"),
        "custom_delivery_component_total_packages": summary.get("total_packages"),
        "custom_delivery_component_remaining_packages": summary.get("remaining_packages"),
        "custom_delivery_component_summary": summary.get("summary"),
        "custom_delivery_component_loaded_packages": summary.get("loaded_packages"),
        "custom_delivery_component_delivered_packages": summary.get("delivered_packages"),
        "custom_fulfillment_readiness_status": overall.get("status"),
        "custom_fulfillment_readiness_summary": overall.get("summary"),
        "custom_fulfillment_ready": 1 if overall.get("is_ready") else 0,
    }
    if source_hash is not None:
        field_map[MR_SOURCE_HASH_FIELD] = source_hash
        field_map["custom_delivery_component_synced_at"] = now_datetime()
    candidate_values = {
        fieldname: value
        for fieldname, value in field_map.items()
        if mr_meta.has_field(fieldname)
    }
    update_values = {
        fieldname: value
        for fieldname, value in candidate_values.items()
        if str(mr_doc.get(fieldname) or "") != str(value or "")
    }
    if update_values:
        frappe.db.set_value("Material Request", material_request, update_values, update_modified=False)
    return {**summary, "overall": overall, "needs_sync": 1 if needs_sync else 0}


def package_started(row: dict[str, Any]) -> bool:
    package_qty = flt(row.get("package_qty") or 0)
    ready_qty = flt(row.get("ready_qty") or 0)
    status = normalize_tracking_status(row.get("tracking_status"), package_qty, ready_qty)
    return ready_qty > 0.000001 or status != TRACKING_STATUS_PENDING or cint(row.get("tracking_revision") or 0) > 0


def package_is_active(row: dict[str, Any]) -> bool:
    active_value = row.get("active")
    return active_value in (None, "") or bool(cint(active_value))


def package_is_excluded(row: dict[str, Any], rules: list[dict[str, Any]]) -> bool:
    rule = find_rule(row.get("component"), row.get("component_label"), rules)
    if not rule:
        return False
    return bool(
        cint(rule.get("exclude_from_delivery") or 0)
        or tracking_route(rule.get("tracking_route")) == TRACKING_ROUTE_NONE
    )


def insert_package_event(
    *,
    material_request: str,
    package_row: dict[str, Any],
    action: str,
    from_status: str,
    to_status: str,
    quantity: float | int,
    source: str,
    revision: int,
    event_at: Any | None = None,
    user: str | None = None,
) -> None:
    if not frappe.db.exists("DocType", EVENT_DOCTYPE):
        return
    package_name = package_row.get("name") or ""
    event_key = "%s|%s|%s" % (package_name, cint(revision), action)
    if frappe.db.exists(EVENT_DOCTYPE, {"event_key": event_key}):
        return
    event_doc = frappe.get_doc(
        {
            "doctype": EVENT_DOCTYPE,
            "event_key": event_key,
            "material_request": material_request,
            "package_id": package_name,
            "package_key": package_row.get("package_key") or "",
            "barcode_key": package_row.get("barcode_key") or package_name,
            "loading_code": package_row.get(PACKAGE_LOADING_CODE_FIELD) or "",
            "component": package_row.get("component") or "",
            "item_code": package_row.get("item_code") or "",
            "action": action,
            "from_status": from_status or "",
            "to_status": to_status or "",
            "quantity": clean_count(quantity),
            "source": source_value(source),
            "event_at": event_at or now_datetime(),
            "event_by": user or frappe.session.user or "Guest",
            "revision": cint(revision),
        }
    )
    event_doc.insert(ignore_permissions=True)


def ensure_package_event_baseline(material_request: str, package_row: dict[str, Any]) -> None:
    if not frappe.db.exists("DocType", EVENT_DOCTYPE):
        return
    package_name = package_row.get("name") or ""
    if not package_name or frappe.db.exists(EVENT_DOCTYPE, {"package_id": package_name}):
        return
    package_qty = flt(package_row.get("package_qty") or 0)
    ready_qty = flt(package_row.get("ready_qty") or 0)
    status = normalize_tracking_status(package_row.get("tracking_status"), package_qty, ready_qty)
    action = "ترحيل" if package_started(package_row) else "توليد"
    insert_package_event(
        material_request=material_request,
        package_row=package_row,
        action=action,
        from_status="",
        to_status=status,
        quantity=ready_qty,
        source="ترحيل" if action == "ترحيل" else "تزامن",
        revision=0,
        event_at=package_row.get("ready_at") or now_datetime(),
        user=package_row.get("ready_by") or frappe.session.user,
    )


def upsert_package_rows(
    material_request: str,
    package_rows: list[dict[str, Any]],
    *,
    rotate_unregistered_barcodes: bool = False,
) -> list[dict[str, Any]]:
    package_fields = doctype_fields(PACKAGE_DOCTYPE)
    existing_rows = frappe.get_all(
        PACKAGE_DOCTYPE,
        filters={"parent": material_request, "parentfield": PACKAGE_PARENTFIELD},
        fields=selected_fields(
            PACKAGE_DOCTYPE,
            [
                "name",
                "package_key",
                "component",
                "component_label",
                "item_code",
                "package_no",
                "package_qty",
                "package_label",
                "ready_qty",
                "ready_at",
                "ready_by",
                "barcode_key",
            ],
            (PACKAGE_LOADING_CODE_FIELD,) + PACKAGE_OPTIONAL_FIELDS,
        ),
        limit_page_length=0,
    )
    existing_by_key = {row.get("package_key"): row for row in existing_rows if row.get("package_key")}
    existing_by_name = {row.get("name"): row for row in existing_rows if row.get("name")}
    desired_matches: dict[str, dict[str, Any]] = {}
    matched_existing_names: set[str] = set()
    for desired in package_rows:
        existing = existing_by_name.get(desired.get("_existing_name")) or existing_by_key.get(desired.get("package_key"))
        if existing:
            desired_matches[desired.get("package_key")] = existing
            matched_existing_names.add(existing.get("name"))
    blockers: list[str] = []
    rules = load_rules()

    for desired in package_rows:
        package_key = desired.get("package_key")
        existing = desired_matches.get(package_key)
        if not existing or not package_started(existing):
            continue
        if abs(flt(existing.get("package_qty") or 0) - flt(desired.get("package_qty") or 0)) > 0.000001:
            blockers.append("تغيرت كمية الحزمة %s بعد بدء تتبعها" % package_key)
        old_package_label = (existing.get("package_label") or "").strip()
        new_package_label = (desired.get("package_label") or "").strip()
        if old_package_label and old_package_label != new_package_label:
            blockers.append("تغير نوع تغليف الحزمة %s بعد بدء تتبعها" % package_key)
        old_pack_size = flt(existing.get("pack_size_snapshot") or 0)
        if old_pack_size > 0 and abs(old_pack_size - flt(desired.get("pack_size_snapshot") or 0)) > 0.000001:
            blockers.append("تغير حجم التغليف للحزمة %s بعد بدء تتبعها" % package_key)
        old_route = (existing.get("tracking_route") or "").strip()
        new_route = (desired.get("tracking_route") or "").strip()
        if old_route and new_route and old_route != new_route:
            blockers.append("تغير مسار تتبع الحزمة %s بعد بدء تتبعها" % package_key)
        old_loading_code = (existing.get(PACKAGE_LOADING_CODE_FIELD) or "").strip()
        new_loading_code = (desired.get(PACKAGE_LOADING_CODE_FIELD) or "").strip()
        if old_loading_code and new_loading_code and old_loading_code != new_loading_code:
            blockers.append("تغير تكويد التحميل للحزمة %s بعد بدء تتبعها" % package_key)

    for existing in existing_rows:
        if existing.get("name") in matched_existing_names or not package_is_active(existing):
            continue
        if package_started(existing) and not package_is_excluded(existing, rules):
            blockers.append("الحزمة %s مسجلة ولا يمكن حذفها تلقائيًا" % (existing.get("package_key") or existing.get("name")))

    if blockers:
        frappe.throw(
            "تعذر تحديث حزم المكونات حفاظًا على سجل المسح:<br>" + "<br>".join("- " + row for row in blockers)
        )

    for existing in existing_rows:
        if existing.get("name") in matched_existing_names or not package_is_active(existing):
            continue
        if package_started(existing) and package_is_excluded(existing, rules):
            archive_values = {
                "required_for_delivery": 0,
                "tracking_route": TRACKING_ROUTE_NONE,
            }
            if "active" in package_fields:
                archive_values["active"] = 0
            frappe.db.set_value(
                PACKAGE_DOCTYPE,
                existing.get("name"),
                {key: value for key, value in archive_values.items() if key in package_fields},
                update_modified=False,
            )
            continue
        frappe.delete_doc(PACKAGE_DOCTYPE, existing.get("name"), ignore_permissions=True, force=True)

    result: list[dict[str, Any]] = []
    for index, desired in enumerate(package_rows, start=1):
        package_key = desired.get("package_key")
        existing = desired_matches.get(package_key)
        row = dict(desired)
        if existing:
            row["name"] = existing.get("name")
            if rotate_unregistered_barcodes and not package_started(existing):
                row["barcode_key"] = frappe.generate_hash(length=20)
            else:
                row["barcode_key"] = existing.get("barcode_key") or existing.get("name")
            update_values = {
                fieldname: value
                for fieldname, value in row.items()
                if fieldname in package_fields and fieldname != "name"
            }
            update_values["idx"] = index
            frappe.db.set_value(PACKAGE_DOCTYPE, existing.get("name"), update_values, update_modified=False)
        else:
            child = {
                fieldname: value
                for fieldname, value in row.items()
                if fieldname in package_fields
            }
            child.update(
                {
                    "doctype": PACKAGE_DOCTYPE,
                    "parent": material_request,
                    "parenttype": "Material Request",
                    "parentfield": PACKAGE_PARENTFIELD,
                    "idx": index,
                }
            )
            doc = frappe.get_doc(child)
            doc.db_insert()
            row["name"] = doc.name
            row["barcode_key"] = row.get("barcode_key") or frappe.generate_hash(length=20)
            frappe.db.set_value(PACKAGE_DOCTYPE, doc.name, "barcode_key", row["barcode_key"], update_modified=False)
        ensure_package_event_baseline(material_request, row)
        result.append(row)
    return result


def get_packages(material_request: str) -> list[dict[str, Any]]:
    if not frappe.db.exists("DocType", PACKAGE_DOCTYPE):
        return []
    fields = selected_fields(PACKAGE_DOCTYPE, [
        "name",
        "package_key",
        "component",
        "component_label",
        "item_code",
        "item_name",
        "package_no",
        "package_count",
        "package_label",
        "package_qty",
        "ready_qty",
        "remaining_qty",
        "status",
        "required_for_delivery",
        "ready_at",
        "ready_by",
        "source",
        "barcode_key",
    ], (PACKAGE_LOADING_CODE_FIELD,) + PACKAGE_OPTIONAL_FIELDS)
    filters: dict[str, Any] = {
        "parent": material_request,
        "parentfield": PACKAGE_PARENTFIELD,
    }
    if frappe.get_meta(PACKAGE_DOCTYPE).has_field("active"):
        filters["active"] = 1
    rows = frappe.get_all(
        PACKAGE_DOCTYPE,
        filters=filters,
        fields=fields,
        order_by="idx asc",
        limit_page_length=0,
    )
    result = []
    for row in rows:
        package_qty = flt(row.get("package_qty") or 0)
        ready_qty = flt(row.get("ready_qty") or 0)
        remaining = max(package_qty - ready_qty, 0)
        row["package_qty"] = clean_count(package_qty)
        row["ready_qty"] = clean_count(ready_qty)
        row["remaining_qty"] = clean_count(remaining)
        row["status"] = package_status(package_qty, ready_qty)
        row["tracking_status"] = normalize_tracking_status(row.get("tracking_status"), package_qty, ready_qty)
        row["tracking_route"] = tracking_route(row.get("tracking_route"))
        if "active" not in row:
            row["active"] = 1
        row["barcode_key"] = row.get("barcode_key") or row.get("name")
        result.append(row)
    return result


def package_matches_token(row: dict[str, Any], package_token: str) -> bool:
    barcode_key = (row.get("barcode_key") or "").strip()
    package_name = (row.get("name") or "").strip()
    accepted = {
        barcode_key,
        (row.get("package_key") or "").strip(),
        (row.get(PACKAGE_LOADING_CODE_FIELD) or "").strip(),
    }
    # Old labels used the child-row name as their barcode token. Once a token
    # rotates after source changes, that old row name must no longer be valid.
    if not barcode_key or barcode_key == package_name:
        accepted.add(package_name)
    return package_token in accepted


def get_package(material_request: str, package_token: str) -> dict[str, Any] | None:
    for row in get_packages(material_request):
        if package_matches_token(row, package_token):
            return row
    return None


def find_package_parent(package_token: str | None) -> str:
    token = (package_token or "").strip()
    if not token or not frappe.db.exists("DocType", PACKAGE_DOCTYPE):
        return ""
    for fieldname in ("barcode_key", PACKAGE_LOADING_CODE_FIELD, "package_key"):
        parent = frappe.db.get_value(
            PACKAGE_DOCTYPE,
            {fieldname: token, "parentfield": PACKAGE_PARENTFIELD},
            "parent",
        )
        if parent:
            return parent
    legacy_parent = frappe.db.get_value(PACKAGE_DOCTYPE, token, "parent")
    if not legacy_parent:
        return ""
    barcode_key = frappe.db.get_value(PACKAGE_DOCTYPE, token, "barcode_key") or ""
    return legacy_parent if not barcode_key or barcode_key == token else ""


def get_package_events(package_id: str | None, limit: int = 50) -> list[dict[str, Any]]:
    if not package_id or not frappe.db.exists("DocType", EVENT_DOCTYPE):
        return []
    return frappe.get_all(
        EVENT_DOCTYPE,
        filters={"package_id": package_id},
        fields=["name", "action", "from_status", "to_status", "quantity", "source", "event_at", "event_by", "revision"],
        order_by="event_at desc, creation desc",
        limit_page_length=max(min(cint(limit or 50), 200), 1),
    )


def sync_delivery_component_packages(material_request: str | None, dry_run: int | str | bool = 0) -> dict[str, Any]:
    material_request = normalize_material_request(material_request)
    dry_run_bool = bool(cint(dry_run or 0))
    if not material_request:
        frappe.throw("اسم طلب المواد مطلوب")
    if not frappe.db.exists("Material Request", material_request):
        frappe.throw("طلب المواد غير موجود: " + material_request)
    if not frappe.db.exists("DocType", PACKAGE_DOCTYPE):
        frappe.throw("جدول حزم مكونات التوريد غير مثبت على الموقع")

    mr_doc = ensure_material_request_access(material_request)
    component_rows = aggregate_components(mr_doc, validate_colors=True)
    source_hash = build_source_hash(mr_doc, component_rows=component_rows)
    previous_source_hash = (mr_doc.get(MR_SOURCE_HASH_FIELD) or "").strip()
    has_existing_packages = bool(get_packages(material_request))
    package_rows = build_package_rows(mr_doc, component_rows=component_rows)
    loading_prefix = get_or_make_loading_prefix(mr_doc, dry_run_bool)
    package_rows = assign_loading_codes(package_rows, loading_prefix)
    summary = summarize_packages(package_rows)

    if not dry_run_bool:
        package_rows = upsert_package_rows(
            material_request,
            package_rows,
            rotate_unregistered_barcodes=should_rotate_unregistered_barcodes(
                previous_source_hash,
                source_hash,
                has_existing_packages=has_existing_packages,
            ),
        )
        summary = update_material_request_summary(material_request, package_rows, source_hash=source_hash)
        frappe.db.commit()

    return {
        "status": "dry_run" if dry_run_bool else "synced",
        "material_request": material_request,
        "summary": summary,
        "loading_code": loading_prefix,
        "packages": package_rows,
        "package_count": len(package_rows),
        "source_hash": source_hash,
    }


def get_delivery_component_packages(
    material_request: str | None = None,
    component_package: str | None = None,
) -> dict[str, Any]:
    material_request = normalize_material_request(material_request)
    component_package = (component_package or "").strip()
    if not material_request and component_package:
        package_parent = find_package_parent(component_package)
        material_request = normalize_material_request(package_parent)

    if not material_request:
        frappe.throw("اسم طلب المواد مطلوب")
    if not frappe.db.exists("Material Request", material_request):
        frappe.throw("طلب المواد غير موجود: " + material_request)
    mr_doc = ensure_material_request_access(material_request, allow_guest=True)

    packages = get_packages(material_request)
    selected_package = None
    if component_package:
        for row in packages:
            if package_matches_token(row, component_package):
                selected_package = row
                break
        if not selected_package:
            frappe.throw("حزمة مكونات التوريد غير موجودة أو لم يتم توليدها بعد")

    summary = summarize_packages(packages)
    needs_sync = packages_need_sync(mr_doc, packages)
    overall = fulfillment_readiness(mr_doc, summary, needs_sync)
    return {
        "material_request": material_request,
        "summary": summary,
        "packages": packages,
        "package": selected_package,
        "selected_package": selected_package,
        "events": get_package_events(selected_package.get("name")) if selected_package else [],
        "package_count": len(packages),
        "needs_sync": 1 if needs_sync else 0,
        "fulfillment": overall,
    }


def publish_package_changed_event(material_request: str, package_row: dict[str, Any], summary: dict[str, Any]) -> None:
    try:
        frappe.publish_realtime(
            REALTIME_EVENT,
            {
                "material_request": material_request,
                "package_token": package_row.get("name") or package_row.get("barcode_key") or "",
                "loading_code": package_row.get(PACKAGE_LOADING_CODE_FIELD) or "",
                "status": package_row.get("status") or "",
                "tracking_status": package_row.get("tracking_status") or "",
                "summary": summary,
            },
            doctype="Material Request",
            docname=material_request,
            after_commit=True,
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Delivery component realtime publish failed")


def mark_delivery_component_package_event(
    material_request: str | None = None,
    component_package: str | None = None,
    action: str = TRACKING_ACTION_READY,
    mode: str = "full",
    ready_qty: float | int | str | None = None,
    source: str = "QR",
) -> dict[str, Any]:
    material_request = normalize_material_request(material_request)
    package_token = (component_package or "").strip()
    mode = (mode or "full").strip()
    source = source_value(source)
    action = (action or TRACKING_ACTION_READY).strip().lower()

    if not package_token:
        frappe.throw("مفتاح حزمة مكونات التوريد مطلوب")
    if not frappe.db.exists("DocType", PACKAGE_DOCTYPE):
        frappe.throw("جدول حزم مكونات التوريد غير مثبت على الموقع")
    if not material_request:
        package_parent = find_package_parent(package_token)
        material_request = normalize_material_request(package_parent)
    if not material_request:
        frappe.throw("اسم طلب المواد مطلوب")
    if not frappe.db.exists("Material Request", material_request):
        frappe.throw("طلب المواد غير موجود: " + material_request)
    mr_doc = ensure_material_request_access(material_request, allow_guest=True)
    if cint(mr_doc.docstatus) == 2:
        frappe.throw("طلب المواد ملغي ولا يمكن تسجيل الحزمة")

    package_row = get_package(material_request, package_token)
    if not package_row:
        frappe.throw("حزمة المكونات غير موجودة أو أن الملصق قديم. أعد طباعة الملصقات.")

    if packages_need_sync(mr_doc):
        frappe.throw("تغيرت مكونات الطلب بعد طباعة الملصق. أعد طباعة ملصقات المكونات.")

    frappe.db.sql(
        "SELECT name FROM `tab%s` WHERE name = %%s FOR UPDATE" % PACKAGE_DOCTYPE,
        (package_row.get("name"),),
    )
    package_row = get_package(material_request, package_token) or package_row
    package_qty = flt(package_row.get("package_qty") or 0)
    current_ready_qty = flt(package_row.get("ready_qty") or 0)
    requested_qty = flt(ready_qty or 0)
    current_tracking_status = normalize_tracking_status(
        package_row.get("tracking_status"), package_qty, current_ready_qty
    )
    package_tracking_route = tracking_route(package_row.get("tracking_route"))
    if package_tracking_route == TRACKING_ROUTE_NONE:
        frappe.throw("هذه الحزمة مستبعدة من التتبع")
    if package_tracking_route == TRACKING_ROUTE_READY_ONLY and action in (TRACKING_ACTION_LOAD, TRACKING_ACTION_DELIVER):
        frappe.throw("مسار هذه الحزمة تجهيز فقط ولا يتطلب تحميلًا أو توريدًا منفصلًا")

    if action == TRACKING_ACTION_READY and mode == "partial":
        if requested_qty <= 0:
            frappe.throw("أدخل كمية جزئية أكبر من صفر")
        new_ready_qty = current_ready_qty + requested_qty
    elif action == TRACKING_ACTION_READY:
        new_ready_qty = package_qty
    elif action == TRACKING_ACTION_REOPEN:
        new_ready_qty = 0
    else:
        new_ready_qty = current_ready_qty

    new_ready_qty = min(max(new_ready_qty, 0), package_qty)
    remaining_qty = max(package_qty - new_ready_qty, 0)
    new_status = package_status(package_qty, new_ready_qty)
    try:
        new_tracking_status = next_tracking_status(
            current_tracking_status,
            action,
            package_is_ready=new_status == TRACKING_STATUS_READY,
        )
    except ValueError as exc:
        frappe.throw(str(exc))

    if new_tracking_status == current_tracking_status and abs(new_ready_qty - current_ready_qty) <= 0.000001:
        summary = update_material_request_summary(material_request)
        package_row["tracking_status"] = current_tracking_status
        return {
            "status": "already_done",
            "material_request": material_request,
            "package": package_row,
            "summary": summary,
            "fulfillment": summary.get("overall") or {},
            "events": get_package_events(package_row.get("name")),
            "message": "تم تسجيل الحزمة مسبقًا",
        }

    stamp = now_datetime()
    user = frappe.session.user if frappe.session.user and frappe.session.user != "Guest" else "Guest"
    revision = cint(package_row.get("tracking_revision") or 0) + 1
    package_fields = doctype_fields(PACKAGE_DOCTYPE)
    update_values = {
        "ready_qty": clean_count(new_ready_qty),
        "remaining_qty": clean_count(remaining_qty),
        "status": new_status,
        "source": source,
    }
    optional_updates = {
        "tracking_status": new_tracking_status,
        "tracking_revision": revision,
    }
    if action == TRACKING_ACTION_READY:
        optional_updates.update({"ready_at": stamp, "ready_by": user})
    elif action == TRACKING_ACTION_LOAD:
        optional_updates.update({"loaded_at": stamp, "loaded_by": user})
    elif action == TRACKING_ACTION_DELIVER:
        optional_updates.update({"delivered_at": stamp, "delivered_by": user})
    elif action == TRACKING_ACTION_REOPEN:
        optional_updates.update(
            {
                "ready_at": None,
                "ready_by": None,
                "loaded_at": None,
                "loaded_by": None,
                "delivered_at": None,
                "delivered_by": None,
            }
        )
    update_values.update({key: value for key, value in optional_updates.items() if key in package_fields})
    frappe.db.set_value(
        PACKAGE_DOCTYPE,
        package_row.get("name"),
        update_values,
        update_modified=False,
    )
    event_actions = {
        TRACKING_ACTION_READY: "تجهيز جزئي" if new_status != TRACKING_STATUS_READY else "تسجيل",
        TRACKING_ACTION_LOAD: "تحميل",
        TRACKING_ACTION_DELIVER: "توريد",
        TRACKING_ACTION_REOPEN: "إعادة فتح",
    }
    package_row["ready_qty"] = clean_count(new_ready_qty)
    package_row["remaining_qty"] = clean_count(remaining_qty)
    package_row["status"] = new_status
    package_row["tracking_status"] = new_tracking_status
    package_row["tracking_revision"] = revision
    package_row["source"] = source
    package_row.update({key: value for key, value in optional_updates.items() if key in package_fields})
    insert_package_event(
        material_request=material_request,
        package_row=package_row,
        action=event_actions.get(action, action),
        from_status=current_tracking_status,
        to_status=new_tracking_status,
        quantity=new_ready_qty,
        source=source,
        revision=revision,
        event_at=stamp,
        user=user,
    )
    summary = update_material_request_summary(material_request)
    publish_package_changed_event(material_request, package_row, summary)
    frappe.db.commit()

    messages = {
        TRACKING_ACTION_READY: "تم تسجيل الحزمة" if new_status == TRACKING_STATUS_READY else "تم تسجيل كمية جزئية من الحزمة",
        TRACKING_ACTION_LOAD: "تم تسجيل تحميل الحزمة",
        TRACKING_ACTION_DELIVER: "تم تسجيل توريد الحزمة",
        TRACKING_ACTION_REOPEN: "تمت إعادة فتح الحزمة",
    }
    return {
        "status": "done" if new_status == TRACKING_STATUS_READY or action != TRACKING_ACTION_READY else "partial",
        "material_request": material_request,
        "package": package_row,
        "summary": summary,
        "fulfillment": summary.get("overall") or {},
        "events": get_package_events(package_row.get("name")),
        "message": messages.get(action, "تم تسجيل حركة الحزمة"),
    }


def mark_delivery_component_package_ready(
    material_request: str | None = None,
    component_package: str | None = None,
    mode: str = "full",
    ready_qty: float | int | str | None = None,
    source: str = "QR",
) -> dict[str, Any]:
    return mark_delivery_component_package_event(
        material_request=material_request,
        component_package=component_package,
        action=TRACKING_ACTION_READY,
        mode=mode,
        ready_qty=ready_qty,
        source=source,
    )


def get_material_request_fulfillment_readiness(material_request: str | None = None) -> dict[str, Any]:
    data = get_delivery_component_packages(material_request=material_request)
    persisted = update_material_request_summary(
        data.get("material_request"),
        data.get("packages") or [],
    )
    frappe.db.commit()
    return {
        "material_request": data.get("material_request"),
        "fulfillment": persisted.get("overall") or data.get("fulfillment") or {},
        "summary": persisted,
        "needs_sync": persisted.get("needs_sync") or 0,
    }


def resolve_delivery_tracking_code(code: str | None = None) -> dict[str, Any]:
    raw_code = normalize_tracking_code(code)
    if not raw_code:
        frappe.throw("أدخل رمز الطلب أو الحزمة")

    direct_request = raw_code if raw_code.startswith("MREQ-") else ""
    if not direct_request and raw_code and raw_code[0].isdigit():
        direct_request = "MREQ-" + raw_code
    if direct_request and frappe.db.exists("Material Request", direct_request):
        ensure_material_request_access(direct_request, allow_guest=True)
        return {
            "type": "material_request",
            "material_request": direct_request,
            "tracking_code": frappe.db.get_value(
                "Material Request", direct_request, MR_LOADING_CODE_FIELD
            )
            or "",
        }

    request_name = frappe.db.get_value(
        "Material Request", {MR_LOADING_CODE_FIELD: raw_code}, "name"
    )
    if request_name:
        ensure_material_request_access(request_name, allow_guest=True)
        return {
            "type": "material_request",
            "material_request": request_name,
            "tracking_code": raw_code,
        }

    package_parent = find_package_parent(raw_code)
    if package_parent:
        ensure_material_request_access(package_parent, allow_guest=True)
        package_row = get_package(package_parent, raw_code)
        if package_row:
            return {
                "type": "component_package",
                "material_request": package_parent,
                "tracking_code": frappe.db.get_value(
                    "Material Request", package_parent, MR_LOADING_CODE_FIELD
                )
                or "",
                "component_package": package_row.get("barcode_key") or package_row.get("name"),
                "loading_code": package_row.get(PACKAGE_LOADING_CODE_FIELD) or "",
            }

    frappe.throw("لم يتم العثور على طلب أو حزمة بهذا الرمز: " + raw_code)
    return {}
