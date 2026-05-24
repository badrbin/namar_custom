frappe.ui.form.on('Delivery Note', {
    refresh: function(frm) {
        set_item_filter(frm);
        // جلب العميل من أمر البيع المرتبط بطلب المواد
        if (frm.doc.__islocal && frm.doc.custom_material_request && !frm.doc.customer) {
            frappe.call({
                method: 'make_dn_from_mr',
                args: {
                    action: 'get_customer',
                    material_request: frm.doc.custom_material_request
                },
                callback: function(r) {
                    if (r.message && r.message.customer) {
                        frm.set_value('customer', r.message.customer);
                    }
                }
            });
        }
    },
    custom_material_request: function(frm) {
        set_item_filter(frm);
        if (frm.doc.custom_material_request) {
            frappe.call({
                method: 'make_dn_from_mr',
                args: {
                    action: 'get_customer',
                    material_request: frm.doc.custom_material_request
                },
                callback: function(r) {
                    if (r.message && r.message.customer) {
                        frm.set_value('customer', r.message.customer);
                    }
                }
            });
        }
    }
});

function set_item_filter(frm) {
    if (frm.doc.custom_material_request) {
        frappe.call({
            method: 'make_dn_from_mr',
            args: {
                action: 'get_qty',
                material_request: frm.doc.custom_material_request,
                current_doc: frm.doc.name || ''
            },
            callback: function(r) {
                if (r.message) {
                    frm.allowed_qty_map = r.message;
                    var items = Object.keys(r.message);
                    frm.set_query('item_code', 'items', function() {
                        return {
                            filters: {
                                'name': ['in', items]
                            }
                        };
                    });
                }
            }
        });
    }
}

frappe.ui.form.on('Delivery Note Item', {
    item_code: function(frm, cdt, cdn) {
        validate_quantity(frm, cdt, cdn);
    },
    qty: function(frm, cdt, cdn) {
        validate_quantity(frm, cdt, cdn);
    }
});

function validate_quantity(frm, cdt, cdn) {
    var row = locals[cdt][cdn];
    if (frm.allowed_qty_map && frm.allowed_qty_map[row.item_code] !== undefined) {
        var max_qty = frm.allowed_qty_map[row.item_code];
        if (row.qty > max_qty) {
            frappe.msgprint({
                title: __('تنبيه'),
                indicator: 'red',
                message: __('الكمية المتاحة للصنف <b>' + row.item_code + '</b> هي <b>' + max_qty + '</b> فقط.')
            });
            frappe.model.set_value(cdt, cdn, 'qty', max_qty);
        }
    }
}
