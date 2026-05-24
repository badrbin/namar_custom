/* Auto-generated from live Client Script records on testnamar.u.frappe.cloud. */
(function () {
  if (!window.frappe || !frappe.boot || !frappe.boot.namar_test_client_scripts_enabled) {
    return;
  }
  window.__namar_test_loaded_scripts = window.__namar_test_loaded_scripts || {};
  if (!window.__namar_test_loaded_scripts["Customer Statement - DN"]) {
    window.__namar_test_loaded_scripts["Customer Statement - DN"] = true;
    // BEGIN legacy Client Script: Customer Statement - DN
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
    // END legacy Client Script: Customer Statement - DN
  }
  if (!window.__namar_test_loaded_scripts["j"]) {
    window.__namar_test_loaded_scripts["j"] = true;
    // BEGIN legacy Client Script: j
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
    // END legacy Client Script: j
  }
})();
