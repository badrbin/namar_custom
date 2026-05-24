if frappe.utils.cint(doc.docstatus) == 1:
    previous_doc = doc.get_doc_before_save()
    previous_docstatus = frappe.utils.cint(previous_doc.docstatus) if previous_doc else 0

    if previous_docstatus != 1:
        request_scenario = (doc.get("custom_request_scenario") or "").strip()
        missing_fields = []

        if request_scenario != "قطاعات":
            if not (doc.get("custom_mobile_no") or "").strip():
                missing_fields.append("رقم الجوال")
            if not (doc.get("custom_google_map") or "").strip():
                missing_fields.append("قوقل ماب")

        if request_scenario in ("استبدال", "نواقص"):
            if not (doc.get("custom_scenario_reference") or "").strip():
                missing_fields.append("طلب المواد المرتبط")
            if not (doc.get("custom_request_reason") or "").strip():
                missing_fields.append("السبب")

        if missing_fields:
            field_rows = "".join(
                f"<li style='margin-bottom:4px;'>{field_label}</li>"
                for field_label in missing_fields
            )
            frappe.throw(
                title="بيانات مطلوبة قبل الاعتماد",
                msg=(
                    "<div dir='rtl' style='text-align:right'>"
                    "لا يمكن اعتماد طلب المواد قبل تعبئة البيانات التالية:"
                    f"<ul style='margin-top:8px;'>{field_rows}</ul>"
                    "</div>"
                )
            )
