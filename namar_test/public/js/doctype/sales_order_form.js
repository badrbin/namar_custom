/* Auto-generated from live Client Script records on testnamar.u.frappe.cloud. */
(function () {
  if (!window.frappe || !frappe.boot) {
    return;
  }
  window.__namar_test_loaded_scripts = window.__namar_test_loaded_scripts || {};
  if (frappe.boot.namar_test_client_scripts_enabled) {
  if (!window.__namar_test_loaded_scripts["Customer Statement - SO"]) {
    window.__namar_test_loaded_scripts["Customer Statement - SO"] = true;
    // BEGIN legacy Client Script: Customer Statement - SO
    // ============================================================
    // Client Script: Customer Statement - SO (Fixed)
    // DocType: Sales Order
    // ============================================================

    frappe.ui.form.on('Sales Order', {
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
    // END legacy Client Script: Customer Statement - SO
  }
  if (!window.__namar_test_loaded_scripts["Sales Order Dashboard SO"]) {
    window.__namar_test_loaded_scripts["Sales Order Dashboard SO"] = true;
    // BEGIN legacy Client Script: Sales Order Dashboard SO
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
    // END legacy Client Script: Sales Order Dashboard SO
  }
  if (!window.__namar_test_loaded_scripts["Sales Order Material Request By Balance"]) {
    window.__namar_test_loaded_scripts["Sales Order Material Request By Balance"] = true;
    // BEGIN legacy Client Script: Sales Order Material Request By Balance
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
    // END legacy Client Script: Sales Order Material Request By Balance
  }
  }
  if (!window.__namar_test_loaded_scripts["Sales Order Cancel Visibility By Role"]) {
    window.__namar_test_loaded_scripts["Sales Order Cancel Visibility By Role"] = true;
    // BEGIN app Client Script: Sales Order Cancel Visibility By Role
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
    // END app Client Script: Sales Order Cancel Visibility By Role
  }
})();
