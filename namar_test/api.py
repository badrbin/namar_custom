from __future__ import annotations

import frappe

from .material_requests import get_related_items as get_related_material_request_items
from .server_runtime import run_api_script

@frappe.whitelist()
def apply_material_request_scenario_bypass(**kwargs):
    return run_api_script("apply_material_request_scenario_bypass", kwargs)

@frappe.whitelist()
def backfill_material_request_state_duration(**kwargs):
    return run_api_script("backfill_material_request_state_duration", kwargs)

@frappe.whitelist()
def get_cutting_report(**kwargs):
    return run_api_script("get_cutting_report", kwargs)

@frappe.whitelist()
def get_cutting_values_bulk(**kwargs):
    return run_api_script("get_cutting_values_bulk", kwargs)

@frappe.whitelist()
def get_lead_map_data(**kwargs):
    return run_api_script("get_lead_map_data", kwargs)

@frappe.whitelist()
def get_lead_timeline_data(**kwargs):
    return run_api_script("get_lead_timeline_data", kwargs)

@frappe.whitelist()
def get_lead_visit_logs(**kwargs):
    return run_api_script("get_lead_visit_logs", kwargs)

@frappe.whitelist(allow_guest=True)
def get_manufactured_items(**kwargs):
    return run_api_script("get_manufactured_items", kwargs)

@frappe.whitelist()
def get_map_locations(**kwargs):
    return run_api_script("get_map_locations", kwargs)

@frappe.whitelist()
def get_material_request_scenario_bypass_rules(**kwargs):
    return run_api_script("get_material_request_scenario_bypass_rules", kwargs)

@frappe.whitelist()
def get_mr_full_data(**kwargs):
    return run_api_script("get_mr_full_data", kwargs)

@frappe.whitelist()
def get_sales_dashboard(**kwargs):
    return run_api_script("get_sales_dashboard v1", kwargs)

@frappe.whitelist()
def get_workflow_transitions(**kwargs):
    return run_api_script("get_workflow_transitions", kwargs)

@frappe.whitelist()
def make_dn_from_mr(**kwargs):
    return run_api_script("make_dn_from_mr", kwargs)

@frappe.whitelist()
def make_installation_note_from_mr(**kwargs):
    return run_api_script("make_installation_note_from_mr", kwargs)

@frappe.whitelist()
def make_material_request_from_sales_order_balance(**kwargs):
    return run_api_script("make_material_request_from_sales_order_balance", kwargs)

@frappe.whitelist()
def map_update_field(**kwargs):
    return run_api_script("map_update_field", kwargs)

@frappe.whitelist(allow_guest=True)
def mark_label_manufactured(**kwargs):
    return run_api_script("mark_label_manufactured", kwargs)

@frappe.whitelist()
def mark_manufactured_rows(**kwargs):
    return run_api_script("mark_manufactured_rows", kwargs)

@frappe.whitelist(allow_guest=True)
def namar_capture_lead(**kwargs):
    return run_api_script("namar_capture_lead", kwargs)

@frappe.whitelist()
def ops_map_apply_workflow(**kwargs):
    return run_api_script("ops_map_apply_workflow", kwargs)

@frappe.whitelist()
def ops_map_get_order(**kwargs):
    return run_api_script("ops_map_get_order", kwargs)

@frappe.whitelist()
def ops_map_get_points(**kwargs):
    return run_api_script("ops_map_get_points", kwargs)

@frappe.whitelist()
def ops_map_get_transitions(**kwargs):
    return run_api_script("ops_map_get_transitions", kwargs)

@frappe.whitelist()
def resolve_map_url(**kwargs):
    return run_api_script("resolve_map_url", kwargs)

@frappe.whitelist()
def run_full_report(**kwargs):
    return run_api_script("run_full_report", kwargs)

@frappe.whitelist()
def save_lead_map_lead(**kwargs):
    return run_api_script("save_lead_map_lead", kwargs)

@frappe.whitelist()
def save_lead_visit_log(**kwargs):
    return run_api_script("save_lead_visit_log", kwargs)

@frappe.whitelist()
def save_map_lead(**kwargs):
    return run_api_script("save_map_lead", kwargs)

@frappe.whitelist()
def sync_customer_vip_from_gl(**kwargs):
    return run_api_script("sync_customer_vip_from_gl", kwargs)

@frappe.whitelist()
def sync_material_request_customer_vip(**kwargs):
    return run_api_script("sync_material_request_customer_vip", kwargs)

@frappe.whitelist()
def update_old_coordinates(**kwargs):
    return run_api_script("update_old_coordinates", kwargs)

@frappe.whitelist()
def update_total_qty(**kwargs):
    return run_api_script("update_total_qty", kwargs)

@frappe.whitelist()
def get_purchase_dashboard(**kwargs):
    return run_api_script("كشف حساب أمر الشراء", kwargs)

@frappe.whitelist()
def get_customer_summary(**kwargs):
    return run_api_script("كشف حساب العميل", kwargs)

@frappe.whitelist()
def get_supplier_summary(**kwargs):
    return run_api_script("كشف حساب المورد", kwargs)

@frappe.whitelist()
def get_related_items(**kwargs):
    return get_related_material_request_items(
        sales_order=kwargs.get("sales_order"),
        mr_name=kwargs.get("mr_name"),
        current_items=kwargs.get("current_items"),
    )
