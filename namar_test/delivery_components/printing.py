from __future__ import annotations

from typing import Any

from namar_test.delivery_components import service
from namar_test.delivery_components.package_logic import is_barcode_tracking_route


def sector_print_status(material_request: str | None) -> dict[str, Any]:
    """Return a live print gate so stale package rows can never produce labels."""
    name = service.normalize_material_request(material_request)
    if not name:
        return {
            "ready": False,
            "needs_sync": True,
            "printable_package_ids": [],
            "message": "طلب المواد غير محدد.",
        }

    mr_doc = service.ensure_material_request_access(name)
    packages = service.get_packages(name)
    needs_sync = service.packages_need_sync(mr_doc, packages)
    printable_package_ids = [
        row.get("name")
        for row in packages
        if row.get("name") and is_barcode_tracking_route(row.get("tracking_route"))
    ]

    if needs_sync:
        message = "تغيرت بيانات القطاعات. استخدم زر طباعة باركود القطاعات من طلب المواد لإعادة المزامنة."
    elif not printable_package_ids:
        message = "لا توجد قطاعات قابلة للطباعة في هذا الطلب."
    else:
        message = ""

    return {
        "ready": not needs_sync and bool(printable_package_ids),
        "needs_sync": needs_sync,
        "printable_package_ids": printable_package_ids,
        "message": message,
    }
