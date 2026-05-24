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
