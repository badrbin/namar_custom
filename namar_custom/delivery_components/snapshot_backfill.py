from __future__ import annotations

from typing import Any


SNAPSHOT_FIELDS = (
    "component_label",
    "color",
    "item_name",
    "package_label",
    "package_qty",
    "tracking_route",
)


def build_event_snapshot_updates(
    events: list[dict[str, Any]],
    packages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    package_map = {row.get("name"): row for row in packages if row.get("name")}
    updates: list[dict[str, Any]] = []
    orphans: list[dict[str, Any]] = []

    for event in events:
        package = package_map.get(event.get("package_id"))
        if not package:
            orphans.append({"name": event.get("name"), "package_id": event.get("package_id")})
            continue

        desired = {
            "component_label": package.get("component_label") or package.get("component") or "",
            "color": package.get("color") or "",
            "item_name": package.get("item_name")
            or package.get("component_label")
            or package.get("component")
            or "",
            "package_label": package.get("package_label") or "",
            "package_qty": package.get("package_qty") or 0,
            "tracking_route": package.get("tracking_route") or "",
        }
        values = {
            fieldname: value
            for fieldname, value in desired.items()
            if event.get(fieldname) in (None, "", 0) and value not in (None, "", 0)
        }
        if values:
            updates.append({"name": event.get("name"), "values": values})

    return updates, orphans
