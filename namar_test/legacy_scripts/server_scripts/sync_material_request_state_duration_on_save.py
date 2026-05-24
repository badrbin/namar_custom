entry_field = "custom_workflow_state_entered_at"
duration_field = "custom_workflow_state_duration"
state_field = "custom_workflow_state_duration_state"

mr_meta = frappe.get_meta("Material Request")
has_state_field = mr_meta.has_field(state_field)


def is_completed_status(status):
    status_text = (status or "").strip()
    return status_text in ("Completed", "مكتمل", "Cancelled", "Canceled", "ملغي", "Rejected", "مرفوض")


def is_duration_excluded_state(state):
    if not state:
        return False
    excluded = frappe.db.get_value("Workflow State", state, "custom_mr_duration_excluded")
    return excluded == 1 or excluded == "1" or excluded is True


if mr_meta.has_field(entry_field) and mr_meta.has_field(duration_field):
    current_state = (doc.get("workflow_state") or "").strip()
    cached_state = (doc.get(state_field) or "").strip() if has_state_field else ""
    previous_state = None

    if not doc.is_new():
        previous_state = (frappe.db.get_value("Material Request", doc.name, "workflow_state") or "").strip()

    now_dt = frappe.utils.now_datetime()

    if current_state:
        if doc.is_new() or not doc.get(entry_field) or cached_state != current_state or previous_state != current_state:
            doc.set(entry_field, now_dt)
            if has_state_field:
                doc.set(state_field, current_state)

        entered_at = doc.get(entry_field)
        duration_seconds = 0
        if is_completed_status(doc.get("status")) or is_duration_excluded_state(current_state):
            duration_seconds = 0
        elif entered_at:
            duration_seconds = frappe.utils.time_diff_in_seconds(now_dt, entered_at)
        doc.set(duration_field, max(int(duration_seconds or 0), 0))
    else:
        doc.set(entry_field, None)
        doc.set(duration_field, 0)
        if has_state_field:
            doc.set(state_field, None)
