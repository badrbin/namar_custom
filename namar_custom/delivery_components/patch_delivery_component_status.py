from __future__ import annotations


FIELDNAME = "custom_delivery_component_status"
NO_BARCODE_STATUS = "لا تتطلب باركود"


def add_no_barcode_status(options: str | None) -> str:
    values = [value.strip() for value in (options or "").splitlines() if value.strip()]
    if NO_BARCODE_STATUS in values:
        return "\n".join(values)

    insert_at = values.index("لا توجد مكونات") + 1 if "لا توجد مكونات" in values else len(values)
    values.insert(insert_at, NO_BARCODE_STATUS)
    return "\n".join(values)


def execute() -> None:
    import frappe

    custom_field = frappe.db.get_value(
        "Custom Field",
        {"dt": "Material Request", "fieldname": FIELDNAME},
        ["name", "options"],
        as_dict=True,
    )
    if not custom_field:
        return

    options = add_no_barcode_status(custom_field.options)
    if options == (custom_field.options or ""):
        return

    frappe.db.set_value("Custom Field", custom_field.name, "options", options)
    frappe.clear_cache(doctype="Material Request")
