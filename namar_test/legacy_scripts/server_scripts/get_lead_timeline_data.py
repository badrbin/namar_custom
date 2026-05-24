# API: get_lead_timeline_data

if frappe.session.user == "Guest":
    frappe.throw("يلزم تسجيل الدخول لعرض تايم لاين الليدز", frappe.PermissionError)


EVENT_LABELS = {
    "create": "إنشاء ليد",
    "visit": "زيارة ميدانية",
    "comment": "ملاحظة",
    "stage": "تغيير مرحلة",
    "location": "تحديث موقع",
    "contact": "تعديل تواصل",
    "owner": "تغيير مالك",
    "share": "مشاركة",
    "attachment": "مرفق",
    "change": "تعديل بيانات",
}

FIELD_LABELS = {
    "lead_name": "اسم الليد",
    "company_name": "اسم الشركة",
    "mobile_no": "الجوال",
    "phone": "الهاتف",
    "custom_secondary_mobile": "الجوال الإضافي",
    "territory": "المدينة",
    "source": "المصدر",
    "owner": "المالك",
    "lead_owner": "مالك الليد",
    "status": "الحالة",
    "custom_sales_stage": "مرحلة البيع",
    "custom_sales_stage_link": "مرحلة البيع",
    "custom_google_map": "رابط قوقل ماب",
    "custom_latitude": "خط العرض",
    "custom_longitude": "خط الطول",
    "custom_map_notes": "الملاحظات",
    "custom_door_count": "عدد الأبواب",
    "custom_project_image": "مرفق المشروع",
    "custom_business_type": "نوع النشاط",
    "custom_last_visit_result": "نتيجة آخر زيارة",
    "custom_next_follow_up_on": "موعد المتابعة",
}

LOCATION_FIELDS = ("custom_google_map", "custom_latitude", "custom_longitude")
STAGE_FIELDS = ("custom_sales_stage", "custom_sales_stage_link", "status")
CONTACT_FIELDS = ("mobile_no", "phone", "custom_secondary_mobile")
OWNER_FIELDS = ("owner", "lead_owner")
ATTACHMENT_FIELDS = ("custom_project_image",)
IGNORED_VERSION_FIELDS = ("modified", "modified_by", "idx", "custom_last_activity_on")


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


def parse_int(value, default_value, min_value, max_value):
    try:
        number = int(value)
    except Exception:
        number = default_value
    if number < min_value:
        return min_value
    if number > max_value:
        return max_value
    return number


def strip_html(value):
    text = clean_text(value)
    if not text:
        return ""
    text = text.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    output = []
    in_tag = False
    for ch in text:
        if ch == "<":
            in_tag = True
            continue
        if ch == ">":
            in_tag = False
            continue
        if not in_tag:
            output.append(ch)
    return " ".join("".join(output).split())


def strip_generated_note_prefix(value):
    return parse_generated_note_content(value).get("note") or clean_text(value)


def parse_generated_note_content(value):
    text = clean_text(value)
    if "المصدر:" not in text:
        return {"source": "", "note": text, "is_generated": False}
    if not (text.startswith("ملاحظة من:") or text.startswith("منشن من:")):
        return {"source": "", "note": text, "is_generated": False}

    after_source = text.split("المصدر:", 1)[1].strip()
    for source_label in ("ملاحظة الزيارة", "ملاحظات الليد"):
        if after_source.startswith(source_label):
            note_text = after_source[len(source_label) :].strip()
            return {"source": source_label, "note": note_text or text, "is_generated": True}
    return {"source": "", "note": text, "is_generated": True}


def timeline_note_key(lead_name, note_text):
    lead_name = clean_text(lead_name)
    note_text = " ".join(clean_text(note_text).split()).lower()
    if not lead_name or not note_text:
        return ""
    return lead_name + "::" + note_text


def timeline_note_occurrence_key(lead_name, note_text, timestamp):
    note_key = timeline_note_key(lead_name, note_text)
    minute_key = clean_text(timestamp)[:16]
    if not note_key or not minute_key:
        return ""
    return note_key + "::" + minute_key


