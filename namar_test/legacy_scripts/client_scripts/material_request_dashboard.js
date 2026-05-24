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
