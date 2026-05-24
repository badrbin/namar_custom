frappe.ui.form.on("Cutting Template", {
    refresh: function(frm) {

        // === زر إضافة مجموعات أصناف ===
        frm.add_custom_button("إضافة مجموعات أصناف", function() {
            var d = new frappe.ui.Dialog({
                title: "إضافة مجموعات أصناف",
                fields: [
                    {
                        fieldname: "item_groups",
                        fieldtype: "MultiSelectList",
                        label: "مجموعات الأصناف",
                        get_data: function(txt) {
                            return frappe.db.get_link_options("Item Group", txt);
                        }
                    },
                    { fieldtype: "Section Break" },
                    {
                        fieldname: "leaf_w", fieldtype: "Float", label: "Leaf W", default: 0, columns: 2
                    },
                    {
                        fieldname: "leaf_h", fieldtype: "Float", label: "Leaf H", default: 0, columns: 2
                    },
                    { fieldtype: "Column Break" },
                    {
                        fieldname: "u_w", fieldtype: "Float", label: "U W", default: 0, columns: 2
                    },
                    {
                        fieldname: "u_h", fieldtype: "Float", label: "U H", default: 0, columns: 2
                    },
                    { fieldtype: "Column Break" },
                    {
                        fieldname: "panel_w", fieldtype: "Float", label: "Panel W", default: 0, columns: 2
                    },
                    {
                        fieldname: "panel_h", fieldtype: "Float", label: "Panel H", default: 0, columns: 2
                    },
                    { fieldtype: "Column Break" },
                    {
                        fieldname: "type", fieldtype: "Link", label: "Type", options: "Cutting Type"
                    }
                ],
                size: "large",
                primary_action_label: "إضافة",
                primary_action: function(values) {
                    var groups = values.item_groups || [];
                    if (!groups.length) {
                        frappe.msgprint("اختر مجموعة واحدة على الأقل");
                        return;
                    }
                    groups.forEach(function(ig) {
                        var row = frm.add_child("items");
                        frappe.model.set_value(row.doctype, row.name, "item_group", ig);
                        frappe.model.set_value(row.doctype, row.name, "leaf_w", values.leaf_w || 0);
                        frappe.model.set_value(row.doctype, row.name, "leaf_h", values.leaf_h || 0);
                        frappe.model.set_value(row.doctype, row.name, "u_w", values.u_w || 0);
                        frappe.model.set_value(row.doctype, row.name, "u_h", values.u_h || 0);
                        frappe.model.set_value(row.doctype, row.name, "panel_w", values.panel_w || 0);
                        frappe.model.set_value(row.doctype, row.name, "panel_h", values.panel_h || 0);
                        frappe.model.set_value(row.doctype, row.name, "type", values.type || '');
                    });
                    frm.refresh_field("items");
                    d.hide();
                    frappe.show_alert({message: "تم إضافة " + groups.length + " مجموعة", indicator: "green"});
                }
            });
            d.show();
        }, "إضافة");

        // === زر إضافة أصناف متعددة (الأصلي) ===
        frm.add_custom_button("إضافة أصناف متعددة", function() {
            var d = new frappe.ui.Dialog({
                title: "إضافة أصناف",
                fields: [
                    {
                        fieldname: "items",
                        fieldtype: "MultiSelectList",
                        label: "الأصناف",
                        get_data: function(txt) {
                            return frappe.db.get_link_options("Item", txt);
                        }
                    }
                ],
                primary_action_label: "إضافة",
                primary_action: function(values) {
                    (values.items || []).forEach(function(item) {
                        var row = frm.add_child("items");
                        frappe.model.set_value(row.doctype, row.name, "item_code", item);
                    });
                    frm.refresh_field("items");
                    d.hide();
                }
            });
            d.show();
        }, "إضافة");

        // === زر إضافة نطاقات متعددة (محدّث) ===
        frm.add_custom_button("إضافة نطاقات متعددة", function() {
            // Collect item_codes and item_groups from items table
            var item_codes = [];
            var item_groups = [];
            (frm.doc.items || []).forEach(function(row) {
                if (row.item_code && item_codes.indexOf(row.item_code) === -1) {
                    item_codes.push(row.item_code);
                }
                if (row.item_group && item_groups.indexOf(row.item_group) === -1) {
                    item_groups.push(row.item_group);
                }
            });

            var d = new frappe.ui.Dialog({
                title: "إضافة نطاقات",
                fields: [
                    {
                        fieldname: "component",
                        fieldtype: "Link",
                        label: "المكوّن",
                        options: "Store Component",
                        reqd: 1
                    },
                    {
                        fieldname: "store_item",
                        fieldtype: "Link",
                        label: "صنف المخزن",
                        options: "Item"
                    },
                    {
                        fieldname: "qty",
                        fieldtype: "Float",
                        label: "الكمية",
                        default: 1
                    },
                    {
                        fieldname: "per_leaf",
                        fieldtype: "Check",
                        label: "بحث بالدرفة"
                    },
                    { fieldtype: "Section Break", label: "التطبيق على" },
                    {
                        fieldname: "target_type",
                        fieldtype: "Select",
                        label: "النوع",
                        options: "مجموعات أصناف\nأصناف\nالكل (بدون تحديد)",
                        default: "مجموعات أصناف",
                        onchange: function() {
                            var val = d.get_value("target_type");
                            d.fields_dict.target_groups.df.hidden = val !== "مجموعات أصناف";
                            d.fields_dict.target_items.df.hidden = val !== "أصناف";
                            d.refresh();
                        }
                    },
                    {
                        fieldname: "target_groups",
                        fieldtype: "MultiSelectList",
                        label: "المجموعات",
                        get_data: function(txt) {
                            return item_groups
                                .filter(function(c) { return !txt || c.indexOf(txt) !== -1; })
                                .map(function(c) { return { value: c, description: c }; });
                        }
                    },
                    {
                        fieldname: "target_items",
                        fieldtype: "MultiSelectList",
                        label: "الأصناف",
                        hidden: 1,
                        get_data: function(txt) {
                            return item_codes
                                .filter(function(c) { return !txt || c.toLowerCase().indexOf(txt.toLowerCase()) !== -1; })
                                .map(function(c) { return { value: c, description: c }; });
                        }
                    },
                    { fieldtype: "Section Break", label: "نطاقات الأبعاد (اختياري)" },
                    {
                        fieldname: "width_from", fieldtype: "Float", label: "عرض من", default: 0
                    },
                    {
                        fieldname: "width_to", fieldtype: "Float", label: "عرض إلى", default: 0
                    },
                    { fieldtype: "Column Break" },
                    {
                        fieldname: "height_from", fieldtype: "Float", label: "طول من", default: 0
                    },
                    {
                        fieldname: "height_to", fieldtype: "Float", label: "طول إلى", default: 0
                    },
                    { fieldtype: "Column Break" },
                    {
                        fieldname: "wall_width_from", fieldtype: "Float", label: "عرض جدار من", default: 0
                    },
                    {
                        fieldname: "wall_width_to", fieldtype: "Float", label: "عرض جدار إلى", default: 0
                    }
                ],
                size: "large",
                primary_action_label: "إضافة",
                primary_action: function(values) {
                    var target_type = values.target_type;
                    var targets = [];

                    if (target_type === "مجموعات أصناف") {
                        (values.target_groups || []).forEach(function(g) {
                            targets.push({ item_group: g, item_code: '' });
                        });
                    } else if (target_type === "أصناف") {
                        (values.target_items || []).forEach(function(ic) {
                            targets.push({ item_group: '', item_code: ic });
                        });
                    } else {
                        targets.push({ item_group: '', item_code: '' });
                    }

                    if (!targets.length) {
                        frappe.msgprint("اختر هدف واحد على الأقل");
                        return;
                    }

                    targets.forEach(function(t) {
                        var row = frm.add_child("wall_ranges");
                        frappe.model.set_value(row.doctype, row.name, "component", values.component);
                        frappe.model.set_value(row.doctype, row.name, "item_code", t.item_code);
                        frappe.model.set_value(row.doctype, row.name, "item_group", t.item_group);
                        frappe.model.set_value(row.doctype, row.name, "item", values.store_item || '');
                        frappe.model.set_value(row.doctype, row.name, "qty", values.qty || 1);
                        frappe.model.set_value(row.doctype, row.name, "per_leaf", values.per_leaf || 0);
                        frappe.model.set_value(row.doctype, row.name, "width_from", values.width_from || 0);
                        frappe.model.set_value(row.doctype, row.name, "width_to", values.width_to || 0);
                        frappe.model.set_value(row.doctype, row.name, "height_from", values.height_from || 0);
                        frappe.model.set_value(row.doctype, row.name, "height_to", values.height_to || 0);
                        frappe.model.set_value(row.doctype, row.name, "wall_width_from", values.wall_width_from || 0);
                        frappe.model.set_value(row.doctype, row.name, "wall_width_to", values.wall_width_to || 0);
                    });
                    frm.refresh_field("wall_ranges");
                    d.hide();
                    frappe.show_alert({message: "تم إضافة " + targets.length + " نطاق", indicator: "green"});
                }
            });
            d.show();
        }, "إضافة");

        // === نسخ نطاقات من نموذج آخر ===
        frm.add_custom_button("نسخ من نموذج آخر", function() {
            var d = new frappe.ui.Dialog({
                title: "نسخ نطاقات من نموذج آخر",
                fields: [
                    {
                        fieldname: "source",
                        fieldtype: "Link",
                        label: "النموذج المصدر",
                        options: "Cutting Template",
                        reqd: 1,
                        get_query: function() {
                            return { filters: { name: ["!=", frm.doc.name] } };
                        }
                    },
                    {
                        fieldname: "copy_items",
                        fieldtype: "Check",
                        label: "نسخ الأصناف أيضاً",
                        default: 0
                    },
                    {
                        fieldname: "copy_ranges",
                        fieldtype: "Check",
                        label: "نسخ النطاقات",
                        default: 1
                    }
                ],
                primary_action_label: "نسخ",
                primary_action: function(values) {
                    frappe.call({
                        method: "frappe.client.get",
                        args: { doctype: "Cutting Template", name: values.source },
                        callback: function(r) {
                            if (!r.message) return;
                            var source = r.message;
                            var count = 0;

                            if (values.copy_items && source.items) {
                                source.items.forEach(function(si) {
                                    var row = frm.add_child("items");
                                    frappe.model.set_value(row.doctype, row.name, "item_code", si.item_code || '');
                                    frappe.model.set_value(row.doctype, row.name, "item_group", si.item_group || '');
                                    frappe.model.set_value(row.doctype, row.name, "leaf_w", si.leaf_w || 0);
                                    frappe.model.set_value(row.doctype, row.name, "leaf_h", si.leaf_h || 0);
                                    frappe.model.set_value(row.doctype, row.name, "u_w", si.u_w || 0);
                                    frappe.model.set_value(row.doctype, row.name, "u_h", si.u_h || 0);
                                    frappe.model.set_value(row.doctype, row.name, "panel_w", si.panel_w || 0);
                                    frappe.model.set_value(row.doctype, row.name, "panel_h", si.panel_h || 0);
                                    frappe.model.set_value(row.doctype, row.name, "type", si.type || '');
                                    frappe.model.set_value(row.doctype, row.name, "sliding_type", si.sliding_type || '');
                                    count++;
                                });
                                frm.refresh_field("items");
                            }

                            if (values.copy_ranges && source.wall_ranges) {
                                source.wall_ranges.forEach(function(sr) {
                                    var row = frm.add_child("wall_ranges");
                                    frappe.model.set_value(row.doctype, row.name, "component", sr.component || '');
                                    frappe.model.set_value(row.doctype, row.name, "item_code", sr.item_code || '');
                                    frappe.model.set_value(row.doctype, row.name, "item_group", sr.item_group || '');
                                    frappe.model.set_value(row.doctype, row.name, "sliding_type", sr.sliding_type || '');
                                    frappe.model.set_value(row.doctype, row.name, "width_from", sr.width_from || 0);
                                    frappe.model.set_value(row.doctype, row.name, "width_to", sr.width_to || 0);
                                    frappe.model.set_value(row.doctype, row.name, "height_from", sr.height_from || 0);
                                    frappe.model.set_value(row.doctype, row.name, "height_to", sr.height_to || 0);
                                    frappe.model.set_value(row.doctype, row.name, "wall_width_from", sr.wall_width_from || 0);
                                    frappe.model.set_value(row.doctype, row.name, "wall_width_to", sr.wall_width_to || 0);
                                    frappe.model.set_value(row.doctype, row.name, "item", sr.item || '');
                                    frappe.model.set_value(row.doctype, row.name, "qty", sr.qty || 1);
                                    frappe.model.set_value(row.doctype, row.name, "per_leaf", sr.per_leaf || 0);
                                    count++;
                                });
                                frm.refresh_field("wall_ranges");
                            }

                            d.hide();
                            frappe.show_alert({message: "تم نسخ " + count + " صف", indicator: "green"});
                        }
                    });
                }
            });
            d.show();
        }, "إضافة");

        // === فلاتر ===
        frm.set_query("item_code", "wall_ranges", function(doc) {
            var codes = [];
            (doc.items || []).forEach(function(row) {
                if (row.item_code && codes.indexOf(row.item_code) === -1) {
                    codes.push(row.item_code);
                }
            });
            if (codes.length) {
                return { filters: { "name": ["in", codes] } };
            }
            return {};
        });

        frm.set_query("item_group", "wall_ranges", function(doc) {
            var groups = [];
            (doc.items || []).forEach(function(row) {
                if (row.item_group && groups.indexOf(row.item_group) === -1) {
                    groups.push(row.item_group);
                }
            });
            if (groups.length) {
                return { filters: { "name": ["in", groups] } };
            }
            return { filters: { "name": ["in", ["___"]] } };
        });
    }
});
