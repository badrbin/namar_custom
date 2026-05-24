sales_order = frappe.form_dict.get('sales_order')
mr_name = frappe.form_dict.get('mr_name')
current_items_raw = frappe.form_dict.get('current_items')


def parse_current_items(raw_items):
    if not raw_items:
        return []
    try:
        parsed = frappe.parse_json(raw_items)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def empty_row(item_code, item_name, is_extra):
    return {
        "item_code": item_code,
        "item_name": item_name or item_code,
        "so_qty": 0.0,
        "delivered_qty": 0.0,
        "billed_qty": 0.0,
        "installed_qty": 0.0,
        "other_mr_qty": 0.0,
        "current_mr_qty": 0.0,
        "mr_qty": 0.0,
        "balance_without_current": 0.0,
        "balance": 0.0,
        "current_delivered_qty": 0.0,
        "current_billed_qty": 0.0,
        "current_installed_qty": 0.0,
        "current_billed_balance": 0.0,
        "current_delivered_balance": 0.0,
        "current_installed_balance": 0.0,
        "is_extra": is_extra
    }


def get_row(summary_data, item_code, item_name=None, is_extra=False):
    if item_code not in summary_data:
        summary_data[item_code] = empty_row(item_code, item_name, is_extra)
    row = summary_data[item_code]
    if item_name and (not row.get("item_name") or row.get("item_name") == row.get("item_code")):
        row["item_name"] = item_name
    if not is_extra:
        row["is_extra"] = False
    return row


if not sales_order:
    frappe.response['message'] = []
