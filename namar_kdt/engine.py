"""Native KDT service migrated from the verified TEST Server Script.

Business rules, source locks, idempotency and saved machine payloads are kept
unchanged. The only lifetime change is request-local cancellation state: the
legacy Safe Exec namespace was fresh for every invocation, while an imported
Python module persists across requests.
"""
from contextvars import ContextVar
import json

import frappe

# Source: cutting_desk/parts.py
"""Read-only piece builder matching the saved Material Request KDT label.

This file is concatenated into a Frappe Server Script. It deliberately has no
imports or writes. The reference print format is checked by the offline tests;
machine payloads must never contain a request, batch or tracking identifier.
"""


KDT_CANCELLED_SOURCE_FIELDS = ["name", "parent", "custom_is_cancelled_row"]


def kdt_current_source_rows(item_names, source_fields=KDT_CANCELLED_SOURCE_FIELDS):
    # Parent locks precede every child lock, matching row cancellation.
    names = sorted(set(str(name or "").strip() for name in item_names if name))
    if not names:
        return []
    references = frappe.get_all(
        "Material Request Item",
        filters={"name": ["in", names]},
        fields=["name", "parent"],
        order_by="name asc",
        limit_page_length=0,
    )
    parents = sorted(set(row.get("parent") for row in references if row.get("parent")))
    for parent in parents:
        frappe.db.sql(
            "SELECT name FROM `tabMaterial Request` WHERE name = %s FOR UPDATE",
            (parent,),
        )
    rows = []
    columns = ", ".join("`" + fieldname + "`" for fieldname in source_fields)
    for name in names:
        current_rows = frappe.db.sql(
            "SELECT " + columns + " FROM `tabMaterial Request Item` WHERE name = %s FOR UPDATE",
            (name,),
            as_dict=True,
        )
        if current_rows:
            current = current_rows[0]
            if current.get("parent") not in parents:
                frappe.throw("تغير طلب المواد المصدر. أعد تحميل الصفحة وحاول مرة أخرى.")
            rows.append(current)
    return rows


def kdt_require_active_sources(piece_rows, loader=kdt_current_source_rows):
    names = sorted(set(
        piece.get("material_request_item") for piece in piece_rows
        if piece.get("material_request_item")
    ))
    current_rows = loader(names)
    current = {row.get("name"): row for row in current_rows}
    for piece in piece_rows:
        row = current.get(piece.get("material_request_item"))
        if not row or frappe.utils.cint(row.get("custom_is_cancelled_row")):
            frappe.throw(
                "سطر طلب المواد ملغى أو غير موجود؛ لا يمكن تنفيذ قطعة KDT: "
                + str(piece.get("piece_id") or piece.get("piece_key") or "")
            )
    return current_rows



_CANCELLATION_STATE = ContextVar("namar_kdt_cancellation_state", default=None)


def _cancellation_state():
    state = _CANCELLATION_STATE.get()
    if state is None:
        state = ({}, {})
        _CANCELLATION_STATE.set(state)
    return state


def kdt_lock_request_sources(request_names, loader=kdt_current_source_rows,
                            current_rows=None, locked_requests=None):
    request_rows, request_locks = _cancellation_state()
    if current_rows is None:
        current_rows = request_rows
    if locked_requests is None:
        locked_requests = request_locks
    references = frappe.get_all(
        "Material Request Item",
        filters={"parent": ["in", request_names]},
        fields=["name", "parent"],
        order_by="name asc",
        limit_page_length=0,
    )
    for row in loader([row.get("name") for row in references]):
        current_rows[row.get("name")] = row
    for request_name in request_names:
        locked_requests[request_name] = 1


def kdt_source_is_cancelled(row, request_name="",
                            current_rows=None, locked_requests=None):
    request_rows, request_locks = _cancellation_state()
    if current_rows is None:
        current_rows = request_rows
    if locked_requests is None:
        locked_requests = request_locks
    parent = request_name or row.get("parent") or row.get("material_request")
    name = row.get("name") or row.get("source_row")
    if parent in locked_requests:
        current = current_rows.get(name)
        return not current or bool(frappe.utils.cint(current.get("custom_is_cancelled_row")))
    return bool(frappe.utils.cint(row.get("custom_is_cancelled_row")))


def cut_part_float(value):
    # Jinja's float filter does not strip thousands separators like flt does.
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def cut_part_int(value):
    # Match Jinja's int filter, including its fallback for numeric strings.
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError, OverflowError):
            return 0


def cut_part_widths(item, value_field, text_field, to_float=cut_part_float):
    text = str(item.get(text_field) or "").strip()
    if text == "-":
        text = ""
    if text and "+" in text:
        return text.replace(" ", "").split("+")
    if text:
        return [text]
    value = to_float(item.get(value_field) or 0)
    return [value] if value > 0 else []


def cut_part_color(item_code):
    # Display grouping follows the existing ERP item-code convention. Color is
    # neither the identity nor part of the machine payload.
    colors = {
        "B": "أسود", "C": "بني", "CG": "بني محروق", "DG": "فيراني",
        "DGZ": "دارك جراي", "F": "فلاور", "G": "رصاصي", "H": "عسلي",
        "N": "بدون لون", "NS": "نيو سيلفر", "P": "خشبي", "S": "سيلفر",
        "T": "تك", "W": "أبيض", "WS": "قشر الجوز", "WT": "وايت تك",
        "Z": "بيج", "011-": "بني 011", "022-": "وايت تك 022",
    }
    code = str(item_code or "").strip()
    if "-" in code:
        prefix = code.split("-")[0].strip()
        if prefix in ("011", "022"):
            prefix = prefix + "-"
    else:
        characters = []
        for char in code:
            if char.isdigit():
                break
            characters.append(char)
        prefix = "".join(characters).strip()
    return colors.get(prefix) or (prefix if prefix and len(prefix) <= 3 else "بدون لون")


def cut_part_rule(item, item_group, components, thickness, station, rules,
                  to_int=cut_part_int):
    sliding = str(item.get("custom_result_sliding_type") or "").strip()
    without_kalon = to_int(item.get("custom_without_kalon") or 0)
    for rule in rules:
        if rule.get("station") != station:
            continue
        if rule.get("item_code") and rule.get("item_code") != item.get("item_code"):
            continue
        if rule.get("item_group") and rule.get("item_group") != item_group:
            continue
        if rule.get("cutting_template") and rule.get("cutting_template") != (item.get("custom_cutting_template") or ""):
            continue
        if rule.get("sliding_type") and str(rule.get("sliding_type") or "").strip() != sliding:
            continue
        if rule.get("required_component") and rule.get("required_component") not in components:
            continue
        if to_int(rule.get("panel_thickness_mm") or 0) > 0 and to_int(rule.get("panel_thickness_mm")) != thickness:
            continue
        if rule.get("without_kalon") == "نعم" and not without_kalon:
            continue
        if rule.get("without_kalon") == "لا" and without_kalon:
            continue
        return dict(rule)
    return {}


def cut_part_machine(rule, station, panel_h, panel_w, leaf_h, leaf_w,
                     thickness, without_kalon, to_int=cut_part_int):
    # The reference has different defaults for the two stations: unknown/empty
    # dimension_source means panel for 550 and leaf for 660.
    use_leaf = (
        rule.get("dimension_source") == "الدرفة"
        if station == "KDT 550"
        else rule.get("dimension_source") != "البنل"
    )
    height = int(round((leaf_h if use_leaf else panel_h) * 10, 0))
    width = int(round((leaf_w if use_leaf else panel_w) * 10, 0))
    output_thickness = (
        to_int(rule.get("output_thickness_mm") or 0)
        if rule.get("thickness_source") == "قيمة ثابتة" else thickness
    )
    mode = rule.get("operation_mode") or ""
    payload = ""
    if rule and mode == "إرسال كود" and rule.get("program_code") and height > 0 and width > 0 and output_thickness > 0:
        separator = rule.get("payload_separator") or "*"
        # Do not trim program_code: the reference Jinja concatenates it as-is.
        payload = str(rule.get("program_code")) + str(separator) + str(height) + str(separator) + str(width) + str(separator) + str(output_thickness)
    state = ""
    if not rule:
        state = "لا توجد قاعدة " + station
    elif mode == "بانتظار الرمز":
        state = "بانتظار رمز " + station
    elif mode == "تخطي المحطة":
        state = "بدون كالون" if station == "KDT 660" and without_kalon else "تخطي " + station
    elif not payload:
        state = "بيانات القص غير مكتملة" if station == "KDT 550" else "بيانات الكالون غير مكتملة"
    return {
        "payload": payload,
        "barcode_type": rule.get("barcode_type") if rule else "QR",
        "height_mm": height,
        "width_mm": width,
        "thickness_mm": output_thickness,
        "state": state,
        "blocked": 1 if state and mode != "تخطي المحطة" else 0,
    }


