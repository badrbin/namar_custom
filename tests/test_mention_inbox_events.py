from __future__ import annotations

import importlib.util
import sys
from datetime import datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from namar_custom.followups.logic import mention_thread_key


EVENTS_PATH = (
    Path(__file__).resolve().parents[1]
    / "namar_custom"
    / "mentions"
    / "events.py"
)


class FakeDatabase:
    def get_value(self, doctype, filters, fieldname, **kwargs):
        if doctype == "User":
            return filters.get("name")
        return None


def load_events_module(*, can_read: bool = True):
    fake_frappe = ModuleType("frappe")
    fake_frappe.db = FakeDatabase()
    fake_frappe.has_permission = lambda *args, **kwargs: can_read
    fake_frappe.DoesNotExistError = type("DoesNotExistError", (Exception,), {})
    fake_frappe.PermissionError = type("PermissionError", (Exception,), {})

    fake_desk = ModuleType("frappe.desk")
    fake_notifications = ModuleType("frappe.desk.notifications")
    fake_notifications.extract_mentions = lambda content: []
    fake_utils = ModuleType("frappe.utils")
    fake_utils.get_datetime = lambda value: value
    fake_utils.now_datetime = datetime.now
    fake_frappe.desk = fake_desk
    fake_frappe.utils = fake_utils
    fake_desk.notifications = fake_notifications

    module_name = f"_namar_mention_events_test_{id(fake_frappe)}"
    spec = importlib.util.spec_from_file_location(module_name, EVENTS_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("تعذر تحميل وحدة أحداث وارد الإشارات")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(
        sys.modules,
        {
            "frappe": fake_frappe,
            "frappe.desk": fake_desk,
            "frappe.desk.notifications": fake_notifications,
            "frappe.utils": fake_utils,
            module_name: module,
        },
    ):
        spec.loader.exec_module(module)
    return module, fake_frappe


class MentionNotificationLinkTestCase(unittest.TestCase):
    def test_eligible_readable_mention_gets_exact_inbox_deep_link(self):
        module, fake_frappe = load_events_module(can_read=True)
        notification = SimpleNamespace(
            type="Mention",
            for_user="employee@example.com",
            document_type="Material Request",
            document_name="MREQ-1",
            link="/app/material-request/MREQ-1",
        )

        module.link_notification_to_mention_thread(notification)

        thread_name = mention_thread_key(
            notification.for_user,
            notification.document_type,
            notification.document_name,
        )
        self.assertEqual(
            notification.link,
            f"/app/my-followups?source=mentions&thread={thread_name}",
        )

    def test_notification_link_is_unchanged_without_reference_read_permission(self):
        module, fake_frappe = load_events_module(can_read=False)
        notification = SimpleNamespace(
            type="Mention",
            for_user="employee@example.com",
            document_type="Material Request",
            document_name="MREQ-2",
            link="/app/material-request/MREQ-2",
        )
        module.link_notification_to_mention_thread(notification)
        self.assertEqual(notification.link, "/app/material-request/MREQ-2")


if __name__ == "__main__":
    unittest.main()
