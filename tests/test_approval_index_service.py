"""HTTP-service integration contracts for the indexed approval worklist.

The engine is replaced only at its documented read boundary. These tests run
the actual service, permission query, pagination and freshness checks. They do
not assert that the retired synchronous resolver is a valid HTTP fallback.
"""
from __future__ import annotations

import ast
import builtins
from contextlib import contextmanager
from copy import deepcopy
import importlib
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

from test_followup_approval_counts import SERVICE_PATH, FakeFrappeDict, load_service, workflow_action


USER = "employee@example.com"
GENERATION = "generation-1"
ROUTING = {"mode": "Targets", "targets": [{"type": "owner", "user": USER}], "responsible_users": [], "fallback": False, "note": ""}


class ReadableDocument(FakeFrappeDict):
    def __init__(self, values, permission_check):
        super().__init__(values)
        self.meta = SimpleNamespace(get_title_field=lambda: "title", has_field=lambda name: name in self)
        self._permission_check = permission_check

    def check_permission(self, permission):
        self._permission_check(self, permission)


class IndexedServiceRuntime:
    def __init__(self, size=1):
        self.service, self.frappe = load_service()
        self.frappe.conf.followup_approval_index_enabled = True
        self.rows = [workflow_action(f"WA-{index}") for index in range(size)]
        self.index_rows = [FakeFrappeDict(row, routing=deepcopy(ROUTING), _index_revision=f"revision-{index}") for index, row in enumerate(self.rows)]
        self.doc_reads = []
        self.query_calls = []
        self.permission_checks = []
        self.denied_actions = set()
        self.denied_references = set()
        self.missing_references = set()
        self.engine = ModuleType("namar_custom.followups.approval_index")
        self.engine.read_counts = Mock(return_value={"open": size, "state": "ready", "generation": GENERATION, "message": ""})
        self.engine.read_page = Mock(return_value={"items": self.index_rows, "total": size, "state": "ready", "generation": GENERATION})
        self.engine.assert_visible = Mock(side_effect=lambda user, name, with_snapshot=False: {
            "routing": deepcopy(ROUTING), "generation": GENERATION, "revision": "revision-0",
        } if with_snapshot else deepcopy(ROUTING))
        self.engine.verify_snapshot = Mock(return_value=True)
        self.frappe.get_list = self.get_list
        self.frappe.get_doc = self.get_doc
        self.frappe.get_all = Mock(side_effect=AssertionError("Service must not bypass native list permissions"))
        self.frappe.db.get_value = lambda doctype, filters, *args, **kwargs: (filters.get("name") if isinstance(filters, dict) else filters)
        self.service.get_transitions = lambda doc: [{"action": "Approve", "next_state": "Approved", "allowed": "Approver"}]

    @contextmanager
    def installed(self):
        package = importlib.import_module("namar_custom.followups")
        with (
            patch.dict(sys.modules, {"namar_custom.followups.approval_index": self.engine}),
            patch.object(package, "approval_index", self.engine, create=True),
        ):
            yield self

    @contextmanager
    def mentions_installed(self):
        service = ModuleType("namar_custom.mentions.service")
        service.get_open_mention_count = Mock(return_value=7)
        package = ModuleType("namar_custom.mentions")
        package.service = service
        with (
            patch.dict(sys.modules, {"namar_custom.mentions": package, "namar_custom.mentions.service": service}),
            patch.object(self.service, "_followup_open_count", return_value=4),
            patch.object(self.service, "_followup_overdue_count", return_value=2),
        ):
            yield service

    def get_list(self, doctype, **options):
        self.query_calls.append((doctype, options))
        if doctype != "Workflow Action":
            raise AssertionError(f"Unexpected service query: {doctype}")
        rows = [row for row in self.rows if row.name not in self.denied_actions and row.status == "Open"]
        names = options.get("filters", {}).get("name")
        if isinstance(names, list):
            self_names = set(names[1])
            rows = [row for row in rows if row.name in self_names]
        elif names:
            rows = [row for row in rows if row.name == names]
        if options.get("fields") == ["count(name) as count"]:
            return [FakeFrappeDict(count=len(rows))]
        start = options.get("limit_start", 0)
        length = options.get("limit_page_length", len(rows))
        return [FakeFrappeDict(row) for row in rows[start:start + length]]

    def get_doc(self, doctype, name):
        self.doc_reads.append((doctype, name))
        if doctype == "Workflow Action":
            return next(row for row in self.rows if row.name == name)
        if name in self.missing_references:
            raise self.frappe.DoesNotExistError(name)
        return ReadableDocument({"doctype": doctype, "name": name, "title": f"عنوان {name}", "owner": USER}, self.check_permission)

    def check_permission(self, doc, permission):
        self.permission_checks.append((doc.name, permission))
        if doc.name in self.denied_references:
            raise self.frappe.PermissionError("Reference read denied")

    @contextmanager
    def detail_helpers(self):
        with (
            patch.object(self.service, "_reference_summary", return_value={"name": "MREQ-WA-0"}),
            patch.object(self.service, "_get_timeline", return_value=[{"content": "مرخص"}]),
        ):
            yield


