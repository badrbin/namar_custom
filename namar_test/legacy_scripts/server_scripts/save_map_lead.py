# API: save_map_lead

if frappe.session.user == "Guest":
    frappe.throw("يلزم تسجيل الدخول لحفظ بيانات البيع الميداني", frappe.PermissionError)


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


def parse_list(value):
    if not value:
        return []
    if isinstance(value, list):
        return value
    try:
        parsed = frappe.parse_json(value)
        if isinstance(parsed, list):
            return parsed
    except Exception:
        pass
    value = clean_text(value)
    return [value] if value else []


def normalize_mobile(value):
    value = clean_text(value)
    if not value:
        return ""
    digits = "".join(ch for ch in value if ch.isdigit())
    if digits.startswith("966") and len(digits) == 12:
        digits = "0" + digits[3:]
    elif len(digits) == 9:
        digits = "0" + digits
    if digits and len(digits) < 9:
        frappe.throw("رقم الجوال غير صالح")
    return digits


def parse_float(value, label):
    value = clean_text(value)
    if not value:
        return None
    try:
        return float(value)
    except Exception:
        frappe.throw(label + " غير صالح")


def parse_non_negative_int(value, label):
    value = clean_text(value)
    if not value:
        return None
    if value.startswith("-"):
        frappe.throw(label + " يجب أن يكون عددًا صحيحًا بدون كسور")
    if not value.isdigit():
        frappe.throw(label + " يجب أن يكون عددًا صحيحًا بدون كسور")
    return int(value)


def require_text(value, label):
    value = clean_text(value)
    if not value:
        frappe.throw(label + " مطلوب")
    return value


def normalize_spaces(value):
    value = clean_text(value)
    if not value:
        return ""
    return " ".join(value.split())


def lowercase_ascii(value):
    out = []
    for ch in clean_text(value):
        code = ord(ch)
        if 65 <= code <= 90:
            out.append(chr(code + 32))
        else:
            out.append(ch)
    return "".join(out)


def percent_decode(value):
    value = clean_text(value)
    if not value:
        return ""
    out = []
    i = 0
    length = len(value)
    while i < length:
        ch = value[i]
        if ch == "+":
            out.append(" ")
            i += 1
            continue
        if ch == "%" and i + 2 < length:
            code = value[i + 1 : i + 3]
            try:
                out.append(chr(int(code, 16)))
                i += 3
                continue
            except Exception:
                pass
        out.append(ch)
        i += 1
    return "".join(out)


def normalize_google_map_link(value):
    value = lowercase_ascii(normalize_spaces(value))
    if not value:
        return ""
    while value.endswith("/"):
        value = value[:-1]
    return value


def extract_google_map_address(value):
    value = clean_text(value)
    if not value:
        return ""
    text = value
    query_pos = text.find("query=")
    if query_pos >= 0:
        query_text = text[query_pos + 6 :]
        amp_pos = query_text.find("&")
        if amp_pos >= 0:
            query_text = query_text[:amp_pos]
        text = percent_decode(query_text)
    else:
        text = percent_decode(text)
    text = text.replace("،", " ")
    text = text.replace(",", " ")
    text = normalize_spaces(lowercase_ascii(text))
    return text


def extract_google_map_address_from_notes(notes):
    notes = clean_text(notes)
    if not notes:
        return ""
    for line in notes.splitlines():
        line = clean_text(line)
        if not line:
            continue
        lower_line = lowercase_ascii(line)
        prefixes = (
            "original google map address:",
            "original map address:",
            "original google map url:",
            "original map url:",
        )
        for prefix in prefixes:
            if lower_line.startswith(prefix):
                return extract_google_map_address(line[len(prefix) :].strip())
    return ""


