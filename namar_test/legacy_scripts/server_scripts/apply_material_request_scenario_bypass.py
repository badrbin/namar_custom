rule_doctype = "Material Request Scenario Bypass Rule"
material_request = (frappe.form_dict.get("material_request") or "").strip()
rule_name = (frappe.form_dict.get("rule_name") or "").strip()

if not material_request:
    frappe.throw("حدد طلب المواد.")

if not rule_name:
    frappe.throw("حدد قاعدة التجاوز.")

if not frappe.db.exists("Material Request", material_request):
    frappe.throw("طلب المواد غير موجود.")

if not frappe.db.exists(rule_doctype, rule_name):
    frappe.throw("قاعدة التجاوز غير موجودة.")

mr_doc = frappe.get_doc("Material Request", material_request)
if not frappe.has_permission("Material Request", "write", doc=mr_doc):
    frappe.throw("ليس لديك صلاحية تعديل طلب المواد.")

rule = frappe.get_doc(rule_doctype, rule_name)
if not int(rule.get("is_active") or 0):
    frappe.throw("قاعدة التجاوز غير مفعلة.")

scenario = (mr_doc.get("custom_request_scenario") or "تصنيع").strip()
current_state = (mr_doc.get("workflow_state") or "").strip()
rule_scenario = (rule.get("request_scenario") or "").strip()
rule_state = (rule.get("current_state") or "").strip()
target_state = (rule.get("target_state") or "").strip()

if rule_scenario != scenario:
    frappe.throw("قاعدة التجاوز لا تطابق نوع طلب المواد الحالي.")

if rule_state and rule_state != current_state:
    frappe.throw("قاعدة التجاوز لا تطابق حالة الطلب الحالية.")

if not target_state:
    frappe.throw("الحالة البديلة غير محددة في قاعدة التجاوز.")

if not frappe.db.exists("Workflow State", target_state):
    frappe.throw("الحالة البديلة غير موجودة في حالات النظام.")

target_docstatus = None
if frappe.db.exists("Workflow", "طلب مواد"):
    workflow_doc = frappe.get_doc("Workflow", "طلب مواد")
    for state_row in workflow_doc.get("states") or []:
        if (state_row.get("state") or "").strip() == target_state:
            target_docstatus = int(state_row.get("doc_status") or 0)
            break

if target_docstatus is not None and int(mr_doc.docstatus or 0) != target_docstatus:
    frappe.throw("لا يمكن التجاوز إلى حالة لها حالة مستند مختلفة.")

old_state = current_state
mr_doc.db_set("workflow_state", target_state, update_modified=True)

comment_text = "تم تجاوز إجراء {0} حسب سيناريو {1}. الحالة السابقة: {2}. الحالة الجديدة: {3}.".format(
    rule.get("skipped_action") or rule.name,
    scenario,
    old_state or "-",
    target_state,
)

frappe.get_doc(
    {
        "doctype": "Comment",
        "comment_type": "Info",
        "reference_doctype": "Material Request",
        "reference_name": mr_doc.name,
        "content": comment_text,
    }
).insert(ignore_permissions=True)

frappe.response["message"] = {
    "material_request": mr_doc.name,
    "old_state": old_state,
    "new_state": target_state,
    "rule": rule.name,
}
