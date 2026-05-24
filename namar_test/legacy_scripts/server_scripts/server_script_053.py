# Server Script (API)
# API Method: get_purchase_dashboard

purchase_order_name = frappe.form_dict.get("purchase_order")

if not purchase_order_name:
    frappe.response["message"] = ""
else:
    # 1) بيانات أمر الشراء
    po_data = frappe.db.sql("""
        SELECT name, rounded_total, grand_total, currency, supplier, company
        FROM `tabPurchase Order`
        WHERE name = %s
    """, (purchase_order_name,), as_dict=True)

    if not po_data:
        frappe.response["message"] = ""
    else:
        po = po_data[0]
        order_total = po.rounded_total or po.grand_total or 0
        currency = po.currency

        def fmt(amount):
            if amount is None:
                return ""
            return frappe.utils.fmt_money(amount, currency=currency)

        # جلب رصيد حساب المورد من دفتر الأستاذ
        supplier_balance_data = frappe.db.sql("""
            SELECT SUM(credit - debit) as balance
            FROM `tabGL Entry`
            WHERE party_type = 'Supplier'
            AND party = %s
            AND is_cancelled = 0
        """, (po.supplier,), as_dict=True)
        supplier_balance = frappe.utils.flt(supplier_balance_data[0].balance) if supplier_balance_data else 0
        supplier_balance_indicator = "red" if supplier_balance > 0 else "green"
        supplier_name = frappe.db.get_value('Supplier', po.supplier, 'supplier_name') or po.supplier

        # 2) جلب الحركات
        transactions = []

        # 2.a) فواتير المشتريات والمرتجعات
        invoices_list = frappe.db.sql("""
            SELECT DISTINCT pi.name, pi.posting_date, pi.grand_total, pi.rounded_total,
                   pi.is_return, pi.return_against
            FROM `tabPurchase Invoice` pi
            INNER JOIN `tabPurchase Invoice Item` pii ON pi.name = pii.parent
            WHERE pii.purchase_order = %s
            AND pi.docstatus = 1
            ORDER BY pi.posting_date ASC
        """, (purchase_order_name,), as_dict=True)

        total_invoiced = 0.0
        total_returned = 0.0
        inv_names = []

        for d in invoices_list:
            if d.is_return:
                amt = abs(d.rounded_total or d.grand_total or 0)
                total_returned = total_returned + amt
                original_inv = d.return_against or ""
                label = "\u0645\u0631\u062a\u062c\u0639 \u0645\u0634\u062a\u0631\u064a\u0627\u062a (" + original_inv + ")"
                inv_names.append(d.name)
                transactions.append({
                    "date": d.posting_date,
                    "type": "Purchase Invoice",
                    "ref": d.name,
                    "debit": amt,
                    "credit": 0,
                    "label": label
                })
            else:
                amt = abs(d.rounded_total or d.grand_total or 0)
                total_invoiced = total_invoiced + amt
                inv_names.append(d.name)
                label = "\u0641\u0627\u062a\u0648\u0631\u0629 \u0645\u0634\u062a\u0631\u064a\u0627\u062a (" + d.name + ")"
                transactions.append({
                    "date": d.posting_date,
                    "type": "Purchase Invoice",
                    "ref": d.name,
                    "debit": 0,
                    "credit": amt,
                    "label": label
                })

        # 2.b) سندات الصرف (المدفوعات)
        payments = frappe.db.sql("""
            SELECT pe.name, pe.posting_date, per.allocated_amount,
                   per.reference_doctype, per.reference_name
            FROM `tabPayment Entry Reference` per
            INNER JOIN `tabPayment Entry` pe ON pe.name = per.parent
            WHERE pe.docstatus = 1
            AND (
                (per.reference_doctype = 'Purchase Order' AND per.reference_name = %s)
                OR (per.reference_doctype = 'Purchase Invoice' AND per.reference_name IN %s)
            )
        """, (purchase_order_name, inv_names if inv_names else [""]), as_dict=True)

        total_paid_pe = 0.0
        for p in payments:
            amt = frappe.utils.flt(p.allocated_amount)
            if amt >= 0:
                total_paid_pe = total_paid_pe + amt
                label = "\u0633\u0646\u062f \u0635\u0631\u0641 (" + p.reference_name + ")"
                transactions.append({
                    "date": p.posting_date,
                    "type": "Payment Entry",
                    "ref": p.name,
                    "debit": amt,
                    "credit": 0,
                    "label": label
                })
            else:
                abs_amt = abs(amt)
                total_paid_pe = total_paid_pe - abs_amt
                label = "\u0633\u0646\u062f \u0645\u0631\u062a\u062c\u0639 (" + p.reference_name + ")"
                transactions.append({
                    "date": p.posting_date,
                    "type": "Payment Entry",
                    "ref": p.name,
                    "debit": 0,
                    "credit": abs_amt,
                    "label": label
                })

        # 2.c) قيود اليومية
        je_res = frappe.db.sql("""
            SELECT je.name, je.posting_date,
                   (IFNULL(jea.debit_in_account_currency, 0) - IFNULL(jea.credit_in_account_currency, 0)) as amt,
                   jea.reference_name
            FROM `tabJournal Entry Account` jea
            INNER JOIN `tabJournal Entry` je ON je.name = jea.parent
            WHERE je.docstatus = 1
            AND jea.party = %s
            AND (
                (jea.reference_type = 'Purchase Order' AND jea.reference_name = %s)
                OR (jea.reference_type = 'Purchase Invoice' AND jea.reference_name IN %s)
            )
        """, (po.supplier, purchase_order_name, inv_names if inv_names else [""]), as_dict=True)

        total_paid_je = 0.0
        for j in je_res:
            val = abs(float(j.amt or 0))
            total_paid_je = total_paid_je + val
            label = "\u0642\u064a\u062f \u064a\u0648\u0645\u064a\u0629 (" + (j.reference_name or "") + ")"
            transactions.append({
                "date": j.posting_date,
                "type": "Journal Entry",
                "ref": j.name,
                "debit": val,
                "credit": 0,
                "label": label
            })

        # ترتيب حسب التاريخ
        transactions.sort(key=lambda x: str(x['date']))

        # 3) الإجماليات
        net_invoiced = total_invoiced - total_returned
        total_paid = total_paid_pe + total_paid_je
        balance = total_paid - order_total
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
            rows_html = '<tr><td colspan="5" style="text-align:center; padding: 15px; color: var(--text-muted);">\u0644\u0627 \u062a\u0648\u062c\u062f \u062d\u0631\u0643\u0627\u062a \u0645\u0627\u0644\u064a\u0629 \u0645\u0631\u062a\u0628\u0637\u0629</td></tr>'

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
        function printPOStatement() {
            var el = document.getElementById("po_statement_box");
            var w = window.open("", "_blank");
            w.document.write("<html><head><title>\u0643\u0634\u0641 \u062d\u0633\u0627\u0628 \u0623\u0645\u0631 \u0627\u0644\u0634\u0631\u0627\u0621</title>");
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

        html = html + '<div id="po_statement_box" class="form-dashboard-section" style="border: 1px solid var(--border-color); border-radius: var(--border-radius); background-color: var(--card-bg);">'

        # العنوان
        html = html + '<div style="padding: 12px 15px; border-bottom: 1px solid var(--border-color); display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;">'
        html = html + '<div style="font-weight: 600; font-size: var(--text-md);">\U0001f4ca \u0643\u0634\u0641 \u062d\u0633\u0627\u0628 \u0623\u0645\u0631 \u0627\u0644\u0634\u0631\u0627\u0621: ' + po.name + '</div>'
        html = html + '<div style="display: flex; flex-direction: column; align-items: flex-end; gap: 6px; flex-shrink: 0;">'
        html = html + '<button class="btn btn-xs btn-default print-hide" onclick="printPOStatement()"><i class="fa fa-print"></i> \u0637\u0628\u0627\u0639\u0629 \u0627\u0644\u0643\u0634\u0641</button>'
        html = html + '<div class="indicator-pill ' + balance_indicator + '" style="white-space: nowrap;">\u0631\u0635\u064a\u062f \u0623\u0645\u0631 \u0627\u0644\u0634\u0631\u0627\u0621: ' + fmt(abs(balance)) + '</div>'
        html = html + '<div class="indicator-pill ' + supplier_balance_indicator + '" style="white-space: nowrap;">\u0631\u0635\u064a\u062f \u0643\u0634\u0641 \u0627\u0644\u062d\u0633\u0627\u0628: ' + fmt(abs(supplier_balance)) + '</div>'
        html = html + '</div></div>'

        # بطاقات الملخص
        html = html + '<div style="padding: 15px;">'
        html = html + '<div style="display: flex; gap: 12px; margin-bottom: 18px; flex-wrap: wrap;">'

        # بطاقة إجمالي أمر الشراء
        html = html + '<div style="flex: 1; min-width: 120px; border: 1px solid var(--border-color); border-radius: var(--border-radius); overflow: hidden;">'
        html = html + '<div style="background: var(--bg-light-gray); padding: 6px 12px; font-size: 11px; color: var(--text-muted); font-weight: 600; border-bottom: 1px solid var(--border-color);">\u0625\u062c\u0645\u0627\u0644\u064a \u0623\u0645\u0631 \u0627\u0644\u0634\u0631\u0627\u0621</div>'
        html = html + '<div style="padding: 10px 12px; font-size: 16px; font-weight: 600;">' + fmt(order_total) + '</div>'
        html = html + '</div>'

        # بطاقة المفوتر
        html = html + '<div style="flex: 1; min-width: 120px; border: 1px solid var(--border-color); border-radius: var(--border-radius); overflow: hidden;">'
        html = html + '<div style="background: var(--bg-light-gray); padding: 6px 12px; font-size: 11px; color: var(--text-muted); font-weight: 600; border-bottom: 1px solid var(--border-color);">\u0627\u0644\u0645\u0641\u0648\u062a\u0631</div>'
        html = html + '<div style="padding: 10px 12px; font-size: 16px; font-weight: 600; color: var(--red-600);">' + fmt(net_invoiced) + '</div>'
        html = html + '</div>'

        # بطاقة المرتجعات
        html = html + '<div style="flex: 1; min-width: 120px; border: 1px solid var(--border-color); border-radius: var(--border-radius); overflow: hidden;">'
        html = html + '<div style="background: var(--bg-light-gray); padding: 6px 12px; font-size: 11px; color: var(--text-muted); font-weight: 600; border-bottom: 1px solid var(--border-color);">\u0627\u0644\u0645\u0631\u062a\u062c\u0639\u0627\u062a</div>'
        html = html + '<div style="padding: 10px 12px; font-size: 16px; font-weight: 600; color: var(--orange-600);">' + fmt(total_returned) + '</div>'
        html = html + '</div>'

        # بطاقة المدفوعات
        html = html + '<div style="flex: 1; min-width: 120px; border: 1px solid var(--border-color); border-radius: var(--border-radius); overflow: hidden;">'
        html = html + '<div style="background: var(--bg-light-gray); padding: 6px 12px; font-size: 11px; color: var(--text-muted); font-weight: 600; border-bottom: 1px solid var(--border-color);">\u0627\u0644\u0645\u062f\u0641\u0648\u0639\u0627\u062a</div>'
        html = html + '<div style="padding: 10px 12px; font-size: 16px; font-weight: 600; color: var(--green-600);">' + fmt(total_paid) + '</div>'
        html = html + '</div>'

        html = html + '</div>'

        # جدول الحركات
        html = html + '<div style="margin-bottom: 8px; font-weight: 600; font-size: 11px; color: var(--text-muted); text-transform: uppercase;">\u062a\u0641\u0627\u0635\u064a\u0644 \u0627\u0644\u062d\u0631\u0643\u0627\u062a</div>'

        html = html + '<div class="scroll-box" style="max-height: 400px; overflow-y: auto; border: 1px solid var(--border-color); border-radius: var(--border-radius);">'
        html = html + '<table style="width: 100%; border-collapse: collapse; font-size: var(--text-sm); margin-bottom: 0;">'

        # رؤوس الجدول
        html = html + '<thead style="position: sticky; top: 0; z-index: 1;">'
        html = html + '<tr style="background: var(--bg-light-gray);">'
        html = html + '<th style="padding: 8px 12px; text-align: right; font-weight: 600; font-size: 11px; color: var(--text-muted); border-bottom: 1px solid var(--border-color); width: 15%;">\u0627\u0644\u062a\u0627\u0631\u064a\u062e</th>'
        html = html + '<th style="padding: 8px 12px; text-align: right; font-weight: 600; font-size: 11px; color: var(--text-muted); border-bottom: 1px solid var(--border-color); width: 35%;">\u0627\u0644\u0646\u0648\u0639 (\u0627\u0644\u0645\u0631\u062c\u0639 \u0627\u0644\u0645\u0631\u0628\u0648\u0637)</th>'
        html = html + '<th style="padding: 8px 12px; text-align: right; font-weight: 600; font-size: 11px; color: var(--text-muted); border-bottom: 1px solid var(--border-color); width: 20%;">\u0631\u0642\u0645 \u0627\u0644\u062d\u0631\u0643\u0629</th>'
        html = html + '<th style="padding: 8px 12px; text-align: right; font-weight: 600; font-size: 11px; color: var(--text-muted); border-bottom: 1px solid var(--border-color); width: 15%;">\u0645\u062f\u064a\u0646 (+)</th>'
        html = html + '<th style="padding: 8px 12px; text-align: right; font-weight: 600; font-size: 11px; color: var(--text-muted); border-bottom: 1px solid var(--border-color); width: 15%;">\u062f\u0627\u0626\u0646 (-)</th>'
        html = html + '</tr></thead>'

        html = html + '<tbody>' + rows_html + '</tbody>'
        html = html + '</table></div>'

        html = html + '</div></div>'

        frappe.response["message"] = html
