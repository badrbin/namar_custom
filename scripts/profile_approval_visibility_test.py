#!/usr/bin/env python3
"""Profile one approval-list request on TEST; never expose recorded headers.

Requires an inactive, empty native Recorder so cleanup cannot remove somebody
else's recordings. This diagnostic is separate from acceptance timings.
"""
import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from smoke_test_approval_routing import Client, Journal, PROD_HOSTS, ensure, origin, private_dir, read_env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--confirm-site", required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args()
    if not args.run:
        print("Dry run: one TEST request, native Recorder, sanitized profile only.")
        return
    env = read_env(args.env_file)
    site = origin(env["FRAPPE_TEST_SITE"])
    ensure(site == origin(args.confirm_site), "TEST confirmation mismatch")
    denied = set(PROD_HOSTS)
    if env.get("FRAPPE_PROD_SITE"):
        denied.add(urlparse(origin(env["FRAPPE_PROD_SITE"])).hostname)
    ensure(urlparse(site).hostname not in denied, "PROD is prohibited")
    directory = private_dir(args.state_dir)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    journal = Journal(directory / ("recorder-" + stamp + ".json"), {"site": site, "events": []})
    journal.flush()
    client = Client(site, "TEST Recorder", journal, 120, env["FRAPPE_TEST_TOKEN"])
    target = "/api/method/namar_test.followups.api.get_approvals"
    armed = False
    request_completed = False
    try:
        ensure(client.call("frappe.auth.get_logged_user") == "Administrator", "TEST Administrator required")
        ensure(client.call("frappe.recorder.status") is False, "An existing Recorder is active; left untouched")
        before = client.call("frappe.recorder.get")
        ensure(isinstance(before, list) and not before, "Existing Recorder data must not be removed; left untouched")
        # Arm before the call: an uncertain start must still be followed by stop.
        armed = True
        client.call("frappe.recorder.start", post=True, args={
            "record_jobs": 0, "record_requests": 1, "record_sql": 1,
            "profile": 1, "capture_stack": 0, "explain": 0,
            "request_filter": target,
        })
        result = client.call("namar_test.followups.api.get_approvals", args={"page_length": 25})
        request_completed = True
        ensure(isinstance(result, dict) and len(result.get("items", [])) == 25, "Profiled list request did not return 25 rows")
    finally:
        if armed:
            client.call("frappe.recorder.stop", post=True)
    records = client.call("frappe.recorder.get")
    ensure(request_completed and isinstance(records, list) and len(records) == 1,
           "Unexpected record count or unsettled request; inspect before cleanup")
    record = records[0]
    ensure(record.get("path") == target and record.get("method") == "GET", "Unexpected capture left untouched")
    # stop() schedules post_process. Wait for its final normalized cache write
    # before deleting this capture, otherwise that job could recreate it.
    deadline = time.monotonic() + 90
    while True:
        detail = client.call("frappe.recorder.get", args={"uuid": record["uuid"]})
        ensure(isinstance(detail, dict) and detail.get("uuid") == record["uuid"], "Invalid Recorder response")
        calls = detail.get("calls")
        ensure(isinstance(calls, list) and calls and isinstance(detail.get("profile"), str), "Invalid profiler data")
        if all("normalized_query" in call and "index" in call for call in calls):
            break
        ensure(time.monotonic() < deadline, "Recorder processing is not complete; capture left intact")
        time.sleep(1)
    safe = {
        "site": site, "uuid": record["uuid"], "path": target,
        "duration_ms": record.get("duration"), "query_count": len(calls),
        "sql_duration_ms": sum(float(call.get("duration") or 0) for call in calls),
        "profile": detail["profile"],
        "not_an_acceptance_timing": True,
    }
    output = directory / ("profile-" + stamp + ".json")
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(safe, stream, ensure_ascii=False, indent=2)
    # The Recorder was empty; remove only if the collection is still exactly
    # our single capture. Headers/form_dict/raw SQL are never written locally.
    current = client.call("frappe.recorder.get")
    ensure(isinstance(current, list) and {item["uuid"] for item in current} == {record["uuid"]},
           "Recorder changed concurrently; recordings left untouched")
    client.call("frappe.recorder.delete", post=True)
    ensure(client.call("frappe.recorder.get") == [], "Recorder cleanup was not confirmed")
    ensure(client.call("frappe.recorder.status") is False, "Recorder still active")
    print(json.dumps({"profile": str(output), "duration_ms": safe["duration_ms"],
                      "query_count": safe["query_count"], "sql_duration_ms": safe["sql_duration_ms"],
                      "recorder_clean": True}, ensure_ascii=False))
    client.session.close()


if __name__ == "__main__":
    main()
