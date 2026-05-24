def clean_text(value):
    return (value or "").strip()


def parse_bool(value):
    return str(value or "").strip().lower() in ("1", "true", "yes", "y", "نعم")


city_branch_map = {
    "مكة": "فرع مكة",
    "الطائف": "فرع مكة",
    "المدينة المنورة": "فرع المدينة",
    "جدة": "فرع جدة2 - مدينة البناء",
}

apply_changes = parse_bool(frappe.form_dict.get("apply"))
limit = frappe.utils.cint(frappe.form_dict.get("limit") or 0)

if not (
    frappe.get_meta("Material Request").has_field("custom_shared_branches")
    and frappe.db.exists("DocType", "Material Request Shared Branch")
    and frappe.db.exists("DocType", "Material Request Branch Share Log")
):
    frappe.throw("إعدادات مشاركة طلب المواد غير مكتملة")

target_branches = tuple(sorted(set(city_branch_map.values())))

users_by_branch = {}
for row in frappe.db.sql(
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
    {"branches": target_branches},
    as_dict=True,
):
    branch = clean_text(row.get("branch"))
    user = clean_text(row.get("user"))
    if branch and user:
        users_by_branch.setdefault(branch, [])
        if user not in users_by_branch[branch]:
            users_by_branch[branch].append(user)

rows = frappe.db.sql(
    """
    SELECT
        mr.name,
        mr.owner,
        mr.territory,
        mr.`الفرع` AS branch
    FROM `tabMaterial Request` mr
    WHERE mr.docstatus != 2
      AND mr.territory IN ('مكة', 'الطائف', 'المدينة المنورة', 'جدة')
      AND (
            (mr.territory IN ('مكة', 'الطائف') AND IFNULL(mr.`الفرع`, '') != 'فرع مكة')
            OR (mr.territory = 'المدينة المنورة' AND IFNULL(mr.`الفرع`, '') != 'فرع المدينة')
            OR (mr.territory = 'جدة' AND IFNULL(mr.`الفرع`, '') != 'فرع جدة2 - مدينة البناء')
      )
      AND (
            NOT EXISTS (
                SELECT 1
                FROM `tabMaterial Request Branch Share Log` log
                WHERE log.material_request = mr.name
                  AND log.branch = CASE
                        WHEN mr.territory IN ('مكة', 'الطائف') THEN 'فرع مكة'
                        WHEN mr.territory = 'المدينة المنورة' THEN 'فرع المدينة'
                        WHEN mr.territory = 'جدة' THEN 'فرع جدة2 - مدينة البناء'
                        ELSE ''
                      END
            )
            OR NOT EXISTS (
                SELECT 1
                FROM `tabMaterial Request Shared Branch` sb
                WHERE sb.parent = mr.name
                  AND sb.parenttype = 'Material Request'
                  AND sb.parentfield = 'custom_shared_branches'
                  AND sb.branch = CASE
                        WHEN mr.territory IN ('مكة', 'الطائف') THEN 'فرع مكة'
                        WHEN mr.territory = 'المدينة المنورة' THEN 'فرع المدينة'
                        WHEN mr.territory = 'جدة' THEN 'فرع جدة2 - مدينة البناء'
                        ELSE ''
                      END
            )
      )
    ORDER BY mr.name
    """,
    as_dict=True,
)
if limit:
    rows = rows[:limit]

summary = {
    "apply": apply_changes,
    "count": len(rows),
    "by_target_branch": {},
    "processed": 0,
    "docshares_created": 0,
    "docshares_updated": 0,
    "logs_created": 0,
    "branch_rows_created": 0,
    "samples": [],
}


def get_target_branch(territory):
    return city_branch_map.get(clean_text(territory))


def get_docshare_name(request_name, user):
    return frappe.db.get_value(
        "DocShare",
        {
            "share_doctype": "Material Request",
            "share_name": request_name,
            "user": user,
            "everyone": 0,
        },
        "name",
    )


def create_or_update_docshare(request_name, user):
    existing = get_docshare_name(request_name, user)
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

    docshare = frappe.get_doc(
        {
            "doctype": "DocShare",
            "user": user,
            "share_doctype": "Material Request",
            "share_name": request_name,
            "read": 1,
            "write": 1,
            "share": 0,
            "submit": 1,
            "everyone": 0,
            "notify_by_email": 0,
        }
    )
    docshare.insert(ignore_permissions=True)
    return {"name": docshare.name, "created": True}


def has_share_log(request_name, branch, user):
    return frappe.db.exists(
        "Material Request Branch Share Log",
        {
            "material_request": request_name,
            "branch": branch,
            "user": user,
        },
    )


def create_share_log(request_name, branch, user, docshare_name):
    frappe.get_doc(
        {
            "doctype": "Material Request Branch Share Log",
            "material_request": request_name,
            "branch": branch,
            "user": user,
            "docshare": docshare_name,
        }
    ).insert(ignore_permissions=True)


def has_shared_branch_row(request_name, branch):
    return frappe.db.exists(
        "Material Request Shared Branch",
        {
            "parent": request_name,
            "parenttype": "Material Request",
            "parentfield": "custom_shared_branches",
            "branch": branch,
        },
    )


def create_shared_branch_row(request_name, branch):
    max_idx = frappe.db.sql(
        """
        SELECT COALESCE(MAX(idx), 0)
        FROM `tabMaterial Request Shared Branch`
        WHERE parent = %s
          AND parenttype = 'Material Request'
          AND parentfield = 'custom_shared_branches'
        """,
        request_name,
    )[0][0]
    frappe.get_doc(
        {
            "doctype": "Material Request Shared Branch",
            "parent": request_name,
            "parenttype": "Material Request",
            "parentfield": "custom_shared_branches",
            "idx": frappe.utils.cint(max_idx) + 1,
            "branch": branch,
            "auto_shared": 1,
        }
    ).insert(ignore_permissions=True)


for row in rows:
    request_name = clean_text(row.get("name"))
    owner = clean_text(row.get("owner"))
    target_branch = get_target_branch(row.get("territory"))
    if not request_name or not target_branch:
        continue

    summary["by_target_branch"][target_branch] = summary["by_target_branch"].get(target_branch, 0) + 1

    if not apply_changes:
        continue

    summary["processed"] = summary["processed"] + 1
    if not has_shared_branch_row(request_name, target_branch):
        create_shared_branch_row(request_name, target_branch)
        summary["branch_rows_created"] = summary["branch_rows_created"] + 1

    for user in users_by_branch.get(target_branch) or []:
        if not user or user == owner:
            continue

        if has_share_log(request_name, target_branch, user):
            continue

        docshare_result = create_or_update_docshare(request_name, user)
        docshare_name = docshare_result.get("name")
        created = docshare_result.get("created")
        if created:
            summary["docshares_created"] = summary["docshares_created"] + 1
        else:
            summary["docshares_updated"] = summary["docshares_updated"] + 1

        create_share_log(request_name, target_branch, user, docshare_name if created else "")
        summary["logs_created"] = summary["logs_created"] + 1

    if len(summary["samples"]) < 20:
        summary["samples"].append(
            {
                "name": request_name,
                "territory": row.get("territory"),
                "branch": row.get("branch"),
                "target_branch": target_branch,
                "users": len(users_by_branch.get(target_branch) or []),
            }
        )

frappe.response["message"] = summary
