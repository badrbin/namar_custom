# API: get_lead_visit_logs

if frappe.session.user == "Guest":
    frappe.throw("يلزم تسجيل الدخول لعرض سجل الزيارات", frappe.PermissionError)


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
    frappe.throw("ليس لديك صلاحية لعرض سجل زيارات هذا الليد", frappe.PermissionError)


lead_name = clean_text(frappe.form_dict.get("lead"))
limit = frappe.utils.cint(frappe.form_dict.get("limit") or 20)
if not lead_name:
    frappe.throw("lead required")

lead_doc = frappe.get_doc("Lead", lead_name)
assert_can_read_lead(lead_doc)

logs = frappe.get_all(
    "Lead Field Visit",
    filters={"lead": lead_name},
    fields=[
        "name",
        "lead",
        "visit_on",
        "visited_by",
        "visit_notes",
        "visit_image",
        "creation",
        "modified",
    ],
    order_by="visit_on desc, creation desc",
    limit_page_length=limit,
)

full_names = {}
for log in logs:
    user = clean_text(log.get("visited_by"))
    if user and user not in full_names:
        full_names[user] = clean_text(frappe.db.get_value("User", user, "full_name")) or user
    log["visited_by_name"] = full_names.get(user, user)

frappe.response["message"] = {
    "logs": logs,
    "lead": lead_name,
}
