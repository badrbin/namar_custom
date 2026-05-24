if doc.party_type == "Supplier":
    frappe.throw("عذراً، لا يُسمح باستخدام الموردين (Suppliers) في شاشة سند الدفع نهائياً، يرجى استخدام قيد اليومية.")
