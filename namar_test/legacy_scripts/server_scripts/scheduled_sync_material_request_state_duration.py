entry_field = "custom_workflow_state_entered_at"
duration_field = "custom_workflow_state_duration"
state_field = "custom_workflow_state_duration_state"
mr_meta = frappe.get_meta("Material Request")
has_state_field = mr_meta.has_field(state_field)

excluded_states = {}
for excluded_row in frappe.db.sql(
    """
    SELECT name
    FROM `tabWorkflow State`
    WHERE IFNULL(custom_mr_duration_excluded, 0) = 1
    """,
    as_dict=True,
):
    excluded_states[(excluded_row.name or "").strip()] = 1


def is_completed_status(status):
    status_text = (status or "").strip()
    return status_text in ("Completed", "مكتمل", "Cancelled", "Canceled", "ملغي", "Rejected", "مرفوض")


def should_zero_duration(status, state):
    return is_completed_status(status) or bool(excluded_states.get(state))


def get_workflow_version_entered_at(material_request_name, state):
    latest_workflow_version_at = None
    versions = frappe.db.sql(
        """
        SELECT creation, data
        FROM `tabVersion`
        WHERE ref_doctype = 'Material Request'
          AND docname = %s
          AND data LIKE %s
        ORDER BY creation DESC
        LIMIT 100
        """,
        (material_request_name, "%workflow_state%"),
        as_dict=True,
    )

    for version in versions:
        if not latest_workflow_version_at:
            latest_workflow_version_at = version.creation
        try:
            data = frappe.parse_json(version.data or "{}") or {}
        except Exception:
            data = {}
        for change in data.get("changed") or []:
            if len(change) >= 3 and change[0] == "workflow_state" and (change[2] or "").strip() == state:
                return version.creation
    return latest_workflow_version_at


def get_workflow_comment_entered_at(material_request_name, state):
    comments = frappe.db.sql(
        """
        SELECT creation
        FROM `tabComment`
        WHERE reference_doctype = 'Material Request'
          AND reference_name = %s
          AND comment_type = 'Workflow'
          AND content = %s
        ORDER BY creation DESC
        LIMIT 1
        """,
        (material_request_name, state),
        as_dict=True,
    )
    if comments:
        return comments[0].creation
    return None


def get_state_entered_at(row, state, cached_state):
    version_entered_at = get_workflow_version_entered_at(row.name, state)
    if version_entered_at:
        return version_entered_at

    comment_entered_at = get_workflow_comment_entered_at(row.name, state)
    if comment_entered_at:
        return comment_entered_at

    if row.custom_workflow_state_entered_at and (not cached_state or cached_state == state):
        return row.custom_workflow_state_entered_at

    return row.modified or row.creation


if mr_meta.has_field(entry_field) and mr_meta.has_field(duration_field):
    now_dt = frappe.utils.now_datetime()
    select_fields = "name, status, workflow_state, creation, modified, custom_workflow_state_entered_at"
    if has_state_field:
        select_fields = select_fields + ", custom_workflow_state_duration_state"
    else:
        select_fields = select_fields + ", NULL AS custom_workflow_state_duration_state"

    rows = frappe.db.sql(
        """
        SELECT """ + select_fields + """
        FROM `tabMaterial Request`
        WHERE IFNULL(workflow_state, '') != ''
        """,
        as_dict=True,
    )

    for row in rows:
        state = (row.workflow_state or "").strip()
        cached_state = (row.custom_workflow_state_duration_state or "").strip()
        entered_at = row.custom_workflow_state_entered_at

        if not entered_at or (cached_state and cached_state != state):
            entered_at = get_state_entered_at(row, state, cached_state)

        if should_zero_duration(row.status, state):
            duration_seconds = 0
        else:
            duration_seconds = frappe.utils.time_diff_in_seconds(now_dt, entered_at)
        update_values = {
            entry_field: entered_at,
            duration_field: max(int(duration_seconds or 0), 0),
        }
        if has_state_field:
            update_values[state_field] = state
        frappe.db.set_value(
            "Material Request",
            row.name,
            update_values,
            update_modified=False,
        )
