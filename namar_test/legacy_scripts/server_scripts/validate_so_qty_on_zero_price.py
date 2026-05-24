# تحديد دقة الحسابات (عادة 3 خانات كافية جداً للمخزون)
precision = 3

for item in doc.items:
    # التحقق مما إذا كان الصنف مرتبطاً بأمر بيع (SO)
    if item.sales_order and item.so_detail:

        # 1. جلب الكمية الأصلية
        so_qty = frappe.utils.flt(frappe.db.get_value("Sales Order Item", item.so_detail, "qty"), precision)

        # 2. جلب الكميات المفوترة سابقاً
        billed_qty_history = frappe.db.sql("""
            SELECT SUM(qty)
            FROM `tabSales Invoice Item`
            WHERE so_detail = %s
            AND docstatus = 1
            AND parent != %s
        """, (item.so_detail, doc.name))

        prev_billed_qty = frappe.utils.flt(billed_qty_history[0][0], precision)

        # 3. حساب الكمية المتاحة المتبقية (مع تنظيف الرقم الناتج)
        # هذا السطر هو الحل السحري: سيحول 0.0099999 إلى 0.010
        remaining_qty = frappe.utils.flt(so_qty - prev_billed_qty, precision)

        # 4. التحقق من الشرط باستخدام هامش سماحية بسيط جداً
        # نقوم بطرح المتبقي من الحالي، إذا كان الناتج أكبر من 0.001 فهذا تجاوز حقيقي
        # (item.qty - remaining_qty) > 0.001

        # أو ببساطة، بما أننا قمنا بـ flt للـ remaining_qty في الخطوة 3، فالمقارنة المباشرة غالباً ستنجح الآن:
        if item.qty > remaining_qty:

            # حساب الفرق للعرض في الرسالة
            overage = item.qty - remaining_qty

            # نتأكد مرة أخرى أن الفرق ليس تافهاً (أقل من 0.0001) قبل رمي الخطأ
            if overage > 0.0001:
                frappe.throw(
                    msg=(
                        f"خطأ في الصنف (Row #{item.idx}): الكمية المدخلة ({item.qty}) تتجاوز الكمية المتبقية في أمر البيع ({remaining_qty}).<br>"
                        f"الفرق المتجاوز: {overage:.4f}"
                    ),
                    title="تجاوز كمية أمر البيع"
                )
