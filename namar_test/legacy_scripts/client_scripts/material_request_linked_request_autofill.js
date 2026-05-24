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
