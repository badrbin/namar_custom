order_name = (frappe.form_dict.get("name") or "").strip()
if not order_name:
    frappe.throw("اسم الطلب مطلوب")

STYLE_COLORS = {
    "Danger": {"solid": "#B52A2A", "text": "#B52A2A", "bg": "#FFF0F0"},
    "Warning": {"solid": "#BD3E0C", "text": "#BD3E0C", "bg": "#FFF1E7"},
    "Success": {"solid": "#16794C", "text": "#16794C", "bg": "#E4F5E9"},
    "Info": {"solid": "#0070CC", "text": "#0070CC", "bg": "#EDF6FD"},
    "Primary": {"solid": "#005BD3", "text": "#005BD3", "bg": "#EAF5FE"},
    "Inverse": {"solid": "#525252", "text": "#525252", "bg": "#F3F3F3"},
}


def normalize_text(value):
    return (value or "").strip()


workflow_state = frappe.db.get_value("Material Request", order_name, "workflow_state")
style_name = frappe.db.get_value("Workflow State", normalize_text(workflow_state), "style") or "Inverse"
palette = STYLE_COLORS.get(normalize_text(style_name), STYLE_COLORS["Inverse"])

doc = frappe.get_doc("Material Request", order_name)
items = []
for item in doc.get("items") or []:
    items.append(
        {
            "item_code": item.get("item_code"),
            "item_name": item.get("item_name"),
            "qty": item.get("qty"),
            "width": item.get("عرض"),
            "height": item.get("طول"),
            "wall_width": item.get("عرض_الجدار"),
            "notes": item.get("ملاحظات"),
        }
    )

frappe.response["message"] = {
    "name": doc.name,
    "customer_name": doc.get("customer_name"),
    "workflow_state": doc.get("workflow_state"),
    "workflow_color": palette["solid"],
    "workflow_text_color": palette["text"],
    "workflow_bg_color": palette["bg"],
    "territory": doc.get("territory"),
    "district": doc.get("custom_district"),
    "team": doc.get("custom_installation_note_teams"),
    "transaction_date": doc.get("transaction_date"),
    "delivery_date": doc.get("delivery_date"),
    "mobile_no": doc.get("custom_mobile_no"),
    "sales_order": doc.get("sales_order"),
    "google_map": doc.get("custom_google_map"),
    "latitude": doc.get("custom_latitude"),
    "longitude": doc.get("custom_longitude"),
    "items": items,
}
