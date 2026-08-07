from __future__ import annotations

import frappe

from namar_test.delivery_components import service


@frappe.whitelist()
def sync_delivery_component_packages(mr: str | None = None, dry_run: int | str | bool = 0):
    return service.sync_delivery_component_packages(mr, dry_run)


@frappe.whitelist()
def get_delivery_component_packages(
    mr: str | None = None,
    component_package: str | None = None,
    package: str | None = None,  # noqa: A002 - API compatibility with old query param
):
    return service.get_delivery_component_packages(mr, component_package or package)


@frappe.whitelist()
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


@frappe.whitelist()
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


@frappe.whitelist()
def get_material_request_fulfillment_readiness(mr: str | None = None):
    return service.get_material_request_fulfillment_readiness(mr)
