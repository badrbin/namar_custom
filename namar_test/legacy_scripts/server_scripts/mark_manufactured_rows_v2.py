cint = frappe.utils.cint
flt = frappe.utils.flt
now_datetime = frappe.utils.now_datetime


def clean_count(value):
    number = flt(value or 0)
    if abs(number - int(number)) < 0.000001:
        return int(number)
    return round(number, 3)


def has_dimension_text(item_row, fieldname):
    text = str(item_row.get(fieldname) or "").strip()
    return bool(text and text != "-")


def is_trackable_row(item_row):
    return (
        flt(item_row.get("custom_result_leaf_w") or 0) > 0
        or flt(item_row.get("custom_result_leaf_h") or 0) > 0
        or flt(item_row.get("custom_result_panel_w") or 0) > 0
        or flt(item_row.get("custom_result_panel_h") or 0) > 0
        or has_dimension_text(item_row, "custom_result_leaf_w_text")
        or has_dimension_text(item_row, "custom_result_panel_w_text")
    )


def get_row_qty(item_row):
    row_qty = flt(item_row.get("qty") or 0)
    if row_qty <= 0:
        row_qty = 1
    return row_qty


def get_row_manufactured_qty(item_row, row_qty, has_manufactured_qty_field):
    manufactured_qty = 0
    if has_manufactured_qty_field:
        manufactured_qty = flt(item_row.get("custom_manufactured_qty") or 0)
    if manufactured_qty <= 0 and cint(item_row.get("custom_is_manufactured") or 0):
        manufactured_qty = row_qty
    if manufactured_qty < 0:
        manufactured_qty = 0
    if manufactured_qty > row_qty:
        manufactured_qty = row_qty
    return manufactured_qty


DETAIL_DOCTYPE = "Material Request Manufacturing Detail"
DOOR_DOCTYPE = "Material Request Manufactured Door"
DOOR_PARENTFIELD = "custom_manufactured_doors"


def get_qty_status(required_qty, manufactured_qty):
    if required_qty and manufactured_qty >= required_qty:
        return "مكتمل"
    if manufactured_qty:
        return "جزئي"
    return "غير مصنع"


def parse_store_data(item_row):
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


def get_store_component_settings():
    settings = {}
    if not frappe.db.exists("DocType", "Store Component"):
        return settings

    meta = frappe.get_meta("Store Component")
    fields = ["name"]
    for fieldname in ("component_name", "label_ar", "custom_manufacturing_tracking_mode", "custom_required_for_delivery"):
        if meta.has_field(fieldname):
            fields.append(fieldname)

    for row in frappe.get_all("Store Component", fields=fields, limit_page_length=0):
        mode = row.get("custom_manufacturing_tracking_mode") or "مع الباب"
        if mode not in ("مع الباب", "مستقل", "لا يتتبع"):
            mode = "مع الباب"
        setting = {
            "tracking_mode": mode,
            "required_for_delivery": 1 if cint(row.get("custom_required_for_delivery", 1)) else 0,
            "label": row.get("label_ar") or row.get("component_name") or row.get("name"),
        }
        if row.get("name"):
            settings[row.get("name")] = setting
        if row.get("component_name"):
            settings[row.get("component_name")] = setting
    return settings


def get_component_setting(settings, component):
    return settings.get(component) or {
        "tracking_mode": "مع الباب",
        "required_for_delivery": 1,
        "label": component,
    }


def get_existing_manufacturing_details(material_request):
    if not frappe.db.exists("DocType", DETAIL_DOCTYPE):
        return {}
    rows = frappe.get_all(
        DETAIL_DOCTYPE,
        filters={"parent": material_request},
        fields=["source_key", "manufactured_qty", "manufactured_at", "manufactured_by"],
        limit_page_length=0,
    )
    return {row.get("source_key"): row for row in rows if row.get("source_key")}


def has_manufactured_door_tracking():
    return bool(frappe.db.exists("DocType", DOOR_DOCTYPE))


