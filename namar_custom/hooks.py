from __future__ import annotations

app_name = "namar_custom"
app_title = "Namar Customizations"
app_publisher = "Namar"
app_description = "Production-safe Namar ERPNext customizations."
app_icon = "octicon octicon-file-directory"
app_color = "grey"
app_email = "badrarroug@namar.net"
app_license = "MIT"

app_include_js = [
    "comment_history.bundle.js",
    "/assets/namar_custom/js/delivery_components/material_request_delivery_components.js",
]

app_include_css = [
    "comment_history.bundle.css",
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
    "Comment": {
        "on_update": "namar_custom.comment_mentions.notify_mentions_on_comment_update",
    },
    "Material Request": {
        "before_insert": "namar_custom.delivery_components.tracking_codes.ensure_material_request_tracking_code",
    },
}
