frappe.ui.form.on('Material Request', {
    refresh: function(frm) {
        calculate_total(frm);
    },
    validate: function(frm) {
        calculate_total(frm);
    }
});

frappe.ui.form.on('Material Request Item', {
    qty: function(frm) {
        calculate_total(frm);
    },
    items_remove: function(frm) {
        calculate_total(frm);
    }
});

function calculate_total(frm) {
    if (!frm.fields_dict['custom_total_quantity']) return;
    var total = 0;
    (frm.doc.items || []).forEach(function(d) { total += flt(d.qty); });
    if (frm.doc.custom_total_quantity !== total) {
        frm.doc.custom_total_quantity = total;
        frm.refresh_field('custom_total_quantity');
    }
}
