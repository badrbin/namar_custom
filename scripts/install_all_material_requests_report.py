#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.parse import quote

import requests


ROOT = Path(__file__).resolve().parents[1]
REPORT_NAME = "كل طلبات المواد"
REPORT_DOCTYPE = "Material Request"
REPORT_MODULE = "Namar Test"
REPORT_JS = (
    ROOT
    / "namar_test"
    / "namar_test"
    / "report"
    / "كل_طلبات_المواد"
    / "كل_طلبات_المواد.js"
)

REPORT_SCRIPT = r'''
filters = filters or {}

if filters.get("view_mode") == "ملخص أمر البيع":
    columns = [
        {
            "label": "ملاحظة",
            "fieldname": "message",
            "fieldtype": "Data",
            "width": 420,
        }
    ]
    result = [
        {
            "message": "ملخص أمر البيع سيعمل من ملف التطبيق بعد اكتمال Deploy الأخير لتطبيق namar_test."
        }
    ]
else:
    columns = [
        {"label": "طلب المواد", "fieldname": "material_request", "fieldtype": "Link", "options": "Material Request", "width": 160},
        {"label": "تاريخ الطلب", "fieldname": "transaction_date", "fieldtype": "Date", "width": 110},
        {"label": "تاريخ الاستحقاق", "fieldname": "schedule_date", "fieldtype": "Date", "width": 120},
        {"label": "نوع الطلب", "fieldname": "material_request_type", "fieldtype": "Data", "width": 110},
        {"label": "حالة Workflow", "fieldname": "workflow_state", "fieldtype": "Link", "options": "Workflow State", "width": 150},
        {"label": "الحالة", "fieldname": "status", "fieldtype": "Data", "width": 120},
        {"label": "الشركة", "fieldname": "company", "fieldtype": "Link", "options": "Company", "width": 140},
        {"label": "أمر البيع", "fieldname": "sales_order", "fieldtype": "Link", "options": "Sales Order", "width": 150},
        {"label": "العميل", "fieldname": "customer", "fieldtype": "Link", "options": "Customer", "width": 150},
        {"label": "اسم العميل", "fieldname": "customer_display", "fieldtype": "Data", "width": 190},
        {"label": "الفرع", "fieldname": "branch", "fieldtype": "Link", "options": "Branch", "width": 110},
        {"label": "VIP", "fieldname": "customer_vip", "fieldtype": "Data", "width": 70},
        {"label": "مستعجل", "fieldname": "is_urgent", "fieldtype": "Data", "width": 80},
        {"label": "سيناريو الطلب", "fieldname": "request_scenario", "fieldtype": "Data", "width": 120},
        {"label": "حالة التصنيع", "fieldname": "manufacturing_status", "fieldtype": "Data", "width": 130},
        {"label": "حالة تصنيع المكونات", "fieldname": "component_manufacturing_status", "fieldtype": "Data", "width": 150},
        {"label": "جاهزية التوريد", "fieldname": "delivery_readiness_status", "fieldtype": "Data", "width": 130},
        {"label": "مدة الحالة", "fieldname": "workflow_state_duration", "fieldtype": "Duration", "width": 110},
        {"label": "متبقي التصنيع", "fieldname": "manufacturing_remaining_count", "fieldtype": "Float", "width": 120},
        {"label": "متبقي المكونات", "fieldname": "component_manufacturing_remaining_count", "fieldtype": "Float", "width": 130},
        {"label": "عدد الأصناف", "fieldname": "item_count", "fieldtype": "Int", "width": 90},
        {"label": "إجمالي الكمية", "fieldname": "total_qty", "fieldtype": "Float", "width": 120},
    ]

    conditions = []
    params = {}
    include_cancelled = 0
    try:
        include_cancelled = int(filters.get("include_cancelled") or 0)
    except Exception:
        include_cancelled = 0
    if not include_cancelled:
        conditions.append("mr.docstatus < 2")
    if filters.get("from_date"):
        conditions.append("mr.transaction_date >= %(from_date)s")
        params["from_date"] = filters.get("from_date")
    if filters.get("to_date"):
        conditions.append("mr.transaction_date <= %(to_date)s")
        params["to_date"] = filters.get("to_date")
    if filters.get("material_request"):
        conditions.append("mr.name = %(material_request)s")
        params["material_request"] = filters.get("material_request")

    exact_fields = [
        ["company", "company"],
        ["sales_order", "sales_order"],
        ["customer", "customer"],
        ["workflow_state", "workflow_state"],
        ["branch", "الفرع"],
        ["request_scenario", "custom_request_scenario"],
        ["manufacturing_status", "custom_manufacturing_status"],
        ["delivery_readiness_status", "custom_delivery_readiness_status"],
    ]
    for item in exact_fields:
        filter_name = item[0]
        fieldname = item[1]
        value = filters.get(filter_name)
        if value:
            quoted = "`" + fieldname.replace("`", "``") + "`"
            conditions.append("mr." + quoted + " = %(" + filter_name + ")s")
            params[filter_name] = value

    if filters.get("customer_vip"):
        conditions.append("mr.custom_customer_vip = 1")

    if filters.get("item_code"):
        conditions.append(
            """
            EXISTS (
                SELECT 1
                FROM `tabMaterial Request Item` item_filter
                WHERE item_filter.parent = mr.name
                AND item_filter.item_code = %(item_code)s
            )
            """
        )
        params["item_code"] = filters.get("item_code")

    limit = 500
    try:
        limit = int(filters.get("limit") or 500)
    except Exception:
        limit = 500
    if limit < 1:
        limit = 500
    if limit > 2000:
        limit = 2000
    params["limit"] = limit

    select_parts = [
        "mr.name AS material_request",
        "mr.transaction_date AS transaction_date",
        "mr.schedule_date AS schedule_date",
        "mr.material_request_type AS material_request_type",
        "mr.workflow_state AS workflow_state",
        "mr.status AS status",
        "mr.company AS company",
        "mr.sales_order AS sales_order",
        "mr.customer AS customer",
        "mr.customer_name AS customer_name",
        "mr.`الفرع` AS branch",
        "mr.custom_customer_vip AS customer_vip",
        "mr.custom_is_urgent AS is_urgent",
        "mr.custom_request_scenario AS request_scenario",
        "mr.custom_manufacturing_status AS manufacturing_status",
        "mr.custom_component_manufacturing_status AS component_manufacturing_status",
        "mr.custom_delivery_readiness_status AS delivery_readiness_status",
        "mr.custom_workflow_state_duration AS workflow_state_duration",
        "mr.custom_manufacturing_remaining_count AS manufacturing_remaining_count",
        "mr.custom_component_manufacturing_remaining_count AS component_manufacturing_remaining_count",
        "mr.docstatus AS docstatus",
        "IFNULL(item_totals.total_qty, 0) AS total_qty",
        "IFNULL(item_totals.item_count, 0) AS item_count",
    ]

    where_sql = " AND ".join(conditions) if conditions else "1 = 1"
    query = """
        SELECT
            """ + ",\n            ".join(select_parts) + """
        FROM `tabMaterial Request` mr
        LEFT JOIN (
            SELECT parent, SUM(qty) AS total_qty, COUNT(*) AS item_count
            FROM `tabMaterial Request Item`
            GROUP BY parent
        ) item_totals ON item_totals.parent = mr.name
        WHERE """ + where_sql + """
        ORDER BY mr.modified DESC
        LIMIT %(limit)s
    """

    result = frappe.db.sql(query, params, as_dict=True)
    for row in result:
        row["customer_display"] = row.get("customer_name") or row.get("customer")
        customer_vip = 0
        is_urgent = 0
        try:
            customer_vip = int(row.get("customer_vip") or 0)
        except Exception:
            customer_vip = 0
        try:
            is_urgent = int(row.get("is_urgent") or 0)
        except Exception:
            is_urgent = 0
        row["customer_vip"] = "نعم" if customer_vip else ""
        row["is_urgent"] = "نعم" if is_urgent else ""

data = columns, result
'''


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def first_env(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def site_url(value: str) -> str:
    text = value.rstrip("/")
    if text.startswith(("http://", "https://")):
        return text
    return "https://" + text


class Client:
    def __init__(self) -> None:
        load_env(ROOT.parent / "erpnex_codex" / ".env.local")
        site = first_env("FRAPPE_TEST_SITE", "FRAPPE_SITE")
        token = first_env("FRAPPE_TEST_TOKEN", "FRAPPE_TOKEN")
        if not site or not token:
            raise SystemExit("Missing Frappe test credentials.")
        self.base = site_url(site)
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": token if token.startswith("token ") else "token " + token,
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )

    def get_report(self) -> dict | None:
        response = self.session.get(
            f"{self.base}/api/resource/Report/{quote(REPORT_NAME, safe='')}",
            timeout=90,
        )
        if response.status_code == 404:
            return None
        if not response.ok:
            raise RuntimeError(f"GET Report failed: {response.status_code} {response.text}")
        return response.json().get("data") or {}

    def upsert_report(self, doc: dict) -> dict:
        existing = self.get_report()
        if existing:
            response = self.session.put(
                f"{self.base}/api/resource/Report/{quote(REPORT_NAME, safe='')}",
                json=doc,
                timeout=120,
            )
        else:
            response = self.session.post(f"{self.base}/api/resource/Report", json=doc, timeout=120)
        if not response.ok:
            raise RuntimeError(f"UPSERT Report failed: {response.status_code} {response.text}")
        return response.json().get("data") or {}


def build_report_doc() -> dict:
    javascript = REPORT_JS.read_text(encoding="utf-8")
    return {
        "doctype": "Report",
        "name": REPORT_NAME,
        "report_name": REPORT_NAME,
        "ref_doctype": REPORT_DOCTYPE,
        "report_type": "Script Report",
        "is_standard": "No",
        "module": REPORT_MODULE,
        "add_total_row": 0,
        "disabled": 0,
        "prepared_report": 0,
        "report_script": REPORT_SCRIPT.strip() + "\n",
        "javascript": javascript,
        "roles": [
            {"role": "System Manager"},
            {"role": "Stock User"},
            {"role": "Stock Manager"},
            {"role": "Sales User"},
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Install the all-material-requests Script Report on test.")
    parser.add_argument("--yes", action="store_true", help="Confirm the live test-site write.")
    args = parser.parse_args()
    if not args.yes:
        raise SystemExit("Pass --yes to update the test site Report record.")

    client = Client()
    doc = client.upsert_report(build_report_doc())
    print(json.dumps({"name": doc.get("name"), "is_standard": doc.get("is_standard")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
