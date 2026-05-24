/* Auto-generated from live Client Script records on testnamar.u.frappe.cloud. */
(function () {
  if (!window.frappe || !frappe.boot || !frappe.boot.namar_test_client_scripts_enabled) {
    return;
  }
  window.__namar_test_loaded_scripts = window.__namar_test_loaded_scripts || {};
  if (!window.__namar_test_loaded_scripts["Purchase Order Dashboard PO"]) {
    window.__namar_test_loaded_scripts["Purchase Order Dashboard PO"] = true;
    // BEGIN legacy Client Script: Purchase Order Dashboard PO
    // ============================================================
    // Client Script: Purchase Order Dashboard PO
    // DocType: Purchase Order
    // ============================================================

    frappe.ui.form.on("Purchase Order", {
        refresh(frm) {
            if (frm.doc.name && frm.doc.docstatus >= 0) {
                call_po_dashboard(frm);
            } else {
                clear_po_dashboard(frm);
            }
        }
    });

    function clear_po_dashboard(frm) {
        if (!frm.fields_dict.custom_purchase_order_statement) return;
        frm.set_df_property("custom_purchase_order_statement", "options", "");
        frm.refresh_field("custom_purchase_order_statement");
        frm._po_dashboard_loaded = false;
    }

    function call_po_dashboard(frm) {
        if (!frm.fields_dict.custom_purchase_order_statement) return;
        if (!frm.doc.name) {
            clear_po_dashboard(frm);
            return;
        }
        // منع الاستدعاء المتكرر
        if (frm._po_dashboard_name === frm.doc.name && frm._po_dashboard_loaded) return;

        frappe.call({
            method: "get_purchase_dashboard",
            args: { purchase_order: frm.doc.name },
            freeze: false,
            callback(r) {
                frm._po_dashboard_name = frm.doc.name;
                frm._po_dashboard_loaded = true;
                var html = (!r.exc && r.message) ? r.message : "";
                frm.set_df_property("custom_purchase_order_statement", "options", html);
                frm.refresh_field("custom_purchase_order_statement");
            }
        });
    }
    // END legacy Client Script: Purchase Order Dashboard PO
  }
  if (!window.__namar_test_loaded_scripts["Supplier Statement - PO"]) {
    window.__namar_test_loaded_scripts["Supplier Statement - PO"] = true;
    // BEGIN legacy Client Script: Supplier Statement - PO
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
    // END legacy Client Script: Supplier Statement - PO
  }
})();