def build_cut_parts(mr_doc, to_float=cut_part_float, to_int=cut_part_int,
                    widths_for=cut_part_widths, color_for=cut_part_color,
                    rule_for=cut_part_rule, machine_for=cut_part_machine,
                    cancelled_source=kdt_source_is_cancelled):
    """Return stable unit/part candidates from saved request fields only.

    Callers must load the saved document and check its read permission. Invalid
    rows remain visible with eligible=0; they cannot silently become cut work.
    Each source signature includes the selected rule snapshots, so editing a
    rule invalidates an old plan even when the request itself did not change.
    """
    rule_fields = [
        "name", "priority", "station", "operation_mode", "program_code",
        "payload_separator", "barcode_type", "dimension_source",
        "thickness_source", "output_thickness_mm", "item_code", "item_group",
        "cutting_template", "sliding_type", "required_component",
        "without_kalon", "panel_thickness_mm",
    ]
    rules = frappe.get_all(
        "KDT Barcode Rule", filters={"enabled": 1}, fields=rule_fields,
        order_by="priority desc, name asc", limit_page_length=0,
    )
    request_name = str(mr_doc.get("name") or "")
    group_cache = {}
    result = []
    for item in mr_doc.get("items") or []:
        if cancelled_source(item, request_name):
            continue
        row_name = str(item.get("name") or "")
        item_code = item.get("item_code") or ""
        if item_code not in group_cache:
            group_cache[item_code] = frappe.db.get_value("Item", item_code, "item_group") or ""
        item_group = group_cache.get(item_code)
        row_blockers = []
        if not request_name or not row_name:
            row_blockers.append("احفظ طلب المواد وسطر الباب أولًا")
        if to_int(mr_doc.get("docstatus") or 0) == 2:
            row_blockers.append("طلب المواد ملغى")
        qty = to_float(item.get("qty") or 0)
        if qty <= 0 or qty != int(qty):
            row_blockers.append("كمية الباب يجب أن تكون عددًا صحيحًا موجبًا")
        unit_count = int(qty) if qty > 0 else 1
        if unit_count < 1:
            unit_count = 1
        stores = []
        try:
            stores = json.loads(item.get("custom_store_data") or "[]")
        except (TypeError, ValueError):
            row_blockers.append("بيانات مكونات الباب غير صالحة للطباعة")
        if not isinstance(stores, list):
            stores = []
            row_blockers.append("بيانات مكونات الباب ليست قائمة")
        components = []
        panel_components = []
        thickness = 0
        component = ""
        for store in stores:
            if not isinstance(store, dict):
                row_blockers.append("أحد مكونات الباب غير صالح")
                continue
            component_name = str(store.get("component") or store.get("component_ar") or "").strip()
            if component_name:
                components.append(component_name)
            component_text = str(store.get("component_ar") or store.get("component") or "").replace("×", "X").replace("x", "X").replace(" ", "")
            if "بانل" in component_text and "X" in component_text:
                if component_name not in panel_components:
                    panel_components.append(component_name)
                thickness_cm = to_float(component_text.split("X")[-1] or 0)
                if not thickness and thickness_cm > 0:
                    thickness = int(round(thickness_cm * 10, 0))
                    component = component_name
        if not component:
            row_blockers.append("لم يُحدد البنل من مكونات الباب")
        elif len(panel_components) > 1:
            row_blockers.append("يوجد أكثر من بنل في سطر الباب؛ راجع المكون المطلوب قصه")
        panel_h = to_float(item.get("custom_result_panel_h") or 0)
        leaf_h = to_float(item.get("custom_result_leaf_h") or 0)
        leaf_base_w = to_float(item.get("custom_result_leaf_w") or 0)
        panel_widths = widths_for(item, "custom_result_panel_w", "custom_result_panel_w_text")
        leaf_widths = widths_for(item, "custom_result_leaf_w", "custom_result_leaf_w_text")
        if not panel_widths:
            panel_widths = [0]
        part_count = len(panel_widths)
        without_kalon = to_int(item.get("custom_without_kalon") or 0)
        cut_rule = rule_for(item, item_group, components, thickness, "KDT 550", rules)
        lock_rule = rule_for(item, item_group, components, thickness, "KDT 660", rules)
        source_values = {
            "request": request_name, "source_row": row_name,
            "item_code": item_code, "item_name": item.get("item_name") or "",
            "qty": qty, "item_group": item_group,
            "panel_h": panel_h, "panel_w": to_float(item.get("custom_result_panel_w") or 0),
            "panel_widths": panel_widths, "leaf_h": leaf_h,
            "leaf_w": leaf_base_w, "leaf_widths": leaf_widths,
            "stores": stores, "cutting_template": item.get("custom_cutting_template") or "",
            "sliding_type": item.get("custom_result_sliding_type") or "",
            "without_kalon": without_kalon, "cut_rule": cut_rule, "lock_rule": lock_rule,
        }
        source_signature = json.dumps(source_values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for unit_no in range(1, unit_count + 1):
            for part_offset in range(part_count):
                part_no = part_offset + 1
                panel_w = to_float(panel_widths[part_offset])
                leaf_w = to_float(leaf_widths[part_offset]) if len(leaf_widths) >= part_no else leaf_base_w
                cut = machine_for(cut_rule, "KDT 550", panel_h, panel_w, leaf_h, leaf_w, thickness, without_kalon)
                lock = machine_for(lock_rule, "KDT 660", panel_h, panel_w, leaf_h, leaf_w, thickness, without_kalon)
                blockers = list(row_blockers)
                if panel_h <= 0 or panel_w <= 0:
                    blockers.append("مقاس البنل غير مكتمل؛ ملصق طلب المواد لا يطبع هذه القطعة")
                    cut["payload"] = ""
                    lock["payload"] = ""
                if cut.get("blocked"):
                    blockers.append(cut.get("state"))
                if lock.get("blocked"):
                    blockers.append(lock.get("state"))
                payload = cut.get("payload")
                kalon_payload = lock.get("payload")
                if payload and kalon_payload:
                    label_mode = (
                        "مشترك KDT 550 وKDT 660"
                        if payload == kalon_payload and cut.get("barcode_type") == lock.get("barcode_type")
                        else "ملصقان KDT 550 ثم KDT 660"
                    )
                elif payload:
                    label_mode = "KDT 550 فقط"
                elif kalon_payload:
                    label_mode = "KDT 660 فقط"
                else:
                    label_mode = "تحتاج مراجعة"
                    blockers.append("لا يوجد باركود ماكينة صالح لهذه القطعة")
                piece_key = "%s|U%04d|P%02d" % (row_name, unit_no, part_no)
                result.append({
                    "piece_key": piece_key, "source_row": row_name,
                    "material_request": request_name,
                    "door_row": to_int(item.get("idx") or 0),
                    "unit_no": unit_no, "unit_count": unit_count,
                    "part_no": part_no, "part_count": part_count,
                    "piece_id": "%s-R%s-U%s-P%s" % (request_name, to_int(item.get("idx") or 0), unit_no, part_no),
                    "item_code": item_code, "item_name": item.get("item_name") or "",
                    "component": component, "color": color_for(item_code),
                    "height_mm": int(round(panel_h * 10, 0)),
                    "width_mm": int(round(panel_w * 10, 0)), "thickness_mm": thickness,
                    "leaf_height_mm": int(round(leaf_h * 10, 0)),
                    "leaf_width_mm": int(round(leaf_w * 10, 0)),
                    "machine_payload": payload, "barcode_type": cut.get("barcode_type"),
                    "kalon_payload": kalon_payload, "kalon_barcode_type": lock.get("barcode_type"),
                    "kalon_operation_mode": lock_rule.get("operation_mode") or "",
                    "barcode_rule": cut_rule.get("name") or "",
                    "kalon_barcode_rule": lock_rule.get("name") or "",
                    "cut_state": cut.get("state"), "kalon_state": lock.get("state"),
                    "cut_height_mm": cut.get("height_mm"), "cut_width_mm": cut.get("width_mm"),
                    "cut_thickness_mm": cut.get("thickness_mm"),
                    "kalon_height_mm": lock.get("height_mm"), "kalon_width_mm": lock.get("width_mm"),
                    "kalon_thickness_mm": lock.get("thickness_mm"),
                    "without_kalon": without_kalon, "label_mode": label_mode,
                    "source_signature": source_signature,
                    "eligible": 0 if blockers else 1,
                    "blocker": "، ".join(dict.fromkeys(blockers)),
                })
    return result

# Source: cutting_desk/printing.py
"""Render saved KDT machine-label snapshots without reading or changing ERP data.

Concatenated into a Frappe Server Script: no imports, writes, current-rule lookup
or automatic printing. Helpers are captured as defaults because safe_exec may
use separate global and local namespaces. The browser must await all barcode
images and block printing if an image fails to load.
"""


CUT_LABEL_STYLE = """
  @page { size: 60mm 40mm; margin: 0; }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: sans-serif; }
  .kdt-label {
    width: 60mm;
    height: 40mm;
    display: block;
    margin: 0 !important;
    break-after: page;
    page-break-after: always;
    break-inside: avoid;
    page-break-inside: avoid;
    overflow: hidden;
  }
  .kdt-label:last-child {
    break-after: auto;
    page-break-after: auto;
  }
  .kdt-card {
    width: 60mm;
    height: 40mm;
    padding: 1mm;
    direction: rtl;
    border: 1px solid #111;
    display: flex;
    flex-direction: column;
    gap: .25mm;
    overflow: hidden;
  }
  .top-row {
    display: grid;
    grid-template-columns: 1fr auto;
    gap: 1mm;
    align-items: center;
    min-height: 4mm;
  }
  .item-name {
    min-width: 0;
    direction: rtl;
    text-align: right;
    font-size: 7.5px;
    font-weight: 900;
    line-height: 1.1;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: clip;
  }
  .item-name.long { font-size: 6px; }
  .item-name.xlong { font-size: 5.2px; }
  .badges {
    direction: ltr;
    display: flex;
    gap: 1px;
    justify-content: flex-start;
    align-items: stretch;
    white-space: nowrap;
  }
  .badge {
    min-width: 5mm;
    border: 1px solid #111;
    border-radius: 3px;
    padding: 1px 2px;
    background: #fff;
    color: #111;
    font-size: 6px;
    font-weight: 900;
    line-height: 1;
    text-align: center;
    display: inline-flex;
    align-items: center;
    justify-content: center;
  }
  .badge-stage {
    min-width: 14mm;
    direction: ltr;
    font-size: 7.5px;
  }
  .badge-cut {
    background: #111 !important;
    color: #fff !important;
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
  }
  .badge-lock {
    border: 2px solid #111;
    background: #fff;
    color: #111;
  }
  .badge-shared {
    min-width: 21mm;
    background: #111 !important;
    color: #fff !important;
    font-size: 6.5px;
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
  }
  .code-text {
    direction: ltr;
    text-align: center;
    font-size: 8px;
    font-weight: 900;
    line-height: 1;
    letter-spacing: .1px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: clip;
  }
  .label-main {
    width: 100%;
    height: 28mm;
    position: relative;
    direction: ltr;
    overflow: hidden;
  }
  .side-info {
    min-width: 0;
    width: 13.5mm;
    height: 28mm;
    position: absolute;
    top: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    overflow: hidden;
    color: #111;
  }
  .side-piece {
    left: 0;
    padding-top: 1mm;
  }
  .side-door {
    right: 0;
    justify-content: center;
    padding-bottom: 2mm;
  }
  .side-label {
    direction: rtl;
    text-align: center;
    font-size: 8px;
    font-weight: 900;
    line-height: 1;
    white-space: nowrap;
  }
  .side-value {
    direction: ltr;
    text-align: center;
    font-size: 30px;
    font-weight: 900;
    line-height: .95;
    letter-spacing: 0;
    white-space: nowrap;
    max-width: 100%;
  }
  .door-value { margin-top: 1.5mm; }
  .door-value.long { font-size: 22px; }
  .door-value.xlong { font-size: 17px; }
  .piece-value {
    margin-top: 1.5mm;
    font-size: 25px;
  }
  .piece-value.long { font-size: 18px; }
  .piece-value.xlong { font-size: 14px; }
  .part-value {
    direction: ltr;
    margin-top: .8mm;
    font-size: 7.5px;
    font-weight: 900;
    line-height: 1;
    white-space: nowrap;
  }
  .request-value {
    direction: ltr;
    margin-top: auto;
    padding-bottom: .4mm;
    text-align: center;
    font-size: 13px;
    font-weight: 900;
    line-height: 1;
    letter-spacing: 0;
    white-space: nowrap;
    max-width: 100%;
  }
  .request-value.long { font-size: 10px; }
  .request-value.xlong { font-size: 8px; }
  .barcode-box {
    width: 100%;
    height: 28mm;
    border: 0;
    background: transparent;
    padding: 0;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .barcode-box img {
    width: 27.5mm !important;
    height: 27.5mm !important;
    display: block;
    object-fit: contain;
  }
  .barcode-box.linear img {
    width: 38mm !important;
    height: 11mm !important;
  }
  .label-main > .barcode-box,
  .label-main > .state-box {
    position: absolute;
    top: 0;
    left: 14mm;
    width: 29.5mm;
  }
  .linear-layout .side-info {
    width: 9mm;
  }
  .linear-layout > .barcode-box {
    left: 9.5mm;
    width: 38.5mm;
  }
  .linear-layout .side-label { font-size: 6px; }
  .linear-layout .door-value { font-size: 20px; }
  .linear-layout .piece-value { font-size: 16px; }
  .linear-layout .request-value { font-size: 8px; }
  .state-box {
    width: 100%;
    height: 27mm;
    border: 1.5px solid #111;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 2mm;
    padding: 2mm;
    text-align: center;
  }
  .state-title {
    direction: rtl;
    font-size: 12px;
    font-weight: 900;
    line-height: 1.15;
  }
  .dimension-text {
    direction: ltr;
    font-size: 7.5px;
    font-weight: 900;
    line-height: 1;
    white-space: nowrap;
  }
  .empty {
    width: 60mm;
    height: 40mm;
    display: flex;
    align-items: center;
    justify-content: center;
    border: 1px dashed #999;
    direction: rtl;
    text-align: center;
    padding: 4mm;
    font-size: 11px;
    font-weight: 800;
  }
"""


def cut_label_escape(value):
    text = "" if value is None else str(value)
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#x27;")


def cut_label_quote(value):
    """RFC 3986 percent encoding with explicit UTF-8; no import is required.

    Encode the entire machine value, including separators. Concatenating an
    unescaped value would let &, #, + or % change what the barcode API encodes.
    """
    encoded = []
    safe = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
    for character in str(value or ""):
        if character in safe:
            encoded.append(character)
            continue
        code = ord(character)
        if 55296 <= code <= 57343:
            # Isolated Unicode surrogates are not valid UTF-8 scalars.
            raise ValueError("قيمة الباركود تحتوي محرفًا غير صالح")
        octets = []
        if code < 128:
            octets = [code]
        elif code < 2048:
            octets = [192 | (code >> 6), 128 | (code & 63)]
        elif code < 65536:
            octets = [224 | (code >> 12), 128 | ((code >> 6) & 63), 128 | (code & 63)]
        else:
            octets = [240 | (code >> 18), 128 | ((code >> 12) & 63), 128 | ((code >> 6) & 63), 128 | (code & 63)]
        for octet in octets:
            encoded.append("%%%02X" % octet)
    return "".join(encoded)


def cut_label_barcode_url(payload, barcode_type, quote=cut_label_quote):
    prefix = "https://bwipjs-api.metafloor.com/?bcid="
    if barcode_type == "Code128":
        return prefix + "code128&text=" + quote(payload) + "&scale=3&height=14&includetext=false&paddingwidth=0&paddingheight=0"
    return prefix + "qrcode&text=" + quote(payload) + "&scale=8&eclevel=M&paddingwidth=0&paddingheight=0"


def cut_label_count(value):
    try:
        count = int(value)
    except (TypeError, ValueError, OverflowError):
        count = 1
    return count if count > 0 else 1


def cut_label_card(part, stage, stage_class, payload, barcode_type,
                   escape=cut_label_escape, count=cut_label_count,
                   barcode_url=cut_label_barcode_url):
    item_text = str(part.get("item_name") or part.get("item_code") or "")
    door_text = str(part.get("door_row") or "")
    unit_no = count(part.get("unit_no"))
    unit_count = count(part.get("unit_count"))
    part_no = count(part.get("part_no"))
    part_count = count(part.get("part_count"))
    piece_text = (
        str(unit_no) + "/" + str(unit_count) if unit_count > 1
        else (str(part_no) + "/" + str(part_count) if part_count > 1 else "1/1")
    )
    short_name = str(part.get("material_request") or "").replace("MREQ-", "")
    item_class = " xlong" if len(item_text) > 42 else (" long" if len(item_text) > 28 else "")
    door_class = " xlong" if len(door_text) > 3 else (" long" if len(door_text) > 2 else "")
    piece_class = " xlong" if len(piece_text) > 6 else (" long" if len(piece_text) > 4 else "")
    request_class = " xlong" if len(short_name) > 8 else (" long" if len(short_name) > 6 else "")
    linear = barcode_type == "Code128"
    fragments = [
        '<div class="kdt-label"><div class="kdt-card">',
        '<div class="top-row"><div class="item-name' + item_class + '">' + escape(item_text) + '</div>',
        '<div class="badges"><span class="badge badge-stage ' + escape(stage_class) + '">' + escape(stage) + '</span></div></div>',
        '<div class="code-text">' + escape(payload) + '</div>',
        '<div class="label-main' + (' linear-layout' if linear else '') + '">',
        '<div class="side-info side-piece"><div class="side-label">القطعة</div>',
        '<div class="side-value piece-value' + piece_class + '">' + escape(piece_text) + '</div>',
    ]
    if unit_count > 1 and part_count > 1:
        fragments.append('<div class="part-value">P' + escape(part_no) + '/' + escape(part_count) + '</div>')
    fragments.extend([
        '<div class="request-value' + request_class + '">' + escape(short_name) + '</div></div>',
        '<div class="barcode-box' + (' linear' if linear else '') + '">',
        '<img src="' + escape(barcode_url(payload, barcode_type)) + '" alt="' + escape(payload) + '"></div>',
        '<div class="side-info side-door"><div class="side-label">باب</div>',
        '<div class="side-value door-value' + door_class + '">' + escape(door_text) + '</div></div>',
        '</div></div></div>',
    ])
    return "".join(fragments)


def render_cut_labels(order_name, parts, escape=cut_label_escape,
                      card=cut_label_card, style=CUT_LABEL_STYLE):
    """Return a complete 60 x 40 mm HTML print document from saved snapshots.

    The caller checks order permission and snapshot freshness before calling.
    A stored piece identity is never appended to either machine payload. Shared
    labels require both the exact payload and barcode type to match, just as
    in the Material Request reference. No empty/station-state label is emitted.
    """
    labels = []
    for part in parts or []:
        if part.get("blocker") or part.get("eligible", 1) in (0, "0", False, None):
            continue
        if part.get("label_mode") == "تحتاج مراجعة":
            continue
        payload = part.get("machine_payload") or ""
        kalon_payload = part.get("kalon_payload") or ""
        barcode_type = part.get("barcode_type")
        kalon_type = part.get("kalon_barcode_type")
        if part.get("cut_state"):
            payload = ""
        if part.get("kalon_state"):
            kalon_payload = ""
        if payload and kalon_payload and payload == kalon_payload and barcode_type == kalon_type:
            labels.append(card(part, "KDT 550 / KDT 660", "badge-shared", payload, barcode_type))
        else:
            if payload:
                labels.append(card(part, "KDT 550", "badge-cut", payload, barcode_type))
            if kalon_payload:
                labels.append(card(part, "KDT 660", "badge-lock", kalon_payload, kalon_type))
    content = "".join(labels)
    if not labels:
        content = '<div class="empty" dir="rtl">لا توجد ملصقات باركود مؤهلة للطباعة في هذا الأمر</div>'
    return (
        '<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<title>ملصقات القص — ' + escape(order_name) + '</title><style>' + style + '</style>'
        '</head><body data-label-count="' + str(len(labels)) + '">' + content + '</body></html>'
    )

# Source: cutting_desk/service.py
"""Cut-only planning, reservations and execution. No stock/manufacturing writes.

Concatenated into an authenticated Frappe API Server Script. Parent Material
Request row locks are shared with the legacy submit guard. Database uniqueness
on KDT Cut Piece and KDT Cut Command is a second line of concurrency protection.
"""

def kd_error(message):
    frappe.throw(message)


def kd_json(value, fallback=None):
    if isinstance(value, (dict, list)):
        return value
    if not value:
        return fallback
    try:
        return json.loads(value)
    except Exception:
        kd_error("بيانات الطلب غير صالحة. حدّث الصفحة وحاول مرة أخرى.")


def kd_dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def kd_names(value, limit=40):
    if not isinstance(value, list):
        kd_error("اختر طلبات المواد من القائمة.")
    values = list(dict.fromkeys(str(v or "").strip() for v in value if v))
    if len(values) > limit:
        kd_error("الحد الأقصى في العملية الواحدة هو %s عنصرًا." % limit)
    return values


def kd_has_permission(doctype, ptype="read", doc=None):
    probe = frappe.get_doc(doctype, doc) if doc else frappe.new_doc(doctype)
    return bool(probe.has_permission(ptype))


def kd_permission(action):
    if frappe.session.user == "Guest":
        kd_error("سجّل الدخول لاستخدام صفحة القص.")
    permissions = {
        "search": "read", "preview": "read", "orders": "read", "order": "read",
        "history": "read", "print_data": "print", "approve": "submit",
        "revise": "write", "complete": "write", "recut": "amend",
        "correct": "cancel", "cancel": "cancel",
    }
    permission = permissions.get(action)
    if not permission:
        kd_error("إجراء القص غير معروف.")
    if not kd_has_permission("KDT Cutting Batch", ptype=permission):
        kd_error("لا تملك صلاحية هذا الإجراء على أوامر القص.")
    if action in ("approve", "revise", "recut"):
        if not kd_has_permission("KDT Cutting Batch", ptype="submit"):
            kd_error("تحتاج صلاحية اعتماد أوامر القص.")


def kd_source(name):
    doc = frappe.get_doc("Material Request", name)
    doc.check_permission("read")
    if int(doc.docstatus or 0) != 1:
        kd_error("طلب المواد %s غير معتمد أو ملغى." % name)
    return doc


def kd_order(name):
    if not name:
        kd_error("اختر أمر القص أولًا.")
    doc = frappe.get_doc("KDT Cut Order", name)
    # Internal cut documents are not exposed via generic resource permissions.
    # The API's legacy-action permission plus every source MR govern access.
    for request_name in kd_json(doc.source_requests, []):
        # Historical orders remain readable if their source was later cancelled.
        frappe.get_doc("Material Request", request_name).check_permission("read")
    return doc


def kd_order_data(doc):
    if doc.get("order_mode") == "components":
        return kc_order_data(doc)
    items = []
    for row in doc.get("items") or []:
        if row.state == "released":
            continue
        part = kd_json(row.details, {})
        part.update({"piece_key": row.piece_key, "state": row.state,
                     "order": doc.name, "attempt": int(row.attempt or 1),
                     "completed_at": row.completed_at,
                     "completed_by": row.completed_by or ""})
        items.append(part)
    return {"name": doc.name, "order_mode": "pieces", "status": doc.status, "revision": int(doc.revision or 0),
            "quantity": len(items), "completed": int(doc.completed or 0),
            "request_names": kd_json(doc.source_requests, []), "items": items,
            "modified": doc.modified,
            "permissions": {"can_approve": kd_has_permission("KDT Cutting Batch", ptype="submit"),
                            "can_edit": kd_has_permission("KDT Cutting Batch", ptype="write") and kd_has_permission("KDT Cutting Batch", ptype="submit"),
                            "can_complete": kd_has_permission("KDT Cutting Batch", ptype="write"),
                            "can_correct": kd_has_permission("KDT Cutting Batch", ptype="cancel"),
                            "can_recut": kd_has_permission("KDT Cutting Batch", ptype="amend") and kd_has_permission("KDT Cutting Batch", ptype="submit")}}


def kd_current_parts(request_names):
    parts = []
    for request_name in request_names:
        source = kd_source(request_name)
        rows = {row.name: 1 for row in source.get("items") or []}
        prior = frappe.get_all("KDT Cut Piece", filters={"material_request": request_name},
                               fields=["source_row", "current_order", "state"])
        orphaned = [row for row in prior if row.get("source_row") not in rows
                    and row.get("state") != "released"]
        legacy_sources = frappe.db.sql(
            "SELECT p.material_request_item AS source_row,p.parent AS current_order "
            "FROM `tabKDT Cutting Batch Item` p INNER JOIN `tabKDT Cutting Batch` b ON b.name=p.parent "
            "WHERE p.material_request=%s AND b.docstatus < 2 "
            "AND COALESCE(NULLIF(b.operation_mode,''),'قص') IN ('قص','تشغيل موحد')",
            (request_name,), as_dict=True)
        orphaned.extend(row for row in legacy_sources if row.get("source_row")
                        and row.get("source_row") not in rows)
        source_parts = build_cut_parts(source)
        if orphaned:
            for part in source_parts:
                part["eligible"] = 0
                part["blocker"] = "حُذف سطر سبق تخصيصه للقص. راجع أمر %s قبل إنشاء قص جديد." % orphaned[0].get("current_order")
        parts.extend(source_parts)
    if len(parts) > 5000:
        kd_error("الاختيار يتجاوز 5000 قطعة. قلّل عدد الطلبات أو نطاق التاريخ.")
    return parts


def kd_legacy(parts, locking=False):
    keys = [part.get("piece_key") for part in parts]
    result = {}
    for start in range(0, len(keys), 200):
        batch_keys = keys[start:start + 200]
        placeholders = ",".join(["%s"] * len(batch_keys))
        rows = frappe.db.sql(
            "SELECT p.piece_key, p.parent AS order_name, p.kdt_550_status, p.kdt_660_status, "
            "b.execution_status, b.docstatus FROM `tabKDT Cutting Batch Item` p "
            "INNER JOIN `tabKDT Cutting Batch` b ON b.name=p.parent "
            "WHERE p.piece_key IN (" + placeholders + ") AND b.docstatus < 2 "
            "AND COALESCE(NULLIF(b.operation_mode,''),'قص') IN ('قص','تشغيل موحد') "
            "ORDER BY b.creation ASC" + (" FOR UPDATE" if locking else ""),
            tuple(batch_keys), as_dict=True)
        for row in rows:
            if row.piece_key not in result:
                result[row.piece_key] = row
    return result


def kd_states(parts, locking=False):
    result = {}
    keys = [part.get("piece_key") for part in parts]
    for start in range(0, len(keys), 200):
        batch_keys = keys[start:start + 200]
        placeholders = ",".join(["%s"] * len(batch_keys))
        rows = frappe.db.sql(
            "SELECT name,piece_key,current_order,state,attempt,source_signature,details "
            "FROM `tabKDT Cut Piece` WHERE piece_key IN (" + placeholders + ")"
            + (" FOR UPDATE" if locking else ""), tuple(batch_keys), as_dict=True)
        for row in rows:
            result[row.piece_key] = row
    return result


def kd_annotate(parts):
    states = kd_states(parts)
    legacy = kd_legacy(parts)
    for part in parts:
        part["state"] = "available"
        part["order"] = ""
        state = states.get(part.get("piece_key"))
        if state and state.state != "released":
            part["state"] = state.state
            part["order"] = state.current_order
            part["attempt"] = int(state.attempt or 1)
            part["source_changed"] = state.source_signature != part.get("source_signature")
        elif part.get("piece_key") in legacy:
            row = legacy.get(part.get("piece_key"))
            done = row.kdt_550_status == "مكتمل" or (
                not part.get("machine_payload") and row.kdt_660_status == "مكتمل")
            part["state"] = "completed" if done or row.execution_status == "مكتمل" else "reserved"
            part["order"] = row.order_name
            part["legacy"] = 1
        elif not part.get("eligible"):
            part["state"] = "blocked"
    return parts


def kd_preparation(request_names):
    # These are request totals from the current report, not remaining cutting
    # pieces. Report summary quantities already include source-row quantities.
    preparation = {"source": "نتائج التخصيم", "scope": "requests_total",
                   "rows": [], "request_count": len(request_names)}
    if not request_names:
        return preparation
    try:
        report = frappe.call(
            "frappe.desk.query_report.run", report_name="نتائج التخصيم",
            filters=kd_dump({"material_requests": request_names}),
            ignore_prepared_report=1, are_default_filters=False)
        if (not isinstance(report, dict) or not isinstance(report.get("result"), list)
                or report.get("prepared_report") or report.get("error")):
            raise ValueError("Invalid preparation report result")
        groups = {}
        for row in report.get("result"):
            if not isinstance(row, dict):
                raise ValueError("Invalid preparation report row")
            if row.get("طلب_المواد") != "ملخص" or row.get("الحالة") != "ملخص المخازن":
                continue
            component = row.get("المكون")
            quantity = row.get("الكمية")
            if not isinstance(component, str) or not component.strip():
                raise ValueError("Missing preparation component")
            if (not isinstance(quantity, (int, float)) or isinstance(quantity, bool)
                    or quantity != quantity or abs(quantity) == float("inf")):
                raise ValueError("Invalid preparation quantity")
            color = str(row.get("اللون") or "بدون لون")
            key = kd_dump([color, component])
            if key not in groups:
                groups[key] = {"color": color, "component": component, "quantity": quantity}
            else:
                groups[key]["quantity"] = groups[key]["quantity"] + quantity
        rows = list(groups.values())
        rows.sort(key=lambda row: (row.get("color"), row.get("component")))
        # The report does not expose a reliable unit for every component. Do
        # not label quantities as panels/pieces or read inventory to guess one.
        preparation["rows"] = rows
    except Exception:
        preparation["error"] = "تعذر تحميل كشف التجهيز من نتائج التخصيم. أعد المحاولة أو افتح التقرير للتحقق."
    return preparation


def kd_preview(payload):
    request_names = kd_names(payload.get("request_names") or [])
    if not request_names:
        return {"groups": [], "summary": {"total": 0, "available": 0, "reserved": 0, "completed": 0, "blocked": 0},
                "preparation": kd_preparation([])}
    parts = kd_annotate(kd_current_parts(request_names))
    # All source requests have passed their read/submitted checks before the
    # report is invoked. A report failure must not disable existing cut work.
    preparation = kd_preparation(request_names)
    groups = {}
    summary = {"total": 0, "available": 0, "reserved": 0, "completed": 0, "blocked": 0}
    for part in parts:
        key = kd_dump([part.get("color"), part.get("component"), part.get("height_mm"),
                       part.get("width_mm"), part.get("thickness_mm")])
        if key not in groups:
            groups[key] = {"key": key, "color": part.get("color"), "component": part.get("component"),
                           "height_mm": part.get("height_mm"), "width_mm": part.get("width_mm"),
                           "thickness_mm": part.get("thickness_mm"), "parts": [], "total": 0,
                           "available": 0, "reserved": 0, "completed": 0, "blocked": 0}
        group = groups[key]
        group["parts"].append(part)
        group["total"] = group["total"] + 1
        group[part["state"]] = group[part["state"]] + 1
        summary["total"] = summary["total"] + 1
        summary[part["state"]] = summary[part["state"]] + 1
    ordered = list(groups.values())
    ordered.sort(key=lambda g: (g.get("color") or "", g.get("component") or "", -int(g.get("height_mm") or 0), -int(g.get("width_mm") or 0)))
    return {"groups": ordered, "summary": summary, "request_names": request_names,
            "preparation": preparation}


def kd_search(payload):
    values = payload.get("filters") or {}
    if not isinstance(values, dict):
        kd_error("خيارات البحث غير صالحة. حدّث الصفحة وحاول مرة أخرى.")
    filters = [["docstatus", "=", 1]]
    for field, operator, value in [("transaction_date", ">=", values.get("from_date")),
                                    ("transaction_date", "<=", values.get("to_date"))]:
        if value:
            filters.append([field, operator, str(value)])
    if "workflow_states" in values:
        states = values.get("workflow_states")
        if not isinstance(states, list) or len(states) > 100:
            kd_error("اختر حالات طلب المواد من القائمة.")
        if any(not isinstance(state, str) or not state.strip() or len(state.strip()) > 140 for state in states):
            kd_error("اختر حالات طلب المواد من القائمة.")
        states = list(dict.fromkeys(state.strip() for state in states))
        if states:
            filters.append(["workflow_state", "in", states])
    elif values.get("workflow_state"):
        # Keep older clients working; an explicit empty list means all states.
        filters.append(["workflow_state", "=", str(values.get("workflow_state"))])
    search = str(values.get("search") or "").strip()[:100]
    args = {"filters": filters, "fields": ["name", "transaction_date", "title", "workflow_state"],
            "order_by": "transaction_date desc, name desc", "limit_page_length": 101}
    if search:
        args["or_filters"] = [["name", "like", "%" + search + "%"], ["title", "like", "%" + search + "%"]]
    rows = frappe.get_list("Material Request", filters=args["filters"], fields=args["fields"],
                           order_by=args["order_by"], limit_page_length=args["limit_page_length"],
                           or_filters=args.get("or_filters"))
    # Options use the same document permissions but are independent of the
    # current search and its first 100 results, so older states stay selectable.
    state_rows = frappe.get_list("Material Request", filters=[["docstatus", "=", 1]],
                                 fields=["workflow_state"], group_by="workflow_state",
                                 order_by="workflow_state asc", limit_page_length=0)
    state_options = [row.get("workflow_state") for row in state_rows if row.get("workflow_state")]
    return {"requests": [{"name": r.name, "date": r.transaction_date, "title": r.title,
                          "status": r.workflow_state} for r in rows[:100]], "truncated": len(rows) > 100,
            "filter_options": {"workflow_states": state_options}}


def kd_history(doc):
    rows = frappe.get_all("KDT Cut Event", filters={"order": doc.name},
                          fields=["event_action", "piece_key", "before_state", "after_state", "revision",
                                  "reason", "user", "event_time", "details"],
                          order_by="creation desc", limit_page_length=300)
    for row in rows:
        details = kd_json(row.get("details"), {})
        part = details.get("after") or details.get("before") or {}
        row["piece_id"] = part.get("piece_id") or row.get("piece_key") or ""
    return rows


def kd_item_snapshot(row):
    part = kd_json(row.details, {})
    part.update({"state": row.state, "attempt": int(row.attempt or 1),
                 "completed_at": row.completed_at, "completed_by": row.completed_by or ""})
    return part


def kd_event(doc, action, key, before, after, payload):
    frappe.get_doc({"doctype": "KDT Cut Event", "order": doc.name, "piece_key": key,
                    "event_action": action, "before_state": (before or {}).get("state") or "",
                    "after_state": (after or {}).get("state") or "", "revision": int(doc.revision or 1),
                    "reason": str(payload.get("reason") or "").strip()[:1000],
                    "details": kd_dump({"before": before, "after": after}), "user": frappe.session.user,
                    "event_time": frappe.utils.now_datetime(), "request_id": payload.get("request_id")}).insert(ignore_permissions=True)


def kd_apply_part(row, part):
    for field in ("piece_key", "source_row", "material_request", "door_row", "unit_no", "part_no", "piece_id",
                  "color", "component", "height_mm", "width_mm", "thickness_mm", "source_signature"):
        row.set(field, part.get(field))
    row.details = kd_dump(part)


def kd_piece_record(row, order_name):
    existing = frappe.db.exists("KDT Cut Piece", row.piece_key)
    record = frappe.get_doc("KDT Cut Piece", row.piece_key) if existing else frappe.new_doc("KDT Cut Piece")
    record.update({"piece_key": row.piece_key, "current_order": order_name, "state": row.state,
                   "attempt": int(row.attempt or 1), "material_request": row.material_request,
                   "source_row": row.source_row, "source_signature": row.source_signature,
                   "details": row.details})
    record.save(ignore_permissions=True)


def kd_refresh_counts(doc):
    active = [row for row in doc.get("items") or [] if row.state != "released"]
    doc.quantity = len(active)
    doc.completed = len([row for row in active if row.state == "completed"])
    doc.status = "ملغى" if not active else ("مكتمل" if doc.completed == doc.quantity else "معتمد")


def kd_reason(payload):
    if not str(payload.get("reason") or "").strip():
        kd_error("اكتب سبب التعديل لحفظه في سجل الأمر.")


def kd_select(payload, parts, retained=None):
    keys = kd_names(payload.get("piece_keys") or [], 5000)
    if not keys:
        kd_error("حدد قطعة واحدة على الأقل.")
    part_map = {part.get("piece_key"): part for part in parts}
    for key in keys:
        if retained and key in retained and retained[key].state == "completed":
            continue
        if key not in part_map:
            kd_error("القطعة لم تعد موجودة ضمن الطلبات المحددة. حدّث المعاينة.")
        if not part_map[key].get("eligible"):
            kd_error(part_map[key].get("blocker") or "بيانات باركود القطعة تحتاج مراجعة.")
    return keys, part_map


def kd_assert_available(key, states, legacy, own_order=""):
    state = states.get(key)
    if state and state.state != "released" and state.current_order != own_order:
        kd_error("القطعة موجودة مسبقًا في أمر القص %s؛ لا يمكن قصها مرتين." % state.current_order)
    if key in legacy:
        kd_error("القطعة موجودة في أمر القص السابق %s. راجع الأمر السابق قبل تخصيصها مجددًا." % legacy[key].order_name)


def kd_mutate(action, payload):
    request_id = str(payload.get("request_id") or "").strip()
    if not request_id or len(request_id) > 120 or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in request_id):
        kd_error("تعذر تحديد رمز العملية. حدّث الصفحة وحاول مرة أخرى.")
    fingerprint = kd_dump({"action": action, "payload": payload, "user": frappe.session.user})
    existing = frappe.db.exists("KDT Cut Command", request_id)
    if existing:
        command = frappe.get_doc("KDT Cut Command", request_id)
        if command.user != frappe.session.user or command.fingerprint != fingerprint:
            kd_error("رمز العملية مستخدم لطلب مختلف.")
        kd_order(command.order)
        return kd_json(command.response, {})

    doc = kd_order(payload.get("name")) if action != "approve" else None
    if doc and doc.get("order_mode") == "components":
        kd_error("هذا أمر قص مكونات. استخدم إجراءات المكونات للحفاظ على الكميات وحبات البنل المرتبطة.")
    names = kd_names(payload.get("request_names") or [])
    if doc:
        names = sorted(set(names + kd_json(doc.source_requests, [])))
    if not names:
        kd_error("اختر طلبات المواد أولًا.")
    for name in names:
        frappe.get_doc("Material Request", name).check_permission("read")
    # All preceding work was read-only. Start a fresh transaction before shared
    # locks, so a waiting writer does not retain a stale repeatable-read snapshot.
    frappe.db.commit()
    try:
        for name in sorted(names):
            frappe.db.sql("SELECT name FROM `tabMaterial Request` WHERE name=%s FOR UPDATE", (name,))
        kdt_lock_request_sources(names)
        commands = frappe.db.sql("SELECT name,user,fingerprint,response,`order` FROM `tabKDT Cut Command` WHERE name=%s FOR UPDATE", (request_id,), as_dict=True)
        if commands:
            command = commands[0]
            if command.user != frappe.session.user or command.fingerprint != fingerprint:
                kd_error("رمز العملية مستخدم لطلب مختلف.")
            result = kd_json(command.response, {})
            frappe.db.commit()
            return result
        if doc:
            rows = frappe.db.sql("SELECT name,revision FROM `tabKDT Cut Order` WHERE name=%s FOR UPDATE", (doc.name,), as_dict=True)
            if not rows or int(rows[0].revision or 0) != int(payload.get("revision") or 0):
                kd_error("عُدّل الأمر بواسطة مستخدم آخر. حدّث الأمر قبل حفظ التغيير.")
            doc = kd_order(doc.name)
            if doc.status == "ملغى":
                kd_error("أمر القص ملغى.")
        current_parts = kd_current_parts(names) if action in ("approve", "revise", "complete", "recut") else []
        current_map = {part.get("piece_key"): part for part in current_parts}
        states = kd_states(current_parts if current_parts else [kd_json(r.details, {}) for r in doc.get("items") or []], True)
        legacy = kd_legacy(current_parts, True) if action in ("approve", "revise", "recut") else {}
        if action == "approve":
            selection = kd_select(payload, current_parts)
            keys = selection[0]
            part_map = selection[1]
            for key in keys:
                kd_assert_available(key, states, legacy)
            doc = frappe.get_doc({"doctype": "KDT Cut Order", "status": "معتمد", "revision": 1,
                                  "source_requests": kd_dump(names), "request_id": request_id, "items": []})
            for key in keys:
                row = doc.append("items", {"state": "reserved", "attempt": int((states.get(key) or {}).get("attempt") or 1)})
                kd_apply_part(row, part_map[key])
            kd_refresh_counts(doc)
            doc.insert(ignore_permissions=True)
            for row in doc.get("items") or []:
                kd_piece_record(row, doc.name)
                kd_event(doc, "اعتماد", row.piece_key, None, kd_item_snapshot(row), payload)
        else:
            doc.revision = int(doc.revision or 0) + 1
            active = {row.piece_key: row for row in doc.get("items") or [] if row.state != "released"}
            keys = kd_names(payload.get("piece_keys") or [], 5000)
            if action in ("revise", "recut", "correct", "cancel"):
                kd_reason(payload)
            if action == "revise":
                selection = kd_select(payload, current_parts, active)
                keys = selection[0]
                part_map = selection[1]
                for key, row in active.items():
                    if row.state == "completed" and key not in keys:
                        kd_error("لا يمكن حذف قطعة مقصوصة. استخدم تصحيح تسجيل القص إذا كان التسجيل خاطئًا.")
                for key in keys:
                    if key not in active or active[key].state != "completed":
                        kd_assert_available(key, states, legacy, doc.name)
                for key, row in active.items():
                    before = kd_item_snapshot(row)
                    if key not in keys:
                        row.state = "released"
                        kd_piece_record(row, doc.name)
                        kd_event(doc, "استبعاد", key, before, kd_item_snapshot(row), payload)
                    elif row.state == "reserved":
                        kd_apply_part(row, part_map[key])
                        kd_piece_record(row, doc.name)
                        if before != kd_item_snapshot(row):
                            kd_event(doc, "تعديل", key, before, kd_item_snapshot(row), payload)
                for key in keys:
                    if key in active:
                        continue
                    row = doc.append("items", {"state": "reserved", "attempt": int((states.get(key) or {}).get("attempt") or 1)})
                    kd_apply_part(row, part_map[key])
                    kd_piece_record(row, doc.name)
                    kd_event(doc, "إضافة", key, None, kd_item_snapshot(row), payload)
                doc.source_requests = kd_dump(names)
            elif action in ("complete", "correct", "recut"):
                if not keys:
                    kd_error("حدد القطع المطلوب تسجيل الإجراء عليها.")
                for key in keys:
                    row = active.get(key)
                    record = states.get(key)
                    if not row or not record or record.current_order != doc.name:
                        kd_error("القطعة ليست ضمن النسخة الحالية لهذا الأمر.")
                    before = kd_item_snapshot(row)
                    if action == "complete":
                        if row.state != "reserved":
                            kd_error("سبق تسجيل قص القطعة المحددة.")
                        current = current_map.get(key)
                        if not current or not current.get("eligible") or current.get("source_signature") != row.source_signature:
                            kd_error("تغيرت بيانات القطعة أو باركودها. راجع تعديل الأمر قبل تسجيل القص.")
                        row.state = "completed"
                        row.completed_at = frappe.utils.now_datetime()
                        row.completed_by = frappe.session.user
                        event = "تم القص"
                    else:
                        if row.state != "completed":
                            kd_error("هذا الإجراء متاح للقطع المقصوصة فقط.")
                        row.state = "reserved"
                        row.completed_at = None
                        row.completed_by = ""
                        event = "تصحيح تسجيل"
                        if action == "recut":
                            current = current_map.get(key)
                            if not current or not current.get("eligible"):
                                kd_error("بيانات القطعة الحالية غير صالحة لإعادة القص.")
                            kd_assert_available(key, states, legacy, doc.name)
                            kd_apply_part(row, current)
                            row.attempt = int(row.attempt or 1) + 1
                            event = "إعادة قص"
                    kd_piece_record(row, doc.name)
                    kd_event(doc, event, key, before, kd_item_snapshot(row), payload)
            elif action == "cancel":
                if not any(row.state == "reserved" for row in active.values()):
                    kd_error("لا توجد قطع غير مقصوصة لإلغائها.")
                for key, row in active.items():
                    if row.state == "completed":
                        continue
                    before = kd_item_snapshot(row)
                    row.state = "released"
                    kd_piece_record(row, doc.name)
                    kd_event(doc, "إلغاء", key, before, kd_item_snapshot(row), payload)
            else:
                kd_error("إجراء تعديل غير معروف.")
            kd_refresh_counts(doc)
            doc.save(ignore_permissions=True)
        result = kd_order_data(doc)
        frappe.get_doc({"doctype": "KDT Cut Command", "request_id": request_id, "order": doc.name,
                        "action": action, "user": frappe.session.user, "fingerprint": fingerprint,
                        "response": kd_dump(result)}).insert(ignore_permissions=True)
        frappe.db.commit()
        return result
    except Exception:
        frappe.db.rollback()
        raise


