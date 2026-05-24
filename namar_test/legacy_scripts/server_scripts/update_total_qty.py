
results = frappe.db.sql("""
    SELECT mr.name, COALESCE(SUM(mri.qty), 0) as total_qty
    FROM `tabMaterial Request` mr
    LEFT JOIN `tabMaterial Request Item` mri ON mri.parent = mr.name
    WHERE mr.docstatus IN (0, 1)
    GROUP BY mr.name
""", as_dict=True)

updated = 0
for r in results:
    cur = frappe.db.get_value("Material Request", r["name"], "custom_total_quantity") or 0
    if float(cur) != float(r["total_qty"]):
        frappe.db.set_value("Material Request", r["name"], "custom_total_quantity", r["total_qty"], update_modified=False)
        updated += 1

frappe.db.commit()
frappe.response["message"] = {"updated": updated, "checked": len(results)}
