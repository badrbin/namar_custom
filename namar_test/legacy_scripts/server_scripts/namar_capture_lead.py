# Server Script (API)
# API Method: namar_capture_lead

def clean_field(*keys):
    for key in keys:
        value = (frappe.form_dict.get(key) or "").strip()
        if value:
            return value
    return ""


lead_name = clean_field("your-name", "lead_name", "name")
mobile_no = clean_field("your-phone", "mobile_no", "phone")
city = clean_field("your-city", "city")
doors_count = clean_field("number-of-doors", "doors_count")
form_kind = clean_field("form_kind")
page_url = clean_field("page_url")
page_title = clean_field("page_title")
referrer = clean_field("referrer")
website_host = clean_field("website_host")
utm_source = clean_field("utm_source")
campaign = clean_field("campaign")

if not lead_name:
    frappe.throw("الاسم مطلوب")

if not mobile_no:
    frappe.throw("رقم الجوال مطلوب")

normalized_mobile = "".join(ch for ch in mobile_no if ch.isdigit())

if normalized_mobile.startswith("966") and len(normalized_mobile) == 12:
    normalized_mobile = "0" + normalized_mobile[3:]
elif len(normalized_mobile) == 9:
    normalized_mobile = "0" + normalized_mobile

if len(normalized_mobile) < 10:
    frappe.throw("رقم الجوال غير صالح")

lead_source = None
for candidate in ("Website", "Namar Website", "الموقع الإلكتروني"):
    if frappe.db.exists("Lead Source", candidate):
        lead_source = candidate
        break

existing_lead_name = frappe.db.get_value("Lead", {"mobile_no": normalized_mobile}, "name")
if not existing_lead_name:
    existing_lead_name = frappe.db.get_value("Lead", {"phone": normalized_mobile}, "name")

is_new = not bool(existing_lead_name)

if existing_lead_name:
    lead = frappe.get_doc("Lead", existing_lead_name)
else:
    lead_data = {
        "doctype": "Lead",
        "lead_name": lead_name,
        "mobile_no": normalized_mobile,
        "phone": normalized_mobile,
        "request_type": "Product Enquiry",
    }
    if lead_source:
        lead_data["source"] = lead_source
    lead = frappe.get_doc(lead_data).insert(ignore_permissions=True)

details = [
    "طلب جديد من موقع نمار",
    "",
    "الاسم: " + lead_name,
    "رقم الجوال: " + normalized_mobile,
]

if city:
    details.append("المدينة: " + city)

if doors_count:
    details.append("عدد الأبواب: " + doors_count)

if form_kind:
    details.append("نوع النموذج: " + form_kind)

if page_title:
    details.append("عنوان الصفحة: " + page_title)

if page_url:
    details.append("رابط الصفحة: " + page_url)

if referrer:
    details.append("المُحيل: " + referrer)

if website_host:
    details.append("الدومين: " + website_host)

if utm_source:
    details.append("UTM Source: " + utm_source)

if campaign:
    details.append("Campaign: " + campaign)

origin = ""
try:
    origin = (frappe.request.headers.get("Origin") or "").strip()
except Exception:
    origin = ""

if origin:
    details.append("Origin: " + origin)

details.append("وقت الإرسال: " + str(frappe.utils.now_datetime()))

frappe.get_doc(
    {
        "doctype": "Comment",
        "comment_type": "Comment",
        "reference_doctype": "Lead",
        "reference_name": lead.name,
        "content": "\n".join(details),
    }
).insert(ignore_permissions=True)

frappe.response["message"] = {
    "ok": True,
    "lead": lead.name,
    "is_new": is_new,
}
