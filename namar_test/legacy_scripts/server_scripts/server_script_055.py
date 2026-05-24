# Server Script (API)
# API Method: get_supplier_summary

supplier = frappe.form_dict.get('supplier')

if supplier:
    supplier_name = frappe.db.get_value('Supplier', supplier, 'supplier_name') or supplier

    # 1. تجميع البيانات المالية الإجمالية للمورد
    gl_aggregates = frappe.db.sql("""
        SELECT
            SUM(CASE WHEN voucher_type = 'Purchase Invoice' THEN credit - debit ELSE 0 END) as total_invoiced,
            SUM(CASE WHEN voucher_type != 'Purchase Invoice' THEN debit - credit ELSE 0 END) as total_paid,
            SUM(credit - debit) as balance
        FROM `tabGL Entry`
        WHERE party_type = 'Supplier'
        AND party = %s
        AND is_cancelled = 0
    """, (supplier,), as_dict=True)

    # 1.b جلب إجمالي المرتجعات
    returns_data = frappe.db.sql("""
        SELECT IFNULL(SUM(ABS(IFNULL(rounded_total, grand_total))), 0) as total_returned
        FROM `tabPurchase Invoice`
        WHERE supplier = %s
        AND is_return = 1
        AND docstatus = 1
    """, (supplier,), as_dict=True)
    total_returned = frappe.utils.flt(returns_data[0].total_returned) if returns_data else 0

    # 2. جلب تفاصيل الحركات
    gl_entries_raw = frappe.db.sql("""
        SELECT posting_date, voucher_type, voucher_no, debit, credit, remarks
        FROM `tabGL Entry`
        WHERE party_type = 'Supplier'
        AND party = %s
        AND is_cancelled = 0
        ORDER BY posting_date DESC, creation DESC
    """, (supplier,), as_dict=True)

    # 3. معالجة الحركات لربط السندات بالفواتير
    type_labels = {
        'Purchase Invoice': '\u0641\u0627\u062a\u0648\u0631\u0629 \u0645\u0634\u062a\u0631\u064a\u0627\u062a',
        'Payment Entry': '\u0633\u0646\u062f \u0635\u0631\u0641',
        'Journal Entry': '\u0642\u064a\u062f \u064a\u0648\u0645\u064a\u0629',
        'Purchase Receipt': '\u0625\u064a\u0635\u0627\u0644 \u0634\u0631\u0627\u0621',
        'Purchase Order': '\u0623\u0645\u0631 \u0634\u0631\u0627\u0621'
    }

    gl_entries = []
    for entry in gl_entries_raw:
        v_type = entry.get("voucher_type") or ""
        v_no = entry.get("voucher_no") or ""
        display_ref = v_no

        ar_label = type_labels.get(v_type, v_type)

        # تمييز المرتجعات (فاتورة مشتريات بجانب مدين = مرتجع)
        if v_type == "Purchase Invoice" and entry.get('debit', 0) > 0:
            return_against = frappe.db.get_value('Purchase Invoice', v_no, 'return_against') or ''
            if return_against:
                ar_label = '\u0645\u0631\u062a\u062c\u0639 \u0645\u0634\u062a\u0631\u064a\u0627\u062a'
                display_ref = return_against

        elif v_type == "Payment Entry":
            references = frappe.db.sql("""
                SELECT reference_name
                FROM `tabPayment Entry Reference`
                WHERE parent = %s
                AND reference_doctype = 'Purchase Invoice'
            """, (v_no,), as_dict=True)
            if references:
                display_ref = ", ".join([r.reference_name for r in references])
            # تمييز سند مرتجع (debit = دفع عادي، credit = مرتجع)
            if entry.get('credit', 0) > 0:
                ar_label = '\u0633\u0646\u062f \u0645\u0631\u062a\u062c\u0639'

        elif v_type == "Journal Entry":
            references = frappe.db.sql("""
                SELECT reference_name
                FROM `tabJournal Entry Account`
                WHERE parent = %s
                AND reference_type = 'Purchase Invoice'
                AND party = %s
            """, (v_no, supplier), as_dict=True)
            if references:
                display_ref = ", ".join(list(set([r.reference_name for r in references if r.reference_name])))

        entry["voucher_type_display"] = ar_label + " (" + display_ref + ")"
        gl_entries.append(entry)

    # 4. تنسيق الأرقام
    res = gl_aggregates[0] if gl_aggregates else {"total_invoiced": 0, "total_paid": 0, "balance": 0}
    total_invoiced = frappe.utils.flt(res.get("total_invoiced", 0))
    total_paid = frappe.utils.flt(res.get("total_paid", 0))
    balance = frappe.utils.flt(res.get("balance", 0))

    currency = frappe.db.get_value("Supplier", supplier, "default_currency") or "SAR"

    def fmt(val):
        return frappe.utils.fmt_money(val, currency=currency)

    # 5. بناء صفوف الجدول مع الرصيد المتحرك
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
        running_balance = running_balance - entry['credit'] + entry['debit']

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
        rows_html = '<tr><td colspan="6" style="text-align:center; padding: 15px; color: var(--text-muted);">\u0644\u0627 \u062a\u0648\u062c\u062f \u062d\u0631\u0643\u0627\u062a \u0645\u0627\u0644\u064a\u0629</td></tr>'

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
    function printSupplierStatement() {
        var el = document.getElementById("supplier_statement_box");
        var w = window.open("", "_blank");
        w.document.write("<html><head><title>\u0643\u0634\u0641 \u062d\u0633\u0627\u0628 \u0645\u0648\u0631\u062f</title>");
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

    html = html + '<div id="supplier_statement_box" class="form-dashboard-section" style="border: 1px solid var(--border-color); border-radius: var(--border-radius); background-color: var(--card-bg);">'

    # العنوان
    html = html + '<div style="padding: 12px 15px; border-bottom: 1px solid var(--border-color); display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;">'
    html = html + '<div style="font-weight: 600; font-size: var(--text-md);">\U0001f4ca \u0643\u0634\u0641 \u062d\u0633\u0627\u0628 \u0627\u0644\u0645\u0648\u0631\u062f: ' + supplier_name + '</div>'
    html = html + '<div style="display: flex; align-items: center; gap: 10px; flex-shrink: 0;">'
    html = html + '<button class="btn btn-xs btn-default print-hide" onclick="printSupplierStatement()"><i class="fa fa-print"></i> \u0637\u0628\u0627\u0639\u0629 \u0627\u0644\u0643\u0634\u0641</button>'
    html = html + '<div class="indicator-pill ' + balance_indicator + '" style="white-space: nowrap;">\u0627\u0644\u0631\u0635\u064a\u062f \u0627\u0644\u062d\u0627\u0644\u064a: ' + fmt(abs(balance)) + '</div>'
    html = html + '</div></div>'

    # بطاقات الملخص
    html = html + '<div style="padding: 15px;">'
    html = html + '<div style="display: flex; gap: 12px; margin-bottom: 18px; flex-wrap: wrap;">'

    # بطاقة الرصيد
    html = html + '<div style="flex: 1; min-width: 140px; border: 1px solid var(--border-color); border-radius: var(--border-radius); overflow: hidden;">'
    html = html + '<div style="background: var(--bg-light-gray); padding: 6px 12px; font-size: 11px; color: var(--text-muted); font-weight: 600; border-bottom: 1px solid var(--border-color);">\u0627\u0644\u0631\u0635\u064a\u062f \u0627\u0644\u062d\u0627\u0644\u064a</div>'
    balance_val_color = "var(--red-600)" if balance > 0 else "var(--green-600)"
    html = html + '<div style="padding: 10px 12px; font-size: 18px; font-weight: 700; color: ' + balance_val_color + ';">' + fmt(abs(balance)) + '</div>'
    html = html + '</div>'

    # بطاقة المفوتر
    html = html + '<div style="flex: 1; min-width: 140px; border: 1px solid var(--border-color); border-radius: var(--border-radius); overflow: hidden;">'
    html = html + '<div style="background: var(--bg-light-gray); padding: 6px 12px; font-size: 11px; color: var(--text-muted); font-weight: 600; border-bottom: 1px solid var(--border-color);">\u0625\u062c\u0645\u0627\u0644\u064a \u0627\u0644\u0645\u0641\u0648\u062a\u0631</div>'
    html = html + '<div style="padding: 10px 12px; font-size: 16px; font-weight: 600; color: var(--red-600);">' + fmt(total_invoiced) + '</div>'
    html = html + '</div>'

    # بطاقة المدفوع
    html = html + '<div style="flex: 1; min-width: 140px; border: 1px solid var(--border-color); border-radius: var(--border-radius); overflow: hidden;">'
    html = html + '<div style="background: var(--bg-light-gray); padding: 6px 12px; font-size: 11px; color: var(--text-muted); font-weight: 600; border-bottom: 1px solid var(--border-color);">\u0625\u062c\u0645\u0627\u0644\u064a \u0627\u0644\u0645\u062f\u0641\u0648\u0639</div>'
    html = html + '<div style="padding: 10px 12px; font-size: 16px; font-weight: 600; color: var(--green-600);">' + fmt(total_paid) + '</div>'
    html = html + '</div>'

    # بطاقة المرتجعات
    html = html + '<div style="flex: 1; min-width: 140px; border: 1px solid var(--border-color); border-radius: var(--border-radius); overflow: hidden;">'
    html = html + '<div style="background: var(--bg-light-gray); padding: 6px 12px; font-size: 11px; color: var(--text-muted); font-weight: 600; border-bottom: 1px solid var(--border-color);">\u0627\u0644\u0645\u0631\u062a\u062c\u0639\u0627\u062a</div>'
    html = html + '<div style="padding: 10px 12px; font-size: 16px; font-weight: 600; color: var(--orange-600);">' + fmt(total_returned) + '</div>'
    html = html + '</div>'

    html = html + '</div>'

    # جدول الحركات
    html = html + '<div style="margin-bottom: 8px; font-weight: 600; font-size: 11px; color: var(--text-muted); text-transform: uppercase;">\u062a\u0641\u0627\u0635\u064a\u0644 \u0627\u0644\u062d\u0631\u0643\u0627\u062a \u0627\u0644\u0645\u0627\u0644\u064a\u0629</div>'

    html = html + '<div class="scroll-box" style="max-height: 400px; overflow-y: auto; border: 1px solid var(--border-color); border-radius: var(--border-radius);">'
    html = html + '<table style="width: 100%; border-collapse: collapse; font-size: var(--text-sm); margin-bottom: 0;">'

    html = html + '<thead style="position: sticky; top: 0; z-index: 1;">'
    html = html + '<tr style="background: var(--bg-light-gray);">'
    html = html + '<th style="padding: 8px 12px; text-align: right; font-weight: 600; font-size: 11px; color: var(--text-muted); border-bottom: 1px solid var(--border-color); width: 12%;">\u0627\u0644\u062a\u0627\u0631\u064a\u062e</th>'
    html = html + '<th style="padding: 8px 12px; text-align: right; font-weight: 600; font-size: 11px; color: var(--text-muted); border-bottom: 1px solid var(--border-color); width: 30%;">\u0627\u0644\u0646\u0648\u0639 (\u0627\u0644\u0645\u0631\u062c\u0639 \u0627\u0644\u0645\u0631\u0628\u0648\u0637)</th>'
    html = html + '<th style="padding: 8px 12px; text-align: right; font-weight: 600; font-size: 11px; color: var(--text-muted); border-bottom: 1px solid var(--border-color); width: 18%;">\u0631\u0642\u0645 \u0627\u0644\u062d\u0631\u0643\u0629</th>'
    html = html + '<th style="padding: 8px 12px; text-align: right; font-weight: 600; font-size: 11px; color: var(--text-muted); border-bottom: 1px solid var(--border-color); width: 13%;">\u0645\u062f\u064a\u0646 (+)</th>'
    html = html + '<th style="padding: 8px 12px; text-align: right; font-weight: 600; font-size: 11px; color: var(--text-muted); border-bottom: 1px solid var(--border-color); width: 13%;">\u062f\u0627\u0626\u0646 (-)</th>'
    html = html + '<th style="padding: 8px 12px; text-align: right; font-weight: 600; font-size: 11px; color: var(--text-muted); border-bottom: 1px solid var(--border-color); width: 14%;">\u0627\u0644\u0631\u0635\u064a\u062f</th>'
    html = html + '</tr></thead>'

    html = html + '<tbody>' + rows_html + '</tbody>'
    html = html + '</table></div>'

    html = html + '</div></div>'

    frappe.response['message'] = html
else:
    frappe.response['message'] = "\u064a\u0631\u062c\u0649 \u062a\u0632\u0648\u064a\u062f \u0627\u0633\u0645 \u0627\u0644\u0645\u0648\u0631\u062f"