def get_existing_door_max_sequence(material_request, row_name):
    if not has_manufactured_door_tracking():
        return 0
    rows = frappe.db.sql(
        """
        SELECT MAX(IFNULL(door_sequence, 0)) AS max_sequence
        FROM `tabMaterial Request Manufactured Door`
        WHERE parent = %(parent)s
          AND parenttype = 'Material Request'
          AND parentfield = %(parentfield)s
          AND material_request_item = %(row_name)s
        """,
        {
            "parent": material_request,
            "parentfield": DOOR_PARENTFIELD,
            "row_name": row_name,
        },
        as_dict=True,
    )
    if not rows:
        return 0
    return cint(rows[0].get("max_sequence") or 0)


def get_existing_door_count(material_request, row_name):
    if not has_manufactured_door_tracking():
        return 0
    rows = frappe.db.sql(
        """
        SELECT COUNT(*) AS door_count
        FROM `tabMaterial Request Manufactured Door`
        WHERE parent = %(parent)s
          AND parenttype = 'Material Request'
          AND parentfield = %(parentfield)s
          AND material_request_item = %(row_name)s
        """,
        {
            "parent": material_request,
            "parentfield": DOOR_PARENTFIELD,
            "row_name": row_name,
        },
        as_dict=True,
    )
    if not rows:
        return 0
    return cint(rows[0].get("door_count") or 0)


def get_next_door_child_idx(material_request):
    if not has_manufactured_door_tracking():
        return 1
    rows = frappe.db.sql(
        """
        SELECT MAX(IFNULL(idx, 0)) AS max_idx
        FROM `tabMaterial Request Manufactured Door`
        WHERE parent = %(parent)s
          AND parenttype = 'Material Request'
          AND parentfield = %(parentfield)s
        """,
        {
            "parent": material_request,
            "parentfield": DOOR_PARENTFIELD,
        },
        as_dict=True,
    )
    if not rows:
        return 1
    return cint(rows[0].get("max_idx") or 0) + 1


def create_manufactured_door_records(material_request, row, current_qty, increment_qty, stamp, user, source):
    if not has_manufactured_door_tracking():
        return {"registered": [], "backfilled": []}

    door_count = int(flt(increment_qty or 0))
    if door_count <= 0:
        return {"registered": [], "backfilled": []}

    current_sequence = int(flt(current_qty or 0))
    existing_count = get_existing_door_count(material_request, row.name)
    existing_sequence = get_existing_door_max_sequence(material_request, row.name)
    next_idx = get_next_door_child_idx(material_request)
    backfilled_rows = []
    created_rows = []

    missing_existing_count = current_sequence - existing_count
    if missing_existing_count > 0:
        legacy_stamp = row.get("custom_manufactured_at") or stamp
        legacy_user = row.get("custom_manufactured_by") or user
        for offset in range(missing_existing_count):
            door_sequence = existing_sequence + offset + 1
            child = {
                "doctype": DOOR_DOCTYPE,
                "parent": material_request,
                "parenttype": "Material Request",
                "parentfield": DOOR_PARENTFIELD,
                "idx": next_idx + offset,
                "material_request_row": row.idx,
                "door_sequence": door_sequence,
                "material_request_item": row.name,
                "item_code": row.item_code,
                "item_name": row.item_name,
                "manufactured_at": legacy_stamp,
                "manufactured_by": legacy_user,
                "source": "سابق",
            }
            frappe.get_doc(child).db_insert()
            backfilled_rows.append(child)
        existing_sequence += missing_existing_count
        next_idx += missing_existing_count

    next_sequence = max(current_sequence, existing_sequence) + 1
    for offset in range(door_count):
        door_sequence = next_sequence + offset
        child = {
            "doctype": DOOR_DOCTYPE,
            "parent": material_request,
            "parenttype": "Material Request",
            "parentfield": DOOR_PARENTFIELD,
            "idx": next_idx + offset,
            "material_request_row": row.idx,
            "door_sequence": door_sequence,
            "material_request_item": row.name,
            "item_code": row.item_code,
            "item_name": row.item_name,
            "manufactured_at": stamp,
            "manufactured_by": user,
            "source": source,
        }
        frappe.get_doc(child).db_insert()
        created_rows.append(child)

    return {"registered": created_rows, "backfilled": backfilled_rows}


