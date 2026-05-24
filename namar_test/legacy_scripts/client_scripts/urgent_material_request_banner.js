frappe.ui.form.on("Material Request", {
    refresh: function(frm) {
        show_urgent_banner(frm);
    },
    custom_is_urgent: function(frm) {
        show_urgent_banner(frm);
    }
});

function show_urgent_banner(frm) {
    // Remove old banner
    frm.$wrapper.find(".urgent-banner").remove();

    if (frm.doc.custom_is_urgent) {
        var banner = $("<div class=\"urgent-banner\" style=\"background: #dc2626; color: white; padding: 10px 15px; font-size: 14px; font-weight: bold; text-align: center; border-radius: 6px; margin-bottom: 10px; direction: rtl;\">\u26a1 \u0637\u0644\u0628 \u0645\u0633\u062a\u0639\u062c\u0644</div>");
        frm.$wrapper.find(".form-page").prepend(banner);

        // Red indicator on form
        frm.page.set_indicator("\u0645\u0633\u062a\u0639\u062c\u0644", "red");
    }
}
