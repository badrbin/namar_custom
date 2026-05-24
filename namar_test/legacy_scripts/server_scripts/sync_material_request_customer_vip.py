VIP_FIELD = "custom_vip"
MR_VIP_FIELD = "custom_customer_vip"


def to_int(value):
    try:
        return int(value or 0)
    except Exception:
        return 0


def sync_material_requests_for_customer(customer, dry_run=0):
    if not customer:
        return {"customer": customer, "vip": 0, "matched": 0, "updated": 0}

    vip = to_int(frappe.db.get_value("Customer", customer, VIP_FIELD) or 0)
    rows = frappe.db.sql(
        """
        SELECT mr.name, IFNULL(mr.custom_customer_vip, 0) AS current_vip
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

    updated = 0
    if not dry_run:
        for row in rows:
            frappe.db.set_value("Material Request", row.name, MR_VIP_FIELD, vip, update_modified=False)
            updated += 1

    return {
        "customer": customer,
        "vip": vip,
        "matched": len(rows),
        "updated": updated,
    }


def reset_material_requests_without_sales_order(dry_run=0):
    rows = frappe.db.sql(
        """
        SELECT mr.name
        FROM `tabMaterial Request`
        mr
        LEFT JOIN `tabSales Order` so ON so.name = mr.sales_order
        WHERE mr.docstatus < 2
          AND IFNULL(mr.custom_customer_vip, 0) != 0
          AND (IFNULL(mr.sales_order, '') = '' OR so.name IS NULL)
        """,
        as_dict=True,
    )

    updated = 0
    if not dry_run:
        for row in rows:
            frappe.db.set_value("Material Request", row.name, MR_VIP_FIELD, 0, update_modified=False)
            updated += 1

    return {
        "matched": len(rows),
        "updated": updated,
    }


dry_run = to_int(frappe.form_dict.get("dry_run"))
single_customer = (frappe.form_dict.get("customer") or "").strip()
limit = to_int(frappe.form_dict.get("limit"))
if limit <= 0 or limit > 10000:
    limit = 10000

if single_customer:
    result = sync_material_requests_for_customer(single_customer, dry_run)
    frappe.response["message"] = {
        "status": "ok",
        "dry_run": dry_run,
        "result": result,
    }
else:
    reset_result = reset_material_requests_without_sales_order(dry_run)
    customer_rows = frappe.db.sql(
        """
        SELECT DISTINCT so.customer
        FROM `tabMaterial Request`
        mr
        INNER JOIN `tabSales Order` so ON so.name = mr.sales_order
        WHERE IFNULL(mr.sales_order, '') != ''
          AND mr.docstatus < 2
        ORDER BY so.customer
        LIMIT """
        + str(limit),
        as_dict=True,
    )

    results = []
    updated_count = 0
    matched_count = 0
    for row in customer_rows:
        result = sync_material_requests_for_customer(row.customer, dry_run)
        matched_count += to_int(result.get("matched"))
        updated_count += to_int(result.get("updated"))
        if result.get("matched"):
            results.append(result)

    frappe.response["message"] = {
        "status": "ok",
        "dry_run": dry_run,
        "source": "sales_order.customer",
        "customers_checked": len(customer_rows),
        "matched_count": matched_count + to_int(reset_result.get("matched")),
        "updated_count": updated_count + to_int(reset_result.get("updated")),
        "reset_without_sales_order": reset_result,
        "results": results,
    }
