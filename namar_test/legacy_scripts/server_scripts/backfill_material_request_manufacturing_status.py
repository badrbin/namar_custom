cint = frappe.utils.cint
flt = frappe.utils.flt
now_datetime = frappe.utils.now_datetime


def is_trackable_row(item_row):
    row_item_name = item_row.item_name or ""
    raw_width = flt(item_row.get("عرض") or 0)
    raw_height = flt(item_row.get("طول") or 0)
    leaf_w = flt(item_row.custom_result_leaf_w or 0)
    leaf_h = flt(item_row.custom_result_leaf_h or 0)
    return ("باب" in row_item_name) or raw_width > 0 or raw_height > 0 or leaf_w > 0 or leaf_h > 0

limit = cint(frappe.form_dict.get("limit") or 500)
start = cint(frappe.form_dict.get("start") or 0)
mr_meta = frappe.get_meta("Material Request")
rows = frappe.get_all("Material Request", filters={"docstatus": ["!=", 2]}, fields=["name"], order_by="modified desc", limit_start=start, limit_page_length=limit)
processed = 0
updated = 0
for row in rows:
    material_request_name = row.name
    mr_doc = frappe.get_doc("Material Request", material_request_name)
    total_items = 0
    manufactured_count = 0
    for item_row in mr_doc.items:
        if not is_trackable_row(item_row):
            continue
        total_items += 1
        if cint(item_row.custom_is_manufactured):
            manufactured_count += 1
    remaining_count = total_items - manufactured_count
    manufacturing_status = "غير مصنع"
    completed_at = None
    completed_by = None
    if total_items and manufactured_count >= total_items:
        manufacturing_status = "مصنع بالكامل"
        if mr_meta.has_field("custom_manufacturing_completed_at"):
            completed_at = mr_doc.custom_manufacturing_completed_at or now_datetime()
        else:
            completed_at = now_datetime()
        if mr_meta.has_field("custom_manufacturing_completed_by"):
            completed_by = mr_doc.custom_manufacturing_completed_by or "Guest"
        else:
            completed_by = "Guest"
    elif manufactured_count:
        manufacturing_status = "قيد التصنيع"
    update_values = {}
    if mr_meta.has_field("custom_manufacturing_status"):
        if (mr_doc.custom_manufacturing_status or "") != manufacturing_status:
            update_values["custom_manufacturing_status"] = manufacturing_status
    if mr_meta.has_field("custom_manufacturing_remaining_count"):
        existing_remaining = mr_doc.custom_manufacturing_remaining_count
        if existing_remaining is None or cint(existing_remaining or 0) != remaining_count:
            update_values["custom_manufacturing_remaining_count"] = remaining_count
    if mr_meta.has_field("custom_manufacturing_completed_at"):
        if mr_doc.custom_manufacturing_completed_at != completed_at:
            update_values["custom_manufacturing_completed_at"] = completed_at
    if mr_meta.has_field("custom_manufacturing_completed_by"):
        if (mr_doc.custom_manufacturing_completed_by or "") != (completed_by or ""):
            update_values["custom_manufacturing_completed_by"] = completed_by
    if update_values:
        frappe.db.set_value("Material Request", material_request_name, update_values, update_modified=False)
        updated += 1
    processed += 1
if updated:
    frappe.db.commit()
frappe.response["message"] = {"processed": processed, "updated": updated, "next_start": start + processed}
