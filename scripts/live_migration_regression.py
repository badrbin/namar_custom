#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT.parent / "erpnex_codex" / ".env.local"
SERVER_MANIFEST = ROOT / "namar_test" / "legacy_scripts" / "server_scripts_manifest.json"
CLIENT_MANIFEST = ROOT / "namar_test" / "legacy_scripts" / "client_scripts_manifest.json"


READ_ONLY_METHODS = [
    "namar_test.api.get_workflow_transitions",
    "namar_test.api.get_cutting_values_bulk",
    "namar_test.api.get_mr_full_data",
    "namar_test.api.get_sales_dashboard",
    "namar_test.api.get_purchase_dashboard",
    "namar_test.api.get_customer_summary",
    "namar_test.api.get_supplier_summary",
    "namar_test.api.get_related_items",
]

REPORT_FILTERS = [
    {"view_mode": "طلبات المواد", "limit": 1},
    {"view_mode": "ملخص أمر البيع", "limit": 1},
    {"view_mode": "نتائج التخصيم", "material_request": "MREQ-08104", "limit": 1},
    {"view_mode": "التصنيع اليومي", "limit": 1},
    {"view_mode": "متابعة التصنيع", "limit": 1},
    {"view_mode": "تفاصيل المخازن", "material_request": "MREQ-08104", "limit": 1},
    {"view_mode": "حالات تشغيلية", "operation_preset": "جاري التصنيع", "limit": 1},
]


class FrappeError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def load_env(path: Path = ENV_PATH) -> None:
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
    def __init__(self, env: str) -> None:
        load_env()
        if env != "test":
            raise SystemExit("هذه الاختبارات الحية مخصصة للتجريبي فقط.")
        site = first_env("FRAPPE_TEST_SITE", "FRAPPE_SITE")
        token = first_env("FRAPPE_TEST_TOKEN", "FRAPPE_TOKEN")
        if not site or not token:
            raise SystemExit(f"Missing Frappe test credentials in {ENV_PATH}")
        self.base = site_url(site)
        self.verify = os.environ.get("FRAPPE_VERIFY_SSL", "1").lower() not in {"0", "false", "no"}
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": token if token.startswith("token ") else "token " + token,
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        response = self.session.request(
            method,
            self.base + path,
            json=payload,
            timeout=kwargs.pop("timeout", 120),
            verify=self.verify,
            **kwargs,
        )
        if not response.ok:
            error_text = response.text
            try:
                error_text = json.dumps(response.json(), ensure_ascii=False)
            except Exception:
                pass
            raise FrappeError(f"{method} {path} failed: {response.status_code} {error_text}", response.status_code)
        if not response.text:
            return {}
        return response.json()

    def get_doc(self, doctype: str, name: str) -> dict[str, Any] | None:
        response = self.session.get(
            f"{self.base}/api/resource/{quote(doctype, safe='')}/{quote(name, safe='')}",
            timeout=120,
            verify=self.verify,
        )
        if response.status_code == 404:
            return None
        if not response.ok:
            raise FrappeError(f"GET {doctype} {name} failed: {response.status_code} {response.text}", response.status_code)
        return response.json().get("data") or {}

    def list_docs(
        self,
        doctype: str,
        fields: list[str],
        filters: list[Any] | dict[str, Any] | None = None,
        limit: int = 100,
        order_by: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "fields": json.dumps(fields, ensure_ascii=False),
            "limit_page_length": limit,
        }
        if filters is not None:
            params["filters"] = json.dumps(filters, ensure_ascii=False)
        if order_by:
            params["order_by"] = order_by
        return self.request(
            "GET",
            f"/api/resource/{quote(doctype, safe='')}",
            params=params,
        ).get("data") or []

    def insert_doc(self, doctype: str, payload: dict[str, Any]) -> dict[str, Any]:
        doc = dict(payload)
        doc["doctype"] = doctype
        return self.request("POST", f"/api/resource/{quote(doctype, safe='')}", doc).get("data") or {}

    def delete_doc(self, doctype: str, name: str) -> None:
        response = self.session.delete(
            f"{self.base}/api/resource/{quote(doctype, safe='')}/{quote(name, safe='')}",
            timeout=120,
            verify=self.verify,
        )
        if response.status_code == 404:
            return
        if not response.ok:
            raise FrappeError(f"DELETE {doctype} {name} failed: {response.status_code} {response.text}", response.status_code)

    def call_method(self, method: str, payload: dict[str, Any] | None = None) -> Any:
        return self.request("POST", f"/api/method/{quote(method, safe='.')}", payload or {}).get("message")

    def asset_text(self, path: str) -> str:
        response = self.session.get(self.base + path, timeout=120, verify=self.verify)
        if not response.ok:
            raise FrappeError(f"GET asset {path} failed: {response.status_code}", response.status_code)
        return response.text


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def managed_legacy_items() -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for entry in json.loads(SERVER_MANIFEST.read_text(encoding="utf-8")):
        items.append(("Server Script", entry["name"]))
    for entry in json.loads(CLIENT_MANIFEST.read_text(encoding="utf-8")):
        items.append(("Client Script", entry["name"]))
    return items


