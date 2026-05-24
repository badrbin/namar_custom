VIP_THRESHOLD = 50000
VIP_FIELD = "custom_vip"
MR_VIP_FIELD = "custom_customer_vip"


def to_int(value):
    try:
        return int(value or 0)
    except Exception:
        return 0


def customer_net_receipts(customer):
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
    return frappe.utils.flt(rows[0].net_receipts if rows else 0)


def sync_material_requests_for_customer(customer, vip):
    if not customer:
        return 0

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
    return len(rows)


def set_customer_vip_if_needed(customer, dry_run=0):
    if not customer:
        return None

    net_receipts = customer_net_receipts(customer)
    current_vip = to_int(frappe.db.get_value("Customer", customer, VIP_FIELD) or 0)
    should_be_vip = net_receipts > VIP_THRESHOLD
    updated = 0
    material_requests_updated = 0

    if should_be_vip and not current_vip and not dry_run:
        frappe.db.set_value("Customer", customer, VIP_FIELD, 1)
        current_vip = 1
        updated = 1

    if not dry_run:
        material_requests_updated = sync_material_requests_for_customer(customer, current_vip)

    return {
        "customer": customer,
        "net_receipts": net_receipts,
        "threshold": VIP_THRESHOLD,
        "should_be_vip": 1 if should_be_vip else 0,
        "vip": current_vip,
        "updated": updated,
        "material_requests_updated": material_requests_updated,
    }


dry_run = to_int(frappe.form_dict.get("dry_run"))
single_customer = (frappe.form_dict.get("customer") or "").strip()
limit = to_int(frappe.form_dict.get("limit"))
if limit <= 0 or limit > 10000:
    limit = 10000

if single_customer:
    frappe.response["message"] = {
        "status": "ok",
        "dry_run": dry_run,
        "result": set_customer_vip_if_needed(single_customer, dry_run),
    }
else:
    candidate_query = (
        """
        SELECT party AS customer, IFNULL(SUM(credit - debit), 0) AS net_receipts
        FROM `tabGL Entry`
        WHERE party_type = 'Customer'
          AND party IS NOT NULL
          AND party != ''
          AND is_cancelled = 0
          AND voucher_type = 'Payment Entry'
        GROUP BY party
        HAVING net_receipts > %s
        ORDER BY net_receipts DESC
        LIMIT """
        + str(limit)
    )
    candidate_rows = frappe.db.sql(
        candidate_query,
        (VIP_THRESHOLD,),
        as_dict=True,
    )

    results = []
    updated_count = 0
    for row in candidate_rows:
        result = set_customer_vip_if_needed(row.customer, dry_run)
        if result:
            updated_count += to_int(result.get("updated"))
            results.append(result)

    frappe.response["message"] = {
        "status": "ok",
        "dry_run": dry_run,
        "threshold": VIP_THRESHOLD,
        "candidates": len(results),
        "updated_count": updated_count,
        "results": results,
    }
