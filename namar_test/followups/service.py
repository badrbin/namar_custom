from __future__ import annotations

from typing import Any, Callable, TypeVar
from uuid import uuid4

import frappe
from frappe.desk.form import assign_to
from frappe.model.workflow import get_transitions, get_workflow_name, get_workflow_state_field
from frappe.utils import get_absolute_url, nowdate

from namar_test.followups.logic import (
    APPROVAL_SEARCH_SCOPES,
    FOLLOWUP_SEARCH_SCOPES,
    MAX_DESCRIPTION_LENGTH,
    MAX_NOTE_LENGTH,
    MAX_REFERENCE_LENGTH,
    MAX_RESULT_LENGTH,
    assignment_args,
    classify_followup,
    clean_text,
    exact_close_args,
    normalize_bucket,
    normalize_date,
    normalize_priority,
    normalize_priority_filter,
    normalize_search,
    normalize_search_scope,
    page_window,
    pagination,
    plain_text,
    require_text,
    timeline_comment,
    timeline_target,
    todo_filters,
    validate_owned_todo,
)


TODO_FIELDS = (
    "name",
    "description",
    "status",
    "priority",
    "date",
    "allocated_to",
    "assigned_by",
    "assigned_by_full_name",
    "role",
    "reference_type",
    "reference_name",
    "creation",
    "modified",
)

WORKFLOW_ACTION_FIELDS = (
    "name",
    "status",
    "reference_doctype",
    "reference_name",
    "workflow_state",
    "user",
    "creation",
    "modified",
)

COMMENT_FIELDS = (
    "name",
    "comment_type",
    "content",
    "owner",
    "comment_email",
    "comment_by",
    "creation",
    "modified",
)

T = TypeVar("T")


def _assert_authenticated() -> str:
    user = clean_text(frappe.session.user, MAX_REFERENCE_LENGTH)
    if not user or user == "Guest":
        frappe.throw(
            "يلزم تسجيل الدخول لاستخدام صفحة متابعاتي",
            frappe.PermissionError,
        )
    return user


def _logic(call: Callable[..., T], *args, **kwargs) -> T:
    try:
        return call(*args, **kwargs)
    except PermissionError as exc:
        frappe.throw(str(exc), frappe.PermissionError)
    except ValueError as exc:
        frappe.throw(str(exc), frappe.ValidationError)
    raise AssertionError("frappe.throw should have raised")


def _required(value: Any, label: str, max_length: int) -> str:
    return _logic(require_text, value, label, max_length)


def _todo_values(todo) -> dict[str, Any]:
    return {fieldname: todo.get(fieldname) for fieldname in TODO_FIELDS}


def _readable_reference_title(
    reference_type: Any,
    reference_name: Any,
    cache: dict[tuple[str, str], str] | None = None,
) -> str | None:
    doctype = clean_text(reference_type, MAX_REFERENCE_LENGTH)
    name = clean_text(reference_name, MAX_REFERENCE_LENGTH)
    if not doctype or not name:
        return None

    key = (doctype, name)
    if cache is not None and key in cache:
        return cache[key]

    title = name
    try:
        doc = frappe.get_doc(doctype, name)
        doc.check_permission("read")
    except (frappe.DoesNotExistError, frappe.PermissionError):
        pass
    else:
        title_field = doc.meta.get_title_field()
        if title_field and title_field != "name":
            title = plain_text(doc.get(title_field), 500) or name

    if cache is not None:
        cache[key] = title
    return title


def _serialize_todo(
    todo,
    current_date: str | None = None,
    reference_title_cache: dict[tuple[str, str], str] | None = None,
) -> dict[str, Any]:
    values = _todo_values(todo)
    values["description"] = plain_text(values.get("description"), MAX_DESCRIPTION_LENGTH)
    values["description_format"] = "plain_text"
    values["allocated_to_full_name"] = (
        frappe.get_cached_value("User", values.get("allocated_to"), "full_name")
        if values.get("allocated_to")
        else None
    )
    current_date = current_date or nowdate()
    values["bucket"] = _logic(
        classify_followup,
        values.get("status"),
        values.get("date"),
        current_date,
    )
    if values.get("reference_type") and values.get("reference_name"):
        values["reference_route"] = get_absolute_url(
            values["reference_type"],
            values["reference_name"],
        )
        values["reference_title"] = _readable_reference_title(
            values["reference_type"],
            values["reference_name"],
            reference_title_cache,
        )
    else:
        values["reference_route"] = None
        values["reference_title"] = None
    return values


