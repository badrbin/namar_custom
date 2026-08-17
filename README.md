# Namar Customizations

Production-safe ERPNext/Frappe customizations for Namar.

This production branch contains only approved production customizations. Delivery component tracking remains separate from the independent delivery supply manifest shown on Material Request.

## Independent delivery supply manifest

The Material Request manufacturing tab keeps barcode-tracked sectors in the manufacturing dashboard and renders a separate delivery list containing request items and every active, non-excluded delivery component. The delivery list has no QR or readiness state and does not affect door or sector manufacturing status.

## Comment edit mention notifications

Frappe sends `@mention` notifications when a comment is created, but not when an existing comment is edited. This app adds a `Comment.on_update` hook so any text edit to a comment that currently contains mentions notifies all current mentioned users.

A quick kill-switch is available via site config:

```text
namar_enable_comment_edit_mentions = 0
```

Default behavior is enabled.
