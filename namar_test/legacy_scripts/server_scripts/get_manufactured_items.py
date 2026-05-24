cint = frappe.utils.cint
flt = frappe.utils.flt


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
        stores = list(parsed)
    except Exception:
        return []
    return stores


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

        door_key = "door::%s" % (item_row.name or item_row.idx)
        detail_rows.append({
            "line_type": "باب",
            "source_key": door_key,
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

        stores = parse_store_data(item_row)
        for store in stores:
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


def sync_material_request_fields(mr_doc, total_items, manufactured_count, remaining_count):
    mr_meta = frappe.get_meta("Material Request")
    item_meta = frappe.get_meta("Material Request Item")
    has_manufactured_qty_field = item_meta.has_field("custom_manufactured_qty")
    manufacturing_status = "غير مصنع"
    completed_at = None
    completed_user = None

    if total_items and manufactured_count >= total_items:
        manufacturing_status = "مصنع بالكامل"
        completed_at = mr_doc.custom_manufacturing_completed_at or frappe.utils.now_datetime()
        completed_user = mr_doc.custom_manufacturing_completed_by or frappe.session.user or "Guest"
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
        frappe.db.set_value("Material Request", mr_doc.name, update_values, update_modified=False)
    if update_values or component_data.get("changed"):
        frappe.db.commit()

    component_data["manufacturing_status"] = manufacturing_status
    return component_data


material_request = (frappe.form_dict.get("mr") or "").strip()
if material_request and not material_request.startswith("MREQ-"):
    material_request = "MREQ-" + material_request

if not material_request:
    frappe.response["message"] = {
        "items": [],
        "pending_items": [],
        "total_items": 0,
        "manufactured_count": 0,
        "remaining_count": 0,
        "completion_percent": 0,
        "workflow_state": "",
    }
else:
    mr_doc = frappe.get_doc("Material Request", material_request)
    item_meta = frappe.get_meta("Material Request Item")
    has_manufactured_qty_field = item_meta.has_field("custom_manufactured_qty")
    items = []
    pending_items = []
    total_items = 0
    manufactured_count = 0

    for row in mr_doc.items:
        if not is_trackable_row(row):
            continue

        row_qty = get_row_qty(row)
        manufactured_qty = get_row_manufactured_qty(row, row_qty, has_manufactured_qty_field)
        remaining_qty = row_qty - manufactured_qty
        if remaining_qty < 0:
            remaining_qty = 0

        total_items += row_qty
        manufactured_count += manufactured_qty

        row_data = {
            "row": row.idx,
            "item_code": row.item_code,
            "item_name": row.item_name,
            "qty": clean_count(row_qty),
            "manufactured_qty": clean_count(manufactured_qty),
            "remaining_qty": clean_count(remaining_qty),
            "is_fully_manufactured": 1 if manufactured_qty >= row_qty else 0,
            "manufactured_at": row.custom_manufactured_at,
            "manufactured_by": row.custom_manufactured_by,
        }
        if manufactured_qty > 0:
            items.append(row_data)
        if remaining_qty > 0:
            pending_items.append(row_data)

    remaining_count = total_items - manufactured_count
    if remaining_count < 0:
        remaining_count = 0
    completion_percent = 0
    if total_items:
        completion_percent = round((manufactured_count * 100.0) / total_items, 1)
    manufacturing_data = sync_material_request_fields(mr_doc, total_items, manufactured_count, remaining_count)

    frappe.response["message"] = {
        "material_request": material_request,
        "workflow_state": mr_doc.workflow_state,
        "manufacturing_status": manufacturing_data.get("manufacturing_status"),
        "items": items,
        "pending_items": pending_items,
        "total_items": clean_count(total_items),
        "manufactured_count": clean_count(manufactured_count),
        "remaining_count": clean_count(remaining_count),
        "completion_percent": completion_percent,
        "component_status": manufacturing_data.get("component_status"),
        "component_total_count": manufacturing_data.get("component_total_count"),
        "component_manufactured_count": manufacturing_data.get("component_manufactured_count"),
        "component_remaining_count": manufacturing_data.get("component_remaining_count"),
        "delivery_readiness_status": manufacturing_data.get("delivery_readiness_status"),
        "delivery_readiness_summary": manufacturing_data.get("delivery_readiness_summary"),
        "manufacturing_details": manufacturing_data.get("manufacturing_details") or [],
        "component_items": manufacturing_data.get("component_items") or [],
        "pending_component_items": manufacturing_data.get("pending_component_items") or [],
    }
