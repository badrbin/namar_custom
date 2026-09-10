"""Require reference Read permission before accessing Frappe activity APIs."""
from __future__ import annotations

import frappe
from frappe.desk.form import activity


def _check_reference_read(doctype: str, name: str | int) -> None:
    frappe.get_doc(doctype, name).check_permission("read")


@frappe.whitelist()
def get_activity_timeline(
    doctype: str,
    name: str | int,
    visible_types: list[str | dict[str, list[str]]] | str | None = None,
) -> dict:
    _check_reference_read(doctype, name)
    return activity.get_activity_timeline(doctype, name, visible_types=visible_types)


@frappe.whitelist()
def get_more_email_activities(doctype: str, name: str | int, start: int) -> dict:
    _check_reference_read(doctype, name)
    return activity.get_more_email_activities(doctype, name, start=start)


@frappe.whitelist()
def get_more_milestone_activities(doctype: str, name: str | int, start: int) -> dict:
    _check_reference_read(doctype, name)
    return activity.get_more_milestone_activities(doctype, name, start=start)
