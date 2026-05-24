VIP_THRESHOLD = 50000
VIP_FIELD = "custom_vip"
MR_VIP_FIELD = "custom_customer_vip"


def sync_material_requests_for_customer(customer, vip):
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
        (customer, vip),
        as_dict=True,
    )
    for row in rows:
        frappe.db.set_value("Material Request", row.name, MR_VIP_FIELD, vip, update_modified=False)


def sync_customer_vip(customer):
    if not customer:
        return

    rows = frappe.db.sql(
        """
        SELECT IFNULL(SUM(credit - debit), 0) AS net_receipts
        FROM `tabGL Entry`
        WHERE party_type = 'Customer'
          AND party = %s
          AND is_cancelled = 0
          AND voucher_type = 'Payment Entry'
        """,
        (customer,),
        as_dict=True,
    )
    net_receipts = frappe.utils.flt(rows[0].net_receipts if rows else 0)
    current_vip = int(frappe.db.get_value("Customer", customer, VIP_FIELD) or 0)

    if net_receipts > VIP_THRESHOLD and not current_vip:
        frappe.db.set_value("Customer", customer, VIP_FIELD, 1)
        current_vip = 1

    sync_material_requests_for_customer(customer, current_vip)


if doc.party_type == "Customer" and doc.party:
    sync_customer_vip(doc.party)
