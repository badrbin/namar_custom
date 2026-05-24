# Server Script (API)
# API Method: get_sales_dashboard

sales_order_name = frappe.form_dict.get("sales_order")

if not sales_order_name:
    frappe.response["message"] = ""
else:
    # 1) بيانات أمر البيع
    so_data = frappe.db.sql("""
        SELECT name, rounded_total, grand_total, currency, customer, company
        FROM `tabSales Order`
        WHERE name = %s
    """, (sales_order_name,), as_dict=True)

    if not so_data:
        frappe.response["message"] = ""
    else:
        so = so_data[0]
        order_total = so.rounded_total or so.grand_total or 0
        currency = so.currency

        def fmt(amount):
            if amount is None:
                return ""
            return frappe.utils.fmt_money(amount, currency=currency)

        # جلب رصيد حساب العميل
        customer_balance_data = frappe.db.sql("""
            SELECT SUM(debit - credit) as balance
            FROM `tabGL Entry`
            WHERE party_type = 'Customer'
            AND party = %s
            AND is_cancelled = 0
        """, (so.customer,), as_dict=True)
        customer_balance = frappe.utils.flt(customer_balance_data[0].balance) if customer_balance_data else 0
        customer_balance_indicator = "red" if customer_balance > 0 else "green"
        customer_name = frappe.db.get_value('Customer', so.customer, 'customer_name') or so.customer

        # 2) جلب الحركات
        transactions = []

        # 2.a) الفواتير والمرتجعات
        invoices_list = frappe.db.sql("""
            SELECT DISTINCT si.name, si.posting_date, si.grand_total, si.rounded_total,
                   si.is_return, si.return_against
            FROM `tabSales Invoice` si
            INNER JOIN `tabSales Invoice Item` sii ON si.name = sii.parent
            WHERE sii.sales_order = %s
            AND si.docstatus = 1
            ORDER BY si.posting_date ASC
        """, (sales_order_name,), as_dict=True)

        total_invoiced = 0.0
        total_returned = 0.0
        inv_names = []

        for d in invoices_list:
            if d.is_return:
                amt = abs(d.rounded_total or d.grand_total or 0)
                total_returned = total_returned + amt
                original_inv = d.return_against or ""
                label = "مرتجع مبيعات (" + original_inv + ")"
                inv_names.append(d.name)
                transactions.append({
                    "date": d.posting_date,
                    "type": "Sales Invoice",
                    "ref": d.name,
                    "debit": 0,
                    "credit": amt,
                    "label": label
                })
            else:
                amt = abs(d.rounded_total or d.grand_total or 0)
                total_invoiced = total_invoiced + amt
                inv_names.append(d.name)
                label = "فاتورة مبيعات (" + d.name + ")"
                transactions.append({
                    "date": d.posting_date,
                    "type": "Sales Invoice",
                    "ref": d.name,
                    "debit": amt,
                    "credit": 0,
                    "label": label
                })

        # 2.b) سندات القبض
        payments = frappe.db.sql("""
            SELECT pe.name, pe.posting_date, per.allocated_amount,
                   per.reference_doctype, per.reference_name
            FROM `tabPayment Entry Reference` per
            INNER JOIN `tabPayment Entry` pe ON pe.name = per.parent
            WHERE pe.docstatus = 1
            AND (
                (per.reference_doctype = 'Sales Order' AND per.reference_name = %s)
                OR (per.reference_doctype = 'Sales Invoice' AND per.reference_name IN %s)
            )
        """, (sales_order_name, inv_names if inv_names else [""]), as_dict=True)

        total_collected_pe = 0.0
        for p in payments:
            amt = frappe.utils.flt(p.allocated_amount)
            if amt >= 0:
                # سند قبض عادي (دائن)
                total_collected_pe = total_collected_pe + amt
                label = "سند قبض (" + p.reference_name + ")"
                transactions.append({
                    "date": p.posting_date,
                    "type": "Payment Entry",
                    "ref": p.name,
                    "debit": 0,
                    "credit": amt,
                    "label": label
                })
            else:
                # سند مرتبط بمرتجع (المبلغ سالب) - يُعرض كمدين
                abs_amt = abs(amt)
                total_collected_pe = total_collected_pe - abs_amt
                label = "سند مرتجع (" + p.reference_name + ")"
                transactions.append({
                    "date": p.posting_date,
                    "type": "Payment Entry",
                    "ref": p.name,
                    "debit": abs_amt,
                    "credit": 0,
                    "label": label
                })

        # 2.c) قيود اليومية
        je_res = frappe.db.sql("""
            SELECT je.name, je.posting_date,
                   (IFNULL(jea.credit_in_account_currency, 0) - IFNULL(jea.debit_in_account_currency, 0)) as amt,
                   jea.reference_name
            FROM `tabJournal Entry Account` jea
            INNER JOIN `tabJournal Entry` je ON je.name = jea.parent
            WHERE je.docstatus = 1
            AND jea.party = %s
            AND (
                (jea.reference_type = 'Sales Order' AND jea.reference_name = %s)
                OR (jea.reference_type = 'Sales Invoice' AND jea.reference_name IN %s)
            )
        """, (so.customer, sales_order_name, inv_names if inv_names else [""]), as_dict=True)

        total_collected_je = 0.0
        for j in je_res:
            val = abs(float(j.amt or 0))
            total_collected_je = total_collected_je + val
            label = "قيد يومية (" + (j.reference_name or "") + ")"
            transactions.append({
                "date": j.posting_date,
                "type": "Journal Entry",
                "ref": j.name,
                "debit": 0,
                "credit": val,
                "label": label
            })

        # ترتيب حسب التاريخ
        transactions.sort(key=lambda x: str(x['date']))

        # 3) الإجماليات
        net_invoiced = total_invoiced - total_returned
        total_collected = total_collected_pe + total_collected_je
        balance = total_collected - order_total
        balance_indicator = "green" if balance >= 0 else "red"

        # 4) بناء صفوف الجدول
        rows_html = ""
        for t in transactions:
            doctype_url = t['type'].lower().replace(" ", "-")
            rows_html = rows_html + '<tr>'
            rows_html = rows_html + '<td style="padding: 8px 12px; border-bottom: 1px solid var(--border-color);">' + frappe.utils.formatdate(t['date']) + '</td>'
            rows_html = rows_html + '<td style="padding: 8px 12px; border-bottom: 1px solid var(--border-color);">' + t['label'] + '</td>'
            rows_html = rows_html + '<td style="padding: 8px 12px; border-bottom: 1px solid var(--border-color);">'
            rows_html = rows_html + '<a href="/app/' + doctype_url + '/' + t['ref'] + '" target="_blank" class="print-hide">' + t['ref'] + '</a>'
            rows_html = rows_html + '<span class="visible-print">' + t['ref'] + '</span>'
            rows_html = rows_html + '</td>'
            rows_html = rows_html + '<td style="padding: 8px 12px; border-bottom: 1px solid var(--border-color); text-align: right; color: var(--red-600);">' + (fmt(t['debit']) if t['debit'] else "") + '</td>'
            rows_html = rows_html + '<td style="padding: 8px 12px; border-bottom: 1px solid var(--border-color); text-align: right; color: var(--green-600);">' + (fmt(t['credit']) if t['credit'] else "") + '</td>'
            rows_html = rows_html + '</tr>'

        if not rows_html:
            rows_html = '<tr><td colspan="5" style="text-align:center; padding: 15px; color: var(--text-muted);">لا توجد حركات مالية مرتبطة</td></tr>'

        # 5) بناء HTML
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
        function printSOStatement() {
            var el = document.getElementById("so_statement_box");
            var w = window.open("", "_blank");
            w.document.write("<html><head><title>كشف حساب أمر البيع</title>");
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

        html = html + '<div id="so_statement_box" class="form-dashboard-section" style="border: 1px solid var(--border-color); border-radius: var(--border-radius); background-color: var(--card-bg);">'

        # العنوان
        html = html + '<div style="padding: 12px 15px; border-bottom: 1px solid var(--border-color); display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;">'
        html = html + '<div style="font-weight: 600; font-size: var(--text-md);">\U0001f4ca كشف حساب أمر البيع: ' + so.name + '</div>'
        html = html + '<div style="display: flex; flex-direction: column; align-items: flex-end; gap: 6px; flex-shrink: 0;">'
        html = html + '<button class="btn btn-xs btn-default print-hide" onclick="printSOStatement()"><i class="fa fa-print"></i> طباعة الكشف</button>'
        html = html + '<div class="indicator-pill ' + balance_indicator + '" style="white-space: nowrap;">رصيد أمر البيع: ' + fmt(abs(balance)) + '</div>'
        html = html + '<div class="indicator-pill ' + customer_balance_indicator + '" style="white-space: nowrap;">رصيد كشف الحساب: ' + fmt(abs(customer_balance)) + '</div>'
        html = html + '</div></div>'

        # بطاقات الملخص
        html = html + '<div style="padding: 15px;">'
        html = html + '<div style="display: flex; gap: 12px; margin-bottom: 18px; flex-wrap: wrap;">'

        # بطاقة إجمالي أمر البيع
        html = html + '<div style="flex: 1; min-width: 120px; border: 1px solid var(--border-color); border-radius: var(--border-radius); overflow: hidden;">'
        html = html + '<div style="background: var(--bg-light-gray); padding: 6px 12px; font-size: 11px; color: var(--text-muted); font-weight: 600; border-bottom: 1px solid var(--border-color);">إجمالي أمر البيع</div>'
        html = html + '<div style="padding: 10px 12px; font-size: 16px; font-weight: 600;">' + fmt(order_total) + '</div>'
        html = html + '</div>'

        # بطاقة المفوتر
        html = html + '<div style="flex: 1; min-width: 120px; border: 1px solid var(--border-color); border-radius: var(--border-radius); overflow: hidden;">'
        html = html + '<div style="background: var(--bg-light-gray); padding: 6px 12px; font-size: 11px; color: var(--text-muted); font-weight: 600; border-bottom: 1px solid var(--border-color);">المفوتر</div>'
        html = html + '<div style="padding: 10px 12px; font-size: 16px; font-weight: 600; color: var(--red-600);">' + fmt(net_invoiced) + '</div>'
        html = html + '</div>'

        # بطاقة المرتجعات
        html = html + '<div style="flex: 1; min-width: 120px; border: 1px solid var(--border-color); border-radius: var(--border-radius); overflow: hidden;">'
        html = html + '<div style="background: var(--bg-light-gray); padding: 6px 12px; font-size: 11px; color: var(--text-muted); font-weight: 600; border-bottom: 1px solid var(--border-color);">المرتجعات</div>'
        html = html + '<div style="padding: 10px 12px; font-size: 16px; font-weight: 600; color: var(--orange-600);">' + fmt(total_returned) + '</div>'
        html = html + '</div>'

        # بطاقة المقبوضات
        html = html + '<div style="flex: 1; min-width: 120px; border: 1px solid var(--border-color); border-radius: var(--border-radius); overflow: hidden;">'
        html = html + '<div style="background: var(--bg-light-gray); padding: 6px 12px; font-size: 11px; color: var(--text-muted); font-weight: 600; border-bottom: 1px solid var(--border-color);">المقبوضات</div>'
        html = html + '<div style="padding: 10px 12px; font-size: 16px; font-weight: 600; color: var(--green-600);">' + fmt(total_collected) + '</div>'
        html = html + '</div>'

        html = html + '</div>'

        # جدول الحركات
        html = html + '<div style="margin-bottom: 8px; font-weight: 600; font-size: 11px; color: var(--text-muted); text-transform: uppercase;">تفاصيل الحركات</div>'

        html = html + '<div class="scroll-box" style="max-height: 400px; overflow-y: auto; border: 1px solid var(--border-color); border-radius: var(--border-radius);">'
        html = html + '<table style="width: 100%; border-collapse: collapse; font-size: var(--text-sm); margin-bottom: 0;">'

        # رؤوس الجدول
        html = html + '<thead style="position: sticky; top: 0; z-index: 1;">'
        html = html + '<tr style="background: var(--bg-light-gray);">'
        html = html + '<th style="padding: 8px 12px; text-align: right; font-weight: 600; font-size: 11px; color: var(--text-muted); border-bottom: 1px solid var(--border-color); width: 15%;">التاريخ</th>'
        html = html + '<th style="padding: 8px 12px; text-align: right; font-weight: 600; font-size: 11px; color: var(--text-muted); border-bottom: 1px solid var(--border-color); width: 35%;">النوع (المرجع المربوط)</th>'
        html = html + '<th style="padding: 8px 12px; text-align: right; font-weight: 600; font-size: 11px; color: var(--text-muted); border-bottom: 1px solid var(--border-color); width: 20%;">رقم الحركة</th>'
        html = html + '<th style="padding: 8px 12px; text-align: right; font-weight: 600; font-size: 11px; color: var(--text-muted); border-bottom: 1px solid var(--border-color); width: 15%;">مدين (+)</th>'
        html = html + '<th style="padding: 8px 12px; text-align: right; font-weight: 600; font-size: 11px; color: var(--text-muted); border-bottom: 1px solid var(--border-color); width: 15%;">دائن (-)</th>'
        html = html + '</tr></thead>'

        html = html + '<tbody>' + rows_html + '</tbody>'
        html = html + '</table></div>'

        html = html + '</div></div>'

        frappe.response["message"] = html
