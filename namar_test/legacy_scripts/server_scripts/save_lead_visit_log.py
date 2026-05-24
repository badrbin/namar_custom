# API: save_lead_visit_log

if frappe.session.user == "Guest":
    frappe.throw("يلزم تسجيل الدخول لحفظ سجل الزيارات", frappe.PermissionError)


LEGACY_STAGE_OPTIONS = (
    "جديد",
    "تمت الزيارة",
    "مهتم",
    "يحتاج عرض سعر",
    "بانتظار الرد",
    "متابعة لاحقة",
    "تم البيع",
    "مغلق - غير مناسب",
)


def clean_text(value):
    if value is None:
        return ""
    return str(value).strip()


def row_get(row, key):
    if row is None:
        return None
    try:
        return row.get(key)
    except Exception:
        pass
    try:
        return row[key]
    except Exception:
        return None


def get_user_roles():
    if frappe.session.user == "Administrator":
        return {"Administrator"}
    rows = frappe.get_all("Has Role", filters={"parent": frappe.session.user}, pluck="role", limit_page_length=0)
    return {clean_text(row) for row in rows if clean_text(row)}


def has_role_based_permission(doctype, ptype="read"):
    if frappe.session.user == "Administrator":
        return True
    user_roles = get_user_roles()
    if not user_roles:
        return False
    for perm in frappe.get_meta(doctype).permissions or []:
        role = clean_text(row_get(perm, "role"))
        if role and role in user_roles and int(row_get(perm, ptype) or 0):
            return True
    return False


def has_standard_doc_permission(doc, ptype="read"):
    if frappe.session.user == "Administrator":
        return True
    try:
        doc.check_permission(ptype)
        return True
    except frappe.PermissionError:
        return False


def get_shared_users(doc):
    users = []
    seen = {}
    for row in doc.get("custom_shared_users") or []:
        user_id = clean_text(row.get("shared_user"))
        if user_id and user_id not in seen:
            seen[user_id] = 1
            users.append(user_id)
    return users


def has_shared_lead_access(doc, ptype="read"):
    current_user = frappe.session.user
    if current_user not in get_shared_users(doc):
        return False
    return has_role_based_permission("Lead", ptype)


def assert_can_read_lead(doc):
    if has_standard_doc_permission(doc, "read"):
        return
    if has_shared_lead_access(doc, "read"):
        return
    frappe.throw("ليس لديك صلاحية لعرض هذا الليد", frappe.PermissionError)


def assert_can_manage_lead(doc):
    if has_standard_doc_permission(doc, "write"):
        return
    if has_shared_lead_access(doc, "write"):
        return
    frappe.throw("ليس لديك صلاحية لتحديث سجل زيارات هذا الليد", frappe.PermissionError)


def infer_status_from_stage(stage):
    stage = clean_text(stage)
    if not stage:
        return "Lead"
    if stage == "تم البيع":
        return "Converted"
    if stage == "مغلق - غير مناسب":
        return "Do Not Contact"
    if stage in ("مهتم", "يحتاج عرض سعر", "بانتظار الرد", "متابعة لاحقة"):
        return "Interested"
    if stage == "تمت الزيارة":
        return "Replied"
    return "Lead"


def get_default_stage():
    if frappe.db.exists("DocType", "Lead Sales Stage"):
        rows = frappe.get_all(
            "Lead Sales Stage",
            filters={"is_active": 1},
            fields=["name", "stage_name", "is_default", "sort_order"],
            order_by="is_default desc, sort_order asc, creation asc",
            limit_page_length=1,
        )
        if rows:
            return clean_text(rows[0].get("stage_name") or rows[0].get("name")) or "جديد"
    return "جديد"


def ensure_valid_stage(stage):
    stage = clean_text(stage)
    if not stage:
        return ""
    if frappe.db.exists("DocType", "Lead Sales Stage") and not frappe.db.exists("Lead Sales Stage", stage):
        frappe.throw("مرحلة البيع غير معرّفة في الإعدادات")
    return stage


def normalize_existing_stage(stage):
    stage = clean_text(stage)
    if not stage:
        return ""
    if frappe.db.exists("DocType", "Lead Sales Stage") and not frappe.db.exists("Lead Sales Stage", stage):
        return ""
    return stage


def get_stage_status(stage):
    stage = clean_text(stage)
    if stage and frappe.db.exists("DocType", "Lead Sales Stage") and frappe.db.exists("Lead Sales Stage", stage):
        mapped = clean_text(frappe.db.get_value("Lead Sales Stage", stage, "workflow_status"))
        if mapped:
            return mapped
    return infer_status_from_stage(stage)


def sync_legacy_stage_field(doc, stage):
    stage = clean_text(stage)
    if stage in LEGACY_STAGE_OPTIONS:
        doc.custom_sales_stage = stage


def create_note_comment(lead_doc, context_label, note_text):
    note_text = clean_text(note_text)
    if not note_text:
        return
    actor = clean_text(frappe.db.get_value("User", frappe.session.user, "full_name")) or frappe.session.user
    content = "\n".join([
        "ملاحظة من: " + actor,
        "المصدر: " + clean_text(context_label),
        "",
        note_text,
    ])
    frappe.get_doc({
        "doctype": "Comment",
        "comment_type": "Comment",
        "reference_doctype": "Lead",
        "reference_name": lead_doc.name,
        "content": content,
    }).insert(ignore_permissions=True)


