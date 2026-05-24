// ============================================================
// Client Script: Customer Statement - DN (Fixed)
// DocType: Delivery Note
// ============================================================

frappe.ui.form.on('Delivery Note', {
    refresh: function(frm) {
        if (frm.doc.customer) {
            render_customer_statement(frm, frm.doc.customer);
        }
    },
    customer: function(frm) {
        if (frm.doc.customer) {
            frm._last_statement_customer = null;
            frm._statement_loaded = false;
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
            if (r.message) {
                frm.fields_dict['custom_customer_statement'].$wrapper.html(r.message);
            } else {
                frm.fields_dict['custom_customer_statement'].$wrapper.html(
                    '<div style="text-align:center; padding:15px; color:var(--text-muted);">لا توجد بيانات</div>'
                );
            }
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
