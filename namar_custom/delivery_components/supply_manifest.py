from __future__ import annotations

from typing import Any

from namar_custom.delivery_components.package_logic import (
    TRACKING_ROUTE_BARCODE,
    TRACKING_ROUTE_EXCLUDED,
    TRACKING_ROUTE_WITH_DOOR,
    clean_count,
    normalize_tracking_route,
)


DELIVERY_CATEGORY_SECTORS = "قطاعات وديكورات"
DELIVERY_CATEGORY_WITH_DOOR = "مكونات مع الباب"
DELIVERY_CATEGORY_OTHER = "بقية مكونات التوريد"


def _category_for_route(route: str) -> str:
    if route == TRACKING_ROUTE_BARCODE:
        return DELIVERY_CATEGORY_SECTORS
    if route == TRACKING_ROUTE_WITH_DOOR:
        return DELIVERY_CATEGORY_WITH_DOOR
    return DELIVERY_CATEGORY_OTHER


def _is_active_package(row: Any) -> bool:
    active = row.get("active")
    return active in (None, "", 1, "1", True)


def build_delivery_supply_manifest(material_request: Any) -> dict[str, Any]:
    """Build a delivery-only view without manufacturing readiness or scan state."""
    request_item_groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in material_request.get("items") or []:
        quantity = float(item.get("qty") or 0)
        if quantity <= 0:
            continue
        item_code = (item.get("item_code") or "").strip()
        item_name = (item.get("item_name") or item_code or "صنف").strip()
        uom = (item.get("stock_uom") or item.get("uom") or "").strip()
        key = (item_code, item_name, uom)
        group = request_item_groups.setdefault(
            key,
            {
                "item_code": item_code,
                "item_name": item_name,
                "uom": uom,
                "quantity": 0.0,
                "row_count": 0,
            },
        )
        group["quantity"] += quantity
        group["row_count"] += 1

    component_groups: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for package in material_request.get("custom_delivery_component_packages") or []:
        if not _is_active_package(package):
            continue
        if int(package.get("exclude_from_delivery") or 0):
            continue

        quantity = float(package.get("package_qty") or 0)
        if quantity <= 0:
            continue

        route = normalize_tracking_route(package.get("tracking_route"))
        if route == TRACKING_ROUTE_EXCLUDED:
            continue

        component = (
            package.get("component_label")
            or package.get("component")
            or package.get("item_name")
            or "مكون"
        ).strip()
        color = (package.get("color") or "").strip()
        item_code = (package.get("item_code") or "").strip()
        package_label = (package.get("package_label") or "حزمة").strip()
        category = _category_for_route(route)
        key = (category, route, component, color, item_code)
        group = component_groups.setdefault(
            key,
            {
                "category": category,
                "tracking_route": route,
                "component": component,
                "color": color,
                "item_code": item_code,
                "package_labels": [],
                "package_count": 0,
                "total_qty": 0.0,
            },
        )
        if package_label not in group["package_labels"]:
            group["package_labels"].append(package_label)
        group["package_count"] += 1
        group["total_qty"] += quantity

    request_items = list(request_item_groups.values())
    for row in request_items:
        row["quantity"] = clean_count(row["quantity"])
    request_items.sort(key=lambda row: (row["item_code"], row["item_name"]))

    category_order = {
        DELIVERY_CATEGORY_SECTORS: 1,
        DELIVERY_CATEGORY_WITH_DOOR: 2,
        DELIVERY_CATEGORY_OTHER: 3,
    }
    components = list(component_groups.values())
    for row in components:
        row["package_count"] = clean_count(row["package_count"])
        row["total_qty"] = clean_count(row["total_qty"])
        row["package_label"] = "، ".join(row.pop("package_labels"))
    components.sort(
        key=lambda row: (
            category_order.get(row["category"], 99),
            row["component"],
            row["color"],
        )
    )

    return {
        "material_request": material_request.get("name") or "",
        "modified": str(material_request.get("modified") or ""),
        "request_items": request_items,
        "components": components,
        "request_item_count": len(request_items),
        "component_count": len(components),
        "package_count": clean_count(sum(row["package_count"] for row in components)),
        "component_total_qty": clean_count(sum(row["total_qty"] for row in components)),
    }