def _serialize_workflow_action(
    action,
    reference_title_cache: dict[tuple[str, str], str] | None = None,
) -> dict[str, Any]:
    values = {fieldname: action.get(fieldname) for fieldname in WORKFLOW_ACTION_FIELDS}
    if values.get("reference_doctype") and values.get("reference_name"):
        values["reference_route"] = get_absolute_url(
            values["reference_doctype"],
            values["reference_name"],
        )
        values["reference_title"] = _readable_reference_title(
            values["reference_doctype"],
            values["reference_name"],
            reference_title_cache,
        )
    else:
        values["reference_route"] = None
        values["reference_title"] = None
    return values


def _serialize_comment(comment) -> dict[str, Any]:
    values = {fieldname: comment.get(fieldname) for fieldname in COMMENT_FIELDS}
    values["content"] = plain_text(values.get("content"), MAX_NOTE_LENGTH)
    values["content_format"] = "plain_text"
    return values


def _reference_summary(doc) -> dict[str, Any]:
    title_field = doc.meta.get_title_field()
    title = doc.name if not title_field or title_field == "name" else doc.get(title_field) or doc.name
    workflow_name = get_workflow_name(doc.doctype)
    workflow_state_field = get_workflow_state_field(workflow_name) if workflow_name else None
    workflow_state = doc.get(workflow_state_field) if workflow_state_field else None
    return {
        "doctype": doc.doctype,
        "name": doc.name,
        "title": plain_text(title, 500),
        "route": get_absolute_url(doc.doctype, doc.name),
        "owner": doc.get("owner"),
        "modified": doc.get("modified"),
        "docstatus": doc.get("docstatus"),
        "status": doc.get("status") if doc.meta.has_field("status") else None,
        "workflow_state": workflow_state,
    }


def _get_owned_todo(
    todo_name: Any,
    *,
    require_open: bool,
    require_write: bool = False,
    for_update: bool = False,
):
    user = _assert_authenticated()
    name = _required(todo_name, "اسم المتابعة", MAX_REFERENCE_LENGTH)
    if for_update:
        frappe.db.get_value("ToDo", name, "name", for_update=True)

    todo = frappe.get_doc("ToDo", name)
    todo.check_permission("read")
    if require_write:
        todo.check_permission("write")
    _logic(validate_owned_todo, todo.as_dict(), user, require_open=require_open)
    return todo


def _get_reference_doc(todo, permission_type: str = "read"):
    target_doctype, target_name = _logic(timeline_target, todo.as_dict())
    doc = todo if target_doctype == "ToDo" and target_name == todo.name else frappe.get_doc(
        target_doctype,
        target_name,
    )
    doc.check_permission(permission_type)
    return doc


def _get_timeline(reference_doc, limit: int = 100) -> list[dict[str, Any]]:
    reference_doc.check_permission("read")
    comments = frappe.get_all(
        "Comment",
        fields=list(COMMENT_FIELDS),
        filters={
            "reference_doctype": reference_doc.doctype,
            "reference_name": reference_doc.name,
        },
        order_by="creation desc",
        limit_page_length=limit,
    )
    return [_serialize_comment(comment) for comment in comments]


def _add_timeline_comment(reference_doc, label: str, value: Any, max_length: int):
    reference_doc.check_permission("read")
    content = _logic(timeline_comment, label, value, max_length)
    return reference_doc.add_comment("Comment", content)


