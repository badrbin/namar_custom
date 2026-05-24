if doc.sales_order:
    customer = frappe.db.get_value("Sales Order", doc.sales_order, "customer")
    doc.custom_customer_vip = int(frappe.db.get_value("Customer", customer, "custom_vip") or 0)
else:
    doc.custom_customer_vip = 0
