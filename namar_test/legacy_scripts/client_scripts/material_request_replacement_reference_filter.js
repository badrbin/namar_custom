function set_replacement_reference_query(frm) {
    frm.set_query('custom_reference_material_request', function() {
        if (!frm.doc.sales_order) {
            return {
                filters: [
                    ['Material Request', 'name', '=', '__no_matching_material_request__']
                ]
            };
        }

        return {
            filters: [
                ['Material Request', 'sales_order', '=', frm.doc.sales_order],
                ['Material Request', 'docstatus', '<', 2],
                ['Material Request', 'name', '!=', frm.doc.name || '']
            ]
        };
    });
}

function clear_replacement_reference_if_needed(frm) {
    if (!frm.doc.custom_reference_material_request) return;

    if (frm.doc.custom_request_kind !== 'استبدال' || !frm.doc.sales_order) {
        frm.set_value('custom_reference_material_request', '');
    }
}

function clear_replacement_reference_on_sales_order_change(frm) {
    if (!frm.doc.custom_reference_material_request) return;
    frm.set_value('custom_reference_material_request', '');
}

frappe.ui.form.on('Material Request', {
    setup: function(frm) {
        set_replacement_reference_query(frm);
    },
    refresh: function(frm) {
        set_replacement_reference_query(frm);
    },
    sales_order: function(frm) {
        clear_replacement_reference_on_sales_order_change(frm);
        set_replacement_reference_query(frm);
    },
    custom_request_kind: function(frm) {
        clear_replacement_reference_if_needed(frm);
        set_replacement_reference_query(frm);
    }
});
