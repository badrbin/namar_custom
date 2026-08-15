from __future__ import annotations

app_name = "namar_test"
app_title = "Namar Test"
app_publisher = "Namar"
app_description = "Namar ERPNext customizations migrated from live test Server Scripts and Client Scripts."
app_email = "badrarroug@namar.net"
app_license = "MIT"

boot_session = "namar_test.boot.boot_session"

fixtures = [
    {
        "dt": "DocType",
        "filters": [
            [
                "name",
                "in",
                [
                    "Material Request Manufacturing Detail",
                    "Material Request Manufactured Door",
                ],
            ]
        ],
    },
    {
        "dt": "Custom Field",
        "filters": [
            [
                "name",
                "in",
                [
                    "Material Request-custom_component_manufacturing_remaining_count",
                    "Material Request-custom_component_manufacturing_status",
                    "Material Request-custom_component_manufacturing_total_count",
                    "Material Request-custom_delivery_readiness_status",
                    "Material Request-custom_delivery_readiness_summary",
                    "Material Request-custom_manufactured_doors",
                    "Material Request-custom_manufacturing_completed_at",
                    "Material Request-custom_manufacturing_completed_by",
                    "Material Request-custom_manufacturing_details",
                    "Material Request-custom_manufacturing_remaining_count",
                    "Material Request-custom_manufacturing_status",
                    "Material Request-custom_manufacturing_total_count",
                    "Material Request Item-custom_manufactured_qty",
                    "Store Component-custom_manufacturing_tracking_mode",
                    "Store Component-custom_required_for_delivery",
                ],
            ]
        ],
    },
]

override_whitelisted_methods = {
    "apply_material_request_scenario_bypass": "namar_test.api.apply_material_request_scenario_bypass",
    "backfill_material_request_state_duration": "namar_test.api.backfill_material_request_state_duration",
    "get_cutting_report": "namar_test.api.get_cutting_report",
    "get_cutting_values_bulk": "namar_test.api.get_cutting_values_bulk",
    "get_customer_summary": "namar_test.api.get_customer_summary",
    "get_lead_map_data": "namar_test.api.get_lead_map_data",
    "get_lead_timeline_data": "namar_test.api.get_lead_timeline_data",
    "get_lead_visit_logs": "namar_test.api.get_lead_visit_logs",
    "get_manufactured_items": "namar_test.api.get_manufactured_items",
    "get_map_locations": "namar_test.api.get_map_locations",
    "get_material_request_scenario_bypass_rules": "namar_test.api.get_material_request_scenario_bypass_rules",
    "get_mr_full_data": "namar_test.api.get_mr_full_data",
    "get_purchase_dashboard": "namar_test.api.get_purchase_dashboard",
    "get_related_items": "namar_test.api.get_related_items",
    "get_sales_dashboard": "namar_test.api.get_sales_dashboard",
    "get_supplier_summary": "namar_test.api.get_supplier_summary",
    "get_workflow_transitions": "namar_test.api.get_workflow_transitions",
    "make_dn_from_mr": "namar_test.api.make_dn_from_mr",
    "make_installation_note_from_mr": "namar_test.api.make_installation_note_from_mr",
    "make_material_request_from_sales_order_balance": "namar_test.api.make_material_request_from_sales_order_balance",
    "map_update_field": "namar_test.api.map_update_field",
    "mark_label_manufactured": "namar_test.api.mark_label_manufactured",
    "mark_label_manufactured_v2": "namar_test.api.mark_label_manufactured_v2",
    "mark_manufactured_rows": "namar_test.api.mark_manufactured_rows",
    "mark_manufactured_rows_v2": "namar_test.api.mark_manufactured_rows_v2",
    "namar_capture_lead": "namar_test.api.namar_capture_lead",
    "ops_map_apply_workflow": "namar_test.api.ops_map_apply_workflow",
    "ops_map_get_order": "namar_test.api.ops_map_get_order",
    "ops_map_get_points": "namar_test.api.ops_map_get_points",
    "ops_map_get_transitions": "namar_test.api.ops_map_get_transitions",
    "resolve_map_url": "namar_test.api.resolve_map_url",
    "run_full_report": "namar_test.api.run_full_report",
    "save_lead_map_lead": "namar_test.api.save_lead_map_lead",
    "save_lead_visit_log": "namar_test.api.save_lead_visit_log",
    "save_map_lead": "namar_test.api.save_map_lead",
    "sync_customer_vip_from_gl": "namar_test.api.sync_customer_vip_from_gl",
    "sync_delivery_component_packages": "namar_test.delivery_components.api.sync_delivery_component_packages",
    "get_delivery_component_packages": "namar_test.delivery_components.api.get_delivery_component_packages",
    "mark_delivery_component_package_ready": "namar_test.delivery_components.api.mark_delivery_component_package_ready",
    "sync_material_request_customer_vip": "namar_test.api.sync_material_request_customer_vip",
    "update_old_coordinates": "namar_test.api.update_old_coordinates",
    "update_total_qty": "namar_test.api.update_total_qty",
}

