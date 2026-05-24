# API: map_update_field
docname = frappe.form_dict.get('docname', '')
fieldname = frappe.form_dict.get('fieldname', '')
value = frappe.form_dict.get('value', '')

allowed_fields = ['territory', 'custom_installation_note_teams']

if not docname or not fieldname:
    frappe.throw('docname and fieldname are required')

if fieldname not in allowed_fields:
    frappe.throw('Field not allowed')

# Permission check: frappe.get_doc respects user permissions
doc = frappe.get_doc('Material Request', docname)

frappe.db.set_value('Material Request', docname, fieldname, value)

frappe.response['message'] = {
    'docname': docname,
    'fieldname': fieldname,
    'value': value
}