def kd_dispatch(action, payload):
    if action == "report_status":
        kd_permission("preview")
        return kc_report_status(payload)
    if action.startswith("component_"):
        return kc_dispatch(action[10:], payload)
    kd_permission(action)
    if action == "search":
        return kd_search(payload)
    if action == "preview":
        return kd_preview(payload)
    if action == "orders":
        rows = frappe.get_all("KDT Cut Order", fields=["name", "order_mode", "status", "quantity", "completed", "revision", "modified", "source_requests"],
                               order_by="modified desc", limit_page_length=100)
        visible = []
        for row in rows:
            allowed = True
            for name in kd_json(row.source_requests, []):
                if not kd_has_permission("Material Request", ptype="read", doc=name):
                    allowed = False
                    break
            if allowed:
                row.pop("source_requests", None)
                visible.append(row)
        return {"orders": visible}
    if action in ("order", "history", "print_data"):
        doc = kd_order(payload.get("name"))
        if action == "order":
            return kd_order_data(doc)
        if action == "history":
            return {"events": kd_history(doc)}
        if doc.get("order_mode") == "components":
            return kc_print_data(payload)
        # Serialize label preparation with edits and use a fresh DB snapshot.
        names = kd_json(doc.source_requests, [])
        frappe.db.commit()
        for name in sorted(names):
            frappe.db.sql("SELECT name FROM `tabMaterial Request` WHERE name=%s FOR UPDATE", (name,))
        kdt_lock_request_sources(names)
        frappe.db.sql("SELECT name,revision FROM `tabKDT Cut Order` WHERE name=%s FOR UPDATE", (doc.name,), as_dict=True)
        doc = kd_order(doc.name)
        if payload.get("revision") is not None and int(payload.get("revision") or 0) != int(doc.revision or 0):
            kd_error("تغير أمر القص. حدّث الأمر قبل الطباعة.")
        if doc.status == "ملغى":
            kd_error("لا يمكن طباعة أمر ملغى.")
        parts = kd_current_parts(kd_json(doc.source_requests, []))
        current = {p.get("piece_key"): p for p in parts}
        selected = kd_names(payload.get("piece_keys") or [], 5000)
        result = kd_order_data(doc)
        items = [p for p in result["items"] if not selected or p.get("piece_key") in selected]
        if not items or (selected and len(items) != len(selected)):
            kd_error("اختيار القطع للطباعة غير صالح.")
        for part in items:
            latest = current.get(part.get("piece_key"))
            if not latest or not latest.get("eligible") or latest.get("source_signature") != part.get("source_signature"):
                kd_error("تغير المقاس أو باركود القطعة بعد اعتماد الأمر. راجع التعديل قبل إعادة الطباعة.")
        result["items"] = items
        result["html"] = render_cut_labels(doc.name, items)
        return result
    return kd_mutate(action, payload)

