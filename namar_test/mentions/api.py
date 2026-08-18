from __future__ import annotations

import frappe

from namar_test.mentions import service


@frappe.whitelist()
def get_mentions(
    bucket: str = "open",
    search: str = "",
    limit_start: int | str = 0,
    page_length: int | str = 25,
):
    return service.get_mentions(
        bucket=bucket,
        search=search,
        limit_start=limit_start,
        page_length=page_length,
    )


@frappe.whitelist()
def get_mention_detail(thread_name: str):
    return service.get_mention_detail(thread_name)


@frappe.whitelist()
def search_reply_mentions(thread_name: str, search_term: str = ""):
    return service.search_reply_mentions(thread_name, search_term)


@frappe.whitelist(methods=["POST"])
def mark_mention_seen(
    thread_name: str,
    seen: int | str,
    expected_last_event_key: str,
):
    return service.mark_mention_seen(thread_name, seen, expected_last_event_key)


@frappe.whitelist(methods=["POST"])
def reply_mention(
    thread_name: str,
    reply: str,
    request_id: str,
    expected_last_event_key: str,
    reply_html: str = "",
):
    return service.reply_mention(
        thread_name,
        reply,
        request_id,
        expected_last_event_key,
        reply_html=reply_html,
    )


@frappe.whitelist(methods=["POST"])
def reply_and_close(
    thread_name: str,
    reply: str,
    request_id: str,
    expected_last_event_key: str,
    reply_html: str = "",
):
    return service.reply_and_close(
        thread_name,
        reply,
        request_id,
        expected_last_event_key,
        reply_html=reply_html,
    )


@frappe.whitelist(methods=["POST"])
def close_mention(thread_name: str, expected_last_event_key: str):
    return service.close_mention(thread_name, expected_last_event_key)


@frappe.whitelist(methods=["POST"])
def reopen_mention(thread_name: str, expected_last_event_key: str):
    return service.reopen_mention(thread_name, expected_last_event_key)


@frappe.whitelist(methods=["POST"])
def convert_mention_to_followup(
    thread_name: str,
    due_date: str,
    priority: str = "Medium",
    description: str = "",
    *,
    expected_last_event_key: str,
):
    return service.convert_mention_to_followup(
        thread_name,
        due_date,
        priority,
        description,
        expected_last_event_key=expected_last_event_key,
    )
