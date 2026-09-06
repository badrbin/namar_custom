from __future__ import annotations

import frappe
from frappe.model.document import Document

from namar_test.ai_readonly.policy import DEFAULT_ROLE, Denied, parse_json, validate_policy


class AIReadOnlySettings(Document):
    def validate(self):
        frappe.only_for("System Manager")
        if self.protected_role in ("All", "Guest", "Desk User", "System Manager"):
            frappe.throw("اختر دور الحساب المستقل؛ لا تستخدم دورًا عامًا للنظام.")
        self.protected_role = self.protected_role or DEFAULT_ROLE
        try:
            validate_policy(parse_json(self.policy_json))
        except (Denied, TypeError, ValueError):
            frappe.throw("سياسة القراءة غير صالحة؛ راجع الطرق وبصمات المصادر وإصدارات التطبيقات.")

    def on_update(self):
        frappe.clear_document_cache(self.doctype, self.name)