# Source: cutting_desk/component_service.py
"""Source-level component cutting allocations from the cutting report.

Uses the same transactional locks, piece ledger and audit documents as the
physical-piece desk. Component quantities retain their report-source precision;
only real, mapped panel pieces have KDT machine labels. No inventory writes.
"""


def kc_hash(value):
    return frappe.utils.sha256_hash(kd_dump(value))


def kc_key(source):
    return "C" + kc_hash([source.get("source_row"), source.get("component"), source.get("raw_item") or ""])


def kc_issue_context(source, group, physical, include_panel=True):
    saved_components = []
    component_names = []
    first = physical[0] if physical else {}
    context = {"material_request": source.get("material_request"), "source_row": source.get("source_row"),
               "door_row": first.get("door_row") or 0, "item_code": first.get("item_code") or "",
               "allocation_key": source.get("allocation_key") or source.get("piece_key") or "",
               "expected_component": group.get("component"),
               "expected_component_label": group.get("component_label") or group.get("component")}
    if not include_panel:
        return context
    stores = []
    try:
        signature = json.loads(first.get("source_signature") or "{}")
        stores = (signature.get("stores") or []) if isinstance(signature, dict) else []
    except Exception:
        stores = []
    for store in stores:
        if not isinstance(store, dict):
            continue
        component = store.get("component") or store.get("component_ar") or ""
        label = store.get("component_ar") or component
        panel_text = str(label).lower()
        if (component and component not in component_names
                and ("بانل" in panel_text or "بنل" in panel_text or "panel" in panel_text)):
            component_names.append(component)
            saved_components.append({"component": component, "label": label})
    for part in physical:
        component = part.get("component") or ""
        if not component or component in component_names:
            continue
        label = component
        for store in stores:
            if isinstance(store, dict) and store.get("component") == component:
                label = store.get("component_ar") or component
                break
        component_names.append(component)
        saved_components.append({"component": component, "label": label})
    missing = []
    if not saved_components:
        missing.append("مكون البنل المحفوظ")
    if not physical or any(int(part.get("height_mm") or 0) <= 0 for part in physical):
        missing.append("ارتفاع البنل المحفوظ")
    if not physical or any(int(part.get("width_mm") or 0) <= 0 for part in physical):
        missing.append("عرض البنل المحفوظ")
    context.update({"saved_components": saved_components, "missing_fields": missing,
                    "expected_part_count": source.get("part_count"),
                    "saved_part_count": len(set(part.get("part_no") for part in physical if part.get("part_no")))})
    return context