else:
    summary_data = {}
    current_items = parse_current_items(current_items_raw)

    if mr_name and not current_items:
        current_items = frappe.get_all(
            "Material Request Item",
            filters={"parent": mr_name},
            fields=["item_code", "item_name", "qty"],
            ignore_permissions=True
        )

    so_items = frappe.db.sql("""
        SELECT
            so_item.name AS so_detail,
            so_item.item_code,
            so_item.item_name,
            so_item.qty,
            so_item.delivered_qty,
            IFNULL((
                SELECT SUM(sii.qty)
                FROM `tabSales Invoice Item` sii
                INNER JOIN `tabSales Invoice` si ON si.name = sii.parent
                WHERE sii.so_detail = so_item.name
                AND si.docstatus = 1
            ), 0) AS billed_actual_qty
        FROM `tabSales Order Item` so_item
        WHERE so_item.parent = %s
    """, (sales_order,), as_dict=1)

    for item in so_items:
        row = get_row(summary_data, item.item_code, item.item_name, False)
        row['so_qty'] = row['so_qty'] + frappe.utils.flt(item.qty)
        row['delivered_qty'] = row['delivered_qty'] + frappe.utils.flt(item.delivered_qty)
        row['billed_qty'] = row['billed_qty'] + frappe.utils.flt(item.billed_actual_qty)

    related_mrs = frappe.get_all(
        "Material Request",
        filters={"sales_order": sales_order, "docstatus": ["<", 2]},
        fields=["name"],
        ignore_permissions=True
    )

    other_related_mrs = []
    for related_mr in related_mrs:
        if related_mr.name != mr_name:
            other_related_mrs.append(related_mr.name)

    if other_related_mrs:
        other_mr_items = frappe.get_all(
            "Material Request Item",
            filters={"parent": ["in", other_related_mrs]},
            fields=["item_code", "item_name", "qty"],
            ignore_permissions=True
        )
        for item in other_mr_items:
            row = get_row(summary_data, item.item_code, item.item_name, True)
            row['other_mr_qty'] = row['other_mr_qty'] + frappe.utils.flt(item.qty)

    for item in current_items:
        item_code = item.get("item_code")
        if not item_code:
            continue
        row = get_row(summary_data, item_code, item.get("item_name"), True)
        row['current_mr_qty'] = row['current_mr_qty'] + frappe.utils.flt(item.get("qty"))

    inst_items = frappe.db.sql("""
        SELECT child.item_code, child.qty
        FROM `tabInstallation Note Item` child
        INNER JOIN `tabInstallation Note` parent ON child.parent = parent.name
        WHERE parent.docstatus = 1
        AND parent.custom_sales_order = %s
    """, (sales_order,), as_dict=1)

    for item in inst_items:
        row = get_row(summary_data, item.item_code, item.item_code, True)
        row['installed_qty'] = row['installed_qty'] + frappe.utils.flt(item.qty)

    if mr_name:
        current_delivered_rows = frappe.db.sql("""
            SELECT child.item_code, SUM(child.qty) AS total_qty
            FROM `tabDelivery Note Item` child
            INNER JOIN `tabDelivery Note` parent ON child.parent = parent.name
            WHERE parent.docstatus = 1
            AND parent.custom_material_request = %s
            GROUP BY child.item_code
        """, (mr_name,), as_dict=1)

        for item in current_delivered_rows:
            row = get_row(summary_data, item.item_code, item.item_code, True)
            row['current_delivered_qty'] = row['current_delivered_qty'] + frappe.utils.flt(item.total_qty)

        current_installed_rows = frappe.db.sql("""
            SELECT child.item_code, SUM(child.qty) AS total_qty
            FROM `tabInstallation Note Item` child
            INNER JOIN `tabInstallation Note` parent ON child.parent = parent.name
            WHERE parent.docstatus = 1
            AND parent.custom_material_request = %s
            GROUP BY child.item_code
        """, (mr_name,), as_dict=1)

        for item in current_installed_rows:
            row = get_row(summary_data, item.item_code, item.item_code, True)
            row['current_installed_qty'] = row['current_installed_qty'] + frappe.utils.flt(item.total_qty)

        current_billed_rows = frappe.db.sql("""
            SELECT
                COALESCE(dni.item_code, sii.item_code) AS item_code,
                SUM(sii.qty) AS total_qty
            FROM `tabSales Invoice Item` sii
            INNER JOIN `tabSales Invoice` si ON si.name = sii.parent
            LEFT JOIN `tabDelivery Note Item` dni ON dni.name = sii.dn_detail
            LEFT JOIN `tabDelivery Note` dn ON dn.name = COALESCE(dni.parent, sii.delivery_note)
            WHERE si.docstatus = 1
            AND dn.custom_material_request = %s
            GROUP BY COALESCE(dni.item_code, sii.item_code)
        """, (mr_name,), as_dict=1)

        for item in current_billed_rows:
            row = get_row(summary_data, item.item_code, item.item_code, True)
            row['current_billed_qty'] = row['current_billed_qty'] + frappe.utils.flt(item.total_qty)

    final_rows = []
    for row in summary_data.values():
        row['mr_qty'] = frappe.utils.flt(row['other_mr_qty']) + frappe.utils.flt(row['current_mr_qty'])
        row['balance_without_current'] = frappe.utils.flt(row['so_qty']) - frappe.utils.flt(row['other_mr_qty'])
        row['balance'] = frappe.utils.flt(row['so_qty']) - frappe.utils.flt(row['mr_qty'])
        row['current_billed_balance'] = frappe.utils.flt(row['current_billed_qty']) - frappe.utils.flt(row['current_mr_qty'])
        row['current_delivered_balance'] = frappe.utils.flt(row['current_delivered_qty']) - frappe.utils.flt(row['current_mr_qty'])
        row['current_installed_balance'] = frappe.utils.flt(row['current_installed_qty']) - frappe.utils.flt(row['current_mr_qty'])
        if not row.get("item_name"):
            row["item_name"] = row["item_code"]
        final_rows.append(row)

    final_rows.sort(key=lambda d: (1 if d.get("is_extra") else 0, d.get("item_code") or ""))
    frappe.response['message'] = final_rows