doc_events = {
    "Comment": {
        "on_update": ["namar_test.comment_mentions.notify_new_mentions_on_comment_update"],
    },
    "Payment Entry": {
        "before_save": ["namar_test.events.stop_pay"],
        "on_cancel": ["namar_test.events.sync_customer_vip_on_payment_cancel"],
        "on_submit": ["namar_test.events.sync_customer_vip_on_payment_submit"],
    },
    "Customer": {
        "before_insert": ["namar_test.events.validate_customer_mobile_on_insert"],
        "on_update": ["namar_test.events.sync_customer_vip_to_material_requests"],
    },
    "Material Request": {
        "before_insert": ["namar_test.delivery_components.tracking_codes.ensure_material_request_tracking_code"],
        "on_update": ["namar_test.events.sync_material_request_branch_shares"],
        "before_update_after_submit": [
            "namar_test.events.sync_material_request_branch_shares_submitted",
            "namar_test.events.sync_material_request_state_duration_after_submit",
            "namar_test.events.total",
        ],
        "before_save": [
            "namar_test.events.sync_material_request_customer_vip_on_save",
            "namar_test.events.sync_material_request_state_duration_on_save",
        ],
        "before_validate": [
            "namar_test.events.validate_material_request_required_on_submit",
            "namar_test.events.event_handler",
            "namar_test.events.event_handler_2",
        ],
    },
    "Sales Invoice": {
        "before_save": ["namar_test.events.validate_so_qty_on_zero_price"],
    },
    "Delivery Note": {
        "before_save": ["namar_test.events.validate_delivery_note_qty"],
    },
    "Installation Note": {
        "before_save": ["namar_test.events.validate_installation_note_qty"],
    },
}

scheduler_events = {
    "hourly": ["namar_test.scheduler.scheduled_sync_material_request_state_duration"],
}

doctype_js = {
    "Cutting Template": "public/js/doctype/cutting_template_form.js",
    "Delivery Note": "public/js/doctype/delivery_note_form.js",
    "Installation Note": "public/js/doctype/installation_note_form.js",
    "Lead": "public/js/doctype/lead_form.js",
    "Material Request": "public/js/doctype/material_request_form.js",
    "Purchase Order": "public/js/doctype/purchase_order_form.js",
    "Sales Invoice": "public/js/doctype/sales_invoice_form.js",
    "Sales Order": "public/js/doctype/sales_order_form.js",
}

doctype_list_js = {
    "Material Request": "public/js/doctype/material_request_list.js",
}

app_include_js = [
    "comment_history.bundle.js",
    "/assets/namar_test/js/doctype/cutting_template_form.js",
    "/assets/namar_test/js/doctype/delivery_note_form.js",
    "/assets/namar_test/js/doctype/installation_note_form.js",
    "/assets/namar_test/js/doctype/lead_form.js",
    "/assets/namar_test/js/doctype/material_request_form.js",
    "/assets/namar_test/js/doctype/material_request_list.js",
    "/assets/namar_test/js/doctype/purchase_order_form.js",
    "/assets/namar_test/js/doctype/sales_invoice_form.js",
    "/assets/namar_test/js/doctype/sales_order_form.js",
    "/assets/namar_test/js/delivery_components/material_request_delivery_components.js",
]

app_include_css = [
    "comment_history.bundle.css",
]

web_include_js = [
    "/assets/namar_test/js/delivery_components/factory_delivery_components.js",
]

jinja = {
    "methods": [
        "namar_test.delivery_components.printing.sector_print_status",
    ],
}