def kc_add_issue(issues, code, message, context, details=None):
    if any(issue.get("code") == code and issue.get("message") == message for issue in issues):
        return
    issue = dict(context)
    issue.update({"code": code, "message": message})
    if details:
        issue["details"] = details
    issues.append(issue)


def kc_scope(payload, doc=None):
    prior = kd_json(doc.component_scope, {}) if doc else {}
    names = sorted(kd_names(payload.get("request_names") if "request_names" in payload else prior.get("request_names") or []))
    if doc:
        names = sorted(set(names + kd_json(doc.source_requests, [])))
    if not names:
        kd_error("اختر طلبات المواد التي تحتوي المكونات أولًا.")
    filters = payload.get("filters") if "filters" in payload else prior.get("filters") or {}
    if not isinstance(filters, dict):
        kd_error("فلاتر المكونات غير صالحة.")
    filters = dict(filters)
    # Source names are permission-checked and authoritative, never an unchecked
    # client-provided broad report scope.
    filters.pop("material_request", None)
    filters["material_requests"] = names
    keys = sorted(kd_names(payload.get("component_keys") if "component_keys" in payload else prior.get("component_keys") or [], 5000))
    scope = {"request_names": names, "filters": filters, "component_keys": keys}
    if "allocation_keys" in payload or "allocation_keys" in prior:
        scope["allocation_keys"] = sorted(kd_names(payload.get("allocation_keys") if "allocation_keys" in payload
                                                    else prior.get("allocation_keys") or [], 5000))
        # Existing groups keep their exact source subset. A component explicitly
        # added in the advanced editor may contribute new sources as usual.
        scope["allocation_component_keys"] = list(prior.get("component_keys") or keys)
    return scope


def kc_report(names, filters):
    source_rows = {}
    for name in names:
        source = kd_source(name)
        source_rows[name] = {
            item.name: not kdt_source_is_cancelled(item, name)
            for item in source.get("items") or []
        }
    report = frappe.call("frappe.desk.query_report.run", report_name="نتائج التخصيم",
                         filters=kd_dump(filters), ignore_prepared_report=1, are_default_filters=False)
    if not isinstance(report, dict) or not isinstance(report.get("result"), list) or report.get("prepared_report") or report.get("error"):
        kd_error("تعذر تحميل مصادر المكونات من نتائج التخصيم. أعد فتح التقرير.")
    groups = []
    seen = {}
    for row in report.get("result"):
        if not isinstance(row, dict) or row.get("طلب_المواد") != "ملخص" or row.get("الحالة") != "ملخص المخازن":
            continue
        key = row.get("_kdt_component_key")
        component = row.get("_kdt_component_name")
        sources = row.get("_kdt_sources")
        if not key or not component or not isinstance(sources, list) or not sources:
            kd_error("التقرير لا يحتوي هويات مصادر المكونات. حدّث التقرير قبل إنشاء أمر القص.")
        identity = kd_json(key, [])
        if not isinstance(identity, list) or len(identity) != 3 or identity[0] != component:
            kd_error("هوية صف المكون في التقرير غير صالحة.")
        if key in seen:
            kd_error("تكرر صف المكون في نتائج التقرير. راجع مصادره قبل القص.")
        seen[key] = 1
        contributions = []
        total = 0
        for source in sources:
            if not isinstance(source, dict) or source.get("material_request") not in names or not source.get("source_row"):
                kd_error("مصدر المكون خارج طلبات المواد المحددة أو غير محفوظ.")
            quantity = source.get("quantity")
            if (not isinstance(quantity, (int, float)) or isinstance(quantity, bool) or quantity <= 0
                    or quantity != quantity or abs(quantity) == float("inf")):
                kd_error("كمية مصدر المكون غير صالحة. راجع نتائج التخصيم.")
            if source.get("component") != component or str(source.get("raw_item") or "") != str(identity[2] or ""):
                kd_error("مصدر المكون لا يطابق صف الملخص المحدد.")
            part_nos = source.get("part_nos") or []
            if not isinstance(part_nos, list) or any(not isinstance(number, int) or isinstance(number, bool) or number < 1 for number in part_nos):
                kd_error("أجزاء مصدر المكون غير صالحة.")
            if source.get("source_row") not in source_rows[source.get("material_request")]:
                kd_error("سطر مصدر المكون لم يعد موجودًا في طلب المواد. حدّث التقرير.")
            if not source_rows[source.get("material_request")].get(source.get("source_row")):
                continue
            item = dict(source)
            item["raw_item"] = str(source.get("raw_item") or "")
            item["color"] = str(source.get("color") or "")
            item["part_nos"] = sorted(set(part_nos))
            item["allocation_key"] = kc_key(item)
            contributions.append(item)
            total = total + quantity
        if not contributions:
            continue
        groups.append({"component_key": key, "component": component,
                       "component_label": str(row.get("المكون") or component),
                       "color": str(identity[1] or ""), "raw_item": str(identity[2] or ""),
                       "quantity": round(total, 3), "sources": contributions})
    return groups


