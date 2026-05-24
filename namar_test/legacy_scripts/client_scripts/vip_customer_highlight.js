frappe.ui.form.on("Material Request", {
    refresh: function(frm) {
        if (frm.doc.customer) {
            frappe.db.get_value("Customer", frm.doc.customer, "custom_vip", function(r) {
                if (r && r.custom_vip) {
                    frm.fields_dict.customer_name.$wrapper.find(".like-disabled-input, .control-value, input").css({"color": "#EF4444", "font-weight": "bold"});
                }
            });
        }
    }
});