def shorten(value, limit=180):
    text = clean_text(value)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


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


def has_system_read_access():
    if not has_role_based_permission("Lead", "read"):
        return False
    try:
        frappe.get_list("Lead", fields=["name"], limit_page_length=1)
        return True
    except frappe.PermissionError:
        return False


def get_shared_parent_names(user):
    if not user or not frappe.db.exists("DocType", "Lead Shared User"):
        return []
    rows = frappe.get_all(
        "Lead Shared User",
        filters={
            "parenttype": "Lead",
            "parentfield": "custom_shared_users",
            "shared_user": user,
        },
        pluck="parent",
        limit_page_length=0,
    )
    seen = {}
    names = []
    for row in rows:
        parent = clean_text(row)
        if parent and parent not in seen:
            seen[parent] = 1
            names.append(parent)
    return names


def get_user_display_map(users):
    ids = []
    seen = {}
    for user in users:
        user = clean_text(user)
        if user and user not in seen:
            seen[user] = 1
            ids.append(user)
    if not ids:
        return {}
    rows = frappe.get_all("User", filters={"name": ["in", ids]}, fields=["name", "full_name"], limit_page_length=0)
    result = {}
    for row in rows:
        user_id = clean_text(row.get("name"))
        result[user_id] = clean_text(row.get("full_name")) or user_id
    return result


def make_event(event_type, lead, timestamp, actor="", title="", description="", detail="", source_id="", subject_user=""):
    lead_name = clean_text(row_get(lead, "name"))
    return {
        "id": clean_text(source_id) or (event_type + "::" + lead_name + "::" + clean_text(timestamp)),
        "type": event_type,
        "type_label": EVENT_LABELS.get(event_type, event_type),
        "lead": lead_name,
        "lead_name": clean_text(row_get(lead, "lead_name")) or lead_name,
        "territory": clean_text(row_get(lead, "territory")),
        "source": clean_text(row_get(lead, "source")),
        "owner": clean_text(row_get(lead, "owner") or row_get(lead, "lead_owner")),
        "actor": clean_text(actor),
        "actor_name": clean_text(actor),
        "subject_user": clean_text(subject_user),
        "timestamp": clean_text(timestamp),
        "title": clean_text(title) or EVENT_LABELS.get(event_type, event_type),
        "description": shorten(description, 220),
        "detail": shorten(detail, 320),
    }


def parse_version_data(data):
    if not data:
        return {}
    if isinstance(data, dict):
        return data
    try:
        parsed = frappe.parse_json(data)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    return {}


def classify_changed_fields(fields):
    for field in STAGE_FIELDS:
        if field in fields:
            return "stage"
    for field in LOCATION_FIELDS:
        if field in fields:
            return "location"
    for field in CONTACT_FIELDS:
        if field in fields:
            return "contact"
    for field in OWNER_FIELDS:
        if field in fields:
            return "owner"
    for field in ATTACHMENT_FIELDS:
        if field in fields:
            return "attachment"
    return "change"


def build_change_description(changes):
    parts = []
    for change in changes[:4]:
        fieldname = clean_text(change.get("fieldname"))
        label = FIELD_LABELS.get(fieldname, fieldname)
        old_value = shorten(clean_text(change.get("old")), 45)
        new_value = shorten(clean_text(change.get("new")), 45)
        if old_value and new_value:
            parts.append(label + ": " + old_value + " ← " + new_value)
        elif new_value:
            parts.append(label + ": " + new_value)
        else:
            parts.append(label)
    if len(changes) > 4:
        parts.append("+" + str(len(changes) - 4) + " حقول")
    return "، ".join(parts)


def event_matches_type(event, event_type):
    event_type = clean_text(event_type)
    if not event_type:
        return True
    return clean_text(event.get("type")) == event_type


