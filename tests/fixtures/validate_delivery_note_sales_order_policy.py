# Server Script (DocType Event)
# DocType: Delivery Note
# Event: Before Save


def clean_text(value):
    return (value or "").strip()


def add_qty(target, item_code, qty):
    target[item_code] = target.get(item_code, 0) + frappe.utils.flt(qty)


material_request = clean_text(doc.get("custom_material_request"))
if not material_request:
    frappe.throw(
        title="طلب المواد مطلوب",
        msg=(
            "<div dir='rtl' style='text-align:right'>"
            "لا يمكن حفظ سند التسليم بدون طلب مواد مرتبط."
            "</div>"
        ),
    )

mr_data = frappe.db.get_value(
    "Material Request",
    material_request,
    ["docstatus", "custom_request_scenario", "sales_order", "company"],
    as_dict=True,
) or {}

if not mr_data:
    frappe.throw(
        title="طلب المواد غير موجود",
        msg=(
            "<div dir='rtl' style='text-align:right'>"
            "طلب المواد المرتبط غير موجود."
            "</div>"
        ),
    )

if frappe.utils.cint(mr_data.get("docstatus")) != 1:
    frappe.throw(
        title="طلب المواد غير معتمد",
        msg=(
            "<div dir='rtl' style='text-align:right'>"
            "يجب اعتماد طلب المواد قبل إنشاء سند التسليم."
            "</div>"
        ),
    )

scenario = clean_text(mr_data.get("custom_request_scenario"))
link_to_sales_order = 1
if scenario and frappe.db.exists("DocType", "Material Request Scenario Rule"):
    configured_value = frappe.db.get_value(
        "Material Request Scenario Rule",
        {"scenario_name": scenario, "enabled": 1},
        "link_delivery_note_to_sales_order",
    )
    if configured_value is not None:
        link_to_sales_order = frappe.utils.cint(configured_value)

errors = []

if link_to_sales_order:
    for row in doc.get("items") or []:
        if not clean_text(row.get("against_sales_order")) or not clean_text(
            row.get("so_detail")
        ):
            errors.append(
                f"السطر {row.idx or '-'}: يجب ربط الصنف "
                f"{clean_text(row.get('item_code')) or '-'} بأمر البيع وسطره."
            )
else:
    reference_sales_order = clean_text(mr_data.get("sales_order"))
    if not reference_sales_order:
        errors.append("طلب المواد لا يحتوي أمر بيع مرجعيًا.")
    else:
        so_data = frappe.db.get_value(
            "Sales Order",
            reference_sales_order,
            ["docstatus", "customer", "company"],
            as_dict=True,
        ) or {}
        if frappe.utils.cint(so_data.get("docstatus")) != 1:
            errors.append("أمر البيع المرجعي غير معتمد.")
        if clean_text(doc.get("customer")) != clean_text(so_data.get("customer")):
            errors.append("عميل سند التسليم لا يطابق عميل أمر البيع المرجعي.")
        if clean_text(doc.get("company")) != clean_text(so_data.get("company")):
            errors.append("شركة سند التسليم لا تطابق شركة أمر البيع المرجعي.")

    requested_qty = {}
    requested_rows = frappe.db.sql(
        """
        SELECT
            item_code,
            SUM(qty * IFNULL(NULLIF(conversion_factor, 0), 1)) AS stock_qty
        FROM `tabMaterial Request Item`
        WHERE parent = %s
        GROUP BY item_code
        """,
        (material_request,),
        as_dict=True,
    )
    for row in requested_rows:
        add_qty(requested_qty, clean_text(row.item_code), row.stock_qty)

    delivered_qty = {}
    delivered_rows = frappe.db.sql(
        """
        SELECT
            item.item_code,
            SUM(item.qty * IFNULL(NULLIF(item.conversion_factor, 0), 1)) AS stock_qty
        FROM `tabDelivery Note Item` item
        INNER JOIN `tabDelivery Note` parent ON parent.name = item.parent
        WHERE parent.custom_material_request = %s
          AND parent.docstatus = 1
          AND parent.name != %s
        GROUP BY item.item_code
        """,
        (material_request, clean_text(doc.get("name"))),
        as_dict=True,
    )
    for row in delivered_rows:
        add_qty(delivered_qty, clean_text(row.item_code), row.stock_qty)

    current_qty = {}
    for row in doc.get("items") or []:
        item_code = clean_text(row.get("item_code"))
        if row.get("against_sales_order") or row.get("so_detail"):
            errors.append(
                f"السطر {row.idx or '-'}: سند {scenario or 'الاستبدال أو النواقص'} "
                "لا يجب ربطه بأمر البيع على مستوى الصنف."
            )
        conversion_factor = frappe.utils.flt(row.get("conversion_factor")) or 1
        stock_qty = frappe.utils.flt(row.get("qty")) * conversion_factor
        if stock_qty <= 0:
            errors.append(
                f"السطر {row.idx or '-'}: كمية الصنف {item_code or '-'} "
                "يجب أن تكون أكبر من صفر."
            )
        add_qty(current_qty, item_code, stock_qty)

    for item_code in current_qty:
        requested = requested_qty.get(item_code, 0)
        delivered = delivered_qty.get(item_code, 0)
        remaining = requested - delivered
        if requested <= 0:
            errors.append(f"الصنف {item_code or '-'} غير موجود في طلب المواد.")
        elif current_qty[item_code] > remaining + 0.000001:
            errors.append(
                f"الصنف {item_code or '-'}: الكمية الحالية "
                f"{frappe.utils.flt(current_qty[item_code])} تتجاوز المتبقي "
                f"{frappe.utils.flt(remaining)} في طلب المواد."
            )

if errors:
    rows = "".join(["<li>" + error + "</li>" for error in errors])
    frappe.throw(
        title="تعذر حفظ سند التسليم",
        msg=(
            "<div dir='rtl' style='text-align:right'>"
            "<p>تحقق من البيانات التالية:</p>"
            "<ul>" + rows + "</ul>"
            "</div>"
        ),
    )
