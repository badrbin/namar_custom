# API: get_lead_map_data

if frappe.session.user == "Guest":
    frappe.throw("يلزم تسجيل الدخول لاستخدام خريطة البيع الميداني", frappe.PermissionError)


DEFAULT_STAGE_CONFIGS = [
    {"name": "جديد", "stage_color": "#3B82F6", "workflow_status": "Lead", "sort_order": 1, "is_default": 1, "is_won": 0, "is_closed": 0},
    {"name": "تمت الزيارة", "stage_color": "#F59E0B", "workflow_status": "Replied", "sort_order": 2, "is_default": 0, "is_won": 0, "is_closed": 0},
    {"name": "مهتم", "stage_color": "#10B981", "workflow_status": "Interested", "sort_order": 3, "is_default": 0, "is_won": 0, "is_closed": 0},
    {"name": "يحتاج عرض سعر", "stage_color": "#8B5CF6", "workflow_status": "Interested", "sort_order": 4, "is_default": 0, "is_won": 0, "is_closed": 0},
    {"name": "بانتظار الرد", "stage_color": "#F59E0B", "workflow_status": "Interested", "sort_order": 5, "is_default": 0, "is_won": 0, "is_closed": 0},
    {"name": "متابعة لاحقة", "stage_color": "#14B8A6", "workflow_status": "Interested", "sort_order": 6, "is_default": 0, "is_won": 0, "is_closed": 0},
    {"name": "تم البيع", "stage_color": "#0F766E", "workflow_status": "Converted", "sort_order": 7, "is_default": 0, "is_won": 1, "is_closed": 1},
    {"name": "مغلق - غير مناسب", "stage_color": "#EF4444", "workflow_status": "Do Not Contact", "sort_order": 8, "is_default": 0, "is_won": 0, "is_closed": 1},
]


def clean_text(value):
    if value is None:
        return ""
    return str(value).strip()


def parse_list(value):
    if not value:
        return []
    if isinstance(value, list):
        return [clean_text(item) for item in value if clean_text(item)]
    try:
        parsed = frappe.parse_json(value)
        if isinstance(parsed, list):
            return [clean_text(item) for item in parsed if clean_text(item)]
    except Exception:
        pass
    value = clean_text(value)
    return [value] if value else []


def add_unique(items, seen, value):
    value = clean_text(value)
    if value and value not in seen:
        items.append(value)
        seen[value] = 1


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


def has_system_read_access():
    if not has_role_based_permission("Lead", "read"):
        return False
    try:
        frappe.get_list("Lead", fields=["name"], limit_page_length=1)
        return True
    except frappe.PermissionError:
        return False


def has_shared_lead_access(lead_name, ptype="read"):
    lead_name = clean_text(lead_name)
    if not lead_name or not has_role_based_permission("Lead", ptype):
        return False
    return lead_name in get_shared_parent_names(frappe.session.user)


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


def get_available_users():
    rows = frappe.get_all(
        "User",
        filters={"enabled": 1, "user_type": "System User"},
        fields=["name", "full_name"],
        order_by="full_name asc, name asc",
        limit_page_length=0,
    )
    users = []
    seen = {}
    for row in rows:
        user_id = clean_text(row.get("name"))
        if not user_id or user_id in ("Guest",):
            continue
        if user_id in seen:
            continue
        seen[user_id] = 1
        users.append({
            "name": user_id,
            "full_name": clean_text(row.get("full_name")) or user_id,
        })
    return users


def get_stage_configs():
    if frappe.db.exists("DocType", "Lead Sales Stage"):
        rows = frappe.get_all(
            "Lead Sales Stage",
            filters={"is_active": 1},
            fields=["name", "stage_name", "stage_color", "workflow_status", "sort_order", "is_default", "is_won", "is_closed"],
            order_by="is_default desc, sort_order asc, creation asc",
            limit_page_length=0,
        )
        configs = []
        for row in rows:
            label = clean_text(row.get("stage_name") or row.get("name"))
            if not label:
                continue
            configs.append(
                {
                    "name": label,
                    "stage_color": clean_text(row.get("stage_color")),
                    "workflow_status": clean_text(row.get("workflow_status")) or "Lead",
                    "sort_order": row.get("sort_order") or 0,
                    "is_default": 1 if row.get("is_default") else 0,
                    "is_won": 1 if row.get("is_won") else 0,
                    "is_closed": 1 if row.get("is_closed") else 0,
                }
            )
        if configs:
            return configs
    return [dict(item) for item in DEFAULT_STAGE_CONFIGS]


