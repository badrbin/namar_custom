GROUP_DEFS = {
    "all": {"label": "الكل", "style": "All"},
    "danger": {"label": "عاجل", "style": "Danger"},
    "warning": {"label": "متابعة", "style": "Warning"},
    "success": {"label": "مكتمل", "style": "Success"},
    "info": {"label": "مجدول", "style": "Info"},
    "primary": {"label": "جديد", "style": "Primary"},
    "neutral": {"label": "أخرى", "style": "Inverse"},
}

STYLE_TO_GROUP = {
    "Danger": "danger",
    "Warning": "warning",
    "Success": "success",
    "Info": "info",
    "Primary": "primary",
    "Inverse": "neutral",
}

STYLE_COLORS = {
    "All": {
        "solid": "#264653",
        "text": "#264653",
        "bg": "#EEF3F4",
        "soft": "#EEF3F4",
    },
    "Danger": {
        "solid": "#B52A2A",
        "text": "#B52A2A",
        "bg": "#FFF0F0",
        "soft": "#FFF0F0",
    },
    "Warning": {
        "solid": "#BD3E0C",
        "text": "#BD3E0C",
        "bg": "#FFF1E7",
        "soft": "#FFF1E7",
    },
    "Success": {
        "solid": "#16794C",
        "text": "#16794C",
        "bg": "#E4F5E9",
        "soft": "#E4F5E9",
    },
    "Info": {
        "solid": "#0070CC",
        "text": "#0070CC",
        "bg": "#EDF6FD",
        "soft": "#EDF6FD",
    },
    "Primary": {
        "solid": "#005BD3",
        "text": "#005BD3",
        "bg": "#EAF5FE",
        "soft": "#EAF5FE",
    },
    "Inverse": {
        "solid": "#525252",
        "text": "#525252",
        "bg": "#F3F3F3",
        "soft": "#F3F3F3",
    },
}

MIN_LAT = 15
MAX_LAT = 33
MIN_LNG = 34
MAX_LNG = 57


def to_float(value):
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def normalize_text(value):
    return (value or "").strip()


def palette_for_style(style_name):
    return STYLE_COLORS.get(normalize_text(style_name) or "Inverse", STYLE_COLORS["Inverse"])


def palette_for_group(group_key):
    group = GROUP_DEFS.get(group_key) or GROUP_DEFS["neutral"]
    return palette_for_style(group.get("style"))


def search_blob(order):
    parts = [
        order.get("name"),
        order.get("short_name"),
        order.get("customer_name"),
        order.get("territory"),
        order.get("district"),
        order.get("team"),
        order.get("mobile_no"),
        order.get("sales_order"),
    ]
    return " ".join([p for p in parts if p]).lower()


requested_group = normalize_text(frappe.form_dict.get("group_key")) or "auto"
selected_city = normalize_text(frappe.form_dict.get("territory"))
search_term = normalize_text(frappe.form_dict.get("search")).lower()

workflow_rows = frappe.get_all(
    "Workflow State",
    fields=["name", "style"],
    order_by="idx asc",
    limit_page_length=0,
)

status_to_group = {}
status_to_palette = {}
for row in workflow_rows:
    style = normalize_text(row.get("style"))
    group_key = STYLE_TO_GROUP.get(style, "neutral")
    status_to_group[row.get("name")] = group_key
    status_to_palette[row.get("name")] = palette_for_style(style)

order_rows = frappe.get_all(
    "Material Request",
    filters={"docstatus": ["in", [0, 1]]},
    fields=[
        "name",
        "customer_name",
        "territory",
        "custom_district",
        "custom_installation_note_teams",
        "custom_mobile_no",
        "delivery_date",
        "workflow_state",
        "custom_google_map",
        "custom_latitude",
        "custom_longitude",
        "sales_order",
        "transaction_date",
    ],
    order_by="transaction_date desc",
    limit_page_length=0,
)

