mr = doc.custom_material_request

if mr:
    mr_items = frappe.db.sql("""
        SELECT item_code, SUM(qty) AS total_qty
        FROM `tabMaterial Request Item`
        WHERE parent = %s
        GROUP BY item_code
    """, (mr,), as_dict=True)

    mr_qty_map = {}
    for item in mr_items:
        mr_qty_map[item.item_code] = frappe.utils.flt(item.total_qty)

    delivered_data = frappe.db.sql("""
        SELECT child.item_code, SUM(child.qty) AS total_delivered
        FROM `tabDelivery Note Item` child
        INNER JOIN `tabDelivery Note` parent ON child.parent = parent.name
        WHERE parent.custom_material_request = %s
        AND parent.docstatus = 1
        AND parent.name != %s
        GROUP BY child.item_code
    """, (mr, doc.name or ""), as_dict=True)

    delivered_map = {}
    for row in delivered_data:
        delivered_map[row.item_code] = frappe.utils.flt(row.total_delivered)

    current_qty = {}
    for item in doc.items:
        if not item.item_code:
            continue
        current_qty[item.item_code] = current_qty.get(item.item_code, 0) + frappe.utils.flt(item.qty)

    errors = []
    for item_code, qty in current_qty.items():
        if item_code not in mr_qty_map:
            errors.append(
                "الصنف <b>" + str(item_code) + "</b> غير موجود في طلب المواد <b>" + str(mr) + "</b>"
            )
            continue

        max_qty = mr_qty_map.get(item_code, 0)
        already_delivered = delivered_map.get(item_code, 0)
        available = max_qty - already_delivered
        if available < 0:
            available = 0

        if qty > available:
            errors.append(
                "الصنف <b>" + str(item_code) + "</b>: "
                "الكمية المدخلة <b>" + str(frappe.utils.flt(qty, 2)) + "</b> "
                "أكبر من المتاح <b>" + str(frappe.utils.flt(available, 2)) + "</b> "
                "(طلب المواد: <b>" + str(frappe.utils.flt(max_qty, 2)) + "</b>, "
                "مُسلّم سابقاً: <b>" + str(frappe.utils.flt(already_delivered, 2)) + "</b>)"
            )

    if errors:
        frappe.throw(
            '<div dir="rtl" style="text-align:right">' + '<br>'.join(errors) + '</div>',
            title="تجاوز كمية طلب المواد"
        )
