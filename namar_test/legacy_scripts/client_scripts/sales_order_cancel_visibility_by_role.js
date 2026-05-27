(function() {
    var cancelRole = "إلغاء أمر البيع";
    var bindKey = "namar_sales_order_cancel_visibility_bound";
    var roleCheck = null;
    var roleCheckPromise = null;

    function has_boot_role() {
        if (frappe.session && frappe.session.user === "Administrator") {
            return true;
        }
        return frappe.user && frappe.user.has_role && frappe.user.has_role(cancelRole);
    }

    function user_doc_has_role(userDoc) {
        return (userDoc.roles || []).some(function(row) {
            return row.role === cancelRole;
        });
    }

    function get_can_show_cancel(callback) {
        if (has_boot_role()) {
            roleCheck = true;
            callback(true);
            return;
        }

        if (roleCheck !== null) {
            callback(roleCheck);
            return;
        }

        if (!roleCheckPromise) {
            roleCheckPromise = new Promise(function(resolve) {
                frappe.call({
                    method: "frappe.client.get",
                    args: {
                        doctype: "User",
                        name: frappe.session.user
                    },
                    callback: function(response) {
                        roleCheck = user_doc_has_role(response.message || {});
                        resolve(roleCheck);
                    },
                    error: function() {
                        roleCheck = false;
                        resolve(false);
                    }
                });
            });
        }

        roleCheckPromise.then(callback);
    }

    function text_of($element) {
        return ($element.text() || "").replace(/\s+/g, " ").trim();
    }

    function is_cancel_text(text) {
        return ["Cancel", __("Cancel"), "إلغاء"].indexOf(text) !== -1;
    }

    function set_cancel_controls_visibility(frm, canShow) {
        if (!frm || !frm.page || !frm.page.wrapper || frm.doc.docstatus !== 1) {
            return;
        }

        var $wrapper = $(frm.page.wrapper);
        var selector = [
            ".dropdown-menu a",
            ".dropdown-menu button",
            ".menu-btn-group a",
            ".menu-btn-group button",
            ".page-actions a",
            ".page-actions button"
        ].join(",");

        $wrapper.find(selector).each(function() {
            var $item = $(this);
            if (!is_cancel_text(text_of($item))) {
                return;
            }

            var $li = $item.closest("li");
            if ($li.length) {
                $li.toggle(canShow);
            } else {
                $item.toggle(canShow);
            }
        });
    }

    function refresh_cancel_controls(frm) {
        if (!frm || !frm.page || !frm.page.wrapper || frm.doc.docstatus !== 1) {
            return;
        }

        if (!has_boot_role()) {
            set_cancel_controls_visibility(frm, false);
        }

        get_can_show_cancel(function(canShow) {
            set_cancel_controls_visibility(frm, canShow);
        });
    }

    function schedule_hide_cancel(frm) {
        [0, 200, 600, 1200].forEach(function(delay) {
            setTimeout(function() {
                refresh_cancel_controls(frm);
            }, delay);
        });
    }

    function bind_dropdown_refresh() {
        if (window[bindKey]) {
            return;
        }

        $(document).on("shown.bs.dropdown.namarSalesOrderCancelVisibility", function() {
            if (cur_frm && cur_frm.doctype === "Sales Order") {
                schedule_hide_cancel(cur_frm);
            }
        });

        window[bindKey] = true;
    }

    frappe.ui.form.on("Sales Order", {
        refresh: function(frm) {
            bind_dropdown_refresh();
            schedule_hide_cancel(frm);
        },
        onload_post_render: function(frm) {
            bind_dropdown_refresh();
            schedule_hide_cancel(frm);
        }
    });
})();
