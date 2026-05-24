comp_order_rows = frappe.db.sql(
    "SELECT component_name FROM `tabStore Component` WHERE IFNULL(custom_print_sort_order, 0) > 0 ORDER BY custom_print_sort_order ASC",
    as_dict=True
)
comp_order = [r.component_name for r in comp_order_rows]

comp_labels = {}
for row in frappe.db.sql("SELECT component_name, label_ar FROM `tabStore Component`", as_dict=True):
    comp_labels[row.component_name] = row.label_ar or row.component_name

def parse_store_data(value):
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except Exception:
        return []
    if isinstance(parsed, list):
        return parsed
    return []

filters = frappe.form_dict
conditions = "mr.docstatus = 1"
values = {}

if filters.get("from_date"):
    conditions += " AND mr.transaction_date >= %(from_date)s"
    values["from_date"] = filters["from_date"]
if filters.get("to_date"):
    conditions += " AND mr.transaction_date <= %(to_date)s"
    values["to_date"] = filters["to_date"]
if filters.get("workflow_state"):
    conditions += " AND mr.workflow_state = %(workflow_state)s"
    values["workflow_state"] = filters["workflow_state"]
if filters.get("material_request"):
    conditions += " AND mr.name = %(material_request)s"
    values["material_request"] = filters["material_request"]
if filters.get("customer_name"):
    conditions += " AND mr.customer_name LIKE %(customer_name)s"
    values["customer_name"] = "%" + filters["customer_name"] + "%"

items_query = """
    SELECT
        mri.parent as material_request,
        mr.transaction_date,
        mr.workflow_state,
        mr.customer_name,
        mr.sales_order,
        mri.item_code,
        mri.item_name,
        mri.qty,
        mri.custom_cutting_template as cutting_template,
        mri.custom_result_leaf_w as leaf_w,
        mri.custom_result_leaf_h as leaf_h,
        mri.custom_result_u_w as u_w,
        mri.custom_result_u_h as u_h,
        mri.custom_result_panel_w as panel_w,
        mri.custom_result_panel_h as panel_h,
        mri.custom_store_data as store_data,
        it.item_group
    FROM `tabMaterial Request Item` mri
    JOIN `tabMaterial Request` mr ON mr.name = mri.parent
    LEFT JOIN `tabItem` it ON it.name = mri.item_code
    WHERE """ + conditions + """
    ORDER BY mr.transaction_date DESC, mri.parent, mri.idx
"""

items = frappe.db.sql(items_query, values, as_dict=True)

cutting_group_set = set()
for row in frappe.db.sql(
    "SELECT DISTINCT item_group FROM `tabCutting Template Item` WHERE item_group IS NOT NULL AND item_group != ''",
    as_dict=True,
):
    cutting_group_set.add(row.item_group)

filtered_items = []
for item in items:
    if item.cutting_template or item.item_group in cutting_group_set:
        filtered_items.append(item)

items = filtered_items

found_comps = []
for item in items:
    if item.store_data:
        stores = parse_store_data(item.store_data)
        if isinstance(stores, list):
            for s in stores:
                c = s.get("component", "")
                if c and c not in found_comps:
                    found_comps.append(c)

sorted_comps = []
for c in comp_order:
    if c in found_comps:
        sorted_comps.append(c)
for c in found_comps:
    if c not in sorted_comps:
        sorted_comps.append(c)

columns = [
    {"fieldname": "material_request", "label": "طلب المواد", "fieldtype": "Link", "options": "Material Request", "width": 120},
    {"fieldname": "transaction_date", "label": "التاريخ", "fieldtype": "Date", "width": 100},
    {"fieldname": "workflow_state", "label": "الحالة", "fieldtype": "Data", "width": 120},
    {"fieldname": "customer_name", "label": "العميل", "fieldtype": "Data", "width": 140},
    {"fieldname": "item_code", "label": "كود الصنف", "fieldtype": "Link", "options": "Item", "width": 100},
    {"fieldname": "item_name", "label": "اسم الصنف", "fieldtype": "Data", "width": 140},
    {"fieldname": "qty", "label": "الكمية", "fieldtype": "Float", "width": 70},
    {"fieldname": "cutting_template", "label": "النموذج", "fieldtype": "Data", "width": 100},
    {"fieldname": "leaf_w", "label": "Leaf W", "fieldtype": "Float", "width": 70},
    {"fieldname": "leaf_h", "label": "Leaf H", "fieldtype": "Float", "width": 70},
    {"fieldname": "u_w", "label": "U W", "fieldtype": "Float", "width": 60},
    {"fieldname": "u_h", "label": "U H", "fieldtype": "Float", "width": 60},
    {"fieldname": "panel_w", "label": "Panel W", "fieldtype": "Float", "width": 70},
    {"fieldname": "panel_h", "label": "Panel H", "fieldtype": "Float", "width": 70},
]

for comp in sorted_comps:
    lbl = comp_labels.get(comp, comp)
    columns.append({"fieldname": "store_" + comp.replace(" ", "_").lower(), "label": lbl, "fieldtype": "Data", "width": 120})

data = []
for item in items:
    row = {
        "material_request": item.material_request,
        "transaction_date": str(item.transaction_date),
        "workflow_state": item.workflow_state,
        "customer_name": item.customer_name,
        "item_code": item.item_code,
        "item_name": item.item_name,
        "qty": item.qty,
        "cutting_template": item.cutting_template,
        "leaf_w": item.leaf_w,
        "leaf_h": item.leaf_h,
        "u_w": item.u_w,
        "u_h": item.u_h,
        "panel_w": item.panel_w,
        "panel_h": item.panel_h,
    }
    if item.store_data:
        stores = parse_store_data(item.store_data)
        if isinstance(stores, list):
            for s in stores:
                c = s.get("component", "")
                if c:
                    fname = "store_" + c.replace(" ", "_").lower()
                    total_qty = (s.get("qty", 0) or 0) * (item.qty or 1)
                    row[fname] = str(s.get("item", "") or "") + " x " + str(round(total_qty, 1))
    data.append(row)

frappe.response["message"] = {"columns": columns, "data": data}
