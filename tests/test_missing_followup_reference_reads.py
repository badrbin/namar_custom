from __future__ import annotations

from types import SimpleNamespace
import unittest

from namar_test.followups.logic import mention_event_key, mention_thread_key
from namar_test.followups.reference_access import can_read_reference, reference_exists
from test_followup_approval_counts import load_service as load_followup_service
from test_mention_followup_lifecycle import (
    FakeDict,
    load_events_module as load_lifecycle,
    make_thread,
)
from test_mention_inbox_events import load_events_module
from test_mention_inbox_permissions import load_mention_service


def reference_runtime(frappe, *, exists=True, permission=True):
    """Model a source record and Frappe's message-producing access failures."""

    frappe.message_log = [{"message": "رسالة سابقة"}]
    frappe.flags = SimpleNamespace(mute_messages=False, error_message="previous")
    frappe.reference_reads = []
    frappe.permission_reads = []
    frappe.metadata = {"Material Request": SimpleNamespace(issingle=False, is_virtual=False)}
    frappe.sources = {("Material Request", "MREQ-1")} if exists else set()

    def failure(exc_type):
        frappe.message_log.append({"message": "المستند غير موجود"})
        frappe.flags.error_message = "missing reference"
        raise exc_type("المستند غير موجود")

    def get_meta(doctype):
        if doctype not in frappe.metadata:
            failure(frappe.DoesNotExistError)
        return frappe.metadata[doctype]

    def get_value(doctype, name, fieldname="name", **kwargs):
        frappe.reference_reads.append((doctype, name, kwargs))
        name = name.get("name") if isinstance(name, dict) else name
        if doctype == "DocType":
            return name if name in frappe.metadata else None
        return name if (doctype, name) in frappe.sources else None

    def get_doc(doctype, name, **kwargs):
        if (doctype, name) not in frappe.sources:
            failure(frappe.DoesNotExistError)
        return SimpleNamespace(
            check_permission=lambda action: None if permission else failure(frappe.PermissionError),
            meta=SimpleNamespace(get_title_field=lambda: "customer_name"),
            get=lambda fieldname: "عنوان المستند",
        )

    def has_permission(doctype, permission_type, *, doc, user):
        frappe.permission_reads.append((doctype, permission_type, doc, user))
        if (doctype, doc) not in frappe.sources:
            failure(frappe.DoesNotExistError)
        return permission

    frappe.db = SimpleNamespace(get_value=get_value)
    frappe.get_meta = get_meta
    frappe.get_doc = get_doc
    frappe.has_permission = has_permission
    frappe.reference_failure = failure
    return frappe


