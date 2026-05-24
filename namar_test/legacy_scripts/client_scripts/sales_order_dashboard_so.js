// ============================================================
// Client Script: Sales Order Dashboard SO (محسّن)
// DocType: Sales Order
// ============================================================

frappe.ui.form.on("Sales Order", {
    refresh(frm) {
        if (frm.doc.name && frm.doc.docstatus >= 0) {
            call_server_dashboard(frm);
        } else {
            clear_dashboard(frm);
        }
    }
});

function clear_dashboard(frm) {
    if (!frm.fields_dict.custom_sales_order_statement) return;
    frm.set_df_property("custom_sales_order_statement", "options", "");
    frm.refresh_field("custom_sales_order_statement");
    frm._dashboard_loaded = false;
}

function call_server_dashboard(frm) {
    if (!frm.fields_dict.custom_sales_order_statement) return;
    if (!frm.doc.name) {
        clear_dashboard(frm);
        return;
    }
    // منع الاستدعاء المتكرر
    if (frm._dashboard_so === frm.doc.name && frm._dashboard_loaded) return;

    frappe.call({
        method: "get_sales_dashboard",
        args: { sales_order: frm.doc.name },
        freeze: false,
        callback(r) {
            frm._dashboard_so = frm.doc.name;
            frm._dashboard_loaded = true;
            var html = (!r.exc && r.message) ? r.message : "";
            frm.set_df_property("custom_sales_order_statement", "options", html);
            frm.refresh_field("custom_sales_order_statement");
        }
    });
}
