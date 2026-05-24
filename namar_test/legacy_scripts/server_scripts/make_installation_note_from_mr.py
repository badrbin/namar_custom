# Server Script (API)
# API Method: make_installation_note_from_mr

source_name = frappe.form_dict.get('source_name')
material_request = frappe.form_dict.get('material_request')
action = frappe.form_dict.get('action')

if action == 'get_qty':
    current_doc = frappe.form_dict.get('current_doc')
    if not material_request:
        frappe.throw('Material Request is required')

    # جلب أصناف طلب المواد بـ query واحد
    mr_items = frappe.db.sql("""
        SELECT item_code, SUM(qty) as total_qty
        FROM `tabMaterial Request Item`
        WHERE parent = %s
        GROUP BY item_code
    """, (material_request,), as_dict=True)

    qty_map = {}
    for item in mr_items:
        qty_map[item.item_code] = frappe.utils.flt(item.total_qty)

    # ✅ جلب كل الكميات المركّبة سابقاً بـ query واحد بدل N+1
    installed_data = frappe.db.sql("""
        SELECT child.item_code, SUM(child.qty) as total_installed
        FROM `tabInstallation Note Item` child
        INNER JOIN `tabInstallation Note` parent ON child.parent = parent.name
        WHERE parent.custom_material_request = %s
        AND parent.docstatus = 1
        AND parent.name != %s
        GROUP BY child.item_code
    """, (material_request, current_doc or ''), as_dict=True)

    for row in installed_data:
        if row.item_code in qty_map:
            qty_map[row.item_code] = qty_map[row.item_code] - frappe.utils.flt(row.total_installed)

    for key in qty_map:
        if qty_map[key] < 0:
            qty_map[key] = 0

    frappe.response['message'] = qty_map

else:
    if not source_name:
        frappe.throw('Material Request name is required')

    # جلب بيانات الهيدر فقط بدل تحميل المستند كامل
    mr_data = frappe.db.get_value('Material Request', source_name,
        ['name', 'docstatus', 'sales_order'], as_dict=True)

    if not mr_data:
        frappe.throw('Material Request not found')
    if mr_data.docstatus != 1:
        frappe.throw('Material Request must be submitted')

    # جلب أصناف طلب المواد
    mr_items = frappe.db.sql("""
        SELECT name, item_code, item_name, description, qty
        FROM `tabMaterial Request Item`
        WHERE parent = %s
    """, (source_name,), as_dict=True)

    qty_map = {}
    for item in mr_items:
        prev = qty_map.get(item.item_code, 0)
        qty_map[item.item_code] = prev + frappe.utils.flt(item.qty)

    # ✅ جلب كل الكميات المركّبة سابقاً بـ query واحد بدل N+1
    installed_data = frappe.db.sql("""
        SELECT child.item_code, SUM(child.qty) as total_installed
        FROM `tabInstallation Note Item` child
        INNER JOIN `tabInstallation Note` parent ON child.parent = parent.name
        WHERE parent.custom_material_request = %s
        AND parent.docstatus = 1
        GROUP BY child.item_code
    """, (source_name,), as_dict=True)

    for row in installed_data:
        if row.item_code in qty_map:
            qty_map[row.item_code] = qty_map[row.item_code] - frappe.utils.flt(row.total_installed)

    # إنشاء Installation Note جديد
    inst = frappe.new_doc('Installation Note')
    inst.custom_material_request = source_name
    if mr_data.sales_order:
        inst.custom_sales_order = mr_data.sales_order

    for item in mr_items:
        remaining = qty_map.get(item.item_code, 0)
        if remaining > 0:
            inst.append('items', {
                'item_code': item.item_code,
                'item_name': item.item_name,
                'description': item.description,
                'qty': remaining,
                'custom_mr_detail': item.name
            })
            qty_map[item.item_code] = 0

    if not inst.items:
        frappe.throw('جميع الأصناف تم تركيبها بالكامل')

    frappe.response['message'] = inst