def assert_unique_google_map(doc_name, google_map):
    normalized_link = normalize_google_map_link(google_map)
    normalized_address = extract_google_map_address(google_map)
    if not normalized_link and not normalized_address:
        return

    rows = frappe.get_all(
        "Lead",
        filters={"custom_google_map": ["is", "set"]},
        fields=["name", "lead_name", "custom_google_map", "custom_map_notes"],
        limit_page_length=0,
    )
    for row in rows:
        row_name = clean_text(row.get("name"))
        if doc_name and row_name == doc_name:
            continue
        existing_map = clean_text(row.get("custom_google_map"))
        if not existing_map:
            continue
        existing_link = normalize_google_map_link(existing_map)
        if normalized_link and existing_link and existing_link == normalized_link:
            lead_label = clean_text(row.get("lead_name")) or row_name
            frappe.throw("رابط قوقل ماب مستخدم مسبقًا في الليد " + lead_label)
        existing_address = extract_google_map_address(existing_map)
        if normalized_address and existing_address and existing_address == normalized_address:
            lead_label = clean_text(row.get("lead_name")) or row_name
            frappe.throw("عنوان قوقل ماب مستخدم مسبقًا في الليد " + lead_label)
        note_address = extract_google_map_address_from_notes(row.get("custom_map_notes"))
        if normalized_address and note_address and note_address == normalized_address:
            lead_label = clean_text(row.get("lead_name")) or row_name
            frappe.throw("عنوان قوقل ماب مستخدم مسبقًا في الليد " + lead_label)


def decode_map_text(value):
    value = clean_text(value)
    if not value:
        return ""
    value = value.replace("%2B", "+").replace("%2b", "+")
    value = value.replace("%2C", ",").replace("%2c", ",")
    value = value.replace("%20", " ").replace("+", " ")
    return value.strip()


def is_valid_map_coord(lat, lng):
    return lat is not None and lng is not None and 15 <= lat <= 33 and 34 <= lng <= 57


def try_parse_coord_pair(text):
    parts = clean_text(text).split(",")
    if len(parts) != 2:
        return None
    try:
        lat_value = float(parts[0].strip())
        lng_value = float(parts[1].strip())
    except Exception:
        return None
    if not is_valid_map_coord(lat_value, lng_value):
        return None
    return {"lat": lat_value, "lng": lng_value}


def find_url_param(text, param):
    marker = param + "="
    idx = text.find(marker)
    if idx < 0:
        return ""
    rest = text[idx + len(marker) :]
    for delim in ("&", "#", " ", ";"):
        pos = rest.find(delim)
        if pos >= 0:
            rest = rest[:pos]
    return clean_text(rest)


def extract_3d4d_coords(text):
    idx = text.find("!3d")
    if idx < 0:
        return None
    rest = text[idx + 3 :]
    end = 0
    for ch in rest:
        if ch in "0123456789.-":
            end = end + 1
        else:
            break
    if end <= 0:
        return None
    lat_text = rest[:end]
    rest = rest[end:]
    if not rest.startswith("!4d"):
        return None
    rest = rest[3:]
    end = 0
    for ch in rest:
        if ch in "0123456789.-":
            end = end + 1
        else:
            break
    if end <= 0:
        return None
    return try_parse_coord_pair(lat_text + "," + rest[:end])


def extract_coords_from_google_text(value):
    text = decode_map_text(value)
    if not text:
        return None

    pair = try_parse_coord_pair(text)
    if pair:
        return pair

    for param in ("q", "query", "destination", "daddr", "ll", "saddr"):
        pair = try_parse_coord_pair(decode_map_text(find_url_param(text, param)))
        if pair:
            return pair

    at_idx = text.find("@")
    if at_idx >= 0:
        rest = text[at_idx + 1 :]
        end = 0
        for ch in rest:
            if ch in "0123456789.,-":
                end = end + 1
            else:
                break
        if end > 0:
            pair = try_parse_coord_pair(rest[:end])
            if pair:
                return pair

    pair = extract_3d4d_coords(text)
    if pair:
        return pair

    return None


def resolve_google_map_coords(google_map):
    google_map = clean_text(google_map)
    if not google_map:
        return None

    direct = extract_coords_from_google_text(google_map)
    if direct:
        return direct

    if "goo.gl" not in google_map and "maps.app" not in google_map:
        return None

    try:
        response_text = frappe.make_get_request(google_map, headers={"User-Agent": "Mozilla/5.0"})
        if response_text:
            return extract_coords_from_google_text(str(response_text))
    except Exception:
        pass
    return None


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


def resolve_valid_territory(value, fallback=""):
    for candidate in (value, fallback):
        candidate = clean_text(candidate)
        if candidate and frappe.db.exists("Territory", candidate):
            return candidate
    return ""


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
    frappe.throw("ليس لديك صلاحية لتعديل هذا الليد", frappe.PermissionError)


def assert_can_create_lead():
    if has_role_based_permission("Lead", "create"):
        return
    frappe.throw("ليس لديك صلاحية لإنشاء ليد جديد", frappe.PermissionError)


