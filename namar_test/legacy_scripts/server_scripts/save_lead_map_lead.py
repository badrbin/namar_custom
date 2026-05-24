# API: save_lead_map_lead

if frappe.session.user == "Guest":
    frappe.throw("يلزم تسجيل الدخول لحفظ الليد", frappe.PermissionError)


def clean_text(value):
    if value is None:
        return ""
    return str(value).strip()


def normalize_mobile(value):
    value = clean_text(value)
    if not value:
        return ""
    digits = "".join(ch for ch in value if ch.isdigit())
    if digits.startswith("966") and len(digits) == 12:
        digits = "0" + digits[3:]
    elif len(digits) == 9:
        digits = "0" + digits
    if digits and len(digits) < 9:
        frappe.throw("رقم الجوال غير صالح")
    return digits


def as_float(value, label):
    value = clean_text(value)
    if not value:
        return None
    try:
        return float(value)
    except Exception:
        frappe.throw(label + " غير صالح")


lead_meta = frappe.get_meta("Lead")
has_latitude = bool(lead_meta.get_field("custom_latitude"))
has_longitude = bool(lead_meta.get_field("custom_longitude"))
has_map_notes = bool(lead_meta.get_field("custom_map_notes"))
has_follow_up = bool(lead_meta.get_field("custom_next_follow_up_on"))

lead_name = clean_text(frappe.form_dict.get("lead_name"))
docname = clean_text(frappe.form_dict.get("name"))
mobile_no = normalize_mobile(frappe.form_dict.get("mobile_no") or frappe.form_dict.get("phone"))
email_id = clean_text(frappe.form_dict.get("email_id"))
city = clean_text(frappe.form_dict.get("city"))
status = clean_text(frappe.form_dict.get("status"))
source = clean_text(frappe.form_dict.get("source"))
map_notes = clean_text(frappe.form_dict.get("custom_map_notes"))
follow_up_on = clean_text(frappe.form_dict.get("custom_next_follow_up_on"))
latitude = as_float(frappe.form_dict.get("lat") or frappe.form_dict.get("custom_latitude"), "خط العرض")
longitude = as_float(frappe.form_dict.get("lng") or frappe.form_dict.get("custom_longitude"), "خط الطول")

if latitude is not None and not (-90 <= latitude <= 90):
    frappe.throw("خط العرض خارج النطاق المسموح")
if longitude is not None and not (-180 <= longitude <= 180):
    frappe.throw("خط الطول خارج النطاق المسموح")

doc = None
if docname:
    doc = frappe.get_doc("Lead", docname)
elif mobile_no:
    existing_name = frappe.db.get_value("Lead", {"mobile_no": mobile_no}, "name")
    if not existing_name:
        existing_name = frappe.db.get_value("Lead", {"phone": mobile_no}, "name")
    if existing_name:
        doc = frappe.get_doc("Lead", existing_name)

if not doc:
    doc = frappe.get_doc({"doctype": "Lead"})

if lead_name:
    doc.lead_name = lead_name

if not clean_text(doc.get("lead_name")):
    frappe.throw("اسم الليد مطلوب")

if mobile_no:
    doc.mobile_no = mobile_no
    if not clean_text(doc.get("phone")):
        doc.phone = mobile_no

if email_id or email_id == "":
    doc.email_id = email_id

if city or city == "":
    doc.city = city

status_field = lead_meta.get_field("status")
status_options = []
if status_field and status_field.options:
    status_options = [clean_text(option) for option in status_field.options.split("\n") if clean_text(option)]

if status:
    doc.status = status
elif not clean_text(doc.get("status")) and status_options:
    doc.status = status_options[0]
elif not clean_text(doc.get("status")):
    doc.status = "Lead"

if source and frappe.db.exists("DocType", "Lead Source") and not frappe.db.exists("Lead Source", source):
    frappe.throw("مصدر الليد غير موجود")
doc.source = source

if follow_up_on and has_follow_up:
    frappe.utils.getdate(follow_up_on)

current_lat = float(doc.get("custom_latitude") or 0) if has_latitude else 0
current_lng = float(doc.get("custom_longitude") or 0) if has_longitude else 0
target_lat = latitude if latitude is not None else current_lat
target_lng = longitude if longitude is not None else current_lng
if not target_lat or not target_lng:
    frappe.throw("حدد موقع الليد على الخريطة أولًا")

if has_latitude:
    doc.custom_latitude = target_lat
if has_longitude:
    doc.custom_longitude = target_lng
if has_map_notes:
    doc.custom_map_notes = map_notes
if has_follow_up:
    doc.custom_next_follow_up_on = follow_up_on

is_new = doc.is_new()
if is_new:
    doc.insert()
else:
    doc.save()

frappe.db.commit()

frappe.response["message"] = {
    "ok": True,
    "name": doc.name,
    "is_new": is_new,
    "lead": {
        "name": doc.name,
        "lead_name": doc.lead_name,
        "status": doc.status,
        "mobile_no": doc.mobile_no,
        "phone": doc.phone,
        "city": doc.city,
        "source": doc.source,
        "email_id": doc.email_id,
        "custom_latitude": doc.get("custom_latitude"),
        "custom_longitude": doc.get("custom_longitude"),
        "custom_map_notes": doc.get("custom_map_notes"),
        "custom_next_follow_up_on": doc.get("custom_next_follow_up_on"),
    },
}