def is_material_request_delivered(mr_doc):
    dn_meta = frappe.get_meta("Delivery Note")
    if not dn_meta.has_field("custom_material_request"):
        return False

    required_by_item = {}
    for item_row in mr_doc.items:
        if not item_row.item_code:
            continue
        required_by_item[item_row.item_code] = required_by_item.get(item_row.item_code, 0) + flt(item_row.qty or 0)
    if not required_by_item:
        return False

    delivered_by_item = {}
    delivered_rows = frappe.db.sql(
        """
        SELECT dni.item_code, SUM(IFNULL(dni.qty, 0)) AS qty
        FROM `tabDelivery Note Item` dni
        INNER JOIN `tabDelivery Note` dn ON dn.name = dni.parent
        WHERE dn.docstatus = 1
          AND dn.custom_material_request = %s
        GROUP BY dni.item_code
        """,
        mr_doc.name,
        as_dict=True,
    )
    for row in delivered_rows:
        delivered_by_item[row.item_code] = flt(row.qty or 0)

    for item_code, required_qty in required_by_item.items():
        if required_qty > 0 and delivered_by_item.get(item_code, 0) + 0.000001 < required_qty:
            return False
    return True


def build_manufacturing_details(mr_doc, total_items, manufactured_count, remaining_count, has_manufactured_qty_field):
    mr_meta = frappe.get_meta("Material Request")
    if not mr_meta.has_field("custom_manufacturing_details") or not frappe.db.exists("DocType", DETAIL_DOCTYPE):
        return {
            "changed": False,
            "component_status": "",
            "component_total_count": 0,
            "component_manufactured_count": 0,
            "component_remaining_count": 0,
            "delivery_readiness_status": "",
            "delivery_readiness_summary": "",
            "manufacturing_details": [],
            "component_items": [],
            "pending_component_items": [],
        }

    settings = get_store_component_settings()
    existing_details = get_existing_manufacturing_details(mr_doc.name)
    detail_rows = []
    component_aggregate = {}

    for item_row in mr_doc.items:
        if not is_trackable_row(item_row):
            continue

        row_qty = get_row_qty(item_row)
        door_manufactured_qty = get_row_manufactured_qty(item_row, row_qty, has_manufactured_qty_field)
        door_remaining_qty = row_qty - door_manufactured_qty
        if door_remaining_qty < 0:
            door_remaining_qty = 0

        detail_rows.append({
            "line_type": "باب",
            "source_key": "door::%s" % (item_row.name or item_row.idx),
            "material_request_row": item_row.idx,
            "component_label": "باب",
            "component": "",
            "item_code": item_row.item_code,
            "item_name": item_row.item_name,
            "required_qty": clean_count(row_qty),
            "manufactured_qty": clean_count(door_manufactured_qty),
            "remaining_qty": clean_count(door_remaining_qty),
            "status": get_qty_status(row_qty, door_manufactured_qty),
            "tracking_mode": "مع الباب",
            "required_for_delivery": 1,
            "manufactured_at": item_row.get("custom_manufactured_at"),
            "manufactured_by": item_row.get("custom_manufactured_by"),
        })

        for store in parse_store_data(item_row):
            component = (store.get("component") or store.get("component_name") or "").strip()
            component_item_code = (store.get("item") or store.get("item_code") or "").strip()
            per_row_qty = flt(store.get("qty") or 0)
            if per_row_qty <= 0 or not component:
                continue

            setting = get_component_setting(settings, component)
            tracking_mode = setting.get("tracking_mode") or "مع الباب"
            if tracking_mode == "لا يتتبع":
                continue

            source_key = "component::%s::%s::%s" % (item_row.name or item_row.idx, component, component_item_code)
            required_qty = per_row_qty * row_qty
            if source_key not in component_aggregate:
                component_aggregate[source_key] = {
                    "line_type": "مكون",
                    "source_key": source_key,
                    "material_request_row": item_row.idx,
                    "component_label": store.get("component_ar") or setting.get("label") or component,
                    "component": component,
                    "item_code": component_item_code,
                    "item_name": store.get("item_name") or component_item_code,
                    "required_qty": 0,
                    "manufactured_qty": 0,
                    "tracking_mode": tracking_mode,
                    "required_for_delivery": 1 if setting.get("required_for_delivery") else 0,
                    "manufactured_at": item_row.get("custom_manufactured_at"),
                    "manufactured_by": item_row.get("custom_manufactured_by"),
                }
            component_row = component_aggregate[source_key]
            component_row["required_qty"] = flt(component_row.get("required_qty") or 0) + required_qty

            if tracking_mode == "مستقل":
                existing_row = existing_details.get(source_key) or {}
                component_row["manufactured_qty"] = flt(existing_row.get("manufactured_qty") or 0)
                component_row["manufactured_at"] = existing_row.get("manufactured_at")
                component_row["manufactured_by"] = existing_row.get("manufactured_by")
            else:
                ratio = door_manufactured_qty / row_qty if row_qty else 0
                component_row["manufactured_qty"] = flt(component_row.get("manufactured_qty") or 0) + (required_qty * ratio)

    for component_row in component_aggregate.values():
        required_qty = flt(component_row.get("required_qty") or 0)
        manufactured_qty = flt(component_row.get("manufactured_qty") or 0)
        if manufactured_qty > required_qty:
            manufactured_qty = required_qty
        if manufactured_qty < 0:
            manufactured_qty = 0
        remaining_qty = required_qty - manufactured_qty
        if remaining_qty < 0:
            remaining_qty = 0
        component_row["required_qty"] = clean_count(required_qty)
        component_row["manufactured_qty"] = clean_count(manufactured_qty)
        component_row["remaining_qty"] = clean_count(remaining_qty)
        component_row["status"] = get_qty_status(required_qty, manufactured_qty)
        detail_rows.append(component_row)

    component_total = 0
    component_manufactured = 0
    component_rows = []
    pending_component_rows = []
    for row in detail_rows:
        if row.get("line_type") != "مكون":
            continue
        component_rows.append(row)
        if flt(row.get("remaining_qty") or 0) > 0:
            pending_component_rows.append(row)
        if cint(row.get("required_for_delivery")):
            component_total += flt(row.get("required_qty") or 0)
            component_manufactured += flt(row.get("manufactured_qty") or 0)

    component_remaining = component_total - component_manufactured
    if component_remaining < 0:
        component_remaining = 0

    if component_total <= 0:
        component_status = "لا توجد مكونات"
    elif component_manufactured >= component_total:
        component_status = "مكتمل"
    elif component_manufactured:
        component_status = "جزئي"
    else:
        component_status = "غير مصنع"

    if is_material_request_delivered(mr_doc):
        delivery_status = "تم التوريد بالكامل"
    elif (remaining_count <= 0) and (component_remaining <= 0):
        delivery_status = "جاهز للتوريد"
    else:
        delivery_status = "غير جاهز"

    summary = "أبواب %s/%s | مكونات %s/%s" % (
        clean_count(manufactured_count),
        clean_count(total_items),
        clean_count(component_manufactured),
        clean_count(component_total),
    )

    existing_row_names = frappe.get_all(DETAIL_DOCTYPE, filters={"parent": mr_doc.name}, pluck="name", limit_page_length=0)
    for existing_row_name in existing_row_names:
        frappe.delete_doc(DETAIL_DOCTYPE, existing_row_name, ignore_permissions=True, force=True)
    for idx, row in enumerate(detail_rows, start=1):
        child = dict(row)
        child.update({
            "doctype": DETAIL_DOCTYPE,
            "parent": mr_doc.name,
            "parenttype": "Material Request",
            "parentfield": "custom_manufacturing_details",
            "idx": idx,
        })
        frappe.get_doc(child).db_insert()

    update_values = {}
    field_map = {
        "custom_component_manufacturing_status": component_status,
        "custom_component_manufacturing_remaining_count": clean_count(component_remaining),
        "custom_component_manufacturing_total_count": clean_count(component_total),
        "custom_delivery_readiness_status": delivery_status,
        "custom_delivery_readiness_summary": summary,
    }
    for fieldname, value in field_map.items():
        if mr_meta.has_field(fieldname) and (mr_doc.get(fieldname) or "") != value:
            update_values[fieldname] = value

    if update_values:
        frappe.db.set_value("Material Request", mr_doc.name, update_values, update_modified=False)

    return {
        "changed": True,
        "component_status": component_status,
        "component_total_count": clean_count(component_total),
        "component_manufactured_count": clean_count(component_manufactured),
        "component_remaining_count": clean_count(component_remaining),
        "delivery_readiness_status": delivery_status,
        "delivery_readiness_summary": summary,
        "manufacturing_details": detail_rows,
        "component_items": component_rows,
        "pending_component_items": pending_component_rows,
    }


