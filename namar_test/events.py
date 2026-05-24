from __future__ import annotations

import frappe

from .server_runtime import run_event_script

def _customer_has_mobile(doc) -> bool:
    mobile_no = (doc.get("mobile_no") or "").strip()
    if mobile_no:
        return True

    primary_contact = (doc.get("customer_primary_contact") or "").strip()
    if not primary_contact:
        return False

    contact_mobile = frappe.db.get_value("Contact", primary_contact, "mobile_no")
    return bool((contact_mobile or "").strip())

def validate_customer_mobile_on_insert(doc, method=None):
    if _customer_has_mobile(doc):
        return

    frappe.throw(
        '<div dir="rtl" style="text-align:right">'
        "رقم الجوال إلزامي عند إنشاء عميل جديد. "
        "اختر جهة اتصال أساسية تحتوي على رقم جوال قبل حفظ العميل."
        "</div>",
        title="رقم الجوال مطلوب",
    )

def stop_pay(doc, method=None):
    return run_event_script("Stop Pay", doc, method)

def sync_customer_vip_on_payment_cancel(doc, method=None):
    return run_event_script("sync_customer_vip_on_payment_cancel", doc, method)

def sync_customer_vip_on_payment_submit(doc, method=None):
    return run_event_script("sync_customer_vip_on_payment_submit", doc, method)

def sync_customer_vip_to_material_requests(doc, method=None):
    return run_event_script("sync_customer_vip_to_material_requests", doc, method)

def sync_material_request_branch_shares(doc, method=None):
    return run_event_script("sync_material_request_branch_shares", doc, method)

def sync_material_request_branch_shares_submitted(doc, method=None):
    return run_event_script("sync_material_request_branch_shares_submitted", doc, method)

def sync_material_request_customer_vip_on_save(doc, method=None):
    return run_event_script("sync_material_request_customer_vip_on_save", doc, method)

def sync_material_request_state_duration_after_submit(doc, method=None):
    return run_event_script("sync_material_request_state_duration_after_submit", doc, method)

def sync_material_request_state_duration_on_save(doc, method=None):
    return run_event_script("sync_material_request_state_duration_on_save", doc, method)

def total(doc, method=None):
    return run_event_script("total", doc, method)

def validate_so_qty_on_zero_price(doc, method=None):
    return run_event_script("Validate SO Qty on Zero Price", doc, method)

def validate_delivery_note_qty(doc, method=None):
    return run_event_script("validate_delivery_note_qty", doc, method)

def validate_installation_note_qty(doc, method=None):
    return run_event_script("validate_installation_note_qty", doc, method)

def validate_material_request_required_on_submit(doc, method=None):
    return run_event_script("validate_material_request_required_on_submit", doc, method)

def event_handler(doc, method=None):
    return run_event_script("تجاوز كمية أمر البيع لطلب المواد", doc, method)

def event_handler_2(doc, method=None):
    return run_event_script("يوجد عجز في رصيد المفوتر", doc, method)