def assert_can_share_lead(doc):
    if has_standard_doc_permission(doc, "share"):
        return
    if has_shared_lead_access(doc, "share"):
        return
    frappe.throw("ليس لديك صلاحية لمشاركة هذا الليد", frappe.PermissionError)


def parse_shared_users(value, owner=""):
    owner = clean_text(owner)
    rows = parse_list(value)
    users = []
    seen = {}
    for item in rows:
        if isinstance(item, dict):
            user_id = clean_text(item.get("user") or item.get("shared_user") or item.get("name"))
        else:
            user_id = clean_text(item)
        if not user_id or user_id == owner or user_id in ("Guest",):
            continue
        if user_id in seen:
            continue
        if not frappe.db.exists("User", user_id):
            frappe.throw("المستخدم " + user_id + " غير موجود")
        enabled = frappe.db.get_value("User", user_id, "enabled")
        user_type = clean_text(frappe.db.get_value("User", user_id, "user_type"))
        if not enabled or user_type != "System User":
            frappe.throw("لا يمكن إضافة المستخدم " + user_id)
        seen[user_id] = 1
        users.append(user_id)
    return users


def rebuild_shared_users(doc, shared_users):
    existing = {}
    for row in doc.get("custom_shared_users") or []:
        user_id = clean_text(row.get("shared_user"))
        if user_id and user_id not in existing:
            existing[user_id] = row
    now_value = frappe.utils.now_datetime()
    doc.set("custom_shared_users", [])
    for user_id in shared_users:
        row = existing.get(user_id)
        doc.append(
            "custom_shared_users",
            {
                "shared_user": user_id,
                "added_by": clean_text(row.get("added_by")) if row else frappe.session.user,
                "added_on": row.get("added_on") if row and row.get("added_on") else now_value,
            },
        )


def serialize_shared_users(doc):
    users = []
    full_names = {}
    for user_id in get_shared_users(doc):
        if user_id not in full_names:
            full_names[user_id] = clean_text(frappe.db.get_value("User", user_id, "full_name")) or user_id
        users.append({"user": user_id, "full_name": full_names[user_id]})
    return users


def resolve_owner_user(value="", fallback=""):
    owner = clean_text(value) or clean_text(fallback) or frappe.session.user
    if owner in ("", "Guest"):
        owner = frappe.session.user
    if not frappe.db.exists("User", owner):
        frappe.throw("المستخدم " + owner + " غير موجود")
    enabled = frappe.db.get_value("User", owner, "enabled")
    user_type = clean_text(frappe.db.get_value("User", owner, "user_type"))
    if not enabled or user_type != "System User":
        frappe.throw("لا يمكن تعيين المالك " + owner)
    return owner


def ensure_owner_field(doc, explicit_owner=""):
    owner_user = resolve_owner_user(explicit_owner, clean_text(doc.get("owner")) or clean_text(doc.get("lead_owner")))
    doc.owner = owner_user
    return owner_user