def get_timeline_period(value):
    value = clean_text(value) or "30"
    today = frappe.utils.nowdate()
    if value == "today":
        return {"value": value, "days": 1, "from_date": today, "to_date": "", "label": "اليوم"}
    if value == "yesterday":
        yesterday = frappe.utils.add_days(today, -1)
        return {"value": value, "days": 1, "from_date": yesterday, "to_date": today, "label": "أمس"}
    days_value = parse_int(value, 30, 1, 365)
    if days_value == 1:
        label = "آخر يوم"
    elif days_value == 2:
        label = "آخر يومين"
    elif days_value in (3, 7):
        label = "آخر " + str(days_value) + " أيام"
    else:
        label = "آخر " + str(days_value) + " يوم"
    return {
        "value": str(days_value),
        "days": days_value,
        "from_date": frappe.utils.add_days(today, -days_value),
        "to_date": "",
        "label": label,
    }


def event_matches_period(event, from_value, to_value):
    timestamp = clean_text(event.get("timestamp"))
    if not timestamp:
        return False
    if from_value and timestamp < from_value:
        return False
    if to_value and timestamp >= to_value:
        return False
    return True


period = get_timeline_period(frappe.form_dict.get("days"))
days = period.get("days")
limit = parse_int(frappe.form_dict.get("limit"), 80, 10, 300)
offset = parse_int(frappe.form_dict.get("offset"), 0, 0, 5000)
fetch_window = offset + limit
event_type_filter = clean_text(frappe.form_dict.get("event_type"))
actor_filter = clean_text(frappe.form_dict.get("actor") or frappe.form_dict.get("activity_user"))
owner_filter = clean_text(frappe.form_dict.get("owner") or frappe.form_dict.get("lead_owner"))
territory_filter = clean_text(frappe.form_dict.get("territory") or frappe.form_dict.get("city"))

if actor_filter == "__mine__":
    actor_filter = frappe.session.user
if owner_filter == "__mine__":
    owner_filter = frappe.session.user

from_date = period.get("from_date")
to_date = period.get("to_date")
lead_filters = {}
if owner_filter:
    lead_filters["owner"] = owner_filter
if territory_filter:
    lead_filters["territory"] = territory_filter

lead_fields = [
    "name",
    "lead_name",
    "owner",
    "lead_owner",
    "territory",
    "source",
    "creation",
    "modified",
    "custom_google_map",
    "custom_latitude",
    "custom_longitude",
]

leads = []
lead_seen = {}
if has_system_read_access():
    rows = frappe.get_all("Lead", filters=lead_filters, fields=lead_fields, limit_page_length=0)
    for row in rows:
        lead_name = clean_text(row.get("name"))
        if lead_name and lead_name not in lead_seen:
            lead_seen[lead_name] = 1
            leads.append(row)

shared_names = get_shared_parent_names(frappe.session.user)
if shared_names:
    shared_filters = dict(lead_filters)
    shared_filters["name"] = ["in", shared_names]
    shared_rows = frappe.get_all("Lead", filters=shared_filters, fields=lead_fields, limit_page_length=0)
    for row in shared_rows:
        lead_name = clean_text(row.get("name"))
        if lead_name and lead_name not in lead_seen:
            lead_seen[lead_name] = 1
            leads.append(row)

lead_by_name = {}
lead_names = []
for lead in leads:
    lead_name = clean_text(lead.get("name"))
    if lead_name:
        lead_by_name[lead_name] = lead
        lead_names.append(lead_name)

events = []

