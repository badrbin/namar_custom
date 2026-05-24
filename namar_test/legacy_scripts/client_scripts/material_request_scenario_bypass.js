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