class ApprovalIndexServiceTestCase(unittest.TestCase):
    def test_service_cannot_import_or_call_retired_global_scan_resolver(self):
        tree = ast.parse(SERVICE_PATH.read_text(encoding="utf-8"))
        retired = {"ApprovalRoutingResolver", "visible_actions", "route_rows"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                self.assertNotIn(node.id, retired)
            elif isinstance(node, ast.Attribute):
                self.assertNotIn(node.attr, retired)
            elif isinstance(node, ast.ImportFrom):
                self.assertFalse(retired.intersection(alias.name for alias in node.names))

    def test_count_reads_only_index_and_never_hydrates_documents_or_legacy_resolver(self):
        runtime = IndexedServiceRuntime(size=8000)
        with runtime.installed(), patch.dict(runtime.service.role_routing.__globals__, {"ApprovalRoutingResolver": Mock(side_effect=AssertionError("Retired resolver"))}):
            result = runtime.service._approval_counts()
        self.assertEqual(result["open"], 8000)
        runtime.engine.read_counts.assert_called_once_with(USER)
        self.assertEqual(runtime.doc_reads, [])
        self.assertEqual(runtime.query_calls, [])

    def test_disabled_index_uses_native_count_even_with_legacy_switch_missing_or_false(self):
        for legacy in (None, False, True):
            runtime = IndexedServiceRuntime(size=4)
            runtime.frappe.conf.followup_approval_index_enabled = False
            if legacy is not None:
                runtime.frappe.conf.disable_followup_approval_routing = legacy
            runtime.denied_actions.add("WA-0")
            with runtime.installed(), patch.dict(runtime.service.role_routing.__globals__, {"ApprovalRoutingResolver": Mock(side_effect=AssertionError("Never restore legacy scan"))}):
                counts = runtime.service._approval_counts()
                with patch.object(runtime.service, "_readable_reference_title", return_value="المستند"):
                    page = runtime.service.get_approvals(page_length=2)
            with self.subTest(legacy=legacy):
                self.assertEqual(counts, {"open": 3})
                self.assertEqual(page["counts"], {"open": 3})
                self.assertEqual(len(page["items"]), 2)
                self.assertTrue(page["has_more"])
                self.assertTrue(all(row["routing"]["mode"] == "Role" for row in page["items"]))
                runtime.engine.read_counts.assert_not_called()
                runtime.engine.read_page.assert_not_called()
                self.assertEqual(runtime.doc_reads, [])

    def test_updating_and_failed_counts_keep_inbox_followups_and_use_null_not_zero(self):
        for state in ("updating", "error"):
            runtime = IndexedServiceRuntime()
            runtime.engine.read_counts.return_value = {"open": 999, "state": state, "generation": "obsolete", "message": "غير جاهز"}
            with runtime.installed(), runtime.mentions_installed():
                result = runtime.service.get_my_followups_counts()
            with self.subTest(state=state):
                self.assertEqual(result["counts"], {"mentions": 7, "followups": 4, "approvals": None, "total": None})
                self.assertEqual(result["attention_counts"], {"mentions": 7, "followups": 2, "approvals": None, "total": None})
                self.assertEqual(result["approval_status"], state)
                self.assertEqual(result["approval_message"], "غير جاهز")
                self.assertEqual(runtime.doc_reads, [])

    def test_engine_failure_isolated_from_unified_counts_and_does_not_fallback(self):
        runtime = IndexedServiceRuntime()
        runtime.engine.read_counts.side_effect = RuntimeError("private database diagnostic")
        with runtime.installed(), runtime.mentions_installed():
            result = runtime.service.get_my_followups_counts()
        self.assertEqual(result["counts"]["mentions"], 7)
        self.assertEqual(result["counts"]["followups"], 4)
        self.assertIsNone(result["counts"]["approvals"])
        self.assertEqual(result["approval_status"], "error")
        self.assertNotIn("private database", str(result))
        self.assertEqual(runtime.query_calls, [])

    def test_import_failure_also_isolated_from_unified_count_and_list(self):
        runtime = IndexedServiceRuntime()
        real_import = builtins.__import__

        def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "namar_custom.followups" and "approval_index" in fromlist:
                raise ImportError("private import diagnostic")
            return real_import(name, globals, locals, fromlist, level)

        with runtime.mentions_installed(), patch("builtins.__import__", side_effect=guarded_import):
            counts = runtime.service.get_my_followups_counts()
            page = runtime.service.get_approvals()
        self.assertEqual(counts["approval_status"], "error")
        self.assertEqual(page["status"], "error")
        self.assertIsNone(page["counts"]["open"])
        self.assertEqual(page["items"], [])
        self.assertEqual(runtime.query_calls, [])

    def test_ready_zero_remains_distinguishable_from_unknown(self):
        runtime = IndexedServiceRuntime(size=0)
        with runtime.installed(), runtime.mentions_installed():
            counts = runtime.service.get_my_followups_counts()
            page = runtime.service.get_approvals()
        self.assertEqual(counts["counts"]["approvals"], 0)
        self.assertEqual(counts["counts"]["total"], 11)
        self.assertEqual(counts["approval_status"], "ready")
        self.assertEqual(page["status"], "ready")
        self.assertEqual(page["counts"], {"open": 0})

    def test_guest_rejected_before_engine_or_native_reads(self):
        runtime = IndexedServiceRuntime()
        runtime.frappe.session.user = "Guest"
        for method, args in (("get_my_followups_counts", ()), ("get_approvals", ()), ("get_approval_detail", ("WA-0",))):
            with self.subTest(method=method), runtime.installed():
                with self.assertRaises(runtime.frappe.PermissionError):
                    getattr(runtime.service, method)(*args)
        runtime.engine.read_counts.assert_not_called()
        runtime.engine.read_page.assert_not_called()
        runtime.engine.assert_visible.assert_not_called()
        self.assertEqual(runtime.query_calls, [])

    def test_ready_page_revalidates_only_page_ids_and_parent_permissions(self):
        runtime = IndexedServiceRuntime(size=3)
        with runtime.installed():
            result = runtime.service.get_approvals(search="  WA  ", search_scope="document", page_length=2)
        runtime.engine.read_page.assert_called_once_with(USER, search="WA", search_field="document", start=0, page_length=3)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["counts"], {"open": 3})
        self.assertEqual([row["name"] for row in result["items"]], ["WA-0", "WA-1"])
        self.assertTrue(result["has_more"])
        self.assertEqual(runtime.query_calls, [("Workflow Action", {"filters": {"name": ["in", ["WA-0", "WA-1", "WA-2"]], "status": "Open"}, "fields": list(runtime.service.WORKFLOW_ACTION_FIELDS), "limit_page_length": 3})])
        self.assertEqual(len(runtime.doc_reads), 3)
        self.assertEqual(len(runtime.permission_checks), 3)
        self.assertEqual(result["items"][0]["routing"], ROUTING)
        runtime.engine.verify_snapshot.assert_called_once_with(
            GENERATION, {"WA-0": "revision-0", "WA-1": "revision-1", "WA-2": "revision-2"}
        )

    def test_native_permission_loss_hides_whole_generation_before_parent_load(self):
        runtime = IndexedServiceRuntime()
        runtime.denied_actions.add("WA-0")
        with runtime.installed():
            result = runtime.service.get_approvals()
        self.assertEqual(result["status"], "updating")
        self.assertEqual(result["items"], [])
        self.assertIsNone(result["counts"]["open"])
        self.assertEqual(runtime.doc_reads, [])

    def test_reference_permission_revocation_or_deletion_never_leaks_partial_page(self):
        for failure in ("permission", "missing"):
            runtime = IndexedServiceRuntime(size=2)
            getattr(runtime, "denied_references" if failure == "permission" else "missing_references").add("MREQ-WA-1")
            with self.subTest(failure=failure), runtime.installed():
                result = runtime.service.get_approvals()
            self.assertEqual(result["status"], "updating")
            self.assertEqual(result["items"], [])
            self.assertIsNone(result["counts"]["open"])

    def test_current_action_mismatch_cannot_be_serialized_as_index_snapshot(self):
        for field, value in (("modified", "later"), ("workflow_state", "Completed"), ("reference_name", "different"), ("reference_doctype", "Sales Order"), ("status", "Completed")):
            runtime = IndexedServiceRuntime()
            runtime.rows[0][field] = value
            with self.subTest(field=field), runtime.installed():
                result = runtime.service.get_approvals()
            self.assertEqual(result["status"], "updating")
            self.assertEqual(result["items"], [])
            self.assertEqual(runtime.doc_reads, [])

    def test_generation_change_during_hydration_discards_list_and_count(self):
        runtime = IndexedServiceRuntime()
        runtime.engine.read_counts.return_value = {"open": 1, "state": "ready", "generation": "generation-2"}
        with runtime.installed():
            result = runtime.service.get_approvals()
        self.assertEqual(result["status"], "updating")
        self.assertEqual(result["items"], [])
        self.assertIsNone(result["counts"]["open"])

    def test_current_snapshot_mismatch_discards_page_despite_unchanged_repeatable_read_generation(self):
        runtime = IndexedServiceRuntime()
        runtime.engine.verify_snapshot.return_value = False
        with runtime.installed():
            result = runtime.service.get_approvals()
        self.assertEqual(result["status"], "updating")
        self.assertEqual(result["items"], [])
        self.assertIsNone(result["counts"]["open"])
        runtime.engine.verify_snapshot.assert_called_once_with(GENERATION, {"WA-0": "revision-0"})

    def test_non_ready_index_page_never_starts_parent_or_permission_reads(self):
        runtime = IndexedServiceRuntime()
        runtime.engine.read_page.return_value = {"state": "updating", "items": runtime.index_rows, "total": 888, "generation": "old"}
        with runtime.installed():
            result = runtime.service.get_approvals()
        self.assertEqual(result["items"], [])
        self.assertIsNone(result["counts"]["open"])
        self.assertEqual(runtime.query_calls, [])
        self.assertEqual(runtime.doc_reads, [])

    def test_page_engine_exception_is_generic_error_not_native_fallback(self):
        runtime = IndexedServiceRuntime()
        runtime.engine.read_page.side_effect = RuntimeError("private SQL payload")
        with runtime.installed():
            result = runtime.service.get_approvals()
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["items"], [])
        self.assertNotIn("private SQL", str(result))
        self.assertEqual(runtime.query_calls, [])

    def test_detail_checks_current_native_permission_before_index(self):
        runtime = IndexedServiceRuntime()
        runtime.denied_actions.add("WA-0")
        with runtime.installed():
            with self.assertRaises(runtime.frappe.PermissionError):
                runtime.service.get_approval_detail("WA-0")
        runtime.engine.assert_visible.assert_not_called()
        self.assertEqual(runtime.doc_reads, [])

    def test_non_ready_detail_returns_status_without_reference_or_timeline(self):
        runtime = IndexedServiceRuntime()
        runtime.engine.read_counts.return_value = {"state": "updating", "open": None, "message": "جار التحديث"}
        with runtime.installed():
            result = runtime.service.get_approval_detail("WA-0")
        self.assertEqual(result, {"status": "updating", "message": "جار التحديث"})
        self.assertEqual(runtime.doc_reads, [])
        runtime.engine.assert_visible.assert_not_called()

    def test_detail_rechecks_current_snapshot_after_hydration_and_preserves_native_actions(self):
        runtime = IndexedServiceRuntime()
        with runtime.installed(), runtime.detail_helpers():
            result = runtime.service.get_approval_detail("WA-0")
        self.assertEqual(result["approval"]["routing"], ROUTING)
        self.assertEqual(result["available_actions"], [{"action": "Approve", "next_state": "Approved", "allowed": "Approver"}])
        self.assertEqual(runtime.engine.assert_visible.call_args_list, [
            call(USER, "WA-0", with_snapshot=True), call(USER, "WA-0"),
        ])
        self.assertEqual(runtime.engine.read_counts.call_args_list[0], call(USER, verify_current=False))
        runtime.engine.verify_snapshot.assert_called_once_with(GENERATION, {"WA-0": "revision-0"})

    def test_detail_generation_changed_mid_read_returns_no_private_payload(self):
        runtime = IndexedServiceRuntime()
        # read_counts still returns the original MVCC snapshot; the final
        # current/locking read is the authority after concurrent invalidation.
        runtime.engine.verify_snapshot.return_value = False
        with runtime.installed(), runtime.detail_helpers():
            result = runtime.service.get_approval_detail("WA-0")
        self.assertEqual(result["status"], "updating")
        for key in ("approval", "reference", "timeline", "available_actions"):
            self.assertNotIn(key, result)

    def test_detail_mid_read_revocation_does_not_return_hydrated_document(self):
        runtime = IndexedServiceRuntime()
        runtime.engine.verify_snapshot.side_effect = runtime.frappe.PermissionError("revoked during read")
        with runtime.installed(), runtime.detail_helpers():
            with self.assertRaises(runtime.frappe.PermissionError):
                runtime.service.get_approval_detail("WA-0")


if __name__ == "__main__":
    unittest.main()
