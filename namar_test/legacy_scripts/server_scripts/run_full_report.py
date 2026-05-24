
filters = frappe.form_dict
from_date = filters.get("from_date", "")
to_date = filters.get("to_date", "")

conds = ["so.docstatus = 1"]
values = {}

if from_date:
    conds.append("so.transaction_date >= %(from_date)s")
    values["from_date"] = from_date

if to_date:
    conds.append("so.transaction_date <= %(to_date)s")
    values["to_date"] = to_date

if filters.get("branch"):
    conds.append("so.branch = %(branch)s")
    values["branch"] = filters["branch"]

if filters.get("customer"):
    conds.append("so.customer = %(customer)s")
    values["customer"] = filters["customer"]

if filters.get("sales_order"):
    conds.append("so.name = %(sales_order)s")
    values["sales_order"] = filters["sales_order"]

if filters.get("item_code"):
    conds.append("soi.item_code = %(item_code)s")
    values["item_code"] = filters["item_code"]

where = " AND ".join(conds)

query = """
    SELECT
        so.name AS sales_order,
        so.transaction_date AS so_date,
        so.branch,
        so.customer AS customer_id,
        so.customer_name,
        IFNULL(gl.balance, 0) AS customer_balance,
        GROUP_CONCAT(DISTINCT mr.name ORDER BY mr.name SEPARATOR ', ') AS mr_name,
        MIN(mr.transaction_date) AS mr_date,
        so.rounded_total AS so_total,
        IFNULL(so_bill.billed, 0) AS billed_amount,
        IFNULL(so_paid.paid, 0) AS paid_amount,
        (IFNULL(so_paid.paid, 0) - IFNULL(so_bill.billed, 0)) AS remaining_billed,
        (IFNULL(so_paid.paid, 0) - so.rounded_total) AS remaining_so,
        soi.item_code,
        soi.item_name,
        soi.qty AS so_qty,
        IFNULL(mri_agg.mr_qty, 0) AS mr_qty,
        soi.delivered_qty,
        IFNULL(sii_agg.billed_qty, 0) AS billed_qty,
        IFNULL(ins_agg.installed_qty, 0) AS installed_qty,
        (IFNULL(sii_agg.billed_qty, 0) - IFNULL(mri_agg.mr_qty, 0)) AS billed_balance,
        (soi.delivered_qty - IFNULL(mri_agg.mr_qty, 0)) AS delivered_balance,
        (IFNULL(ins_agg.installed_qty, 0) - IFNULL(mri_agg.mr_qty, 0)) AS installed_balance
    FROM `tabSales Order` so
    INNER JOIN `tabSales Order Item` soi ON soi.parent = so.name
    LEFT JOIN `tabMaterial Request` mr
        ON mr.sales_order = so.name AND mr.docstatus = 1
    LEFT JOIN (
        SELECT mr2.sales_order, mri2.item_code, SUM(mri2.qty) AS mr_qty
        FROM `tabMaterial Request` mr2
        INNER JOIN `tabMaterial Request Item` mri2 ON mri2.parent = mr2.name
        WHERE mr2.docstatus = 1
        GROUP BY mr2.sales_order, mri2.item_code
    ) mri_agg ON mri_agg.sales_order = so.name AND mri_agg.item_code = soi.item_code
    LEFT JOIN (
        SELECT sii.so_detail, SUM(sii.qty) AS billed_qty
        FROM `tabSales Invoice Item` sii
        INNER JOIN `tabSales Invoice` si ON si.name = sii.parent AND si.docstatus = 1
        GROUP BY sii.so_detail
    ) sii_agg ON sii_agg.so_detail = soi.name
    LEFT JOIN (
        SELECT ini.item_code, ins.custom_sales_order, SUM(ini.qty) AS installed_qty
        FROM `tabInstallation Note` ins
        INNER JOIN `tabInstallation Note Item` ini ON ini.parent = ins.name
        WHERE ins.docstatus = 1
        GROUP BY ins.custom_sales_order, ini.item_code
    ) ins_agg ON ins_agg.custom_sales_order = so.name AND ins_agg.item_code = soi.item_code
    LEFT JOIN (
        SELECT party, SUM(debit - credit) AS balance
        FROM `tabGL Entry`
        WHERE party_type = 'Customer' AND is_cancelled = 0
        GROUP BY party
    ) gl ON gl.party = so.customer
    LEFT JOIN (
        SELECT sii2.sales_order, SUM(sii2.amount) AS billed
        FROM `tabSales Invoice Item` sii2
        INNER JOIN `tabSales Invoice` si2 ON si2.name = sii2.parent AND si2.docstatus = 1
        GROUP BY sii2.sales_order
    ) so_bill ON so_bill.sales_order = so.name
    LEFT JOIN (
        SELECT per.reference_name, SUM(per.allocated_amount) AS paid
        FROM `tabPayment Entry Reference` per
        INNER JOIN `tabPayment Entry` pe ON pe.name = per.parent AND pe.docstatus = 1
        WHERE per.reference_doctype = 'Sales Order'
        GROUP BY per.reference_name
    ) so_paid ON so_paid.reference_name = so.name
    WHERE """ + where + """
    GROUP BY so.name, soi.item_code, soi.name
    ORDER BY so.transaction_date, so.name, soi.idx
"""

result = frappe.db.sql(query, values, as_dict=True)
frappe.response["message"] = result
