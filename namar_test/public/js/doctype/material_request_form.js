/* Auto-generated from live Client Script records on testnamar.u.frappe.cloud. */
(function () {
  if (!window.frappe || !frappe.boot || !frappe.boot.namar_test_client_scripts_enabled) {
    return;
  }
  window.__namar_test_loaded_scripts = window.__namar_test_loaded_scripts || {};
  if (!window.__namar_test_loaded_scripts["Extract Google Map Coordinates"]) {
    window.__namar_test_loaded_scripts["Extract Google Map Coordinates"] = true;
    // BEGIN legacy Client Script: Extract Google Map Coordinates
    frappe.ui.form.on("Material Request", {
        custom_google_map: function(frm) {
            if (!frm.doc.custom_google_map) {
                frm.set_value("custom_latitude", 0);
                frm.set_value("custom_longitude", 0);
                if (frm.doc.docstatus === 1) frm.save("Update");
                return;
            }
            var url = frm.doc.custom_google_map.trim();
            var coords = extract_coordinates(url);
            if (coords) {
                frm.set_value("custom_latitude", coords.lat);
                frm.set_value("custom_longitude", coords.lng);
                if (frm.doc.docstatus === 1) frm.save("Update");
                return;
            }
            frappe.call({
                method: "resolve_map_url",
                args: { url: url },
                callback: function(r) {
                    if (r.message && r.message.lat && r.message.lng) {
                        frm.set_value("custom_latitude", r.message.lat);
                        frm.set_value("custom_longitude", r.message.lng);
                        if (frm.doc.docstatus === 1) frm.save("Update");
                    } else {
                        frappe.msgprint({
                            title: __("تنبيه"),
                            indicator: "orange",
                            message: '<div dir="rtl">تعذر استخراج الإحداثيات من الرابط. تأكد أن الرابط صحيح.</div>'
                        });
                    }
                }
            });
        }
    });

    function extract_coordinates(url) {
        if (!url) return null;
        var patterns = [
            /daddr=([-\d.]+),([-\d.]+)/,
            /!3d([-\d.]+)!4d([-\d.]+)/,
            /[?&]q=([-\d.]+),([-\d.]+)/,
            /@([-\d.]+),([-\d.]+)/,
            /place\/([-\d.]+),([-\d.]+)/,
            /ll=([-\d.]+),([-\d.]+)/,
            /saddr=([-\d.]+),([-\d.]+)/,
            /^([-\d.]+),\s*([-\d.]+)$/
        ];
        for (var i = 0; i < patterns.length; i++) {
            var match = url.match(patterns[i]);
            if (match) {
                var lat = parseFloat(match[1]), lng = parseFloat(match[2]);
                if (lat >= 15 && lat <= 33 && lng >= 34 && lng <= 57) {
                    return { lat: lat, lng: lng };
                }
            }
        }
        return null;
    }
    // END legacy Client Script: Extract Google Map Coordinates
  }
  if (!window.__namar_test_loaded_scripts["Material Request Dashboard"]) {
    window.__namar_test_loaded_scripts["Material Request Dashboard"] = true;
    // BEGIN legacy Client Script: Material Request Dashboard
    frappe.ui.form.on('Material Request', {
        refresh: function(frm) {
            add_custom_buttons(frm);
            load_mr_dashboard(frm);
        },
        sales_order: function(frm) {
            frm._mr_data_loaded = false;
            frm._dashboard_loaded = false;
            frm._statement_loaded = false;
            frm._last_statement_customer = null;
            frm._dashboard_so = null;
            if (frm.doc.sales_order) {
                load_mr_dashboard(frm);
            } else {
                clear_all(frm);
            }
        }
    });

    function add_custom_buttons(frm) {
        frm.remove_custom_button(__('Stock Entry (Manual)'), __('Create'));
        if (frm.doc.docstatus === 1 && frm.doc.status !== 'Stopped') {
            frm.add_custom_button(__('Stock Entry (Manual)'), function() {
                frappe.model.with_doctype('Stock Entry', function() {
                    var se = frappe.model.get_new_doc('Stock Entry');
                    se.company = frm.doc.company;
                    se.custom_material_request = frm.doc.name;
                    se.custom_sales_order = frm.doc.sales_order || null;
                    if (frm.doc.branch) se['branch'] = frm.doc.branch;
                    se.items = [];
                    se.purpose = 'Material Issue';
                    se.stock_entry_type = 'Material Issue';
                    frappe.set_route('Form', 'Stock Entry', se.name);
                });
            }, __('Create'));
            frm.add_custom_button(__('Delivery Note'), function() {
                frappe.xcall('make_dn_from_mr', { source_name: frm.doc.name }).then(function(r) {
                    if (r) { var doc = frappe.model.sync(r); frappe.set_route('Form', 'Delivery Note', doc[0].name); }
                });
            }, __('Create'));
            frm.add_custom_button(__('Installation Note'), function() {
                frappe.xcall('make_installation_note_from_mr', { source_name: frm.doc.name }).then(function(r) {
                    if (r) { var doc = frappe.model.sync(r); frappe.set_route('Form', 'Installation Note', doc[0].name); }
                });
            }, __('Create'));
        }
    }

    function clear_all(frm) {
        frm.set_query("item_code", "items", function() { return {}; });
        if (frm.fields_dict.custom_sales_order_statement) {
            frm.set_df_property("custom_sales_order_statement", "options", "");
            frm.refresh_field("custom_sales_order_statement");
        }
        if (frm.fields_dict['custom_customer_statement']) {
            frm.fields_dict['custom_customer_statement'].$wrapper.html('');
        }
        if (frm.fields_dict.custom_sales_order_summary) {
            frm.set_df_property('custom_sales_order_summary', 'hidden', 1);
        }
    }

    function load_mr_dashboard(frm) {
        var se_field = frm.get_field('custom_stock_entry_summary');
        if (se_field && frm.is_new()) {
            se_field.$wrapper.html('<div class="text-muted" style="padding:10px; font-size:12px;">احفظ المستند أولاً.</div>');
        }

        frappe.call({
            method: 'get_mr_full_data',
            args: { mr_name: frm.doc.name || '', sales_order: frm.doc.sales_order || '' },
            freeze: false,
            callback: function(r) {
                if (!r.message) return;
                var data = r.message;

                if (data.allowed_items && data.allowed_items.length) {
                    frm.set_query("item_code", "items", function() {
                        return { filters: [["Item", "name", "in", data.allowed_items]] };
                    });
                }

                if (data.stock_entries) {
                    render_stock_entries(frm, data.stock_entries);
                }

                if (data.related_items && data.related_items.length) {
                    render_related_items(frm, data.related_items, frm.doc.sales_order);
                } else if (frm.fields_dict.custom_sales_order_summary) {
                    frm.set_df_property('custom_sales_order_summary', 'hidden', 1);
                }

                if (data.customer) {
                    load_customer_statement(frm, data.customer);
                }
            }
        });

        if (frm.doc.sales_order) {
            load_sales_dashboard(frm);
        }
    }

    function load_sales_dashboard(frm) {
        if (!frm.fields_dict.custom_sales_order_statement) return;
        if (frm._dashboard_so === frm.doc.sales_order && frm._dashboard_loaded) return;
        frappe.call({
            method: "get_sales_dashboard",
            args: { sales_order: frm.doc.sales_order },
            freeze: false,
            callback: function(r) {
                frm._dashboard_so = frm.doc.sales_order;
                frm._dashboard_loaded = true;
                var html = (!r.exc && r.message) ? r.message : "";
                frm.set_df_property("custom_sales_order_statement", "options", html);
                frm.refresh_field("custom_sales_order_statement");
            }
        });
    }

    function load_customer_statement(frm, customer) {
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

    function render_stock_entries(frm, data) {
        var wrapper = frm.get_field('custom_stock_entry_summary');
        if (!wrapper) return;
        wrapper = wrapper.$wrapper;

        if (!data.entries || !data.entries.length) {
            wrapper.html('<div style="border: 1px solid var(--border-color); border-radius: var(--border-radius); margin-top: 10px; background-color: var(--card-bg); padding: 15px; text-align: center; color: var(--text-muted); font-size: 12px;">' + __("No Stock Entries linked yet") + '</div>');
            return;
        }
        var entries = data.entries;
        var all_items = data.items;
        var cs = 'padding: 8px 10px; font-size: 12px; vertical-align: top; border-bottom: 1px solid var(--border-color); color: var(--text-color);';
        var html = '<div style="border: 1px solid var(--border-color); border-radius: var(--border-radius); overflow: hidden; margin-top: 10px; background-color: var(--card-bg);">';
        html += '<table class="table" style="margin:0; width:100%; border-collapse: collapse;">';
        html += '<thead><tr style="background-color: var(--control-bg); color: var(--text-muted); font-size: 11px;">';
        html += '<th style="' + cs + ' width:20%;">' + __("Stock Entry") + '</th>';
        html += '<th style="' + cs + ' width:55%;">' + __("Items Details") + '</th>';
        html += '<th style="' + cs + ' width:15%;">' + __("Type") + '</th>';
        html += '<th style="' + cs + ' width:10%;">' + __("Status") + '</th>';
        html += '</tr></thead><tbody>';

        for (var i = 0; i < entries.length; i++) {
            var d = entries[i];
            var link = frappe.utils.get_form_link('Stock Entry', d.name, true);
            var status_label = d.docstatus === 1 ? __('Submitted') : __('Draft');
            var indicator_color = d.docstatus === 1 ? 'blue' : 'orange';
            var my_items = all_items.filter(function(item) { return item.parent === d.name; });
            var items_html = '';
            if (my_items.length > 0) {
                items_html = '<ul style="margin:0; padding-left:15px; list-style:none;">';
                for (var j = 0; j < my_items.length; j++) {
                    var it = my_items[j];
                    var txt_color = 'var(--text-color)';
                    var sign = '';
                    if (it.s_warehouse && it.t_warehouse) { txt_color = 'var(--blue-500)'; sign = '\u2194'; }
                    else if (it.s_warehouse) { txt_color = 'var(--red-500)'; sign = '-'; }
                    else if (it.t_warehouse) { txt_color = 'var(--green-500)'; sign = '+'; }
                    items_html += '<li style="font-size:12px; margin-bottom:4px; display: flex; align-items: center; justify-content: space-between; border-bottom: 1px dashed var(--border-color); padding-bottom: 4px;">';
                    items_html += '<span style="font-weight:500;">' + it.item_code + '</span>';
                    items_html += '<span style="color:' + txt_color + '; background-color:var(--control-bg); padding: 2px 6px; border-radius: 4px; font-weight: bold; font-size: 11px;">' + sign + ' ' + it.qty + ' ' + it.uom + '</span>';
                    items_html += '</li>';
                }
                items_html += '</ul>';
            } else {
                items_html = '<span style="color:var(--text-muted); font-size:11px;">' + __("No Items") + '</span>';
            }
            html += '<tr>';
            html += '<td style="' + cs + ' font-weight: bold;">' + link + '</td>';
            html += '<td style="' + cs + '">' + items_html + '</td>';
            html += '<td style="' + cs + '">' + frappe.utils.escape_html(d.stock_entry_type || '-') + '</td>';
            html += '<td style="' + cs + '"><span class="indicator-pill ' + indicator_color + ' no-indicator-dot" style="font-size: 11px;">' + status_label + '</span></td>';
            html += '</tr>';
        }
        html += '</tbody></table></div>';
        wrapper.html(html);
    }

    function render_related_items(frm, data, sales_order) {
        if (!frm.fields_dict.custom_sales_order_summary) return;
        var html = '<div style="margin-top: 10px; margin-bottom: 10px;">';
        html += '<div class="form-group"><div class="clearfix"><label class="control-label" style="font-weight: bold;">';
        html += '\u0645\u0644\u062e\u0635 \u0634\u0627\u0645\u0644 \u0644\u0623\u0645\u0631 \u0627\u0644\u0628\u064a\u0639: <a href="/app/sales-order/' + sales_order + '" target="_blank">' + sales_order + '</a>';
        html += '</label></div></div>';
        html += '<table class="table table-bordered table-sm" style="font-size: var(--text-sm);">';
        html += '<thead><tr style="background-color: var(--table-bg); color: var(--text-muted);">';
        html += '<th width="20%">\u0627\u0644\u0635\u0646\u0641</th><th width="8%" class="text-right">\u0645\u0637\u0644\u0648\u0628 (SO)</th><th width="8%" class="text-right">\u062a\u0645 \u0637\u0644\u0628\u0647 (MR)</th><th width="8%" class="text-right">\u0627\u0644\u0645\u062a\u0628\u0642\u064a (MR)</th><th width="8%" class="text-right">\u0645\u0633\u0644\u0645\u0629</th><th width="8%" class="text-right" style="color: #28a745;">\u0645\u0641\u0648\u062a\u0631\u0629</th><th width="8%" class="text-right" style="color: #007bff;">\u062a\u0645 \u062a\u0631\u0643\u064a\u0628\u0647</th><th width="11%" class="text-right">\u0631\u0635\u064a\u062f \u0627\u0644\u0645\u0641\u0648\u062a\u0631</th><th width="11%" class="text-right">\u0631\u0635\u064a\u062f \u0627\u0644\u0645\u0633\u0644\u0645</th><th width="10%" class="text-right">\u0631\u0635\u064a\u062f \u0627\u0644\u062a\u0631\u0643\u064a\u0628</th>';
        html += '</tr></thead><tbody>';

        for (var i = 0; i < data.length; i++) {
            var row = data[i];
            var name_suffix = "";
            if (row.is_extra) { name_suffix = '<span class="indicator-pill red" style="margin-right: 5px; font-size: 10px;">\u0625\u0636\u0627\u0641\u064a</span>'; }
            var diff_qty = row.billed_qty - row.mr_qty;
            var diff_style = diff_qty < 0 ? "color: #dc3545; font-weight: bold;" : "color: #28a745; font-weight: bold;";
            var delivered_balance = row.delivered_qty - row.mr_qty;
            var delivered_style = delivered_balance < 0 ? "color: #dc3545; font-weight: bold;" : "color: #28a745; font-weight: bold;";
            var install_balance = row.installed_qty - row.mr_qty;
            var install_bal_style = install_balance < 0 ? "color: #dc3545; font-weight: bold;" : "color: #28a745; font-weight: bold;";
            html += '<tr>';
            html += '<td>' + name_suffix + '<span style="font-weight: bold; color: var(--text-color);">' + row.item_code + '</span><br><span style="font-size: 11px; color: var(--text-muted);">' + row.item_name + '</span></td>';
            html += '<td class="text-right">' + format_number(row.so_qty, null, 2) + '</td>';
            html += '<td class="text-right">' + format_number(row.mr_qty, null, 2) + '</td>';
            html += '<td class="text-right" style="font-weight: bold;">' + format_number(row.balance, null, 2) + '</td>';
            html += '<td class="text-right">' + format_number(row.delivered_qty, null, 2) + '</td>';
            html += '<td class="text-right" style="font-weight: bold; color: #28a745;">' + format_number(row.billed_qty, null, 2) + '</td>';
            html += '<td class="text-right" style="font-weight: bold; color: #007bff;">' + format_number(row.installed_qty, null, 2) + '</td>';
            html += '<td class="text-right" style="' + diff_style + '">' + format_number(diff_qty, null, 2) + '</td>';
            html += '<td class="text-right" style="' + delivered_style + '">' + format_number(delivered_balance, null, 2) + '</td>';
            html += '<td class="text-right" style="' + install_bal_style + '">' + format_number(install_balance, null, 2) + '</td>';
            html += '</tr>';
        }
        html += '</tbody></table></div>';
        frm.set_df_property('custom_sales_order_summary', 'hidden', 0);
        frm.fields_dict.custom_sales_order_summary.$wrapper.html(html);
    }
    // END legacy Client Script: Material Request Dashboard
  }
  if (!window.__namar_test_loaded_scripts["Material Request Driver Filter"]) {
    window.__namar_test_loaded_scripts["Material Request Driver Filter"] = true;
    // BEGIN legacy Client Script: Material Request Driver Filter
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
    // END legacy Client Script: Material Request Driver Filter
  }
  if (!window.__namar_test_loaded_scripts["Material Request Linked Request Autofill"]) {
    window.__namar_test_loaded_scripts["Material Request Linked Request Autofill"] = true;
    // BEGIN legacy Client Script: Material Request Linked Request Autofill
    frappe.ui.form.on("Material Request", {
        refresh: function(frm) {
            autofill_from_linked_material_request(frm, null, true);
        },
        custom_scenario_reference: function(frm) {
            autofill_from_linked_material_request(frm, "custom_scenario_reference", false);
        },
        custom_reference_material_request: function(frm) {
            autofill_from_linked_material_request(frm, "custom_reference_material_request", false);
        }
    });

    function autofill_from_linked_material_request(frm, source_fieldname, only_missing) {
        var linked_request = get_linked_material_request(frm, source_fieldname);

        if (!linked_request || linked_request === frm.doc.name) {
            return;
        }

        frappe.db.get_value(
            "Material Request",
            linked_request,
            [
                "custom_google_map",
                "delivery_date",
                "custom_installation_note_teams",
                "custom_project_name"
            ],
            function(result) {
                var values = result || {};

                set_autofill_value(frm, "custom_google_map", values.custom_google_map || "", only_missing);
                set_autofill_value(frm, "delivery_date", values.delivery_date || "", only_missing);
                set_autofill_value(frm, "custom_installation_note_teams", values.custom_installation_note_teams || "", only_missing);
                set_autofill_value(frm, "custom_project_name", values.custom_project_name || "", only_missing);
            }
        );
    }

    function get_linked_material_request(frm, source_fieldname) {
        if (source_fieldname && frm.doc[source_fieldname]) {
            return frm.doc[source_fieldname];
        }
        return frm.doc.custom_scenario_reference || frm.doc.custom_reference_material_request || "";
    }

    function set_autofill_value(frm, fieldname, value, only_missing) {
        if (!frm.fields_dict[fieldname]) {
            return;
        }
        if (only_missing && frm.doc[fieldname]) {
            return;
        }
        if ((frm.doc[fieldname] || "") !== (value || "")) {
            frm.set_value(fieldname, value || "");
        }
    }
    // END legacy Client Script: Material Request Linked Request Autofill
  }
  if (!window.__namar_test_loaded_scripts["Material Request Manufacturing Tab"]) {
    window.__namar_test_loaded_scripts["Material Request Manufacturing Tab"] = true;
    // BEGIN legacy Client Script: Material Request Manufacturing Tab
    function manufacturingEscapeHtml(value) {
        return frappe.utils.escape_html(value == null ? '' : String(value));
    }

    function manufacturingFormatDateTime(value) {
        if (!value) return 'غير مسجل';
        try {
            return frappe.datetime.str_to_user(value);
        } catch (error) {
            return manufacturingEscapeHtml(value);
        }
    }

    function manufacturingShortName(name) {
        return manufacturingEscapeHtml((name || '').replace(/^MREQ-/i, ''));
    }

    function manufacturingToInt(value) {
        var numericValue = parseInt(value, 10);
        return isNaN(numericValue) ? 0 : numericValue;
    }

    function manufacturingStatusLabel(data) {
        var total = manufacturingToInt(data.total_items || 0);
        var remaining = manufacturingToInt(data.remaining_count || 0);
        var manufactured = manufacturingToInt(data.manufactured_count || 0);

        if (total > 0 && remaining <= 0) {
            return { text: 'مكتمل', indicatorClass: 'green' };
        }
        if (manufactured > 0) {
            return { text: 'قيد التصنيع', indicatorClass: 'yellow' };
        }
        return { text: 'غير مصنع', indicatorClass: 'red' };
    }

    function manufacturingRemainingTone(data) {
        var total = manufacturingToInt(data.total_items || 0);
        var remaining = manufacturingToInt(data.remaining_count || 0);
        var manufactured = manufacturingToInt(data.manufactured_count || 0);

        if (total > 0 && remaining <= 0) return 'green';
        if (manufactured > 0) return 'yellow';
        return 'red';
    }

    function manufacturingSummaryCard(label, value, valueColor) {
        return ''
            + '<div class="manufacturing-summary-card">'
            +   '<div class="manufacturing-summary-label">' + manufacturingEscapeHtml(label) + '</div>'
            +   '<div class="manufacturing-summary-value" style="color:' + manufacturingEscapeHtml(valueColor || 'var(--text-color)') + ';">' + manufacturingEscapeHtml(value) + '</div>'
            + '</div>';
    }

    function manufacturingSectionTone(mode, count, data) {
        if (mode === 'done') {
            return count > 0 ? 'green' : 'red';
        }
        if (count <= 0) {
            return manufacturingToInt(data.total_items || 0) > 0 ? 'green' : 'red';
        }
        return manufacturingRemainingTone(data);
    }

    function manufacturingItemRow(item, mode) {
        var badgeClass = mode === 'done' ? 'green' : 'yellow';
        var statusText = mode === 'done' ? 'تم تصنيعه' : 'متبقي';
        var timeText = mode === 'done' ? manufacturingFormatDateTime(item.manufactured_at) : 'بانتظار التصنيع';
        var byText = mode === 'done' ? manufacturingEscapeHtml(item.manufactured_by || 'غير مسجل') : '-';
        return ''
            + '<tr>'
            +   '<td style="padding: 8px 12px; border-bottom: 1px solid var(--border-color); text-align: right; font-weight: 600; white-space: nowrap;">' + manufacturingEscapeHtml(item.row || '-') + '</td>'
            +   '<td style="padding: 8px 12px; border-bottom: 1px solid var(--border-color); text-align: right;">' + manufacturingEscapeHtml(item.item_name || item.item_code || '-') + '</td>'
            +   '<td style="padding: 8px 12px; border-bottom: 1px solid var(--border-color); text-align: right; color: var(--text-muted); white-space: nowrap;">' + manufacturingEscapeHtml(item.item_code || '-') + '</td>'
            +   '<td style="padding: 8px 12px; border-bottom: 1px solid var(--border-color); text-align: right;">'
            +     '<div class="indicator-pill ' + badgeClass + '" style="white-space: nowrap;">' + statusText + '</div>'
            +     '<div style="margin-top: 6px; font-size: 12px; color: var(--text-muted); white-space: nowrap;">' + manufacturingEscapeHtml(timeText) + '</div>'
            +   '</td>'
            +   '<td style="padding: 8px 12px; border-bottom: 1px solid var(--border-color); text-align: right; white-space: nowrap;">' + byText + '</td>'
            + '</tr>';
    }

    function manufacturingSection(title, count, items, mode, emptyText, data) {
        var toneClass = manufacturingSectionTone(mode, count, data || {});
        var rows = (items || []).length
            ? items.map(function(item) { return manufacturingItemRow(item, mode); }).join('')
            : '<tr><td colspan="5" style="text-align:center; padding: 15px; color: var(--text-muted);">' + manufacturingEscapeHtml(emptyText) + '</td></tr>';
        return ''
            + '<div class="manufacturing-section-box">'
            +   '<div class="manufacturing-section-head">'
            +     '<div class="manufacturing-section-title">' + manufacturingEscapeHtml(title) + '</div>'
            +     '<div class="indicator-pill ' + toneClass + '" style="white-space: nowrap;">' + manufacturingEscapeHtml(count) + '</div>'
            +   '</div>'
            +   '<div class="scroll-box manufacturing-scroll-box">'
            +     '<table class="manufacturing-table">'
            +       '<thead>'
            +         '<tr>'
            +           '<th>رقم الباب</th>'
            +           '<th>الصنف</th>'
            +           '<th>الكود</th>'
            +           '<th>الحالة</th>'
            +           '<th>بواسطة</th>'
            +         '</tr>'
            +       '</thead>'
            +       '<tbody>' + rows + '</tbody>'
            +     '</table>'
            +   '</div>'
            + '</div>';
    }

    function manufacturingOpenFactoryLink(docname) {
        var shortName = (docname || '').replace(/^MREQ-/i, '');
        return '/factory?mr=' + encodeURIComponent(shortName) + '&v=202604091012';
    }

    function renderManufacturingDashboard(frm, data) {
        var field = frm.fields_dict.custom_manufacturing_dashboard;
        if (!field || !field.$wrapper) return;

        var status = manufacturingStatusLabel(data);
        var remainingTone = manufacturingRemainingTone(data);
        var total = manufacturingToInt(data.total_items || 0);
        var manufactured = manufacturingToInt(data.manufactured_count || 0);
        var remaining = manufacturingToInt(data.remaining_count || 0);
        var percent = data.completion_percent || 0;
        var requestName = manufacturingShortName(frm.doc.name || data.material_request || '');

        var html = ''
            + '<style>'
            + '.manufacturing-dashboard{padding: 8px 0 4px; color: var(--text-color);}'
            + '.manufacturing-shell{border: 1px solid var(--border-color); border-radius: var(--border-radius); background-color: var(--card-bg); overflow: hidden;}'
            + '.manufacturing-header{padding: 12px 15px; border-bottom: 1px solid var(--border-color); display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;}'
            + '.manufacturing-title{font-weight: 600; font-size: var(--text-md);}'
            + '.manufacturing-subtitle{font-size: 12px; color: var(--text-muted); margin-top: 4px;}'
            + '.manufacturing-actions{display: flex; flex-direction: column; align-items: flex-end; gap: 6px; flex-shrink: 0;}'
            + '.manufacturing-link{white-space: nowrap;}'
            + '.manufacturing-body{padding: 15px;}'
            + '.manufacturing-grid{display: flex; gap: 12px; margin-bottom: 18px; flex-wrap: wrap;}'
            + '.manufacturing-summary-card{flex: 1; min-width: 120px; border: 1px solid var(--border-color); border-radius: var(--border-radius); overflow: hidden; background-color: var(--card-bg);}'
            + '.manufacturing-summary-label{background: var(--bg-light-gray); padding: 6px 12px; font-size: 11px; color: var(--text-muted); font-weight: 600; border-bottom: 1px solid var(--border-color);}'
            + '.manufacturing-summary-value{padding: 10px 12px; font-size: 20px; font-weight: 700; line-height: 1.3;}'
            + '.manufacturing-progress-box{margin-bottom: 16px; padding: 10px 12px; border: 1px solid var(--border-color); border-radius: var(--border-radius); background: var(--bg-light-gray); display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap;}'
            + '.manufacturing-progress-label{font-size: 11px; color: var(--text-muted); font-weight: 600;}'
            + '.manufacturing-progress-value{font-size: 14px; font-weight: 600; color: var(--text-color);}'
            + '.manufacturing-sections-title{margin-bottom: 8px; font-weight: 600; font-size: 11px; color: var(--text-muted);}'
            + '.manufacturing-sections{display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 16px;}'
            + '.manufacturing-section-box{border: 1px solid var(--border-color); border-radius: var(--border-radius); background-color: var(--card-bg); overflow: hidden;}'
            + '.manufacturing-section-head{padding: 12px 14px; border-bottom: 1px solid var(--border-color); display: flex; justify-content: space-between; align-items: center; gap: 8px;}'
            + '.manufacturing-section-title{font-weight: 600; font-size: var(--text-sm);}'
            + '.manufacturing-scroll-box{max-height: 360px; overflow-y: auto;}'
            + '.manufacturing-table{width: 100%; border-collapse: collapse; font-size: var(--text-sm); margin-bottom: 0;}'
            + '.manufacturing-table thead{position: sticky; top: 0; z-index: 1;}'
            + '.manufacturing-table tr{background: var(--card-bg);}'
            + '.manufacturing-table th{padding: 8px 12px; text-align: right; font-weight: 600; font-size: 11px; color: var(--text-muted); border-bottom: 1px solid var(--border-color); background: var(--bg-light-gray); white-space: nowrap;}'
            + '@media (max-width: 767px){'
            +   '.manufacturing-shell{border-radius: 12px;}'
            +   '.manufacturing-header{align-items: flex-start;}'
            +   '.manufacturing-actions{align-items: stretch; width: 100%;}'
            +   '.manufacturing-link{width: 100%; text-align: center;}'
            +   '.manufacturing-sections{grid-template-columns: 1fr;}'
            +   '.manufacturing-table{font-size: 12px;}'
            + '}'
            + '</style>'
            + '<div class="manufacturing-dashboard">'
            +   '<div class="manufacturing-shell">'
            +   '<div class="manufacturing-header">'
            +     '<div>'
            +       '<div class="manufacturing-title">لوحة التصنيع: ' + requestName + '</div>'
            +       '<div class="manufacturing-subtitle">عرض سريع لما تم تصنيعه وما تبقى داخل هذا الطلب</div>'
            +     '</div>'
            +     '<div class="manufacturing-actions">'
            +       '<a href="' + manufacturingOpenFactoryLink(frm.doc.name) + '" target="_blank" class="btn btn-xs btn-default manufacturing-link">فتح شاشة التصنيع</a>'
            +       '<div class="indicator-pill ' + status.indicatorClass + '" style="white-space: nowrap;">حالة التصنيع: ' + manufacturingEscapeHtml(status.text) + '</div>'
            +       '<div class="indicator-pill ' + remainingTone + '" style="white-space: nowrap;">المتبقي: ' + manufacturingEscapeHtml(remaining) + '</div>'
            +     '</div>'
            +   '</div>'
            +   '<div class="manufacturing-body">'
            +   '<div class="manufacturing-grid">'
            +     manufacturingSummaryCard('المتبقي', remaining, 'var(--yellow-700, #b7791f)')
            +     manufacturingSummaryCard('تم تصنيعه', manufactured, 'var(--green-700, #15803d)')
            +     manufacturingSummaryCard('الإجمالي', total, 'var(--text-color)')
            +   '</div>'
            +   '<div class="manufacturing-progress-box">'
            +     '<div class="manufacturing-progress-label">نسبة الإنجاز</div>'
            +     '<div class="manufacturing-progress-value">' + manufacturingEscapeHtml(percent) + '%</div>'
            +   '</div>';

        if (!total) {
            html += '<div style="text-align:center; padding: 15px; color: var(--text-muted); border: 1px solid var(--border-color); border-radius: var(--border-radius);">لا توجد أصناف تصنيع متتبعة داخل هذا الطلب حتى الآن.</div>';
        } else {
            html += ''
                + '<div class="manufacturing-sections-title">تفاصيل التصنيع</div>'
                + '<div class="manufacturing-sections">'
                +   manufacturingSection('المتبقي للتصنيع', remaining, data.pending_items || [], 'pending', 'لا توجد أصناف متبقية للتصنيع', data)
                +   manufacturingSection('تم تصنيعه', manufactured, data.items || [], 'done', 'لا توجد أصناف مصنعة حتى الآن', data)
                + '</div>';
        }

        html += '</div></div></div>';
        field.$wrapper.html(html);
    }

    function renderManufacturingLoading(frm, text, tone) {
        var field = frm.fields_dict.custom_manufacturing_dashboard;
        if (!field || !field.$wrapper) return;

        var palette = tone || {
            background: 'var(--card-bg)',
            border: 'var(--border-color)',
            color: 'var(--text-muted)'
        };

        field.$wrapper.html(
            '<div style="padding:16px 18px;border-radius:14px;border:1px solid ' + palette.border + ';background:' + palette.background + ';color:' + palette.color + ';">'
            + manufacturingEscapeHtml(text)
            + '</div>'
        );
    }

    function refreshManufacturingTab(frm) {
        if (!frm.fields_dict.custom_manufacturing_dashboard) return;

        if (frm.is_new() || !frm.doc.name) {
            renderManufacturingLoading(frm, 'احفظ طلب المواد أولًا حتى تظهر لوحة التصنيع.');
            return;
        }

        renderManufacturingLoading(frm, 'جاري تحميل بيانات التصنيع...');

        frappe.call({
            method: 'get_manufactured_items',
            args: {
                mr: frm.doc.name
            },
            freeze: false,
            callback: function(response) {
                if (!response || !response.message) {
                    renderManufacturingLoading(frm, 'تعذر تحميل بيانات التصنيع.', {
                        background: '#fef2f2',
                        border: '#fecaca',
                        color: '#991b1b'
                    });
                    return;
                }
                renderManufacturingDashboard(frm, response.message);
            },
            error: function() {
                renderManufacturingLoading(frm, 'تعذر تحميل بيانات التصنيع.', {
                    background: '#fef2f2',
                    border: '#fecaca',
                    color: '#991b1b'
                });
            }
        });
    }

    frappe.ui.form.on('Material Request', {
        refresh: function(frm) {
            refreshManufacturingTab(frm);
        }
    });
    // END legacy Client Script: Material Request Manufacturing Tab
  }
  if (!window.__namar_test_loaded_scripts["Material Request Replacement Reference Filter"]) {
    window.__namar_test_loaded_scripts["Material Request Replacement Reference Filter"] = true;
    // BEGIN legacy Client Script: Material Request Replacement Reference Filter
    function set_replacement_reference_query(frm) {
        frm.set_query('custom_reference_material_request', function() {
            if (!frm.doc.sales_order) {
                return {
                    filters: [
                        ['Material Request', 'name', '=', '__no_matching_material_request__']
                    ]
                };
            }

            return {
                filters: [
                    ['Material Request', 'sales_order', '=', frm.doc.sales_order],
                    ['Material Request', 'docstatus', '<', 2],
                    ['Material Request', 'name', '!=', frm.doc.name || '']
                ]
            };
        });
    }

    function clear_replacement_reference_if_needed(frm) {
        if (!frm.doc.custom_reference_material_request) return;

        if (frm.doc.custom_request_kind !== 'استبدال' || !frm.doc.sales_order) {
            frm.set_value('custom_reference_material_request', '');
        }
    }

    function clear_replacement_reference_on_sales_order_change(frm) {
        if (!frm.doc.custom_reference_material_request) return;
        frm.set_value('custom_reference_material_request', '');
    }

    frappe.ui.form.on('Material Request', {
        setup: function(frm) {
            set_replacement_reference_query(frm);
        },
        refresh: function(frm) {
            set_replacement_reference_query(frm);
        },
        sales_order: function(frm) {
            clear_replacement_reference_on_sales_order_change(frm);
            set_replacement_reference_query(frm);
        },
        custom_request_kind: function(frm) {
            clear_replacement_reference_if_needed(frm);
            set_replacement_reference_query(frm);
        }
    });
    // END legacy Client Script: Material Request Replacement Reference Filter
  }
  if (!window.__namar_test_loaded_scripts["Material Request Scenario Bypass"]) {
    window.__namar_test_loaded_scripts["Material Request Scenario Bypass"] = true;
    // BEGIN legacy Client Script: Material Request Scenario Bypass
    frappe.ui.form.on('Material Request', {
        refresh: function(frm) {
            install_material_request_scenario_bypass_hook();
            setup_material_request_scenario_reference_query(frm);
            add_material_request_scenario_buttons(frm);
        },
        custom_request_scenario: function(frm) {
            install_material_request_scenario_bypass_hook();
            setup_material_request_scenario_reference_query(frm);
            clear_material_request_scenario_fields_when_needed(frm);
        },
        sales_order: function(frm) {
            setup_material_request_scenario_reference_query(frm);
        },
        custom_scenario_reference: function(frm) {
            apply_material_request_reference_data(frm);
        }
    });

    var MATERIAL_REQUEST_REFERENCE_SCENARIOS = ['استبدال', 'نواقص'];
    var MATERIAL_REQUEST_SCENARIO_COPY_FIELDS = [
        'material_request_type',
        'company',
        'transaction_date',
        'schedule_date',
        'sales_order',
        'customer',
        'customer_name',
        'territory',
        'branch',
        'الفرع',
        'custom_google_map',
        'custom_installation_note_teams',
        'custom_driver',
        'sales_invoice',
        'set_warehouse',
        'warehouse'
    ];

    function setup_material_request_scenario_reference_query(frm) {
        if (!frm || !frm.set_query || !frm.fields_dict || !frm.fields_dict.custom_scenario_reference) {
            return;
        }

        frm.set_query('custom_scenario_reference', function() {
            var filters = {
                docstatus: ['!=', 2]
            };

            if (frm.doc.name && !frm.doc.__islocal) {
                filters.name = ['!=', frm.doc.name];
            }

            if (frm.doc.sales_order) {
                filters.sales_order = frm.doc.sales_order;
            } else {
                filters.name = ['=', '__no_sales_order__'];
            }

            return { filters: filters };
        });
    }

    function add_material_request_scenario_buttons(frm) {
        if (!frm || !frm.doc || frm.doc.__islocal) {
            return;
        }

        frm.add_custom_button(__('إنشاء طلب استبدال'), function() {
            create_material_request_from_current(frm, 'استبدال');
        }, __('سيناريو طلب المواد'));

        frm.add_custom_button(__('إنشاء طلب نواقص'), function() {
            create_material_request_from_current(frm, 'نواقص');
        }, __('سيناريو طلب المواد'));
    }

    function clear_material_request_scenario_fields_when_needed(frm) {
        if (!frm || !frm.doc) {
            return;
        }

        var scenario = (frm.doc.custom_request_scenario || 'تصنيع').trim();
        if (MATERIAL_REQUEST_REFERENCE_SCENARIOS.indexOf(scenario) === -1) {
            var values = {};
            if (frm.fields_dict.custom_scenario_reference) {
                values.custom_scenario_reference = '';
            }
            if (frm.fields_dict.custom_return_status) {
                values.custom_return_status = '';
            }
            if (frm.fields_dict.custom_request_reason) {
                values.custom_request_reason = '';
            }
            if (Object.keys(values).length) {
                frm.set_value(values);
            }
            return;
        }

        if (scenario !== 'استبدال' && frm.fields_dict.custom_return_status && frm.doc.custom_return_status) {
            frm.set_value('custom_return_status', '');
        }
    }

    function apply_material_request_reference_data(frm) {
        if (!frm || !frm.doc || !frm.doc.custom_scenario_reference) {
            return;
        }

        var scenario = (frm.doc.custom_request_scenario || 'تصنيع').trim();
        if (MATERIAL_REQUEST_REFERENCE_SCENARIOS.indexOf(scenario) === -1) {
            return;
        }

        if (frm.doc.custom_scenario_reference === frm.doc.name) {
            frappe.msgprint(__('لا يمكن ربط الطلب بنفسه.'));
            frm.set_value('custom_scenario_reference', '');
            return;
        }

        load_material_request_doc(frm.doc.custom_scenario_reference).then(function(source_doc) {
            copy_material_request_reference_fields_to_form(frm, source_doc);
        });
    }

    function load_material_request_doc(material_request) {
        return new Promise(function(resolve, reject) {
            frappe.call({
                method: 'frappe.client.get',
                args: {
                    doctype: 'Material Request',
                    name: material_request
                },
                callback: function(response) {
                    resolve(response.message || {});
                },
                error: function(error) {
                    reject(error);
                }
            });
        });
    }

    function copy_material_request_reference_fields_to_form(frm, source_doc) {
        if (!frm || !source_doc) {
            return;
        }

        var values = {};
        MATERIAL_REQUEST_SCENARIO_COPY_FIELDS.forEach(function(fieldname) {
            if (!can_copy_material_request_field(frm, fieldname)) {
                return;
            }
            if (source_doc[fieldname] !== undefined) {
                values[fieldname] = source_doc[fieldname];
            }
        });

        if (!Object.keys(values).length) {
            return;
        }

        frm.set_value(values).then(function() {
            frappe.show_alert({
                message: __('تم نسخ بيانات الطلب المرتبط.'),
                indicator: 'green'
            });
        });
    }

    function can_copy_material_request_field(frm, fieldname) {
        if (!frm || !frm.fields_dict || !frm.fields_dict[fieldname]) {
            return false;
        }

        if (['custom_request_scenario', 'custom_scenario_reference', 'custom_return_status', 'custom_request_reason'].indexOf(fieldname) !== -1) {
            return false;
        }

        var df = frm.fields_dict[fieldname].df || {};
        if (frm.doc.docstatus === 1 && !df.allow_on_submit) {
            return false;
        }

        return true;
    }

    function create_material_request_from_current(frm, scenario) {
        if (!frm || !frm.doc || frm.doc.__islocal) {
            frappe.msgprint(__('احفظ الطلب أولًا قبل إنشاء طلب مرتبط.'));
            return;
        }

        frappe.model.with_doctype('Material Request', function() {
            var new_doc = frappe.model.get_new_doc('Material Request');
            copy_material_request_fields_to_new_doc(new_doc, frm.doc);
            new_doc.custom_request_scenario = scenario;
            new_doc.custom_scenario_reference = frm.doc.name;
            if (scenario === 'استبدال') {
                new_doc.custom_return_status = 'بانتظار الاسترجاع';
            }
            frappe.set_route('Form', 'Material Request', new_doc.name);
        });
    }

    function copy_material_request_fields_to_new_doc(target_doc, source_doc) {
        if (!target_doc || !source_doc) {
            return;
        }

        MATERIAL_REQUEST_SCENARIO_COPY_FIELDS.forEach(function(fieldname) {
            if (!material_request_has_meta_field(fieldname)) {
                return;
            }
            if (source_doc[fieldname] !== undefined) {
                target_doc[fieldname] = source_doc[fieldname];
            }
        });
    }

    function material_request_has_meta_field(fieldname) {
        if (!fieldname || typeof frappe === 'undefined') {
            return false;
        }

        if (frappe.meta && typeof frappe.meta.get_docfield === 'function') {
            return Boolean(frappe.meta.get_docfield('Material Request', fieldname));
        }

        var meta = frappe.get_meta && frappe.get_meta('Material Request');
        if (!meta) {
            return false;
        }

        if (typeof meta.get_field === 'function') {
            return Boolean(meta.get_field(fieldname));
        }

        return Boolean((meta.fields || []).some(function(field) {
            return field.fieldname === fieldname;
        }));
    }

    function install_material_request_scenario_bypass_hook() {
        if (!window.frappe || !frappe.xcall) {
            setTimeout(install_material_request_scenario_bypass_hook, 300);
            return;
        }

        if (frappe.__material_request_scenario_bypass_xcall_patched) {
            return;
        }

        var original_xcall = frappe.xcall;
        frappe.__material_request_scenario_bypass_xcall_patched = true;
        frappe.__material_request_scenario_bypass_original_xcall = original_xcall;

        frappe.xcall = function(method, params) {
            var context = this;
            var original_arguments = arguments;

            if (!should_check_material_request_scenario_bypass(method, params)) {
                return original_xcall.apply(context, original_arguments);
            }

            var doc = params.doc;
            var action = (params.action || '').trim();

            return get_matching_material_request_scenario_bypass_rule(doc.name, action)
                .then(function(rule) {
                    if (!rule) {
                        return original_xcall.apply(context, original_arguments);
                    }

                    return run_material_request_scenario_bypass(doc.name, rule);
                });
        };
    }

    function should_check_material_request_scenario_bypass(method, params) {
        if (method !== 'frappe.model.workflow.apply_workflow') {
            return false;
        }

        if (!params || !params.doc || !params.action) {
            return false;
        }

        var doc = params.doc;
        if (doc.doctype !== 'Material Request' || !doc.name || doc.__islocal) {
            return false;
        }

        var scenario = (doc.custom_request_scenario || 'تصنيع').trim();
        return Boolean(scenario && scenario !== 'تصنيع');
    }

    function get_matching_material_request_scenario_bypass_rule(material_request, action) {
        return new Promise(function(resolve, reject) {
            frappe.call({
                method: 'get_material_request_scenario_bypass_rules',
                args: {
                    material_request: material_request
                },
                callback: function(response) {
                    var rules = response.message || [];
                    resolve(find_material_request_scenario_bypass_rule(rules, action));
                },
                error: function(error) {
                    reject(error);
                }
            });
        });
    }

    function find_material_request_scenario_bypass_rule(rules, action) {
        var requested_action = (action || '').trim();
        if (!requested_action) {
            return null;
        }

        for (var i = 0; i < rules.length; i++) {
            var rule_action = (rules[i].skipped_action || '').trim();
            if (rule_action && rule_action === requested_action) {
                return rules[i];
            }
        }

        return null;
    }

    function run_material_request_scenario_bypass(material_request, rule) {
        return new Promise(function(resolve, reject) {
            frappe.call({
                method: 'apply_material_request_scenario_bypass',
                args: {
                    material_request: material_request,
                    rule_name: rule.name
                },
                freeze: true,
                freeze_message: __('جاري تطبيق تجاوز السيناريو...'),
                callback: function(response) {
                    fetch_updated_material_request_after_bypass(material_request)
                        .then(function(updated_doc) {
                            var result = response.message || {};
                            frappe.show_alert({
                                message: __('تم تطبيق التجاوز إلى: ') + (result.new_state || rule.target_state || '-'),
                                indicator: 'green'
                            });
                            resolve(updated_doc || result);
                        })
                        .catch(reject);
                },
                error: function(error) {
                    reject(error);
                }
            });
        });
    }

    function fetch_updated_material_request_after_bypass(material_request) {
        return new Promise(function(resolve, reject) {
            frappe.call({
                method: 'frappe.client.get',
                args: {
                    doctype: 'Material Request',
                    name: material_request
                },
                callback: function(response) {
                    var updated_doc = response.message;
                    if (updated_doc) {
                        frappe.model.sync(updated_doc);
                    }
                    resolve(updated_doc);
                },
                error: function(error) {
                    reject(error);
                }
            });
        });
    }
    // END legacy Client Script: Material Request Scenario Bypass
  }
  if (!window.__namar_test_loaded_scripts["Total Quantity"]) {
    window.__namar_test_loaded_scripts["Total Quantity"] = true;
    // BEGIN legacy Client Script: Total Quantity
    frappe.ui.form.on('Material Request', {
        refresh: function(frm) {
            calculate_total(frm);
        },
        validate: function(frm) {
            calculate_total(frm);
        }
    });

    frappe.ui.form.on('Material Request Item', {
        qty: function(frm) {
            calculate_total(frm);
        },
        items_remove: function(frm) {
            calculate_total(frm);
        }
    });

    function calculate_total(frm) {
        if (!frm.fields_dict['custom_total_quantity']) return;
        var total = 0;
        (frm.doc.items || []).forEach(function(d) { total += flt(d.qty); });
        if (frm.doc.custom_total_quantity !== total) {
            frm.doc.custom_total_quantity = total;
            frm.refresh_field('custom_total_quantity');
        }
    }
    // END legacy Client Script: Total Quantity
  }
  if (!window.__namar_test_loaded_scripts["Urgent Material Request Banner"]) {
    window.__namar_test_loaded_scripts["Urgent Material Request Banner"] = true;
    // BEGIN legacy Client Script: Urgent Material Request Banner
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
    // END legacy Client Script: Urgent Material Request Banner
  }
  if (!window.__namar_test_loaded_scripts["VIP Customer Highlight"]) {
    window.__namar_test_loaded_scripts["VIP Customer Highlight"] = true;
    // BEGIN legacy Client Script: VIP Customer Highlight
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
    // END legacy Client Script: VIP Customer Highlight
  }
  if (!window.__namar_test_loaded_scripts["حساب التخصيم"]) {
    window.__namar_test_loaded_scripts["حساب التخصيم"] = true;
    // BEGIN legacy Client Script: حساب التخصيم
    var _cutting_rendering = false;
    var _option_labels = null;
    var _comp_sort_map = null;
    var _item_split_support_map = {};
    var _item_sliding_options_map = {};
    var _item_square_count_support_map = {};
    var _item_glass_options_support_map = {};
    var _item_component_exclusion_support_map = {};
    var _item_frame_component_support_map = {};
    var _store_component_options = [];
    var _frame_component_options = [];
    var _cutting_result_cache = {};
    var _cutting_render_timer = null;
    var _cutting_pending_render = false;
    var _cutting_applying_results = false;
    var _cutting_cache_version = "row-cache-v4";
    var _component_exclusion_child_doctype = "Material Request Item Excluded Store Component";
    var _component_exclusion_meta_loaded = false;
    var _component_exclusion_syncing = false;

    function schedule_render_cutting(frm, delay_ms) {
        if (_cutting_render_timer) {
            clearTimeout(_cutting_render_timer);
        }
        _cutting_render_timer = setTimeout(function() {
            _cutting_render_timer = null;
            render_all_cutting(frm);
        }, delay_ms === undefined ? 250 : delay_ms);
    }

    function request_cutting_render(frm) {
        if (_cutting_applying_results) {
            return;
        }
        if (_cutting_rendering) {
            _cutting_pending_render = true;
            return;
        }
        schedule_render_cutting(frm);
    }

    function get_cutting_request_key(item) {
        var fixedLeafKey = flt(item.fixed_leaf_w || 0);
        if (Number.isInteger(fixedLeafKey)) {
            fixedLeafKey = fixedLeafKey.toFixed(1);
        } else {
            fixedLeafKey = String(fixedLeafKey);
        }
        return item.item_code + "||" + (item.sliding || "") + "||" + String(item.w) + "||" + String(item.h) + "||" + String(item.ww)
            + "||" + String(item.leaf_count || 1)
            + "||" + (item.split_type || "")
            + "||" + fixedLeafKey
            + "||" + String(item.taksiya_1 || 0)
            + "||" + String(item.taksiya_2 || 0)
            + "||" + String(item.no_qitaat || 0)
            + "||" + String(item.net_leaf || 0)
            + "||" + String(item.parquet || 0)
            + "||" + (item.square_count || "")
            + "||" + (item.glass_type || "")
            + "||" + (item.glass_model || "")
            + "||" + (item.component_exclusion_group || "")
            + "||" + normalize_component_exclusions(item.excluded_components || "")
            + "||" + (item.frame_component || "");
    }

    function get_cutting_cache_key(item) {
        return _cutting_cache_version + "|" + get_cutting_request_key(item);
    }

    function parse_component_exclusions(value) {
        if (!value) return [];
        var raw = value;
        if (Array.isArray(raw)) {
            return raw.map(function(v) {
                if (v && typeof v === "object") {
                    return String(v.store_component || v.component || v.name || "").trim();
                }
                return String(v || "").trim();
            }).filter(Boolean);
        }
        var text = String(raw || "").trim();
        if (!text) return [];
        try {
            var parsed = JSON.parse(text);
            if (Array.isArray(parsed)) {
                return parse_component_exclusions(parsed);
            }
        } catch (e) {
            // MultiSelectPills may store comma-separated text depending on Frappe version.
        }
        return text.replace(/\n/g, ",").replace(/;/g, ",").split(",")
            .map(function(v) { return String(v || "").trim(); })
            .filter(Boolean);
    }

    function clear_component_exclusion_rows(cdt, cdn) {
        var row = locals[cdt] && locals[cdt][cdn];
        if (row) {
            row.custom_excluded_store_components = [];
            row.__component_exclusions_snapshot = "";
        }
    }

    function ensure_component_exclusion_snapshot(cdt, cdn) {
        var row = locals[cdt] && locals[cdt][cdn];
        if (!row) return "";
        if (row.__component_exclusions_snapshot === undefined) {
            row.__component_exclusions_snapshot = normalize_component_exclusions(row.custom_excluded_store_components || []);
        }
        return row.__component_exclusions_snapshot || "";
    }

    function update_component_exclusion_snapshot(cdt, cdn, components) {
        var row = locals[cdt] && locals[cdt][cdn];
        if (row) {
            row.__component_exclusions_snapshot = normalize_component_exclusions(components || "");
        }
    }

    function set_component_exclusion_rows(frm, cdt, cdn, components, options) {
        var row = locals[cdt] && locals[cdt][cdn];
        if (!row) return;
        var opts = options || {};
        var normalized = normalize_component_exclusions(components || "").split(",").filter(Boolean);
        var current = normalize_component_exclusions(row.custom_excluded_store_components || []);
        if (current === normalized.join(",")) {
            update_component_exclusion_snapshot(cdt, cdn, normalized);
            return;
        }
        _component_exclusion_syncing = true;
        try {
            row.custom_excluded_store_components = [];
            normalized.forEach(function(component) {
                var child = frappe.model.add_child(row, "Material Request Item Excluded Store Component", "custom_excluded_store_components");
                child.store_component = component;
            });
            update_component_exclusion_snapshot(cdt, cdn, normalized);
            if (!opts.skip_refresh) {
                frm.refresh_field("items");
            }
        } finally {
            _component_exclusion_syncing = false;
        }
        if (!opts.skip_render) {
            request_cutting_render(frm);
        }
    }

    function merge_component_exclusion_rows(frm, cdt, cdn, components) {
        var row = locals[cdt] && locals[cdt][cdn];
        if (!row) return;
        var current = parse_component_exclusions(row.custom_excluded_store_components || []);
        var incoming = parse_component_exclusions(components || []);
        set_component_exclusion_rows(frm, cdt, cdn, current.concat(incoming));
    }

    function apply_component_exclusion_group(frm, cdt, cdn) {
        var row = locals[cdt] && locals[cdt][cdn];
        var group = row ? String(row.custom_component_exclusion_group || "").trim() : "";
        if (!group) {
            clear_component_exclusion_rows(cdt, cdn);
            if (frm) frm.refresh_field("items");
            request_cutting_render(frm);
            return;
        }
        frappe.call({
            method: "frappe.client.get",
            args: {
                doctype: "Store Component Exclusion Group",
                name: group
            },
            callback: function(r) {
                var components = [];
                ((r.message && r.message.components) || []).forEach(function(child) {
                    if (child.store_component) {
                        components.push(child.store_component);
                    }
                });
                set_component_exclusion_rows(frm, cdt, cdn, components);
            }
        });
    }

    function reconcile_component_exclusion_change(frm, cdt, cdn) {
        if (_component_exclusion_syncing) return;
        var row = locals[cdt] && locals[cdt][cdn];
        if (!row) return;
        apply_component_exclusion_group(frm, cdt, cdn);
    }

    function normalize_component_exclusions(value) {
        var seen = {};
        return parse_component_exclusions(value).filter(function(component) {
            if (seen[component]) return false;
            seen[component] = true;
            return true;
        }).sort().join(",");
    }

    function get_sliding_query_for_row(cdn) {
        var opts = _item_sliding_options_map[cdn] || [];
        if (opts.length === 0) {
            return { filters: { name: ["in", ["___"]] } };
        }
        return { filters: { name: ["in", opts] } };
    }

    function apply_sliding_type_query(frm, cdn) {
        var grid = frm.fields_dict.items && frm.fields_dict.items.grid;
        if (!grid) return;

        if (grid.get_field("custom_result_sliding_type")) {
            grid.get_field("custom_result_sliding_type").get_query = function(doc, cdt, child_cdn) {
                return get_sliding_query_for_row(child_cdn);
            };
        }

        var grid_rows = grid.grid_rows || [];
        for (var i = 0; i < grid_rows.length; i++) {
            var grid_row = grid_rows[i];
            if (!grid_row || !grid_row.doc || grid_row.doc.name !== cdn) continue;
            if (!grid_row.grid_form || !grid_row.grid_form.fields_dict) return;

            var control = grid_row.grid_form.fields_dict.custom_result_sliding_type;
            if (!control) return;

            var query = function() {
                return get_sliding_query_for_row(cdn);
            };
            control.get_query = query;
            control.df.get_query = query;
            if (typeof control.refresh === "function") {
                control.refresh();
            }
            return;
        }
    }

    function apply_sliding_type_visibility(frm, cdn) {
        var grid_rows = (frm.fields_dict.items && frm.fields_dict.items.grid && frm.fields_dict.items.grid.grid_rows) || [];
        var grid_row = null;
        for (var i = 0; i < grid_rows.length; i++) {
            if (grid_rows[i] && grid_rows[i].doc && grid_rows[i].doc.name === cdn) {
                grid_row = grid_rows[i];
                break;
            }
        }
        if (!grid_row || !grid_row.grid_form || !grid_row.grid_form.fields_dict) {
            return;
        }
        var control = grid_row.grid_form.fields_dict.custom_result_sliding_type;
        if (!control) return;

        var row_doc = grid_row.doc || {};
        var opts = _item_sliding_options_map[cdn] || [];
        var current_value = String(row_doc.custom_result_sliding_type || "").trim();
        var show = opts.length > 0 || !!current_value;
        control.df.hidden = show ? 0 : 1;
        if (control.wrapper) {
            $(control.wrapper).toggle(show);
            $(control.wrapper).closest(".frappe-control").toggle(show);
        }
        if (typeof control.refresh === "function") {
            control.refresh();
        }
        if (grid_row.grid_form.layout && typeof grid_row.grid_form.layout.refresh_sections === "function") {
            grid_row.grid_form.layout.refresh_sections();
        }
    }

    function is_frame_store_component(component) {
        return _frame_component_options.indexOf(String(component || "").trim()) !== -1;
    }

    function row_has_frame_component(stores) {
        return (stores || []).some(function(store_row) {
            return is_frame_store_component(store_row.component);
        });
    }

    function apply_frame_component_visibility(frm, cdn) {
        var grid_rows = (frm.fields_dict.items && frm.fields_dict.items.grid && frm.fields_dict.items.grid.grid_rows) || [];
        var grid_row = null;
        for (var i = 0; i < grid_rows.length; i++) {
            if (grid_rows[i] && grid_rows[i].doc && grid_rows[i].doc.name === cdn) {
                grid_row = grid_rows[i];
                break;
            }
        }
        if (!grid_row || !grid_row.grid_form || !grid_row.grid_form.fields_dict) {
            return;
        }
        var control = grid_row.grid_form.fields_dict.custom_frame_type;
        if (!control) return;
        var row_doc = grid_row.doc || {};
        var show = !!_item_frame_component_support_map[cdn] || !!String(row_doc.custom_frame_type || "").trim();
        control.df.hidden = show ? 0 : 1;
        if (control.wrapper) {
            $(control.wrapper).toggle(show);
            $(control.wrapper).closest(".frappe-control").toggle(show);
        }
        if (typeof control.refresh === "function") {
            control.refresh();
        }
        if (grid_row.grid_form.layout && typeof grid_row.grid_form.layout.refresh_sections === "function") {
            grid_row.grid_form.layout.refresh_sections();
        }
    }

    function _load_option_labels(callback) {
        if (_option_labels !== null) {
            callback();
            return;
        }
        _option_labels = {};
        _comp_sort_map = {};
        var pending = 2;
        function done() { pending--; if (pending === 0) callback(); }

        frappe.call({
            method: "frappe.client.get_list",
            args: { doctype: "Option", fields: ["name", "label_ar"], limit_page_length: 0 },
            callback: function(r) {
                (r.message || []).forEach(function(o) {
                    if (o.label_ar) _option_labels[o.name] = o.label_ar;
                });
                done();
            }
        });

        frappe.call({
            method: "frappe.client.get_list",
            args: {
                doctype: "Store Component",
                fields: ["name", "component_name", "label_ar", "custom_print_sort_order", "custom_cutting_option_group"],
                order_by: "custom_print_sort_order asc, component_name asc",
                limit_page_length: 0
            },
            callback: function(r) {
                _store_component_options = [];
                _frame_component_options = [];
                (r.message || []).forEach(function(o) {
                    var component_name = o.component_name || o.name || "";
                    var ord = o.custom_print_sort_order;
                    if (component_name && _store_component_options.indexOf(component_name) === -1) {
                        _store_component_options.push(component_name);
                    }
                    if (component_name && String(o.custom_cutting_option_group || "").trim() === "برواز" && _frame_component_options.indexOf(component_name) === -1) {
                        _frame_component_options.push(component_name);
                    }
                    if (ord && component_name) _comp_sort_map[component_name] = ord;
                    if (ord && o.label_ar) _comp_sort_map[o.label_ar] = ord;
                });
                done();
            }
        });
    }

    function has_component_exclusion_link_meta() {
        try {
            var meta = frappe.get_meta && frappe.get_meta(_component_exclusion_child_doctype);
            return !!(meta && (meta.fields || []).some(function(field) {
                return field.fieldtype === "Link";
            }));
        } catch (e) {
            return false;
        }
    }

    function ensure_component_exclusion_meta(callback) {
        if (_component_exclusion_meta_loaded || has_component_exclusion_link_meta()) {
            _component_exclusion_meta_loaded = true;
            if (callback) callback(true);
            return;
        }
        if (!frappe.model || typeof frappe.model.with_doctype !== "function") {
            if (callback) callback(false);
            return;
        }
        frappe.model.with_doctype(_component_exclusion_child_doctype, function() {
            _component_exclusion_meta_loaded = has_component_exclusion_link_meta();
            if (callback) callback(_component_exclusion_meta_loaded);
        });
    }

    function safe_apply_component_exclusion_options(frm) {
        try {
            apply_component_exclusion_options(frm);
        } catch (e) {
            console.warn("تعذر تهيئة حقل استثناء المكونات دون تعطيل نتائج التخصيم", e);
        }
    }

    function update_items_grid_docfield(frm, fieldname, property, value) {
        var grid = frm.fields_dict.items && frm.fields_dict.items.grid;
        if (!grid) return;
        if (typeof grid.update_docfield_property === "function") {
            grid.update_docfield_property(fieldname, property, value);
            return;
        }
        (grid.docfields || []).forEach(function(df) {
            if (df.fieldname === fieldname) {
                df[property] = value;
            }
        });
    }

    function toggle_items_grid_column(frm, fieldname, show) {
        var grid = frm.fields_dict.items && frm.fields_dict.items.grid;
        if (!grid) return;
        if (typeof grid.toggle_column === "function") {
            grid.toggle_column(fieldname, show);
            return;
        }
        update_items_grid_docfield(frm, fieldname, "hidden", show ? 0 : 1);
    }

    frappe.ui.form.on("Material Request", {
        refresh: function(frm) {
            // Move Required By to first position in grid
            var grid = frm.fields_dict.items.grid;
            var fields = grid.grid_rows.length ? grid.grid_rows[0].docfields : [];
            var sd_idx = fields.findIndex(function(f) { return f.fieldname === 'schedule_date'; });
            if (sd_idx > 0) {
                var sd = fields.splice(sd_idx, 1)[0];
                fields.unshift(sd);
            }
            frm.set_query("custom_component_exclusion_group", "items", function() {
                return { filters: { is_active: 1 } };
            });
            grid.refresh();
            render_manual_manufacturing_button(frm);
            _load_option_labels(function() {
                schedule_render_cutting(frm, 0);
                ensure_component_exclusion_meta(function() {
                    safe_apply_component_exclusion_options(frm);
                });
            });
        }
    });

    function get_pending_manufacturing_rows(frm) {
        return sort_manufacturing_rows((frm.doc.items || []).filter(function(row) {
            return row.item_code && is_trackable_manufacturing_row(row) && get_manufacturing_remaining_qty(row) > 0;
        }));
    }

    function get_selected_manufacturing_rows(dialog) {
        var selected = [];
        var htmlField = dialog && dialog.fields_dict ? dialog.fields_dict.rows_html : null;
        if (htmlField && htmlField.$wrapper && htmlField.$wrapper.length) {
            selected = htmlField.$wrapper
                .find(".manual-manufacturing-row-checkbox:checked")
                .map(function() {
                    var idx = String($(this).val() || "").trim();
                    var qtyInput = htmlField.$wrapper.find('.manual-manufacturing-row-qty[data-row-idx="' + idx + '"]');
                    var qty = qtyInput.length ? flt(qtyInput.val() || 1) : 1;
                    if (qty <= 0) qty = 1;
                    return { idx: idx, qty: qty };
                })
                .get();
        }

        if (!Array.isArray(selected)) {
            if (selected && typeof selected === "object") {
                selected = Object.keys(selected).filter(function(key) { return !!selected[key]; });
            } else if (selected) {
                selected = [selected];
            } else {
                selected = [];
            }
        }

        return selected.map(function(value) {
            if (value && typeof value === "object") {
                return {
                    idx: String(value.idx || value.row || "").trim(),
                    qty: flt(value.qty || 1) || 1
                };
            }
            return {
                idx: String(value || "").trim(),
                qty: 1
            };
        }).filter(function(value) {
            return !!value.idx;
        });
    }

    function escape_html_text(value) {
        var text = String(value == null ? "" : value);
        return text
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    function format_manufacturing_count(value) {
        var number = flt(value || 0);
        if (Math.abs(number - Math.round(number)) < 0.000001) {
            return String(Math.round(number));
        }
        return String(Math.round(number * 1000) / 1000);
    }

    function get_manufacturing_row_qty(row) {
        var qty = flt(row.qty || 0);
        return qty > 0 ? qty : 1;
    }

    function get_manufacturing_done_qty(row) {
        var rowQty = get_manufacturing_row_qty(row);
        var doneQty = flt(row.custom_manufactured_qty || 0);
        if (doneQty <= 0 && (parseInt(row.custom_is_manufactured, 10) || 0)) {
            doneQty = rowQty;
        }
        if (doneQty < 0) doneQty = 0;
        if (doneQty > rowQty) doneQty = rowQty;
        return doneQty;
    }

    function get_manufacturing_remaining_qty(row) {
        var remaining = get_manufacturing_row_qty(row) - get_manufacturing_done_qty(row);
        return remaining > 0 ? remaining : 0;
    }

    function sum_manufacturing_qty(rows, mode) {
        return (rows || []).reduce(function(total, row) {
            if (mode === "done") {
                return total + get_manufacturing_done_qty(row);
            }
            if (mode === "remaining") {
                return total + get_manufacturing_remaining_qty(row);
            }
            return total + get_manufacturing_row_qty(row);
        }, 0);
    }

    function get_completed_manufacturing_rows(frm) {
        return sort_manufacturing_rows((frm.doc.items || []).filter(function(row) {
            return row.item_code && is_trackable_manufacturing_row(row) && get_manufacturing_done_qty(row) > 0;
        }));
    }

    function sort_manufacturing_rows(rows) {
        return (rows || []).slice().sort(function(a, b) {
            var aIdx = parseInt(a.idx, 10);
            var bIdx = parseInt(b.idx, 10);
            if (isNaN(aIdx)) aIdx = 999999;
            if (isNaN(bIdx)) bIdx = 999999;
            if (aIdx !== bIdx) return aIdx - bIdx;
            return String(a.item_code || "").localeCompare(String(b.item_code || ""));
        });
    }

    function render_manual_selection_rows_html(rows) {
        if (!rows || !rows.length) {
            return '<div style="padding:16px; color:var(--text-muted);">كل السطور مسجلة كمصنعة بالفعل.</div>';
        }

        return rows.map(function(row) {
            var rowQty = get_manufacturing_row_qty(row);
            var doneQty = get_manufacturing_done_qty(row);
            var remainingQty = get_manufacturing_remaining_qty(row);
            return ''
                + '<div style="display:grid; grid-template-columns:24px 70px 1fr 118px; align-items:center; gap:10px; padding:8px 0;">'
                +   '<input type="checkbox" class="manual-manufacturing-row-checkbox" value="' + escape_html_text(row.idx) + '" style="width:18px; height:18px;">'
                +   '<span style="font-weight:700; white-space:nowrap;">#' + escape_html_text(row.idx) + '</span>'
                +   '<div style="min-width:0;">'
                +       '<div style="font-weight:700;">' + escape_html_text(row.item_code || "") + ' | ' + escape_html_text(row.item_name || row.item_code || "") + '</div>'
                +       '<div style="color:var(--text-muted); font-size:12px; margin-top:3px;">الكمية: ' + format_manufacturing_count(rowQty) + ' | مصنع: ' + format_manufacturing_count(doneQty) + ' | المتبقي: ' + format_manufacturing_count(remainingQty) + '</div>'
                +   '</div>'
                +   '<input type="number" min="1" max="' + escape_html_text(format_manufacturing_count(remainingQty)) + '" step="1" value="1" data-row-idx="' + escape_html_text(row.idx) + '" class="manual-manufacturing-row-qty form-control" style="height:32px; text-align:center;">'
                + '</div>';
        }).join("");
    }

    function is_trackable_manufacturing_row(row) {
        var leafW = flt(row.custom_result_leaf_w || 0);
        var leafH = flt(row.custom_result_leaf_h || 0);
        var panelW = flt(row.custom_result_panel_w || 0);
        var panelH = flt(row.custom_result_panel_h || 0);
        return leafW > 0
            || leafH > 0
            || panelW > 0
            || panelH > 0
            || has_cutting_display_text(row.custom_result_leaf_w_text)
            || has_cutting_display_text(row.custom_result_panel_w_text);
    }

    function normalize_manufacturing_request_name(name) {
        return String(name || "").replace(/^MREQ-/, "");
    }

    function format_manufacturing_datetime(value) {
        if (!value) return "-";
        try {
            return frappe.datetime.str_to_user(value);
        } catch (e) {
            return value;
        }
    }

    function render_manufacturing_stat_card(label, value, tone) {
        var colors = {
            amber: { border: "rgba(245, 158, 11, 0.18)", value: "#f59e0b" },
            green: { border: "rgba(34, 197, 94, 0.18)", value: "#22c55e" },
            default: { border: "rgba(148, 163, 184, 0.18)", value: "var(--text-color)" }
        };
        var palette = colors[tone] || colors.default;
        return ''
            + '<div style="flex:1 1 200px; min-width:180px; border:1px solid ' + palette.border + '; border-radius:14px; overflow:hidden; background:var(--card-bg);">'
            +   '<div style="padding:10px 14px; background:var(--control-bg); color:var(--text-muted); font-size:13px; font-weight:700;">' + escape_html_text(label) + '</div>'
            +   '<div style="padding:18px 14px; font-size:36px; font-weight:900; color:' + palette.value + ';">' + value + '</div>'
            + '</div>';
    }

    function render_manufacturing_table_html(title, rows, emptyText, tone, isCompleted) {
        var badgeBg = tone === "green" ? "rgba(34, 197, 94, 0.14)" : "rgba(245, 158, 11, 0.14)";
        var badgeColor = tone === "green" ? "#22c55e" : "#f59e0b";
        var badgeQty = format_manufacturing_count(sum_manufacturing_qty(rows, isCompleted ? "done" : "remaining"));
        var rowsHtml = "";

        if (rows.length) {
            rowsHtml = rows.map(function(row) {
                var rowQty = get_manufacturing_row_qty(row);
                var doneQty = get_manufacturing_done_qty(row);
                var remainingQty = get_manufacturing_remaining_qty(row);
                var qtyText = isCompleted
                    ? format_manufacturing_count(doneQty) + " / " + format_manufacturing_count(rowQty)
                    : format_manufacturing_count(remainingQty) + " / " + format_manufacturing_count(rowQty);
                var statusPill = isCompleted
                    ? '<span style="display:inline-flex; align-items:center; gap:6px; padding:4px 10px; border-radius:999px; background:rgba(34,197,94,0.14); color:#22c55e; font-size:12px; font-weight:800;">تم تصنيعه</span>'
                    : '<span style="display:inline-flex; align-items:center; gap:6px; padding:4px 10px; border-radius:999px; background:rgba(245,158,11,0.14); color:#f59e0b; font-size:12px; font-weight:800;">متبقي</span>';
                var byText = isCompleted ? escape_html_text(row.custom_manufactured_by || "-") : "-";
                var timeText = isCompleted ? format_manufacturing_datetime(row.custom_manufactured_at) : "بانتظار التصنيع";
                return ''
                    + '<tr>'
                    +   '<td style="padding:12px 10px; border-bottom:1px solid var(--border-color); white-space:nowrap;">' + byText + '</td>'
                    +   '<td style="padding:12px 10px; border-bottom:1px solid var(--border-color);">'
                    +     statusPill
                    +     '<div style="margin-top:6px; color:var(--text-muted); font-size:12px;">' + escape_html_text(timeText) + '</div>'
                    +   '</td>'
                    +   '<td style="padding:12px 10px; border-bottom:1px solid var(--border-color); font-weight:900; white-space:nowrap;">' + escape_html_text(qtyText) + '</td>'
                    +   '<td style="padding:12px 10px; border-bottom:1px solid var(--border-color); font-weight:700;">' + escape_html_text(row.item_code || "-") + '</td>'
                    +   '<td style="padding:12px 10px; border-bottom:1px solid var(--border-color); min-width:220px;">' + escape_html_text(row.item_name || row.item_code || "-") + '</td>'
                    +   '<td style="padding:12px 10px; border-bottom:1px solid var(--border-color); font-weight:900; text-align:center;">' + escape_html_text(row.idx) + '</td>'
                    + '</tr>';
            }).join("");
        } else {
            rowsHtml = '<tr><td colspan="6" style="padding:18px 12px; color:var(--text-muted); text-align:center;">' + escape_html_text(emptyText) + '</td></tr>';
        }

        return ''
            + '<div style="flex:1 1 480px; min-width:380px; border:1px solid var(--border-color); border-radius:14px; overflow:hidden; background:var(--card-bg);">'
            +   '<div style="display:flex; justify-content:space-between; align-items:center; gap:8px; margin-bottom:10px;">'
            +     '<div style="padding:12px 14px; font-weight:800;">' + escape_html_text(title) + '</div>'
            +     '<span style="margin:12px; padding:3px 10px; border-radius:999px; background:' + badgeBg + '; color:' + badgeColor + '; font-size:12px; font-weight:800;">' + badgeQty + '</span>'
            +   '</div>'
            +   '<div style="padding:0 12px 12px 12px;">'
            +     '<div style="max-height:420px; overflow:auto; border:1px solid var(--border-color); border-radius:12px;">'
            +       '<table style="width:100%; border-collapse:collapse;">'
            +         '<thead style="position:sticky; top:0; background:var(--control-bg); z-index:1;">'
            +           '<tr>'
            +             '<th style="padding:12px 10px; text-align:right; color:var(--text-muted); font-size:13px;">بواسطة</th>'
            +             '<th style="padding:12px 10px; text-align:right; color:var(--text-muted); font-size:13px;">الحالة</th>'
            +             '<th style="padding:12px 10px; text-align:right; color:var(--text-muted); font-size:13px;">الكمية</th>'
            +             '<th style="padding:12px 10px; text-align:right; color:var(--text-muted); font-size:13px;">الكود</th>'
            +             '<th style="padding:12px 10px; text-align:right; color:var(--text-muted); font-size:13px;">الصنف</th>'
            +             '<th style="padding:12px 10px; text-align:center; color:var(--text-muted); font-size:13px;">رقم الباب</th>'
            +           '</tr>'
            +         '</thead>'
            +         '<tbody>' + rowsHtml + '</tbody>'
            +       '</table>'
            +     '</div>'
            +   '</div>'
            + '</div>';
    }

    function get_manufacturing_detail_rows(frm, lineType) {
        var rows = frm.__manufacturing_details || frm.doc.custom_manufacturing_details || [];
        return (rows || []).filter(function(row) {
            return row.line_type === lineType;
        }).slice().sort(function(a, b) {
            var aIdx = parseInt(a.material_request_row || 0, 10);
            var bIdx = parseInt(b.material_request_row || 0, 10);
            if (isNaN(aIdx)) aIdx = 999999;
            if (isNaN(bIdx)) bIdx = 999999;
            if (aIdx !== bIdx) return aIdx - bIdx;
            return String(a.component_label || a.component || "").localeCompare(String(b.component_label || b.component || ""));
        });
    }

    function sum_detail_qty(rows, fieldname) {
        return (rows || []).reduce(function(total, row) {
            return total + flt(row[fieldname] || 0);
        }, 0);
    }

    function render_component_manufacturing_table_html(rows) {
        if (!rows || !rows.length) {
            return "";
        }

        var totalQty = sum_detail_qty(rows, "required_qty");
        var doneQty = sum_detail_qty(rows, "manufactured_qty");
        var rowsHtml = rows.map(function(row) {
            var remainingQty = flt(row.remaining_qty || 0);
            var statusColor = remainingQty > 0 ? "#f59e0b" : "#22c55e";
            var statusBg = remainingQty > 0 ? "rgba(245,158,11,0.14)" : "rgba(34,197,94,0.14)";
            return ''
                + '<tr>'
                +   '<td style="padding:10px; border-bottom:1px solid var(--border-color); font-weight:900; text-align:center;">' + escape_html_text(row.material_request_row || "-") + '</td>'
                +   '<td style="padding:10px; border-bottom:1px solid var(--border-color); font-weight:800;">' + escape_html_text(row.component_label || row.component || "-") + '</td>'
                +   '<td style="padding:10px; border-bottom:1px solid var(--border-color);">' + escape_html_text(row.item_code || "-") + '</td>'
                +   '<td style="padding:10px; border-bottom:1px solid var(--border-color); font-weight:900; white-space:nowrap;">' + format_manufacturing_count(row.manufactured_qty || 0) + " / " + format_manufacturing_count(row.required_qty || 0) + '</td>'
                +   '<td style="padding:10px; border-bottom:1px solid var(--border-color);">'
                +     '<span style="display:inline-flex; padding:4px 10px; border-radius:999px; background:' + statusBg + '; color:' + statusColor + '; font-size:12px; font-weight:800;">' + escape_html_text(row.status || "غير مصنع") + '</span>'
                +   '</td>'
                + '</tr>';
        }).join("");

        return ''
            + '<div style="margin-top:16px; border:1px solid var(--border-color); border-radius:14px; overflow:hidden; background:var(--card-bg);">'
            +   '<div style="display:flex; justify-content:space-between; align-items:center; padding:12px 14px; background:var(--control-bg);">'
            +     '<div style="font-weight:900;">تفاصيل تصنيع المكونات</div>'
            +     '<span style="padding:3px 10px; border-radius:999px; background:rgba(59,130,246,0.14); color:#3b82f6; font-size:12px; font-weight:800;">' + format_manufacturing_count(doneQty) + " / " + format_manufacturing_count(totalQty) + '</span>'
            +   '</div>'
            +   '<div style="max-height:360px; overflow:auto;">'
            +     '<table style="width:100%; border-collapse:collapse;">'
            +       '<thead style="position:sticky; top:0; background:var(--control-bg); z-index:1;">'
            +         '<tr>'
            +           '<th style="padding:10px; text-align:center; color:var(--text-muted); font-size:13px;">رقم الباب</th>'
            +           '<th style="padding:10px; text-align:right; color:var(--text-muted); font-size:13px;">المكون</th>'
            +           '<th style="padding:10px; text-align:right; color:var(--text-muted); font-size:13px;">الصنف</th>'
            +           '<th style="padding:10px; text-align:right; color:var(--text-muted); font-size:13px;">الكمية</th>'
            +           '<th style="padding:10px; text-align:right; color:var(--text-muted); font-size:13px;">الحالة</th>'
            +         '</tr>'
            +       '</thead>'
            +       '<tbody>' + rowsHtml + '</tbody>'
            +     '</table>'
            +   '</div>'
            + '</div>';
    }

    function open_manual_manufacturing_dialog(frm) {
        if (frm.is_new() || frm.doc.docstatus !== 1) {
            return;
        }

        var pendingRows = get_pending_manufacturing_rows(frm);
        if (!pendingRows.length) {
            frappe.msgprint("كل السطور مسجلة كمصنعة بالفعل.");
            return;
        }

        var dialog = new frappe.ui.Dialog({
            title: "تسجيل التصنيع يدويًا",
            fields: [
                {
                    fieldtype: "HTML",
                    fieldname: "help_html"
                },
                {
                    fieldtype: "HTML",
                    fieldname: "rows_html",
                    label: "السطور غير المصنعة"
                }
            ],
            primary_action_label: "تسجيل المحدد",
            primary_action: function() {
                var selected = get_selected_manufacturing_rows(dialog);
                if (!selected.length) {
                    frappe.msgprint("حدد سطرًا واحدًا على الأقل.");
                    return;
                }

                frappe.call({
                    method: "mark_manufactured_rows_v2",
                    args: {
                        mr: frm.doc.name,
                        selected_rows: selected.map(function(entry) {
                            return entry.idx + ":" + entry.qty;
                        }).join(",")
                    },
                    freeze: true,
                    freeze_message: "جاري تسجيل التصنيع...",
                    callback: function(r) {
                        var msg = r.message || {};
                        (msg.updated_rows || []).forEach(function(entry) {
                            var target = (frm.doc.items || []).find(function(row) {
                                return String(row.idx) === String(entry.idx);
                            });
                            if (!target) return;
                            frappe.model.set_value(target.doctype, target.name, "custom_manufactured_qty", entry.manufactured_qty || 0);
                            frappe.model.set_value(target.doctype, target.name, "custom_is_manufactured", entry.is_fully_manufactured ? 1 : 0);
                            frappe.model.set_value(target.doctype, target.name, "custom_manufactured_at", entry.manufactured_at || "");
                            frappe.model.set_value(target.doctype, target.name, "custom_manufactured_by", entry.manufactured_by || "");
                        });

                        if (frm.doc.doctype === "Material Request") {
                            if (Object.prototype.hasOwnProperty.call(msg, "manufacturing_status")) {
                                frm.set_value("custom_manufacturing_status", msg.manufacturing_status || "");
                            }
                            if (Object.prototype.hasOwnProperty.call(msg, "remaining_count")) {
                                frm.set_value("custom_manufacturing_remaining_count", msg.remaining_count || 0);
                            }
                            if (Object.prototype.hasOwnProperty.call(msg, "manufacturing_total_items")) {
                                frm.set_value("custom_manufacturing_total_count", msg.manufacturing_total_items || 0);
                            }
                            if (Object.prototype.hasOwnProperty.call(msg, "manufacturing_completed_at")) {
                                frm.set_value("custom_manufacturing_completed_at", msg.manufacturing_completed_at || "");
                            }
                            if (Object.prototype.hasOwnProperty.call(msg, "manufacturing_completed_by")) {
                                frm.set_value("custom_manufacturing_completed_by", msg.manufacturing_completed_by || "");
                            }
                            if (Object.prototype.hasOwnProperty.call(msg, "component_status")) {
                                frm.set_value("custom_component_manufacturing_status", msg.component_status || "");
                            }
                            if (Object.prototype.hasOwnProperty.call(msg, "component_remaining_count")) {
                                frm.set_value("custom_component_manufacturing_remaining_count", msg.component_remaining_count || 0);
                            }
                            if (Object.prototype.hasOwnProperty.call(msg, "component_total_count")) {
                                frm.set_value("custom_component_manufacturing_total_count", msg.component_total_count || 0);
                            }
                            if (Object.prototype.hasOwnProperty.call(msg, "delivery_readiness_status")) {
                                frm.set_value("custom_delivery_readiness_status", msg.delivery_readiness_status || "");
                            }
                            if (Object.prototype.hasOwnProperty.call(msg, "delivery_readiness_summary")) {
                                frm.set_value("custom_delivery_readiness_summary", msg.delivery_readiness_summary || "");
                            }
                            if (Array.isArray(msg.manufacturing_details)) {
                                frm.__manufacturing_details = msg.manufacturing_details;
                            }
                        }

                        frm.refresh_field("items");
                        dialog.hide();
                        render_manual_manufacturing_button(frm);

                        var parts = [];
                        if (msg.registered_qty) {
                            parts.push("تم تسجيل " + format_manufacturing_count(msg.registered_qty) + " وحدة.");
                        }
                        if (msg.already_done_count) {
                            parts.push(msg.already_done_count + " سطر كان مسجلًا مسبقًا.");
                        }
                        if (msg.missing_count) {
                            parts.push(msg.missing_count + " سطر لم يتم العثور عليه.");
                        }
                        if (msg.skipped_count) {
                            parts.push(msg.skipped_count + " سطر غير قابل للتصنيع لعدم وجود مقاسات.");
                        }
                        if (msg.manufacturing_status) {
                            parts.push("حالة الطلب: " + msg.manufacturing_status + ".");
                        }
                        frappe.show_alert({
                            message: parts.join(" "),
                            indicator: msg.updated_count ? "green" : "orange"
                        }, 7);
                    }
                });
            }
        });

        dialog.show();
        if (dialog.fields_dict.help_html && dialog.fields_dict.help_html.$wrapper) {
            dialog.fields_dict.help_html.$wrapper.html(
                '<div style="margin-bottom: 12px; color: var(--text-muted); line-height: 1.8;">'
                + 'اختر السطور التي تريد تسجيلها كمصنعة يدويًا عند تعذر قراءة الباركود.'
                + '<div style="margin-top:8px;">'
                + '<a href="#" class="manual-manufacturing-select-all">تحديد الكل</a>'
                + ' | '
                + '<a href="#" class="manual-manufacturing-clear-all">إلغاء التحديد</a>'
                + '</div>'
                + '</div>'
            );
            dialog.fields_dict.help_html.$wrapper.find(".manual-manufacturing-select-all").on("click", function(e) {
                e.preventDefault();
                if (dialog.fields_dict.rows_html && dialog.fields_dict.rows_html.$wrapper) {
                    dialog.fields_dict.rows_html.$wrapper.find(".manual-manufacturing-row-checkbox").prop("checked", true);
                }
            });
            dialog.fields_dict.help_html.$wrapper.find(".manual-manufacturing-clear-all").on("click", function(e) {
                e.preventDefault();
                if (dialog.fields_dict.rows_html && dialog.fields_dict.rows_html.$wrapper) {
                    dialog.fields_dict.rows_html.$wrapper.find(".manual-manufacturing-row-checkbox").prop("checked", false);
                }
            });
        }
        if (dialog.fields_dict.rows_html && dialog.fields_dict.rows_html.$wrapper) {
            dialog.fields_dict.rows_html.$wrapper.html(
                '<div style="max-height:420px; overflow:auto; border-top:1px solid var(--border-color); padding-top:10px;">'
                + render_manual_selection_rows_html(pendingRows)
                + '</div>'
            );
        }
    }

    window.open_manual_manufacturing_dialog_for_current_form = function() {
        if (window.cur_frm) {
            open_manual_manufacturing_dialog(window.cur_frm);
        }
    };

    function render_manual_manufacturing_button(frm) {
        frm.remove_custom_button("تسجيل التصنيع يدويًا");

        var field = frm.fields_dict.custom_manufacturing_dashboard;
        if (!field) {
            return;
        }

        if (frm.is_new() || frm.doc.docstatus !== 1) {
            frm.set_df_property("custom_manufacturing_dashboard", "options", "");
            frm.refresh_field("custom_manufacturing_dashboard");
            return;
        }

        var pendingRows = get_pending_manufacturing_rows(frm);
        var completedRows = get_completed_manufacturing_rows(frm);
        var trackableRows = (frm.doc.items || []).filter(function(row) {
            return row.item_code && is_trackable_manufacturing_row(row);
        });
        var componentRows = get_manufacturing_detail_rows(frm, "مكون");
        var statusText = frm.doc.custom_manufacturing_status || "غير مصنع";
        var totalRows = sum_manufacturing_qty(trackableRows, "total");
        var completedTotal = sum_manufacturing_qty(trackableRows, "done");
        var remainingValue = totalRows - completedTotal;
        if (remainingValue < 0) remainingValue = 0;
        var remainingText = format_manufacturing_count(remainingValue);
        var completedText = format_manufacturing_count(completedTotal);
        var totalText = format_manufacturing_count(totalRows);
        var componentTotal = flt(frm.doc.custom_component_manufacturing_total_count || 0) || sum_detail_qty(componentRows, "required_qty");
        var componentRemaining = flt(frm.doc.custom_component_manufacturing_remaining_count || 0);
        if (!componentRemaining && componentRows.length) {
            componentRemaining = sum_detail_qty(componentRows, "remaining_qty");
        }
        var componentDone = componentTotal - componentRemaining;
        if (componentDone < 0) componentDone = 0;
        var deliveryStatusText = frm.doc.custom_delivery_readiness_status || "";
        var percent = totalRows ? ((completedTotal * 100) / totalRows).toFixed(1) : "0.0";
        var normalizedMr = normalize_manufacturing_request_name(frm.doc.name);
        var statusBadgeBg = statusText === "مصنع بالكامل" ? "rgba(34,197,94,0.14)" : "rgba(245,158,11,0.14)";
        var statusBadgeColor = statusText === "مصنع بالكامل" ? "#22c55e" : "#f59e0b";

        var bodyHtml = [
            '<div class="manual-manufacturing-inline-wrap" style="padding:16px; border:1px solid var(--border-color); border-radius:16px; background:var(--card-bg); margin-bottom:12px;">',
            '<div style="display:flex; justify-content:space-between; align-items:flex-start; gap:16px; flex-wrap:wrap;">',
            '<div>',
            '<div style="font-size:22px; font-weight:900; margin-bottom:4px;">لوحة التصنيع: ' + escape_html_text(normalizedMr) + '</div>',
            '<div style="color:var(--text-muted);">عرض سريع لما تم تصنيعه وما تبقى داخل هذا الطلب</div>',
            '</div>',
            '<div style="display:flex; flex-direction:column; align-items:flex-end; gap:8px;">',
            '<a href="/factory?mr=' + encodeURIComponent(normalizedMr) + '" target="_blank" class="btn btn-default btn-sm">فتح شاشة التصنيع</a>',
            '<div style="display:flex; flex-direction:column; gap:6px; align-items:flex-end;">',
            '<span style="display:inline-flex; align-items:center; gap:6px; padding:4px 10px; border-radius:999px; background:' + statusBadgeBg + '; color:' + statusBadgeColor + '; font-size:12px; font-weight:800;">حالة التصنيع: ' + escape_html_text(statusText) + '</span>',
            '<span style="display:inline-flex; align-items:center; gap:6px; padding:4px 10px; border-radius:999px; background:rgba(245,158,11,0.14); color:#f59e0b; font-size:12px; font-weight:800;">المتبقي: ' + remainingText + '</span>',
            '</div>',
            '</div>',
            '</div>',
            '<div style="display:flex; gap:12px; flex-wrap:wrap; margin-top:16px;">',
            render_manufacturing_stat_card("المتبقي", remainingText, "amber"),
            render_manufacturing_stat_card("تم تصنيعه", completedText, "green"),
            render_manufacturing_stat_card("الإجمالي", totalText, "default"),
            componentTotal ? render_manufacturing_stat_card("المكونات", format_manufacturing_count(componentDone) + " / " + format_manufacturing_count(componentTotal), componentRemaining > 0 ? "amber" : "green") : "",
            deliveryStatusText ? render_manufacturing_stat_card("جاهزية التوريد", escape_html_text(deliveryStatusText), deliveryStatusText === "غير جاهز" ? "amber" : "green") : "",
            '</div>',
            '<div style="margin-top:16px;">',
            '<div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; color:var(--text-muted); font-size:13px; font-weight:700;">',
            '<span>نسبة الإنجاز</span>',
            '<span>' + percent + '%</span>',
            '</div>',
            '<div style="height:12px; background:var(--control-bg); border-radius:999px; overflow:hidden; border:1px solid var(--border-color);">',
            '<div style="height:100%; width:' + percent + '%; background:linear-gradient(90deg, #22c55e, #16a34a);"></div>',
            '</div>',
            '</div>',
            '<div style="margin-top:18px; font-weight:800;">تفاصيل التصنيع</div>'
        ];

        if (pendingRows.length) {
            bodyHtml.push(
                '<div style="margin-top:12px; display:flex; justify-content:flex-end;">'
                + '<button type="button" class="btn btn-primary btn-sm manual-manufacturing-inline-btn" onclick="window.open_manual_manufacturing_dialog_for_current_form && window.open_manual_manufacturing_dialog_for_current_form()">تسجيل التصنيع يدويًا</button>'
                + '</div>'
            );
        }

        bodyHtml.push('<div style="display:flex; gap:16px; flex-wrap:wrap; margin-top:12px;">');
        bodyHtml.push(render_manufacturing_table_html("المتبقي للتصنيع", pendingRows, "لا توجد سطور غير مصنعة.", "orange", false));
        bodyHtml.push(render_manufacturing_table_html("تم تصنيعه", completedRows, "لا توجد سطور مصنعة بعد.", "green", true));
        bodyHtml.push('</div></div>');
        bodyHtml.push(render_component_manufacturing_table_html(componentRows));
        frm.set_df_property("custom_manufacturing_dashboard", "options", bodyHtml.join(""));
        frm.refresh_field("custom_manufacturing_dashboard");
    }

    frappe.ui.form.on("Material Request Item", {
        item_code: function(frm, cdt, cdn) {
            if (frm.doc.docstatus <= 1) {
                frappe.model.set_value(cdt, cdn, "custom_result_leaf_w", 0);
                frappe.model.set_value(cdt, cdn, "custom_result_leaf_h", 0);
                frappe.model.set_value(cdt, cdn, "custom_result_u_w", 0);
                frappe.model.set_value(cdt, cdn, "custom_result_u_h", 0);
                frappe.model.set_value(cdt, cdn, "custom_result_panel_w", 0);
                frappe.model.set_value(cdt, cdn, "custom_result_panel_h", 0);
                frappe.model.set_value(cdt, cdn, "custom_result_leaf_w_text", "");
                frappe.model.set_value(cdt, cdn, "custom_result_u_w_text", "");
                frappe.model.set_value(cdt, cdn, "custom_result_panel_w_text", "");
                frappe.model.set_value(cdt, cdn, "custom_result_sliding_type", "");
                frappe.model.set_value(cdt, cdn, "custom_cutting_template", "");
                frappe.model.set_value(cdt, cdn, "custom_show_glass_options", 0);
                frappe.model.set_value(cdt, cdn, "custom_glass_type", "");
                frappe.model.set_value(cdt, cdn, "custom_glass_model", "");
                frappe.model.set_value(cdt, cdn, "custom_show_component_exclusions", 0);
                frappe.model.set_value(cdt, cdn, "custom_component_exclusion_group", "");
                frappe.model.set_value(cdt, cdn, "custom_frame_type", "");
                clear_component_exclusion_rows(cdt, cdn);
            }
            request_cutting_render(frm);
        },
        custom_leaf_count: function(frm, cdt, cdn) {
            request_cutting_render(frm);
        },
        custom_split_type: function(frm, cdt, cdn) {
            request_cutting_render(frm);
        },
        custom_fixed_leaf_width: function(frm, cdt, cdn) {
            request_cutting_render(frm);
        },
        custom_net_leaf: function(frm, cdt, cdn) {
            request_cutting_render(frm);
        },
        custom_parquet: function(frm, cdt, cdn) {
            request_cutting_render(frm);
        },
        custom_taksiya_1: function(frm, cdt, cdn) {
            request_cutting_render(frm);
        },
        custom_taksiya_2: function(frm, cdt, cdn) {
            request_cutting_render(frm);
        },
        custom_no_qitaat: function(frm, cdt, cdn) {
            request_cutting_render(frm);
        },
        custom_without_kalon: function(frm, cdt, cdn) {
            request_cutting_render(frm);
        },
        custom_square_count: function(frm, cdt, cdn) {
            request_cutting_render(frm);
        },
        custom_glass_type: function(frm, cdt, cdn) {
            request_cutting_render(frm);
        },
        custom_glass_model: function(frm, cdt, cdn) {
            request_cutting_render(frm);
        },
        custom_component_exclusion_group: function(frm, cdt, cdn) {
            ensure_component_exclusion_snapshot(cdt, cdn);
            apply_component_exclusion_group(frm, cdt, cdn);
        },
        custom_excluded_store_components: function(frm, cdt, cdn) {
            reconcile_component_exclusion_change(frm, cdt, cdn);
        },
        custom_frame_type: function(frm, cdt, cdn) {
            request_cutting_render(frm);
        },
        custom_result_sliding_type: function(frm, cdt, cdn) {
            if (_cutting_applying_results) return;
            var row = locals[cdt][cdn];
            if (row.item_code && frm.doc.docstatus <= 1) {
                frappe.model.set_value(cdt, cdn, "custom_result_leaf_w", 0);
                frappe.model.set_value(cdt, cdn, "custom_result_leaf_h", 0);
                frappe.model.set_value(cdt, cdn, "custom_result_u_w", 0);
                frappe.model.set_value(cdt, cdn, "custom_result_u_h", 0);
                frappe.model.set_value(cdt, cdn, "custom_result_panel_w", 0);
                frappe.model.set_value(cdt, cdn, "custom_result_panel_h", 0);
                frappe.model.set_value(cdt, cdn, "custom_result_leaf_w_text", "");
                frappe.model.set_value(cdt, cdn, "custom_result_u_w_text", "");
                frappe.model.set_value(cdt, cdn, "custom_result_panel_w_text", "");
                request_cutting_render(frm);
            }
        },
        form_render: function(frm, cdt, cdn) {
            apply_split_fields_visibility(frm, cdn);
            apply_square_count_visibility(frm, cdn);
            apply_glass_options_visibility(frm, cdn);
            safe_apply_component_exclusion_options(frm);
            apply_component_exclusion_visibility(frm, cdn);
            apply_frame_component_visibility(frm, cdn);
            apply_sliding_type_query(frm, cdn);
            apply_sliding_type_visibility(frm, cdn);
        }
    });

    function apply_split_fields_visibility(frm, cdn) {
        var show = !!_item_split_support_map[cdn];
        var grid_rows = (frm.fields_dict.items && frm.fields_dict.items.grid && frm.fields_dict.items.grid.grid_rows) || [];
        var grid_row = null;
        for (var i = 0; i < grid_rows.length; i++) {
            if (grid_rows[i] && grid_rows[i].doc && grid_rows[i].doc.name === cdn) {
                grid_row = grid_rows[i];
                break;
            }
        }
        if (!grid_row || !grid_row.grid_form || !grid_row.grid_form.fields_dict) {
            return;
        }
        ["custom_leaf_count", "custom_split_type", "custom_fixed_leaf_width"].forEach(function(fieldname) {
            var control = grid_row.grid_form.fields_dict[fieldname];
            if (!control) return;
            control.df.hidden = show ? 0 : 1;
            if (control.wrapper) {
                $(control.wrapper).toggle(show);
                $(control.wrapper).closest(".frappe-control").toggle(show);
            }
            if (typeof control.refresh === "function") {
                control.refresh();
            }
        });
        if (grid_row.grid_form.layout && typeof grid_row.grid_form.layout.refresh_sections === "function") {
            grid_row.grid_form.layout.refresh_sections();
        }
    }

    function apply_square_count_visibility(frm, cdn) {
        var show = !!_item_square_count_support_map[cdn];
        var grid_rows = (frm.fields_dict.items && frm.fields_dict.items.grid && frm.fields_dict.items.grid.grid_rows) || [];
        var grid_row = null;
        for (var i = 0; i < grid_rows.length; i++) {
            if (grid_rows[i] && grid_rows[i].doc && grid_rows[i].doc.name === cdn) {
                grid_row = grid_rows[i];
                break;
            }
        }
        if (!grid_row || !grid_row.grid_form || !grid_row.grid_form.fields_dict) {
            return;
        }
        var control = grid_row.grid_form.fields_dict.custom_square_count;
        if (!control) return;
        control.df.hidden = show ? 0 : 1;
        if (control.wrapper) {
            $(control.wrapper).toggle(show);
            $(control.wrapper).closest(".frappe-control").toggle(show);
        }
        if (typeof control.refresh === "function") {
            control.refresh();
        }
        if (grid_row.grid_form.layout && typeof grid_row.grid_form.layout.refresh_sections === "function") {
            grid_row.grid_form.layout.refresh_sections();
        }
    }

    function apply_glass_options_visibility(frm, cdn) {
        var grid_rows = (frm.fields_dict.items && frm.fields_dict.items.grid && frm.fields_dict.items.grid.grid_rows) || [];
        var grid_row = null;
        for (var i = 0; i < grid_rows.length; i++) {
            if (grid_rows[i] && grid_rows[i].doc && grid_rows[i].doc.name === cdn) {
                grid_row = grid_rows[i];
                break;
            }
        }
        if (!grid_row || !grid_row.grid_form || !grid_row.grid_form.fields_dict) {
            return;
        }
        var row_doc = grid_row.doc || {};
        var show = !!_item_glass_options_support_map[cdn] || !!(parseInt(row_doc.custom_show_glass_options, 10) || 0);
        row_doc.custom_show_glass_options = show ? 1 : 0;
        ["custom_glass_type", "custom_glass_model"].forEach(function(fieldname) {
            var control = grid_row.grid_form.fields_dict[fieldname];
            if (!control) return;
            control.df.hidden = show ? 0 : 1;
            if (typeof control.refresh === "function") {
                control.refresh();
            }
            if (control.wrapper) {
                $(control.wrapper).toggle(show);
                $(control.wrapper).closest(".frappe-control").toggle(show);
            }
        });
        if (grid_row.grid_form.layout && typeof grid_row.grid_form.layout.refresh_sections === "function") {
            grid_row.grid_form.layout.refresh_sections();
        }
    }

    function apply_component_exclusion_options(frm) {
        var grid = frm.fields_dict.items && frm.fields_dict.items.grid;
        if (!grid) return;
        var options = _component_exclusion_child_doctype;
        if (!has_component_exclusion_link_meta()) {
            ensure_component_exclusion_meta(function(ready) {
                if (ready) safe_apply_component_exclusion_options(frm);
            });
            return;
        }
        var grid_field = grid.get_field && grid.get_field("custom_excluded_store_components");
        if (grid_field) {
            if (grid_field.df) {
                grid_field.df.options = options;
                grid_field.df.read_only = 1;
            } else {
                grid_field.options = options;
                grid_field.read_only = 1;
            }
        }
        (grid.grid_rows || []).forEach(function(grid_row) {
            var control = grid_row && grid_row.grid_form && grid_row.grid_form.fields_dict
                ? grid_row.grid_form.fields_dict.custom_excluded_store_components
                : null;
            if (!control || !control.df) return;
            control.df.options = options;
            control.df.read_only = 1;
            if (typeof control.refresh === "function") {
                try {
                    control.refresh();
                } catch (e) {
                    console.warn("تعذر تحديث حقل استثناء المكونات", e);
                }
            }
        });
    }

    function apply_component_exclusion_visibility(frm, cdn) {
        var grid_rows = (frm.fields_dict.items && frm.fields_dict.items.grid && frm.fields_dict.items.grid.grid_rows) || [];
        var grid_row = null;
        for (var i = 0; i < grid_rows.length; i++) {
            if (grid_rows[i] && grid_rows[i].doc && grid_rows[i].doc.name === cdn) {
                grid_row = grid_rows[i];
                break;
            }
        }
        if (!grid_row || !grid_row.grid_form || !grid_row.grid_form.fields_dict) {
            return;
        }
        var row_doc = grid_row.doc || {};
        ensure_component_exclusion_snapshot(row_doc.doctype || "Material Request Item", row_doc.name);
        var show = !!_item_component_exclusion_support_map[cdn] || !!(parseInt(row_doc.custom_show_component_exclusions, 10) || 0);
        row_doc.custom_show_component_exclusions = show ? 1 : 0;
        ["custom_component_exclusion_group", "custom_excluded_store_components"].forEach(function(fieldname) {
            var control = grid_row.grid_form.fields_dict[fieldname];
            if (!control) return;
            control.df.hidden = show ? 0 : 1;
            if (fieldname === "custom_excluded_store_components") {
                if (!has_component_exclusion_link_meta()) {
                    ensure_component_exclusion_meta(function(ready) {
                        if (ready) safe_apply_component_exclusion_options(frm);
                    });
                    return;
                }
                control.df.options = _component_exclusion_child_doctype;
                control.df.read_only = 1;
            }
            if (typeof control.refresh === "function") {
                try {
                    control.refresh();
                } catch (e) {
                    console.warn("تعذر تحديث ظهور حقل استثناء المكونات", e);
                }
            }
            if (control.wrapper) {
                $(control.wrapper).toggle(show);
                $(control.wrapper).closest(".frappe-control").toggle(show);
            }
        });
        if (grid_row.grid_form.layout && typeof grid_row.grid_form.layout.refresh_sections === "function") {
            grid_row.grid_form.layout.refresh_sections();
        }
    }

    function render_cutting_table_with_guard(frm, items_to_check, bulk_result) {
        _cutting_applying_results = true;
        try {
            _render_cutting_table(frm, items_to_check, bulk_result);
        } finally {
            _cutting_applying_results = false;
        }
    }

    function render_all_cutting(frm) {
        if (_cutting_rendering) {
            _cutting_pending_render = true;
            return;
        }
        _cutting_rendering = true;
        _cutting_pending_render = false;

        function finish_render() {
            _cutting_rendering = false;
            if (_cutting_pending_render) {
                _cutting_pending_render = false;
                schedule_render_cutting(frm, 0);
            }
        }

        try {
            if (!frm.doc.items || frm.doc.items.length === 0) {
                if (frm.fields_dict.custom_cutting_results_html) {
                    frm.fields_dict.custom_cutting_results_html.$wrapper.html('');
                }
                finish_render();
                return;
            }

            var items_to_check = [];
            frm.doc.items.forEach(function(row) {
                if (row.item_code) {
                    var row_width = flt(row["\u0639\u0631\u0636"]) || 0;
                    var row_height = flt(row["\u0637\u0648\u0644"]) || 0;
                    var net_leaf = parseInt(row.custom_net_leaf, 10) || 0;
                    var requested_no_qitaat = parseInt(row.custom_no_qitaat, 10) || 0;
                    items_to_check.push({
                        idx: row.idx,
                        item_code: row.item_code,
                        item_name: row.item_name || row.item_code,
                        w: row_width,
                        h: row_height,
                        cdt: row.doctype,
                        cdn: row.name,
                        lw: flt(row.custom_result_leaf_w),
                        lwt: row.custom_result_leaf_w_text || "",
                        lh: flt(row.custom_result_leaf_h),
                        uw: flt(row.custom_result_u_w),
                        uwt: row.custom_result_u_w_text || "",
                        uh: flt(row.custom_result_u_h),
                        pw: flt(row.custom_result_panel_w),
                        pwt: row.custom_result_panel_w_text || "",
                        ph: flt(row.custom_result_panel_h),
                        ww: flt(row["\u0639\u0631\u0636_\u0627\u0644\u062c\u062f\u0627\u0631"]) || 0,
                        notes: strip_html(row["\u0645\u0644\u0627\u062d\u0638\u0627\u062a"] || ""),
                        tmpl: row.custom_cutting_template || "",
                        sliding: row.custom_result_sliding_type || "",
                        max_w: 0, max_h: 0,
                        item_type: '',
                        leaf_count: parseInt(row.custom_leaf_count) || 1,
                        split_type: row.custom_split_type || '',
                        fixed_leaf_w: flt(row.custom_fixed_leaf_width),
                        net_leaf: net_leaf,
                        parquet: row.custom_parquet || 0,
                        show_square_count: parseInt(row.custom_show_square_count, 10) || 0,
                        square_count: (parseInt(row.custom_show_square_count, 10) || 0) ? (row.custom_square_count || "") : "",
                        show_glass_options: parseInt(row.custom_show_glass_options, 10) || 0,
                        glass_type: (parseInt(row.custom_show_glass_options, 10) || 0) ? String(row.custom_glass_type || "").trim() : "",
                        glass_model: (parseInt(row.custom_show_glass_options, 10) || 0) ? String(row.custom_glass_model || "").trim() : "",
                        show_component_exclusions: parseInt(row.custom_show_component_exclusions, 10) || 0,
                        component_exclusion_group: String(row.custom_component_exclusion_group || "").trim(),
                        excluded_components: (parseInt(row.custom_show_component_exclusions, 10) || 0) ? normalize_component_exclusions(row.custom_excluded_store_components || []) : "",
                        frame_component: String(row.custom_frame_type || "").trim(),
                        taksiya_1: row.custom_taksiya_1 || 0,
                        taksiya_2: row.custom_taksiya_2 || 0,
                        no_qitaat: requested_no_qitaat,
                        display_no_qitaat: (!net_leaf && requested_no_qitaat) ? 1 : 0,
                        without_kalon: row.custom_without_kalon || 0,
                        item_qty: flt(row.qty) || 1,
                        allow_missing_dimensions: 0,
                        stores: []
                    });
                }
            });

            if (items_to_check.length === 0) {
                frm.fields_dict.custom_cutting_results_html.$wrapper.html('');
                finish_render();
                return;
            }

            var bulk_result = {};
            var uncached_items = [];
            var uncached_keys = {};
            items_to_check.forEach(function(item) {
                var request_key = get_cutting_request_key(item);
                var cache_key = get_cutting_cache_key(item);
                item._cutting_request_key = request_key;
                item._cutting_cache_key = cache_key;
                if (Object.prototype.hasOwnProperty.call(_cutting_result_cache, cache_key)) {
                    bulk_result[request_key] = _cutting_result_cache[cache_key];
                } else if (!uncached_keys[request_key]) {
                    uncached_keys[request_key] = true;
                    uncached_items.push(item);
                }
            });

            if (uncached_items.length === 0) {
                render_cutting_table_with_guard(frm, items_to_check, bulk_result);
                finish_render();
                return;
            }

            var codes = [];
            var slides = [];
            var widths = [];
            var heights = [];
            var walls = [];
            var leaf_counts_arr = [];
            var split_types_arr = [];
            var fixed_lws_arr = [];
            var taksiya1_arr = [];
            var taksiya2_arr = [];
            var no_qitaat_arr = [];
            var net_leaf_arr = [];
            var parquet_arr = [];
            var square_count_arr = [];
            var glass_type_arr = [];
            var glass_model_arr = [];
            var component_exclusion_groups_arr = [];
            var component_exclusions_arr = [];
            var frame_components_arr = [];
            uncached_items.forEach(function(item) {
                codes.push(item.item_code);
                slides.push(item.sliding || '');
                widths.push(String(item.w));
                heights.push(String(item.h));
                walls.push(String(item.ww));
                leaf_counts_arr.push(String(item.leaf_count || 1));
                split_types_arr.push(item.split_type || '');
                fixed_lws_arr.push(String(item.fixed_leaf_w || 0));
                taksiya1_arr.push(String(item.taksiya_1 || 0));
                taksiya2_arr.push(String(item.taksiya_2 || 0));
                no_qitaat_arr.push(String(item.no_qitaat || 0));
                net_leaf_arr.push(String(item.net_leaf || 0));
                parquet_arr.push(String(item.parquet || 0));
                square_count_arr.push(item.square_count || "");
                glass_type_arr.push(item.glass_type || "");
                glass_model_arr.push(item.glass_model || "");
                component_exclusion_groups_arr.push(item.component_exclusion_group || "");
                component_exclusions_arr.push(normalize_component_exclusions(item.excluded_components || ""));
                frame_components_arr.push(item.frame_component || "");
            });

            frappe.call({
                method: "get_cutting_values_bulk",
                args: {
                    item_codes: codes.join(','),
                    sliding_types: slides.join(','),
                    widths: widths.join(','),
                    heights: heights.join(','),
                    wall_widths: walls.join(','),
                    leaf_counts: leaf_counts_arr.join(','),
                    split_types: split_types_arr.join(','),
                    fixed_leaf_widths: fixed_lws_arr.join(','),
                    taksiya1s: taksiya1_arr.join(','),
                    taksiya2s: taksiya2_arr.join(','),
                    no_qitaats: no_qitaat_arr.join(','),
                    net_leafs: net_leaf_arr.join(','),
                    parquets: parquet_arr.join(','),
                    square_counts: square_count_arr.join(','),
                    glass_types: glass_type_arr.join(','),
                    glass_models: glass_model_arr.join(','),
                    component_exclusion_groups: component_exclusion_groups_arr.join(';'),
                    component_exclusions: component_exclusions_arr.join(';'),
                    frame_components: frame_components_arr.join(';')
                },
                callback: function(r) {
                    var response_values = {};
                    if (r && r.message) {
                        response_values = r.message.values || {};
                    }
                    uncached_items.forEach(function(item) {
                        var response_value = Object.prototype.hasOwnProperty.call(response_values, item._cutting_request_key)
                            ? response_values[item._cutting_request_key]
                            : null;
                        _cutting_result_cache[item._cutting_cache_key] = response_value;
                        bulk_result[item._cutting_request_key] = response_value;
                    });
                    render_cutting_table_with_guard(frm, items_to_check, bulk_result);
                    finish_render();
                },
                error: function() {
                    finish_render();
                }
            });

        } catch (e) {
            finish_render();
            throw e;
        }
    }

    function resolve_cutting_result_value(result_row, fieldname, fallback_value) {
        if (result_row && result_row[fieldname] !== undefined && result_row[fieldname] !== null && result_row[fieldname] !== "") {
            return flt(result_row[fieldname]);
        }
        return fallback_value;
    }

    function has_valid_cutting_dimensions(item) {
        if (item.allow_missing_dimensions) {
            return true;
        }
        return flt(item.w) > 0 && flt(item.h) > 0;
    }

    function invalid_cutting_fields(item) {
        if (!has_valid_cutting_dimensions(item)) {
            return [];
        }

        var fields = [];
        function push_if_invalid(label, value) {
            if (value === null || value === undefined || value === "") return;
            if (flt(value) < 0) fields.push(label);
        }

        if (item.show_leaf) {
            push_if_invalid("Leaf W", item.lw);
            push_if_invalid("Leaf H", item.lh);
        }
        if (item.show_u) {
            push_if_invalid("U W", item.uw);
            push_if_invalid("U H", item.uh);
        }
        if (item.show_panel) {
            push_if_invalid("Panel W", item.pw);
            push_if_invalid("Panel H", item.ph);
        }
        return fields;
    }

    function clear_cutting_result_values(item) {
        item.lw = null;
        item.lh = null;
        item.uw = null;
        item.uh = null;
        item.pw = null;
        item.ph = null;
        item.lwt = "";
        item.uwt = "";
        item.pwt = "";
        item.stores = [];
    }

    function format_glass_options(item) {
        var parts = [];
        var glass_type = String(item.glass_type || "").trim();
        var glass_model = String(item.glass_model || "").trim();
        if (glass_type) parts.push(glass_type);
        if (glass_model) parts.push(glass_model);
        return parts.length ? parts.join(" ") : "-";
    }

    function template_supports_split(result_row) {
        return !!(result_row && result_row.default_leaf_count && parseInt(result_row.default_leaf_count, 10) > 1);
    }

    function _render_cutting_table(frm, items_to_check, bulk_result) {
        try {
            // Store per-item sliding options for set_query
            _item_sliding_options_map = {};
            var invalid_cutting_items = [];
            var incomplete_cutting_items = [];

            // Collect all store component names across all items
            _item_split_support_map = {};
            _item_square_count_support_map = {};
            _item_glass_options_support_map = {};
            _item_component_exclusion_support_map = {};
            _item_frame_component_support_map = {};
            var all_comps = [];
            var comp_labels = {};
            items_to_check.forEach(function(item) {
                var key = item._cutting_request_key || get_cutting_request_key(item);
                var t = bulk_result[key];
                if (t) {
                    item.tmpl = t.parent;
                    _item_sliding_options_map[item.cdn] = t.item_sliding_options || [];
                    item.item_type = t.type || '';
                    var supports_split = template_supports_split(t);
                    _item_split_support_map[item.cdn] = supports_split;
                    if (!supports_split) {
                        item.leaf_count = 1;
                        item.split_type = '';
                        item.fixed_leaf_w = 0;
                        if (frm.doc.docstatus <= 1) {
                            var resetSplitInputs = false;
                            if ((parseInt(frappe.model.get_value(item.cdt, item.cdn, 'custom_leaf_count'), 10) || 0) > 1) {
                                frappe.model.set_value(item.cdt, item.cdn, 'custom_leaf_count', '');
                                resetSplitInputs = true;
                            }
                            if (frappe.model.get_value(item.cdt, item.cdn, 'custom_split_type')) {
                                frappe.model.set_value(item.cdt, item.cdn, 'custom_split_type', '');
                                resetSplitInputs = true;
                            }
                            if (flt(frappe.model.get_value(item.cdt, item.cdn, 'custom_fixed_leaf_width'))) {
                                frappe.model.set_value(item.cdt, item.cdn, 'custom_fixed_leaf_width', 0);
                                resetSplitInputs = true;
                            }
                            if (resetSplitInputs) {
                                _cutting_pending_render = true;
                            }
                        }
                    }
                    // Set defaults from template only if user hasn't set them
                    if (!item.leaf_count || item.leaf_count <= 1) {
                        if (t.default_leaf_count && parseInt(t.default_leaf_count) > 1) {
                            item.leaf_count = parseInt(t.default_leaf_count);
                            item.split_type = t.default_split_type || '';
                            item.fixed_leaf_w = flt(t.fixed_leaf_width);
                            if (frm.doc.docstatus <= 1) {
                                frappe.model.set_value(item.cdt, item.cdn, 'custom_leaf_count', t.default_leaf_count);
                                frappe.model.set_value(item.cdt, item.cdn, 'custom_split_type', t.default_split_type || '');
                                frappe.model.set_value(item.cdt, item.cdn, 'custom_fixed_leaf_width', flt(t.fixed_leaf_width));
                                _cutting_pending_render = true;
                            }
                        }
                    }
                    var show_leaf = (t.show_leaf_result === undefined || t.show_leaf_result === null || t.show_leaf_result === '') ? 1 : (parseInt(t.show_leaf_result, 10) || 0);
                    var show_u = (t.show_u_result === undefined || t.show_u_result === null || t.show_u_result === '') ? 1 : (parseInt(t.show_u_result, 10) || 0);
                    var show_panel = (t.show_panel_result === undefined || t.show_panel_result === null || t.show_panel_result === '') ? 1 : (parseInt(t.show_panel_result, 10) || 0);
                    var u_fallback = ((t.u_fallback || 'none') + '').toLowerCase();
                    var panel_fallback = ((t.panel_fallback || 'none') + '').toLowerCase();
                    item.show_leaf = show_leaf;
                    item.show_u = show_u;
                    item.show_panel = show_panel;
                    item.allow_missing_dimensions = parseInt(t.allow_missing_dimensions, 10) || 0;
                    item.show_square_count = parseInt(t.show_square_count, 10) || 0;
                    item.show_glass_options = parseInt(t.show_glass_options, 10) || 0;
                    item.show_component_exclusions = parseInt(t.show_component_exclusions, 10) || 0;
                    _item_square_count_support_map[item.cdn] = !!item.show_square_count;
                    _item_glass_options_support_map[item.cdn] = !!item.show_glass_options;
                    _item_component_exclusion_support_map[item.cdn] = !!item.show_component_exclusions;
                    var live_row = locals[item.cdt] && locals[item.cdt][item.cdn];
                    if (live_row) {
                        live_row.custom_show_glass_options = item.show_glass_options ? 1 : 0;
                        live_row.custom_show_component_exclusions = item.show_component_exclusions ? 1 : 0;
                    }
                    item.display_no_qitaat = (!item.net_leaf && (parseInt(t.effective_no_qitaat, 10) || 0)) ? 1 : 0;
                    if (item.parquet && flt(t.parquet_deduction)) {
                        item.h = item.h + flt(t.parquet_deduction);
                    }
                    if (item.net_leaf) {
                        item.lw = resolve_cutting_result_value(t, "result_leaf_w", item.w + flt(t.net_leaf_w_deduction));
                        item.lh = resolve_cutting_result_value(t, "result_leaf_h", item.h + flt(t.net_leaf_h_deduction));
                    } else {
                        item.lw = resolve_cutting_result_value(t, "result_leaf_w", (flt(t.leaf_w) ? item.w + flt(t.leaf_w) : item.w));
                        item.lh = resolve_cutting_result_value(t, "result_leaf_h", (flt(t.leaf_h) ? item.h + flt(t.leaf_h) : item.h));
                    }
                    if (show_u && item.net_leaf) {
                        item.uw = resolve_cutting_result_value(t, "result_u_w", (flt(t.net_leaf_u_w) ? item.w + flt(t.net_leaf_u_w) : (u_fallback === 'leaf' ? item.lw : 0)));
                        item.uh = resolve_cutting_result_value(t, "result_u_h", (flt(t.net_leaf_u_h) ? item.h + flt(t.net_leaf_u_h) : (u_fallback === 'leaf' ? item.lh : 0)));
                    } else if (show_u) {
                        item.uw = resolve_cutting_result_value(t, "result_u_w", (flt(t.u_w) ? item.w + flt(t.u_w) : (u_fallback === 'leaf' ? item.lw : 0)));
                        item.uh = resolve_cutting_result_value(t, "result_u_h", (flt(t.u_h) ? item.h + flt(t.u_h) : (u_fallback === 'leaf' ? item.lh : 0)));
                    } else {
                        item.uw = null;
                        item.uh = null;
                    }
                    if (show_panel && item.net_leaf) {
                        item.pw = resolve_cutting_result_value(t, "result_panel_w", (flt(t.net_leaf_panel_w) ? item.w + flt(t.net_leaf_panel_w) : (panel_fallback === 'leaf' ? item.lw : 0)));
                        item.ph = resolve_cutting_result_value(t, "result_panel_h", (flt(t.net_leaf_panel_h) ? item.h + flt(t.net_leaf_panel_h) : (panel_fallback === 'leaf' ? item.lh : 0)));
                    } else if (show_panel) {
                        item.pw = resolve_cutting_result_value(t, "result_panel_w", (flt(t.panel_w) ? item.w + flt(t.panel_w) : (panel_fallback === 'leaf' ? item.lw : 0)));
                        item.ph = resolve_cutting_result_value(t, "result_panel_h", (flt(t.panel_h) ? item.h + flt(t.panel_h) : (panel_fallback === 'leaf' ? item.lh : 0)));
                    } else {
                        item.pw = null;
                        item.ph = null;
                    }
                    item.max_w = flt(t.max_width);
                    item.max_h = flt(t.max_height);
                    item.lwt = t.leaf_w_text || '';
                    item.uwt = t.u_w_text || '';
                    item.pwt = t.panel_w_text || '';
                    item.square_count = item.show_square_count ? (t.square_count || item.square_count || "") : "";
                    if (!item.show_glass_options) {
                        item.glass_type = "";
                        item.glass_model = "";
                    }
                    item.excluded_components = item.show_component_exclusions ? (t.excluded_components || item.excluded_components || "") : "";
                    var exclusion_live_row = locals[item.cdt] && locals[item.cdt][item.cdn];
                    if (exclusion_live_row) {
                        if (item.show_component_exclusions) {
                            set_component_exclusion_rows(frm, item.cdt, item.cdn, item.excluded_components, { skip_render: true, skip_refresh: true });
                        } else {
                            clear_component_exclusion_rows(item.cdt, item.cdn);
                        }
                    }
                    item.stores = t.stores || [];
                    _item_frame_component_support_map[item.cdn] = row_has_frame_component(item.stores) || !!String(item.frame_component || "").trim();
                    if (!has_valid_cutting_dimensions(item)) {
                        incomplete_cutting_items.push({
                            idx: item.idx,
                            item_name: item.item_name || item.item_code,
                            template: t.parent
                        });
                        item.show_leaf = 0;
                        item.show_u = 0;
                        item.show_panel = 0;
                        clear_cutting_result_values(item);
                    }
                    var invalid_fields = invalid_cutting_fields(item);
                    if (invalid_fields.length) {
                        invalid_cutting_items.push({
                            idx: item.idx,
                            item_name: item.item_name || item.item_code,
                            template: t.parent,
                            fields: invalid_fields.join(", ")
                        });
                        clear_cutting_result_values(item);
                    }
                    item.stores.forEach(function(s) {
                        if (all_comps.indexOf(s.component) === -1) {
                            all_comps.push(s.component);
                        }
                        if (s.component_ar) { comp_labels[s.component] = s.component_ar; }
                    });

                    if (frm.doc.docstatus <= 1) {
                        frappe.model.set_value(item.cdt, item.cdn, "custom_cutting_template", t.parent);
                        frappe.model.set_value(item.cdt, item.cdn, "custom_result_leaf_w", item.lw || 0);
                        frappe.model.set_value(item.cdt, item.cdn, "custom_result_leaf_h", item.lh || 0);
                        frappe.model.set_value(item.cdt, item.cdn, "custom_result_u_h", item.uh || 0);
                        frappe.model.set_value(item.cdt, item.cdn, "custom_result_u_w", item.uw || 0);
                        frappe.model.set_value(item.cdt, item.cdn, "custom_result_panel_w", item.pw || 0);
                        frappe.model.set_value(item.cdt, item.cdn, "custom_result_panel_h", item.ph || 0);
                        frappe.model.set_value(item.cdt, item.cdn, "custom_result_leaf_w_text", item.lwt || '');
                        frappe.model.set_value(item.cdt, item.cdn, "custom_result_u_w_text", item.uwt || '');
                        frappe.model.set_value(item.cdt, item.cdn, "custom_result_panel_w_text", item.pwt || '');
                        frappe.model.set_value(item.cdt, item.cdn, "custom_show_square_count", item.show_square_count ? 1 : 0);
                        frappe.model.set_value(item.cdt, item.cdn, "custom_square_count", item.square_count || "");
                        frappe.model.set_value(item.cdt, item.cdn, "custom_show_glass_options", item.show_glass_options ? 1 : 0);
                        if (!item.show_glass_options) {
                            frappe.model.set_value(item.cdt, item.cdn, "custom_glass_type", "");
                            frappe.model.set_value(item.cdt, item.cdn, "custom_glass_model", "");
                        }
                        frappe.model.set_value(item.cdt, item.cdn, "custom_show_component_exclusions", item.show_component_exclusions ? 1 : 0);
                        frappe.model.set_value(item.cdt, item.cdn, "custom_store_data", item.stores.length ? JSON.stringify(item.stores) : "");
                    }
                } else if (frm.doc.docstatus <= 1) {
                    _item_split_support_map[item.cdn] = false;
                    _item_square_count_support_map[item.cdn] = false;
                    _item_glass_options_support_map[item.cdn] = false;
                    _item_component_exclusion_support_map[item.cdn] = false;
                    _item_frame_component_support_map[item.cdn] = false;
                    item.show_square_count = 0;
                    item.square_count = "";
                    item.show_glass_options = 0;
                    item.glass_type = "";
                    item.glass_model = "";
                    item.show_component_exclusions = 0;
                    item.excluded_components = "";
                    var empty_live_row = locals[item.cdt] && locals[item.cdt][item.cdn];
                    if (empty_live_row) {
                        empty_live_row.custom_show_glass_options = 0;
                        empty_live_row.custom_show_component_exclusions = 0;
                    }
                    frappe.model.set_value(item.cdt, item.cdn, "custom_cutting_template", "");
                    frappe.model.set_value(item.cdt, item.cdn, "custom_result_leaf_w", 0);
                    frappe.model.set_value(item.cdt, item.cdn, "custom_result_leaf_h", 0);
                    frappe.model.set_value(item.cdt, item.cdn, "custom_result_u_w", 0);
                    frappe.model.set_value(item.cdt, item.cdn, "custom_result_u_h", 0);
                    frappe.model.set_value(item.cdt, item.cdn, "custom_result_panel_w", 0);
                    frappe.model.set_value(item.cdt, item.cdn, "custom_result_panel_h", 0);
                    frappe.model.set_value(item.cdt, item.cdn, "custom_result_leaf_w_text", "");
                    frappe.model.set_value(item.cdt, item.cdn, "custom_result_u_w_text", "");
                    frappe.model.set_value(item.cdt, item.cdn, "custom_result_panel_w_text", "");
                    frappe.model.set_value(item.cdt, item.cdn, "custom_show_square_count", 0);
                    frappe.model.set_value(item.cdt, item.cdn, "custom_square_count", "");
                    frappe.model.set_value(item.cdt, item.cdn, "custom_show_glass_options", 0);
                    frappe.model.set_value(item.cdt, item.cdn, "custom_glass_type", "");
                    frappe.model.set_value(item.cdt, item.cdn, "custom_glass_model", "");
                    frappe.model.set_value(item.cdt, item.cdn, "custom_show_component_exclusions", 0);
                    frappe.model.set_value(item.cdt, item.cdn, "custom_store_data", "");
                }
            });

            // Sort store components by custom_print_sort_order
            function comp_sort_key(name) {
                if (_comp_sort_map) {
                    if (_comp_sort_map[name] !== undefined) {
                        return _comp_sort_map[name] * 1000 + name.length;
                    }
                    for (var key in _comp_sort_map) {
                        if (name.indexOf(key + ' ') === 0) {
                            return _comp_sort_map[key] * 1000 + name.length;
                        }
                    }
                }
                return 99000;
            }
            all_comps.sort(function(a, b) { return comp_sort_key(a) - comp_sort_key(b); });

            var is_editable = false;
            var has_sliding = items_to_check.some(function(r) { return r.sliding; });
            var has_type = items_to_check.some(function(r) { return r.item_type; });
            var has_stores = all_comps.length > 0;
            var has_net_leaf = items_to_check.some(function(r) { return r.net_leaf; });
            var has_parquet = items_to_check.some(function(r) { return r.parquet; });
            var has_taksiya1 = items_to_check.some(function(r) { return r.taksiya_1; });
            var has_taksiya2 = items_to_check.some(function(r) { return r.taksiya_2; });
            var has_no_qitaat = items_to_check.some(function(r) { return r.display_no_qitaat; });
            var has_without_kalon = items_to_check.some(function(r) { return r.without_kalon; });
            var has_square_count = items_to_check.some(function(r) { return r.show_square_count; });
            var has_glass_options = items_to_check.some(function(r) { return r.show_glass_options; });
            var has_component_exclusions = items_to_check.some(function(r) { return r.show_component_exclusions; });
            var has_frame_component = items_to_check.some(function(r) { return !!_item_frame_component_support_map[r.cdn] || !!String(r.frame_component || "").trim(); });
            var has_glass_value = items_to_check.some(function(r) {
                return String(r.glass_type || "").trim() || String(r.glass_model || "").trim();
            });
            var has_leaf = items_to_check.some(function(r) { return has_cutting_result_data(r.show_leaf, r.lw, r.lh, r.lwt); });
            var has_u = items_to_check.some(function(r) { return has_cutting_result_data(r.show_u, r.uw, r.uh, r.uwt); });
            var has_panel = items_to_check.some(function(r) { return has_cutting_result_data(r.show_panel, r.pw, r.ph, r.pwt); });

            var html = '<style>.cutting-input::-webkit-inner-spin-button,.cutting-input::-webkit-outer-spin-button{-webkit-appearance:none;margin:0}.cutting-input{-moz-appearance:textfield}</style>'
                + '<style>.cutting-num{direction:ltr;unicode-bidi:bidi-override}</style>'
                + '<style>'
                + '.cutting-results-wrap{direction:rtl;width:100%;min-height:320px;max-height:calc(100vh - 220px);overflow:auto;border:1px solid var(--border-color);border-radius:var(--border-radius-md);background:var(--card-bg);}'
                + '.cutting-results-wrap table{margin:0;min-width:100%;width:max-content;background:var(--card-bg);border-collapse:separate;border-spacing:0;font-variant-numeric:tabular-nums;font-feature-settings:\'tnum\';}'
                + '.cutting-results-wrap thead th{position:sticky;white-space:nowrap;color:var(--text-color);background:var(--bg-light-gray);z-index:3;outline:1px solid var(--border-color);outline-offset:-1px;box-shadow:inset 0 -1px 0 var(--border-color);}'
                + '.cutting-results-wrap thead tr:first-child th{top:0;z-index:4;background:var(--bg-light-gray);}'
                + '.cutting-results-wrap thead tr:nth-child(2) th{top:34px;z-index:5;background:var(--card-bg);}'
                + '.cutting-results-wrap tbody td{background:var(--card-bg);}'
                + '.cutting-results-wrap .cutting-sticky-idx{position:sticky;right:0;z-index:6;min-width:56px;width:56px;background:var(--card-bg);box-shadow:inset 1px 0 0 var(--border-color), inset -1px 0 0 var(--border-color);}'
                + '.cutting-results-wrap .cutting-sticky-item{position:sticky;right:56px;z-index:6;background:var(--card-bg);box-shadow:inset 1px 0 0 var(--border-color), inset -1px 0 0 var(--border-color);}'
                + '.cutting-results-wrap thead .cutting-sticky-idx,.cutting-results-wrap thead .cutting-sticky-item{background:var(--bg-light-gray);z-index:7;}'
                + '</style>'
                + '<div class="cutting-results-wrap">'
                + '<table class="table table-bordered" style="text-align:center;font-size:var(--text-sm)">'
                + '<thead>'
                + '<tr>'
                + '<th rowspan="2" class="cutting-sticky-idx" style="vertical-align:middle">#</th>'
                + '<th rowspan="2" class="cutting-sticky-item" style="vertical-align:middle;text-align:right">\u0627\u0644\u0635\u0646\u0641</th>'

                + (has_type ? '<th rowspan="2" style="vertical-align:middle">Type</th>' : '')
                + '<th rowspan="2" style="vertical-align:middle">\u0627\u0644\u0639\u0631\u0636</th>'
                + '<th rowspan="2" style="vertical-align:middle">\u0627\u0644\u0637\u0648\u0644</th>'
                + '<th rowspan="2" style="vertical-align:middle">\u0639\u0631\u0636 \u0627\u0644\u062c\u062f\u0627\u0631</th>'
                + (has_sliding ? '<th rowspan="2" style="vertical-align:middle">نوع السحاب</th>' : '')
                + '<th rowspan="2" style="vertical-align:middle">\u0627\u0644\u0646\u0645\u0648\u0630\u062c</th>'
                + (has_net_leaf ? '<th rowspan="2" style="vertical-align:middle">\u0635\u0627\u0641\u064a</th>' : '')
                + (has_parquet ? '<th rowspan="2" style="vertical-align:middle">\u0628\u0627\u0631\u0643\u064a\u0647</th>' : '')
                + (has_taksiya1 ? '<th rowspan="2" style="vertical-align:middle;font-size:9px">تكسية1</th>' : '')
                + (has_taksiya2 ? '<th rowspan="2" style="vertical-align:middle;font-size:9px">تكسية2</th>' : '')
                + (has_no_qitaat ? '<th rowspan="2" style="vertical-align:middle;font-size:9px">بدون أغراض تركيب</th>' : '')
                + (has_without_kalon ? '<th rowspan="2" style="vertical-align:middle;font-size:9px">بدون كالون</th>' : '')
                + (has_square_count ? '<th rowspan="2" style="vertical-align:middle;font-size:9px">عدد المربعات</th>' : '')
                + (has_glass_value ? '<th rowspan="2" style="vertical-align:middle;font-size:9px">الزجاج</th>' : '')
                + (has_leaf ? '<th colspan="2">الدرفة</th>' : '')
                + (has_u ? '<th colspan="2">U</th>' : '')
                + (has_panel ? '<th colspan="2">البانل</th>' : '');

            if (has_stores) {
                html += '<th colspan="' + all_comps.length + '">\u0627\u0644\u0645\u062e\u0627\u0632\u0646</th>';
            }

            html += '<th rowspan="2" style="vertical-align:middle">\u0645\u0644\u0627\u062d\u0638\u0627\u062a</th>'
                + '</tr>'
                + '<tr>'
                + (has_leaf ? '<th>W</th><th>H</th>' : '')
                + (has_u ? '<th>W</th><th>H</th>' : '')
                + (has_panel ? '<th>W</th><th>H</th>' : '');

            if (has_stores) {
                all_comps.forEach(function(comp) {
                    html += '<th>' + (comp_labels[comp] || comp) + '</th>';
                });
            }

            html += '</tr></thead><tbody>';

            var component_totals = {};
            all_comps.forEach(function(comp) {
                component_totals[comp] = 0;
            });

            items_to_check.forEach(function(r) {
                var w_over = r.max_w > 0 && r.w > r.max_w;
                var h_over = r.max_h > 0 && r.h > r.max_h;
                html += '<tr>'
                    + '<td class="cutting-sticky-idx">' + r.idx + '</td>'
                    + '<td class="cutting-sticky-item" style="text-align:right">' + r.item_name + '</td>'

                    + (has_type ? '<td>' + (r.item_type || '-') + '</td>' : '')
                    + '<td' + (w_over ? ' style="color:#dc2626" title="\u0623\u0642\u0635\u0649 \u0639\u0631\u0636: ' + r.max_w + '"' : '') + '>' + r.w + '</td>'
                    + '<td' + (h_over ? ' style="color:#dc2626" title="\u0623\u0642\u0635\u0649 \u0627\u0631\u062a\u0641\u0627\u0639: ' + r.max_h + '"' : '') + '>' + r.h + '</td>'
                    + '<td>' + r.ww + '</td>'
                    + (has_sliding ? '<td>' + (_option_labels[r.sliding] || r.sliding || '-') + '</td>' : '')
                    + '<td style="font-size:var(--text-xs)"><a href="/app/cutting-template/' + encodeURIComponent(r.tmpl) + '" target="_blank" style="color:var(--text-muted)">' + r.tmpl + '</a></td>'
                    + (has_net_leaf ? '<td>' + (r.net_leaf ? '\u2713' : '-') + '</td>' : '')
                    + (has_parquet ? '<td>' + (r.parquet ? '\u2713' : '-') + '</td>' : '')
                    + (has_taksiya1 ? '<td>' + (r.taksiya_1 ? '\u2713' : '-') + '</td>' : '')
                    + (has_taksiya2 ? '<td>' + (r.taksiya_2 ? '\u2713' : '-') + '</td>' : '')
                    + (has_no_qitaat ? '<td>' + (r.display_no_qitaat ? '\u2713' : '-') + '</td>' : '')
                    + (has_without_kalon ? '<td>' + (r.without_kalon ? '\u2713' : '-') + '</td>' : '')
                    + (has_square_count ? '<td>' + (r.square_count || '-') + '</td>' : '')
                    + (has_glass_value ? '<td>' + format_glass_options(r) + '</td>' : '')
                    + (has_leaf ? leaf_w_cell_html(r, is_editable) : '')
                    + (has_leaf ? cell_html(r.cdn, "custom_result_leaf_h", r.show_leaf ? r.lh : null, is_editable) : '')
                    + (has_u ? u_w_cell_html(r, is_editable) : '')
                    + (has_u ? cell_html(r.cdn, "custom_result_u_h", r.show_u ? r.uh : null, is_editable) : '')
                    + (has_panel ? panel_w_cell_html(r, is_editable) : '')
                    + (has_panel ? cell_html(r.cdn, "custom_result_panel_h", r.show_panel ? r.ph : null, is_editable) : '');

                if (has_stores) {
                    all_comps.forEach(function(comp) {
                        var matches = [];
                        for (var j = 0; j < r.stores.length; j++) {
                            if (r.stores[j].component === comp) matches.push(r.stores[j]);
                        }
                        if (matches.length === 0) {
                            html += '<td style="font-size:var(--text-xs)">-</td>';
                        } else if (matches.length === 1) {
                            var single_total = round_qty(matches[0].qty * r.item_qty);
                            component_totals[comp] = round_qty(component_totals[comp] + single_total);
                            html += '<td style="font-size:var(--text-xs)">' + format_qty_display(single_total) + '</td>';
                        } else {
                            var parts = [];
                            matches.forEach(function(m) {
                                var part_total = round_qty(m.qty * r.item_qty);
                                component_totals[comp] = round_qty(component_totals[comp] + part_total);
                                parts.push(format_qty_display(part_total));
                            });
                            html += '<td style="font-size:var(--text-xs)">' + parts.join('<br>') + '</td>';
                        }
                    });
                }

                html += text_cell_html(r.cdn, "\u0645\u0644\u0627\u062d\u0638\u0627\u062a", r.notes, is_editable)
                    + '</tr>';
            });

            if (has_stores) {
                var totalsRowStyle = 'background:var(--bg-light-gray);font-weight:600;border-top:1px solid var(--border-color)';
                var totalsEmptyCellStyle = 'background:var(--bg-light-gray);border-top:1px solid var(--border-color)';
                var totalsValueCellStyle = 'font-size:var(--text-xs);background:var(--bg-light-gray);border-top:1px solid var(--border-color)';
                var totalsTitleCellStyle = totalsEmptyCellStyle + ';text-align:right;color:var(--text-muted)';

                html += '<tr style="' + totalsRowStyle + '">'
                    + '<td class="cutting-sticky-idx" style="' + totalsEmptyCellStyle + '"></td>'
                    + '<td class="cutting-sticky-item" style="' + totalsTitleCellStyle + '">إجمالي الكميات</td>'
                    + (has_type ? '<td style="' + totalsEmptyCellStyle + '"></td>' : '')
                    + '<td style="' + totalsEmptyCellStyle + '"></td>'
                    + '<td style="' + totalsEmptyCellStyle + '"></td>'
                    + '<td style="' + totalsEmptyCellStyle + '"></td>'
                    + (has_sliding ? '<td style="' + totalsEmptyCellStyle + '"></td>' : '')
                    + '<td style="' + totalsEmptyCellStyle + '"></td>'
                    + (has_net_leaf ? '<td style="' + totalsEmptyCellStyle + '"></td>' : '')
                    + (has_parquet ? '<td style="' + totalsEmptyCellStyle + '"></td>' : '')
                    + (has_taksiya1 ? '<td style="' + totalsEmptyCellStyle + '"></td>' : '')
                    + (has_taksiya2 ? '<td style="' + totalsEmptyCellStyle + '"></td>' : '')
                    + (has_no_qitaat ? '<td style="' + totalsEmptyCellStyle + '"></td>' : '')
                    + (has_without_kalon ? '<td style="' + totalsEmptyCellStyle + '"></td>' : '')
                    + (has_square_count ? '<td style="' + totalsEmptyCellStyle + '"></td>' : '')
                    + (has_glass_value ? '<td style="' + totalsEmptyCellStyle + '"></td>' : '')
                    + (has_leaf ? '<td style="' + totalsEmptyCellStyle + '"></td><td style="' + totalsEmptyCellStyle + '"></td>' : '')
                    + (has_u ? '<td style="' + totalsEmptyCellStyle + '"></td><td style="' + totalsEmptyCellStyle + '"></td>' : '')
                    + (has_panel ? '<td style="' + totalsEmptyCellStyle + '"></td><td style="' + totalsEmptyCellStyle + '"></td>' : '');

                all_comps.forEach(function(comp) {
                    var total = round_qty(component_totals[comp]);
                    html += '<td style="' + totalsValueCellStyle + '">' + (total > 0 ? format_qty_display(total) : '-') + '</td>';
                });

                html += '<td style="' + totalsEmptyCellStyle + '"></td></tr>';
            }

            html += '</tbody></table></div>';
            frm.fields_dict.custom_cutting_results_html.$wrapper.html(html);

            if (is_editable) {
                frm.fields_dict.custom_cutting_results_html.$wrapper.find('.cutting-input').on('change', function() {
                    var cdn = $(this).data('cdn');
                    var field = $(this).data('field');
                    var val = flt($(this).val());
                    frappe.model.set_value("Material Request Item", cdn, field, val);
                });
                frm.fields_dict.custom_cutting_results_html.$wrapper.find('.cutting-text-input').on('change', function() {
                    var cdn = $(this).data('cdn');
                    var field = $(this).data('field');
                    var val = $(this).val();
                    frappe.model.set_value("Material Request Item", cdn, field, val);
                });

            }

            // Hide/show Option column and filter per item
            var _any_has_sliding = has_sliding;
            for (var _k in _item_sliding_options_map) {
                if (_item_sliding_options_map[_k].length > 0) { _any_has_sliding = true; break; }
            }
            var gridStateSignature = [
                _any_has_sliding ? 1 : 0,
                has_square_count ? 1 : 0,
                has_glass_options ? 1 : 0,
                has_component_exclusions ? 1 : 0,
                has_frame_component ? 1 : 0
            ].join("|");
            if (frm.__cutting_grid_state_signature !== gridStateSignature) {
                update_items_grid_docfield(frm, "custom_result_sliding_type", "hidden", _any_has_sliding ? 0 : 1);
                update_items_grid_docfield(frm, "custom_result_sliding_type", "label", "نوع السحاب");
                update_items_grid_docfield(frm, "custom_show_glass_options", "hidden", 1);
                update_items_grid_docfield(frm, "custom_glass_type", "hidden", has_glass_options ? 0 : 1);
                update_items_grid_docfield(frm, "custom_glass_model", "hidden", has_glass_options ? 0 : 1);
                update_items_grid_docfield(frm, "custom_show_component_exclusions", "hidden", 1);
                update_items_grid_docfield(frm, "custom_component_exclusion_group", "hidden", has_component_exclusions ? 0 : 1);
                update_items_grid_docfield(frm, "custom_excluded_store_components", "hidden", has_component_exclusions ? 0 : 1);
                update_items_grid_docfield(frm, "custom_excluded_store_components", "options", _component_exclusion_child_doctype);
                update_items_grid_docfield(frm, "custom_excluded_store_components", "read_only", 1);
                update_items_grid_docfield(frm, "custom_frame_type", "hidden", has_frame_component ? 0 : 1);
                toggle_items_grid_column(frm, "custom_leaf_count", false);
                toggle_items_grid_column(frm, "custom_split_type", false);
                toggle_items_grid_column(frm, "custom_fixed_leaf_width", false);
                toggle_items_grid_column(frm, "custom_show_square_count", false);
                toggle_items_grid_column(frm, "custom_square_count", has_square_count);
                toggle_items_grid_column(frm, "custom_show_glass_options", false);
                toggle_items_grid_column(frm, "custom_glass_type", has_glass_options);
                toggle_items_grid_column(frm, "custom_glass_model", has_glass_options);
                toggle_items_grid_column(frm, "custom_show_component_exclusions", false);
                toggle_items_grid_column(frm, "custom_component_exclusion_group", false);
                toggle_items_grid_column(frm, "custom_excluded_store_components", false);
                toggle_items_grid_column(frm, "custom_frame_type", has_frame_component);
                frm.fields_dict.items.grid.refresh();
                frm.__cutting_grid_state_signature = gridStateSignature;
            }
            safe_apply_component_exclusion_options(frm);
            ((frm.fields_dict.items.grid && frm.fields_dict.items.grid.grid_rows) || []).forEach(function(grid_row) {
                if (grid_row && grid_row.doc) {
                    apply_split_fields_visibility(frm, grid_row.doc.name);
                    apply_square_count_visibility(frm, grid_row.doc.name);
                    apply_glass_options_visibility(frm, grid_row.doc.name);
                    apply_component_exclusion_visibility(frm, grid_row.doc.name);
                    apply_frame_component_visibility(frm, grid_row.doc.name);
                    apply_sliding_type_visibility(frm, grid_row.doc.name);
                }
            });

            frm.set_query("custom_result_sliding_type", "items", function(doc, cdt, cdn) {
                return get_sliding_query_for_row(cdn);
            });
            frm.fields_dict.items.grid.get_field("custom_result_sliding_type").get_query = function(doc, cdt, cdn) {
                return get_sliding_query_for_row(cdn);
            };
            (frm.fields_dict.items.grid.grid_rows || []).forEach(function(grid_row) {
                if (grid_row && grid_row.doc) {
                    apply_sliding_type_query(frm, grid_row.doc.name);
                    apply_sliding_type_visibility(frm, grid_row.doc.name);
                }
            });

            var invalidSignature = invalid_cutting_items.map(function(entry) {
                return entry.idx + ":" + entry.fields;
            }).join("|");
            if (invalidSignature) {
                if (frm.__last_invalid_cutting_signature !== invalidSignature) {
                    var rowsText = invalid_cutting_items.map(function(entry) {
                        return "#" + entry.idx;
                    }).join("، ");
                    frappe.show_alert({
                        message: "تم تجاهل حفظ نتائج سالبة في السطور " + rowsText + ". راجع نموذج التخصيم.",
                        indicator: "orange"
                    }, 9);
                }
                frm.__last_invalid_cutting_signature = invalidSignature;
            } else {
                frm.__last_invalid_cutting_signature = "";
            }

            var incompleteSignature = incomplete_cutting_items.map(function(entry) {
                return entry.idx;
            }).join("|");
            if (incompleteSignature) {
                if (frm.__last_incomplete_cutting_signature !== incompleteSignature) {
                    var incompleteRowsText = incomplete_cutting_items.map(function(entry) {
                        return "#" + entry.idx;
                    }).join("، ");
                    frappe.show_alert({
                        message: "تم تجاهل التخصيم في السطور " + incompleteRowsText + " لأن العرض أو الطول غير مكتمل.",
                        indicator: "orange"
                    }, 9);
                }
                frm.__last_incomplete_cutting_signature = incompleteSignature;
            } else {
                frm.__last_incomplete_cutting_signature = "";
            }

        } finally {
            _cutting_rendering = false;
        }
    }

    function has_cutting_display_text(value) {
        var text = String(value === null || value === undefined ? "" : value).trim();
        return !!text && text !== "-";
    }

    function has_cutting_display_number(value) {
        if (value === null || value === undefined || value === "") {
            return false;
        }
        return flt(value) > 0;
    }

    function has_cutting_result_data(show_result, width_value, height_value, width_text) {
        return !!show_result && (
            has_cutting_display_text(width_text)
            || has_cutting_display_number(width_value)
            || has_cutting_display_number(height_value)
        );
    }

    function panel_w_cell_html(r, is_editable) {
        if (!r.show_panel) {
            return '<td>-</td>';
        }
        if (r.pw === null || r.pw === undefined || r.pw === "") {
            return '<td>-</td>';
        }
        if (r.pwt) {
            return '<td style="font-weight:bold;font-size:var(--text-xs)">' + r.pwt + '</td>';
        }
        if (r.leaf_count <= 1 || !r.pw) {
            return cell_html(r.cdn, "custom_result_panel_w", r.pw, is_editable);
        }
        var panels = split_panel_width_parts(r);
        var display = panels.join(' + ');
        var total = Math.round(panels.reduce(function(sum, value) { return sum + flt(value); }, 0) * 10) / 10;
        return '<td style="font-weight:bold;font-size:var(--text-xs)" title="' + total + ' \u2192 ' + display + '">' + display + '</td>';
    }

    function u_w_cell_html(r, is_editable) {
        if (!r.show_u) {
            return '<td>-</td>';
        }
        if (r.uw === null || r.uw === undefined || r.uw === "") {
            return '<td>-</td>';
        }
        if (r.uwt) {
            return '<td style="font-weight:bold;font-size:var(--text-xs)">' + r.uwt + '</td>';
        }
        return cell_html(r.cdn, "custom_result_u_w", r.uw, is_editable);
    }

    function split_width_parts(total, leaf_count, split_type, fixed_leaf_w) {
        var count = parseInt(leaf_count, 10) || 1;
        var totalWidth = flt(total);
        if (count <= 1) {
            return [Math.round(totalWidth * 10) / 10];
        }
        var fixedWidth = flt(fixed_leaf_w);
        if (split_type === 'ثابت' && fixedWidth > 0) {
            var remainingWidth = totalWidth - fixedWidth;
            var otherWidth = Math.round(remainingWidth / (count - 1) * 10) / 10;
            var fixedParts = [Math.round(fixedWidth * 10) / 10];
            for (var i = 1; i < count; i++) {
                fixedParts.push(otherWidth);
            }
            return fixedParts;
        }
        var eachWidth = Math.round(totalWidth / count * 10) / 10;
        var equalParts = [];
        for (var i = 0; i < count; i++) {
            equalParts.push(eachWidth);
        }
        return equalParts;
    }

    function split_panel_width_parts(r) {
        var leafParts = split_width_parts(r.lw, r.leaf_count, r.split_type, r.fixed_leaf_w);
        var panelDelta = flt(r.pw) - flt(r.lw);
        return leafParts.map(function(leafPart) {
            return Math.round((flt(leafPart) + panelDelta) * 10) / 10;
        });
    }

    function leaf_w_cell_html(r, is_editable) {
        if (!r.show_leaf) {
            return '<td>-</td>';
        }
        if (r.lw === null || r.lw === undefined || r.lw === "") {
            return '<td>-</td>';
        }
        if (r.lwt) {
            return '<td style="font-weight:bold;font-size:var(--text-xs)">' + r.lwt + '</td>';
        }
        if (r.leaf_count <= 1) {
            return cell_html(r.cdn, "custom_result_leaf_w", r.lw, is_editable);
        }
        var leaves = split_width_parts(r.lw, r.leaf_count, r.split_type, r.fixed_leaf_w);
        var display = leaves.join(' + ');
        return '<td style="font-weight:bold;font-size:var(--text-xs)" title="' + r.lw + ' \u2192 ' + display + '">' + display + '</td>';
    }

    function cell_html(cdn, field, val, is_editable) {
        var is_empty = (val === null || val === undefined || val === '');
        if (is_editable) {
            return '<td style="padding:2px">'
                + '<input type="number" class="cutting-input" '
                + 'data-cdn="' + cdn + '" data-field="' + field + '" '
                + 'value="' + (is_empty ? '' : val) + '" '
                + 'style="width:100%;text-align:center;border:none;background:transparent;'
                + 'font-weight:bold;font-size:var(--text-sm);padding:4px;outline:none"'
                + '></td>';
        }
        return '<td><b>' + (is_empty ? '-' : val) + '</b></td>';
    }

    function text_cell_html(cdn, field, val, is_editable) {
        if (is_editable) {
            return '<td style="padding:2px">'
                + '<input type="text" class="cutting-text-input" '
                + 'data-cdn="' + cdn + '" data-field="' + field + '" '
                + 'value="' + (val || '').replace(/"/g, '&quot;') + '" '
                + 'style="width:100%;text-align:center;border:none;background:transparent;'
                + 'font-size:var(--text-sm);padding:4px;outline:none;min-width:80px"'
                + '></td>';
        }
        return '<td>' + (val || '-') + '</td>';
    }

    function strip_html(str) {
        if (!str) return "";
        var tmp = document.createElement("div");
        tmp.innerHTML = str;
        return (tmp.textContent || tmp.innerText || "").trim();
    }

    function round_qty(value) {
        var num = flt(value);
        var sign = num < 0 ? -1 : 1;
        var scaled = Math.abs(num) * 10;
        return sign * (Math.floor(scaled + 0.5) / 10);
    }

    function format_qty_display(value) {
        var num = round_qty(value);
        if (Math.abs(num) < 0.000001) {
            num = 0;
        }
        if (Math.abs(num - Math.round(num)) < 0.000001) {
            return String(Math.round(num));
        }
        return String(num).replace(/(\.\d*?[1-9])0+$/, '$1').replace(/\.0+$/, '');
    }
    // END legacy Client Script: حساب التخصيم
  }
  if (!window.__namar_test_loaded_scripts["كل طلبات المواد"]) {
    window.__namar_test_loaded_scripts["كل طلبات المواد"] = true;
    // BEGIN legacy Client Script: كل طلبات المواد
    frappe.ui.form.on('Material Request', {
        refresh: function(frm) {
            load_sales_order_summary(frm);
        },
        sales_order: function(frm) {
            load_sales_order_summary(frm);
        }
    });

    frappe.ui.form.on('Material Request Item', {
        item_code: function(frm) {
            queue_summary_refresh(frm);
        },
        qty: function(frm) {
            queue_summary_refresh(frm);
        },
        items_add: function(frm) {
            queue_summary_refresh(frm);
        },
        items_remove: function(frm) {
            queue_summary_refresh(frm);
        }
    });

    function get_current_items_payload(frm) {
        return (frm.doc.items || [])
            .filter(function(row) {
                return row.item_code;
            })
            .map(function(row) {
                return {
                    item_code: row.item_code,
                    item_name: row.item_name || '',
                    qty: flt(row.qty)
                };
            });
    }

    function queue_summary_refresh(frm) {
        if (!frm || !frm.doc.sales_order) return;
        if (frm._sales_order_summary_timer) {
            window.clearTimeout(frm._sales_order_summary_timer);
        }
        frm._sales_order_summary_timer = window.setTimeout(function() {
            load_sales_order_summary(frm);
        }, 250);
    }

    function clear_summary(frm) {
        if (!frm.fields_dict.custom_sales_order_summary) return;
        frm.set_df_property('custom_sales_order_summary', 'hidden', 1);
        frm.fields_dict.custom_sales_order_summary.$wrapper.html('');
    }

    function load_sales_order_summary(frm) {
        if (!frm.fields_dict.custom_sales_order_summary) return;

        if (!frm.doc.sales_order) {
            clear_summary(frm);
            return;
        }

        frappe.call({
            method: 'get_related_items',
            args: {
                sales_order: frm.doc.sales_order,
                mr_name: frm.doc.name || '',
                current_items: JSON.stringify(get_current_items_payload(frm))
            },
            freeze: false,
            callback: function(r) {
                if (r.message && r.message.length) {
                    render_summary_table(frm, r.message, frm.doc.sales_order);
                } else {
                    clear_summary(frm);
                }
            }
        });
    }

    function escape_html(value) {
        return frappe.utils.escape_html(value == null ? '' : String(value));
    }

    function balance_style(value) {
        return 'color: ' + (flt(value) < 0 ? '#dc3545' : '#28a745') + '; font-weight: bold;';
    }

    function signed_metric_style(value) {
        var numeric = flt(value);
        if (numeric > 0) return 'color: #28a745; font-weight: bold;';
        if (numeric < 0) return 'color: #dc3545; font-weight: bold;';
        return 'font-weight: bold;';
    }

    function render_metric_cell(mainValue, subLabel, subValue, mainStyle, subStyle) {
        var html = '<div style="line-height: 1.45; text-align: right;">';
        html += '<div style="' + (mainStyle || 'font-weight: bold;') + '">' + format_number(mainValue, null, 2) + '</div>';
        if (subLabel) {
            html += '<div style="font-size: 10px; color: var(--text-muted); margin-top: 2px;">';
            html += '<span>' + escape_html(subLabel) + ':</span> ';
            html += '<span style="' + (subStyle || '') + '">' + format_number(subValue, null, 2) + '</span>';
            html += '</div>';
        }
        html += '</div>';
        return html;
    }

    function render_summary_table(frm, data, sales_order) {
        var html = '<div style="margin-top: 10px; margin-bottom: 10px;">';
        html += '<div class="form-group"><div class="clearfix"><label class="control-label" style="font-weight: bold;">';
        html += 'ملخص شامل لأمر البيع: <a href="/app/sales-order/' + sales_order + '" target="_blank">' + sales_order + '</a>';
        html += '</label></div></div>';
        html += '<div style="font-size: 11px; color: var(--text-muted); margin-bottom: 8px;">';
        html += 'تم طلبه يعرض الحالي مع الكلي، والمتبقي يعرض الكلي فقط، وبقية الأعمدة تعرض الحالي فقط.';
        html += '</div>';
        html += '<table class="table table-bordered table-sm" style="font-size: var(--text-sm);">';
        html += '<thead><tr style="background-color: var(--table-bg); color: var(--text-muted);">';
        html += '<th width="20%">الصنف</th><th width="8%" class="text-right">مطلوب (SO)</th><th width="8%" class="text-right">تم طلبه (MR)</th><th width="8%" class="text-right">المتبقي (MR)</th><th width="8%" class="text-right">مسلمة</th><th width="8%" class="text-right" style="color: #28a745;">مفوترة</th><th width="8%" class="text-right" style="color: #007bff;">تم تركيبه</th><th width="11%" class="text-right">رصيد المفوتر</th><th width="11%" class="text-right">رصيد المسلم</th><th width="10%" class="text-right">رصيد التركيب</th>';
        html += '</tr></thead><tbody>';

        for (var i = 0; i < data.length; i++) {
            var row = data[i];
            var name_suffix = '';
            if (row.is_extra) {
                name_suffix = '<span class="indicator-pill red" style="margin-right: 5px; font-size: 10px;">إضافي</span>';
            }

            html += '<tr>';
            html += '<td>' + name_suffix + '<span style="font-weight: bold; color: var(--text-color);">' + row.item_code + '</span><br><span style="font-size: 11px; color: var(--text-muted);">' + row.item_name + '</span></td>';
            html += '<td class="text-right">' + format_number(row.so_qty, null, 2) + '</td>';
            html += '<td class="text-right">' + render_metric_cell(row.current_mr_qty, 'الكلي', row.mr_qty, 'font-weight: bold;', '') + '</td>';
            html += '<td class="text-right"><div style="' + balance_style(row.balance) + '">' + format_number(row.balance, null, 2) + '</div></td>';
            html += '<td class="text-right"><div style="font-weight: bold;">' + format_number(row.current_delivered_qty, null, 2) + '</div></td>';
            html += '<td class="text-right"><div style="font-weight: bold;">' + format_number(row.current_billed_qty, null, 2) + '</div></td>';
            html += '<td class="text-right"><div style="font-weight: bold;">' + format_number(row.current_installed_qty, null, 2) + '</div></td>';
            html += '<td class="text-right"><div style="' + signed_metric_style(row.current_billed_balance) + '">' + format_number(row.current_billed_balance, null, 2) + '</div></td>';
            html += '<td class="text-right"><div style="' + signed_metric_style(row.current_delivered_balance) + '">' + format_number(row.current_delivered_balance, null, 2) + '</div></td>';
            html += '<td class="text-right"><div style="' + signed_metric_style(row.current_installed_balance) + '">' + format_number(row.current_installed_balance, null, 2) + '</div></td>';
            html += '</tr>';
        }

        html += '</tbody></table></div>';
        frm.set_df_property('custom_sales_order_summary', 'hidden', 0);
        frm.fields_dict.custom_sales_order_summary.$wrapper.html(html);
    }
    // END legacy Client Script: كل طلبات المواد
  }
})();
