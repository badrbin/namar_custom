vip = int(doc.custom_vip or 0)

rows = frappe.db.sql(
    """
    SELECT mr.name
    FROM `tabMaterial Request`
    mr
    INNER JOIN `tabSales Order` so ON so.name = mr.sales_order
    WHERE so.customer = %s
      AND mr.docstatus < 2
      AND IFNULL(mr.custom_customer_vip, 0) != %s
    """,
    (doc.name, vip),
    as_dict=True,
)

for row in rows:
    frappe.db.set_value("Material Request", row.name, "custom_customer_vip", vip, update_modified=False)