def finalize_owner_fields(doc, owner_user, clear_legacy=False):
    owner_user = clean_text(owner_user)
    if owner_user and clean_text(doc.get("owner")) != owner_user:
        frappe.db.set_value("Lead", doc.name, "owner", owner_user, update_modified=False)
        doc.owner = owner_user
    if clear_legacy and clean_text(doc.get("lead_owner")):
        frappe.db.set_value("Lead", doc.name, "lead_owner", "", update_modified=False)
        doc.lead_owner = ""


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
name = clean_text(frappe.form_dict.get("name"))
lead_name = clean_text(frappe.form_dict.get("lead_name"))
owner_input = clean_text(frappe.form_dict.get("owner") or frappe.form_dict.get("lead_owner"))
company_name = clean_text(frappe.form_dict.get("company_name"))
mobile_no = normalize_mobile(frappe.form_dict.get("mobile_no") or frappe.form_dict.get("phone"))
email_id = clean_text(frappe.form_dict.get("email_id"))
city = clean_text(frappe.form_dict.get("city"))
territory = clean_text(frappe.form_dict.get("territory") or city)
notes = clean_text(frappe.form_dict.get("notes") or frappe.form_dict.get("custom_map_notes"))
follow_up = clean_text(frappe.form_dict.get("custom_next_follow_up_on"))
google_map = clean_text(frappe.form_dict.get("google_map"))
sales_stage = clean_text(frappe.form_dict.get("custom_sales_stage") or frappe.form_dict.get("sales_stage"))
business_type = clean_text(frappe.form_dict.get("custom_business_type"))
door_count = parse_non_negative_int(frappe.form_dict.get("custom_door_count"), "عدد الأبواب")
contact_person = clean_text(frappe.form_dict.get("custom_contact_person"))
secondary_mobile = normalize_mobile(frappe.form_dict.get("custom_secondary_mobile"))
sales_priority = clean_text(frappe.form_dict.get("custom_sales_priority"))
next_action = clean_text(frappe.form_dict.get("custom_next_action"))
last_visit_result = clean_text(frappe.form_dict.get("custom_last_visit_result"))
project_image = clean_text(frappe.form_dict.get("custom_project_image") or frappe.form_dict.get("project_image"))
status_input = clean_text(frappe.form_dict.get("status"))
shared_users_input = frappe.form_dict.get("shared_users")
sales_stage = ensure_valid_stage(sales_stage)
status_value = status_input or get_stage_status(sales_stage or get_default_stage())
source = clean_text(frappe.form_dict.get("source"))
lat = parse_float(frappe.form_dict.get("lat"), "خط العرض")
lng = parse_float(frappe.form_dict.get("lng"), "خط الطول")
territory_provided = "territory" in frappe.form_dict or "city" in frappe.form_dict

if google_map and (lat is None or lng is None):
    resolved_map = resolve_google_map_coords(google_map)
    if resolved_map:
        lat = resolved_map.get("lat")
        lng = resolved_map.get("lng")

if lat is not None and not (-90 <= lat <= 90):
    frappe.throw("خط العرض خارج النطاق")
if lng is not None and not (-180 <= lng <= 180):
    frappe.throw("خط الطول خارج النطاق")
if follow_up:
    frappe.utils.getdate(follow_up)

if mode == "update_location":
    if not name or lat is None or lng is None:
        frappe.throw("name, lat, lng required for update_location")
    doc = frappe.get_doc("Lead", name)
    assert_can_manage_lead(doc)
    next_google_map = google_map or ("https://www.google.com/maps/search/?api=1&query=" + str(lat) + "," + str(lng))
    assert_unique_google_map(doc.name, next_google_map)
    doc.custom_latitude = lat
    doc.custom_longitude = lng
    doc.custom_google_map = next_google_map
    if city:
        doc.city = city
    valid_territory = resolve_valid_territory(territory, city)
    if valid_territory:
        doc.territory = valid_territory
    doc.custom_last_activity_on = frappe.utils.now_datetime()
    owner_user = ensure_owner_field(doc, owner_input)
    doc.save(ignore_permissions=True)
    finalize_owner_fields(doc, owner_user)
    frappe.db.commit()
    frappe.response["message"] = {"success": True, "name": doc.name, "mode": "update_location"}
elif mode == "update_attachment":
    if not name or not project_image:
        frappe.throw("name and custom_project_image required for update_attachment")
    doc = frappe.get_doc("Lead", name)
    assert_can_manage_lead(doc)
    doc.custom_project_image = project_image
    doc.custom_last_activity_on = frappe.utils.now_datetime()
    owner_user = ensure_owner_field(doc, owner_input)
    doc.save(ignore_permissions=True)
    finalize_owner_fields(doc, owner_user)
    frappe.db.commit()
    frappe.response["message"] = {
        "success": True,
        "name": doc.name,
        "mode": "update_attachment",
        "custom_project_image": doc.custom_project_image,
    }
elif mode == "update_status":
    if not name or not (status_input or sales_stage):
        frappe.throw("name and status or stage required for update_status")
    doc = frappe.get_doc("Lead", name)
    assert_can_manage_lead(doc)
    resolved_stage = sales_stage or normalize_existing_stage(doc.get("custom_sales_stage_link") or doc.get("custom_sales_stage")) or get_default_stage()
    resolved_stage = ensure_valid_stage(resolved_stage) or get_default_stage()
    doc.status = status_input or get_stage_status(resolved_stage)
    doc.custom_sales_stage_link = resolved_stage
    sync_legacy_stage_field(doc, resolved_stage)
    if last_visit_result:
        doc.custom_last_visit_result = last_visit_result
    doc.custom_last_activity_on = frappe.utils.now_datetime()
    owner_user = ensure_owner_field(doc, owner_input)
    doc.save(ignore_permissions=True)
    finalize_owner_fields(doc, owner_user)
    frappe.db.commit()
    frappe.response["message"] = {
        "success": True,
        "name": doc.name,
        "mode": "update_status",
        "status": doc.status,
        "custom_sales_stage": doc.custom_sales_stage,
        "custom_last_visit_result": doc.custom_last_visit_result,
    }
