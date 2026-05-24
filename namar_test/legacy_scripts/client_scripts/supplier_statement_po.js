// ============================================================
// Client Script: Supplier Statement - PO
// DocType: Purchase Order
// ============================================================

frappe.ui.form.on('Purchase Order', {
    refresh: function(frm) {
        if (frm.doc.supplier) {
            render_supplier_statement(frm, frm.doc.supplier);
        }
    },
    supplier: function(frm) {
        if (frm.doc.supplier) {
            frm._last_statement_supplier = null;
            frm._supplier_loaded = false;
            render_supplier_statement(frm, frm.doc.supplier);
        } else {
            clear_supplier_statement(frm);
        }
    }
});

function render_supplier_statement(frm, supplier) {
    if (!frm.fields_dict['custom_supplier_statement']) return;
    if (frm._last_statement_supplier === supplier && frm._supplier_loaded) return;

    frm.fields_dict['custom_supplier_statement'].$wrapper.html(
        '<div style="text-align:center; padding:15px; color:var(--text-muted);">جاري التحميل...</div>'
    );

    frappe.call({
        method: 'get_supplier_summary',
        args: { supplier: supplier },
        freeze: false,
        callback: function(r) {
            frm._last_statement_supplier = supplier;
            frm._supplier_loaded = true;
            if (r.message) {
                frm.fields_dict['custom_supplier_statement'].$wrapper.html(r.message);
            } else {
                frm.fields_dict['custom_supplier_statement'].$wrapper.html(
                    '<div style="text-align:center; padding:15px; color:var(--text-muted);">لا توجد بيانات</div>'
                );
            }
        }
    });
}

function clear_supplier_statement(frm) {
    if (frm.fields_dict['custom_supplier_statement']) {
        frm.fields_dict['custom_supplier_statement'].$wrapper.html('');
        frm._last_statement_supplier = null;
        frm._supplier_loaded = false;
    }
}
