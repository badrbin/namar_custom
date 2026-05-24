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