def kc_build(scope, own_order=""):
    names = scope["request_names"]
    cancelled_sources = {}
    for request_name in names:
        for source_item in kd_source(request_name).get("items") or []:
            if kdt_source_is_cancelled(source_item, request_name):
                cancelled_sources[source_item.get("name")] = 1
    # Check every current component before selecting a subset. A changed raw
    # item or removed component must not masquerade as fresh cutting demand.
    full = kc_report(names, {"material_requests": names})
    current_ids = {}
    lineage = {}
    for group in full:
        for source in group["sources"]:
            key = source["allocation_key"]
            if key in current_ids:
                kd_error("المصدر نفسه موزع على أكثر من صف مكون. راجع التقرير قبل الاعتماد.")
            current_ids[key] = source
            line_key = kd_dump([source["source_row"], source["component"]])
            if line_key not in lineage:
                lineage[line_key] = []
            lineage[line_key].append(source)
    orphan_orders = {}
    own_existing = {}
    for name in names:
        prior = frappe.get_all("KDT Cut Piece", filters={"material_request": name},
                               fields=["piece_key", "current_order", "state", "details", "source_row"])
        for record in prior:
            if record.get("source_row") in cancelled_sources:
                continue
            if record.get("state") == "released":
                continue
            details = kd_json(record.get("details"), {})
            if details.get("record_kind") != "component":
                continue
            if record.get("current_order") == own_order:
                own_existing[record.get("piece_key")] = 1
            if record.get("piece_key") not in current_ids and (record.get("current_order") != own_order or record.get("state") == "completed"):
                if name not in orphan_orders or record.get("current_order") != own_order:
                    orphan_orders[name] = record.get("current_order")
    groups = full if scope["filters"] == {"material_requests": names} else kc_report(names, scope["filters"])
    physical = kd_current_parts(names)
    physical_by_source = {}
    physical_by_row = {}
    for part in physical:
        line_key = kd_dump([part.get("source_row"), part.get("component")])
        if line_key not in physical_by_source:
            physical_by_source[line_key] = []
        physical_by_source[line_key].append(part)
        source_row = part.get("source_row")
        if source_row not in physical_by_row:
            physical_by_row[source_row] = []
        physical_by_row[source_row].append(part)
    allocations = {}
    panels = {}
    for group in groups:
        group["allocation_keys"] = []
        for source in group["sources"]:
            key = source["allocation_key"]
            if key in allocations:
                kd_error("مصدر المكون مكرر في التحديد؛ لا يمكن احتساب كميته مرتين.")
            issues = []
            line_key = kd_dump([source["source_row"], source["component"]])
            siblings = lineage.get(line_key) or []
            candidates = physical_by_source.get(line_key) or []
            part_nos = source["part_nos"]
            linked = []
            component_text = (group["component_label"] + " " + group["component"]).lower()
            looks_like_panel = "بانل" in component_text or "بنل" in component_text or "panel" in component_text
            context = kc_issue_context(source, group, physical_by_row.get(source["source_row"]) or [],
                                       bool(candidates) or looks_like_panel)
            if looks_like_panel and not candidates:
                if not context["saved_components"]:
                    kc_add_issue(issues, "panel_source_missing",
                                 "بيانات البنل غير محفوظة في سطر طلب المواد؛ راجع مكون البنل ومقاساته قبل القص.", context)
                elif any(saved.get("component") == source["component"] for saved in context["saved_components"]):
                    kc_add_issue(issues, "panel_source_invalid",
                                 "تعذر استخراج حبات KDT من بيانات البنل المحفوظة؛ راجع المكونات والمقاسات وقواعد الباركود.", context)
                else:
                    kc_add_issue(issues, "panel_component_mismatch",
                                 "بنل التقرير يختلف عن البنل المحفوظ في سطر طلب المواد؛ راجع المصدر قبل القص.", context)
            if candidates:
                if len(siblings) > 1 and any(not sibling.get("part_nos") for sibling in siblings):
                    kc_add_issue(issues, "ambiguous_raw_scope",
                                 "المكون موزع على أكثر من صنف خام دون تحديد الأجزاء؛ لا يمكن توزيع كمية الخام على الباركود بالتخمين.", context)
                expected = source.get("part_count")
                actual = sorted(set(int(part.get("part_no") or 0) for part in candidates))
                if not isinstance(expected, int) or isinstance(expected, bool) or expected < 1:
                    kc_add_issue(issues, "panel_part_count_missing", "مصدر البنل يفتقد عدد الأجزاء الصحيح من تقرير التخصيم؛ حدّث التقرير.", context)
                elif expected != len(actual) or actual != list(range(1, expected + 1)):
                    kc_add_issue(issues, "panel_part_count_mismatch", "عدد أجزاء التخصيم لا يطابق أجزاء البنل المحفوظة في طلب المواد.", context)
                if part_nos and any(number not in actual for number in part_nos):
                    kc_add_issue(issues, "panel_part_scope_mismatch", "أجزاء التخصيم المحددة لا تطابق باركود البنل المحفوظ في طلب المواد.", context)
                for part in candidates:
                    if part_nos and int(part.get("part_no") or 0) not in part_nos:
                        continue
                    linked.append(dict(part))
                    if not part.get("eligible"):
                        kc_add_issue(issues, "panel_barcode_invalid", part.get("blocker") or "باركود البنل يحتاج مراجعة.", context)
                    for sibling in siblings:
                        if sibling["allocation_key"] != key and (not sibling.get("part_nos") or int(part.get("part_no") or 0) in sibling.get("part_nos")):
                            kc_add_issue(issues, "raw_part_overlap", "الجزء نفسه مرتبط بأكثر من صنف خام؛ راجع توزيع مصادر التخصيم.", context)
            if (source["material_request"] in orphan_orders
                    and not (orphan_orders[source["material_request"]] == own_order and key in own_existing)):
                kc_add_issue(issues, "orphan_source",
                             "حُذف أو تغير مصدر مكون سبق تخصيصه في أمر %s. راجع الأمر السابق قبل إنشاء قص جديد." % orphan_orders[source["material_request"]],
                             context, {"order": orphan_orders[source["material_request"]]})
            for part in linked:
                panel_key = part["piece_key"]
                if panel_key in panels and panels[panel_key].get("allocation_key") != key:
                    kc_add_issue(issues, "shared_panel_overlap", "حبة البنل نفسها مخصصة لمكونين في الاختيار.", context)
                part["record_kind"] = "component_panel"
                part["allocation_key"] = key
                panels[panel_key] = part
            allocation = dict(source)
            allocation.update({"piece_key": key, "piece_id": group["component_label"],
                               "door_row": context["door_row"], "item_code": context["item_code"],
                               "record_kind": "component", "component_key": group["component_key"],
                               "component_label": group["component_label"], "group_color": group["color"],
                               "linked_piece_keys": [part["piece_key"] for part in linked],
                               "eligible": 0 if issues else 1, "issues": issues,
                               "blocker": "، ".join(dict.fromkeys(issue["message"] for issue in issues))})
            allocation["source_signature"] = kd_dump({"source": source,
                                                       "component_label": group["component_label"],
                                                       "panels": [{"piece_key": part["piece_key"], "signature": part["source_signature"]} for part in linked]})
            allocations[key] = allocation
            group["allocation_keys"].append(key)
    parts = list(allocations.values()) + list(panels.values())
    if len(allocations) > 5000 or len(panels) > 5000:
        kd_error("اختيار المكونات يتجاوز 5000 مصدر أو حبة. قلّل عدد الطلبات المحددة.")
    return {"groups": groups, "allocations": allocations, "panels": panels, "parts": parts}


def kc_retain_completed(build, doc):
    if not doc:
        return build
    snapshots = {row.piece_key: row for row in doc.get("items") or [] if row.state == "completed"}
    for key, row in snapshots.items():
        saved = kd_json(row.details, {})
        if saved.get("record_kind") != "component":
            continue
        latest = build["allocations"].get(key)
        saved["source_changed"] = not latest or latest.get("source_signature") != saved.get("source_signature")
        saved["state"] = "completed"
        saved["attempt"] = int(row.attempt or 1)
        saved["completed_at"] = row.completed_at
        saved["completed_by"] = row.completed_by
        build["allocations"][key] = saved
        for panel_key in saved.get("linked_piece_keys") or []:
            if panel_key not in snapshots:
                kd_error("سجل حبات المكون المكتمل غير متسق. راجع الأمر قبل التعديل.")
            build["panels"][panel_key] = kd_json(snapshots[panel_key].details, {})
    build["groups"] = kc_rows(list(build["allocations"].values()))
    build["parts"] = list(build["allocations"].values()) + list(build["panels"].values())
    return build


def kc_selected(scope, build, doc=None):
    selected = scope["component_keys"]
    if not selected:
        kd_error("حدد صف مكون واحدًا على الأقل من التقرير.")
    groups = {group["component_key"]: group for group in build["groups"]}
    if any(key not in groups for key in selected):
        kd_error("أحد صفوف المكونات المحددة لم يعد موجودًا في التقرير. حدّث التحديد.")
    allocations = []
    restricted = scope.get("allocation_component_keys") or []
    allowed = scope.get("allocation_keys") or []
    anchors = ([row.piece_key for row in doc.get("items") or []
                if row.state != "released" and kd_json(row.details, {}).get("record_kind") == "component"]
               if doc and "allocation_keys" in scope else [])
    for key in selected:
        # Color changes regroup stable source identities. A current group
        # containing a retained identity must keep the original subset even
        # when its color-based component_key is new. A separate group with no
        # retained identity is still an explicit addition in the editor.
        restrict = key in restricted or any(source_key in allowed or source_key in anchors
                                            for source_key in groups[key]["allocation_keys"])
        allocations.extend(source_key for source_key in groups[key]["allocation_keys"]
                           if not restrict or source_key in allowed)
    return allocations


def kc_signature(scope, build, allocation_keys):
    return kc_hash({"scope": scope, "sources": [{"key": key, "signature": build["allocations"][key]["source_signature"]}
                                               for key in sorted(allocation_keys)]})


def kc_rows(allocations):
    rows = {}
    issue_index = {}
    for allocation in allocations:
        key = allocation["component_key"]
        if key not in rows:
            rows[key] = {"component_key": key, "component": allocation["component"],
                         "component_label": allocation["component_label"], "color": allocation.get("group_color") or "",
                         "raw_item": allocation.get("raw_item") or "", "quantity": 0,
                         "sources": [], "allocation_keys": [], "machine_piece_count": 0,
                         "orders": [], "state": "available", "blocker": "", "issues": [], "editable": True}
            issue_index[key] = {}
        row = rows[key]
        row["quantity"] = row["quantity"] + allocation["quantity"]
        row["allocation_keys"].append(allocation["piece_key"])
        row["sources"].append(dict(allocation))
        row["machine_piece_count"] = row["machine_piece_count"] + len(allocation.get("linked_piece_keys") or [])
        if allocation.get("order") and allocation["order"] not in row["orders"]:
            row["orders"].append(allocation["order"])
        source_issues = allocation.get("issues") or []
        if allocation.get("blocker") and not source_issues:
            source_issues = [{"code": "source_blocked", "message": allocation["blocker"],
                              "material_request": allocation.get("material_request"),
                              "source_row": allocation.get("source_row"), "door_row": allocation.get("door_row") or 0,
                              "allocation_key": allocation.get("piece_key")}]
        for issue in source_issues:
            issue_key = kd_dump([issue.get("code"), issue.get("message")])
            if issue_key not in issue_index[key]:
                summary = {"code": issue.get("code"), "message": issue.get("message"), "count": 0, "sources": []}
                issue_index[key][issue_key] = summary
                row["issues"].append(summary)
            summary = issue_index[key][issue_key]
            source_id = issue.get("allocation_key") or kd_dump([issue.get("material_request"), issue.get("source_row")])
            if not any((saved.get("allocation_key") or kd_dump([saved.get("material_request"), saved.get("source_row")])) == source_id for saved in summary["sources"]):
                summary["sources"].append(dict(issue))
                summary["count"] = summary["count"] + 1
        if allocation.get("state") == "completed":
            row["editable"] = False
    result = list(rows.values())
    for row in result:
        row["blocker"] = "، ".join(dict.fromkeys(issue.get("message") for issue in row["issues"] if issue.get("message")))
        states = [source.get("state") or "available" for source in row["sources"]]
        row["state"] = ("blocked" if row["blocker"] else
                        ("completed" if all(state == "completed" for state in states) else
                         ("reserved" if any(state in ("reserved", "completed") for state in states) else "available")))
        row["quantity"] = round(row["quantity"], 3)
    result.sort(key=lambda row: (row["color"], row["component_label"], row["raw_item"]))
    return result


def kc_report_keys(scope, build):
    # Direct creation always represents the whole filtered report. The browser
    # supplies identities, never trusted source quantities or availability.
    if sorted(scope["component_keys"]) != sorted(group["component_key"] for group in build["groups"]):
        kd_error("تغيرت نتائج التقرير. حدّث التقرير لإنشاء أمر من جميع المكونات الظاهرة.")
    return [source["piece_key"] for source in build["allocations"].values()]


def kc_order_reference(name, state, legacy=False):
    return {"name": name, "doctype": "KDT Cutting Batch" if legacy else "KDT Cut Order",
            "state": state, "status": "تم القص" if state == "completed" else "له أمر قص",
            "url": ("/app/kdt-cutting-batch/" if legacy else "/app/kdt-cutting-desk?cut_order=") + name}


def kc_legacy_references(parts, locking=False):
    keys = [part["piece_key"] for part in parts]
    result = {}
    for start in range(0, len(keys), 200):
        batch_keys = keys[start:start + 200]
        placeholders = ",".join(["%s"] * len(batch_keys))
        rows = frappe.db.sql(
            "SELECT p.piece_key, p.parent AS order_name, p.kdt_550_status, p.kdt_660_status, "
            "b.execution_status, b.docstatus FROM `tabKDT Cutting Batch Item` p "
            "INNER JOIN `tabKDT Cutting Batch` b ON b.name=p.parent "
            "WHERE p.piece_key IN (" + placeholders + ") AND b.docstatus < 2 "
            "AND COALESCE(NULLIF(b.operation_mode,''),'قص') IN ('قص','تشغيل موحد') "
            "ORDER BY b.creation ASC" + (" FOR UPDATE" if locking else ""), tuple(batch_keys), as_dict=True)
        for row in rows:
            if row.piece_key not in result:
                result[row.piece_key] = []
            result[row.piece_key].append(row)
    return result