def get_territory_options():
    if frappe.db.exists("DocType", "Territory"):
        rows = frappe.get_all(
            "Territory",
            filters={"is_group": 0},
            fields=["name", "lft", "custom_display_order"],
            order_by="lft asc, creation asc",
            limit_page_length=0,
        )
        def sort_key(row):
            order = row.get("custom_display_order")
            try:
                order = int(order)
            except Exception:
                order = None
            if order is not None and order <= 0:
                order = None
            lft = row.get("lft") or 0
            name = clean_text(row.get("name"))
            return (0 if order is not None else 1, order if order is not None else 0, lft, name)

        rows = sorted(rows, key=sort_key)
        options = [clean_text(row.get("name")) for row in rows if clean_text(row.get("name"))]
        if options:
            return options
    return []


def get_source_options():
    if frappe.db.exists("DocType", "Lead Source"):
        rows = frappe.get_all(
            "Lead Source",
            fields=["name", "source_name"],
            order_by="source_name asc, name asc",
            limit_page_length=0,
        )
        options = []
        seen = {}
        for row in rows:
            label = clean_text(row.get("source_name") or row.get("name"))
            if label and label not in seen:
                seen[label] = 1
                options.append(label)
        if options:
            return options
    return []


def get_system_defaults():
    return {
        "date_format": clean_text(frappe.db.get_single_value("System Settings", "date_format")) or "dd-mm-yyyy",
        "time_format": clean_text(frappe.db.get_single_value("System Settings", "time_format")) or "HH:mm",
        "language": clean_text(frappe.db.get_single_value("System Settings", "language")) or "en",
        "time_zone": clean_text(frappe.db.get_single_value("System Settings", "time_zone")) or "Asia/Riyadh",
    }


territory = clean_text(frappe.form_dict.get("territory"))
source = clean_text(frappe.form_dict.get("source"))
owner = clean_text(frappe.form_dict.get("owner") or frappe.form_dict.get("lead_owner"))
status_list = parse_list(frappe.form_dict.get("status"))
stage_list = parse_list(frappe.form_dict.get("sales_stage"))
available_users = get_available_users()
stage_configs = get_stage_configs()
system_defaults = get_system_defaults()
source_options = get_source_options()

filters = {}
if territory:
    filters["territory"] = territory
if source:
    filters["source"] = source
if owner:
    filters["owner"] = owner
if status_list:
    filters["status"] = ["in", status_list]

if not has_system_read_access() and not get_shared_parent_names(frappe.session.user):
    frappe.throw("ليس لديك صلاحية لعرض الليدز من النظام", frappe.PermissionError)

fields = [
    "name",
    "lead_name",
    "company_name",
    "mobile_no",
    "phone",
    "email_id",
    "city",
    "territory",
    "source",
    "status",
    "owner",
    "lead_owner",
    "custom_latitude",
    "custom_longitude",
    "custom_google_map",
    "custom_map_notes",
    "custom_next_follow_up_on",
    "custom_sales_stage",
    "custom_sales_stage_link",
    "custom_business_type",
    "custom_door_count",
    "custom_contact_person",
    "custom_secondary_mobile",
    "custom_sales_priority",
    "custom_next_action",
    "custom_last_visit_result",
    "custom_last_activity_on",
    "custom_project_image",
    "creation",
    "modified",
]

leads = frappe.get_list(
    "Lead",
    filters=filters,
    fields=fields,
    order_by="modified desc",
    limit_page_length=0,
)

lead_names_seen = {}
shared_names = []
for row in get_shared_parent_names(frappe.session.user):
    lead_name = clean_text(row)
    if lead_name:
        shared_names.append(lead_name)

for lead in leads:
    lead_name = clean_text(lead.get("name"))
    if lead_name:
        lead_names_seen[lead_name] = 1

missing_shared_names = [name for name in shared_names if name and name not in lead_names_seen]
if missing_shared_names:
    shared_filters = dict(filters)
    shared_filters["name"] = ["in", missing_shared_names]
    shared_leads = frappe.get_all(
        "Lead",
        filters=shared_filters,
        fields=fields,
        order_by="modified desc",
        limit_page_length=0,
    )
    for lead in shared_leads:
        lead_name = clean_text(lead.get("name"))
        if not lead_name or lead_name in lead_names_seen:
            continue
        if has_shared_lead_access(lead_name, "read"):
            lead_names_seen[lead_name] = 1
            leads.append(lead)