def _close_exact_todo(todo) -> None:
    if todo.reference_type and todo.reference_name:
        args = _logic(exact_close_args, todo.as_dict())
        assign_to.set_status(**args)
        return

    # ToDo مستقل بلا مرجع؛ لا يستطيع assign_to.set_status التعامل معه.
    todo.status = "Closed"
    todo.save()


def _followup_search_filters(search: str, search_scope: str = "all") -> list[list[str]]:
    if not search:
        return []
    pattern = f"%{search}%"
    fields = {
        "document": ("reference_name",),
        "doctype": ("reference_type",),
        "employee": ("assigned_by", "assigned_by_full_name"),
        "content": ("description",),
        "all": (
            "description",
            "reference_type",
            "reference_name",
            "assigned_by",
            "assigned_by_full_name",
        ),
    }
    return [["ToDo", fieldname, "like", pattern] for fieldname in fields[search_scope]]


def _approval_search_filters(search: str, search_scope: str = "all") -> list[list[str]]:
    if not search:
        return []
    pattern = f"%{search}%"
    fields = {
        "document": ("reference_name",),
        "doctype": ("reference_doctype",),
        "state": ("workflow_state",),
        "all": ("reference_doctype", "reference_name", "workflow_state"),
    }
    return [
        ["Workflow Action", fieldname, "like", pattern]
        for fieldname in fields[search_scope]
    ]


def _approval_counts() -> dict[str, int]:
    # get_list تُبقي Permission Query القياسي لـ Workflow Action مطبقًا حتى
    # مع حقل تجميعي؛ لذلك يطابق العدد نفس نطاق العناصر المرئية للمستخدم.
    rows = frappe.get_list(
        "Workflow Action",
        fields=["count(name) as count"],
        filters={"status": "Open"},
        limit_page_length=1,
    )
    open_count = rows[0].get("count") if rows else 0
    return {"open": int(open_count or 0)}


def _followup_open_count(user: str) -> int:
    # get_list مقصودة حتى يبقى Permission Query مطبقًا، إضافة إلى قيد المكلّف.
    rows = frappe.get_list(
        "ToDo",
        fields=["count(name) as count"],
        filters={"allocated_to": user, "status": "Open"},
        limit_page_length=1,
    )
    open_count = rows[0].get("count") if rows else 0
    return int(open_count or 0)


def _followup_overdue_count(user: str, current_date: str) -> int:
    # الشارة العلوية تعرض المتأخر فقط، مع نفس Permission Query المستخدم
    # لعداد المفتوح، حتى لا يتسع نطاق العدد عن العناصر المرئية للمستخدم.
    rows = frappe.get_list(
        "ToDo",
        fields=["count(name) as count"],
        filters=_logic(todo_filters, "overdue", user, current_date),
        limit_page_length=1,
    )
    overdue_count = rows[0].get("count") if rows else 0
    return int(overdue_count or 0)


def get_my_followups_counts() -> dict[str, dict[str, int]]:
    user = _assert_authenticated()

    # Import محلي لتجنب الدوران؛ mentions.service يعتمد أصلًا على هذه الخدمة.
    from namar_test.mentions import service as mention_service

    counts = {
        "mentions": int(mention_service.get_open_mention_count()),
        "followups": _followup_open_count(user),
        "approvals": int(_approval_counts()["open"]),
    }
    counts["total"] = sum(counts.values())

    # نحافظ على counts بوصفها كل المفتوح حتى لا يتغير عقد عدادات الصفحة.
    # attention_counts هي العقد المخصص للشارات الملونة في الشريط العلوي.
    attention_counts = {
        "mentions": counts["mentions"],
        "followups": _followup_overdue_count(user, nowdate()),
        "approvals": counts["approvals"],
    }
    attention_counts["total"] = sum(attention_counts.values())
    return {"counts": counts, "attention_counts": attention_counts}


def _followup_counts(user: str, current_date: str, priority: str = "") -> dict[str, int]:
    counts = {
        bucket: frappe.db.count(
            "ToDo",
            filters=_logic(todo_filters, bucket, user, current_date, priority),
        )
        for bucket in ("all", "overdue", "today", "upcoming", "recent")
    }
    counts["open"] = counts["all"]
    return counts