def kc_add_reference(references, reference):
    for saved in references:
        if saved["name"] == reference["name"] and saved["doctype"] == reference["doctype"]:
            if saved["state"] != reference["state"]:
                saved["state"] = "mixed"
                saved["status"] = "له أمر قص"
            return
    references.append(reference)


def kc_report_data(scope, build, states, legacy):
    keys = kc_report_keys(scope, build)
    allocations = []
    totals = {"available": 0, "already_created": 0, "blocked": 0}
    for key in keys:
        allocation = dict(build["allocations"][key])
        references = []
        claimed_panels = []
        allocation_claimed = False
        for checked in [key] + allocation["linked_piece_keys"]:
            state = states.get(checked)
            if state and state.state != "released":
                if checked == key:
                    allocation_claimed = True
                elif checked not in claimed_panels:
                    claimed_panels.append(checked)
                reference = kc_order_reference(state.current_order, state.state)
                kc_add_reference(references, reference)
            for previous in legacy.get(checked) or []:
                if checked not in claimed_panels:
                    claimed_panels.append(checked)
                done = (previous.get("kdt_550_status") == "مكتمل" or previous.get("execution_status") == "مكتمل"
                        or (not build["panels"].get(checked, {}).get("machine_payload")
                            and previous.get("kdt_660_status") == "مكتمل"))
                reference = kc_order_reference(previous.order_name, "completed" if done else "reserved", True)
                kc_add_reference(references, reference)
        references.sort(key=lambda reference: (reference["doctype"], reference["name"]))
        lineage_blocked = any(issue.get("code") == "orphan_source" for issue in allocation.get("issues") or [])
        creation_state = ("blocked" if lineage_blocked else ("already_created" if references else
                          ("available" if allocation.get("eligible") else "blocked")))
        coverage = ("full" if allocation_claimed or (claimed_panels and len(claimed_panels) == len(allocation["linked_piece_keys"]))
                    else ("partial" if claimed_panels else "none"))
        allocation.update({"creation_state": creation_state, "orders": references, "coverage": coverage,
                           "state": ("completed" if references and all(reference["state"] == "completed" for reference in references)
                                     else ("reserved" if references else "available")),
                           "order": references[0]["name"] if references else "",
                           "creation_note": ("بعض حبات المصدر مرتبطة بأمر سابق؛ استُبعد المصدر كاملًا دون تقسيم كميته."
                                             if coverage == "partial" else ("سبق إنشاء أمر قص لهذا المصدر." if references else ""))})
        allocations.append(allocation)
        totals[creation_state] = totals[creation_state] + 1
    rows = kc_rows(allocations)
    for row in rows:
        row["available_quantity"] = round(sum(source["quantity"] for source in row["sources"]
                                              if source["creation_state"] == "available"), 3)
        row["already_created_quantity"] = round(sum(source["quantity"] for source in row["sources"]
                                                    if source["creation_state"] == "already_created"), 3)
        row["blocked_quantity"] = round(sum(source["quantity"] for source in row["sources"]
                                            if source["creation_state"] == "blocked"), 3)
        row["orders"] = []
        for source in row["sources"]:
            for reference in source["orders"]:
                kc_add_reference(row["orders"], dict(reference))
        row["creation_state"] = ("blocked" if any(source["creation_state"] == "blocked" for source in row["sources"])
                                 else ("available" if all(source["creation_state"] == "available" for source in row["sources"])
                                       else ("already_created" if all(source["creation_state"] == "already_created" for source in row["sources"])
                                             else "mixed")))
    availability = [{"key": source["piece_key"], "creation_state": source["creation_state"],
                     "coverage": source["coverage"], "orders": source["orders"]}
                    for source in sorted(allocations, key=lambda source: source["piece_key"])]
    return {"scope": scope, "request_names": scope["request_names"], "rows": rows, "counts": totals,
            "report_signature": kc_signature(scope, build, keys), "status_signature": kc_hash(availability),
            "can_create": bool(totals["available"]) and not totals["blocked"]
                          and kd_has_permission("KDT Cutting Batch", "submit")}


def kc_report_status(payload):
    scope = kc_scope({"request_names": payload.get("request_names"), "filters": payload.get("filters") or {},
                      "component_keys": payload.get("component_keys") or []})
    build = kc_build(scope)
    return kc_report_data(scope, build, kd_states(build["parts"]), kc_legacy_references(list(build["panels"].values())))


def kc_command_result(command, fingerprint, payload):
    if command.user != frappe.session.user or command.fingerprint != fingerprint:
        kd_error("رمز العملية مستخدم لطلب مختلف.")
    if command.get("order"):
        kd_order(command.order)
    for name in kd_names(payload.get("request_names") or []):
        frappe.get_doc("Material Request", name).check_permission("read")
    return kd_json(command.response, {})


def kc_created_result(order, status, skipped, changed):
    return {"created": bool(order), "order": order, "report_status": status,
            "skipped_sources": skipped, "availability_changed": changed,
            "message": ("تم إنشاء أمر القص للمصادر المتاحة، مع استبعاد المصادر التي لها أوامر سابقة."
                        if order and skipped else ("تم إنشاء أمر القص من نتائج التقرير."
                        if order else ("كل المصادر لها أوامر قص سابقة؛ لم يُنشأ أمر جديد." if skipped
                                       else "لا توجد مصادر مكونات متاحة لإنشاء أمر قص.")))}


def kc_preview(payload):
    doc = kd_order(payload.get("name")) if payload.get("name") else None
    if doc and doc.get("order_mode") != "components":
        kd_error("الأمر المحدد ليس أمر قص مكونات.")
    if doc and doc.status == "ملغى":
        kd_error("أمر القص ملغى.")
    if doc and payload.get("revision") is not None and int(payload["revision"]) != int(doc.revision or 0):
        kd_error("عُدّل الأمر بواسطة مستخدم آخر. حدّث الأمر قبل المعاينة.")
    scope = kc_scope(payload, doc)
    build = kc_retain_completed(kc_build(scope, doc.name if doc else ""), doc)
    keys = kc_selected(scope, build, doc)
    states = kd_states(build["parts"])
    legacy = kd_legacy(list(build["panels"].values()))
    allocations = []
    for key in keys:
        allocation = dict(build["allocations"][key])
        allocation["issues"] = [dict(issue) for issue in allocation.get("issues") or []]
        allocation["state"] = "available"
        allocation["order"] = ""
        for checked in [key] + allocation["linked_piece_keys"]:
            state = states.get(checked)
            if state and state.state != "released":
                allocation["state"] = state.state
                allocation["order"] = state.current_order
                if state.current_order != (doc.name if doc else ""):
                    kc_add_issue(allocation["issues"], "already_allocated",
                                 "المصدر أو إحدى حباته موجودة مسبقًا في أمر القص %s؛ لا يمكن تخصيص جزء من الكمية أو تجاهله." % state.current_order,
                                 {"material_request": allocation["material_request"], "source_row": allocation["source_row"],
                                  "door_row": allocation.get("door_row") or 0, "allocation_key": key},
                                 {"order": state.current_order, "state": state.state})
            if checked in legacy:
                allocation["state"] = "reserved"
                allocation["order"] = legacy[checked].order_name
                kc_add_issue(allocation["issues"], "legacy_allocated", "حبة من مصدر المكون موجودة في أمر القص السابق %s." % legacy[checked].order_name,
                             {"material_request": allocation["material_request"], "source_row": allocation["source_row"],
                              "door_row": allocation.get("door_row") or 0, "allocation_key": key}, {"order": legacy[checked].order_name})
        allocation["blocker"] = "، ".join(dict.fromkeys(issue["message"] for issue in allocation["issues"]))
        allocations.append(allocation)
    rows = kc_rows(allocations)
    if doc:
        retained = set(keys)
        missing = [row for row in doc.get("items") or [] if row.state == "completed"
                   and kd_json(row.details, {}).get("record_kind") == "component" and row.piece_key not in retained]
        if missing:
            for row in rows:
                row["blocker"] = "لا يمكن استبعاد مكون تم قصه من الأمر. أبقِ الصفوف المكتملة ضمن التحديد."
                row["state"] = "blocked"
    return {"rows": rows, "request_names": scope["request_names"], "component_scope": scope,
            "preview_signature": kc_signature(scope, build, keys),
            "can_approve": bool(rows) and not any(row.get("blocker") for row in rows)
                           and kd_has_permission("KDT Cutting Batch", "submit")
                           and (not doc or kd_has_permission("KDT Cutting Batch", "write"))}


def kc_order_data(doc):
    allocations = []
    panels = []
    for row in doc.get("items") or []:
        if row.state == "released":
            continue
        item = kd_item_snapshot(row)
        item.update({"piece_key": row.piece_key, "order": doc.name})
        if item.get("record_kind") == "component":
            item["allocation_key"] = row.piece_key
            allocations.append(item)
        elif item.get("record_kind") == "component_panel":
            panels.append(item)
    return {"name": doc.name, "order_mode": "components", "status": doc.status,
            "revision": int(doc.revision or 0), "quantity": len(allocations),
            "completed": len([item for item in allocations if item["state"] == "completed"]),
            "request_names": kd_json(doc.source_requests, []), "component_scope": kd_json(doc.component_scope, {}),
            "component_rows": kc_rows(allocations), "items": allocations, "machine_items": panels,
            "modified": doc.modified,
            "permissions": {"can_approve": kd_has_permission("KDT Cutting Batch", "submit"),
                            "can_edit": kd_has_permission("KDT Cutting Batch", "write") and kd_has_permission("KDT Cutting Batch", "submit"),
                            "can_complete": kd_has_permission("KDT Cutting Batch", "write"),
                            "can_correct": kd_has_permission("KDT Cutting Batch", "cancel"),
                            "can_recut": kd_has_permission("KDT Cutting Batch", "amend") and kd_has_permission("KDT Cutting Batch", "submit")}}


def kc_refresh(doc):
    active = [row for row in doc.get("items") or [] if row.state != "released"
              and kd_json(row.details, {}).get("record_kind") == "component"]
    doc.quantity = len(active)
    doc.completed = len([row for row in active if row.state == "completed"])
    doc.status = "ملغى" if not active else ("مكتمل" if doc.quantity == doc.completed else "معتمد")


def kc_targets(payload, doc):
    active = {row.piece_key: row for row in doc.get("items") or [] if row.state != "released"}
    allocations = {key: row for key, row in active.items() if kd_json(row.details, {}).get("record_kind") == "component"}
    keys = kd_names(payload.get("allocation_keys") or [], 5000)
    if not keys and payload.get("component_keys"):
        groups = kd_names(payload["component_keys"], 5000)
        keys = [key for key, row in allocations.items() if kd_json(row.details, {}).get("component_key") in groups]
        if set(groups) != set(kd_json(allocations[key].details, {}).get("component_key") for key in keys):
            kd_error("تحديد صفوف المكونات غير صالح لهذا الأمر.")
    if not keys or any(key not in allocations for key in keys):
        kd_error("حدد مصادر المكونات الموجودة في الأمر لتنفيذ الإجراء.")
    return keys


def kc_linked(keys, allocations):
    result = []
    for key in keys:
        result.append(key)
        result.extend(allocations[key].get("linked_piece_keys") or [])
    return list(dict.fromkeys(result))