elif mode == "update_follow_up":
    if not name:
        frappe.throw("name required for update_follow_up")
    doc = frappe.get_doc("Lead", name)
    assert_can_manage_lead(doc)
    doc.custom_next_follow_up_on = follow_up or None
    doc.custom_last_activity_on = frappe.utils.now_datetime()
    owner_user = ensure_owner_field(doc, owner_input)
    doc.save(ignore_permissions=True)
    finalize_owner_fields(doc, owner_user)
    frappe.db.commit()
    frappe.response["message"] = {
        "success": True,
        "name": doc.name,
        "mode": "update_follow_up",
        "custom_next_follow_up_on": doc.custom_next_follow_up_on,
        "custom_last_activity_on": doc.custom_last_activity_on,
    }
elif mode == "update_profile_fields":
    if not name:
        frappe.throw("name required for update_profile_fields")
    doc = frappe.get_doc("Lead", name)
    assert_can_manage_lead(doc)
    mobile_no = require_text(mobile_no, "رقم الجوال")
    source = require_text(source, "المصدر")
    if door_count is None:
        frappe.throw("عدد الأبواب مطلوب")
    if secondary_mobile and secondary_mobile == mobile_no:
        frappe.throw("الجوال الإضافي يجب أن يكون مختلفًا عن الجوال الأساسي")
    doc.mobile_no = mobile_no
    doc.phone = mobile_no
    doc.custom_door_count = door_count
    doc.source = source
    doc.company_name = company_name
    doc.custom_secondary_mobile = secondary_mobile
    doc.custom_last_activity_on = frappe.utils.now_datetime()
    owner_user = ensure_owner_field(doc, owner_input)
    doc.save(ignore_permissions=True)
    finalize_owner_fields(doc, owner_user)
    frappe.db.commit()
    frappe.response["message"] = {
        "success": True,
        "name": doc.name,
        "mode": "update_profile_fields",
        "mobile_no": doc.mobile_no,
        "phone": doc.phone,
        "company_name": doc.company_name,
        "custom_door_count": doc.custom_door_count,
        "custom_secondary_mobile": doc.custom_secondary_mobile,
        "source": doc.source,
        "custom_last_activity_on": doc.custom_last_activity_on,
    }
elif mode == "update_shared_users":
    if not name:
        frappe.throw("name required for update_shared_users")
    doc = frappe.get_doc("Lead", name)
    assert_can_share_lead(doc)
    owner_user = ensure_owner_field(doc, owner_input)
    shared_users = parse_shared_users(shared_users_input, owner_user)
    rebuild_shared_users(doc, shared_users)
    doc.custom_last_activity_on = frappe.utils.now_datetime()
    doc.save(ignore_permissions=True)
    finalize_owner_fields(doc, owner_user)
    frappe.db.commit()
    frappe.response["message"] = {
        "success": True,
        "name": doc.name,
        "mode": "update_shared_users",
        "shared_users": serialize_shared_users(doc),
    }