mode = clean_text(frappe.form_dict.get("mode"))
lead_name = clean_text(frappe.form_dict.get("lead"))
log_name = clean_text(frappe.form_dict.get("name"))
visit_result = clean_text(frappe.form_dict.get("visit_result"))
visit_on = clean_text(frappe.form_dict.get("visit_on"))
sales_stage_after = clean_text(frappe.form_dict.get("sales_stage_after"))
next_action = clean_text(frappe.form_dict.get("next_action"))
next_follow_up_on = clean_text(frappe.form_dict.get("next_follow_up_on"))
visit_notes = clean_text(frappe.form_dict.get("visit_notes"))
visit_image = clean_text(frappe.form_dict.get("visit_image"))

if next_follow_up_on:
    frappe.utils.getdate(next_follow_up_on)

if mode == "update_attachment":
    if not log_name or not visit_image:
        frappe.throw("name and visit_image required for update_attachment")
    visit_doc = frappe.get_doc("Lead Field Visit", log_name)
    lead_doc = frappe.get_doc("Lead", visit_doc.lead)
    assert_can_read_lead(lead_doc)
    assert_can_manage_lead(lead_doc)
    visit_doc.visit_image = visit_image
    visit_doc.save(ignore_permissions=True)
    frappe.db.commit()
    frappe.response["message"] = {
        "success": True,
        "name": visit_doc.name,
        "mode": "update_attachment",
        "visit_image": visit_doc.visit_image,
    }
else:
    if not lead_name:
        frappe.throw("lead required")

    lead_doc = frappe.get_doc("Lead", lead_name)
    assert_can_read_lead(lead_doc)
    assert_can_manage_lead(lead_doc)
    stage_before = normalize_existing_stage(lead_doc.get("custom_sales_stage_link") or lead_doc.get("custom_sales_stage")) or clean_text(lead_doc.get("status")) or get_default_stage()
    requested_stage = ensure_valid_stage(sales_stage_after)
    stage_after = requested_stage or normalize_existing_stage(stage_before) or get_default_stage()
    visit_timestamp = frappe.utils.get_datetime(visit_on) if visit_on else frappe.utils.now_datetime()
    normalized_result = visit_result or clean_text(lead_doc.get("custom_last_visit_result")) or "تمت زيارة"

    visit_doc = frappe.new_doc("Lead Field Visit")
    visit_doc.lead = lead_doc.name
    visit_doc.visit_on = visit_timestamp
    visit_doc.visited_by = frappe.session.user
    visit_doc.visit_result = normalized_result
    visit_doc.sales_stage_before = stage_before
    visit_doc.sales_stage_after = stage_after
    visit_doc.next_action = next_action
    visit_doc.next_follow_up_on = next_follow_up_on
    visit_doc.visit_notes = visit_notes
    if visit_image:
        visit_doc.visit_image = visit_image
    visit_doc.save(ignore_permissions=True)

    lead_doc.custom_last_activity_on = visit_timestamp
    if requested_stage:
        lead_doc.custom_sales_stage_link = stage_after
        sync_legacy_stage_field(lead_doc, stage_after)
        lead_doc.status = get_stage_status(stage_after)
    if visit_result:
        lead_doc.custom_last_visit_result = normalized_result
    if next_action:
        lead_doc.custom_next_action = next_action
    if next_follow_up_on:
        lead_doc.custom_next_follow_up_on = next_follow_up_on
    if visit_notes:
        lead_doc.custom_map_notes = visit_notes
    lead_doc.save(ignore_permissions=True)
    if visit_notes:
        create_note_comment(lead_doc, "ملاحظة الزيارة", visit_notes)

    frappe.db.commit()

    frappe.response["message"] = {
        "success": True,
        "name": visit_doc.name,
        "mode": "create",
        "visit_log": {
            "name": visit_doc.name,
            "lead": visit_doc.lead,
            "visit_on": visit_doc.visit_on,
            "visited_by": visit_doc.visited_by,
            "visit_result": visit_doc.visit_result,
            "sales_stage_before": visit_doc.sales_stage_before,
            "sales_stage_after": visit_doc.sales_stage_after,
            "next_action": visit_doc.next_action,
            "next_follow_up_on": visit_doc.next_follow_up_on,
            "visit_notes": visit_doc.visit_notes,
            "visit_image": visit_doc.visit_image,
        },
        "lead": {
            "name": lead_doc.name,
            "custom_sales_stage": lead_doc.custom_sales_stage_link,
            "custom_sales_stage_link": lead_doc.custom_sales_stage_link,
            "status": lead_doc.status,
            "custom_last_visit_result": lead_doc.custom_last_visit_result,
            "custom_last_activity_on": lead_doc.custom_last_activity_on,
            "custom_next_action": lead_doc.custom_next_action,
            "custom_next_follow_up_on": lead_doc.custom_next_follow_up_on,
            "custom_map_notes": lead_doc.custom_map_notes,
        },
    }
