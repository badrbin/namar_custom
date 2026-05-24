frappe.ui.form.on('Material Request', {
    setup: function(frm) {
        frm.set_query('custom_driver', function() {
            return {
                filters: {
                    is_active: 1
                }
            };
        });
    }
});
