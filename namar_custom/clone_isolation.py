"""Disable copied integrations around migration on the explicitly isolated clone.

This is a migration hook, not an RPC. It never connects to another site and
does not commit: Frappe owns the surrounding migration transaction. Keep the
target and column allowlist explicit; never derive them from restored data.
"""
from __future__ import annotations

import frappe


CLONE_SITE = "namar15-frankfurt.f.frappe.cloud"
ISOLATION_FLAG = "namar_frankfurt_clone_isolation"
DISABLED_FIELDS = (
    ("Company", ("custom_zatca_invoice_enabled",)),
    ("Email Account", ("enable_outgoing", "enable_incoming")),
    ("Notification", ("enabled",)),
    ("Webhook", ("enabled",)),
)


def disable_copied_integrations():
    """Run around migration patches only for the target with explicit opt-in.

    SQL intentionally bypasses document events: saving an integration document
    could itself trigger a copied Webhook or Notification. Missing schema is
    normal for a new site. Other errors must abort migration, not be ignored.
    """
    if getattr(frappe.local, "site", None) != CLONE_SITE:
        return {"state": "skipped", "reason": "different_site"}
    if frappe.conf.get(ISOLATION_FLAG) not in (True, 1, "1"):
        return {"state": "skipped", "reason": "flag_disabled"}

    guarded = {}
    for doctype, requested_fields in DISABLED_FIELDS:
        if not frappe.db.table_exists(doctype, cached=False):
            continue
        columns = set(frappe.db.get_table_columns(doctype))
        fields = tuple(field for field in requested_fields if field in columns)
        if not fields:
            continue

        # Identifiers come only from the constant allowlist above. The WHERE
        # clause makes repeat migrations leave already-disabled rows untouched.
        assignments = ", ".join(f"`{field}` = %s" for field in fields)
        enabled = " OR ".join(f"COALESCE(`{field}`, 0) != 0" for field in fields)
        frappe.db.sql(
            f"UPDATE `tab{doctype}` SET {assignments} WHERE {enabled}",
            (0,) * len(fields),
        )
        guarded[doctype] = list(fields)

    if guarded:
        # A preceding app hook may have loaded a copied configuration document.
        # Clear this site's caches before any patch can use the old enable flag.
        frappe.clear_cache()
    return {"state": "isolated", "guarded_fields": guarded}
