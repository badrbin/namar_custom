from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from typing import Any


Sanitizer = Callable[[str], str]


def _row_value(row: Any, fieldname: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        value = row.get(fieldname)
    elif hasattr(row, "get"):
        value = row.get(fieldname)
    else:
        value = getattr(row, fieldname, default)
    return default if value is None else value


def parse_version_data(version_data: str | Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    if not version_data:
        return None

    try:
        data = json.loads(version_data) if isinstance(version_data, str) else version_data
    except (TypeError, ValueError):
        return None

    if not isinstance(data, Mapping):
        return None
    return data


def extract_content_change(version_data: str | Mapping[str, Any] | None) -> tuple[str, str] | None:
    """Return the old/new Comment content stored in a Frappe Version diff."""

    data = parse_version_data(version_data)
    if not data:
        return None

    for change in data.get("changed") or []:
        if not isinstance(change, (list, tuple)) or len(change) < 3 or change[0] != "content":
            continue
        old_content = "" if change[1] is None else str(change[1])
        new_content = "" if change[2] is None else str(change[2])
        return old_content, new_content

    return None


def build_comment_histories(
    comments: Iterable[Any],
    versions: Iterable[Any],
    *,
    full_names: Mapping[str, str] | None = None,
    sanitize: Sanitizer | None = None,
) -> dict[str, dict[str, Any]]:
    """Build newest-first, display-ready revision history for linked comments."""

    comment_names = {
        str(_row_value(comment, "name", ""))
        for comment in comments
        if _row_value(comment, "name")
    }
    names = full_names or {}
    clean = sanitize or (lambda value: value)
    histories: dict[str, list[dict[str, Any]]] = {name: [] for name in comment_names}

    ordered_versions = sorted(
        versions,
        key=lambda row: (
            str(_row_value(row, "creation", "")),
            str(_row_value(row, "name", "")),
        ),
    )
    for version in ordered_versions:
        comment_name = str(_row_value(version, "docname", ""))
        if comment_name not in histories:
            continue

        content_change = extract_content_change(_row_value(version, "data"))
        if not content_change:
            continue

        old_content, _new_content = content_change
        edited_by = str(_row_value(version, "owner", ""))
        version_data = parse_version_data(_row_value(version, "data")) or {}
        impersonated_by = str(version_data.get("impersonated_by") or "")
        audit_user = str(version_data.get("audit_user") or "")
        histories[comment_name].append(
            {
                "edited_at": _row_value(version, "creation"),
                "edited_by": edited_by,
                "edited_by_full_name": names.get(edited_by) or edited_by,
                "impersonated_by": impersonated_by,
                "impersonated_by_full_name": names.get(impersonated_by) or impersonated_by,
                "audit_user": audit_user,
                "audit_user_full_name": names.get(audit_user) or audit_user,
                "before_content": clean(old_content),
                "after_content": clean(_new_content),
            }
        )

    response: dict[str, dict[str, Any]] = {}
    for comment_name, revisions in histories.items():
        if not revisions:
            continue

        revisions[0]["is_earliest_recorded"] = True
        for index, revision in enumerate(revisions, start=1):
            revision["edit_number"] = index
            revision.setdefault("is_earliest_recorded", False)

        latest = revisions[-1]
        response[comment_name] = {
            "edit_count": len(revisions),
            "last_edited_at": latest["edited_at"],
            "last_edited_by": latest["edited_by"],
            "last_edited_by_full_name": latest["edited_by_full_name"],
            "revisions": list(reversed(revisions)),
        }

    return response


def _sanitize_comment_content(content: str) -> str:
    import frappe

    return frappe.utils.sanitize_html(
        content,
        always_sanitize=True,
        disallowed_tags=["form", "input", "button", "script", "style"],
    )


def _get_user_full_names(usernames: set[str]) -> dict[str, str]:
    import frappe

    if not usernames:
        return {}

    return {
        row.name: row.full_name or row.name
        for row in frappe.get_all(
            "User",
            filters={"name": ["in", sorted(usernames)]},
            fields=["name", "full_name"],
        )
    }


def _validate_reference(reference_doctype: str | None, reference_name: str | None) -> tuple[str, str]:
    import frappe
    from frappe import _
    from frappe.utils import cstr

    doctype = cstr(reference_doctype).strip()
    name = cstr(reference_name).strip()
    if not doctype or not name:
        frappe.throw(_("Document type and name are required."))

    reference_doc = frappe.get_doc(doctype, name)
    reference_doc.check_permission("read")
    return doctype, name


def _load_comment_history_rows(reference_doctype: str, reference_name: str):
    import frappe

    comments = frappe.get_all(
        "Comment",
        filters={
            "reference_doctype": reference_doctype,
            "reference_name": reference_name,
            "comment_type": "Comment",
        },
        fields=["name"],
    )
    comment_names = [row.name for row in comments]
    if not comment_names:
        return comments, []

    versions = frappe.get_all(
        "Version",
        filters={
            "ref_doctype": "Comment",
            "docname": ["in", comment_names],
        },
        fields=["name", "docname", "owner", "creation", "data"],
        order_by="creation asc, name asc",
    )
    return comments, versions


def _collect_version_usernames(versions: Iterable[Any]) -> set[str]:
    usernames: set[str] = set()
    for version in versions:
        owner = str(_row_value(version, "owner", ""))
        if owner:
            usernames.add(owner)
        version_data = parse_version_data(_row_value(version, "data")) or {}
        for fieldname in ("impersonated_by", "audit_user"):
            username = str(version_data.get(fieldname) or "")
            if username:
                usernames.add(username)
    return usernames


def get_comment_edit_history(reference_doctype: str | None = None, reference_name: str | None = None):
    """Return visible Comment revisions after enforcing read access to the parent document."""

    doctype, name = _validate_reference(reference_doctype, reference_name)
    comments, versions = _load_comment_history_rows(doctype, name)
    histories = build_comment_histories(
        comments,
        versions,
        full_names=_get_user_full_names(_collect_version_usernames(versions)),
        sanitize=_sanitize_comment_content,
    )
    return {"histories": histories}


try:
    import frappe
except ImportError:  # Keep pure helpers importable in local unit tests without Frappe.
    frappe = None
else:
    get_comment_edit_history = frappe.whitelist()(get_comment_edit_history)
