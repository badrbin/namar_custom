
import frappe
from frappe.utils import flt

def execute(filters=None):
    filters = filters or {}
    customer = filters.get('customer')
    if not customer:
        return [], []

    columns = [
        {"label": "📅 التاريخ", "fieldname": "date", "fieldtype": "Date", "width": 100},
        {"label": "البيان", "fieldname": "type", "fieldtype": "Data", "width": 150},
        {"label": "الرقم", "fieldname": "ref", "fieldtype": "Dynamic Link", "options": "type", "width": 180},
        {"label": "المدين", "fieldname": "debit", "fieldtype": "Currency", "width": 120},
        {"label": "الدائن", "fieldname": "credit", "fieldtype": "Currency", "width": 120},
        {"label": "الرصيد", "fieldname": "balance", "fieldtype": "Currency", "width": 120},
    ]

    data = []
    balance = 0

    def add_row(date, type_, ref, debit, credit):
        nonlocal balance
        balance += flt(debit) - flt(credit)
        data.append({
            "date": date,
            "type": type_,
            "ref": ref,
            "debit": debit,
            "credit": credit,
            "balance": balance
        })

    for so in frappe.get_all("Sales Order", filters={"customer": customer, "docstatus": 1}, fields=["name","transaction_date","grand_total"]):
        add_row(so.transaction_date, "Sales Order", so.name, so.grand_total, 0)

    for si in frappe.get_all("Sales Invoice", filters={"customer": customer, "docstatus": 1}, fields=["name","posting_date","grand_total"]):
        add_row(si.posting_date, "Sales Invoice", si.name, si.grand_total, 0)

    for dn in frappe.get_all("Delivery Note", filters={"customer": customer, "docstatus": 1}, fields=["name","posting_date","grand_total"]):
        add_row(dn.posting_date, "Delivery Note", dn.name, dn.grand_total, 0)

    for pe in frappe.get_all("Payment Entry", filters={"party_type":"Customer","party":customer,"docstatus":1}, fields=["name","posting_date","paid_amount"]):
        add_row(pe.posting_date, "Payment Entry", pe.name, 0, pe.paid_amount)

    data.sort(key=lambda x: x["date"])
    return columns, data
