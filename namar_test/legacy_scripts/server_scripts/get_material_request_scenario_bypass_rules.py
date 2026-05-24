rule_doctype = "Material Request Scenario Bypass Rule"
material_request = (frappe.form_dict.get("material_request") or "").strip()

if not material_request:
    frappe.throw("حدد طلب المواد.")

if not frappe.db.exists("Material Request", material_request):
    frappe.throw("طلب المواد غير موجود.")

if not frappe.db.exists("DocType", rule_doctype):
    frappe.response["message"] = []
else:
    mr_doc = frappe.get_doc("Material Request", material_request)
    if not frappe.has_permission("Material Request", "read", doc=mr_doc):
        frappe.throw("ليس لديك صلاحية قراءة طلب المواد.")

    scenario = (mr_doc.get("custom_request_scenario") or "تصنيع").strip()
    current_state = (mr_doc.get("workflow_state") or "").strip()

    rows = frappe.get_all(
        rule_doctype,
        filters={"is_active": 1, "request_scenario": scenario},
        fields=[
            "name",
            "rule_title",
            "request_scenario",
            "current_state",
            "skipped_action",
            "target_state",
            "button_label",
            "sort_order",
        ],
        order_by="sort_order asc, modified asc",
        limit_page_length=100,
    )

    rules = []
    for row in rows:
        rule_state = (row.get("current_state") or "").strip()
        target_state = (row.get("target_state") or "").strip()
        if rule_state and rule_state != current_state:
            continue
        if not target_state:
            continue
        rules.append(
            {
                "name": row.get("name"),
                "rule_title": row.get("rule_title"),
                "request_scenario": row.get("request_scenario"),
                "current_state": rule_state,
                "skipped_action": row.get("skipped_action"),
                "target_state": target_state,
                "button_label": row.get("button_label") or row.get("skipped_action") or "تجاوز المسار",
            }
        )

    frappe.response["message"] = rules