def check_versions(client: Client) -> dict[str, Any]:
    versions = client.request("GET", "/api/method/frappe.utils.change_log.get_versions").get("message") or {}
    app = versions.get("namar_test") or {}
    require(bool(app), "namar_test app is not visible in installed versions")
    return {"namar_test": app, "frappe": versions.get("frappe"), "erpnext": versions.get("erpnext")}


def check_legacy_scripts_removed(client: Client) -> dict[str, Any]:
    managed = managed_legacy_items()
    enabled: list[tuple[str, str]] = []
    disabled: list[tuple[str, str]] = []
    missing: list[tuple[str, str]] = []
    for doctype, name in managed:
        doc = client.get_doc(doctype, name)
        if doc is None:
            missing.append((doctype, name))
            continue
        if doctype == "Server Script":
            active = int(doc.get("disabled") or 0) == 0
        else:
            active = int(doc.get("enabled") or 0) == 1
        (enabled if active else disabled).append((doctype, name))

    require(not enabled and not disabled and len(missing) == len(managed), "managed legacy scripts are not fully deleted")

    managed_set = set(managed)
    unmanaged: list[tuple[str, str]] = []
    for row in client.list_docs("Server Script", ["name", "disabled"], limit=500):
        name = row.get("name")
        if name and ("Server Script", name) not in managed_set and int(row.get("disabled") or 0) == 0:
            unmanaged.append(("Server Script", name))
    for row in client.list_docs("Client Script", ["name", "enabled"], limit=500):
        name = row.get("name")
        if name and ("Client Script", name) not in managed_set and int(row.get("enabled") or 0) == 1:
            unmanaged.append(("Client Script", name))
    require(not unmanaged, "unmanaged active Server Script or Client Script records still exist")
    return {"managed_total": len(managed), "missing": len(missing), "unmanaged_active": 0}


def check_assets(client: Client) -> dict[str, Any]:
    material_request_js = client.asset_text("/assets/namar_test/js/doctype/material_request_form.js")
    delivery_js = client.asset_text("/assets/namar_test/js/delivery_components/material_request_delivery_components.js")
    require("window.__namar_test_loaded_scripts" in material_request_js, "doctype JS asset does not contain migration guard")
    require("حساب التخصيم" in material_request_js, "Material Request migrated client script block is missing")
    require("delivery-component-sync-btn" in delivery_js, "delivery component app asset is missing dashboard buttons")
    require("data-delivery-tracking-source" in delivery_js, "delivery component app marker is missing")
    return {
        "material_request_form_js_bytes": len(material_request_js),
        "delivery_component_js_bytes": len(delivery_js),
    }


def check_read_only_methods(client: Client) -> list[dict[str, Any]]:
    results = []
    for method in READ_ONLY_METHODS:
        message = client.call_method(method)
        results.append({"method": method, "type": type(message).__name__})
    return results


def check_reports(client: Client) -> list[dict[str, Any]]:
    results = []
    for filters in REPORT_FILTERS:
        message = client.call_method(
            "frappe.desk.query_report.run",
            {
                "report_name": "كل طلبات المواد",
                "filters": json.dumps(filters, ensure_ascii=False),
                "ignore_prepared_report": 1,
            },
        )
        require(isinstance(message, dict) and "result" in message, f"unexpected report response for {filters}")
        results.append(
            {
                "view_mode": filters.get("view_mode"),
                "rows": len(message.get("result") or []),
            }
        )
    return results


def first_name(client: Client, doctype: str, filters: list[Any] | dict[str, Any] | None = None) -> str:
    rows = client.list_docs(doctype, ["name"], filters=filters, limit=1)
    return (rows[0].get("name") if rows else "") or ""


