sales_order = frappe.form_dict.get('sales_order')

if not sales_order:
    frappe.response['message'] = []
else:
    # جلب الأصناف مع تجاوز الصلاحيات
    items = frappe.get_all("Sales Order Item",
        filters={"parent": sales_order},
        pluck="item_code", # يجلب قائمة بالأكواد فقط ['Item-A', 'Item-B']
        ignore_permissions=True # <--- هنا يتم تجاوز الصلاحيات
    )

    frappe.response['message'] = items
