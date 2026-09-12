from __future__ import annotations

from contextlib import contextmanager


@contextmanager
def quiet_reference_errors(frappe):
    """Discard only messages from an expected, locally handled access failure."""

    message_log = getattr(frappe, "message_log", None)
    previous_messages = list(message_log) if message_log is not None else None
    flags = getattr(frappe, "flags", None)
    missing = object()
    previous_error = (
        flags.get("error_message", missing)
        if flags is not None and callable(getattr(flags, "get", None))
        else getattr(flags, "error_message", missing)
    )
    try:
        yield
    except (frappe.DoesNotExistError, frappe.PermissionError):
        if previous_messages is not None:
            frappe.message_log[:] = previous_messages
        if flags is not None:
            if previous_error is not missing:
                flags.error_message = previous_error
            elif callable(getattr(flags, "pop", None)):
                flags.pop("error_message", None)
            elif hasattr(flags, "error_message"):
                delattr(flags, "error_message")
        raise


def reference_exists(frappe, doctype: str, name: str, *, for_update: bool = False) -> bool:
    """Check a reference without emitting the missing-document popup.

    A normal row lock serializes queued mention creation with source deletion.
    Singles have no normal row; virtual controllers own their storage/locking.
    """

    if not doctype or not name:
        return False
    try:
        with quiet_reference_errors(frappe):
            meta = frappe.get_meta(doctype)
            if meta.issingle:
                if name != doctype:
                    return False
                if for_update:
                    return bool(frappe.db.get_value("DocType", doctype, "name", for_update=True))
                return True
            if meta.is_virtual:
                kwargs = {"for_update": True} if for_update else {}
                frappe.get_doc(doctype, name, **kwargs)
                return True
            # A string filter equal to the DocType makes Frappe query Singles.
            # Explicit filters keep ordinary records on their own table.
            return bool(frappe.db.get_value(doctype, {"name": name}, "name", for_update=for_update))
    except (frappe.DoesNotExistError, frappe.PermissionError):
        return False


def can_read_reference(frappe, user: str, doctype: str, name: str) -> bool:
    try:
        with quiet_reference_errors(frappe):
            return reference_exists(frappe, doctype, name) and bool(
                frappe.has_permission(doctype, "read", doc=name, user=user)
            )
    except (frappe.DoesNotExistError, frappe.PermissionError):
        return False
