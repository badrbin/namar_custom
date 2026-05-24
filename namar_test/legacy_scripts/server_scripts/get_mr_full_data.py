mr_name = frappe.form_dict.get('mr_name')
sales_order = frappe.form_dict.get('sales_order')

result = {}

# 1. Stock Entries (always needed)
if mr_name:
    entries = frappe.get_all("Stock Entry",
        filters={
            "custom_material_request": mr_name,
            "docstatus": ["<", 2]
        },
        fields=["name", "stock_entry_type", "posting_date", "docstatus"],
        order_by="creation desc"
    )
    items_list = []
    if entries:
        se_names = [d.name for d in entries]
        items_list = frappe.get_all("Stock Entry Detail",
            filters={"parent": ["in", se_names]},
            fields=["parent", "item_code", "qty", "uom", "s_warehouse", "t_warehouse"]
        )
    result['stock_entries'] = {"entries": entries, "items": items_list}
else:
    result['stock_entries'] = {"entries": [], "items": []}

if sales_order:
    # 2. Allowed items from SO
    result['allowed_items'] = frappe.get_all("Sales Order Item",
        filters={"parent": sales_order},
        pluck="item_code",
        ignore_permissions=True
    )

    # 3. Customer from SO
    result['customer'] = frappe.db.get_value('Sales Order', sales_order, 'customer') or ''

    # 4. Related items summary
    summary_data = {}

    so_items = frappe.db.sql("""
        SELECT
            so_item.name as so_detail,
            so_item.item_code,
            so_item.item_name,
            so_item.qty,
            so_item.delivered_qty
        FROM `tabSales Order Item` so_item
        WHERE so_item.parent = %s
    """, (sales_order), as_dict=1)

    billed_items = frappe.db.sql("""
        SELECT
            sii.item_code,
            SUM(sii.qty) AS billed_qty
        FROM `tabSales Invoice Item` sii
        INNER JOIN `tabSales Invoice` si ON si.name = sii.parent
        WHERE sii.sales_order = %s
        AND si.docstatus = 1
        GROUP BY sii.item_code
    """, (sales_order), as_dict=1)

    for item in so_items:
        if item.item_code in summary_data:
            summary_data[item.item_code]['so_qty'] = summary_data[item.item_code]['so_qty'] + item.qty
            summary_data[item.item_code]['delivered_qty'] = summary_data[item.item_code]['delivered_qty'] + item.delivered_qty
            summary_data[item.item_code]['balance'] = summary_data[item.item_code]['balance'] + item.qty
        else:
            summary_data[item.item_code] = {
                "item_code": item.item_code,
                "item_name": item.item_name,
                "so_qty": item.qty,
                "delivered_qty": item.delivered_qty,
                "billed_qty": 0.0,
                "mr_qty": 0.0,
                "installed_qty": 0.0,
                "balance": item.qty,
                "is_extra": False
            }

    for row in billed_items:
        if row.item_code in summary_data:
            summary_data[row.item_code]['billed_qty'] = row.billed_qty
        else:
            summary_data[row.item_code] = {
                "item_code": row.item_code,
                "item_name": row.item_code,
                "so_qty": 0.0,
                "delivered_qty": 0.0,
                "billed_qty": row.billed_qty,
                "mr_qty": 0.0,
                "installed_qty": 0.0,
                "balance": 0.0,
                "is_extra": True
            }

    related_mrs = frappe.get_all("Material Request",
        filters={"sales_order": sales_order, "docstatus": 1},
        pluck="name",
        ignore_permissions=True
    )

    if related_mrs:
        mr_items = frappe.get_all("Material Request Item",
            filters={"parent": ["in", related_mrs]},
            fields=["item_code", "item_name", "qty"],
            ignore_permissions=True
        )
        for row in mr_items:
            if row.item_code in summary_data:
                summary_data[row.item_code]['mr_qty'] = summary_data[row.item_code]['mr_qty'] + row.qty
                summary_data[row.item_code]['balance'] = summary_data[row.item_code]['so_qty'] - summary_data[row.item_code]['mr_qty']
            else:
                summary_data[row.item_code] = {
                    "item_code": row.item_code,
                    "item_name": row.item_name,
                    "so_qty": 0.0,
                    "delivered_qty": 0.0,
                    "billed_qty": 0.0,
                    "mr_qty": row.qty,
                    "installed_qty": 0.0,
                    "balance": 0.0 - row.qty,
                    "is_extra": True
                }

    inst_items = frappe.db.sql("""
        SELECT child.item_code, child.qty
        FROM `tabInstallation Note Item` child
        INNER JOIN `tabInstallation Note` parent ON child.parent = parent.name
        WHERE parent.docstatus = 1
        AND parent.custom_sales_order = %s
    """, (sales_order), as_dict=1)

    for row in inst_items:
        if row.item_code in summary_data:
            summary_data[row.item_code]['installed_qty'] = summary_data[row.item_code]['installed_qty'] + row.qty
        else:
            summary_data[row.item_code] = {
                "item_code": row.item_code,
                "item_name": row.item_code,
                "so_qty": 0.0,
                "delivered_qty": 0.0,
                "billed_qty": 0.0,
                "mr_qty": 0.0,
                "installed_qty": row.qty,
                "balance": 0.0,
                "is_extra": True
            }

    result['related_items'] = list(summary_data.values())

frappe.response['message'] = result