def get_followups(
    bucket: str = "all",
    search: str = "",
    limit_start: int | str = 0,
    page_length: int | str = 50,
    priority: str = "",
    search_scope: str = "all",
) -> dict[str, Any]:
    user = _assert_authenticated()
    normalized_bucket = _logic(normalize_bucket, bucket)
    normalized_search = normalize_search(search)
    normalized_search_scope = _logic(
        normalize_search_scope,
        search_scope,
        FOLLOWUP_SEARCH_SCOPES,
    )
    normalized_priority = _logic(normalize_priority_filter, priority)
    start, length, query_length = page_window(limit_start, page_length)
    current_date = nowdate()
    filters = _logic(todo_filters, normalized_bucket, user, current_date, normalized_priority)
    order_by = "modified desc" if normalized_bucket == "recent" else "date asc, modified desc"

    rows = frappe.get_list(
        "ToDo",
        fields=list(TODO_FIELDS),
        filters=filters,
        or_filters=_followup_search_filters(normalized_search, normalized_search_scope),
        order_by=order_by,
        limit_start=start,
        limit_page_length=query_length,
    )
    reference_title_cache: dict[tuple[str, str], str] = {}
    serialized = [
        _serialize_todo(row, current_date, reference_title_cache)
        for row in rows
    ]
    result = pagination(serialized, start, length)
    result.update(
        {
            "bucket": normalized_bucket,
            "search": normalized_search,
            "search_scope": normalized_search_scope,
            "priority": normalized_priority,
            "today": current_date,
            "counts": _followup_counts(user, current_date, normalized_priority),
        }
    )
    return result


def get_followup_detail(todo_name: str) -> dict[str, Any]:
    todo = _get_owned_todo(todo_name, require_open=False)
    reference_doc = _get_reference_doc(todo)
    return {
        "followup": _serialize_todo(todo),
        "reference": _reference_summary(reference_doc),
        "timeline": _get_timeline(reference_doc),
        "permissions": {
            "can_complete": todo.status == "Open" and todo.has_permission("write"),
            "can_reschedule": todo.status == "Open" and todo.has_permission("write"),
            "can_add_note": reference_doc.has_permission("read"),
        },
    }


def complete_followup(todo_name: str, result: str) -> dict[str, Any]:
    result_text = _required(result, "نتيجة المتابعة", MAX_RESULT_LENGTH)
    todo = _get_owned_todo(
        todo_name,
        require_open=True,
        require_write=True,
        for_update=True,
    )
    reference_doc = _get_reference_doc(todo)
    comment = _add_timeline_comment(
        reference_doc,
        "نتيجة المتابعة",
        result_text,
        MAX_RESULT_LENGTH,
    )
    _close_exact_todo(todo)
    completed = frappe.get_doc("ToDo", todo.name)
    return {
        "followup": _serialize_todo(completed),
        "result_comment": _serialize_comment(comment),
    }


