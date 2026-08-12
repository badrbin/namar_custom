from __future__ import annotations

import unittest

from namar_test.delivery_components.snapshot_backfill import build_event_snapshot_updates


class DeliveryComponentSnapshotBackfillTestCase(unittest.TestCase):
    def test_fills_only_blank_snapshot_values(self):
        events = [
            {
                "name": "EV-1",
                "package_id": "PKG-1",
                "component_label": "اسم محفوظ",
                "color": "",
                "item_name": None,
                "package_label": "",
                "package_qty": 0,
                "tracking_route": "تصنيع مستقل - باركود",
            }
        ]
        packages = [
            {
                "name": "PKG-1",
                "component": "حلق 20",
                "component_label": "حلق عشرين",
                "color": "تك",
                "item_name": "حلق تك",
                "package_label": "كرتون",
                "package_qty": 4,
                "tracking_route": "توريد فقط - بدون باركود",
            }
        ]

        updates, orphans = build_event_snapshot_updates(events, packages)

        self.assertEqual(orphans, [])
        self.assertEqual(len(updates), 1)
        self.assertNotIn("component_label", updates[0]["values"])
        self.assertNotIn("tracking_route", updates[0]["values"])
        self.assertEqual(updates[0]["values"]["color"], "تك")
        self.assertEqual(updates[0]["values"]["package_qty"], 4)

    def test_keeps_deleted_package_events_as_orphans(self):
        updates, orphans = build_event_snapshot_updates(
            [{"name": "EV-2", "package_id": "DELETED-PKG"}],
            [],
        )

        self.assertEqual(updates, [])
        self.assertEqual(
            orphans,
            [{"name": "EV-2", "package_id": "DELETED-PKG"}],
        )

    def test_skips_events_with_complete_snapshots(self):
        event = {
            "name": "EV-3",
            "package_id": "PKG-3",
            "component_label": "برواز 8X2.6",
            "color": "أبيض",
            "item_name": "برواز أبيض",
            "package_label": "كرتون",
            "package_qty": 6,
            "tracking_route": "تصنيع مستقل - باركود",
        }
        package = {"name": "PKG-3", **{key: value for key, value in event.items() if key not in {"name", "package_id"}}}

        updates, orphans = build_event_snapshot_updates([event], [package])

        self.assertEqual(updates, [])
        self.assertEqual(orphans, [])
