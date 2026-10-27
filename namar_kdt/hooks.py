app_name = "namar_kdt"
app_title = "Namar KDT"
app_publisher = "Namar"
app_description = "أوامر القص والطباعة من نتائج التخصيم"
app_email = "developer@example.com"
app_license = "Proprietary"

required_apps = ["erpnext", "namar_test"]
# Frappe 15 skips doctype_js for Custom DocTypes. Desk loads bootinfo and its
# form bundle before app assets; the script's boot flag and registration guard
# keep installation inactive and register only the KDT form after activation.
app_include_js = ["/assets/namar_kdt/js/desk.js"]
extend_bootinfo = "namar_kdt.boot.boot_session"
override_whitelisted_methods = {
    "kdt_cutting_desk_api": "namar_kdt.api.legacy_call",
}
doc_events = {
    "KDT Cutting Batch": {
        "before_submit": "namar_kdt.guards.guard_legacy_batch",
    },
}
before_uninstall = "namar_kdt.migration.before_uninstall"
