from __future__ import annotations

import frappe

from namar_test.mentions.events import (
    EVENT_DOCTYPE,
    THREAD_DOCTYPE,
    _todo_matches_thread,
    reopen_stale_converted_mention,
    sync_linked_mentions_on_todo_change,
)


def execute() -> None:
    """Reconcile converted threads that predate the ToDo lifecycle hook."""

    if not (
        frappe.db.table_exists(THREAD_DOCTYPE)
        and frappe.db.table_exists(EVENT_DOCTYPE)
    ):
        return

    thread_names = frappe.db.get_values(
        THREAD_DOCTYPE,
        {"status": "Converted"},
        "name",
        pluck=True,
    )
    for thread_name in sorted(set(thread_names or [])):
        thread = frappe.get_doc(THREAD_DOCTYPE, thread_name)
        todo_name = thread.converted_to_todo
        if not todo_name or not frappe.db.exists("ToDo", todo_name):
            reopen_stale_converted_mention(
                thread_name,
                (
                    f"المتابعة المرتبطة {todo_name} غير موجودة؛ عادت الرسالة إلى تحتاج قرارًا"
                    if todo_name
                    else "لا يوجد رابط متابعة صالح؛ عادت الرسالة إلى تحتاج قرارًا"
                ),
            )
            continue
        todo = frappe.get_doc("ToDo", todo_name)
        if not _todo_matches_thread(todo, thread):
            reopen_stale_converted_mention(
                thread_name,
                f"المتابعة المرتبطة {todo_name} لا تطابق الرسالة؛ عادت إلى تحتاج قرارًا",
            )
            continue
        sync_linked_mentions_on_todo_change(todo)
