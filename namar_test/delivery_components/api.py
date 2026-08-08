from __future__ import annotations

import frappe

from namar_test.delivery_components import service
from namar_test.delivery_components import tracking_codes


@frappe.whitelist()
def sync_delivery_component_packages(mr: str | None = None, dry_run: int | str | bool = 0):
    return service.sync_delivery_component_packages(mr, dry_run)


@frappe.whitelist(allow_guest=True)
def get_delivery_component_packages(
    mr: str | None = None,
    component_package: str | None = None,
    package: str | None = None,  # noqa: A002 - API compatibility with old query param
):
    return service.get_delivery_component_packages(mr, component_package or package)


@frappe.whitelist(allow_guest=True)
def mark_delivery_component_package_ready(
    mr: str | None = None,
    component_package: str | None = None,
    package: str | None = None,  # noqa: A002 - API compatibility with old query param
    mode: str = "full",
    ready_qty=None,
    qty=None,
    source: str = "QR",
):
    return service.mark_delivery_component_package_ready(
        material_request=mr,
        component_package=component_package or package,
        mode=mode,
        ready_qty=ready_qty or qty,
        source=source,
    )


@frappe.whitelist(allow_guest=True)
def mark_delivery_component_package_event(
    mr: str | None = None,
    component_package: str | None = None,
    package: str | None = None,  # noqa: A002 - API compatibility with barcode query param
    action: str = "ready",
    mode: str = "full",
    ready_qty=None,
    qty=None,
    source: str = "QR",
):
    return service.mark_delivery_component_package_event(
        material_request=mr,
        component_package=component_package or package,
        action=action,
        mode=mode,
        ready_qty=ready_qty or qty,
        source=source,
    )


@frappe.whitelist(allow_guest=True)
def get_material_request_fulfillment_readiness(mr: str | None = None):
    return service.get_material_request_fulfillment_readiness(mr)


@frappe.whitelist(allow_guest=True)
def resolve_delivery_tracking_code(code: str | None = None):
    return service.resolve_delivery_tracking_code(code)


@frappe.whitelist()
def backfill_material_request_tracking_codes(
    limit: int | str | None = 250,
    dry_run: int | str | bool = 1,
):
    frappe.only_for("System Manager")
    return tracking_codes.backfill_material_request_tracking_codes(limit=limit, dry_run=dry_run)


@frappe.whitelist()
def audit_material_request_tracking_codes():
    frappe.only_for("System Manager")
    return tracking_codes.audit_material_request_tracking_codes()


@frappe.whitelist()
def backfill_component_package_identifiers(
    limit: int | str | None = 100,
    dry_run: int | str | bool = 1,
):
    frappe.only_for("System Manager")
    return tracking_codes.backfill_component_package_identifiers(limit=limit, dry_run=dry_run)


@frappe.whitelist()
def audit_component_package_identifiers():
    frappe.only_for("System Manager")
    return tracking_codes.audit_component_package_identifiers()