if lead_names:
    for lead in leads:
        creation = clean_text(lead.get("creation"))
        if creation and creation >= from_date:
            events.append(
                make_event(
                    "create",
                    lead,
                    creation,
                    actor=lead.get("owner"),
                    title="إنشاء ليد",
                    description="تم إنشاء الليد من المصدر " + (clean_text(lead.get("source")) or "غير محدد"),
                    source_id="lead-create::" + clean_text(lead.get("name")),
                )
            )

    visit_rows = frappe.get_all(
        "Lead Field Visit",
        filters={"lead": ["in", lead_names], "visit_on": [">=", from_date]},
        fields=["name", "lead", "visit_on", "visited_by", "visit_notes", "visit_image", "creation"],
        order_by="visit_on desc, creation desc",
        limit_page_length=fetch_window * 3,
    )
    visit_note_keys = {}
    for row in visit_rows:
        lead = lead_by_name.get(clean_text(row.get("lead")))
        if not lead:
            continue
        notes = clean_text(row.get("visit_notes"))
        note_key = timeline_note_key(row.get("lead"), notes)
        if note_key:
            visit_note_keys[note_key] = 1

    comment_rows = frappe.get_all(
        "Comment",
        filters={
            "reference_doctype": "Lead",
            "reference_name": ["in", lead_names],
            "creation": [">=", from_date],
        },
        fields=["name", "reference_name", "comment_type", "content", "owner", "comment_by", "creation"],
        order_by="creation desc",
        limit_page_length=fetch_window * 3,
    )
    generated_visit_comment_keys = {}
    seen_generated_note_occurrences = {}
    for row in comment_rows:
        lead = lead_by_name.get(clean_text(row.get("reference_name")))
        if not lead:
            continue
        content = strip_html(row.get("content"))
        if not content:
            continue
        parsed_note = parse_generated_note_content(content)
        content = clean_text(parsed_note.get("note"))
        if parsed_note.get("is_generated"):
            occurrence_key = timeline_note_occurrence_key(row.get("reference_name"), content, row.get("creation"))
            if occurrence_key and occurrence_key in seen_generated_note_occurrences:
                continue
            if occurrence_key:
                seen_generated_note_occurrences[occurrence_key] = 1
        if parsed_note.get("source") == "ملاحظة الزيارة":
            note_key = timeline_note_key(row.get("reference_name"), content)
            if note_key and note_key in visit_note_keys:
                generated_visit_comment_keys[note_key] = 1
        comment_type = clean_text(row.get("comment_type"))
        title = "ملاحظة" if comment_type == "Comment" else ("نشاط: " + comment_type if comment_type else "ملاحظة")
        events.append(
            make_event(
                "comment",
                lead,
                row.get("creation"),
                actor=row.get("owner") or row.get("comment_by"),
                title=title,
                description=content,
                detail=content,
                source_id="comment::" + clean_text(row.get("name")),
            )
        )

    if generated_visit_comment_keys:
        events = [
            event
            for event in events
            if not (
                clean_text(event.get("type")) == "visit"
                and timeline_note_key(event.get("lead"), event.get("detail") or event.get("description")) in generated_visit_comment_keys
            )
        ]

    if frappe.db.exists("DocType", "Lead Shared User"):
        share_rows = frappe.get_all(
            "Lead Shared User",
            filters={"parenttype": "Lead", "parent": ["in", lead_names], "creation": [">=", from_date]},
            fields=["name", "parent", "shared_user", "added_by", "added_on", "creation"],
            order_by="creation desc",
            limit_page_length=fetch_window * 3,
        )
        for row in share_rows:
            lead = lead_by_name.get(clean_text(row.get("parent")))
            if not lead:
                continue
            shared_user = clean_text(row.get("shared_user"))
            events.append(
                make_event(
                    "share",
                    lead,
                    row.get("added_on") or row.get("creation"),
                    actor=row.get("added_by"),
                    title="مشاركة ليد",
                    description="تمت مشاركة الليد مع " + shared_user,
                    subject_user=shared_user,
                    source_id="share::" + clean_text(row.get("name")),
                )
            )

    version_rows = frappe.get_all(
        "Version",
        filters={"ref_doctype": "Lead", "docname": ["in", lead_names], "creation": [">=", from_date]},
        fields=["name", "docname", "owner", "creation", "data"],
        order_by="creation desc",
        limit_page_length=fetch_window * 4,
    )
    for row in version_rows:
        lead = lead_by_name.get(clean_text(row.get("docname")))
        if not lead:
            continue
        data = parse_version_data(row.get("data"))
        raw_changes = data.get("changed") or []
        changes = []
        for item in raw_changes:
            if not isinstance(item, (list, tuple)) or len(item) < 3:
                continue
            fieldname = clean_text(item[0])
            if not fieldname or fieldname in IGNORED_VERSION_FIELDS:
                continue
            if fieldname not in FIELD_LABELS:
                continue
            changes.append({"fieldname": fieldname, "old": item[1], "new": item[2]})
        if not changes:
            continue
        change_fields = [change.get("fieldname") for change in changes]
        event_type = classify_changed_fields(change_fields)
        events.append(
            make_event(
                event_type,
                lead,
                row.get("creation"),
                actor=row.get("owner"),
                title=EVENT_LABELS.get(event_type, "تعديل بيانات"),
                description=build_change_description(changes),
                detail=build_change_description(changes),
                source_id="version::" + clean_text(row.get("name")),
            )
        )

