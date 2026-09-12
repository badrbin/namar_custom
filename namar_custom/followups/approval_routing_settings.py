"""Editable, per-workflow-state recipients for the My Followups approval inbox."""

from __future__ import annotations

from html import escape
import json

import frappe


STATE_DOCTYPE = "Workflow Document State"
ROUTING_FIELD = "custom_followups_routing_targets"
ROLE_TARGET = "role"
USER_TARGET = "user"
OWNER_TARGET = "owner"
FIELD_TARGET = "field"
MAX_TARGETS = 50
TARGET_VALUE_FIELDS = {USER_TARGET: "user", ROLE_TARGET: "role", FIELD_TARGET: "field"}
TARGET_TYPES = frozenset((*TARGET_VALUE_FIELDS, OWNER_TARGET))

ROUTING_DESCRIPTION = (
    '<div dir="rtl" style="text-align:right">'
    "حدد من تظهر له موافقة هذه المرحلة في متابعاتي. يمكنك اختيار أكثر من موظف أو دور، "
    "ويكفي تطابق أحد الاختيارات. تبقى صلاحيات الاعتماد في المستند كما هي. "
    "عند عدم تحديد مستلمين، أو تعذر تحديد جميع المستلمين المؤهلين، "
    "تظهر الموافقة للمؤهلين حسب أدوار المرحلة."
    "</div>"
)


