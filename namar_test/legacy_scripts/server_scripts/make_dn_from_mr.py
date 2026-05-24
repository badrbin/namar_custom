# Server Script (API)
# API Method: make_dn_from_mr

action = frappe.form_dict.get('action')
material_request = frappe.form_dict.get('material_request')
source_name = frappe.form_dict.get('source_name')

if action == 'get_customer':
    if not material_request:
        frappe.throw('Material Request is required')

    so_name = frappe.db.get_value('Material Request', material_request, 'sales_order')
    if so_name:
        customer = frappe.db.get_value('Sales Order', so_name, 'customer')
        frappe.response['message'] = {'customer': customer}
    else:
        frappe.response['message'] = {}

elif action == 'get_qty':
    if not material_request:
        frappe.throw('Material Request is required')

    so_name = frappe.db.get_value('Material Request', material_request, 'sales_order')

    if not so_name:
        frappe.response['message'] = {}
    else:
        remaining_items = frappe.db.sql("""
            SELECT item_code, SUM(qty - delivered_qty) as remaining
            FROM `tabSales Order Item`
            WHERE parent = %s
            AND qty > delivered_qty
            GROUP BY item_code
        """, (so_name,), as_dict=True)

        qty_map = {}
        for item in remaining_items:
            qty_map[item.item_code] = frappe.utils.flt(item.remaining)

        frappe.response['message'] = qty_map