def build_row_quantity_payload(row, row_qty, manufactured_qty, remaining_qty):
    return {
        "idx": row.idx,
        "name": row.name,
        "item_code": row.item_code,
        "item_name": row.item_name,
        "qty": clean_count(row_qty),
        "manufactured_qty": clean_count(manufactured_qty),
        "remaining_qty": clean_count(remaining_qty),
        "is_fully_manufactured": 1 if manufactured_qty >= row_qty else 0,
        "manufactured_at": row.custom_manufactured_at,
        "manufactured_by": row.custom_manufactured_by,
    }


def sync_material_request_manufacturing_status(material_request_name, completed_by):
    mr_doc = frappe.get_doc("Material Request", material_request_name)
    mr_meta = frappe.get_meta("Material Request")
    item_meta = frappe.get_meta("Material Request Item")
    has_manufactured_qty_field = item_meta.has_field("custom_manufactured_qty")
    total_items = 0
    manufactured_count = 0

    for item_row in mr_doc.items:
        if not is_trackable_row(item_row):
            continue
        row_qty = get_row_qty(item_row)
        total_items += row_qty
        manufactured_count += get_row_manufactured_qty(item_row, row_qty, has_manufactured_qty_field)

    remaining_count = total_items - manufactured_count
    if remaining_count < 0:
        remaining_count = 0
    completion_percent = round((manufactured_count * 100.0) / total_items, 1) if total_items else 0

    manufacturing_status = "غير مصنع"
    completed_at = None
    completed_user = None

    if total_items and manufactured_count >= total_items:
        manufacturing_status = "مصنع بالكامل"
        if mr_meta.has_field("custom_manufacturing_completed_at"):
            completed_at = mr_doc.custom_manufacturing_completed_at or now_datetime()
        else:
            completed_at = now_datetime()
        if mr_meta.has_field("custom_manufacturing_completed_by"):
            completed_user = mr_doc.custom_manufacturing_completed_by or completed_by or "Guest"
        else:
            completed_user = completed_by or "Guest"
    elif manufactured_count:
        manufacturing_status = "قيد التصنيع"

    update_values = {}
    if mr_meta.has_field("custom_manufacturing_status"):
        if (mr_doc.custom_manufacturing_status or "") != manufacturing_status:
            update_values["custom_manufacturing_status"] = manufacturing_status
    if mr_meta.has_field("custom_manufacturing_remaining_count"):
        existing_remaining_count = flt(mr_doc.custom_manufacturing_remaining_count or 0)
        if existing_remaining_count != remaining_count:
            update_values["custom_manufacturing_remaining_count"] = clean_count(remaining_count)
    if mr_meta.has_field("custom_manufacturing_total_count"):
        existing_total_count = flt(mr_doc.get("custom_manufacturing_total_count") or 0)
        if existing_total_count != total_items:
            update_values["custom_manufacturing_total_count"] = clean_count(total_items)
    if mr_meta.has_field("custom_manufacturing_completed_at"):
        if mr_doc.custom_manufacturing_completed_at != completed_at:
            update_values["custom_manufacturing_completed_at"] = completed_at
    if mr_meta.has_field("custom_manufacturing_completed_by"):
        if (mr_doc.custom_manufacturing_completed_by or "") != (completed_user or ""):
            update_values["custom_manufacturing_completed_by"] = completed_user

    component_data = build_manufacturing_details(
        mr_doc,
        total_items,
        manufactured_count,
        remaining_count,
        has_manufactured_qty_field,
    )

    if update_values:
        frappe.db.set_value("Material Request", material_request_name, update_values, update_modified=False)

    return {
        "manufacturing_status": manufacturing_status,
        "manufacturing_completed_at": completed_at,
        "manufacturing_completed_by": completed_user,
        "manufacturing_total_items": clean_count(total_items),
        "manufactured_count": clean_count(manufactured_count),
        "remaining_count": clean_count(remaining_count),
        "completion_percent": completion_percent,
        "component_status": component_data.get("component_status"),
        "component_total_count": component_data.get("component_total_count"),
        "component_manufactured_count": component_data.get("component_manufactured_count"),
        "component_remaining_count": component_data.get("component_remaining_count"),
        "delivery_readiness_status": component_data.get("delivery_readiness_status"),
        "delivery_readiness_summary": component_data.get("delivery_readiness_summary"),
        "manufacturing_details": component_data.get("manufacturing_details") or [],
        "component_items": component_data.get("component_items") or [],
        "pending_component_items": component_data.get("pending_component_items") or [],
        "changed": bool(update_values) or component_data.get("changed"),
    }


