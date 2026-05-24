sales_order = (frappe.form_dict.get("sales_order") or "").strip()

if not sales_order:
    frappe.throw("اسم أمر البيع مطلوب")

so = frappe.get_doc("Sales Order", sales_order)

if so.docstatus != 1:
    frappe.throw("يمكن إنشاء طلب المواد من أمر بيع معتمد فقط")

template_name = frappe.db.get_value(
    "Material Request",
    {"sales_order": sales_order, "docstatus": 1},
    "name",
    order_by="creation desc"
)

template_doc = frappe.get_doc("Material Request", template_name) if template_name else None

so_items = frappe.get_all(
    "Sales Order Item",
    filters={"parent": sales_order},
    fields=[
        "item_code",
        "item_name",
        "description",
        "item_group",
        "qty",
        "warehouse",
        "uom",
        "stock_uom",
        "conversion_factor",
        "rate",
        "cost_center",
    ],
    order_by="idx asc",
    ignore_permissions=True
)

item_totals = {}

for row in so_items:
    item_code = row.item_code
    if item_code not in item_totals:
        item_totals[item_code] = {
            "item_code": item_code,
            "item_name": row.item_name,
            "description": row.description,
            "item_group": row.item_group,
            "qty": 0.0,
            "warehouse": row.warehouse,
            "uom": row.uom,
            "stock_uom": row.stock_uom,
            "conversion_factor": frappe.utils.flt(row.conversion_factor or 1),
            "rate": frappe.utils.flt(row.rate),
            "cost_center": row.cost_center,
        }
    item_totals[item_code]["qty"] = item_totals[item_code]["qty"] + frappe.utils.flt(row.qty)

existing_mrs = frappe.get_all(
    "Material Request",
    filters={"sales_order": sales_order, "docstatus": 1},
    pluck="name",
    ignore_permissions=True
)

existing_qty_map = {}

if existing_mrs:
    existing_items = frappe.get_all(
        "Material Request Item",
        filters={"parent": ["in", existing_mrs]},
        fields=["item_code", "qty"],
        ignore_permissions=True
    )
    for row in existing_items:
        item_code = row.item_code
        existing_qty_map[item_code] = existing_qty_map.get(item_code, 0.0) + frappe.utils.flt(row.qty)

remaining_items = []

for item_code, row in item_totals.items():
    remaining_qty = frappe.utils.flt(row["qty"]) - frappe.utils.flt(existing_qty_map.get(item_code, 0))
    remaining_qty = frappe.utils.flt(remaining_qty, 3)
    if remaining_qty > 0.001:
        remaining_row = dict(row)
        remaining_row["qty"] = remaining_qty
        remaining_items.append(remaining_row)

if not remaining_items:
    frappe.throw("لا توجد كميات متبقية في أمر البيع بعد احتساب طلبات المواد السابقة")

mr = frappe.new_doc("Material Request")

def set_if_empty(fieldname, value):
    if value not in [None, ""] and not mr.get(fieldname):
        mr.set(fieldname, value)

if template_doc:
    for fieldname in [
        "material_request_type",
        "custom_request_kind",
        "company",
        "schedule_date",
        "transaction_date",
        "delivery_date",
        "buying_price_list",
        "territory",
        "customer_name",
        "title",
        "custom_district",
        "custom_project_name",
        "custom_latitude",
        "custom_longitude",
        "letter_head",
        "الفرع",
    ]:
        set_if_empty(fieldname, template_doc.get(fieldname))

set_if_empty("sales_order", so.name)
set_if_empty("company", so.company)
set_if_empty("customer_name", so.customer_name)
set_if_empty("territory", so.territory)
set_if_empty("delivery_date", so.delivery_date)
set_if_empty("schedule_date", so.delivery_date or frappe.utils.nowdate())
set_if_empty("transaction_date", frappe.utils.nowdate())
set_if_empty("material_request_type", "Purchase")
set_if_empty("custom_request_kind", "تصنيع")
set_if_empty("title", "Customer")
set_if_empty("buying_price_list", "Standard Buying")
set_if_empty("custom_project_name", so.get("custom_project_name"))
set_if_empty("الفرع", so.get("branch"))
set_if_empty("letter_head", "فراغ")

for row in remaining_items:
    mr.append("items", {
        "item_code": row["item_code"],
        "item_name": row["item_name"],
        "description": row["description"] or row["item_name"] or row["item_code"],
        "item_group": row["item_group"],
        "qty": row["qty"],
        "schedule_date": mr.get("schedule_date"),
        "uom": row["uom"] or row["stock_uom"] or "Unit",
        "stock_uom": row["stock_uom"] or row["uom"] or "Unit",
        "conversion_factor": row["conversion_factor"] or 1,
        "warehouse": row["warehouse"],
        "cost_center": row["cost_center"],
        "rate": row["rate"] or 0,
    })

mr.flags.ignore_permissions = True
mr.insert(ignore_permissions=True, ignore_mandatory=True)

frappe.response["message"] = {
    "name": mr.name,
    "items_count": len(mr.items),
    "items": [{"item_code": row.item_code, "qty": row.qty} for row in mr.items],
}