def expect_frappe_error_contains(action, expected: str) -> str:
    try:
        action()
    except FrappeError as exc:
        text = str(exc)
        require(expected in text, f"expected error containing {expected!r}, got: {text[:300]}")
        return text[:300]
    raise AssertionError(f"expected Frappe error containing {expected!r}")


def customer_mobile_validation_flow(client: Client, stamp: str) -> dict[str, Any]:
    customer_group = first_name(client, "Customer Group", [["is_group", "=", 0]]) or first_name(client, "Customer Group")
    territory = first_name(client, "Territory", [["is_group", "=", 0]]) or first_name(client, "Territory")
    require(customer_group and territory, "Customer Group and Territory are required for customer test")
    base_payload = {
        "customer_name": f"Codex Test Customer {stamp}",
        "customer_type": "Individual",
        "customer_group": customer_group,
        "territory": territory,
    }
    expected_error = expect_frappe_error_contains(lambda: client.insert_doc("Customer", base_payload), "رقم الجوال")
    created = client.insert_doc("Customer", {**base_payload, "mobile_no": "0500000000"})
    client.delete_doc("Customer", created["name"])
    return {"blocked_without_mobile": True, "created_then_deleted": created["name"], "error_sample": expected_error}


def lead_map_flow(client: Client, stamp: str, keep_created: bool) -> dict[str, Any]:
    created: list[tuple[str, str]] = []
    mobile_suffix = stamp[-8:]
    mobile = "05" + mobile_suffix
    capture = client.call_method(
        "namar_test.api.namar_capture_lead",
        {
            "lead_name": f"Codex Lead {stamp}",
            "mobile_no": mobile,
            "city": "الرياض",
            "doors_count": "3",
            "form_kind": "codex-live-regression",
            "page_url": "https://namar.sa/codex-live-regression",
            "utm_source": "codex",
        },
    )
    lead_name = capture.get("lead")
    require(lead_name, "namar_capture_lead did not return a lead")
    created.append(("Lead", lead_name))

    lead_source = first_name(client, "Lead Source") or "Website"
    lat = 24.70 + (int(stamp[-2:]) / 10000.0)
    lng = 46.67 + (int(stamp[-2:]) / 10000.0)
    profile = client.call_method(
        "namar_test.api.save_map_lead",
        {
            "name": lead_name,
            "mode": "update_profile_fields",
            "mobile_no": mobile,
            "source": lead_source,
            "custom_door_count": 4,
            "company_name": f"Codex Company {stamp}",
        },
    )
    location = client.call_method(
        "namar_test.api.save_map_lead",
        {
            "name": lead_name,
            "mode": "update_location",
            "lat": lat,
            "lng": lng,
            "city": "الرياض",
        },
    )
    visit = client.call_method(
        "namar_test.api.save_lead_visit_log",
        {
            "lead": lead_name,
            "visit_result": "تمت زيارة",
            "next_action": "متابعة",
            "visit_notes": f"Codex live regression {stamp}",
        },
    )
    visit_name = (visit.get("visit_log") or {}).get("name") or visit.get("name")
    if visit_name:
        created.append(("Lead Field Visit", visit_name))
    logs_response = client.call_method("namar_test.api.get_lead_visit_logs", {"lead": lead_name})
    logs = logs_response.get("logs") if isinstance(logs_response, dict) else logs_response
    require(isinstance(logs, list) and logs, "get_lead_visit_logs did not return the created visit")

    if not keep_created:
        for doctype, name in reversed(created):
            client.delete_doc(doctype, name)
    return {
        "lead": lead_name,
        "deleted": not keep_created,
        "capture_is_new": capture.get("is_new"),
        "profile_mode": profile.get("mode"),
        "location_mode": location.get("mode"),
        "visit_log": visit_name,
        "visit_logs_count": len(logs),
    }


def delivery_component_flow(client: Client, material_request: str) -> dict[str, Any]:
    synced = client.call_method("sync_delivery_component_packages", {"mr": material_request})
    require(synced.get("status") == "synced", "delivery sync did not return synced")
    packages = client.call_method("get_delivery_component_packages", {"mr": material_request})
    pending = [
        row
        for row in packages.get("packages", [])
        if float(row.get("remaining_qty") or 0) > 0 and int(row.get("required_for_delivery") or 0)
    ]
    require(pending, f"no pending delivery component package found on {material_request}")
    target = pending[0]
    marked = client.call_method(
        "mark_delivery_component_package_ready",
        {
            "mr": material_request,
            "component_package": target.get("barcode_key") or target.get("name"),
            "mode": "full",
            "source": "يدوي",
        },
    )
    require(marked.get("status") in {"done", "partial"}, "delivery package mark did not complete")
    return {
        "material_request": material_request,
        "package_count": packages.get("package_count"),
        "marked_package": target.get("loading_code") or target.get("name"),
        "new_summary": marked.get("summary"),
    }