def add_row_request(row_requests, row_idx, qty):
    row_idx = cint(row_idx)
    if not row_idx:
        return
    request_qty = int(flt(qty or 1))
    if request_qty <= 0:
        request_qty = 1
    row_requests[row_idx] = row_requests.get(row_idx, 0) + request_qty


def add_row_request_chunk(row_requests, chunk):
    text = (chunk or "").strip()
    if not text:
        return
    separator = ":" if ":" in text else "x" if "x" in text else "*" if "*" in text else ""
    if separator:
        parts = text.split(separator, 1)
        add_row_request(row_requests, parts[0], parts[1])
    else:
        add_row_request(row_requests, text, 1)


material_request = (frappe.form_dict.get("mr") or "").strip()
rows_raw = frappe.form_dict.get("selected_rows") or frappe.form_dict.get("rows")
args_raw = frappe.form_dict.get("args")

if (not material_request or rows_raw is None) and args_raw:
    try:
        parsed_args = frappe.parse_json(args_raw) or {}
    except Exception:
        parsed_args = {}
    if not material_request:
        material_request = (parsed_args.get("mr") or "").strip()
    if rows_raw is None:
        rows_raw = parsed_args.get("selected_rows") or parsed_args.get("rows")

if not material_request:
    frappe.throw("اسم طلب المواد مطلوب")

