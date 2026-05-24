# جلب اسم طلب المواد من المعاملات المرسلة
mr_name = frappe.form_dict.get("mr_name")

if not mr_name:
    frappe.response['message'] = {"entries": [], "items": []}
else:
    # 1. جلب رؤوس السندات المخزنية المرتبطة
    entries = frappe.get_all("Stock Entry",
        filters={
            "custom_material_request": mr_name,
            "docstatus": ["<", 2] # جلب المسودات والمرحلة (استبعاد الملغاة)
        },
        fields=["name", "stock_entry_type", "posting_date", "docstatus"],
        order_by="creation desc"
    )

    if not entries:
        frappe.response['message'] = {"entries": [], "items": []}
    else:
        # استخراج قائمة بأسماء السندات
        se_names = [d.name for d in entries]

        # 2. جلب تفاصيل الأصناف (مع المستودعات لتحديد اتجاه الحركة)
        items = frappe.get_all("Stock Entry Detail",
            filters={"parent": ["in", se_names]},
            fields=["parent", "item_code", "qty", "uom", "s_warehouse", "t_warehouse"]
        )

        # إرجاع النتيجة
        frappe.response['message'] = {
            "entries": entries,
            "items": items
        }
