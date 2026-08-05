from __future__ import annotations

import frappe

from namar_test.operation import service


@frappe.whitelist()
def get_bootstrap():
    return service.get_bootstrap()


@frappe.whitelist()
def list_material_requests(
    search: str | None = None,
    status: str | None = None,
    workflow_state: str | None = None,
    docstatus: int | str | None = None,
    page: int | str | None = None,
    page_length: int | str | None = None,
    limit_start: int | str | None = None,
    limit_page_length: int | str | None = None,
    view: str | None = None,
    material_request_type: str | None = None,
    sales_order: str | None = None,
):
    return service.list_material_requests(
        search=search,
        status=status or workflow_state,
        docstatus=docstatus,
        page=page,
        page_length=page_length or limit_page_length,
        limit_start=limit_start,
        view=view,
        material_request_type=material_request_type,
        sales_order=sales_order,
    )


@frappe.whitelist()
def get_material_request(name: str):
    return service.get_material_request(name)


@frappe.whitelist()
def search_options(
    doctype: str | None = None,
    option_type: str | None = None,
    txt: str | None = None,
    search: str | None = None,
    query: str | None = None,
    page: int | str | None = None,
    page_length: int | str | None = None,
    company: str | None = None,
):
    return service.search_options(
        doctype=doctype,
        option_type=option_type,
        search=txt or search or query,
        page=page,
        page_length=page_length,
        company=company,
    )


@frappe.whitelist()
def prepare_from_sales_order(sales_order: str):
    return service.prepare_from_sales_order(sales_order)


@frappe.whitelist(methods=["POST"])
def save_material_request(doc, expected_modified: str | None = None):
    return service.save_material_request(doc, expected_modified=expected_modified)


@frappe.whitelist(methods=["POST"])
def apply_workflow(name: str, action: str, expected_modified: str):
    return service.apply_workflow(name, action, expected_modified)
