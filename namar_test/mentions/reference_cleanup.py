"""Remove work-inbox derivatives only when their source row has been deleted.

These functions deliberately use database deletion: closing ToDos or invoking
document deletion hooks would create new activity for a source that is gone.
The caller owns the transaction; this module never commits or queues cleanup.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import frappe

from namar_test.followups.logic import MAX_REFERENCE_LENGTH


THREAD_DOCTYPE = "Namar Mention Thread"
EVENT_DOCTYPE = "Namar Mention Event"
BATCH_SIZE = 500
REFERENCE_FIELDS = {
    THREAD_DOCTYPE: ("reference_doctype", "reference_name"),
    "ToDo": ("reference_type", "reference_name"),
    "Workflow Action": ("reference_doctype", "reference_name"),
    "Notification Log": ("document_type", "document_name"),
}
COUNT_KEYS = {
    THREAD_DOCTYPE: "threads",
    EVENT_DOCTYPE: "events",
    "ToDo": "todos",
    "Workflow Action": "workflow_actions",
    "Notification Log": "notifications",
}
SCHEMA_FLAGS = ("for_reload", "in_migrate", "in_install", "in_uninstall")


def _value(value: Any, key: str, default: Any = None) -> Any:
    if hasattr(value, "get"):
        return value.get(key, default)
    return getattr(value, key, default)


def _identifier(value: Any) -> bool:
    # Never trim, truncate or coerce a destructive target into a different name.
    return bool(
        isinstance(value, str)
        and value
        and value == value.strip()
        and len(value) <= MAX_REFERENCE_LENGTH
        and not any(ord(char) < 32 for char in value)
    )


def _stored_meta(doctype: str):
    if not _identifier(doctype) or not frappe.db.exists("DocType", doctype):
        return None
    meta = frappe.get_meta(doctype)
    if _value(meta, "issingle") or _value(meta, "is_virtual"):
        return None
    if not frappe.db.table_exists(doctype):
        return None
    return meta


def _counts() -> dict[str, int]:
    return {**dict.fromkeys(COUNT_KEYS.values(), 0), "child_rows": 0}


def _batches(values: list[str]):
    for offset in range(0, len(values), BATCH_SIZE):
        yield values[offset : offset + BATCH_SIZE]


def _names(doctype: str, filters: dict, *, for_update: bool = False) -> list[str]:
    if not filters:
        raise ValueError("A cleanup query requires an exact reference or parent filter")
    return list(
        frappe.db.get_values(
            doctype, filters, "name", pluck=True, for_update=for_update, order_by="name"
        ) or []
    )


def _remove_rows(doctype: str, filters: dict, *, dry_run: bool) -> tuple[int, int]:
    meta = _stored_meta(doctype)
    if not meta:
        return 0, 0
    names = _names(doctype, filters, for_update=not dry_run)
    if not names:
        return 0, 0
    child_count = 0
    for batch in _batches(names):
        for field in meta.get_table_fields():
            child_doctype = _value(field, "options")
            fieldname = _value(field, "fieldname")
            child_meta = _stored_meta(child_doctype)
            if not fieldname or not child_meta or not _value(child_meta, "istable"):
                continue
            child_filters = {
                "parenttype": doctype,
                "parentfield": fieldname,
                "parent": ["in", batch],
            }
            child_count += len(_names(child_doctype, child_filters))
            if not dry_run:
                frappe.db.delete(child_doctype, child_filters)
        if not dry_run:
            frappe.db.delete(doctype, {**filters, "name": ["in", batch]})
    return len(names), child_count


def purge_reference_followups(
    reference_doctype: str, reference_name: str, *, dry_run: bool = False
) -> dict[str, int]:
    """Purge one missing source's derivatives, irrespective of user or status.

    Unknown, virtual, single, malformed and still-existing references are left
    untouched. This is an internal administrative helper, not a whitelisted API.
    """
    counts = _counts()
    if not _identifier(reference_name) or not _stored_meta(reference_doctype):
        return counts
    # A locking read also rejects a source restored between scan and cleanup.
    if frappe.db.get_value(
        reference_doctype, {"name": reference_name}, "name", for_update=not dry_run
    ):
        return counts

    if _stored_meta(THREAD_DOCTYPE):
        thread_names = _names(
            THREAD_DOCTYPE,
            {"reference_doctype": reference_doctype, "reference_name": reference_name},
            for_update=not dry_run,
        )
        # Mention writers lock the thread before appending events. Hold that
        # same lock until commit, so a reply cannot leave an event behind here.
        for batch in _batches(thread_names):
            rows, children = _remove_rows(EVENT_DOCTYPE, {"thread": ["in", batch]}, dry_run=dry_run)
            counts["events"] += rows
            counts["child_rows"] += children

    for doctype, (type_field, name_field) in REFERENCE_FIELDS.items():
        rows, children = _remove_rows(
            doctype,
            {type_field: reference_doctype, name_field: reference_name},
            dry_run=dry_run,
        )
        counts[COUNT_KEYS[doctype]] += rows
        counts["child_rows"] += children
    return counts


def cleanup_deleted_reference(doc: Any, method: str | None = None) -> None:
    """Run synchronously after a normal Frappe deletion, never on cancel/reload."""
    flags = _value(doc, "flags")
    if not _value(flags, "in_delete"):
        # Frappe's for_reload path also runs after_delete but skips in_delete.
        # Administrative ignore_on_trash bypasses use the explicit purge helper.
        return
    if any(
        _value(flags, key) or _value(getattr(frappe, "flags", None), key)
        for key in SCHEMA_FLAGS
    ):
        return
    purge_reference_followups(_value(doc, "doctype"), _value(doc, "name"))


def _missing_references() -> tuple[list[tuple[str, str]], int, int]:
    references: set[tuple[str, str]] = set()
    invalid_count = 0
    # Notifications alone do not establish a work item to be migrated.
    for doctype in (THREAD_DOCTYPE, "ToDo", "Workflow Action"):
        if not _stored_meta(doctype):
            continue
        type_field, name_field = REFERENCE_FIELDS[doctype]
        rows = frappe.db.get_values(
            doctype, filters={}, fieldname=[type_field, name_field], as_dict=True, distinct=True
        )
        for row in rows or []:
            source_type, source_name = _value(row, type_field), _value(row, name_field)
            if not _identifier(source_type) or not _identifier(source_name):
                invalid_count += 1
                continue
            references.add((source_type, source_name))

    by_doctype: dict[str, list[str]] = defaultdict(list)
    for doctype, name in sorted(references):
        by_doctype[doctype].append(name)
    missing: list[tuple[str, str]] = []
    skipped = invalid_count
    for doctype, names in by_doctype.items():
        if not _stored_meta(doctype):
            skipped += len(names)
            continue
        for batch in _batches(names):
            existing = set(
                frappe.db.get_values(doctype, {"name": ["in", batch]}, "name", pluck=True) or []
            )
            missing.extend((doctype, name) for name in batch if name not in existing)
    return missing, len(references) + invalid_count, skipped


def _purge_missing(*, dry_run: bool) -> dict[str, Any]:
    missing, scanned, skipped = _missing_references()
    totals = _counts()
    for doctype, name in missing:
        counts = purge_reference_followups(doctype, name, dry_run=dry_run)
        for key, value in counts.items():
            totals[key] += value
    return {
        "references_scanned": scanned,
        "missing_references": len(missing),
        "skipped_references": skipped,
        "counts": totals,
    }


def scan_deleted_reference_followups() -> dict[str, Any]:
    """Return a read-only count summary, without names, users or message text."""
    return _purge_missing(dry_run=True)


def purge_deleted_reference_followups() -> dict[str, Any]:
    """Reconcile old missing references in the caller's migration transaction."""
    return _purge_missing(dry_run=False)