class MissingReferenceReadsTestCase(unittest.TestCase):
    def test_missing_reference_never_reaches_permission_loading_or_adds_a_popup(self):
        row = SimpleNamespace(reference_doctype="Material Request", reference_name="MREQ-1")
        for loader, check in (
            (lambda: load_mention_service([]), lambda module: module._can_read_reference(row, "employee@example.com")),
            (load_events_module, lambda module: module._can_read_reference("employee@example.com", "Material Request", "MREQ-1")),
        ):
            module, frappe = loader()
            reference_runtime(frappe, exists=False)
            with self.subTest(module=module.__name__):
                self.assertFalse(check(module))
                self.assertEqual(frappe.permission_reads, [])
                self.assertEqual(frappe.message_log, [{"message": "رسالة سابقة"}])

    def test_existing_reference_uses_the_same_user_and_read_permission(self):
        module, frappe = load_mention_service([])
        reference_runtime(frappe, permission=False)
        row = SimpleNamespace(reference_doctype="Material Request", reference_name="MREQ-1")
        self.assertFalse(module._can_read_reference(row, "employee@example.com"))
        self.assertEqual(frappe.permission_reads, [("Material Request", "read", "MREQ-1", "employee@example.com")])

    def test_reference_disappearing_between_lookup_and_permission_is_silent(self):
        _, frappe = load_mention_service([])
        reference_runtime(frappe)
        frappe.has_permission = lambda *args, **kwargs: frappe.reference_failure(frappe.DoesNotExistError)
        self.assertFalse(can_read_reference(frappe, "employee@example.com", "Material Request", "MREQ-1"))
        self.assertEqual(frappe.message_log, [{"message": "رسالة سابقة"}])
        self.assertEqual(frappe.flags.error_message, "previous")
        self.assertFalse(frappe.flags.mute_messages)

    def test_title_loading_is_silent_for_missing_and_denied_references(self):
        for exists, permission in ((False, True), (True, False)):
            module, frappe = load_followup_service()
            reference_runtime(frappe, exists=exists, permission=permission)
            cache = {}
            with self.subTest(exists=exists, permission=permission):
                self.assertEqual(module._readable_reference_title("Material Request", "MREQ-1", cache), "MREQ-1")
                self.assertEqual(cache, {("Material Request", "MREQ-1"): "MREQ-1"})
                self.assertEqual(frappe.message_log, [{"message": "رسالة سابقة"}])
                self.assertEqual(frappe.flags.error_message, "previous")

    def test_title_is_still_resolved_for_readable_reference(self):
        module, frappe = load_followup_service()
        reference_runtime(frappe)
        self.assertEqual(module._readable_reference_title("Material Request", "MREQ-1"), "عنوان المستند")

    def test_title_load_race_restores_messages_without_enabling_muted_messages(self):
        module, frappe = load_followup_service()
        reference_runtime(frappe)
        frappe.flags.mute_messages = True
        del frappe.flags.error_message
        frappe.get_doc = lambda *args, **kwargs: frappe.reference_failure(frappe.DoesNotExistError)
        self.assertEqual(module._readable_reference_title("Material Request", "MREQ-1"), "MREQ-1")
        self.assertEqual(frappe.message_log, [{"message": "رسالة سابقة"}])
        self.assertTrue(frappe.flags.mute_messages)
        self.assertFalse(hasattr(frappe.flags, "error_message"))

    def test_unexpected_errors_and_their_messages_are_not_hidden(self):
        _, frappe = load_mention_service([])
        reference_runtime(frappe)
        frappe.has_permission = lambda *args, **kwargs: frappe.reference_failure(RuntimeError)
        with self.assertRaises(RuntimeError):
            can_read_reference(frappe, "employee@example.com", "Material Request", "MREQ-1")
        self.assertEqual(len(frappe.message_log), 2)
        self.assertEqual(frappe.flags.error_message, "missing reference")

    def test_missing_doctype_is_handled_without_changing_existing_messages(self):
        _, frappe = load_mention_service([])
        reference_runtime(frappe)
        self.assertFalse(reference_exists(frappe, "Removed DocType", "OLD-1"))
        self.assertEqual(frappe.message_log, [{"message": "رسالة سابقة"}])
        self.assertEqual(frappe.reference_reads, [])

    def test_single_reference_uses_its_metadata_row_instead_of_a_nonexistent_table(self):
        _, frappe = load_mention_service([])
        reference_runtime(frappe)
        frappe.metadata["Settings"] = SimpleNamespace(issingle=True, is_virtual=False)
        self.assertTrue(reference_exists(frappe, "Settings", "Settings", for_update=True))
        self.assertEqual(frappe.reference_reads, [("DocType", "Settings", {"for_update": True})])

    def test_regular_record_named_after_its_doctype_does_not_use_singles_lookup(self):
        _, frappe = load_mention_service([])
        reference_runtime(frappe)
        frappe.sources = {("Material Request", "Material Request")}
        get_value = frappe.db.get_value
        singles_reads = []

        def frappe_value_lookup(doctype, filters, fieldname="name", **kwargs):
            # Frappe Database.get_values routes this string-filter case to Singles.
            if filters == doctype and doctype != "DocType":
                singles_reads.append(doctype)
                return None
            return get_value(doctype, filters, fieldname, **kwargs)

        frappe.db.get_value = frappe_value_lookup
        for for_update in (False, True):
            with self.subTest(for_update=for_update):
                self.assertTrue(reference_exists(frappe, "Material Request", "Material Request", for_update=for_update))
        self.assertEqual(singles_reads, [])
        self.assertEqual(frappe.reference_reads[-1][2], {"for_update": True})
        frappe.sources.clear()
        self.assertFalse(reference_exists(frappe, "Material Request", "Material Request", for_update=True))

    def test_virtual_reference_delegates_loading_and_lock_flag_to_its_controller(self):
        _, frappe = load_mention_service([])
        reference_runtime(frappe)
        frappe.metadata["Virtual Record"] = SimpleNamespace(issingle=False, is_virtual=True)
        frappe.sources.add(("Virtual Record", "REMOTE-1"))
        calls = []
        load_doc = frappe.get_doc

        def get_doc(doctype, name, **kwargs):
            calls.append((doctype, name, kwargs))
            return load_doc(doctype, name, **kwargs)

        frappe.get_doc = get_doc
        self.assertTrue(reference_exists(frappe, "Virtual Record", "REMOTE-1", for_update=True))
        self.assertEqual(calls, [("Virtual Record", "REMOTE-1", {"for_update": True})])
        self.assertEqual(frappe.reference_reads, [])
        frappe.sources.clear()
        self.assertFalse(reference_exists(frappe, "Virtual Record", "REMOTE-1", for_update=True))
        self.assertEqual(frappe.message_log, [{"message": "رسالة سابقة"}])