if not material_request.startswith("MREQ-"):
    material_request = "MREQ-" + material_request

row_requests = {}
if isinstance(rows_raw, str):
    raw_text = rows_raw.strip()
    if raw_text.startswith("["):
        try:
            parsed_rows = frappe.parse_json(raw_text) or []
        except Exception:
            parsed_rows = []
        for value in parsed_rows:
            if isinstance(value, dict):
                add_row_request(row_requests, value.get("idx") or value.get("row"), value.get("qty") or 1)
            else:
                add_row_request(row_requests, value, 1)
    else:
        for chunk in raw_text.replace("،", ",").split(","):
            add_row_request_chunk(row_requests, chunk)
elif isinstance(rows_raw, (list, tuple)):
    for value in rows_raw:
        if isinstance(value, dict):
            add_row_request(row_requests, value.get("idx") or value.get("row"), value.get("qty") or 1)
        else:
            add_row_request(row_requests, value, 1)

if not row_requests:
    frappe.throw("حدد سطرًا واحدًا على الأقل")

mr_workflow_state = frappe.db.get_value("Material Request", material_request, "workflow_state") or ""
item_meta = frappe.get_meta("Material Request Item")
has_manufactured_qty_field = item_meta.has_field("custom_manufactured_qty")
stamp = now_datetime()
user = (frappe.session.user if frappe.session.user and frappe.session.user != "Guest" else "Guest")

updated_rows = []
already_done_rows = []
missing_rows = []
skipped_rows = []
registered_qty = 0

fields = [
    "name",
    "idx",
    "item_code",
    "item_name",
    "qty",
    "custom_is_manufactured",
    "custom_manufactured_at",
    "custom_manufactured_by",
    "custom_result_leaf_w",
    "custom_result_leaf_h",
    "custom_result_panel_w",
    "custom_result_panel_h",
    "custom_result_leaf_w_text",
    "custom_result_panel_w_text",
]
if has_manufactured_qty_field:
    fields.append("custom_manufactured_qty")

