/* Auto-generated from live Client Script records on testnamar.u.frappe.cloud. */
(function () {
  if (!window.frappe || !frappe.boot || !frappe.boot.namar_test_client_scripts_enabled) {
    return;
  }
  window.__namar_test_loaded_scripts = window.__namar_test_loaded_scripts || {};
  if (!window.__namar_test_loaded_scripts["Customer Statement - SI"]) {
    window.__namar_test_loaded_scripts["Customer Statement - SI"] = true;
    // BEGIN legacy Client Script: Customer Statement - SI
    // ============================================================
    // Client Script: Customer Statement - SI
    // DocType: Sales Invoice
    // ============================================================

    frappe.ui.form.on('Sales Invoice', {
        refresh: function(frm) {
            if (frm.doc.customer) {
                render_customer_statement(frm, frm.doc.customer);
            }
        },
        customer: function(frm) {
            if (frm.doc.customer) {
                render_customer_statement(frm, frm.doc.customer);
            } else {
                clear_customer_statement(frm);
            }
        }
    });

    function render_customer_statement(frm, customer) {
        if (!frm.fields_dict['custom_customer_statement']) return;
        if (frm._last_statement_customer === customer && frm._statement_loaded) return;

        frm.fields_dict['custom_customer_statement'].$wrapper.html(
            '<div style="text-align:center; padding:15px; color:var(--text-muted);">جاري التحميل...</div>'
        );

        frappe.call({
            method: 'get_customer_summary',
            args: { customer: customer },
            freeze: false,
            callback: function(r) {
                frm._last_statement_customer = customer;
                frm._statement_loaded = true;
                if (!r.message) {
                    frm.fields_dict['custom_customer_statement'].$wrapper.html(
                        '<div style="text-align:center; padding:15px; color:var(--text-muted);">لا توجد بيانات</div>'
                    );
                    return;
                }
                build_statement_html(frm, r.message);
            }
        });
    }

    function clear_customer_statement(frm) {
        if (frm.fields_dict['custom_customer_statement']) {
            frm.fields_dict['custom_customer_statement'].$wrapper.html('');
            frm._last_statement_customer = null;
            frm._statement_loaded = false;
        }
    }

    function build_statement_html(frm, data) {
        var entries = data.gl_entries || [];
        var total_invoices = parseFloat(data.total_invoices || 0);
        var total_payments = parseFloat(data.total_payments || 0);
        var current_balance = parseFloat(data.current_balance || 0);
        var balance_color = current_balance > 0 ? '#dc3545' : '#28a745';

        var running_balance = current_balance;
        var rows_html = '';

        if (entries.length === 0) {
            rows_html = '<tr><td colspan="6" style="text-align:center; padding:12px; color:var(--text-muted);">لا توجد حركات حديثة</td></tr>';
        } else {
            var type_map = {
                'Sales Invoice': 'فاتورة مبيعات',
                'Payment Entry': 'سند دفع',
                'Delivery Note': 'سند تسليم',
                'Journal Entry': 'قيد يومية',
                'Sales Order': 'أمر بيع'
            };

            for (var i = 0; i < entries.length; i++) {
                var e = entries[i];
                var debit = parseFloat(e.debit || 0);
                var credit = parseFloat(e.credit || 0);
                var row_balance = running_balance;
                running_balance = running_balance - debit + credit;

                var link = frappe.utils.get_form_link(e.voucher_type, e.voucher_no);
                var type_label = type_map[e.voucher_type] || e.voucher_type;
                var row_bal_color = row_balance > 0 ? '#dc3545' : '#28a745';

                rows_html += '<tr>'
                    + '<td style="padding:8px 10px; border-bottom:1px solid var(--border-color);">' + frappe.datetime.str_to_user(e.posting_date) + '</td>'
                    + '<td style="padding:8px 10px; border-bottom:1px solid var(--border-color);">' + type_label + '</td>'
                    + '<td style="padding:8px 10px; border-bottom:1px solid var(--border-color);">' + link + '</td>'
                    + '<td style="padding:8px 10px; border-bottom:1px solid var(--border-color);">' + (debit > 0 ? format_currency(debit) : '-') + '</td>'
                    + '<td style="padding:8px 10px; border-bottom:1px solid var(--border-color);">' + (credit > 0 ? format_currency(credit) : '-') + '</td>'
                    + '<td style="padding:8px 10px; border-bottom:1px solid var(--border-color); font-weight:bold; color:' + row_bal_color + ';">' + format_currency(row_balance) + '</td>'
                    + '</tr>';
            }
        }

        var html = '<div style="border:1px solid var(--border-color); border-radius:var(--border-radius); padding:15px; margin:10px 0; background:var(--card-bg);">'
            + '<h6 style="margin-bottom:12px; color:var(--heading-color);">كشف حساب العميل المختصر</h6>'
            + '<div style="display:flex; gap:15px; margin-bottom:15px; flex-wrap:wrap;">'

            + '<div style="flex:1; min-width:130px; padding:10px; border-radius:var(--border-radius); background:var(--control-bg); text-align:center;">'
            + '<div style="font-size:11px; color:var(--text-muted);">الرصيد الحالي</div>'
            + '<div style="font-size:16px; font-weight:bold; color:' + balance_color + ';">' + format_currency(current_balance) + '</div></div>'

            + '<div style="flex:1; min-width:130px; padding:10px; border-radius:var(--border-radius); background:var(--control-bg); text-align:center;">'
            + '<div style="font-size:11px; color:var(--text-muted);">إجمالي الفواتير</div>'
            + '<div style="font-size:14px; font-weight:bold;">' + format_currency(total_invoices) + '</div></div>'

            + '<div style="flex:1; min-width:130px; padding:10px; border-radius:var(--border-radius); background:var(--control-bg); text-align:center;">'
            + '<div style="font-size:11px; color:var(--text-muted);">إجمالي المقبوضات</div>'
            + '<div style="font-size:14px; font-weight:bold;">' + format_currency(total_payments) + '</div></div>'

            + '</div>'
            + '<table style="width:100%; border-collapse:collapse; font-size:12px; direction:rtl;">'
            + '<thead><tr style="background:var(--control-bg);">'
            + '<th style="padding:8px 10px; text-align:right; font-weight:normal; color:var(--text-muted); font-size:11px;">التاريخ</th>'
            + '<th style="padding:8px 10px; text-align:right; font-weight:normal; color:var(--text-muted); font-size:11px;">النوع</th>'
            + '<th style="padding:8px 10px; text-align:right; font-weight:normal; color:var(--text-muted); font-size:11px;">رقم المستند</th>'
            + '<th style="padding:8px 10px; text-align:right; font-weight:normal; color:var(--text-muted); font-size:11px;">مدين</th>'
            + '<th style="padding:8px 10px; text-align:right; font-weight:normal; color:var(--text-muted); font-size:11px;">دائن</th>'
            + '<th style="padding:8px 10px; text-align:right; font-weight:normal; color:var(--text-muted); font-size:11px;">الرصيد</th>'
            + '</tr></thead>'
            + '<tbody>' + rows_html + '</tbody>'
            + '</table></div>';

        frm.fields_dict['custom_customer_statement'].$wrapper.html(html);
    }
    // END legacy Client Script: Customer Statement - SI
  }
})();