leads = sorted(leads, key=lambda item: clean_text(item.get("modified")), reverse=True)

lead_names = [clean_text(lead.get("name")) for lead in leads if clean_text(lead.get("name"))]
user_directory = {}
for user_item in available_users:
    user_directory[user_item["name"]] = user_item["full_name"]

shared_by_parent = {}
if lead_names and frappe.db.exists("DocType", "Lead Shared User"):
    rows = frappe.get_all(
        "Lead Shared User",
        filters={
            "parent": ["in", lead_names],
            "parenttype": "Lead",
            "parentfield": "custom_shared_users",
        },
        fields=["parent", "shared_user", "added_by", "added_on"],
        order_by="idx asc, creation asc",
        limit_page_length=0,
    )
    for row in rows:
        parent = clean_text(row.get("parent"))
        shared_user = clean_text(row.get("shared_user"))
        if not parent or not shared_user:
            continue
        shared_by_parent.setdefault(parent, []).append(
            {
                "user": shared_user,
                "full_name": user_directory.get(shared_user, shared_user),
                "added_by": clean_text(row.get("added_by")),
                "added_by_name": user_directory.get(clean_text(row.get("added_by")), clean_text(row.get("added_by"))),
                "added_on": row.get("added_on"),
            }
        )

cities = []
sources = []
statuses = []
stages = []
owners = []
business_types = []
territory_counts = {}
territory_options = get_territory_options()
default_stage = ""
for stage_config in stage_configs:
    if stage_config.get("is_default"):
        default_stage = clean_text(stage_config.get("name"))
        break
if not default_stage and stage_configs:
    default_stage = clean_text(stage_configs[0].get("name"))
if not default_stage:
    default_stage = "جديد"

seen_city = {}
seen_source = {}
seen_status = {}
seen_stage = {}
seen_owner = {}
seen_business_type = {}

for lead in leads:
    territory_label = clean_text(lead.get("territory") or lead.get("city"))
    source_label = clean_text(lead.get("source"))
    status_label = clean_text(lead.get("status"))
    stage_label = clean_text(lead.get("custom_sales_stage_link") or lead.get("custom_sales_stage"))
    owner_label = clean_text(lead.get("owner") or lead.get("lead_owner"))
    business_type_label = clean_text(lead.get("custom_business_type"))
    phone_label = clean_text(lead.get("mobile_no") or lead.get("phone"))
    shared_users = shared_by_parent.get(clean_text(lead.get("name")), [])

    lead["display_phone"] = phone_label
    lead["city"] = territory_label
    lead["territory"] = territory_label
    lead["custom_sales_stage"] = stage_label
    lead["shared_users"] = shared_users
    lead["owner"] = owner_label
    lead["lead_owner"] = owner_label
    lead["owner_full_name"] = user_directory.get(owner_label, owner_label)

    add_unique(cities, seen_city, territory_label)
    add_unique(sources, seen_source, source_label)
    add_unique(statuses, seen_status, status_label)
    add_unique(stages, seen_stage, stage_label)
    add_unique(owners, seen_owner, owner_label)
    add_unique(business_types, seen_business_type, business_type_label)

    if territory_label:
        territory_counts[territory_label] = territory_counts.get(territory_label, 0) + 1

for stage_config in stage_configs:
    add_unique(stages, seen_stage, stage_config.get("name"))

if not territory_options:
    territory_options = sorted(cities)

for source_label in sources:
    if source_label and source_label not in source_options:
        source_options.append(source_label)
source_options = sorted(source_options)

if stage_list:
    leads = [lead for lead in leads if clean_text(lead.get("custom_sales_stage")) in stage_list]

frappe.response["message"] = {
    "leads": leads,
    "filters": {
        "cities": territory_options,
        "territories": territory_options,
        "sources": source_options,
        "statuses": sorted(statuses),
        "stages": sorted(stages),
        "owners": sorted(owners),
        "business_types": sorted(business_types),
    },
    "filters_data": {
        "territories": territory_options,
        "cities": territory_options,
        "sources": source_options,
        "statuses": sorted(statuses),
        "stages": sorted(stages),
        "owners": sorted(owners),
        "business_types": sorted(business_types),
    },
    "stage_configs": stage_configs,
    "default_stage": default_stage,
    "territory_counts": territory_counts,
    "defaults": {
        "center_lat": 23.8859,
        "center_lng": 45.0792,
        "zoom": 6,
    },
    "system_defaults": system_defaults,
    "current_user": frappe.session.user,
    "available_users": available_users,
}