def complete_and_schedule_next(
    todo_name: str,
    result: str,
    next_date: str,
    description: str | None = None,
    priority: str | None = None,
) -> dict[str, Any]:
    # Import محلي لتجنب دوران mentions.service -> followups.service.
    from namar_test.mentions import events as mention_events

    result_text = _required(result, "نتيجة المتابعة", MAX_RESULT_LENGTH)
    normalized_next_date = _logic(normalize_date, next_date, "تاريخ المتابعة القادمة")
    todo = _get_owned_todo(
        todo_name,
        require_open=True,
        require_write=True,
        for_update=True,
    )
    if not todo.reference_type or not todo.reference_name:
        frappe.throw(
            "لا يمكن جدولة متابعة تالية لمتابعة بلا مستند مرجعي",
            frappe.ValidationError,
        )

    reference_doc = _get_reference_doc(todo)
    next_description = (
        _required(description, "وصف المتابعة القادمة", MAX_DESCRIPTION_LENGTH)
        if description is not None
        else _required(todo.description, "وصف المتابعة القادمة", MAX_DESCRIPTION_LENGTH)
    )
    next_priority = _logic(normalize_priority, priority, todo.priority or "Medium")

    duplicate_name = frappe.db.get_value(
        "ToDo",
        {
            "reference_type": todo.reference_type,
            "reference_name": todo.reference_name,
            "allocated_to": todo.allocated_to,
            "status": "Open",
            "name": ["!=", todo.name],
        },
        "name",
        for_update=True,
    )
    if duplicate_name:
        frappe.throw(
            f"توجد متابعة مفتوحة أخرى على المستند نفسه: {duplicate_name}",
            frappe.ValidationError,
        )

    savepoint = f"followups_next_{uuid4().hex[:12]}"
    frappe.db.savepoint(savepoint)
    try:
        result_comment = _add_timeline_comment(
            reference_doc,
            "نتيجة المتابعة",
            result_text,
            MAX_RESULT_LENGTH,
        )
        with mention_events.suppress_todo_mention_sync(todo.name):
            _close_exact_todo(todo)

        args = _logic(
            assignment_args,
            reference_type=todo.reference_type,
            reference_name=todo.reference_name,
            description=next_description,
            due_date=normalized_next_date,
            priority=next_priority,
            allocated_to=todo.allocated_to,
            assigned_by=frappe.session.user,
        )
        assign_to.add(args)

        next_name = frappe.db.get_value(
            "ToDo",
            {
                "reference_type": todo.reference_type,
                "reference_name": todo.reference_name,
                "allocated_to": todo.allocated_to,
                "status": "Open",
                "name": ["!=", todo.name],
            },
            "name",
            order_by="creation desc",
            for_update=True,
        )
        if not next_name:
            frappe.throw(
                "لم يتم إنشاء المتابعة القادمة؛ لم تُغلق المتابعة الحالية",
                frappe.ValidationError,
            )
        next_todo = frappe.get_doc("ToDo", next_name)
        mention_events.transfer_linked_mentions_to_next_todo(todo, next_todo)
    except Exception:
        frappe.db.rollback(save_point=savepoint)
        raise
    else:
        frappe.db.release_savepoint(savepoint)

    completed = frappe.get_doc("ToDo", todo.name)
    return {
        "completed_followup": _serialize_todo(completed),
        "next_followup": _serialize_todo(next_todo),
        "result_comment": _serialize_comment(result_comment),
    }


def reschedule_followup(todo_name: str, new_date: str) -> dict[str, Any]:
    normalized_date = _logic(normalize_date, new_date, "تاريخ الاستحقاق الجديد")
    todo = _get_owned_todo(
        todo_name,
        require_open=True,
        require_write=True,
        for_update=True,
    )
    todo.date = normalized_date
    todo.save()
    return {"followup": _serialize_todo(todo)}


def add_followup_note(todo_name: str, note: str) -> dict[str, Any]:
    note_text = _required(note, "الملاحظة", MAX_NOTE_LENGTH)
    todo = _get_owned_todo(todo_name, require_open=False)
    reference_doc = _get_reference_doc(todo)
    comment = _add_timeline_comment(
        reference_doc,
        "ملاحظة متابعة",
        note_text,
        MAX_NOTE_LENGTH,
    )
    return {"comment": _serialize_comment(comment)}


def _assert_enabled_user(user: str) -> None:
    enabled = frappe.db.get_value("User", user, "enabled")
    if enabled is None:
        frappe.throw("المستخدم المكلف غير موجود", frappe.ValidationError)
    if not int(enabled):
        frappe.throw(
            "لا يمكن إسناد متابعة إلى مستخدم معطل",
            frappe.ValidationError,
        )


