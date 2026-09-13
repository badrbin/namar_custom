#!/usr/bin/env python3
"""Narrow real-Material-Request regression on TEST, with reversible metadata only.

Default is an offline plan. --run changes only the two routing fields of the
existing completed Workflow row: Owner, then hidden, then restores them in
finally. No Material Request, native Workflow Action, user, role or permission
is written. Private read snapshots establish exact sets without hydrating every
document through the public list. The actual ordinary-user counts, sample pages
and native get_transitions are also checked. --restore-manifest resumes cleanup
after an interrupted/uncertain request; writes are never automatically retried.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import stat
import sys
import time
from urllib.parse import quote, urlparse
from uuid import uuid4

_SPEC = importlib.util.spec_from_file_location("approval_smoke_common", Path(__file__).with_name("smoke_test_approval_index.py"))
common = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(common)
Failure, ensure = common.Failure, common.ensure
WORKFLOW = "طلب مواد"
DOCTYPE = "Material Request"
COMPLETED = "مكتمل"
FIELD, HIDE = common.FIELD, common.HIDE
MAX_ROWS = 30000
READ_BATCH = 2000
OWNER = json.dumps({"version": 1, "targets": [{"type": "owner"}]}, separators=(",", ":"))


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def semantic(value):
    """Only automatic modification metadata and JSON formatting are ignored."""
    if isinstance(value, dict):
        result = {key: semantic(item) for key, item in value.items()
                  if key not in {"modified", "modified_by"} and not key.startswith("__")}
        if result.get(FIELD):
            result[FIELD] = json.loads(result[FIELD]) if isinstance(result[FIELD], str) else result[FIELD]
        return result
    if isinstance(value, list):
        return [semantic(item) for item in value]
    return value


def target_row(doc, row_name):
    ensure(doc.get("doctype") == "Workflow" and doc.get("name") == WORKFLOW
           and doc.get("document_type") == DOCTYPE and doc.get("is_active") == 1
           and doc.get("workflow_state_field") == "workflow_state", "Workflow identity/configuration mismatch")
    ensure(isinstance(row_name, str) and row_name.strip(), "Exact Workflow state-row argument is required")
    matches = [row for row in doc.get("states", []) if row.get("name") == row_name and row.get("state") == COMPLETED]
    ensure(len(matches) == 1, "Exact completed Workflow row is missing or ambiguous")
    ensure(sum(row.get("state") == COMPLETED for row in doc["states"]) == 1, "Duplicate completed state")
    return matches[0]


def without_target_settings(doc, row_name):
    copy = deepcopy(doc)
    row = target_row(copy, row_name)
    row.pop(FIELD, None)
    row.pop(HIDE, None)
    return semantic(copy)


def planned(doc, targets, hidden, row_name):
    copy = deepcopy(doc)
    target_row(copy, row_name).update({FIELD: targets, HIDE: hidden})
    return copy


def expected_sets(before, actions, owners, actor):
    completed = {name for name in before if actions[name]["reference_doctype"] == DOCTYPE
                 and actions[name]["workflow_state"] == COMPLETED}
    other = before - completed
    ensure(completed and other, "Acceptance requires visible completed and unrelated approvals")
    ensure(all(actions[name]["reference_name"] in owners for name in completed), "Missing completed-document owner")
    owned = {name for name in completed if owners[actions[name]["reference_name"]] == actor}
    ensure(completed - owned, "Acceptance requires a completed approval owned by another user")
    return {"owner": other | owned, "hidden": other, "completed": completed, "owned": owned}


def load_config(args):
    env = common.config(args)
    values = {}
    for raw in args.env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip().removeprefix("export ")
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    login = urlparse(values.get("BROWSER_LOGIN_URL", ""))
    ensure(login.scheme == "https" and login.hostname == urlparse(env["site"]).hostname
           and not login.username and not login.password, "Ordinary login origin does not match dedicated TEST")
    ensure(values.get("BROWSER_LOGIN_EMAIL") not in (None, "", "Administrator", "Guest")
           and values.get("BROWSER_LOGIN_PASSWORD"), "Missing ordinary TEST login")
    return {**env, "actor": values["BROWSER_LOGIN_EMAIL"], "password": values["BROWSER_LOGIN_PASSWORD"]}


class Runner:
    def __init__(self, env, args, journal):
        self.env, self.args, self.journal = env, args, journal
        self.admin = common.Client(self, "Administrator", env["token"])
        self.actor = common.Client(self, "ordinary-user")
        self.api = "namar_test.followups.api"
        self.engine = "namar_test.followups.approval_index"

    def rows(self, doctype, fields, filters=None):
        """POST is a read-only get_list call; bounded JSON prevents oversized URLs."""
        result = []
        while len(result) < MAX_ROWS:
            rows = self.admin.call("frappe.client.get_list", {"doctype": doctype, "fields": fields,
                "filters": filters or {}, "order_by": "name asc", "limit_start": len(result),
                "limit_page_length": READ_BATCH}, post=True)
            ensure(isinstance(rows, list), "Invalid administrative list envelope")
            result.extend(rows)
            ensure(len({row["name"] for row in result}) == len(result), "Administrative snapshot contains duplicate names")
            if len(rows) < READ_BATCH:
                return result
        raise Failure("Read snapshot exceeded bounded row limit")

    def exact_rows(self, doctype, fields, names):
        result = []
        names = sorted(set(names))
        for offset in range(0, len(names), READ_BATCH):
            result.extend(self.rows(doctype, fields, {"name": ["in", names[offset:offset + READ_BATCH]]}))
        ensure({row["name"] for row in result} == set(names), "Exact administrative lookup changed or lost a reference")
        return result

    def wait_ready(self):
        deadline = time.monotonic() + self.args.wait_seconds
        while True:
            status = self.admin.call(self.engine + ".status")
            counts = self.actor.call(self.api + ".get_my_followups_counts")
            ensure(status and status.get("serving_enabled") is True and status.get("build_enabled") is True,
                   "TEST index build/serving is not enabled")
            state = common.Runner.count_state(counts, "ordinary-user")
            self.journal.event("readiness", administrative_status=status, ordinary_counts=counts)
            if state == "ready":
                ensure(type(status.get("generation")) is int and (status.get("control") or {}).get("scan_complete"),
                       "Ready ordinary cohort has no complete index generation")
                return status["generation"], counts
            ensure(time.monotonic() < deadline, "Ordinary-user index readiness timed out")
            time.sleep(5)

    def business_snapshot(self):
        materials = self.rows(DOCTYPE, ["name", "owner", "modified", "docstatus", "workflow_state"])
        ensure(materials and all(row.get("workflow_state") for row in materials),
               "Blank Material Request workflow_state: Workflow.save could initialize business records")
        actions = self.rows("Workflow Action", ["name", "modified", "status", "reference_doctype", "reference_name", "workflow_state", "user"],
                            {"status": "Open"})
        return {"material_requests": materials, "native_open_actions": actions,
                "materials_sha256": digest(materials), "native_open_sha256": digest(actions)}

    def assert_business_unchanged(self):
        current, baseline = self.business_snapshot(), self.journal.data["business_before"]
        ensure(current["materials_sha256"] == baseline["materials_sha256"], "Material Request metadata changed during acceptance")
        ensure(current["native_open_sha256"] == baseline["native_open_sha256"], "Native open Workflow Actions changed during acceptance")
        self.journal.event("business_unchanged", material_requests=len(current["material_requests"]),
                           native_open_actions=len(current["native_open_actions"]),
                           materials_sha256=current["materials_sha256"], native_open_sha256=current["native_open_sha256"])

    def snapshot(self, scenario):
        epoch, counts = self.wait_ready()
        native = self.rows("Workflow Action", ["name", "modified", "status", "reference_doctype", "reference_name", "workflow_state", "user"],
                           {"status": "Open"})
        recipients = self.rows("Namar Approval Index Recipient", ["name", "action_name", "for_user", "epoch", "revision"],
                               {"for_user": self.env["actor"], "epoch": epoch})
        indexed = self.exact_rows("Namar Approval Index Action", ["name", "epoch", "state", "requested_revision", "built_revision"],
                                  [row["action_name"] for row in recipients])
        native_by_name, index_by_name = ({row["name"]: row for row in records} for records in (native, indexed))
        visible = set()
        for recipient in recipients:
            action = index_by_name[recipient["action_name"]]
            if (action["name"] in native_by_name and action["state"] == "Ready" and action["epoch"] == epoch
                    and action["requested_revision"] == action["built_revision"] == recipient["revision"]):
                ensure(action["name"] not in visible, "Duplicate ordinary recipient for one approval")
                visible.add(action["name"])
        end_epoch, end_counts = self.wait_ready()
        ensure(end_epoch == epoch and end_counts == counts, "Index generation or ordinary counters changed within snapshot")
        ensure(counts["counts"]["approvals"] == len(visible), "Public approval badge disagrees with indexed/native set")
        ensure(digest(native) == self.journal.data["business_before"]["native_open_sha256"], "Native approvals changed within snapshot")
        self.journal.event("set_snapshot", scenario=scenario, epoch=epoch, visible=sorted(visible), counts=counts,
                           recipient_rows=len(recipients), native_open_count=len(native))
        return visible, native_by_name

    def native_transitions(self):
        doc = {"doctype": DOCTYPE, "name": self.args.sample_reference}
        result = self.actor.call("frappe.model.workflow.get_transitions", {"doc": json.dumps(doc)}, post=True)
        ensure(isinstance(result, list) and result, "Ordinary sample has no native transitions to compare")
        return semantic(result)

    def check_public(self, expected, native, transitions):
        page = self.actor.call(self.api + ".get_approvals", {"page_length": 25})
        common.Runner.require_page_ready(page, "ordinary-user")
        names = [row["name"] for row in page["items"]]
        ordered = sorted(expected, key=lambda name: (native[name]["modified"], name), reverse=True)[:25]
        ensure(names == ordered and page["counts"]["open"] == len(expected), "Public first page or count disagrees with exact expected set")
        for search, scope in ((COMPLETED, "state"), (self.args.sample_reference, "document")):
            page = self.actor.call(self.api + ".get_approvals", {"search": search, "search_scope": scope, "page_length": 25})
            common.Runner.require_page_ready(page, "ordinary-user")
            field = "workflow_state" if scope == "state" else "reference_name"
            filtered = {name for name in expected if search in native[name][field]}
            ordered = sorted(filtered, key=lambda name: (native[name]["modified"], name), reverse=True)[:25]
            ensure([row["name"] for row in page["items"]] == ordered and page["counts"]["open"] == len(expected),
                   "Public search changed visibility or total count")
        ensure(self.native_transitions() == transitions, "Routing/hiding changed the native transition list")
        self.journal.event("public_api_passed", expected_count=len(expected), native_transitions_unchanged=True)

    def save(self, expected_current, desired, operation):
        current = self.admin.doc("Workflow", WORKFLOW)
        ensure(semantic(current) == semantic(expected_current), "Workflow changed before write; refused overwrite")
        baseline = self.journal.data["workflow_before"]
        ensure(without_target_settings(desired, self.args.state_row) == without_target_settings(baseline, self.args.state_row),
               "Mutation changed fields outside completed-row routing")
        if operation == "restore":
            # An unrelated legitimate business edit must fail acceptance, not
            # strand our known metadata change. The dangerous blank-state
            # condition still blocks Workflow.save, including restoration.
            self.business_snapshot()
        else:
            self.assert_business_unchanged()
        self.journal.data["restore_needed"] = True
        self.journal.data["allowed_workflows"].append(desired)
        self.journal.event("mutation_before", operation=operation, intended=desired)
        result = self.admin.request("PUT", "/api/resource/Workflow/" + quote(WORKFLOW, safe=""),
                                    body={"modified": current["modified"], "states": desired["states"]}).get("data")
        ensure(result and semantic(result) == semantic(desired), "Workflow save changed unexpected fields")
        self.journal.event("mutation_after", operation=operation, after=result)
        return result

    def exercise(self):
        before = self.admin.doc("Workflow", WORKFLOW)
        row = target_row(before, self.args.state_row)
        ensure(not row.get(FIELD) and not row.get(HIDE), "Completed baseline is not native-role/unhidden")
        self.journal.data.update({"workflow_before": before, "allowed_workflows": [before], "restore_needed": False,
                                  "business_before": self.business_snapshot()})
        self.journal.flush()
        visible, native = self.snapshot("baseline")
        completed_names = [native[name]["reference_name"] for name in visible
                           if native[name]["reference_doctype"] == DOCTYPE and native[name]["workflow_state"] == COMPLETED]
        owners = {row["name"]: row["owner"] for row in self.exact_rows(DOCTYPE, ["name", "owner"], completed_names)}
        expected = expected_sets(visible, native, owners, self.env["actor"])
        transitions = self.native_transitions()
        self.journal.data.update({"baseline_visible": sorted(visible), "native_transitions_before": transitions})
        self.journal.event("expectations", completed=len(expected["completed"]), owned=len(expected["owned"]),
                           owner_count=len(expected["owner"]), hidden_count=len(expected["hidden"]))
        self.check_public(visible, native, transitions)
        current = before
        for scenario, desired in (("owner", planned(before, OWNER, 0, self.args.state_row)),
                                  ("hidden", planned(before, OWNER, 1, self.args.state_row))):
            current = self.save(current, desired, scenario)
            actual, native = self.snapshot(scenario)
            ensure(actual == expected[scenario], f"{scenario}: exact approval membership mismatch")
            self.check_public(expected[scenario], native, transitions)
            self.assert_business_unchanged()
        self.journal.data["tests_passed"] = True
        self.journal.flush()

    def restore(self):
        data = self.journal.data
        if not data.get("workflow_before"):
            return
        baseline = data["workflow_before"]
        current = self.admin.doc("Workflow", WORKFLOW)
        ensure(without_target_settings(current, self.args.state_row) == without_target_settings(baseline, self.args.state_row),
               "Workflow changed outside test scope; restoration refused")
        ensure(any(semantic(current) == semantic(allowed) for allowed in data["allowed_workflows"]),
               "Completed routing changed externally; restoration refused")
        if semantic(current) != semantic(baseline):
            self.save(current, baseline, "restore")
        self.wait_ready()
        ensure(semantic(self.admin.doc("Workflow", WORKFLOW)) == semantic(baseline), "Workflow restoration differs from baseline")
        data["workflow_restored"] = True
        self.journal.flush()
        self.assert_business_unchanged()
        if data.get("baseline_visible") is not None:
            actual, native = self.snapshot("restored")
            ensure(actual == set(data["baseline_visible"]), "Restored approval membership differs from baseline")
            self.check_public(actual, native, data["native_transitions_before"])
        data.update({"restore_needed": False, "restoration_complete": True})
        self.journal.event("restoration_complete", workflow_semantic_sha256=digest(semantic(baseline)))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--confirm-site")
    parser.add_argument("--env-file", type=Path, default=Path("/Users/badrarroug/erpnex_codex/.env.local"))
    parser.add_argument("--namespace", default="namar_test")
    parser.add_argument("--state-dir", type=Path, default=Path.home() / "namar-real-material-request-acceptance")
    parser.add_argument("--sample-reference", help="Required for execution: exact real TEST Material Request reference")
    parser.add_argument("--state-row", help="Required for execution: exact completed Workflow child-row name")
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--wait-seconds", type=int, default=900)
    parser.add_argument("--restore-manifest", type=Path)
    args = parser.parse_args(argv)
    if not args.run and not args.restore_manifest:
        print(json.dumps({"network": False, "writes": False, "target": "TEST only", "workflow": WORKFLOW,
                          "required_execution_args": ["--sample-reference", "--state-row"],
                          "mutations": ["Owner on completed", "hide completed", "restore"],
                          "business_document_writes": False, "native_actions_executed": False}, ensure_ascii=False, indent=2))
        return 0
    runner = journal = None
    try:
        ensure(args.sample_reference and args.sample_reference.strip() and args.state_row and args.state_row.strip(),
               "--sample-reference and --state-row are required for execution or restoration")
        env = load_config(args)
        directory = common.private_dir(args.state_dir)
        if args.restore_manifest:
            path = args.restore_manifest.resolve()
            ensure(path.parent == directory and not args.restore_manifest.is_symlink() and stat.S_IMODE(path.stat().st_mode) == 0o600,
                   "Restore manifest must be a private 0600 file in state-dir")
            data = json.loads(path.read_text(encoding="utf-8"))
            ensure(data.get("schema") == "real-material-request-index/1" and data.get("site") == env["site"]
                   and data.get("actor") == env["actor"] and data.get("sample_reference") == args.sample_reference
                   and data.get("state_row") == args.state_row,
                   "Restore manifest identity mismatch")
        else:
            path = directory / ("acceptance-" + uuid4().hex + ".json")
            data = {"schema": "real-material-request-index/1", "site": env["site"], "actor": env["actor"],
                    "sample_reference": args.sample_reference, "state_row": args.state_row,
                    "events": [], "tests_passed": False, "restoration_complete": False}
        journal = common.Journal(path, data)
        journal.flush()
        runner = Runner(env, args, journal)
        ensure(runner.admin.call("frappe.auth.get_logged_user") == "Administrator", "Dedicated TEST Administrator required")
        runner.actor.login(env["actor"], env["password"])
        if args.restore_manifest:
            runner.restore()
        else:
            try:
                runner.exercise()
            finally:
                runner.restore()
        print(json.dumps({"status": "passed", "tests_passed": data["tests_passed"],
                          "restoration_complete": data["restoration_complete"], "manifest": str(path)}, indent=2))
        return 0
    except Exception as exc:
        error = str(exc) if isinstance(exc, Failure) else type(exc).__name__
        if journal:
            journal.event("failed", error=error)
        print(json.dumps({"status": "failed", "error": error, "manifest": str(journal.path) if journal else None}), file=sys.stderr)
        return 1
    finally:
        if runner:
            runner.admin.session.close()
            runner.actor.session.close()


if __name__ == "__main__":
    raise SystemExit(main())