orders = []
group_totals = {}
for row in order_rows:
    workflow_state = normalize_text(row.get("workflow_state"))
    group_key = status_to_group.get(workflow_state, "neutral")
    workflow_palette = status_to_palette.get(workflow_state, palette_for_group(group_key))
    group_palette = palette_for_group(group_key)
    lat = to_float(row.get("custom_latitude"))
    lng = to_float(row.get("custom_longitude"))
    has_map = MIN_LAT <= lat <= MAX_LAT and MIN_LNG <= lng <= MAX_LNG

    order = {
        "name": normalize_text(row.get("name")),
        "short_name": normalize_text(row.get("name")).replace("MREQ-", ""),
        "customer_name": normalize_text(row.get("customer_name")),
        "territory": normalize_text(row.get("territory")),
        "district": normalize_text(row.get("custom_district")),
        "team": normalize_text(row.get("custom_installation_note_teams")),
        "mobile_no": normalize_text(row.get("custom_mobile_no")),
        "delivery_date": row.get("delivery_date"),
        "transaction_date": row.get("transaction_date"),
        "workflow_state": workflow_state,
        "workflow_color": workflow_palette["solid"],
        "workflow_text_color": workflow_palette["text"],
        "workflow_bg_color": workflow_palette["bg"],
        "group_key": group_key,
        "group_label": GROUP_DEFS[group_key]["label"],
        "group_color": group_palette["solid"],
        "group_text_color": group_palette["text"],
        "group_bg_color": group_palette["bg"],
        "google_map": normalize_text(row.get("custom_google_map")),
        "latitude": lat,
        "longitude": lng,
        "has_map": has_map,
        "sales_order": normalize_text(row.get("sales_order")),
    }
    orders.append(order)
    group_totals[group_key] = group_totals.get(group_key, 0) + 1

default_group = "danger" if group_totals.get("danger") else "all"
if requested_group == "auto":
    selected_group = default_group
elif requested_group in GROUP_DEFS:
    selected_group = requested_group
else:
    selected_group = "all"

search_filtered = orders
if search_term:
    search_filtered = [order for order in orders if search_term in search_blob(order)]

group_count_source = search_filtered
if selected_city:
    group_count_source = [order for order in group_count_source if order.get("territory") == selected_city]

city_count_source = search_filtered
if selected_group != "all":
    city_count_source = [order for order in city_count_source if order.get("group_key") == selected_group]

final_orders = search_filtered
if selected_group != "all":
    final_orders = [order for order in final_orders if order.get("group_key") == selected_group]
if selected_city:
    final_orders = [order for order in final_orders if order.get("territory") == selected_city]

group_counts = {"all": len(group_count_source)}
for order in group_count_source:
    key = order.get("group_key") or "neutral"
    group_counts[key] = group_counts.get(key, 0) + 1

groups = []
for key in ["all", "danger", "warning", "success", "info", "primary", "neutral"]:
    palette = palette_for_group(key)
    groups.append(
        {
            "key": key,
            "label": GROUP_DEFS[key]["label"],
            "color": palette["solid"],
            "text_color": palette["text"],
            "bg_color": palette["bg"],
            "count": group_counts.get(key, 0),
            "active": key == selected_group,
        }
    )

city_counts = {}
for order in city_count_source:
    city = order.get("territory")
    if not city:
        continue
    city_counts[city] = city_counts.get(city, 0) + 1

cities = [
    {
        "name": city,
        "count": city_counts[city],
        "active": city == selected_city,
    }
    for city in sorted(city_counts.keys())
]

mapped_orders = len([order for order in final_orders if order.get("has_map")])

frappe.response["message"] = {
    "orders": final_orders,
    "filters": {
        "selected_group": selected_group,
        "selected_city": selected_city,
        "search": search_term,
        "groups": groups,
        "cities": cities,
        "default_group": default_group,
    },
    "summary": {
        "total_orders": len(final_orders),
        "mapped_orders": mapped_orders,
        "unmapped_orders": len(final_orders) - mapped_orders,
        "overall_orders": len(orders),
    },
}