for row_idx, request_qty in row_requests.items():
    row = frappe.db.get_value(
        "Material Request Item",
        {"parent": material_request, "idx": row_idx},
        fields,
        as_dict=1,
    )
    if not row:
        missing_rows.append(row_idx)
        continue

    if not is_trackable_row(row):
        skipped_rows.append({
            "idx": row.idx,
            "name": row.name,
            "item_code": row.item_code,
            "item_name": row.item_name,
            "reason": "لا توجد مقاسات درفة أو بانل",
        })
        continue

    row_qty = get_row_qty(row)
    current_qty = get_row_manufactured_qty(row, row_qty, has_manufactured_qty_field)
    remaining_qty = row_qty - current_qty
    if remaining_qty <= 0:
        already_done_rows.append(build_row_quantity_payload(row, row_qty, current_qty, 0))
        continue

    increment_qty = request_qty
    if increment_qty > remaining_qty:
        increment_qty = remaining_qty
    increment_qty = int(flt(increment_qty or 0))
    if increment_qty <= 0:
        already_done_rows.append(build_row_quantity_payload(row, row_qty, current_qty, remaining_qty))
        continue
    new_qty = current_qty + increment_qty
    new_remaining_qty = row_qty - new_qty
    if new_remaining_qty < 0:
        new_remaining_qty = 0

    door_data = create_manufactured_door_records(
        material_request,
        row,
        current_qty,
        increment_qty,
        stamp,
        user,
        "يدوي",
    )
    door_records = door_data.get("registered") or []
    backfilled_records = door_data.get("backfilled") or []

    row_doc = frappe.get_doc("Material Request Item", row.name)
    if has_manufactured_qty_field:
        row_doc.custom_manufactured_qty = clean_count(new_qty)
    row_doc.custom_is_manufactured = 1 if new_qty >= row_qty else 0
    row_doc.custom_manufactured_at = stamp
    row_doc.custom_manufactured_by = user
    row_doc.db_update()
    registered_qty += increment_qty

    row.custom_manufactured_at = stamp
    row.custom_manufactured_by = user
    updated_rows.append(build_row_quantity_payload(row, row_qty, new_qty, new_remaining_qty))
    updated_rows[-1]["registered_qty"] = clean_count(increment_qty)
    updated_rows[-1]["registered_doors"] = len(door_records)
    updated_rows[-1]["backfilled_doors"] = len(backfilled_records)
    updated_rows[-1]["door_sequences"] = [door.get("door_sequence") for door in door_records]

manufacturing_data = sync_material_request_manufacturing_status(material_request, user)

if updated_rows or manufacturing_data.get("changed"):
    frappe.db.commit()

frappe.response["message"] = {
    "status": "done" if updated_rows else "no_change",
    "material_request": material_request,
    "workflow_state": mr_workflow_state,
    "manufacturing_status": manufacturing_data.get("manufacturing_status"),
    "manufacturing_completed_at": manufacturing_data.get("manufacturing_completed_at"),
    "manufacturing_completed_by": manufacturing_data.get("manufacturing_completed_by"),
    "manufacturing_total_items": manufacturing_data.get("manufacturing_total_items"),
    "manufactured_count": manufacturing_data.get("manufactured_count"),
    "remaining_count": manufacturing_data.get("remaining_count"),
    "completion_percent": manufacturing_data.get("completion_percent"),
    "component_status": manufacturing_data.get("component_status"),
    "component_total_count": manufacturing_data.get("component_total_count"),
    "component_manufactured_count": manufacturing_data.get("component_manufactured_count"),
    "component_remaining_count": manufacturing_data.get("component_remaining_count"),
    "delivery_readiness_status": manufacturing_data.get("delivery_readiness_status"),
    "delivery_readiness_summary": manufacturing_data.get("delivery_readiness_summary"),
    "manufacturing_details": manufacturing_data.get("manufacturing_details") or [],
    "component_items": manufacturing_data.get("component_items") or [],
    "pending_component_items": manufacturing_data.get("pending_component_items") or [],
    "updated_count": len(updated_rows),
    "registered_qty": clean_count(registered_qty),
    "already_done_count": len(already_done_rows),
    "missing_count": len(missing_rows),
    "skipped_count": len(skipped_rows),
    "updated_rows": updated_rows,
    "already_done_rows": already_done_rows,
    "missing_rows": missing_rows,
    "skipped_rows": skipped_rows,
}
