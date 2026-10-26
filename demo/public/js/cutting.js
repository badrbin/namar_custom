// Demo app: migrated from the TEST Client Script "حساب التخصيم".
// Activation is controlled by Demo Settings through the server-provided boot flag.
;(function () {
    if (!frappe.boot?.demo_cutting_enabled) return;
    if (window.__demo_cutting_loaded) return;
    window.__demo_cutting_loaded = true;

// BEGIN MIGRATED CLIENT SCRIPT
// NAMAR_CANCELLED_ROW_OPERATIONS_V1
var _cutting_rendering = false;
var _option_labels = null;
var _comp_sort_map = null;
var _item_split_support_map = {};
var _item_sliding_options_map = {};
var _item_square_count_support_map = {};
var _item_glass_options_support_map = {};
var _item_frame_component_support_map = {};
var _store_component_options = [];
var _frame_component_options = [];
var _cutting_result_cache = {};
var _cutting_render_timer = null;
var _cutting_pending_render = false;
var _cutting_applying_results = false;
var _cutting_cache_version = "row-cache-v7";
var _component_exclusion_group_options = [];
var _component_exclusion_group_options_loaded = false;
var _manufacture_stock_entry_running = false;
var COMPONENT_EXCLUSION_CHECKBOX_GROUPS = [
    { fieldname: "custom_exception_component_1", group: "exception component 1" },
    { fieldname: "custom_exception_component_2", group: "exception component 2" }
];

var COMPONENT_MANUFACTURING_POLICY_DEFAULT = "حسب إعداد المكون";
var COMPONENT_MANUFACTURING_POLICY_EXCLUDED = "لا يدخل في التصنيع";
var COMPONENT_MANUFACTURING_POLICY_INCLUDED = "يدخل في التصنيع";

function normalize_component_manufacturing_policy(value) {
    var text = String(value || "").trim();
    if (text === COMPONENT_MANUFACTURING_POLICY_EXCLUDED || text === COMPONENT_MANUFACTURING_POLICY_INCLUDED) {
        return text;
    }
    return COMPONENT_MANUFACTURING_POLICY_DEFAULT;
}

function is_component_manufacturing_excluded(row) {
    return normalize_component_manufacturing_policy(row && row.custom_component_manufacturing_policy) === COMPONENT_MANUFACTURING_POLICY_EXCLUDED;
}

function set_component_manufacturing_policy_value(cdt, cdn, value) {
    if (!cdt || !cdn) return;
    if (frappe.meta && typeof frappe.meta.get_docfield === "function" && !frappe.meta.get_docfield(cdt, "custom_component_manufacturing_policy")) {
        return;
    }
    frappe.model.set_value(cdt, cdn, "custom_component_manufacturing_policy", normalize_component_manufacturing_policy(value));
}

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
        + "||" + normalize_component_exclusion_groups(item.component_exclusion_groups || "")
        + "||" + (item.frame_component || "");
}

function get_cutting_cache_key(item) {
    return _cutting_cache_version + "|" + get_cutting_request_key(item);
}

function parse_component_exclusion_groups(value) {
    if (!value) return [];
    var raw = value;
    if (Array.isArray(raw)) {
        return raw.map(function(v) {
            if (v && typeof v === "object") {
                return String(v.component_exclusion_group || v.group_name || v.value || v.label || v.name || "").trim();
            }
            return String(v || "").trim();
        }).filter(Boolean);
    }
    var text = String(raw || "").trim();
    if (!text) return [];
    try {
        var parsed = JSON.parse(text);
        if (Array.isArray(parsed)) {
            return parse_component_exclusion_groups(parsed);
        }
    } catch (e) {
        // MultiSelectPills may store comma-separated text depending on Frappe version.
    }
    return text.replace(/\n/g, "|").replace(/;/g, "|").replace(/,/g, "|").split("|")
        .map(function(v) { return String(v || "").trim(); })
        .filter(Boolean);
}

function normalize_component_exclusion_groups(value) {
    var seen = {};
    return parse_component_exclusion_groups(value).filter(function(group) {
        if (seen[group]) return false;
        seen[group] = true;
        return true;
    }).sort().join("|");
}

function component_exclusion_groups_from_row(row) {
    var groups = [];
    COMPONENT_EXCLUSION_CHECKBOX_GROUPS.forEach(function(option) {
        if (component_exception_checked(row, option)) {
            groups.push(option.group);
        }
    });
    return normalize_component_exclusion_groups(groups);
}

function component_exception_checked(row, option) {
    if (!row || !option) return 0;
    if (parseInt(row[option.fieldname], 10) || 0) {
        return 1;
    }
    return 0;
}

function sync_component_exclusion_groups_from_checkboxes(frm, cdt, cdn) {
    var row = locals[cdt] && locals[cdt][cdn];
    if (!row) return;
    var normalized = component_exclusion_groups_from_row(row);
    if (String(row.custom_component_exclusion_groups || "") !== normalized) {
        frappe.model.set_value(cdt, cdn, "custom_component_exclusion_groups", normalized);
    }
}

function get_component_exclusion_group_data(txt) {
    var query = String(txt || "").toLowerCase();
    return (_component_exclusion_group_options || []).filter(function(group) {
        return !query || String(group || "").toLowerCase().indexOf(query) !== -1;
    }).map(function(group) {
        return {
            value: group,
            label: group,
            description: group
        };
    });
}

function load_component_exclusion_group_options(callback) {
    if (_component_exclusion_group_options_loaded) {
        if (callback) callback();
        return;
    }
    frappe.call({
        method: "frappe.client.get_list",
        args: {
            doctype: "Store Component Exclusion Group",
            filters: { is_active: 1 },
            fields: ["name"],
            limit_page_length: 200,
            order_by: "name asc"
        },
        callback: function(r) {
            _component_exclusion_group_options = ((r && r.message) || []).map(function(row) {
                return String(row.name || "").trim();
            }).filter(Boolean);
            _component_exclusion_group_options_loaded = true;
            if (callback) callback();
        },
        error: function() {
            _component_exclusion_group_options_loaded = true;
            if (callback) callback();
        }
    });
}

function apply_component_exclusion_multiselect_df(df) {
    if (!df) return;
    df.fieldtype = "MultiSelectList";
    df.options = "";
    df.get_data = function(txt) {
        return get_component_exclusion_group_data(txt);
    };
    df.hidden = 0;
}

function normalize_component_exclusion_group_values(frm) {
    (frm.doc.items || []).forEach(function(row) {
        row.custom_component_exclusion_groups = component_exclusion_groups_from_row(row);
    });
}

function get_component_exclusion_control_wrapper(control) {
    if (!control) return null;
    return control.$wrapper || (control.wrapper ? $(control.wrapper) : null);
}

function read_selected_component_exclusion_groups(control) {
    var value = "";
    if (control && typeof control.get_value === "function") {
        value = control.get_value();
    }
    var wrapper = get_component_exclusion_control_wrapper(control);
    if ((!value || (Array.isArray(value) && !value.length)) && wrapper) {
        var selected = [];
        wrapper.find(".selectable-item.selected").each(function() {
            var item = $(this);
            var label = item.find("strong").first().text().trim()
                || decodeURIComponent(item.attr("data-value") || "").trim();
            if (label) {
                selected.push(label);
            }
        });
        value = selected;
    }
    return normalize_component_exclusion_groups(value);
}

function sync_component_exclusion_control_value(frm, control, row) {
    if (!row) return;
    var normalized = read_selected_component_exclusion_groups(control);
    if (String(row.custom_component_exclusion_groups || "") !== normalized) {
        frappe.model.set_value(row.doctype, row.name, "custom_component_exclusion_groups", normalized);
    }
    request_cutting_render(frm);
}

function patch_component_exclusion_multiselect_model_sync(control) {
    if (!control || control.__component_exclusion_model_sync_patched) return;
    if (typeof control.parse_validate_and_set_in_model !== "function") return;
    var original_parse_validate_and_set = control.parse_validate_and_set_in_model;
    control.parse_validate_and_set_in_model = function(value) {
        var normalized = normalize_component_exclusion_groups(
            typeof this.get_value === "function" ? this.get_value() : value
        );
        return original_parse_validate_and_set.call(this, normalized);
    };
    control.__component_exclusion_model_sync_patched = true;
}

function bind_component_exclusion_control(frm, control, row) {
    var wrapper = get_component_exclusion_control_wrapper(control);
    if (!control || !wrapper || control.__component_exclusion_bound) return;
    patch_component_exclusion_multiselect_model_sync(control);
    control.__component_exclusion_bound = true;
    wrapper.on(
        "click.component_exclusions",
        ".selectable-item, .select-all-options, .clear-selections",
        function() {
            setTimeout(function() {
                sync_component_exclusion_control_value(frm, control, row);
            }, 100);
        }
    );
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

function normalize_delivery_component_tracking_route(value) {
    var route = String(value || "").trim();
    var aliases = {
        "تصنيع وتغليف": "تصنيع مستقل - باركود",
        "تجهيز فقط": "توريد فقط - بدون باركود",
        "لا يتتبع": "مستبعد"
    };
    return aliases[route] || route || "توريد فقط - بدون باركود";
}

function is_delivery_component_barcode_route(row) {
    return normalize_delivery_component_tracking_route(row && row.tracking_route) === "تصنيع مستقل - باركود";
}

function get_delivery_component_package_rows(frm) {
    return (frm.doc.custom_delivery_component_packages || []).filter(function(row) {
        var active = row.active;
        var isActive = active === undefined || active === null || active === "" || Number(active) === 1;
        return isActive && normalize_delivery_component_tracking_route(row.tracking_route) !== "مستبعد";
    });
}

function form_has_unsaved_delivery_changes(frm) {
    if (!frm) return false;
    if (typeof frm.is_dirty === "function" && frm.is_dirty()) {
        return true;
    }
    return !!(frm.doc && frm.doc.__unsaved);
}

function get_pending_delivery_component_package_rows(frm) {
    return get_delivery_component_package_rows(frm).filter(function(row) {
        return is_delivery_component_barcode_route(row) && flt(row.remaining_qty || 0) > 0;
    });
}

function get_delivery_component_print_url(frm) {
    return "/printview?doctype=Material%20Request&name="
        + encodeURIComponent(frm.doc.name || "")
        + "&format="
        + encodeURIComponent("ملصق قطاعات التصنيع 4x3")
        + "&no_letterhead=1";
}

function get_manufacturing_print_url(frm) {
    return "/printview?doctype=Material%20Request&name="
        + encodeURIComponent(frm.doc.name || "")
        + "&format="
        + encodeURIComponent("ملصق تصنيع 4x3")
        + "&no_letterhead=1";
}

function get_kdt_print_url(frm) {
    return "/printview?doctype=Material%20Request&name="
        + encodeURIComponent(frm.doc.name || "")
        + "&format="
        + encodeURIComponent("ملصق KDT 4x3")
        + "&no_letterhead=1";
}

function sync_delivery_component_packages(frm) {
    if (frm.is_new()) {
        frappe.msgprint("احفظ طلب المواد قبل تحديث حزم مكونات التوريد.");
        return;
    }
    if (form_has_unsaved_delivery_changes(frm)) {
        frappe.msgprint("احفظ طلب المواد أولًا حتى يتم تحديث الحزم من آخر نتائج تخصيم محفوظة.");
        return;
    }
    frappe.call({
        method: "sync_delivery_component_packages",
        args: { mr: frm.doc.name },
        freeze: true,
        freeze_message: "جاري تحديث حزم مكونات التوريد...",
        callback: function(r) {
            var msg = r.message || {};
            frappe.show_alert({
                message: "تم تحديث المكونات، وحزم الباركود: " + (msg.printable_package_count || 0) + ".",
                indicator: "green"
            }, 7);
            frm.reload_doc();
        }
    });
}

var delivery_component_print_in_progress = false;

function close_delivery_component_print_window(printWindow) {
    if (!printWindow || printWindow.closed) {
        return;
    }
    try {
        printWindow.close();
    } catch (error) {
        // The browser may revoke access after navigation; there is nothing else to clean up.
    }
}

function show_delivery_component_print_fallback(frm) {
    var url = get_delivery_component_print_url(frm);
    frappe.msgprint({
        title: "ملصقات القطاعات جاهزة",
        indicator: "green",
        message: '<div dir="rtl" style="text-align:right;">منع المتصفح فتح نافذة الطباعة تلقائيًا. '
            + '<a class="btn btn-primary btn-sm" target="_blank" rel="noopener" href="' + escape_html_text(url) + '">فتح الملصقات</a></div>'
    });
}

function prepare_and_print_delivery_component_packages(frm) {
    if (delivery_component_print_in_progress) {
        return;
    }
    if (frm.is_new()) {
        frappe.msgprint("احفظ طلب المواد قبل طباعة باركود القطاعات.");
        return;
    }
    if (form_has_unsaved_delivery_changes(frm)) {
        frappe.msgprint("احفظ طلب المواد أولًا حتى يتم تجهيز الحزم من آخر نتائج تخصيم محفوظة.");
        return;
    }

    delivery_component_print_in_progress = true;

    var printWindow = window.open("about:blank", "_blank");
    if (printWindow) {
        try {
            printWindow.document.open();
            printWindow.document.write(
                '<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">'
                + '<title>جاري تجهيز ملصقات القطاعات</title></head>'
                + '<body style="font-family:sans-serif; padding:40px; text-align:center;">'
                + '<h2>جاري تجهيز حزم القطاعات...</h2>'
                + '<p>ستفتح الملصقات تلقائيًا بعد اكتمال التجهيز.</p></body></html>'
            );
            printWindow.document.close();
        } catch (error) {
            // Continue with the sync; a fallback link is shown if navigation is unavailable.
        }
    }

    frappe.call({
        method: "sync_delivery_component_packages",
        args: { mr: frm.doc.name },
        freeze: true,
        freeze_message: "جاري تجهيز حزم القطاعات للطباعة...",
        callback: function(r) {
            var msg = r.message || {};
            var packageCount = Number(msg.printable_package_count || 0);
            if (!packageCount) {
                delivery_component_print_in_progress = false;
                close_delivery_component_print_window(printWindow);
                frappe.msgprint("لا توجد قطاعات قابلة للتغليف والطباعة في طلب المواد الحالي.");
                frm.reload_doc();
                return;
            }

            if (printWindow && !printWindow.closed) {
                try {
                    printWindow.location.replace(get_delivery_component_print_url(frm));
                } catch (error) {
                    close_delivery_component_print_window(printWindow);
                    show_delivery_component_print_fallback(frm);
                }
            } else {
                show_delivery_component_print_fallback(frm);
            }

            frappe.show_alert({
                message: "تم تجهيز " + packageCount + " حزمة قطاعات وفتح ملصقاتها للطباعة.",
                indicator: "green"
            }, 7);
            delivery_component_print_in_progress = false;
            frm.reload_doc();
        },
        error: function() {
            delivery_component_print_in_progress = false;
            close_delivery_component_print_window(printWindow);
        }
    });
}

window.sync_delivery_component_packages_for_current_form = function() {
    if (window.cur_frm) {
        sync_delivery_component_packages(window.cur_frm);
    }
};

window.prepare_delivery_component_packages_for_print_for_current_form = function() {
    if (window.cur_frm) {
        prepare_and_print_delivery_component_packages(window.cur_frm);
    }
};

function render_manual_delivery_component_package_rows(rows) {
    if (!rows || !rows.length) {
        return '<div style="padding:16px; color:var(--text-muted);">لا توجد حزم غير جاهزة للتسجيل اليدوي.</div>';
    }

    return rows.map(function(row) {
        var token = row.barcode_key || row.name || row.package_key || "";
        var loadingCode = row.loading_code || "";
        var doorReference = format_delivery_component_door_reference(row);
        var itemText = row.item_code ? ' | الصنف: ' + escape_html_text(row.item_code) : "";
        var searchText = [
            loadingCode,
            row.component_label || row.component || "",
            row.package_label || "",
            row.package_no || "",
            row.package_count || "",
            doorReference,
            row.item_code || "",
            format_manufacturing_count(row.package_qty || 0)
        ].join(" ").toLowerCase();
        return ''
            + '<div class="manual-delivery-component-package-row" data-search="' + escape_html_text(searchText) + '" style="display:grid; grid-template-columns:24px minmax(0,1fr); align-items:center; gap:10px; padding:10px 0; border-bottom:1px solid var(--border-color);">'
            +   '<input type="checkbox" class="manual-delivery-component-package-checkbox" value="' + escape_html_text(token) + '" style="width:18px; height:18px;">'
            +   '<div style="min-width:0;">'
            +     '<div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap; font-weight:800;">'
            +       (loadingCode ? '<span style="direction:ltr; display:inline-flex; padding:2px 8px; border-radius:999px; background:var(--control-bg); border:1px solid var(--border-color); font-size:12px;">' + escape_html_text(loadingCode) + '</span>' : '')
            +       '<span>' + escape_html_text(row.component_label || row.component || "-") + ' | ' + escape_html_text(row.package_label || "حزمة") + '</span>'
            +     '</div>'
            +     '<div style="color:var(--text-muted); font-size:12px; margin-top:3px;">'
            +       (doorReference ? escape_html_text(doorReference) : 'الحزمة: ' + escape_html_text(row.package_no || "-") + '/' + escape_html_text(row.package_count || "-"))
            +       itemText
            +       ' | عدد القطع: ' + format_manufacturing_count(row.package_qty || 0)
            +     '</div>'
            +   '</div>'
            + '</div>';
    }).join("");
}

function bind_manual_delivery_component_package_search(dialog) {
    if (!dialog || !dialog.fields_dict || !dialog.fields_dict.help_html || !dialog.fields_dict.rows_html) return;
    var helpWrapper = dialog.fields_dict.help_html.$wrapper;
    var rowsWrapper = dialog.fields_dict.rows_html.$wrapper;
    if (!helpWrapper || !rowsWrapper) return;

    helpWrapper.find(".manual-delivery-component-search").on("input", function() {
        var query = String($(this).val() || "").trim().toLowerCase();
        var visibleCount = 0;
        rowsWrapper.find(".manual-delivery-component-package-row").each(function() {
            var row = $(this);
            var haystack = String(row.attr("data-search") || "").toLowerCase();
            var visible = !query || haystack.indexOf(query) !== -1;
            row.toggle(visible);
            if (visible) visibleCount += 1;
        });
        helpWrapper.find(".manual-delivery-component-search-count").text(visibleCount ? "النتائج: " + visibleCount : "لا توجد نتائج مطابقة");
    });
}

function get_selected_delivery_component_packages(dialog) {
    var selected = [];
    var rowsField = dialog && dialog.fields_dict ? dialog.fields_dict.rows_html : null;
    if (!rowsField || !rowsField.$wrapper || !rowsField.$wrapper.length) {
        return selected;
    }

    rowsField.$wrapper.find(".manual-delivery-component-package-checkbox:checked").each(function() {
        var checkbox = $(this);
        selected.push({
            token: String(checkbox.val() || "").trim(),
            mode: "full"
        });
    });

    return selected.filter(function(entry) {
        return !!entry.token;
    });
}

function submit_manual_delivery_component_packages(frm, dialog, selected) {
    var total = selected.length;
    var updatedCount = 0;
    var failedCount = 0;
    var index = 0;

    function finish() {
        if (dialog) {
            dialog.hide();
        }
        frappe.show_alert({
            message: "تم تسجيل " + updatedCount + " من " + total + " حزمة يدويًا" + (failedCount ? "، وتعذر تسجيل " + failedCount + "." : "."),
            indicator: failedCount ? "orange" : "green"
        }, 7);
        frm.reload_doc();
    }

    function run_next() {
        if (index >= total) {
            finish();
            return;
        }

        var entry = selected[index];
        index += 1;
        var args = {
            mr: frm.doc.name,
            component_package: entry.token,
            mode: "full",
            source: "يدوي"
        };

        frappe.call({
            method: "mark_delivery_component_package_ready",
            args: args,
            freeze: true,
            freeze_message: "جاري تسجيل حزم مكونات التوريد يدويًا...",
            callback: function() {
                updatedCount += 1;
                run_next();
            },
            error: function() {
                failedCount += 1;
                run_next();
            }
        });
    }

    run_next();
}

function open_manual_delivery_component_package_dialog(frm) {
    if (frm.is_new()) {
        frappe.msgprint("احفظ طلب المواد قبل تسجيل حزم مكونات التوريد.");
        return;
    }
    if (form_has_unsaved_delivery_changes(frm)) {
        frappe.msgprint("احفظ طلب المواد أولًا حتى يتم التسجيل اليدوي على آخر نتائج تخصيم محفوظة.");
        return;
    }

    var pendingRows = get_pending_delivery_component_package_rows(frm);
    if (!pendingRows.length) {
        frappe.msgprint("لا توجد حزم غير جاهزة. إذا تغيرت نتائج التخصيم، حدّث حزم المكونات أولًا.");
        return;
    }

    var dialog = new frappe.ui.Dialog({
        title: "تسجيل حزم التوريد يدويًا",
        fields: [
            {
                fieldtype: "HTML",
                fieldname: "help_html"
            },
            {
                fieldtype: "HTML",
                fieldname: "rows_html",
                label: "الحزم غير الجاهزة"
            }
        ],
        primary_action_label: "تسجيل المحدد",
        primary_action: function() {
            var selected = get_selected_delivery_component_packages(dialog);
            if (!selected.length) {
                frappe.msgprint("حدد حزمة واحدة على الأقل.");
                return;
            }
            submit_manual_delivery_component_packages(frm, dialog, selected);
        }
    });

    dialog.show();
    if (dialog.fields_dict.help_html && dialog.fields_dict.help_html.$wrapper) {
        dialog.fields_dict.help_html.$wrapper.html(
            '<div style="margin-bottom:12px; color:var(--text-muted); line-height:1.8;">'
            + 'حدد الحزم الجاهزة للتوريد يدويًا. كل حزمة محددة ستسجل كاملة بدون أجزاء.'
            + '<input type="search" class="manual-delivery-component-search form-control" placeholder="بحث بالتكويد أو المكون أو التغليف أو الصنف..." style="margin-top:10px; height:34px;">'
            + '<div class="manual-delivery-component-search-count" style="margin-top:6px; font-size:12px;"></div>'
            + '<div style="margin-top:8px;">'
            + '<a href="#" class="manual-delivery-component-select-all">تحديد الكل</a>'
            + ' | '
            + '<a href="#" class="manual-delivery-component-clear-all">إلغاء التحديد</a>'
            + '</div>'
            + '</div>'
        );
        dialog.fields_dict.help_html.$wrapper.find(".manual-delivery-component-select-all").on("click", function(e) {
            e.preventDefault();
            if (dialog.fields_dict.rows_html && dialog.fields_dict.rows_html.$wrapper) {
                dialog.fields_dict.rows_html.$wrapper.find(".manual-delivery-component-package-row:visible .manual-delivery-component-package-checkbox").prop("checked", true);
            }
        });
        dialog.fields_dict.help_html.$wrapper.find(".manual-delivery-component-clear-all").on("click", function(e) {
            e.preventDefault();
            if (dialog.fields_dict.rows_html && dialog.fields_dict.rows_html.$wrapper) {
                dialog.fields_dict.rows_html.$wrapper.find(".manual-delivery-component-package-checkbox").prop("checked", false);
            }
        });
    }
    if (dialog.fields_dict.rows_html && dialog.fields_dict.rows_html.$wrapper) {
        dialog.fields_dict.rows_html.$wrapper.html(
            '<div style="max-height:460px; overflow:auto; border-top:1px solid var(--border-color); padding-top:8px;">'
            + render_manual_delivery_component_package_rows(pendingRows)
            + '</div>'
        );
        bind_manual_delivery_component_package_search(dialog);
        dialog.fields_dict.help_html.$wrapper.find(".manual-delivery-component-search").trigger("input");
    }
}

window.open_manual_delivery_component_package_dialog_for_current_form = function() {
    if (window.cur_frm) {
        open_manual_delivery_component_package_dialog(window.cur_frm);
    }
};

function apply_delivery_component_dashboard_search(wrapper) {
    if (!wrapper || !wrapper.length) return;
    var input = wrapper.find(".delivery-component-dashboard-search");
    var query = String(input.val() || "").trim().toLowerCase();
    var visibleCount = 0;

    wrapper.find(".delivery-component-dashboard-row").each(function() {
        var row = $(this);
        var haystack = String(row.attr("data-search") || "").toLowerCase();
        var visible = !query || haystack.indexOf(query) !== -1;
        row.toggle(visible);
        if (visible) visibleCount += 1;
    });

    wrapper.find(".delivery-component-dashboard-search-count").text(
        visibleCount ? "النتائج: " + visibleCount : "لا توجد نتائج مطابقة"
    );
}

function bind_delivery_component_dashboard_search(field) {
    if (!field || !field.$wrapper || !field.$wrapper.length) return;
    var wrapper = field.$wrapper;
    wrapper.off(".deliveryComponentDashboardSearch");
    wrapper.on(
        "input.deliveryComponentDashboardSearch keyup.deliveryComponentDashboardSearch change.deliveryComponentDashboardSearch search.deliveryComponentDashboardSearch",
        ".delivery-component-dashboard-search",
        function() {
            apply_delivery_component_dashboard_search(wrapper);
        }
    );
    apply_delivery_component_dashboard_search(wrapper);
}

function render_delivery_component_dashboard(frm) {
    var field = frm.fields_dict.custom_delivery_component_dashboard;
    if (!field) return;

    if (frm.is_new()) {
        frm.set_df_property("custom_delivery_component_dashboard", "options", "");
        frm.refresh_field("custom_delivery_component_dashboard");
        return;
    }

    var rows = get_delivery_component_package_rows(frm);
    var totalPackages = flt(frm.doc.custom_delivery_component_total_packages || 0) || rows.filter(function(row) {
        return parseInt(row.required_for_delivery || 0, 10);
    }).length;
    var remainingPackages = flt(frm.doc.custom_delivery_component_remaining_packages || 0);
    if (!remainingPackages && rows.length) {
        remainingPackages = rows.filter(function(row) {
            return parseInt(row.required_for_delivery || 0, 10) && flt(row.remaining_qty || 0) > 0;
        }).length;
    }
    var readyPackages = totalPackages - remainingPackages;
    if (readyPackages < 0) readyPackages = 0;
    var deliveryOnlyComponents = rows.filter(function(row) {
        return normalize_delivery_component_tracking_route(row.tracking_route) === "توريد فقط - بدون باركود";
    }).length;
    var withDoorComponents = rows.filter(function(row) {
        return normalize_delivery_component_tracking_route(row.tracking_route) === "مع الباب";
    }).length;
    var statusText = frm.doc.custom_delivery_component_status || (rows.length ? "غير جاهز" : "لا توجد مكونات");
    var totalRequiredQty = 0;
    var readyRequiredQty = 0;
    rows.forEach(function(row) {
        if (!parseInt(row.required_for_delivery || 0, 10)) return;
        totalRequiredQty += flt(row.package_qty || 0);
        readyRequiredQty += flt(row.ready_qty || 0);
    });
    var loadingCode = frm.doc.custom_delivery_loading_code || "-";
    var statusBg = statusText === "مكتمل" ? "rgba(34,197,94,0.14)" : statusText === "جزئي" ? "rgba(245,158,11,0.14)" : "rgba(148,163,184,0.16)";
    var statusColor = statusText === "مكتمل" ? "#16a34a" : statusText === "جزئي" ? "#b45309" : "#475569";
    var pendingPackageRows = get_pending_delivery_component_package_rows(frm);

    var previewRows = rows.map(function(row) {
        var route = normalize_delivery_component_tracking_route(row.tracking_route);
        var barcodeRoute = is_delivery_component_barcode_route(row);
        var remaining = flt(row.remaining_qty || 0);
        var displayStatus = barcodeRoute ? (remaining > 0 ? "غير مصنع" : "تم التصنيع") : route;
        var rowStatusBg = barcodeRoute && remaining > 0 ? "rgba(245,158,11,0.14)" : "rgba(34,197,94,0.14)";
        var rowStatusColor = barcodeRoute && remaining > 0 ? "#b45309" : "#16a34a";
        var loadingCode = barcodeRoute ? (row.loading_code || "") : "";
        var packageText = barcodeRoute ? (format_delivery_component_door_reference(row) || ((row.package_no || "-") + "/" + (row.package_count || "-"))) : "";
        var searchText = [
            loadingCode,
            row.component_label || row.component || "",
            row.package_label || "",
            row.package_no || "",
            row.package_count || "",
            packageText,
            row.item_code || "",
            row.item_name || "",
            format_manufacturing_count(row.package_qty || 0),
            displayStatus
        ].join(" ").toLowerCase();
        return ''
            + '<tr class="delivery-component-dashboard-row" data-search="' + escape_html_text(searchText) + '">'
            + '<td style="padding:9px 8px; border-bottom:1px solid var(--border-color); text-align:center; direction:ltr; vertical-align:middle; font-weight:900; white-space:nowrap;">' + escape_html_text(loadingCode || "-") + '</td>'
            + '<td style="padding:9px 8px; border-bottom:1px solid var(--border-color); text-align:center; direction:ltr; vertical-align:middle; font-weight:800; white-space:nowrap;">' + escape_html_text(packageText) + '</td>'
            + '<td style="padding:9px 8px; border-bottom:1px solid var(--border-color); text-align:right; direction:rtl; vertical-align:middle; font-weight:800; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;" title="' + escape_html_text(row.item_code || row.item_name || "") + '">' + escape_html_text(row.component_label || row.component || "-") + '</td>'
            + '<td style="padding:9px 8px; border-bottom:1px solid var(--border-color); text-align:center; direction:rtl; vertical-align:middle; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">' + escape_html_text(row.package_label || "حزمة") + '</td>'
            + '<td style="padding:9px 8px; border-bottom:1px solid var(--border-color); text-align:center; direction:ltr; vertical-align:middle; white-space:nowrap; font-weight:800;">' + format_manufacturing_count(row.package_qty || 0) + '</td>'
            + '<td style="padding:9px 8px; border-bottom:1px solid var(--border-color); text-align:center; direction:rtl; vertical-align:middle;"><span style="display:inline-flex; justify-content:center; min-width:72px; padding:3px 9px; border-radius:999px; background:' + rowStatusBg + '; color:' + rowStatusColor + '; font-size:12px; font-weight:800; white-space:nowrap;">' + escape_html_text(displayStatus) + '</span></td>'
            + '</tr>';
    }).join("");

    var tableHtml = rows.length ? ''
        + '<div style="margin-top:14px;">'
        + '<div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin-bottom:8px;">'
        + '<input type="search" class="delivery-component-dashboard-search form-control" placeholder="بحث بالتكويد أو المكون أو التغليف أو الصنف..." style="max-width:360px; height:34px; direction:rtl; text-align:right;">'
        + '<span class="delivery-component-dashboard-search-count" style="color:var(--text-muted); font-size:12px;"></span>'
        + '</div>'
        + '<div style="max-height:420px; overflow:auto; border:1px solid var(--border-color); border-radius:10px;">'
        + '<table style="width:100%; min-width:920px; border-collapse:collapse; table-layout:fixed; direction:ltr;">'
        + '<colgroup>'
        + '<col style="width:104px;">'
        + '<col style="width:86px;">'
        + '<col style="width:32%;">'
        + '<col style="width:120px;">'
        + '<col style="width:104px;">'
        + '<col style="width:128px;">'
        + '</colgroup>'
        + '<thead style="position:sticky; top:0; background:var(--control-bg); z-index:1;">'
        + '<tr>'
        + '<th style="padding:9px 8px; text-align:center; direction:rtl; color:var(--text-muted); font-size:12px; white-space:nowrap;">تكويد التحميل</th>'
        + '<th style="padding:9px 8px; text-align:center; direction:rtl; color:var(--text-muted); font-size:12px; white-space:nowrap;">الحزمة</th>'
        + '<th style="padding:9px 8px; text-align:right; direction:rtl; color:var(--text-muted); font-size:12px; white-space:nowrap;">المكون</th>'
        + '<th style="padding:9px 8px; text-align:center; direction:rtl; color:var(--text-muted); font-size:12px; white-space:nowrap;">التغليف</th>'
        + '<th style="padding:9px 8px; text-align:center; direction:rtl; color:var(--text-muted); font-size:12px; white-space:nowrap;">عدد القطع</th>'
        + '<th style="padding:9px 8px; text-align:center; direction:rtl; color:var(--text-muted); font-size:12px; white-space:nowrap;">الحالة</th>'
        + '</tr>'
        + '</thead><tbody>' + previewRows + '</tbody></table></div></div>' : '<div style="margin-top:12px; color:var(--text-muted);">لا توجد حزم قطاعات بعد. اضغط طباعة باركود القطاعات لتجهيزها وفتح الملصقات تلقائيًا.</div>';

    var html = ''
        + '<div class="delivery-component-wrap" style="padding:16px; border:1px solid var(--border-color); border-radius:12px; background:var(--card-bg); margin-bottom:12px;">'
        + '<div style="display:flex; justify-content:space-between; align-items:flex-start; gap:12px; flex-wrap:wrap;">'
        + '<div>'
        + '<div style="font-size:18px; font-weight:900; margin-bottom:4px;">تسجيل حزم المكونات</div>'
        + '<div style="color:var(--text-muted);">بعد إغلاق الكرتون، امسح الملصق مرة واحدة لتسجيل الحزمة كاملة.</div>'
        + '</div>'
        + '<span style="display:inline-flex; padding:5px 11px; border-radius:999px; background:' + statusBg + '; color:' + statusColor + '; font-size:12px; font-weight:900;">' + escape_html_text(statusText) + '</span>'
        + '</div>'
        + '<div style="display:flex; gap:10px; flex-wrap:wrap; margin-top:14px;">'
        + render_manufacturing_stat_card("حزم القطاعات المصنعة", format_manufacturing_count(readyPackages) + " / " + format_manufacturing_count(totalPackages), remainingPackages > 0 ? "amber" : "green")
        + (deliveryOnlyComponents ? render_manufacturing_stat_card("مكونات توريد فقط", format_manufacturing_count(deliveryOnlyComponents), "default") : "")
        + (withDoorComponents ? render_manufacturing_stat_card("مكونات مع الباب", format_manufacturing_count(withDoorComponents), "default") : "")
        + (rows.length ? render_manufacturing_stat_card("الكمية الجاهزة", format_manufacturing_count(readyRequiredQty) + " / " + format_manufacturing_count(totalRequiredQty), statusText === "مكتمل" ? "green" : "amber") : "")
        + render_manufacturing_stat_card("رمز التحميل", escape_html_text(loadingCode), "default")
        + '</div>'
        + tableHtml
        + '</div>';

    frm.set_df_property("custom_delivery_component_dashboard", "options", html);
    frm.refresh_field("custom_delivery_component_dashboard");
    if (field.$wrapper) {
        field.$wrapper.find(".delivery-component-print-btn").on("click", function() {
            prepare_and_print_delivery_component_packages(frm);
        });
        field.$wrapper.find(".manual-delivery-component-package-btn").on("click", function() {
            open_manual_delivery_component_package_dialog(frm);
        });
        bind_delivery_component_dashboard_search(field);
    }
}

function add_manufacturing_create_button(frm) {
    if (!frm) {
        return;
    }
    var createGroup = __("Create");
    frm.remove_custom_button("تصنيع واعتماد المخزون", createGroup);
    frm.remove_custom_button("إنشاء قيد تصنيع مخزني", createGroup);
    if (frm.is_new() || frm.doc.docstatus !== 1) {
        return;
    }
    if (!(frm.doc.items || []).some(function(row) { return !!row.item_code; })) {
        return;
    }
    frm.add_custom_button("إنشاء قيد تصنيع مخزني", function() {
        make_manufacturing_stock_entries_from_material_request(frm);
    }, createGroup);
}

frappe.ui.form.on("Material Request", {
    before_save: function(frm) {
        normalize_component_exclusion_group_values(frm);
    },
    refresh: function(frm) {
        // Move Required By to first position in grid
        var grid = frm.fields_dict.items.grid;
        var fields = grid.grid_rows.length ? grid.grid_rows[0].docfields : [];
        var sd_idx = fields.findIndex(function(f) { return f.fieldname === 'schedule_date'; });
        if (sd_idx > 0) {
            var sd = fields.splice(sd_idx, 1)[0];
            fields.unshift(sd);
        }
        grid.refresh();
        add_manufacturing_create_button(frm);
        render_manual_manufacturing_button(frm);
        render_mr_manufacture_stock_entries(frm);
        if (
            window.namar_delivery_tracking
            && window.namar_delivery_tracking.delivery_components
            && window.namar_delivery_tracking.delivery_components.interface_version >= 3
            && typeof window.namar_delivery_tracking.delivery_components.render_material_request === "function"
        ) {
            window.namar_delivery_tracking.delivery_components.render_material_request(frm);
        } else {
            render_delivery_component_dashboard(frm);
        }
        _load_option_labels(function() {
            load_component_exclusion_group_options(function() {
                schedule_render_cutting(frm, 0);
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

function get_stock_entry_manufacturing_rows(frm) {
    return sort_manufacturing_rows((frm.doc.items || []).filter(function(row) {
        return row.item_code && is_trackable_manufacturing_row(row);
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

function format_delivery_component_door_reference(row) {
    var doorNo = Number((row && row.custom_door_no) || 0);
    if (!doorNo) return "";
    var pieceNo = Number(row.custom_piece_no || 1);
    var pieceCount = Number(row.custom_piece_count || 1);
    return "باب " + doorNo + " | " + pieceNo + "/" + pieceCount;
}

function get_manufacturing_row_qty(row) {
    if (parseInt(row.custom_is_cancelled_row, 10) || 0) return 0;
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
    if (parseInt(row.custom_is_cancelled_row, 10) || 0) return false;
    if (is_component_manufacturing_excluded(row)) {
        return false;
    }
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
        +   '<div style="padding:18px 14px; font-size:36px; font-weight:900; color:' + palette.value + '; direction:ltr; text-align:left; unicode-bidi:isolate;">' + value + '</div>'
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
                method: "mark_manufactured_rows_v3",
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

function make_manufacturing_stock_entries_from_material_request(frm) {
    if (!frm || frm.is_new() || frm.doc.docstatus !== 1 || _manufacture_stock_entry_running) {
        return;
    }

    _manufacture_stock_entry_running = true;
    frappe.call({
        method: "make_manufacture_stock_entries_from_mr",
        args: {
            mr: frm.doc.name
        },
        freeze: true,
        freeze_message: "جاري إنشاء واعتماد قيد التصنيع...",
        callback: function(r) {
            var msg = r.message || {};
            var entries = msg.stock_entries || [];
            var linkedEntries = msg.linked_stock_entries || [];
            var entryLinks = entries.map(function(entry) {
                return frappe.utils.get_form_link("Stock Entry", entry.name, true);
            });

            frm.reload_doc().then(function() {
                render_manual_manufacturing_button(frm);
                render_mr_manufacture_stock_entries(frm);
                if (msg.status === "blocked") {
                    frappe.msgprint({
                        title: "تعذر إنشاء قيد التصنيع",
                        indicator: "orange",
                        wide: true,
                        message: build_manufacturing_stock_entry_response_html(msg)
                    });
                    return;
                }
                if (msg.status === "already_done") {
                    frappe.msgprint({
                        title: "قيد التصنيع موجود",
                        indicator: "blue",
                        message: build_existing_stock_entry_links_html(linkedEntries, msg.message || "تم إنشاء قيد التصنيع سابقًا.")
                    });
                    return;
                }
                if (entries.length) {
                    frappe.msgprint({
                        title: "تم اعتماد قيد التصنيع",
                        indicator: "green",
                        message: "تم إنشاء واعتماد " + entries.length + " قيد تصنيع:<br>" + entryLinks.join("<br>")
                    });
                    return;
                }
                frappe.show_alert({
                    message: msg.message || "لا توجد سطور قابلة لإنشاء قيد تصنيع مخزني جديد.",
                    indicator: "orange"
                }, 7);
            });
        },
        always: function() {
            _manufacture_stock_entry_running = false;
        }
    });
}

window.make_manufacturing_stock_entries_from_current_form = function() {
    if (window.cur_frm) {
        make_manufacturing_stock_entries_from_material_request(window.cur_frm);
    }
};

function build_existing_stock_entry_links_html(entries, fallbackMessage) {
    if (!entries || !entries.length) {
        return escape_html_text(fallbackMessage || "لا توجد قيود مخزنية مرتبطة.");
    }
    var links = entries.map(function(entry) {
        return frappe.utils.get_form_link("Stock Entry", entry.name, true);
    });
    return escape_html_text(fallbackMessage || "قيود مخزنية مرتبطة بهذا الطلب:") + "<br>" + links.join("<br>");
}

function build_manufacturing_stock_entry_response_html(msg) {
    var blockedRows = msg.blocked_rows || [];
    if (!blockedRows.length) {
        return '<div dir="rtl" style="text-align:right;">' + escape_html_text(msg.message || "تعذر إنشاء قيد التصنيع.") + '</div>';
    }

    var stats = { stock: 0, config: 0, valuation: 0, data: 0 };
    blockedRows.forEach(function(row) {
        stats[classify_manufacturing_blocked_row(row).key] += 1;
    });

    var shownRows = blockedRows.slice(0, 12);
    var html = '<div dir="rtl" style="text-align:right; font-family:inherit;">'
        + '<div style="display:grid; gap:12px;">'
        + '<div style="padding:12px 14px; border:1px solid rgba(249,115,22,.28); border-radius:10px; background:rgba(249,115,22,.10);">'
        + '<div style="font-weight:800; color:#fb923c; margin-bottom:4px;">لا يمكن إنشاء قيد التصنيع الآن</div>'
        + '<div style="color:var(--text-muted); line-height:1.7;">تم فحص المكونات، ووجدنا مشاكل يجب معالجتها قبل اعتماد القيد. التفاصيل مرتبة حسب نوع المشكلة أدناه.</div>'
        + '</div>'
        + '<div style="display:flex; flex-wrap:wrap; gap:8px;">'
        + render_manufacturing_issue_count("نقص رصيد", stats.stock, "#f59e0b")
        + render_manufacturing_issue_count("إعداد مفقود", stats.config, "#ef4444")
        + render_manufacturing_issue_count("تقييم صفر", stats.valuation, "#8b5cf6")
        + render_manufacturing_issue_count("بيانات أخرى", stats.data, "#64748b")
        + '</div>';

    shownRows.forEach(function(row) {
        html += render_manufacturing_issue_card(row);
    });

    if (blockedRows.length > shownRows.length) {
        html += '<div style="padding:10px 12px; border-radius:8px; background:rgba(100,116,139,.10); color:var(--text-muted);">'
            + 'يوجد ' + escape_html_text(blockedRows.length - shownRows.length) + ' ملاحظة إضافية غير معروضة هنا لتقليل الازدحام.'
            + '</div>';
    }

    html += '</div></div>';
    return html;
}

function render_manufacturing_issue_count(label, count, color) {
    if (!count) {
        return "";
    }
    return '<div style="display:inline-flex; align-items:center; gap:6px; padding:7px 10px; border-radius:999px; background:' + color + '22; color:' + color + '; font-weight:800;">'
        + '<span>' + escape_html_text(label) + '</span>'
        + '<strong style="direction:ltr;">' + escape_html_text(count) + '</strong>'
        + '</div>';
}

function classify_manufacturing_blocked_row(row) {
    var reason = String((row && row.reason) || "");
    if (reason.indexOf("تقييم") !== -1 || reason.indexOf("صفر") !== -1) {
        return {
            key: "valuation",
            label: "تقييم صفر",
            color: "#8b5cf6",
            action: "صحح تقييم الصنف في المستودع أو أنشئ طبقة تكلفة صحيحة قبل إعادة المحاولة."
        };
    }
    if (reason.indexOf("رصيد") !== -1 || reason.indexOf("المتاح") !== -1 || reason.indexOf("المطلوب") !== -1) {
        return {
            key: "stock",
            label: "نقص رصيد",
            color: "#f59e0b",
            action: "وفّر الرصيد في مستودع مصدر التصنيع ثم أعد المحاولة."
        };
    }
    if (reason.indexOf("صنف خام") !== -1 || reason.indexOf("إعداد") !== -1 || reason.indexOf("بديل") !== -1) {
        return {
            key: "config",
            label: "إعداد مفقود",
            color: "#ef4444",
            action: "افتح إعداد المكون واربط الصنف الخام أو نمط الصنف الخام الملون والصنف الافتراضي بدون لون."
        };
    }
    return {
        key: "data",
        label: "بيانات ناقصة",
        color: "#64748b",
        action: "راجع بيانات التخصيم وإعداد المكون لهذا السطر."
    };
}

function render_manufacturing_issue_card(row) {
    var issue = classify_manufacturing_blocked_row(row || {});
    var lineNo = escape_html_text(row.idx || "-");
    var component = escape_html_text(row.component || row.item_code || "-");
    var reason = escape_html_text(row.reason || "بيانات ناقصة");
    var itemCode = row.item_code ? '<span style="color:var(--text-muted);">الصنف المنتج: ' + escape_html_text(row.item_code) + '</span>' : "";

    return '<div style="border:1px solid ' + issue.color + '44; border-right:4px solid ' + issue.color + '; border-radius:10px; padding:12px; background:' + issue.color + '12;">'
        + '<div style="display:flex; align-items:flex-start; justify-content:space-between; gap:10px; flex-wrap:wrap;">'
        + '<div style="display:grid; gap:4px;">'
        + '<div style="font-weight:850; color:var(--text-color);">السطر ' + lineNo + ' · ' + component + '</div>'
        + itemCode
        + '</div>'
        + '<span style="white-space:nowrap; padding:4px 9px; border-radius:999px; background:' + issue.color + '22; color:' + issue.color + '; font-weight:800;">' + issue.label + '</span>'
        + '</div>'
        + '<div style="margin-top:10px; line-height:1.75; color:var(--text-color);">' + reason + '</div>'
        + '<div style="margin-top:10px; padding:8px 10px; border-radius:8px; background:rgba(255,255,255,.06); color:var(--text-muted); line-height:1.7;">'
        + '<strong style="color:' + issue.color + ';">المطلوب الآن: </strong>' + escape_html_text(issue.action)
        + '</div>'
        + '</div>';
}

function render_mr_manufacture_stock_entries(frm) {
    var field = frm.fields_dict.custom_stock_entry_summary;
    if (!field || !field.$wrapper) {
        return;
    }
    if (!frm.doc || frm.is_new()) {
        field.$wrapper.html('<div style="padding:12px; color:var(--text-muted);">احفظ طلب المواد أولًا لعرض القيود المخزنية.</div>');
        return;
    }
    field.$wrapper.html('<div style="padding:12px; color:var(--text-muted);">جاري تحميل القيود المخزنية...</div>');
    frappe.call({
        method: "get_mr_manufacture_stock_entries",
        args: {
            mr_name: frm.doc.name
        },
        freeze: false,
        callback: function(r) {
            render_mr_manufacture_stock_entries_html(field.$wrapper, (r && r.message) || {});
        },
        error: function() {
            field.$wrapper.html('<div style="padding:12px; color:var(--red-500);">تعذر تحميل القيود المخزنية المرتبطة.</div>');
        }
    });
}

function mr_stock_entry_warehouses_html(items) {
    var sections = [];
    [["s_warehouse", "من مستودع"], ["t_warehouse", "إلى مستودع"]].forEach(function(definition) {
        var warehouses = [];
        (items || []).forEach(function(item) {
            var warehouse = item[definition[0]];
            if (warehouse && warehouses.indexOf(warehouse) === -1) warehouses.push(warehouse);
        });
        if (warehouses.length) {
            sections.push('<div style="margin-top:4px;"><strong>' + definition[1] + ':</strong>'
                + warehouses.map(function(warehouse) {
                    return '<div>' + frappe.utils.escape_html(warehouse) + '</div>';
                }).join('') + '</div>');
        }
    });
    return sections.length
        ? '<div class="mr-stock-warehouses" dir="rtl" style="text-align:right; font-size:11px; font-weight:normal; line-height:1.6; overflow-wrap:anywhere; margin-top:6px;">' + sections.join('') + '</div>'
        : '';
}

function render_mr_manufacture_stock_entries_html(wrapper, data) {
    var entries = data.entries || [];
    var items = data.items || [];
    if (!entries.length) {
        wrapper.html(
            '<div dir="rtl" style="border:1px solid var(--border-color); border-radius:8px; padding:12px; color:var(--text-muted); background:var(--card-bg); text-align:center;">'
            + 'لا توجد قيود مخزنية مرتبطة بهذا الطلب.'
            + '</div>'
        );
        return;
    }

    var cardsHtml = entries.map(function(entry) {
        var entryItems = items.filter(function(item) {
            return item.parent === entry.name;
        });
        var itemHtml = entryItems.length ? entryItems.map(function(item) {
            var sign = item.s_warehouse && item.t_warehouse ? "↔" : (item.s_warehouse ? "-" : "+");
            var color = item.s_warehouse && item.t_warehouse ? "var(--blue-500)" : (item.s_warehouse ? "var(--red-500)" : "var(--green-500)");
            var warehouse = item.s_warehouse || item.t_warehouse || "";
            var warehouseLabel = item.s_warehouse && item.t_warehouse
                ? 'من ' + escape_html_text(item.s_warehouse) + ' إلى ' + escape_html_text(item.t_warehouse)
                : escape_html_text(warehouse);
            return '<div style="display:grid; gap:4px; padding:8px 0; border-top:1px dashed var(--border-color);">'
                + '<div style="display:flex; align-items:center; justify-content:space-between; gap:10px; flex-wrap:wrap;">'
                + '<span dir="ltr" style="font-weight:850; text-align:left; color:var(--text-color); overflow-wrap:anywhere;">' + escape_html_text(item.item_code || "-") + '</span>'
                + '<span dir="ltr" style="color:' + color + '; white-space:nowrap; font-weight:850;">' + sign + ' ' + format_manufacturing_count(item.qty || 0) + ' ' + escape_html_text(item.uom || "") + '</span>'
                + '</div>'
                + '<div style="color:var(--text-muted); line-height:1.5; font-size:12px;">' + warehouseLabel + '</div>'
                + '</div>';
        }).join("") : '<div style="color:var(--text-muted); padding-top:8px;">لا توجد سطور أصناف.</div>';
        var statusText = parseInt(entry.docstatus, 10) === 1 ? "معتمد" : "مسودة";
        var statusColor = parseInt(entry.docstatus, 10) === 1 ? "blue" : "orange";
        return '<div style="border:1px solid var(--border-color); border-radius:8px; background:var(--card-bg); overflow:hidden;">'
            + '<div style="display:flex; align-items:flex-start; justify-content:space-between; gap:10px; flex-wrap:wrap; padding:10px 12px; background:var(--control-bg); border-bottom:1px solid var(--border-color);">'
            + '<div style="display:grid; gap:4px; min-width:0;">'
            + '<div style="font-weight:900;">' + frappe.utils.get_form_link("Stock Entry", entry.name, true) + '</div>'
            + mr_stock_entry_warehouses_html(entryItems)
            + '<div style="color:var(--text-muted); line-height:1.5;">' + escape_html_text(entry.stock_entry_type || entry.purpose || "-") + '</div>'
            + '</div>'
            + '<span class="indicator-pill ' + statusColor + ' no-indicator-dot" style="white-space:nowrap;">' + statusText + '</span>'
            + '</div>'
            + '<div style="padding:0 12px 4px;">' + itemHtml + '</div>'
            + '</div>';
    }).join("");

    wrapper.html(
        '<div dir="rtl" style="display:grid; gap:8px; margin-top:8px;">'
        + '<div style="padding:10px 12px; font-weight:900; border:1px solid var(--border-color); border-radius:8px; background:var(--control-bg);">قيود مخزنية مرتبطة بطلب المواد</div>'
        + cardsHtml
        + '</div>'
    );
}

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
    var statusText = frm.doc.custom_manufacturing_status || "غير مصنع";
    var totalRows = sum_manufacturing_qty(trackableRows, "total");
    var completedTotal = sum_manufacturing_qty(trackableRows, "done");
    var remainingValue = totalRows - completedTotal;
    if (remainingValue < 0) remainingValue = 0;
    var remainingText = format_manufacturing_count(remainingValue);
    var completedText = format_manufacturing_count(completedTotal);
    var totalText = format_manufacturing_count(totalRows);
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
        '<div style="display:flex; gap:8px; flex-wrap:wrap; justify-content:flex-end;">',
        '<a href="/factory?mr=' + encodeURIComponent(normalizedMr) + '" target="_blank" class="btn btn-default btn-sm">فتح شاشة التصنيع</a>',
        '<a href="' + get_manufacturing_print_url(frm) + '" target="_blank" class="btn btn-default btn-sm">طباعة باركود التصنيع</a>',
        '<button type="button" class="btn btn-default btn-sm" onclick="window.prepare_delivery_component_packages_for_print_for_current_form && window.prepare_delivery_component_packages_for_print_for_current_form()">طباعة باركود القطاعات</button>',
        '<a href="' + get_kdt_print_url(frm) + '" target="_blank" class="btn btn-default btn-sm">طباعة باركود KDT</a>',
        '</div>',
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

    bodyHtml.push('<div style="display:flex; gap:16px; flex-wrap:wrap; margin-top:12px;">');
    bodyHtml.push(render_manufacturing_table_html("المتبقي للتصنيع", pendingRows, "لا توجد سطور غير مصنعة.", "orange", false));
    bodyHtml.push(render_manufacturing_table_html("تم تصنيعه", completedRows, "لا توجد سطور مصنعة بعد.", "green", true));
    bodyHtml.push('</div></div>');
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
            set_component_manufacturing_policy_value(cdt, cdn, COMPONENT_MANUFACTURING_POLICY_DEFAULT);
            frappe.model.set_value(cdt, cdn, "custom_show_glass_options", 0);
            frappe.model.set_value(cdt, cdn, "custom_glass_type", "");
            frappe.model.set_value(cdt, cdn, "custom_glass_model", "");
            frappe.model.set_value(cdt, cdn, "custom_component_exclusion_groups", "");
            frappe.model.set_value(cdt, cdn, "custom_frame_type", "");
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
    custom_exception_component_1: function(frm, cdt, cdn) {
        sync_component_exclusion_groups_from_checkboxes(frm, cdt, cdn);
        request_cutting_render(frm);
    },
    custom_exception_component_2: function(frm, cdt, cdn) {
        sync_component_exclusion_groups_from_checkboxes(frm, cdt, cdn);
        request_cutting_render(frm);
    },
    custom_no_qitaat: function(frm, cdt, cdn) {
        sync_component_exclusion_groups_from_checkboxes(frm, cdt, cdn);
        request_cutting_render(frm);
    },
    custom_without_frame_components: function(frm, cdt, cdn) {
        sync_component_exclusion_groups_from_checkboxes(frm, cdt, cdn);
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
    custom_component_exclusion_groups: function(frm, cdt, cdn) {
        var row = locals[cdt] && locals[cdt][cdn];
        if (row) {
            var current = row.custom_component_exclusion_groups || "";
            var normalized = component_exclusion_groups_from_row(row);
            if (String(current || "") !== normalized) {
                frappe.model.set_value(cdt, cdn, "custom_component_exclusion_groups", normalized);
            }
        }
        request_cutting_render(frm);
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
        apply_component_exclusion_group_visibility(frm, cdn);
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
    var grid_field = grid.get_field && grid.get_field("custom_component_exclusion_groups");
    if (grid_field) {
        var group_df = grid_field.df || grid_field;
        group_df.fieldtype = "Small Text";
        group_df.hidden = 1;
    }
    COMPONENT_EXCLUSION_CHECKBOX_GROUPS.forEach(function(option) {
        var checkbox_field = grid.get_field && grid.get_field(option.fieldname);
        if (checkbox_field) {
            var checkbox_df = checkbox_field.df || checkbox_field;
            checkbox_df.hidden = 0;
            checkbox_df.fieldtype = "Check";
        }
    });
    (grid.grid_rows || []).forEach(function(grid_row) {
        var control = grid_row && grid_row.grid_form && grid_row.grid_form.fields_dict
            ? grid_row.grid_form.fields_dict.custom_component_exclusion_groups
            : null;
        if (control && control.df) {
            control.df.fieldtype = "Small Text";
            control.df.hidden = 1;
            try {
                if (typeof control.refresh === "function") {
                    control.refresh();
                }
            } catch (e) {
                console.warn("تعذر تحديث حقل استثناء المكونات", e);
            }
        }
        COMPONENT_EXCLUSION_CHECKBOX_GROUPS.forEach(function(option) {
            var checkbox_control = grid_row && grid_row.grid_form && grid_row.grid_form.fields_dict
                ? grid_row.grid_form.fields_dict[option.fieldname]
                : null;
            if (!checkbox_control || !checkbox_control.df) return;
            checkbox_control.df.hidden = 0;
            checkbox_control.df.fieldtype = "Check";
            if (typeof checkbox_control.refresh === "function") {
                try {
                    checkbox_control.refresh();
                } catch (e) {
                    console.warn("تعذر تحديث checkbox استثناء المكونات", e);
                }
            }
        });
    });
}

function apply_component_exclusion_group_visibility(frm, cdn) {
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
    var control = grid_row.grid_form.fields_dict.custom_component_exclusion_groups;
    if (control) {
        control.df.fieldtype = "Small Text";
        control.df.hidden = 1;
        try {
            if (typeof control.refresh === "function") {
                control.refresh();
            }
        } catch (e) {
            console.warn("تعذر تحديث ظهور حقل استثناء المكونات", e);
        }
    }
    COMPONENT_EXCLUSION_CHECKBOX_GROUPS.forEach(function(option) {
        var checkbox_control = grid_row.grid_form.fields_dict[option.fieldname];
        if (!checkbox_control) return;
        checkbox_control.df.hidden = 0;
        checkbox_control.df.fieldtype = "Check";
        if (typeof checkbox_control.refresh === "function") {
            try {
                checkbox_control.refresh();
            } catch (e) {
                console.warn("تعذر تحديث ظهور checkbox استثناء المكونات", e);
            }
        }
        if (checkbox_control.wrapper) {
            $(checkbox_control.wrapper).show();
            $(checkbox_control.wrapper).closest(".frappe-control").show();
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
                var exception_component_1 = component_exception_checked(row, COMPONENT_EXCLUSION_CHECKBOX_GROUPS[0]);
                var exception_component_2 = component_exception_checked(row, COMPONENT_EXCLUSION_CHECKBOX_GROUPS[1]);
                var requested_component_exclusion_groups = component_exclusion_groups_from_row(row);
                items_to_check.push({
                    idx: row.idx,
                    custom_is_cancelled_row: parseInt(row.custom_is_cancelled_row, 10) || 0,
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
                    component_exclusion_groups: requested_component_exclusion_groups,
                    frame_component: String(row.custom_frame_type || "").trim(),
                    taksiya_1: row.custom_taksiya_1 || 0,
                    taksiya_2: row.custom_taksiya_2 || 0,
                    no_qitaat: exception_component_1,
                    exception_component_1: exception_component_1,
                    exception_component_2: exception_component_2,
                    without_kalon: row.custom_without_kalon || 0,
                    item_qty: (parseInt(row.custom_is_cancelled_row, 10) || 0) ? 0 : (flt(row.qty) || 1),
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
            if (item.custom_is_cancelled_row) return;
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
            component_exclusion_groups_arr.push(normalize_component_exclusion_groups(item.component_exclusion_groups || ""));
            frame_components_arr.push(item.frame_component || "");
        });

        frappe.call({
            method: "demo.api.calculate_cutting",
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
        _item_frame_component_support_map = {};
        var all_comps = [];
        var comp_labels = {};
        items_to_check.forEach(function(item) {
            var current_row = locals[item.cdt] && locals[item.cdt][item.cdn];
            item.custom_is_cancelled_row = current_row ? (parseInt(current_row.custom_is_cancelled_row, 10) || 0) : item.custom_is_cancelled_row;
            if (item.custom_is_cancelled_row) {
                clear_cutting_result_values(item);
                item.w = item.h = item.ww = item.item_qty = 0;
                item.lw = item.lh = item.uw = item.uh = item.pw = item.ph = 0;
                item.show_leaf = item.show_u = item.show_panel = false;
                item.item_type = "";
                return;
            }
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
                item.component_manufacturing_policy = normalize_component_manufacturing_policy(t.component_manufacturing_policy);
                item.show_square_count = parseInt(t.show_square_count, 10) || 0;
                item.show_glass_options = parseInt(t.show_glass_options, 10) || 0;
                _item_square_count_support_map[item.cdn] = !!item.show_square_count;
                _item_glass_options_support_map[item.cdn] = !!item.show_glass_options;
                var live_row = locals[item.cdt] && locals[item.cdt][item.cdn];
                if (live_row) {
                    live_row.custom_show_glass_options = item.show_glass_options ? 1 : 0;
                }
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
                    set_component_manufacturing_policy_value(item.cdt, item.cdn, item.component_manufacturing_policy);
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
                    frappe.model.set_value(item.cdt, item.cdn, "custom_store_data", item.stores.length ? JSON.stringify(item.stores) : "");
                }
            } else if (frm.doc.docstatus <= 1) {
                _item_split_support_map[item.cdn] = false;
                _item_square_count_support_map[item.cdn] = false;
                _item_glass_options_support_map[item.cdn] = false;
                _item_frame_component_support_map[item.cdn] = false;
                item.show_square_count = 0;
                item.square_count = "";
                item.show_glass_options = 0;
                item.glass_type = "";
                item.glass_model = "";
                var empty_live_row = locals[item.cdt] && locals[item.cdt][item.cdn];
                if (empty_live_row) {
                    empty_live_row.custom_show_glass_options = 0;
                }
                frappe.model.set_value(item.cdt, item.cdn, "custom_cutting_template", "");
                set_component_manufacturing_policy_value(item.cdt, item.cdn, COMPONENT_MANUFACTURING_POLICY_DEFAULT);
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
        var has_exception_component_1 = items_to_check.some(function(r) { return r.exception_component_1; });
        var has_exception_component_2 = items_to_check.some(function(r) { return r.exception_component_2; });
        var has_without_kalon = items_to_check.some(function(r) { return r.without_kalon; });
        var has_square_count = items_to_check.some(function(r) { return r.show_square_count; });
        var has_glass_options = items_to_check.some(function(r) { return r.show_glass_options; });
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
            + '.cutting-results-wrap tr.cancelled-cutting-row td{background:#eee!important;color:#666;}'
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
            + (has_exception_component_1 ? '<th rowspan="2" style="vertical-align:middle;font-size:9px">استثناء 1</th>' : '')
            + (has_exception_component_2 ? '<th rowspan="2" style="vertical-align:middle;font-size:9px">استثناء 2</th>' : '')
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
            html += (r.custom_is_cancelled_row ? '<tr class="cancelled-cutting-row">' : '<tr>')
                + '<td class="cutting-sticky-idx">' + r.idx + '</td>'
                + '<td class="cutting-sticky-item" style="text-align:right">' + r.item_name + (r.custom_is_cancelled_row ? '<br><strong>ملغى — لحفظ التسلسل فقط</strong>' : '') + '</td>'

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
                + (has_exception_component_1 ? '<td>' + (r.exception_component_1 ? '\u2713' : '-') + '</td>' : '')
                + (has_exception_component_2 ? '<td>' + (r.exception_component_2 ? '\u2713' : '-') + '</td>' : '')
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
                + (has_exception_component_1 ? '<td style="' + totalsEmptyCellStyle + '"></td>' : '')
                + (has_exception_component_2 ? '<td style="' + totalsEmptyCellStyle + '"></td>' : '')
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
            has_frame_component ? 1 : 0
        ].join("|");
        if (frm.__cutting_grid_state_signature !== gridStateSignature) {
            update_items_grid_docfield(frm, "custom_result_sliding_type", "hidden", _any_has_sliding ? 0 : 1);
            update_items_grid_docfield(frm, "custom_result_sliding_type", "label", "نوع السحاب");
            update_items_grid_docfield(frm, "custom_show_glass_options", "hidden", 1);
            update_items_grid_docfield(frm, "custom_glass_type", "hidden", has_glass_options ? 0 : 1);
            update_items_grid_docfield(frm, "custom_glass_model", "hidden", has_glass_options ? 0 : 1);
            update_items_grid_docfield(frm, "custom_exception_component_1", "hidden", 0);
            update_items_grid_docfield(frm, "custom_exception_component_1", "fieldtype", "Check");
            update_items_grid_docfield(frm, "custom_exception_component_2", "hidden", 0);
            update_items_grid_docfield(frm, "custom_exception_component_2", "fieldtype", "Check");
            update_items_grid_docfield(frm, "custom_no_qitaat", "hidden", 1);
            update_items_grid_docfield(frm, "custom_without_frame_components", "hidden", 1);
            update_items_grid_docfield(frm, "custom_component_exclusion_groups", "hidden", 1);
            update_items_grid_docfield(frm, "custom_component_exclusion_groups", "fieldtype", "Small Text");
            update_items_grid_docfield(frm, "custom_frame_type", "hidden", has_frame_component ? 0 : 1);
            toggle_items_grid_column(frm, "custom_leaf_count", false);
            toggle_items_grid_column(frm, "custom_split_type", false);
            toggle_items_grid_column(frm, "custom_fixed_leaf_width", false);
            toggle_items_grid_column(frm, "custom_show_square_count", false);
            toggle_items_grid_column(frm, "custom_square_count", has_square_count);
            toggle_items_grid_column(frm, "custom_show_glass_options", false);
            toggle_items_grid_column(frm, "custom_glass_type", has_glass_options);
            toggle_items_grid_column(frm, "custom_glass_model", has_glass_options);
            toggle_items_grid_column(frm, "custom_exception_component_1", true);
            toggle_items_grid_column(frm, "custom_exception_component_2", true);
            toggle_items_grid_column(frm, "custom_no_qitaat", false);
            toggle_items_grid_column(frm, "custom_without_frame_components", false);
            toggle_items_grid_column(frm, "custom_component_exclusion_groups", false);
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
                apply_component_exclusion_group_visibility(frm, grid_row.doc.name);
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

// Refresh calculations while retaining the cancelled placeholder row.
frappe.ui.form.on("Material Request Item", {
    custom_is_cancelled_row: function(frm) { request_cutting_render(frm); render_manual_manufacturing_button(frm); }
});
// END MIGRATED CLIENT SCRIPT
})();