def kc_mutate(action, payload):
    direct_create = action == "create_available"
    command_action = "component_" + action
    request_id = str(payload.get("request_id") or "").strip()
    if not request_id or len(request_id) > 120 or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in request_id):
        kd_error("تعذر تحديد رمز العملية. حدّث الصفحة وحاول مرة أخرى.")
    fingerprint = kd_dump({"action": command_action, "payload": payload, "user": frappe.session.user})
    if frappe.db.exists("KDT Cut Command", request_id):
        command = frappe.get_doc("KDT Cut Command", request_id)
        return kc_command_result(command, fingerprint, payload)
    if direct_create:
        action = "approve"
    doc = kd_order(payload.get("name")) if action != "approve" else None
    if doc and doc.get("order_mode") != "components":
        kd_error("الأمر المحدد ليس أمر قص مكونات.")
    scope = (kc_scope({"request_names": payload.get("request_names"), "filters": payload.get("filters") or {},
                       "component_keys": payload.get("component_keys") or []}) if direct_create else kc_scope(payload, doc))
    report_scope = scope
    names = scope["request_names"]
    for name in names:
        frappe.get_doc("Material Request", name).check_permission("read")
    frappe.db.commit()
    try:
        for name in sorted(names):
            frappe.db.sql("SELECT name FROM `tabMaterial Request` WHERE name=%s FOR UPDATE", (name,))
        kdt_lock_request_sources(names)
        commands = frappe.db.sql("SELECT name,user,fingerprint,response,`order` FROM `tabKDT Cut Command` WHERE name=%s FOR UPDATE", (request_id,), as_dict=True)
        if commands:
            command = commands[0]
            result = kc_command_result(command, fingerprint, payload)
            frappe.db.commit()
            return result
        if doc:
            locked = frappe.db.sql("SELECT name,revision FROM `tabKDT Cut Order` WHERE name=%s FOR UPDATE", (doc.name,), as_dict=True)
            if not locked or int(locked[0].revision or 0) != int(payload.get("revision") or 0):
                kd_error("عُدّل الأمر بواسطة مستخدم آخر. حدّث الأمر قبل حفظ التغيير.")
            doc = kd_order(doc.name)
            if doc.status == "ملغى":
                kd_error("أمر القص ملغى.")
        build = kc_build(scope, doc.name if doc else "") if action in ("approve", "revise", "complete", "recut") else None
        if action == "revise":
            build = kc_retain_completed(build, doc)
        if direct_create:
            keys = kc_report_keys(scope, build)
            if not payload.get("report_signature") or payload["report_signature"] != kc_signature(scope, build, keys):
                kd_error("تغيرت بيانات المكونات أو كمياتها. حدّث التقرير قبل إنشاء الأمر.")
        elif action in ("approve", "revise"):
            keys = kc_selected(scope, build, doc)
            if not payload.get("preview_signature") or payload["preview_signature"] != kc_signature(scope, build, keys):
                kd_error("تغيرت بيانات المكونات أو كمياتها. راجع المعاينة قبل الاعتماد.")
        else:
            keys = kc_targets(payload, doc) if action != "cancel" else []
        before_active = {row.piece_key: row for row in doc.get("items") or [] if row.state != "released"} if doc else {}
        saved = {key: kd_json(row.details, {}) for key, row in before_active.items()}
        relevant = (build["parts"] if build else []) + list(saved.values())
        states = kd_states(relevant, True)
        legacy_references = kc_legacy_references(list(build["panels"].values()), True) if direct_create else {}
        legacy = ({key: references[0] for key, references in legacy_references.items()} if direct_create else
                  (kd_legacy(list(build["panels"].values()), True) if build else {}))
        skipped = []
        availability_changed = False
        if direct_create:
            status = kc_report_data(report_scope, build, states, legacy_references)
            availability_changed = bool(payload.get("status_signature") and payload["status_signature"] != status["status_signature"])
            skipped = [dict(source) for group in status["rows"] for source in group["sources"]
                       if source["creation_state"] == "already_created"]
            blocked = [source for group in status["rows"] for source in group["sources"]
                       if source["creation_state"] == "blocked"]
            if blocked:
                kd_error(blocked[0].get("blocker") or "توجد مصادر تحتاج مراجعة. حدّث التقرير لمعرفة سبب المنع.")
            keys = [source["piece_key"] for group in status["rows"] for source in group["sources"]
                    if source["creation_state"] == "available"]
            if not keys:
                result = kc_created_result(None, status, skipped, availability_changed)
                frappe.get_doc({"doctype": "KDT Cut Command", "request_id": request_id,
                                "action": command_action, "user": frappe.session.user,
                                "fingerprint": fingerprint, "response": kd_dump(result)}).insert(ignore_permissions=True)
                frappe.db.commit()
                return result
            scope = dict(scope)
            scope["allocation_keys"] = sorted(keys)
            scope["component_keys"] = sorted(set(build["allocations"][key]["component_key"] for key in keys))
            scope["allocation_component_keys"] = list(scope["component_keys"])
            names = sorted(set(build["allocations"][key]["material_request"] for key in keys))
            scope["request_names"] = names
            scope["filters"] = dict(scope["filters"])
            scope["filters"]["material_requests"] = names
        if action == "approve":
            doc = frappe.get_doc({"doctype": "KDT Cut Order", "order_mode": "components", "status": "معتمد", "revision": 1,
                                  "source_requests": kd_dump(names), "component_scope": kd_dump(scope),
                                  "request_id": request_id, "items": []})
        else:
            doc.revision = int(doc.revision or 0) + 1
            if action in ("revise", "correct", "recut", "cancel"):
                kd_reason(payload)
        if action in ("approve", "revise"):
            if not keys:
                kd_error("لا توجد مصادر مكونات محددة لإنشاء الأمر أو تعديله.")
            if action == "revise":
                for key, row in before_active.items():
                    if row.state == "completed" and saved[key].get("record_kind") == "component" and key not in keys:
                        kd_error("لا يمكن استبعاد مكون تم قصه. استخدم تصحيح التسجيل إذا كان التنفيذ سُجل بالخطأ.")
            final_parts = {}
            for key in keys:
                old = before_active.get(key)
                if old and old.state == "completed":
                    for retained in [key] + saved[key].get("linked_piece_keys", []):
                        final_parts[retained] = saved[retained]
                    continue
                allocation = build["allocations"][key]
                if not allocation.get("eligible"):
                    kd_error(allocation.get("blocker") or "مصدر المكون غير صالح للاعتماد.")
                final_parts[key] = allocation
                for panel_key in allocation["linked_piece_keys"]:
                    final_parts[panel_key] = build["panels"][panel_key]
            for key in final_parts:
                if key in before_active and before_active[key].state == "completed":
                    continue
                kd_assert_available(key, states, legacy, doc.name or "")
            if action == "approve":
                kc_refresh(doc)
                doc.insert(ignore_permissions=True)
            for key, row in before_active.items():
                if key in final_parts:
                    continue
                before = kd_item_snapshot(row)
                row.state = "released"
                kd_piece_record(row, doc.name)
                kd_event(doc, "استبعاد", key, before, kd_item_snapshot(row), payload)
            for key, part in final_parts.items():
                old = before_active.get(key)
                if old and old.state == "completed":
                    continue
                before = kd_item_snapshot(old) if old else None
                row = old or doc.append("items", {"state": "reserved", "attempt": int((states.get(key) or {}).get("attempt") or 1)})
                kd_apply_part(row, part)
                kd_piece_record(row, doc.name)
                if before != kd_item_snapshot(row):
                    kd_event(doc, "تعديل" if old else ("اعتماد" if action == "approve" else "إضافة"), key, before, kd_item_snapshot(row), payload)
            if "allocation_keys" in scope:
                scope["allocation_keys"] = sorted(keys)
                scope["allocation_component_keys"] = list(scope["component_keys"])
            doc.component_scope = kd_dump(scope)
            doc.source_requests = kd_dump(names)
        elif action in ("complete", "correct", "recut"):
            for key in keys:
                row = before_active[key]
                before = saved[key]
                expected_state = "reserved" if action == "complete" else "completed"
                if row.state != expected_state:
                    kd_error("سبق تسجيل القص أو أن حالة المكون لا تسمح بهذا الإجراء.")
                changed_links = []
                current = build["allocations"].get(key) if build else None
                if action in ("complete", "recut"):
                    if not current or not current.get("eligible"):
                        kd_error("تغير مصدر المكون أو خامه أو أجزاؤه. راجع التخصيم قبل التنفيذ.")
                    if action == "complete" and current["source_signature"] != row.source_signature:
                        kd_error("تغيرت بيانات المكون أو كميته أو الباركود. عدّل الأمر قبل تسجيل القص.")
                    changed_links = current["linked_piece_keys"]
                old_links = before.get("linked_piece_keys") or []
                next_attempt = int(row.attempt or 1) + 1
                affected = [key] + old_links
                if action == "recut":
                    for removed in old_links:
                        if removed not in changed_links:
                            kd_error("تغيرت هوية حبات البنل بعد القص؛ لا يمكن تبديل الحبات المنفذة دون مراجعة المصدر.")
                    affected = [key] + changed_links
                for target in affected:
                    existing = before_active.get(target)
                    record = states.get(target)
                    if existing and (not record or record.current_order != doc.name):
                        kd_error("مصدر المكون أو حباته لم يعد ضمن النسخة الحالية للأمر.")
                    if existing and existing.state != expected_state:
                        kd_error("حالة المكون وحباته المرتبطة غير متسقة؛ لا يمكن تسجيل القص مرتين.")
                    if action == "recut":
                        kd_assert_available(target, states, legacy, doc.name)
                    if not existing and action != "recut":
                        kd_error("حبات المكون المرتبطة غير مكتملة. راجع الأمر.")
                    item_before = kd_item_snapshot(existing) if existing else None
                    target_row = existing or doc.append("items", {"state": "reserved", "attempt": int(row.attempt or 1)})
                    if action == "recut":
                        kd_apply_part(target_row, current if target == key else build["panels"][target])
                        target_row.attempt = int(target_row.attempt or 1) + 1 if existing else next_attempt
                    target_row.state = "completed" if action == "complete" else "reserved"
                    target_row.completed_at = frappe.utils.now_datetime() if action == "complete" else None
                    target_row.completed_by = frappe.session.user if action == "complete" else ""
                    kd_piece_record(target_row, doc.name)
                    kd_event(doc, "تم القص" if action == "complete" else ("إعادة قص" if action == "recut" else "تصحيح تسجيل"),
                             target, item_before, kd_item_snapshot(target_row), payload)
        elif action == "cancel":
            if not any(row.state == "reserved" for row in before_active.values()):
                kd_error("لا توجد مكونات غير مقصوصة لإلغائها.")
            for key, row in before_active.items():
                if row.state == "completed":
                    continue
                before = kd_item_snapshot(row)
                row.state = "released"
                kd_piece_record(row, doc.name)
                kd_event(doc, "إلغاء", key, before, kd_item_snapshot(row), payload)
        else:
            kd_error("إجراء مكونات غير معروف.")
        kc_refresh(doc)
        if action == "recut":
            revised_scope = kd_json(doc.component_scope, {})
            revised_scope["component_keys"] = sorted(set(kd_json(row.details, {}).get("component_key")
                                                          for row in doc.get("items") or []
                                                          if row.state != "released" and kd_json(row.details, {}).get("record_kind") == "component"))
            doc.component_scope = kd_dump(revised_scope)
        doc.save(ignore_permissions=True)
        result = kc_order_data(doc)
        if direct_create:
            status = kc_report_data(report_scope, build, kd_states(build["parts"]), legacy_references)
            result = kc_created_result(result, status, skipped, availability_changed)
        frappe.get_doc({"doctype": "KDT Cut Command", "request_id": request_id, "order": doc.name,
                        "action": command_action, "user": frappe.session.user,
                        "fingerprint": fingerprint, "response": kd_dump(result)}).insert(ignore_permissions=True)
        frappe.db.commit()
        return result
    except Exception:
        frappe.db.rollback()
        raise


def kc_print_data(payload):
    doc = kd_order(payload.get("name"))
    if doc.get("order_mode") != "components":
        kd_error("الأمر المحدد ليس أمر قص مكونات.")
    names = kd_json(doc.source_requests, [])
    frappe.db.commit()
    for name in sorted(names):
        frappe.db.sql("SELECT name FROM `tabMaterial Request` WHERE name=%s FOR UPDATE", (name,))
    kdt_lock_request_sources(names)
    frappe.db.sql("SELECT name,revision FROM `tabKDT Cut Order` WHERE name=%s FOR UPDATE", (doc.name,), as_dict=True)
    doc = kd_order(doc.name)
    if payload.get("revision") is not None and int(payload["revision"]) != int(doc.revision or 0):
        kd_error("تغير أمر القص. حدّث الأمر قبل الطباعة.")
    if doc.status == "ملغى":
        kd_error("لا يمكن طباعة أمر ملغى.")
    result = kc_order_data(doc)
    keys = kc_targets(payload, doc) if payload.get("allocation_keys") or payload.get("component_keys") else [item["piece_key"] for item in result["items"]]
    current = kc_build(kc_scope({}, doc), doc.name)
    chosen = [item for item in result["items"] if item["piece_key"] in keys]
    for item in chosen:
        latest = current["allocations"].get(item["piece_key"])
        if not latest or not latest.get("eligible") or latest["source_signature"] != item["source_signature"]:
            kd_error("تغير مصدر المكون أو مقاسه أو باركوده بعد الاعتماد. راجع التعديل قبل الطباعة.")
    machine_keys = kc_linked(keys, {item["piece_key"]: item for item in chosen})
    panels = [item for item in result["machine_items"] if item["piece_key"] in machine_keys]
    result["items"] = chosen
    result["component_rows"] = kc_rows(chosen)
    result["machine_items"] = panels
    result["machine_print_available"] = bool(panels)
    result["html"] = render_cut_labels(doc.name, panels) if panels else ""
    result["message"] = "" if panels else "المكونات المحددة ليس لها باركود KDT؛ يمكن طباعة كشف المكونات من الأمر."
    return result


def kc_dispatch(action, payload):
    if action not in ("preview", "approve", "create_available", "revise", "complete", "correct", "recut", "cancel", "print_data"):
        kd_error("إجراء المكونات غير معروف.")
    kd_permission("approve" if action == "create_available" else action)
    if action == "preview":
        return kc_preview(payload)
    if action == "print_data":
        return kc_print_data(payload)
    return kc_mutate(action, payload)



def dispatch(action="search", payload=None):
    """Execute one isolated request with the legacy API parsing rules."""
    action = str(action or "search").strip()
    payload = kd_json(payload, {})
    if not isinstance(payload, dict):
        kd_error("صيغة بيانات صفحة القص غير صحيحة.")
    state_token = _CANCELLATION_STATE.set(({}, {}))
    try:
        return kd_dispatch(action, payload)
    finally:
        _CANCELLATION_STATE.reset(state_token)
