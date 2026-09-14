#!/usr/bin/env python3
"""TEST-only automatic deployment-revision recovery acceptance.

Default is an offline plan. --run performs exactly one deliberate mutation of
the private derived Control.current.engine_revision, using a fresh modified CAS.
It never calls rebuild/recover_pending, changes a business document, or changes
site configuration. The ordinary HTTP endpoints must fail closed immediately;
the normal scheduler must adopt once and rebuild automatically. Private receipts
include unchanged Workflow/MR/native-action fingerprints and exact approval
membership before/after. A failed/interrupted run can restore only its own still
present tamper stamp, with another fresh CAS; no uncertain write is retried.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import re
import stat
import sys
import time
from urllib.parse import quote
from uuid import uuid4

_SPEC = importlib.util.spec_from_file_location("approval_real_common", Path(__file__).with_name("verify_real_material_request_index.py"))
real = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(real)
common, Failure, ensure = real.common, real.Failure, real.ensure
CONTROL = "Namar Approval Index Control"
CONTROL_NAME = "current"
CONTROL_PATH = "/api/resource/" + quote(CONTROL, safe="") + "/" + CONTROL_NAME
SCHEMA = "approval-engine-revision-acceptance/1"
STAMP = re.compile(r"^[0-9a-f]{64}$")
TAMPER = re.compile(r"^test-stale-[0-9a-f]{32}$")


def validate_control(doc):
    ensure(isinstance(doc, dict) and doc.get("doctype") == CONTROL and doc.get("name") == CONTROL_NAME,
           "Private Control identity mismatch")
    ensure(type(doc.get("epoch")) is int and doc["epoch"] > 0 and doc.get("modified"),
           "Control lacks generation or modified CAS")
    return doc


def validate_status(status):
    ensure(isinstance(status, dict) and status.get("serving_enabled") is True
           and status.get("build_enabled") is True, "TEST index build and serving must be enabled")
    ensure(STAMP.fullmatch(status.get("runtime_engine_revision") or ""), "Runtime fingerprint missing or malformed")
    control = status.get("control") or {}
    ensure(status.get("stored_engine_revision") == control.get("engine_revision"), "Status/control revision disagreement")
    ensure(type(status.get("generation")) is int and status["generation"] == control.get("epoch"),
           "Status/control generation disagreement")
    return status


class Runner(real.Runner):
    def status(self):
        return validate_status(self.admin.call(self.engine + ".status"))

    def wait_ready(self):
        deadline = time.monotonic() + self.args.wait_seconds
        while True:
            status = self.status()
            counts = self.actor.call(self.api + ".get_my_followups_counts")
            count_state = common.Runner.count_state(counts, "ordinary-user")
            self.journal.event("readiness", administrative_status=status, ordinary_counts=counts)
            if status.get("state") == "ready" and count_state == "ready":
                ensure(status["runtime_engine_revision"] == status["stored_engine_revision"]
                       and status["control"].get("scan_complete"), "Ready index has no matching complete engine stamp")
                return status["generation"], counts
            ensure(status.get("state") == "updating" and time.monotonic() < deadline,
                   "Index is not ready within bounded preflight/recovery wait")
            time.sleep(self.args.poll_seconds)

    def check_schema(self):
        metadata = self.admin.doc("DocType", CONTROL)
        fields = [row for row in (metadata or {}).get("fields", []) if row.get("fieldname") == "engine_revision"]
        ensure(metadata and metadata.get("name") == CONTROL and len(fields) == 1
               and fields[0].get("fieldtype") == "Data" and fields[0].get("hidden") == 1
               and fields[0].get("read_only") == 1, "Fresh Control metadata must include private read-only engine_revision")
        self.journal.event("schema_verified", field=fields[0])

    def workflow_unchanged(self):
        baseline = self.journal.data["workflow_before"]
        current = self.admin.doc("Workflow", real.WORKFLOW)
        ensure(real.semantic(current) == real.semantic(baseline)
               and current.get("modified") == baseline.get("modified"), "Workflow changed during revision acceptance")
        self.journal.event("workflow_unchanged", fingerprint=real.digest(real.semantic(current)), modified=current.get("modified"))

    def check_public_sample(self, visible, native):
        page = self.actor.call(self.api + ".get_approvals", {"page_length": 25})
        common.Runner.require_page_ready(page, "ordinary-user")
        expected = sorted(visible, key=lambda name: (native[name]["modified"], name), reverse=True)[:25]
        ensure([row["name"] for row in page["items"]] == expected
               and page["counts"]["open"] == len(visible), "Ordinary public first page/count differs from exact indexed/native membership")
        self.journal.event("public_sample_verified", names=expected, total=len(visible))

    def tamper_once(self):
        data = self.journal.data
        ensure(not data.get("tamper_attempted"), "Deliberate tamper may be attempted only once")
        current = validate_control(self.admin.doc(CONTROL, CONTROL_NAME))
        before = validate_control(data["control_before"])
        ensure(current == before, "Control changed before tamper; refused overwrite")
        ensure(STAMP.fullmatch(before.get("engine_revision") or "") and TAMPER.fullmatch(data.get("tamper_revision") or ""),
               "Invalid known-original or isolated tamper stamp")
        data.update(tamper_attempted=True, restore_needed=True)
        self.journal.event("mutation_before", operation="tamper_engine_revision", target=CONTROL_PATH,
                           before=current, intended={"engine_revision": data["tamper_revision"]})
        result = self.admin.request("PUT", CONTROL_PATH,
                                    body={"modified": current["modified"], "engine_revision": data["tamper_revision"]}).get("data")
        validate_control(result)
        ensure(result.get("engine_revision") == data["tamper_revision"] and result["epoch"] == before["epoch"],
               "Tamper response changed unexpected Control fields")
        for key, value in before.items():
            if key not in {"modified", "modified_by", "engine_revision"} and not key.startswith("__"):
                ensure(result.get(key) == value, "Tamper changed non-target Control field")
        self.journal.event("mutation_after", operation="tamper_engine_revision", after=result)

    def observe_fail_closed(self):
        counts = self.actor.call(self.api + ".get_my_followups_counts")
        page = self.actor.call(self.api + ".get_approvals", {"page_length": 25})
        status = self.status()
        self.journal.event("immediate_public_result", ordinary_counts=counts, ordinary_page=page,
                           administrative_status=status,
                           scheduler_already_adopted=status["stored_engine_revision"] == status["runtime_engine_revision"])
        ensure(common.Runner.count_state(counts, "ordinary-user") == "updating", "Did not observe fail-closed public counter window")
        ensure(page and page.get("status") == "updating" and page.get("items") == []
               and (page.get("counts") or {}).get("open", "missing") is None,
               "Did not observe fail-closed empty public page")
        self.journal.data["fail_closed_observed"] = True
        self.journal.flush()

    def await_automatic_recovery(self):
        data = self.journal.data
        baseline = data["control_before"]
        expected_epoch = baseline["epoch"] + 1
        deadline = time.monotonic() + self.args.wait_seconds
        while True:
            status = self.status()
            ensure(status["runtime_engine_revision"] == baseline["engine_revision"], "Application version changed during acceptance")
            ensure(status["generation"] in (baseline["epoch"], expected_epoch), "Automatic recovery incremented generation more than once")
            ensure(status["stored_engine_revision"] in (data["tamper_revision"], baseline["engine_revision"]),
                   "Control revision changed to an unrelated value")
            self.journal.event("automatic_recovery_wait", status=status)
            if status["generation"] == expected_epoch and status["state"] == "ready":
                ensure(status["stored_engine_revision"] == baseline["engine_revision"]
                       and status["control"].get("scan_complete"), "Automatic generation is not completely rebuilt")
                self.journal.data.update(automatic_recovery=True, recovered_epoch=expected_epoch, restore_needed=False)
                self.journal.flush()
                return
            ensure(status["state"] == "updating" and time.monotonic() < deadline,
                   "Automatic scheduler recovery failed or exceeded bounded wait")
            time.sleep(self.args.poll_seconds)

    def exercise(self):
        self.check_schema()
        self.wait_ready()
        before_workflow = self.admin.doc("Workflow", real.WORKFLOW)
        ensure(before_workflow and before_workflow.get("doctype") == "Workflow"
               and before_workflow.get("name") == real.WORKFLOW, "Workflow baseline identity mismatch")
        self.journal.data.update(workflow_before=before_workflow, business_before=self.business_snapshot())
        self.journal.flush()
        visible, native = self.snapshot("baseline")
        ensure(visible, "Acceptance requires nonempty ordinary approval membership")
        self.check_public_sample(visible, native)
        self.journal.data["baseline_visible"] = sorted(visible)
        status = self.status()
        before = validate_control(self.admin.doc(CONTROL, CONTROL_NAME))
        ensure(status["state"] == "ready" and before["engine_revision"] == status["runtime_engine_revision"]
               == status["stored_engine_revision"] and before["epoch"] == status["generation"],
               "Control is not an unchanged ready baseline")
        self.journal.data.update(control_before=before, tamper_revision="test-stale-" + uuid4().hex)
        self.journal.flush()
        self.tamper_once()
        self.observe_fail_closed()
        self.await_automatic_recovery()
        restored, native = self.snapshot("automatically_restored")
        ensure(restored == visible, "Automatic rebuild changed ordinary approval membership")
        self.check_public_sample(restored, native)
        self.assert_business_unchanged()
        self.workflow_unchanged()
        final = self.status()
        ensure(final["generation"] == before["epoch"] + 1 and final["state"] == "ready"
               and final["stored_engine_revision"] == before["engine_revision"], "Final automatic generation changed unexpectedly")
        self.journal.data.update(tests_passed=True, restoration_complete=True, restore_needed=False)
        self.journal.event("acceptance_passed", epoch_before=before["epoch"], epoch_after=final["generation"],
                           membership_sha256=real.digest(sorted(restored)), approvals=len(restored))

    def restore(self):
        """Only undo our known still-present tamper; never rewind a rebuilt epoch."""
        data = self.journal.data
        if not data.get("restore_needed") or not data.get("tamper_attempted"):
            return
        before = validate_control(data["control_before"])
        ensure(TAMPER.fullmatch(data.get("tamper_revision") or "") and STAMP.fullmatch(before.get("engine_revision") or ""),
               "Invalid restoration fingerprint")
        current = validate_control(self.admin.doc(CONTROL, CONTROL_NAME))
        if current.get("engine_revision") != data["tamper_revision"]:
            data["restore_needed"] = False
            self.journal.event("restoration_not_written", reason="own_tamper_no_longer_present", current=current)
            return
        ensure(not data.get("restore_attempted"), "Uncertain restoration is never retried")
        status = self.status()
        ensure(status["runtime_engine_revision"] == before["engine_revision"], "Refused restoration across an application version change")
        data["restore_attempted"] = True
        self.journal.event("mutation_before", operation="restore_own_engine_revision", before=current,
                           intended={"engine_revision": before["engine_revision"]})
        result = self.admin.request("PUT", CONTROL_PATH,
                                    body={"modified": current["modified"], "engine_revision": before["engine_revision"]}).get("data")
        validate_control(result)
        ensure(result.get("engine_revision") == before["engine_revision"] and result["epoch"] == current["epoch"],
               "Restored Control stamp or epoch mismatch")
        data.update(restore_needed=False, restoration_complete=True)
        self.journal.event("restoration_complete", after=result, automatic_recovery_proven=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--confirm-site")
    parser.add_argument("--env-file", type=Path, default=Path("/Users/badrarroug/erpnex_codex/.env.local"))
    parser.add_argument("--namespace", default="namar_test")
    parser.add_argument("--state-dir", type=Path, default=Path.home() / "namar-approval-engine-revision-acceptance")
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--wait-seconds", type=int, default=900)
    parser.add_argument("--poll-seconds", type=int, default=10)
    parser.add_argument("--restore-manifest", type=Path)
    args = parser.parse_args(argv)
    if not args.run and not args.restore_manifest:
        print(json.dumps({"network": False, "writes": False, "target": "TEST only", "deliberate_mutations": 1,
                          "mutation": CONTROL + ".current.engine_revision", "recovery": "normal scheduler only",
                          "business_writes": False, "manual_rebuild_or_recovery_calls": False}, indent=2))
        return 0
    runner = journal = None
    try:
        ensure(1 <= args.poll_seconds <= 20, "Polling interval must be between 1 and 20 seconds")
        env = real.load_config(args)
        directory = common.private_dir(args.state_dir)
        if args.restore_manifest:
            path = args.restore_manifest.resolve()
            ensure(path.parent == directory and not args.restore_manifest.is_symlink()
                   and stat.S_IMODE(path.stat().st_mode) == 0o600, "Restore manifest must be private 0600 in state-dir")
            data = json.loads(path.read_text(encoding="utf-8"))
            ensure(data.get("schema") == SCHEMA and data.get("site") == env["site"]
                   and data.get("actor") == env["actor"], "Restore manifest identity mismatch")
        else:
            path = directory / ("acceptance-" + uuid4().hex + ".json")
            data = {"schema": SCHEMA, "site": env["site"], "actor": env["actor"], "events": [],
                    "tests_passed": False, "restoration_complete": False, "tamper_attempted": False}
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
        outcome = ("restored" if data.get("restoration_complete") else "checked_no_write") if args.restore_manifest else "passed"
        print(json.dumps({"status": outcome, "tests_passed": data["tests_passed"],
                          "automatic_recovery": data.get("automatic_recovery", False),
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
