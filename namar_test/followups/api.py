from __future__ import annotations

import frappe

from namar_test.followups import service


@frappe.whitelist()
def get_followups(
    bucket: str = "all",
    search: str = "",
    limit_start: int | str = 0,
    page_length: int | str = 50,
    priority: str = "",
):
    return service.get_followups(
        bucket=bucket,
        search=search,
        priority=priority,
        limit_start=limit_start,
        page_length=page_length,
    )


@frappe.whitelist()
def get_followup_detail(todo_name: str):
    return service.get_followup_detail(todo_name)


@frappe.whitelist(methods=["POST"])
def complete_followup(todo_name: str, result: str):
    return service.complete_followup(todo_name, result)


@frappe.whitelist(methods=["POST"])
def complete_and_schedule_next(
    todo_name: str,
    result: str,
    next_date: str,
    description: str | None = None,
    priority: str | None = None,
):
    return service.complete_and_schedule_next(
        todo_name,
        result,
        next_date,
        description=description,
        priority=priority,
    )


@frappe.whitelist(methods=["POST"])
def reschedule_followup(todo_name: str, new_date: str):
    return service.reschedule_followup(todo_name, new_date)


@frappe.whitelist(methods=["POST"])
def add_followup_note(todo_name: str, note: str):
    return service.add_followup_note(todo_name, note)


@frappe.whitelist(methods=["POST"])
def create_followup(
    reference_type: str,
    reference_name: str,
    description: str,
    due_date: str,
    priority: str = "Medium",
    allocated_to: str | None = None,
):
    return service.create_followup(
        reference_type,
        reference_name,
        description,
        due_date,
        priority=priority,
        allocated_to=allocated_to,
    )


@frappe.whitelist()
def get_approvals(
    search: str = "",
    limit_start: int | str = 0,
    page_length: int | str = 50,
):
    return service.get_approvals(
        search=search,
        limit_start=limit_start,
        page_length=page_length,
    )


@frappe.whitelist()
def get_approval_detail(action_name: str):
    return service.get_approval_detail(action_name)
