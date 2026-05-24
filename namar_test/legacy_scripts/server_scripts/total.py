# حساب إجمالي الكميات
flt = frappe.utils.flt
total_qty = 0
for item in doc.items:
	total_qty += flt(item.qty)

doc.custom_total_quantity = total_qty
