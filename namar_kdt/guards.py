"""Keep legacy batches from reserving sources held by the cutting desk."""
import frappe

from namar_kdt.migration import is_enabled


def guard_legacy_batch(doc, method=None):
    if not is_enabled():
        return
    # Share Material Request transaction locks with the cut-only desk. This additive
    # guard prevents a legacy order from allocating a piece already held by the desk.
    if (doc.get("operation_mode") or "قص") in ("قص", "تشغيل موحد"):
        request_names = sorted(set(row.material_request for row in doc.get("pieces") or [] if row.material_request))
        for request_name in request_names:
            frappe.db.sql("SELECT name FROM `tabMaterial Request` WHERE name=%s FOR UPDATE", (request_name,))
        for piece in doc.get("pieces") or []:
            if not piece.piece_key:
                continue
            held = frappe.db.sql(
                "SELECT current_order,state FROM `tabKDT Cut Piece` WHERE piece_key=%s AND state IN ('reserved','completed') FOR UPDATE",
                (piece.piece_key,), as_dict=True)
            if held:
                frappe.throw("القطعة %s موجودة في أمر القص %s. استخدم صفحة القص لتعديلها أو إعادة قصها." %
                             (piece.piece_id or piece.piece_key, held[0].current_order))