else:
    # إنشاء سند تسليم من طلب المواد
    if not source_name:
        frappe.throw('Material Request name is required')

    mr_data = frappe.db.get_value('Material Request', source_name,
        ['name', 'docstatus', 'sales_order'], as_dict=True)

    if not mr_data:
        frappe.throw('Material Request not found')
    if mr_data.docstatus != 1:
        frappe.throw('Material Request must be submitted')

    # جلب أصناف طلب المواد
    mr_items = frappe.db.sql("""
        SELECT name, item_code, item_name, description, qty, uom, stock_uom, conversion_factor
        FROM `tabMaterial Request Item`
        WHERE parent = %s
    """, (source_name,), as_dict=True)

    # جلب الكميات المتبقية من أمر البيع (إن وجد)
    qty_map = {}
    so_detail_map = {}
    if mr_data.sales_order:
        so_items = frappe.db.sql("""
            SELECT name, item_code, qty, delivered_qty, rate, warehouse
            FROM `tabSales Order Item`
            WHERE parent = %s
            AND qty > delivered_qty
            ORDER BY idx
        """, (mr_data.sales_order,), as_dict=True)

        for si in so_items:
            remaining = frappe.utils.flt(si.qty) - frappe.utils.flt(si.delivered_qty)
            prev = qty_map.get(si.item_code, 0)
            qty_map[si.item_code] = prev + remaining
            if si.item_code not in so_detail_map:
                so_detail_map[si.item_code] = []
            so_detail_map[si.item_code].append({
                'so_detail': si.name,
                'remaining': remaining,
                'rate': frappe.utils.flt(si.rate),
                'warehouse': si.warehouse or ''
            })

    # جلب العميل
    customer = None
    if mr_data.sales_order:
        customer = frappe.db.get_value('Sales Order', mr_data.sales_order, 'customer')

    # إنشاء سند التسليم
    dn = frappe.new_doc('Delivery Note')

    # جلب الكميات المسلّمة سابقاً
    delivered_data = frappe.db.sql("""
        SELECT child.item_code, SUM(child.qty) as total_delivered
        FROM `tabDelivery Note Item` child
        INNER JOIN `tabDelivery Note` parent ON child.parent = parent.name
        WHERE parent.custom_material_request = %s
        AND parent.docstatus = 1
        GROUP BY child.item_code
    """, (source_name,), as_dict=True)

    delivered_map = {}
    for row in delivered_data:
        delivered_map[row.item_code] = frappe.utils.flt(row.total_delivered)

    # بناء خريطة كميات طلب المواد المتبقية
    mr_remaining = {}
    for item in mr_items:
        prev = mr_remaining.get(item.item_code, 0)
        mr_remaining[item.item_code] = prev + frappe.utils.flt(item.qty)

    for code in mr_remaining:
        mr_remaining[code] = mr_remaining[code] - delivered_map.get(code, 0)
        if mr_remaining[code] < 0:
            mr_remaining[code] = 0

    dn.custom_material_request = source_name
    if customer:
        dn.customer = customer

    # ملء الحقول الإلزامية يدوياً (بدون set_missing_values)
    company = frappe.db.get_default('Company') or frappe.db.get_single_value('Global Defaults', 'default_company')
    dn.company = company

    company_data = frappe.db.get_value('Company', company,
        ['default_currency'], as_dict=True) or {}
    default_currency = company_data.get('default_currency') or 'SAR'
    default_warehouse = frappe.db.get_single_value('Stock Settings', 'default_warehouse') or ''

    # حقول العملة وقائمة الأسعار
    dn.currency = default_currency
    dn.selling_price_list = frappe.db.get_single_value('Selling Settings', 'selling_price_list') or ''
    dn.price_list_currency = default_currency
    dn.conversion_rate = 1
    dn.plc_conversion_rate = 1
    dn.ignore_pricing_rule = 1

    for item in mr_items:
        mr_avail = mr_remaining.get(item.item_code, 0)
        if mr_avail <= 0:
            continue

        # بدون أمر بيع - نضيف الصنف مباشرة
        if not mr_data.sales_order:
            dn.append('items', {
                'item_code': item.item_code,
                'item_name': item.item_name,
                'description': item.description,
                'qty': mr_avail,
                'uom': item.uom or item.stock_uom or '',
                'stock_uom': item.stock_uom or item.uom or '',
                'conversion_factor': item.conversion_factor or 1,
                'warehouse': default_warehouse
            })
            mr_remaining[item.item_code] = 0
            continue

        # مع أمر بيع: نفصل الكمية على أسطر SO حسب المتبقي في كل سطر
        remaining_to_add = mr_avail

        while remaining_to_add > 0 and item.item_code in so_detail_map and so_detail_map[item.item_code]:
            so_row = so_detail_map[item.item_code][0]
            # الكمية = الأقل بين: المتبقي للإضافة، المتبقي في سطر SO
            row_qty = min(remaining_to_add, so_row['remaining'])

            if row_qty <= 0:
                so_detail_map[item.item_code].pop(0)
                continue

            dn.append('items', {
                'item_code': item.item_code,
                'item_name': item.item_name,
                'description': item.description,
                'qty': row_qty,
                'uom': item.uom or item.stock_uom or '',
                'stock_uom': item.stock_uom or item.uom or '',
                'conversion_factor': item.conversion_factor or 1,
                'warehouse': so_row['warehouse'] or default_warehouse,
                'against_sales_order': mr_data.sales_order,
                'so_detail': so_row['so_detail'],
                'rate': frappe.utils.flt(so_row['rate']),
                'price_list_rate': frappe.utils.flt(so_row['rate']),
                'discount_percentage': 0,
                'discount_amount': 0
            })

            so_row['remaining'] = so_row['remaining'] - row_qty
            remaining_to_add = remaining_to_add - row_qty
            if so_row['remaining'] <= 0:
                so_detail_map[item.item_code].pop(0)

        mr_remaining[item.item_code] = remaining_to_add

    if not dn.items:
        frappe.throw('جميع الأصناف تم تسليمها بالكامل')

    dn.flags.ignore_permissions = True

    # حساب الإجماليات
    total = 0
    for row in dn.items:
        r = frappe.utils.flt(row.rate)
        row.base_rate = r
        row.base_price_list_rate = r
        row.net_rate = r
        row.base_net_rate = r
        row.amount = r * frappe.utils.flt(row.qty)
        row.base_amount = row.amount
        row.net_amount = row.amount
        row.base_net_amount = row.amount
        total = total + row.amount

    dn.total = total
    dn.base_total = total
    dn.net_total = total
    dn.base_net_total = total
    dn.grand_total = total
    dn.base_grand_total = total
    dn.rounded_total = round(total)
    dn.base_rounded_total = round(total)

    # إرجاع الـ doc بدون حفظ - المستخدم يحفظ يدوياً
    frappe.response['message'] = dn