def parse_routing_targets(value) -> tuple[dict, ...]:
    """Parse and de-duplicate the versioned configuration; blanks use workflow roles.

    Invalid configuration raises ValueError. Runtime readers may fall back to the
    workflow roles, while the Workflow validate hook rejects invalid new settings.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return ()
    try:
        data = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError) as exc:
        raise ValueError("تعذر قراءة إعداد مستلمي الموافقة. أعد تحديد المستلمين.") from exc
    if not isinstance(data, dict) or set(data) != {"version", "targets"}:
        raise ValueError("صيغة إعداد مستلمي الموافقة غير صحيحة.")
    if type(data["version"]) is not int or data["version"] != 1:
        raise ValueError("إصدار إعداد مستلمي الموافقة غير مدعوم.")
    targets = data["targets"]
    if not isinstance(targets, list) or len(targets) > MAX_TARGETS:
        raise ValueError(f"يمكن تحديد {MAX_TARGETS} مستلمًا أو قاعدة كحد أقصى لكل مرحلة.")

    result = []
    seen = set()
    for target in targets:
        if not isinstance(target, dict):
            raise ValueError("أحد اختيارات مستلمي الموافقة غير صحيح.")
        target_type = target.get("type")
        if not isinstance(target_type, str) or target_type not in TARGET_TYPES:
            raise ValueError("نوع مستلم الموافقة غير معروف.")
        value_field = TARGET_VALUE_FIELDS.get(target_type)
        allowed_keys = {"type", value_field} if value_field else {"type"}
        if set(target) != allowed_keys:
            raise ValueError("حدد بيانات المستلم المطلوبة لنوع الاختيار فقط.")
        normalized = {"type": target_type}
        if value_field:
            raw_value = target[value_field]
            if not isinstance(raw_value, str) or not raw_value.strip():
                raise ValueError("أكمل اختيار الموظف أو الدور أو حقل المسؤول.")
            normalized[value_field] = raw_value.strip()
        key = (target_type, normalized.get(value_field, ""))
        if key not in seen:
            seen.add(key)
            result.append(normalized)
    return tuple(result)


def get_user_link_fields(document_type: str) -> tuple[str, ...]:
    """Return only direct fields explicitly linked to User, never nested fields."""
    if not document_type:
        return ()
    return tuple(
        field.fieldname
        for field in frappe.get_meta(document_type).fields
        if field.fieldtype == "Link" and field.options == "User" and field.fieldname
    )


def validate_workflow_approval_routing(doc, method=None) -> None:
    """Validate configuration without modifying workflow roles or transitions."""
    user_fields = None
    verified_links = set()
    for row in doc.get("states") or ():
        value = row.get(ROUTING_FIELD)
        try:
            targets = parse_routing_targets(value)
            for target in targets:
                target_type = target["type"]
                if target_type == FIELD_TARGET:
                    if user_fields is None:
                        user_fields = set(get_user_link_fields(doc.get("document_type")))
                    if target["field"] not in user_fields:
                        raise ValueError("اختر حقلًا مباشرًا مرتبطًا بمستخدم في نوع المستند المحدد.")
                elif target_type in (USER_TARGET, ROLE_TARGET):
                    doctype = "User" if target_type == USER_TARGET else "Role"
                    name = target[TARGET_VALUE_FIELDS[target_type]]
                    key = (doctype, name)
                    if key not in verified_links:
                        if not frappe.db.exists(doctype, {"name": name}):
                            raise ValueError("الموظف أو الدور المحدد غير موجود. أعد اختيار المستلم.")
                        verified_links.add(key)
        except ValueError as exc:
            state_label = row.get("state") or str(row.get("idx") or "")
            frappe.throw(
                '<div dir="rtl" style="text-align:right">'
                f"مستلمو الموافقة في المرحلة «{escape(str(state_label))}»: {escape(str(exc))}"
                "</div>",
                title="راجع مستلمي الموافقة",
            )
        if value:
            row.update({ROUTING_FIELD: json.dumps(
                {"version": 1, "targets": list(targets)}, ensure_ascii=False, separators=(",", ":")
            )})


def get_custom_field_definitions() -> list[dict]:
    """Only the state-owned setting is persisted; controls stay in the state form."""
    return [
        {
            "fieldname": "custom_followups_routing_section",
            "label": "مستلمو الموافقة في متابعاتي",
            "fieldtype": "Section Break",
            "insert_after": "workflow_builder_id",
            "description": ROUTING_DESCRIPTION,
        },
        {
            "fieldname": ROUTING_FIELD,
            "label": "إعداد مستلمي الموافقة",
            "fieldtype": "Long Text",
            "insert_after": "custom_followups_routing_section",
            "hidden": 1,
            "no_copy": 0,
        },
        {
            "fieldname": "custom_followups_routing_summary",
            "label": "المستلمون الحاليون",
            "fieldtype": "HTML",
            "insert_after": ROUTING_FIELD,
            "options": '<div dir="rtl" style="text-align:right">حسب أدوار المرحلة</div>',
        },
        {
            "fieldname": "custom_followups_routing_edit",
            "label": "تحديد مستلمي الموافقة",
            "fieldtype": "Button",
            "insert_after": "custom_followups_routing_summary",
        },
    ]


def configure_approval_routing_fields() -> None:
    """Create missing fields once; never overwrite a field with another purpose."""
    definitions = get_custom_field_definitions()
    existing = {
        field["fieldname"]: field
        for field in frappe.get_all(
            "Custom Field",
            filters={"dt": STATE_DOCTYPE, "fieldname": ["in", [df["fieldname"] for df in definitions]]},
            fields=["name", "fieldname", "fieldtype", "options", "hidden"],
        )
    }
    meta = frappe.get_meta(STATE_DOCTYPE)
    for definition in definitions:
        fieldname = definition["fieldname"]
        current = existing.get(fieldname)
        standard_collision = meta.get_field(fieldname) and current is None
        incompatible = current is not None and (
            current.get("fieldtype") != definition["fieldtype"]
            or (fieldname == ROUTING_FIELD and (
                not current.get("hidden") or current.get("options")
            ))
        )
        if standard_collision or incompatible:
            frappe.throw(
                '<div dir="rtl" style="text-align:right">'
                f"يتعارض الحقل {escape(fieldname)} مع إعداد مستلمي الموافقة؛ لم تُستبدل إعداداته."
                "</div>",
                title="تعارض تخصيص قائم",
            )

    missing = [definition for definition in definitions if definition["fieldname"] not in existing]
    if missing:
        from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

        create_custom_fields({STATE_DOCTYPE: missing}, update=False)
