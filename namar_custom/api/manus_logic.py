import frappe
from frappe import _
from frappe.utils import flt

# --- Validations ---

def validate_material_request_against_billed(doc, method=None):
    if doc.docstatus == 1 and doc.sales_order:
        billed_data = frappe.db.sql("""
            SELECT item_code, SUM(qty) as total_billed 
            FROM `tabSales Invoice Item` 
            WHERE sales_order = %s AND docstatus = 1 
            GROUP BY item_code
        """, (doc.sales_order,), as_dict=True)
        billed_map = {d.item_code: flt(d.total_billed) for d in billed_data}

        prev_mr_data = frappe.db.sql("""
            SELECT child.item_code, SUM(child.qty) as total_qty 
            FROM `tabMaterial Request` par 
            INNER JOIN `tabMaterial Request Item` child ON child.parent = par.name 
            WHERE par.sales_order = %s AND par.name != %s AND par.docstatus = 1 
            GROUP BY child.item_code
        """, (doc.sales_order, doc.name or "NEW"), as_dict=True)
        prev_qty_map = {d.item_code: flt(d.total_qty) for d in prev_mr_data}

        current_request_map = {}
        for item in doc.items:
            current_request_map[item.item_code] = current_request_map.get(item.item_code, 0) + flt(item.qty)

        errors = []
        for item_code, qty_now in current_request_map.items():
            total_billed = flt(billed_map.get(item_code, 0))
            total_prev_submitted = flt(prev_qty_map.get(item_code, 0))
            balance = total_billed - (total_prev_submitted + qty_now)

            if balance < -0.001:
                shortage = abs(balance)
                errors.append(
                    f"<li><b>الصنف: {item_code}</b><br>"
                    f"إجمالي المفوتر: {total_billed}, "
                    f"طلب سابق: {total_prev_submitted}, "
                    f"طلب حالي: {qty_now}, "
                    f"<b>العجز: {shortage}</b></li>"
                )

        if errors:
            frappe.throw(
                title=_("يوجد عجز في رصيد المفوتر"),
                msg=_("لا يمكن اعتماد طلب المواد لوجود عجز في رصيد المفوتر:<ul>{0}</ul>").format("".join(errors))
            )

def validate_payment_entry_supplier(doc, method=None):
    if doc.party_type == "Supplier":
        frappe.throw(_("عذراً، لا يُسمح باستخدام الموردين (Suppliers) في شاشة سند الدفع نهائياً، يرجى استخدام قيد اليومية."))

def validate_sales_invoice_qty_against_so(doc, method=None):
    for item in doc.items:
        if item.sales_order and item.so_detail:
            so_qty = frappe.db.get_value("Sales Order Item", item.so_detail, "qty") or 0
            billed_qty_history = frappe.db.sql("""
                SELECT SUM(qty) 
                FROM `tabSales Invoice Item` 
                WHERE so_detail = %s AND docstatus = 1 AND parent != %s
            """, (item.so_detail, doc.name))
            prev_billed_qty = flt(billed_qty_history[0][0]) if billed_qty_history else 0.0
            remaining_qty = so_qty - prev_billed_qty
            if flt(item.qty) > flt(remaining_qty) + 0.001:
                frappe.throw(
                    msg=_("خطأ في الصنف (Row #{0}): الكمية المدخلة ({1}) تتجاوز الكمية المتبقية في أمر البيع ({2}).").format(
                        item.idx, item.qty, remaining_qty
                    ),
                    title=_("تجاوز كمية أمر البيع")
                )

# --- API Functions ---

@frappe.whitelist()
def get_customer_gl_summary(customer=None):
    if not customer: customer = frappe.form_dict.get('customer')
    if not customer: return {}
    gl_aggregates = frappe.db.sql("""
        SELECT 
            SUM(CASE WHEN voucher_type = 'Sales Invoice' THEN debit - credit ELSE 0 END) as total_invoices,
            SUM(CASE WHEN voucher_type != 'Sales Invoice' THEN credit - debit ELSE 0 END) as total_payments,
            SUM(debit - credit) as balance
        FROM `tabGL Entry`
        WHERE party_type = 'Customer' AND party = %s AND is_cancelled = 0
    """, (customer), as_dict=True)
    gl_entries = frappe.db.sql("""
        SELECT posting_date, voucher_type, voucher_no, debit, credit, remarks
        FROM `tabGL Entry`
        WHERE party_type = 'Customer' AND party = %s AND is_cancelled = 0
        ORDER BY posting_date DESC, creation DESC
    """, (customer), as_dict=True)
    total_invoices = flt(gl_aggregates[0].total_invoices) if gl_aggregates and gl_aggregates[0] else 0.0
    total_payments = flt(gl_aggregates[0].total_payments) if gl_aggregates and gl_aggregates[0] else 0.0
    current_balance = flt(gl_aggregates[0].balance) if gl_aggregates and gl_aggregates[0] else 0.0
    return {"total_invoices": total_invoices, "total_payments": total_payments, "current_balance": current_balance, "gl_entries": gl_entries}

@frappe.whitelist()
def get_sales_order_summary(sales_order=None):
    if not sales_order: sales_order = frappe.form_dict.get('sales_order')
    if not sales_order: return []
    summary_data = {}
    so_items = frappe.db.sql("""
        SELECT so_item.name as so_detail, so_item.item_code, so_item.item_name, so_item.qty, so_item.delivered_qty,
            IFNULL((SELECT SUM(sii.qty) FROM `tabSales Invoice Item` sii WHERE sii.so_detail = so_item.name AND sii.docstatus = 1), 0) as billed_actual_qty
        FROM `tabSales Order Item` so_item WHERE so_item.parent = %s
    """, (sales_order), as_dict=1)
    for item in so_items:
        if item.item_code in summary_data:
            summary_data[item.item_code]['so_qty'] += item.qty
            summary_data[item.item_code]['delivered_qty'] += item.delivered_qty
            summary_data[item.item_code]['billed_qty'] += item.billed_actual_qty
            summary_data[item.item_code]['balance'] += item.qty
        else:
            summary_data[item.item_code] = {"item_code": item.item_code, "item_name": item.item_name, "so_qty": item.qty, "delivered_qty": item.delivered_qty, "billed_qty": item.billed_actual_qty, "mr_qty": 0.0, "installed_qty": 0.0, "balance": item.qty, "is_extra": False}
    related_mrs = frappe.get_all("Material Request", filters={"sales_order": sales_order, "docstatus": 1}, pluck="name", ignore_permissions=True)
    if related_mrs:
        mr_items = frappe.get_all("Material Request Item", filters={"parent": ["in", related_mrs]}, fields=["item_code", "item_name", "qty"], ignore_permissions=True)
        for row in mr_items:
            if row.item_code in summary_data:
                summary_data[row.item_code]['mr_qty'] += row.qty
                summary_data[row.item_code]['balance'] = summary_data[row.item_code]['so_qty'] - summary_data[row.item_code]['mr_qty']
            else:
                summary_data[row.item_code] = {"item_code": row.item_code, "item_name": row.item_name, "so_qty": 0.0, "delivered_qty": 0.0, "billed_qty": 0.0, "mr_qty": row.qty, "installed_qty": 0.0, "balance": 0.0 - row.qty, "is_extra": True}
    return list(summary_data.values())
