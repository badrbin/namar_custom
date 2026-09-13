from __future__ import annotations

app_name = "namar_custom"
app_title = "Namar Customizations"
app_publisher = "Namar"
app_description = "Production-safe Namar ERPNext customizations."
app_icon = "octicon octicon-file-directory"
app_color = "grey"
app_email = "badrarroug@namar.net"
app_license = "MIT"

override_whitelisted_methods = {
    "frappe.desk.form.activity.get_activity_timeline": "namar_custom.activity_permissions.get_activity_timeline",
    "frappe.desk.form.activity.get_more_email_activities": "namar_custom.activity_permissions.get_more_email_activities",
    "frappe.desk.form.activity.get_more_milestone_activities": "namar_custom.activity_permissions.get_more_milestone_activities",
    "frappe.core.page.permission_manager.permission_manager.update": "namar_custom.followups.approval_index_permission_events.update_role_permission",
    "frappe.core.page.permission_manager.permission_manager.remove": "namar_custom.followups.approval_index_permission_events.remove_role_permission",
    "frappe.core.page.permission_manager.permission_manager.reset": "namar_custom.followups.approval_index_permission_events.reset_role_permissions",
    "frappe.core.doctype.user_permission.user_permission.clear_user_permissions": "namar_custom.followups.approval_index_permission_events.clear_user_permissions",
    "frappe.core.doctype.user_permission.user_permission.add_user_permissions": "namar_custom.followups.approval_index_permission_events.update_user_permissions",
}

permission_query_conditions = {
    "Namar Mention Thread": (
        "namar_custom.namar_custom.doctype.namar_mention_thread."
        "namar_mention_thread.get_permission_query_conditions"
    ),
    "Namar Mention Event": (
        "namar_custom.namar_custom.doctype.namar_mention_event."
        "namar_mention_event.get_permission_query_conditions"
    ),
}

has_permission = {
    "Namar Mention Thread": (
        "namar_custom.namar_custom.doctype.namar_mention_thread."
        "namar_mention_thread.has_permission"
    ),
    "Namar Mention Event": (
        "namar_custom.namar_custom.doctype.namar_mention_event."
        "namar_mention_event.has_permission"
    ),
}

app_include_js = [
    "namar_custom_comment_history.bundle.js",
    "namar_custom_my_followups_navbar.bundle.js",
    "/assets/namar_custom/js/delivery_components/material_request_delivery_components.js",
]

app_include_css = [
    "namar_custom_comment_history.bundle.css",
    "namar_custom_my_followups_navbar.bundle.css",
]

web_include_js = [
    "/assets/namar_custom/js/delivery_components/factory_delivery_components.js",
]

jinja = {
    "methods": ["namar_custom.delivery_components.printing.sector_print_status"],
}

# Keep this production branch intentionally narrow. Do not merge the broad
# test branch into production; add only approved production hooks here.
doc_events = {
    "Workflow": {
        "validate": ["namar_custom.followups.approval_routing_settings.validate_workflow_approval_routing"],
    },
    "*": {
        "on_change": ["namar_custom.followups.approval_index.on_document_change"],
        "on_update_after_submit": ["namar_custom.followups.approval_index.on_document_change"],
        "on_cancel": ["namar_custom.followups.approval_index.on_document_change"],
        "after_rename": ["namar_custom.followups.approval_index.on_document_rename"],
        "after_delete": [
            "namar_custom.mentions.reference_cleanup.cleanup_deleted_reference",
            "namar_custom.followups.approval_index.on_document_change",
        ],
    },
    "ToDo": {
        "on_change": [
            "namar_custom.mentions.events.sync_linked_mentions_on_todo_change"
        ],
        "on_trash": [
            "namar_custom.mentions.events.sync_linked_mentions_on_todo_trash"
        ],
    },
    "Comment": {
        "after_insert": [
            "namar_custom.mentions.events.capture_mentions_after_insert"
        ],
        "on_update": [
            "namar_custom.comment_mentions.notify_mentions_on_comment_update",
            "namar_custom.mentions.events.capture_mentions_on_update",
        ],
    },
    "Notification Log": {
        "before_insert": [
            "namar_custom.mentions.events.link_notification_to_mention_thread"
        ],
    },
    "Material Request": {
        "before_insert": "namar_custom.delivery_components.tracking_codes.ensure_material_request_tracking_code",
    },
}

doctype_js = {
    "Workflow": "public/js/doctype/workflow_approval_routing.js",
}

# The durable Pending rows survive Redis/RQ restarts. A minute recovery tick only
# dispatches bounded work; it never evaluates business documents in the scheduler.
scheduler_events = {
    "cron": {
        "* * * * *": ["namar_custom.followups.approval_index.recover_pending"],
    },
}

after_migrate = ["namar_custom.followups.approval_index.invalidate_after_migrate"]
