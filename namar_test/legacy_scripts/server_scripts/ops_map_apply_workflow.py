docname = (frappe.form_dict.get("docname") or "").strip()
action = (frappe.form_dict.get("action") or "").strip()

if not docname:
    frappe.throw("اسم الطلب مطلوب")
if not action:
    frappe.throw("الإجراء مطلوب")

updated_doc = frappe.call(
    "frappe.model.workflow.apply_workflow",
    doc={"doctype": "Material Request", "name": docname},
    action=action,
)

frappe.response["message"] = {
    "name": updated_doc.get("name") if isinstance(updated_doc, dict) else updated_doc.name,
    "workflow_state": updated_doc.get("workflow_state") if isinstance(updated_doc, dict) else updated_doc.workflow_state,
}
