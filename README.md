# Namar Customizations

Production-safe ERPNext/Frappe customizations for Namar.

This production branch is intentionally narrow. It currently includes only the edited-comment mention notification hook.

## Comment edit mention notifications

Frappe sends `@mention` notifications when a comment is created, but not when an existing comment is edited. This app adds a `Comment.on_update` hook so any text edit to a comment that currently contains mentions notifies all current mentioned users.

A quick kill-switch is available via site config:

```text
namar_enable_comment_edit_mentions = 0
```

Default behavior is enabled.