def find_pending_manufacturing_row(client: Client, preferred_mr: str) -> tuple[str, int, dict[str, Any]]:
    candidates = [preferred_mr] if preferred_mr else []
    for row in client.list_docs("Material Request", ["name"], [["docstatus", "<", 2]], limit=80, order_by="modified desc"):
        name = row.get("name")
        if name and name not in candidates:
            candidates.append(name)
    for mr in candidates:
        try:
            data = client.call_method("get_manufactured_items", {"mr": mr}) or {}
        except FrappeError:
            continue
        for item in data.get("items") or []:
            if float(item.get("remaining_qty") or 0) > 0:
                return mr, int(item.get("row")), item
    raise AssertionError("no pending manufacturing row was found")


def manufacturing_flow(client: Client, preferred_mr: str) -> dict[str, Any]:
    mr, row_idx, item = find_pending_manufacturing_row(client, preferred_mr)
    result = client.call_method("mark_manufactured_rows_v2", {"mr": mr, "rows": f"{row_idx}:1"})
    require(result.get("status") in {"done", "no_change"}, "manufacturing registration did not return a valid status")
    require(result.get("updated_count", 0) >= 1, "manufacturing registration did not update any row")
    return {
        "material_request": mr,
        "row": row_idx,
        "item_code": item.get("item_code"),
        "registered_qty": result.get("registered_qty"),
        "manufacturing_status": result.get("manufacturing_status"),
        "remaining_count": result.get("remaining_count"),
    }


def sales_order_balance_flow(client: Client) -> dict[str, Any]:
    errors: list[str] = []
    for row in client.list_docs("Sales Order", ["name"], [["docstatus", "=", 1]], limit=40, order_by="modified desc"):
        sales_order = row.get("name")
        if not sales_order:
            continue
        try:
            message = client.call_method("make_material_request_from_sales_order_balance", {"sales_order": sales_order})
        except FrappeError as exc:
            errors.append(str(exc)[:180])
            continue
        mr_name = message.get("name")
        require(mr_name, "make_material_request_from_sales_order_balance did not return a Material Request")
        client.delete_doc("Material Request", mr_name)
        return {
            "sales_order": sales_order,
            "material_request_created_then_deleted": mr_name,
            "items_count": message.get("items_count"),
        }
    raise AssertionError("no submitted Sales Order with remaining quantities was found; samples: " + " | ".join(errors[:3]))


def main() -> None:
    parser = argparse.ArgumentParser(description="Live regression tests for migrated namar_test customizations on test.")
    parser.add_argument("--env", choices=["test"], default="test")
    parser.add_argument("--delivery-mr", default="MREQ-06077-1")
    parser.add_argument("--manufacturing-mr", default="MREQ-05110")
    parser.add_argument("--keep-created", action="store_true")
    args = parser.parse_args()

    stamp = time.strftime("%Y%m%d%H%M%S")
    client = Client(args.env)
    summary: dict[str, Any] = {
        "env": args.env,
        "site": client.base,
        "stamp": stamp,
        "checks": {},
    }

    summary["checks"]["versions"] = check_versions(client)
    summary["checks"]["legacy_scripts"] = check_legacy_scripts_removed(client)
    summary["checks"]["assets"] = check_assets(client)
    summary["checks"]["app_methods"] = check_read_only_methods(client)
    summary["checks"]["reports"] = check_reports(client)
    summary["checks"]["customer_mobile_validation"] = customer_mobile_validation_flow(client, stamp)
    summary["checks"]["lead_map"] = lead_map_flow(client, stamp, keep_created=args.keep_created)
    summary["checks"]["delivery_components"] = delivery_component_flow(client, args.delivery_mr)
    summary["checks"]["manufacturing"] = manufacturing_flow(client, args.manufacturing_mr)
    summary["checks"]["sales_order_balance"] = sales_order_balance_flow(client)

    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
