frappe.ui.form.on("Sales Order", {
    refresh(frm) {
        if (frm.doc.docstatus !== 1) return;
        schedule_material_request_button_override(frm);
    }
});

function schedule_material_request_button_override(frm) {
    [0, 300, 1200].forEach((delay) => {
        setTimeout(() => replace_material_request_button(frm), delay);
    });
}

function replace_material_request_button(frm) {
    remove_material_request_buttons(frm);
    frm.add_custom_button(__("Material Request"), function () {
        create_material_request_from_sales_order_balance(frm);
    }, __("Create"));
}

function remove_material_request_buttons(frm) {
    [__("Material Request"), __("طلب المواد")].forEach((label) => {
        try { frm.remove_custom_button(label, __("Create")); } catch (e) {}
        try { frm.remove_custom_button(label); } catch (e) {}
    });
}

function create_material_request_from_sales_order_balance(frm) {
    frappe.call({
        method: "make_material_request_from_sales_order_balance",
        args: { sales_order: frm.doc.name },
        freeze: true,
        freeze_message: __("جاري إنشاء طلب المواد من رصيد أمر البيع..."),
        callback(r) {
            if (r.exc) return;
            var result = r.message || {};
            if (!result.name) {
                frappe.msgprint(__("لم يتم إنشاء طلب المواد."));
                return;
            }
            frappe.show_alert({
                message: __("تم إنشاء طلب المواد {0}", [result.name]),
                indicator: "green"
            });
            frappe.set_route("Form", "Material Request", result.name);
        }
    });
}
