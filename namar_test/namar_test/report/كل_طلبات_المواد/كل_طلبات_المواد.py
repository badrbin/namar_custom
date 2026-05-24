from __future__ import annotations

from namar_test.material_requests import execute_all_material_requests_report


def execute(filters=None):
    return execute_all_material_requests_report(filters)
