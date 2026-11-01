"""Keep legacy batches from reserving sources held by the cutting desk."""
import frappe

from namar_kdt.migration import is_enabled


def guard_legacy_batch(doc, method=None):
    if not is_enabled():
        return
    # Share Material Request transaction locks with the cut-only desk. This additive
    # guard prevents legacy submission for manufactured sources or pieces held by the desk.
    if (doc.get("operation_mode") or "قص") in ("قص", "تشغيل موحد"):
        request_names = sorted(set(row.material_request for row in doc.get("pieces") or [] if row.material_request))
        for request_name in request_names:
            frappe.db.sql("SELECT name FROM `tabMaterial Request` WHERE name=%s FOR UPDATE", (request_name,))
        source_meta = frappe.get_meta("Material Request Item")
        source_fields = ["name", "parent", "qty"]
        for fieldname in ("custom_manufactured_qty", "custom_is_manufactured"):
            if source_meta.has_field(fieldname):
                source_fields.append(fieldname)
        source_columns = ", ".join("`" + fieldname + "`" for fieldname in source_fields)
        checked_sources = {}
        for piece in doc.get("pieces") or []:
            source_name = piece.get("material_request_item") or ""
            request_name = piece.get("material_request") or ""
            if not source_name or not request_name:
                frappe.throw("مصدر القطعة غير محدد؛ حدّث بيانات أمر القص قبل الاعتماد.")
            source_key = request_name + "|" + source_name
            if source_key not in checked_sources:
                sources = frappe.db.sql(
                    "SELECT " + source_columns + " FROM `tabMaterial Request Item` WHERE name=%s AND parent=%s FOR UPDATE",
                    (source_name, request_name), as_dict=True)
                if not sources:
                    frappe.throw("مصدر القطعة لم يعد موجودًا في طلب المواد؛ حدّث بيانات أمر القص قبل الاعتماد.")
                source = sources[0]
                required = frappe.utils.flt(source.get("qty") or 0)
                if required <= 0:
                    required = 1
                manufactured = frappe.utils.flt(source.get("custom_manufactured_qty") or 0)
                if manufactured <= 0 and frappe.utils.cint(source.get("custom_is_manufactured") or 0):
                    manufactured = required
                manufactured = min(max(manufactured, 0), required)
                if manufactured > 0:
                    frappe.throw("سبق تصنيع %g من %g للمصدر %s في طلب المواد %s — المتبقي %g؛ لا يمكن تخصيصه لقص جديد." %
                                 (manufactured, required, source_name, request_name, max(required - manufactured, 0)))
                checked_sources[source_key] = 1
            if not piece.piece_key:
                continue
            held = frappe.db.sql(
                "SELECT current_order,state FROM `tabKDT Cut Piece` WHERE piece_key=%s AND state IN ('reserved','completed') FOR UPDATE",
                (piece.piece_key,), as_dict=True)
            if held:
                frappe.throw("القطعة %s موجودة في أمر القص %s. استخدم صفحة القص لتعديلها أو إعادة قصها." %
                             (piece.piece_id or piece.piece_key, held[0].current_order))