else:
    doc = None
    is_create_submission = not name
    if name:
        doc = frappe.get_doc("Lead", name)
        assert_can_read_lead(doc)
        assert_can_manage_lead(doc)

    if not doc:
        assert_can_create_lead()
        doc = frappe.new_doc("Lead")

    previous_notes = clean_text(doc.get("custom_map_notes"))
    notes_provided = "notes" in frappe.form_dict or "custom_map_notes" in frappe.form_dict

    if lead_name:
        doc.lead_name = lead_name
    if company_name or "company_name" in frappe.form_dict:
        doc.company_name = company_name

    if not clean_text(doc.get("lead_name")):
        frappe.throw("اسم الفرصة أو العميل مطلوب")

    if is_create_submission and not mobile_no:
        frappe.throw("رقم الجوال مطلوب")
    if secondary_mobile and secondary_mobile == mobile_no:
        frappe.throw("الجوال الإضافي يجب أن يكون مختلفًا عن الجوال الأساسي")

    if mobile_no:
        doc.mobile_no = mobile_no
        doc.phone = mobile_no

    doc.email_id = email_id
    if city:
        doc.city = city
    valid_territory = resolve_valid_territory(territory, city)
    if is_create_submission and not valid_territory:
        frappe.throw("المدينة مطلوبة")
    if territory_provided and not valid_territory:
        frappe.throw("المدينة مطلوبة")
    if valid_territory:
        doc.territory = valid_territory
    doc.custom_map_notes = notes
    doc.custom_next_follow_up_on = follow_up
    existing_stage = normalize_existing_stage(doc.get("custom_sales_stage_link") or doc.get("custom_sales_stage"))
    doc.custom_sales_stage_link = sales_stage or existing_stage or get_default_stage()
    sync_legacy_stage_field(doc, doc.custom_sales_stage_link)
    doc.custom_business_type = business_type
    if is_create_submission and door_count is None:
        frappe.throw("عدد الأبواب مطلوب")
    doc.custom_door_count = door_count
    doc.custom_contact_person = contact_person
    if secondary_mobile or "custom_secondary_mobile" in frappe.form_dict:
        doc.custom_secondary_mobile = secondary_mobile
    doc.custom_sales_priority = sales_priority or clean_text(doc.get("custom_sales_priority")) or "متوسطة"
    doc.custom_next_action = next_action
    doc.custom_last_visit_result = last_visit_result
    if project_image:
        doc.custom_project_image = project_image
    doc.custom_last_activity_on = frappe.utils.now_datetime()

    if lat is not None:
        doc.custom_latitude = lat
    if lng is not None:
        doc.custom_longitude = lng
    next_google_map = ""
    if google_map:
        next_google_map = google_map
    elif lat is not None and lng is not None:
        next_google_map = "https://www.google.com/maps/search/?api=1&query=" + str(lat) + "," + str(lng)
    if next_google_map:
        assert_unique_google_map("" if doc.is_new() else doc.name, next_google_map)
        doc.custom_google_map = next_google_map

    doc.status = status_input or get_stage_status(doc.custom_sales_stage_link)

    if is_create_submission:
        source = require_text(source, "المصدر")
    if source:
        doc.source = source

    owner_user = ensure_owner_field(doc, owner_input)
    shared_users = parse_shared_users(shared_users_input, owner_user)
    rebuild_shared_users(doc, shared_users)

    is_new = doc.is_new()
    doc.save(ignore_permissions=True)
    should_record_note = notes_provided and notes and (is_new or notes != previous_notes)
    if should_record_note:
        create_note_comment(doc, "ملاحظات الليد", notes)
    finalize_owner_fields(doc, owner_user, clear_legacy=is_new)
    frappe.db.commit()

    frappe.response["message"] = {
        "success": True,
        "name": doc.name,
        "mode": "create" if is_new else "update",
        "is_new": is_new,
        "lead": {
            "name": doc.name,
            "lead_name": doc.lead_name,
            "company_name": doc.company_name,
            "mobile_no": doc.mobile_no,
            "phone": doc.phone,
            "email_id": doc.email_id,
            "city": doc.city,
            "territory": doc.territory,
            "status": doc.status,
            "owner": doc.owner,
            "lead_owner": doc.lead_owner,
            "source": doc.source,
            "custom_latitude": doc.custom_latitude,
            "custom_longitude": doc.custom_longitude,
            "custom_google_map": doc.custom_google_map,
            "custom_map_notes": doc.custom_map_notes,
            "custom_next_follow_up_on": doc.custom_next_follow_up_on,
            "custom_sales_stage": doc.custom_sales_stage_link,
            "custom_sales_stage_link": doc.custom_sales_stage_link,
            "custom_business_type": doc.custom_business_type,
            "custom_door_count": doc.custom_door_count,
            "custom_contact_person": doc.custom_contact_person,
            "custom_secondary_mobile": doc.custom_secondary_mobile,
            "custom_sales_priority": doc.custom_sales_priority,
            "custom_next_action": doc.custom_next_action,
            "custom_last_visit_result": doc.custom_last_visit_result,
            "custom_last_activity_on": doc.custom_last_activity_on,
            "custom_project_image": doc.custom_project_image,
            "shared_users": serialize_shared_users(doc),
        },
    }
