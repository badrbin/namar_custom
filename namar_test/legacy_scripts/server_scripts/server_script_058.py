if doc.docstatus == 1 and doc.sales_order:
    is_cash_sale = 0
    if frappe.get_meta("Sales Order").has_field("custom_cash_sale"):
        is_cash_sale = int(frappe.db.get_value("Sales Order", doc.sales_order, "custom_cash_sale") or 0)

    if not is_cash_sale:
        billed_data = frappe.db.sql("""
            SELECT item_code, SUM(qty) as total_billed
            FROM `tabSales Invoice Item`
            WHERE sales_order = %s
            AND docstatus = 1
            GROUP BY item_code
        """, (doc.sales_order,), as_dict=True)

        billed_map = {d.item_code: frappe.utils.flt(d.total_billed) for d in billed_data}

        prev_mr_data = frappe.db.sql("""
            SELECT
                child.item_code, SUM(child.qty) as total_qty
            FROM
                `tabMaterial Request` par
            INNER JOIN
                `tabMaterial Request Item` child ON child.parent = par.name
            WHERE
                par.sales_order = %s
                AND par.name != %s
                AND par.docstatus = 1
            GROUP BY
                child.item_code
        """, (doc.sales_order, doc.name or "NEW"), as_dict=True)

        prev_qty_map = {d.item_code: frappe.utils.flt(d.total_qty) for d in prev_mr_data}

        current_request_map = {}
        for item in doc.items:
            current_request_map[item.item_code] = current_request_map.get(item.item_code, 0) + frappe.utils.flt(item.qty)

        errors = []
        precision = 3

        for item_code, qty_now in current_request_map.items():
            total_billed = frappe.utils.flt(billed_map.get(item_code, 0), precision)
            total_prev_submitted = frappe.utils.flt(prev_qty_map.get(item_code, 0), precision)
            balance = frappe.utils.flt(total_billed - (total_prev_submitted + qty_now), precision)

            if balance < -0.001:
                shortage = abs(balance)
                errors.append(
                    f"<div style='text-align: right; direction: rtl; border-bottom:1px solid #ccc; padding:5px;'>"
                    f"<b>الصنف: {item_code}</b><br>"
                    f"• إجمالي المفوتر: <b>{total_billed:.2f}</b><br>"
                    f"• تم طلبه سابقاً (مرحّل): <b>{total_prev_submitted:.2f}</b><br>"
                    f"• طلبك الحالي: <b>{qty_now:.2f}</b><br>"
                    f"• <b style='color:red'>العجز الحالي: {shortage:.2f}</b>"
                    f"</div>"
                )

        if errors:
            frappe.throw(
                title="يوجد عجز في رصيد المفوتر",
                msg=(
                    f"<div style='text-align: right; direction: rtl;'>"
                    f"لا يمكن اعتماد طلب المواد لوجود عجز في رصيد المفوتر:<br><br>"
                    f"{''.join(errors)}"
                    f"</div>"
                )
            )