period_events = [event for event in events if event_matches_period(event, from_date, to_date)]
typed_events = [event for event in period_events if event_matches_type(event, event_type_filter)]
actor_ids = []
seen_actors = {}
for event in typed_events:
    actor_id = clean_text(event.get("actor"))
    if actor_id and actor_id not in seen_actors:
        seen_actors[actor_id] = 1
        actor_ids.append(actor_id)
if actor_filter:
    filtered_events = [event for event in typed_events if clean_text(event.get("actor")) == actor_filter]
else:
    filtered_events = typed_events
filtered_events.sort(key=lambda item: clean_text(item.get("timestamp")), reverse=True)
available_count = len(filtered_events)
events = filtered_events[offset : offset + limit]
next_offset = offset + len(events)
has_more = available_count > next_offset

users_to_fetch = []
for actor_id in actor_ids:
    users_to_fetch.append(actor_id)
for event in events:
    users_to_fetch.append(event.get("actor"))
    users_to_fetch.append(event.get("owner"))
    users_to_fetch.append(event.get("subject_user"))
user_display = get_user_display_map(users_to_fetch)
actor_options = []
for actor_id in actor_ids:
    actor_options.append({"value": actor_id, "label": user_display.get(actor_id, actor_id)})
actor_options.sort(key=lambda item: clean_text(item.get("label")))
for event in events:
    actor = clean_text(event.get("actor"))
    owner = clean_text(event.get("owner"))
    subject_user = clean_text(event.get("subject_user"))
    event["actor_name"] = user_display.get(actor, actor)
    event["owner_name"] = user_display.get(owner, owner)
    if subject_user:
        subject_user_name = user_display.get(subject_user, subject_user)
        event["subject_user_name"] = subject_user_name
        if clean_text(event.get("type")) == "share":
            event["description"] = shorten("تمت مشاركة الليد مع " + subject_user_name, 220)

counts = {}
for event in events:
    event_type = clean_text(event.get("type"))
    counts[event_type] = counts.get(event_type, 0) + 1

frappe.response["message"] = {
    "events": events,
    "summary": {
        "total": len(events),
        "counts": counts,
        "days": days,
        "period": period.get("value"),
        "period_label": period.get("label"),
        "limit": limit,
        "offset": offset,
        "returned": len(events),
        "displayed": next_offset,
        "available_count": available_count,
        "has_more": has_more,
        "next_offset": next_offset,
        "actor": actor_filter,
    },
    "actor_options": actor_options,
    "event_types": [
        {"value": "", "label": "كل الأحداث"},
        {"value": "create", "label": EVENT_LABELS["create"]},
        {"value": "comment", "label": EVENT_LABELS["comment"]},
        {"value": "stage", "label": EVENT_LABELS["stage"]},
        {"value": "location", "label": EVENT_LABELS["location"]},
        {"value": "contact", "label": EVENT_LABELS["contact"]},
        {"value": "owner", "label": EVENT_LABELS["owner"]},
        {"value": "share", "label": EVENT_LABELS["share"]},
        {"value": "attachment", "label": EVENT_LABELS["attachment"]},
        {"value": "change", "label": EVENT_LABELS["change"]},
    ],
}
