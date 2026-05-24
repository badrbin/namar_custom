from __future__ import annotations

import frappe

MIGRATED_CLIENT_SCRIPT_NAMES = {
    "Customer Statement - DN",
    "Customer Statement - SI",
    "Customer Statement - SO",
    "Extract Google Map Coordinates",
    "h",
    "j",
    "Lead Google Map Coordinates",
    "Material Request Dashboard",
    "Material Request Driver Filter",
    "Material Request Linked Request Autofill",
    "Material Request Manufacturing Status List",
    "Material Request Manufacturing Tab",
    "Material Request Replacement Reference Filter",
    "Material Request Scenario Bypass",
    "Purchase Order Dashboard PO",
    "Sales Order Dashboard SO",
    "Sales Order Material Request By Balance",
    "Supplier Statement - PO",
    "Total Quantity",
    "Urgent Material Request Banner",
    "VIP Customer Highlight",
    "حساب التخصيم",
    "خصم تصنيع",
    "فلتر أصناف النطاقات",
    "كل طلبات المواد",
}


def boot_session(bootinfo):
    enabled_legacy_count = frappe.db.count(
        "Client Script",
        filters={"name": ["in", list(MIGRATED_CLIENT_SCRIPT_NAMES)], "enabled": 1},
    )
    bootinfo.namar_test_client_scripts_enabled = enabled_legacy_count == 0
