from __future__ import annotations

app_name = "namar_custom"
app_title = "Namar Customizations"
app_publisher = "Namar"
app_description = "Production-safe Namar ERPNext customizations."
app_icon = "octicon octicon-file-directory"
app_color = "grey"
app_email = "badrarroug@namar.net"
app_license = "MIT"

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
