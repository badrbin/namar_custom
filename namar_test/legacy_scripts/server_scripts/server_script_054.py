# Server Script (API)
# API Method: get_customer_summary

customer = frappe.form_dict.get('customer')

if customer:
    customer_name = frappe.db.get_value('Customer', customer, 'customer_name') or customer

    # 1. تجميع البيانات المالية الإجمالية للعميل
    gl_aggregates = frappe.db.sql("""
        SELECT
            SUM(CASE WHEN voucher_type = 'Sales Invoice' THEN debit - credit ELSE 0 END) as total_invoiced,
            SUM(CASE WHEN voucher_type != 'Sales Invoice' THEN credit - debit ELSE 0 END) as total_paid,
            SUM(debit - credit) as balance
        FROM `tabGL Entry`
        WHERE party_type = 'Customer'
        AND party = %s
        AND is_cancelled = 0
    """, (customer,), as_dict=True)

    # 1.b جلب إجمالي المرتجعات
    returns_data = frappe.db.sql("""
        SELECT IFNULL(SUM(ABS(IFNULL(rounded_total, grand_total))), 0) as total_returned
        FROM `tabSales Invoice`
        WHERE customer = %s
        AND is_return = 1
        AND docstatus = 1
    """, (customer,), as_dict=True)
    total_returned = frappe.utils.flt(returns_data[0].total_returned) if returns_data else 0

    # 2. جلب تفاصيل الحركات
    gl_entries_raw = frappe.db.sql("""
        SELECT posting_date, voucher_type, voucher_no, debit, credit, remarks
        FROM `tabGL Entry`
        WHERE party_type = 'Customer'
        AND party = %s
        AND is_cancelled = 0
        ORDER BY posting_date DESC, creation DESC
    """, (customer,), as_dict=True)

    # 3. معالجة الحركات لربط السندات بالفواتير
    # خريطة ترجمة أنواع السندات للعربية
    type_labels = {
        'Sales Invoice': 'فاتورة مبيعات',
        'Payment Entry': 'سند قبض',
        'Journal Entry': 'قيد يومية',
        'Delivery Note': 'سند تسليم',
        'Sales Order': 'أمر بيع'
    }

    gl_entries = []
    for entry in gl_entries_raw:
        v_type = entry.get("voucher_type") or ""
        v_no = entry.get("voucher_no") or ""
        display_ref = v_no

        # تحديد الاسم العربي
        ar_label = type_labels.get(v_type, v_type)

        # تمييز المرتجعات (فاتورة مبيعات بجانب دائن = مرتجع)
        if v_type == "Sales Invoice" and entry.get('credit', 0) > 0:
            # جلب الفاتورة الأصلية للمرتجع
            return_against = frappe.db.get_value('Sales Invoice', v_no, 'return_against') or ''
            if return_against:
                ar_label = 'مرتجع مبيعات'
                display_ref = return_against

        elif v_type == "Payment Entry":
            references = frappe.db.sql("""
                SELECT reference_name
                FROM `tabPayment Entry Reference`
                WHERE parent = %s
                AND reference_doctype = 'Sales Invoice'
            """, (v_no,), as_dict=True)
            if references:
                display_ref = ", ".join([r.reference_name for r in references])
            # تمييز سند مرتجع (credit = دفع عادي، debit = مرتجع)
            if entry.get('debit', 0) > 0:
                ar_label = 'سند مرتجع'

        elif v_type == "Journal Entry":
            references = frappe.db.sql("""
                SELECT reference_name
                FROM `tabJournal Entry Account`
                WHERE parent = %s
                AND reference_type = 'Sales Invoice'
                AND party = %s
            """, (v_no, customer), as_dict=True)
            if references:
                display_ref = ", ".join(list(set([r.reference_name for r in references if r.reference_name])))

        entry["voucher_type_display"] = ar_label + " (" + display_ref + ")"
        gl_entries.append(entry)

    # 4. تنسيق الأرقام
    res = gl_aggregates[0] if gl_aggregates else {"total_invoiced": 0, "total_paid": 0, "balance": 0}
    total_invoiced = frappe.utils.flt(res.get("total_invoiced", 0))
    total_paid = frappe.utils.flt(res.get("total_paid", 0))
    balance = frappe.utils.flt(res.get("balance", 0))

    currency = frappe.db.get_value("Customer", customer, "default_currency") or "SAR"

    def fmt(val):
        return frappe.utils.fmt_money(val, currency=currency)

    # 5. بناء صفوف الجدول
    rows_html = ""
    running_balance = balance
    for entry in gl_entries:
        debit_str = ""
        credit_str = ""
        if entry['debit'] > 0:
            debit_str = fmt(entry['debit'])
        if entry['credit'] > 0:
            credit_str = fmt(entry['credit'])

        row_balance = running_balance
        running_balance = running_balance - entry['debit'] + entry['credit']

        row_bal_color = "var(--red-600)" if row_balance > 0 else "var(--green-600)"
        doctype_url = entry['voucher_type'].lower().replace(" ", "-")

        rows_html = rows_html + '<tr>'
        rows_html = rows_html + '<td style="padding: 8px 12px; border-bottom: 1px solid var(--border-color);">' + frappe.utils.formatdate(entry['posting_date']) + '</td>'
        rows_html = rows_html + '<td style="padding: 8px 12px; border-bottom: 1px solid var(--border-color);">' + entry['voucher_type_display'] + '</td>'
        rows_html = rows_html + '<td style="padding: 8px 12px; border-bottom: 1px solid var(--border-color);">'
        rows_html = rows_html + '<a href="/app/' + doctype_url + '/' + entry['voucher_no'] + '" target="_blank" class="print-hide">' + entry['voucher_no'] + '</a>'
        rows_html = rows_html + '<span class="visible-print">' + entry['voucher_no'] + '</span>'
        rows_html = rows_html + '</td>'
        rows_html = rows_html + '<td style="padding: 8px 12px; border-bottom: 1px solid var(--border-color); text-align: right; color: var(--red-600);">' + debit_str + '</td>'
        rows_html = rows_html + '<td style="padding: 8px 12px; border-bottom: 1px solid var(--border-color); text-align: right; color: var(--green-600);">' + credit_str + '</td>'
        rows_html = rows_html + '<td style="padding: 8px 12px; border-bottom: 1px solid var(--border-color); text-align: right; font-weight: bold; color: ' + row_bal_color + ';">' + fmt(abs(row_balance)) + '</td>'
        rows_html = rows_html + '</tr>'

    if not rows_html:
        rows_html = '<tr><td colspan="6" style="text-align:center; padding: 15px; color: var(--text-muted);">لا توجد حركات مالية</td></tr>'

    balance_indicator = "red" if balance > 0 else "green"

    # 6. بناء HTML الكامل
    html = """
    <style>
        .visible-print { display: none; }
        @media print {
            .print-hide { display: none !important; }
            .visible-print { display: inline !important; }
            .scroll-box { max-height: none !important; overflow: visible !important; }
        }
    </style>
    <script>
    function printCustomerStatement() {
        var el = document.getElementById("customer_statement_box");
        var w = window.open("", "_blank");
        w.document.write("<html><head><title>كشف حساب عميل</title>");
        w.document.write("<link rel='stylesheet' href='https://cdn.jsdelivr.net/npm/bootstrap@3.3.7/dist/css/bootstrap.min.css'>");
        w.document.write("<style>:root{--red-600:#e53e3e;--green-600:#38a169;--orange-600:#dd6b20;--text-muted:#8d99a6;--text-color:#36414c;--border-color:#d1d8dd;--bg-light-gray:#f5f7fa;--card-bg:#fff;--text-md:14px;--text-sm:12px;--border-radius:8px}body{direction:rtl;font-family:sans-serif;padding:20px}.print-hide{display:none!important}.visible-print{display:inline!important}.scroll-box{max-height:none!important;overflow:visible!important}table{width:100%;border-collapse:collapse}th,td{border:1px solid #ddd;padding:8px;text-align:right}th{background:#f5f5f5}.indicator-pill{padding:4px 12px;border-radius:12px;font-size:12px;font-weight:600;display:inline-block}.indicator-pill.red{background:#fff5f5;color:#e53e3e}.indicator-pill.green{background:#f0fff4;color:#38a169}</style>");
        w.document.write("</head><body>");
        w.document.write(el.innerHTML);
        w.document.write("</body></html>");
        w.document.close();
        setTimeout(function(){ w.print(); w.close(); }, 500);
    }
    </script>
    """

    html = html + '<div id="customer_statement_box" class="form-dashboard-section" style="border: 1px solid var(--border-color); border-radius: var(--border-radius); background-color: var(--card-bg);">'

    # === العنوان الرئيسي ===
    html = html + '<div style="padding: 12px 15px; border-bottom: 1px solid var(--border-color); display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;">'
    html = html + '<div style="font-weight: 600; font-size: var(--text-md);">\U0001f4ca كشف حساب العميل: ' + customer_name + '</div>'
    html = html + '<div style="display: flex; align-items: center; gap: 10px; flex-shrink: 0;">'
    html = html + '<button class="btn btn-xs btn-default print-hide" onclick="printCustomerStatement()"><i class="fa fa-print"></i> طباعة الكشف</button>'
    html = html + '<div class="indicator-pill ' + balance_indicator + '" style="white-space: nowrap;">الرصيد الحالي: ' + fmt(abs(balance)) + '</div>'
    html = html + '</div></div>'

    # === بطاقات الملخص ===
    html = html + '<div style="padding: 15px;">'
    html = html + '<div style="display: flex; gap: 12px; margin-bottom: 18px; flex-wrap: wrap;">'

    # بطاقة الرصيد
    html = html + '<div style="flex: 1; min-width: 140px; border: 1px solid var(--border-color); border-radius: var(--border-radius); overflow: hidden;">'
    html = html + '<div style="background: var(--bg-light-gray); padding: 6px 12px; font-size: 11px; color: var(--text-muted); font-weight: 600; border-bottom: 1px solid var(--border-color);">الرصيد الحالي</div>'
    balance_val_color = "var(--red-600)" if balance > 0 else "var(--green-600)"
    html = html + '<div style="padding: 10px 12px; font-size: 18px; font-weight: 700; color: ' + balance_val_color + ';">' + fmt(abs(balance)) + '</div>'
    html = html + '</div>'

    # بطاقة المفوتر
    html = html + '<div style="flex: 1; min-width: 140px; border: 1px solid var(--border-color); border-radius: var(--border-radius); overflow: hidden;">'
    html = html + '<div style="background: var(--bg-light-gray); padding: 6px 12px; font-size: 11px; color: var(--text-muted); font-weight: 600; border-bottom: 1px solid var(--border-color);">إجمالي المفوتر</div>'
    html = html + '<div style="padding: 10px 12px; font-size: 16px; font-weight: 600; color: var(--red-600);">' + fmt(total_invoiced) + '</div>'
    html = html + '</div>'

    # بطاقة المحصل
    html = html + '<div style="flex: 1; min-width: 140px; border: 1px solid var(--border-color); border-radius: var(--border-radius); overflow: hidden;">'
    html = html + '<div style="background: var(--bg-light-gray); padding: 6px 12px; font-size: 11px; color: var(--text-muted); font-weight: 600; border-bottom: 1px solid var(--border-color);">إجمالي المحصل</div>'
    html = html + '<div style="padding: 10px 12px; font-size: 16px; font-weight: 600; color: var(--green-600);">' + fmt(total_paid) + '</div>'
    html = html + '</div>'

    # بطاقة المرتجعات
    html = html + '<div style="flex: 1; min-width: 140px; border: 1px solid var(--border-color); border-radius: var(--border-radius); overflow: hidden;">'
    html = html + '<div style="background: var(--bg-light-gray); padding: 6px 12px; font-size: 11px; color: var(--text-muted); font-weight: 600; border-bottom: 1px solid var(--border-color);">المرتجعات</div>'
    html = html + '<div style="padding: 10px 12px; font-size: 16px; font-weight: 600; color: var(--orange-600);">' + fmt(total_returned) + '</div>'
    html = html + '</div>'

    html = html + '</div>'

    # === جدول الحركات ===
    html = html + '<div style="margin-bottom: 8px; font-weight: 600; font-size: 11px; color: var(--text-muted); text-transform: uppercase;">تفاصيل الحركات المالية</div>'

    html = html + '<div class="scroll-box" style="max-height: 400px; overflow-y: auto; border: 1px solid var(--border-color); border-radius: var(--border-radius);">'
    html = html + '<table style="width: 100%; border-collapse: collapse; font-size: var(--text-sm); margin-bottom: 0;">'

    # رؤوس الجدول
    html = html + '<thead style="position: sticky; top: 0; z-index: 1;">'
    html = html + '<tr style="background: var(--bg-light-gray);">'
    html = html + '<th style="padding: 8px 12px; text-align: right; font-weight: 600; font-size: 11px; color: var(--text-muted); border-bottom: 1px solid var(--border-color); width: 12%;">التاريخ</th>'
    html = html + '<th style="padding: 8px 12px; text-align: right; font-weight: 600; font-size: 11px; color: var(--text-muted); border-bottom: 1px solid var(--border-color); width: 30%;">النوع (المرجع المربوط)</th>'
    html = html + '<th style="padding: 8px 12px; text-align: right; font-weight: 600; font-size: 11px; color: var(--text-muted); border-bottom: 1px solid var(--border-color); width: 18%;">رقم الحركة</th>'
    html = html + '<th style="padding: 8px 12px; text-align: right; font-weight: 600; font-size: 11px; color: var(--text-muted); border-bottom: 1px solid var(--border-color); width: 13%;">مدين (+)</th>'
    html = html + '<th style="padding: 8px 12px; text-align: right; font-weight: 600; font-size: 11px; color: var(--text-muted); border-bottom: 1px solid var(--border-color); width: 13%;">دائن (-)</th>'
    html = html + '<th style="padding: 8px 12px; text-align: right; font-weight: 600; font-size: 11px; color: var(--text-muted); border-bottom: 1px solid var(--border-color); width: 14%;">الرصيد</th>'
    html = html + '</tr></thead>'

    html = html + '<tbody>' + rows_html + '</tbody>'
    html = html + '</table></div>'

    html = html + '</div></div>'

    frappe.response['message'] = html
else:
    frappe.response['message'] = "يرجى تزويد اسم العميل"
