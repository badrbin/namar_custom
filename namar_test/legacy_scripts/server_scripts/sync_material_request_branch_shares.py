def clean_text(value):
    return (value or "").strip()


def has_required_setup(
    share_doctype="Material Request",
    share_field="custom_shared_branches",
    shared_branch_child="Material Request Shared Branch",
    log_doctype="Material Request Branch Share Log",
):
    return (
        frappe.get_meta(share_doctype).has_field(share_field)
        and frappe.db.exists("DocType", shared_branch_child)
        and frappe.db.exists("DocType", log_doctype)
    )


def get_selected_branches(current_doc, share_field="custom_shared_branches", clean=clean_text):
    branches = []
    seen = {}
    for row in current_doc.get(share_field) or []:
        branch = clean(row.get("branch"))
        if branch and branch not in seen:
            branches.append(branch)
            seen[branch] = 1
    return branches


def merge_branches(*branch_lists, clean=clean_text):
    branches = []
    seen = {}
    for branch_list in branch_lists:
        for branch_value in branch_list or []:
            branch = clean(branch_value)
            if branch and branch not in seen:
                branches.append(branch)
                seen[branch] = 1
    return branches


def get_amended_shared_branches(current_doc, share_field="custom_shared_branches", clean=clean_text, get_branches=get_selected_branches):
    amended_from = clean(current_doc.get("amended_from"))
    if not amended_from or not frappe.db.exists("Material Request", amended_from):
        return []
    return get_branches(frappe.get_doc("Material Request", amended_from), share_field, clean)


def insert_shared_branches(current_doc, branches, share_field="custom_shared_branches", child_doctype="Material Request Shared Branch"):
    idx = 1
    for branch in branches:
        frappe.get_doc(
            {
                "doctype": child_doctype,
                "parent": current_doc.name,
                "parenttype": "Material Request",
                "parentfield": share_field,
                "idx": idx,
                "branch": branch,
            }
        ).insert(ignore_permissions=True)
        idx = idx + 1


def get_users_for_branches(branches):
    if not branches:
        return []

    return frappe.db.sql(
        """
        SELECT DISTINCT
            up.for_value AS branch,
            up.user AS user
        FROM `tabUser Permission` up
        INNER JOIN `tabUser` u ON u.name = up.user
        WHERE up.allow = 'Branch'
          AND up.for_value IN %(branches)s
          AND IFNULL(u.enabled, 0) = 1
          AND up.user NOT IN ('Guest', 'Administrator')
          AND (
                IFNULL(up.apply_to_all_doctypes, 0) = 1
                OR IFNULL(up.applicable_for, '') IN ('', 'Material Request')
          )
        """,
        {"branches": tuple(branches)},
        as_dict=True,
    )


def get_docshare_name(current_doc, user, share_doctype="Material Request"):
    return frappe.db.get_value(
        "DocShare",
        {
            "share_doctype": share_doctype,
            "share_name": current_doc.name,
            "user": user,
            "everyone": 0,
        },
        "name",
    )


def create_or_update_share(current_doc, user, share_doctype="Material Request", get_existing_docshare=get_docshare_name):
    existing = get_existing_docshare(current_doc, user)
    if existing:
        frappe.db.set_value(
            "DocShare",
            existing,
            {
                "read": 1,
                "write": 1,
                "share": 0,
                "submit": 1,
                "notify_by_email": 0,
            },
            update_modified=False,
        )
        return {"name": existing, "created": False}

    share_doc = frappe.get_doc(
        {
            "doctype": "DocShare",
            "user": user,
            "share_doctype": share_doctype,
            "share_name": current_doc.name,
            "read": 1,
            "write": 1,
            "share": 0,
            "submit": 1,
            "everyone": 0,
            "notify_by_email": 0,
        }
    )
    share_doc.insert(ignore_permissions=True)
    return {"name": share_doc.name, "created": True}


def create_log(current_doc, branch, user, docshare_name, log_doctype="Material Request Branch Share Log"):
    frappe.get_doc(
        {
            "doctype": log_doctype,
            "material_request": current_doc.name,
            "branch": branch,
            "user": user,
            "docshare": docshare_name,
        }
    ).insert(ignore_permissions=True)


def remove_log_and_managed_share(row, log_doctype="Material Request Branch Share Log"):
    docshare_name = row.get("docshare")
    frappe.delete_doc(log_doctype, row.get("name"), ignore_permissions=True)
    if docshare_name and frappe.db.exists("DocShare", docshare_name) and not frappe.db.exists(log_doctype, {"docshare": docshare_name}):
        frappe.delete_doc("DocShare", docshare_name, ignore_permissions=True)


if doc.name and has_required_setup():
    manual_branches = get_selected_branches(doc)
    amended_branches = []
    if not manual_branches:
        amended_branches = get_amended_shared_branches(doc)
        if amended_branches:
            insert_shared_branches(doc, amended_branches)
    selected_branches = merge_branches(manual_branches, amended_branches)

    desired = {}
    for row in get_users_for_branches(selected_branches):
        branch = clean_text(row.get("branch"))
        user = clean_text(row.get("user"))
        if not branch or not user or user == doc.owner:
            continue
        desired[(branch, user)] = 1

    existing_logs = frappe.get_all(
        "Material Request Branch Share Log",
        filters={"material_request": doc.name},
        fields=["name", "branch", "user", "docshare"],
    )
    existing_by_key = {}
    for row in existing_logs:
        key = (clean_text(row.get("branch")), clean_text(row.get("user")))
        existing_by_key[key] = row

    for key in existing_by_key:
        row = existing_by_key.get(key)
        if key not in desired:
            remove_log_and_managed_share(row)

    for key in desired:
        branch = key[0]
        user = key[1]
        current_log = existing_by_key.get(key)
        if current_log and current_log.get("docshare") and frappe.db.exists("DocShare", current_log.get("docshare")):
            create_or_update_share(doc, user)
            continue
        share_result = create_or_update_share(doc, user)
        docshare_name = share_result.get("name")
        if not current_log:
            create_log(doc, branch, user, docshare_name)
        elif not current_log.get("docshare") and docshare_name:
            frappe.db.set_value("Material Request Branch Share Log", current_log.get("name"), "docshare", docshare_name, update_modified=False)