class QueuedMentionDeletionTestCase(unittest.TestCase):
    def event_args(self):
        content = "@employee راجع الطلب"
        return dict(
            for_user="employee@example.com", reference_doctype="Sales Order", reference_name="SO-1",
            comment_name="COMMENT-1", comment_modified="2026-09-12 12:00:00", content=content,
            from_user="sender@example.com",
            event_key=mention_event_key("employee@example.com", "COMMENT-1", "2026-09-12 12:00:00", content),
        )

    def test_late_queued_snapshot_does_not_recreate_deleted_source_thread(self):
        module, _, database = load_lifecycle()
        # A retained snapshot/comment cannot substitute for the deleted source.
        database.comments["COMMENT-1"] = FakeDict(reference_doctype="Sales Order", reference_name="SO-1")
        self.assertIsNone(module.process_mention_event(**self.event_args()))
        self.assertEqual(database.threads, {})
        self.assertEqual(database.events, {})

    def test_source_lock_is_acquired_before_thread_lock_and_write(self):
        module, _, database = load_lifecycle()
        name = mention_thread_key("employee@example.com", "Sales Order", "SO-1")
        make_thread(database, name=name, status="Open")
        database.comments["COMMENT-1"] = FakeDict(reference_doctype="Sales Order", reference_name="SO-1")
        locks = []
        get_value = database.get_value

        def record_lock(doctype, filters, fieldname="name", **kwargs):
            if kwargs.get("for_update"):
                locks.append((doctype, filters))
            return get_value(doctype, filters, fieldname, **kwargs)

        database.get_value = record_lock
        self.assertEqual(module.process_mention_event(**self.event_args()), name)
        self.assertEqual(locks[:2], [("Sales Order", {"name": "SO-1"}), ("Namar Mention Thread", name)])
        self.assertEqual(len(database.events), 1)

    def test_deletion_winning_before_source_lock_prevents_all_thread_writes(self):
        module, _, database = load_lifecycle()
        database.references.add(("Sales Order", "SO-1"))
        database.comments["COMMENT-1"] = FakeDict(reference_doctype="Sales Order", reference_name="SO-1")
        get_value = database.get_value

        def delete_before_lock(doctype, filters, fieldname="name", **kwargs):
            if doctype == "Sales Order" and kwargs.get("for_update"):
                database.references.clear()
            return get_value(doctype, filters, fieldname, **kwargs)

        database.get_value = delete_before_lock
        self.assertIsNone(module.process_mention_event(**self.event_args()))
        self.assertEqual(database.threads, {})
        self.assertEqual(database.events, {})


if __name__ == "__main__":
    unittest.main()