def create_followup(
    reference_type: str,
    reference_name: str,
    description: str,
    due_date: str,
    priority: str = "Medium",
    allocated_to: str | None = None,
) -> dict[str, Any]:
    user = _assert_authenticated()
    args = _logic(
        assignment_args,
        reference_type=reference_type,
        reference_name=reference_name,
        description=description,
        due_date=due_date,
        priority=priority,
        allocated_to=allocated_to or user,
        assigned_by=user,
    )
    assignee = _required(allocated_to or user, "المستخدم المكلف", MAX_REFERENCE_LENGTH)
    _assert_enabled_user(assignee)

    reference_doc = frappe.get_doc(args["doctype"], args["name"])
    reference_doc.check_permission("read")
    existing_name = frappe.db.get_value(
        "ToDo",
        {
            "reference_type": args["doctype"],
            "reference_name": args["name"],
            "allocated_to": assignee,
            "status": "Open",
        },
        "name",
        for_update=True,
    )
    if existing_name:
        frappe.throw(
            "توجد متابعة مفتوحة لهذا المستخدم على المستند نفسه",
            frappe.ValidationError,
        )

    assign_to.add(args)
    todo_name = frappe.db.get_value(
        "ToDo",
        {
            "reference_type": args["doctype"],
            "reference_name": args["name"],
            "allocated_to": assignee,
            "status": "Open",
        },
        "name",
        order_by="creation desc",
    )
    if not todo_name:
        frappe.throw("لم يتم إنشاء المتابعة", frappe.ValidationError)
    return {"followup": _serialize_todo(frappe.get_doc("ToDo", todo_name))}


def get_approvals(
    search: str = "",
    limit_start: int | str = 0,
    page_length: int | str = 50,
    search_scope: str = "all",
) -> dict[str, Any]:
    _assert_authenticated()
    normalized_search = normalize_search(search)
    normalized_search_scope = _logic(
        normalize_search_scope,
        search_scope,
        APPROVAL_SEARCH_SCOPES,
    )
    start, length, query_length = page_window(limit_start, page_length)

    # get_list مقصودة هنا: فهي تطبق Permission Query القياسي لـ Workflow Action.
    rows = frappe.get_list(
        "Workflow Action",
        fields=list(WORKFLOW_ACTION_FIELDS),
        filters={"status": "Open"},
        or_filters=_approval_search_filters(normalized_search, normalized_search_scope),
        order_by="modified desc",
        limit_start=start,
        limit_page_length=query_length,
    )
    reference_title_cache: dict[tuple[str, str], str] = {}
    result = pagination(
        [_serialize_workflow_action(row, reference_title_cache) for row in rows],
        start,
        length,
    )
    result["search"] = normalized_search
    result["search_scope"] = normalized_search_scope
    result["counts"] = _approval_counts()
    return result


def get_approval_detail(action_name: str) -> dict[str, Any]:
    _assert_authenticated()
    name = _required(action_name, "اسم الموافقة", MAX_REFERENCE_LENGTH)

    # نتحقق بالقائمة ذات Permission Query بدل get_all أو قراءة DB مباشرة.
    permitted = frappe.get_list(
        "Workflow Action",
        fields=list(WORKFLOW_ACTION_FIELDS),
        filters={"name": name, "status": "Open"},
        limit_page_length=1,
    )
    if not permitted:
        frappe.throw(
            "الموافقة غير متاحة لك أو لم تعد مفتوحة",
            frappe.PermissionError,
        )

    action = frappe.get_doc("Workflow Action", name)
    if not action.reference_doctype or not action.reference_name:
        frappe.throw("الموافقة لا تحتوي مستندًا مرجعيًا", frappe.ValidationError)

    reference_doc = frappe.get_doc(action.reference_doctype, action.reference_name)
    reference_doc.check_permission("read")
    transitions = get_transitions(reference_doc)
    available_actions = [
        {
            "action": row.get("action"),
            "next_state": row.get("next_state"),
            "allowed": row.get("allowed"),
        }
        for row in transitions
        if row.get("action")
    ]
    return {
        "approval": _serialize_workflow_action(action),
        "reference": _reference_summary(reference_doc),
        "available_actions": available_actions,
        "permitted_roles": [row.role for row in action.get("permitted_roles") or []],
        "timeline": _get_timeline(reference_doc),
    }
