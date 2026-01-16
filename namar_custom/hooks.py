app_name = "namar_custom"
app_title = "Namar Custom"
app_publisher = "Badr"
app_description = "Customizations for Namar"
app_icon = "octicon octicon-file-directory"
app_color = "grey"
app_email = "badrbin@gmail.com"
app_license = "MIT"

doc_events = {
    "Material Request": {
        "validate": "namar_custom.api.manus_logic.validate_material_request_against_billed"
    },
    "Payment Entry": {
        "validate": "namar_custom.api.manus_logic.validate_payment_entry_supplier"
    },
    "Sales Invoice": {
        "validate": "namar_custom.api.manus_logic.validate_sales_invoice_qty_against_so"
    }
}
