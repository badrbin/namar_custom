app_name = "demo"
app_title = "Demo"
app_publisher = "Namar"
app_description = "حساب التخصيم على بيئة التجربة"
app_email = "developer@example.com"
app_license = "Proprietary"

required_apps = ["erpnext", "namar_test"]
doctype_js = {"Material Request": "public/js/cutting.js"}
# Runs even when Frappe serves cached bootinfo, so a page reload observes the
# activation switch immediately without waiting for another user's cache.
extend_bootinfo = "demo.boot.boot_session"

# Preserve existing callers; while the trial is inactive this executes the
# existing enabled Server Script. Activation switches both names to Python.
override_whitelisted_methods = {
    "get_cutting_values_bulk_exclusions_test": "demo.api.legacy_cutting_values",
}
