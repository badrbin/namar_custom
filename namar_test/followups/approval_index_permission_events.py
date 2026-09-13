"""Invalidate the projection for native permission RPCs that bypass doc hooks.

These supported whitelisted-method overrides delegate validation, permission
checks and mutations to Frappe unchanged. The projection generation changes in
the same transaction: any failure propagates, so a revoked permission cannot be
committed while its old projection remains ready. Never commit in a wrapper.

Native permission mutations made directly by patches/server code must call the
index invalidation API too; RPC overrides do not intercept Python calls or SQL.
"""
from __future__ import annotations

import json
from typing import Any

import frappe
from frappe.core.doctype.user_permission import user_permission
from frappe.core.page.permission_manager import permission_manager


def _invalidate(*, doctype=None, user=None):
    from namar_test.followups.approval_index import request_rebuild

    # Some native paths clear Redis before the SQL transaction commits, allowing
    # a concurrent reader to repopulate the old permissions. Clear the relevant
    # native cache after commit as well, before this wrapper's enqueue callback.
    # The worker separately handles previously queued jobs/generation freshness.
    if doctype:
        frappe.db.after_commit.add(lambda: frappe.clear_cache(doctype=doctype))
    elif user:
        frappe.db.after_commit.add(lambda: frappe.clear_cache(user=user))
    request_rebuild("native_permission_rpc_changed")


@frappe.whitelist(methods=["POST"])
def update_role_permission(
    doctype: str,
    role: str,
    permlevel: int,
    ptype: str,
    value: str | int | None = None,
    if_owner: str | int = 0,
) -> str | None:
    result = permission_manager.update(
        doctype=doctype, role=role, permlevel=permlevel, ptype=ptype,
        value=value, if_owner=if_owner,
    )
    _invalidate(doctype=doctype)
    return result


@frappe.whitelist(methods=["POST"])
def remove_role_permission(doctype: str, role: str, permlevel: int, if_owner: str | int = 0):
    result = permission_manager.remove(
        doctype=doctype, role=role, permlevel=permlevel, if_owner=if_owner,
    )
    _invalidate(doctype=doctype)
    return result


@frappe.whitelist(methods=["POST"])
def reset_role_permissions(doctype: str):
    result = permission_manager.reset(doctype=doctype)
    _invalidate(doctype=doctype)
    return result


@frappe.whitelist(methods=["POST"])
def clear_user_permissions(user: str, for_doctype: str):
    result = user_permission.clear_user_permissions(user=user, for_doctype=for_doctype)
    _invalidate(user=user)
    return result


@frappe.whitelist(methods=["POST"])
def update_user_permissions(data: str | dict[str, Any]):
    result = user_permission.add_user_permissions(data=data)
    payload = json.loads(data) if isinstance(data, str) else data
    _invalidate(user=payload.get("user"))
    return result
