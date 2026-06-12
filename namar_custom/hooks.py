from __future__ import annotations

app_name = "namar_custom"
app_title = "Namar Customizations"
app_publisher = "Namar"
app_description = "Production-safe Namar ERPNext customizations."
app_icon = "octicon octicon-file-directory"
app_color = "grey"
app_email = "badrarroug@namar.net"
app_license = "MIT"

# Keep this production branch intentionally narrow: it currently contains only
# edited-comment mention notifications.  Do not merge the broad `namar_test`
# branch into production; cherry-pick approved changes only.
doc_events = {
    "Comment": {
        "on_update": "namar_custom.comment_mentions.notify_mentions_on_comment_update",
    },
}
