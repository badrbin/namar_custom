#!/usr/bin/env python3
"""Bounded native-workflow acceptance on TEST, never production.

Default is a local plan (no network, credentials or writes). Explicit --run and
--confirm-site are required. Three temporary users, two roles and one isolated
DocType/Workflow exercise the actual API and native transition permissions.
All writes are journalled before execution, are not automatically retried, and
are cleaned in finally. --cleanup-manifest resumes only fingerprinted cleanup.
This never changes site configuration, enables an index, or invokes a rebuild.
Frappe may retain normal Deleted Document recovery rows and empty custom tables;
the harness never issues SQL/DROP or deletes ordinary business records.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
from threading import RLock
import time
from urllib.parse import quote, urlparse
from uuid import uuid4

import requests


ROOT = Path(__file__).resolve().parents[1]
FIELD = "custom_followups_routing_targets"
HIDE = "custom_followups_hide_from_approvals"
PREFIX_RE = re.compile(r"^NAI Smoke (\d{14}) ([a-f0-9]{8})$")
PROD_HOSTS = {"erp.namar.net", "zawaya7.frappe.cloud"}
MAX_TARGETS = 40
SECRET_FIELDS = {"new_password", "password", "pwd", "token", "api_key", "api_secret", "csrf_token"}


class Failure(RuntimeError):
    pass


class TransientIndexUpdate(Failure):
    """Only a validated updating response permits a bounded read-only retry."""


def ensure(condition, message):
    if not condition:
        raise Failure(message)


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def redact(value):
    if isinstance(value, dict):
        return {key: "[REDACTED]" if key.lower() in SECRET_FIELDS else redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def origin(value):
    parsed = urlparse(value if "://" in value else "https://" + value)
    ensure(parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password,
           "TEST must be HTTPS without URL credentials")
    ensure(parsed.path in ("", "/") and not parsed.query and not parsed.fragment and parsed.port in (None, 443),
           "TEST must be an HTTPS origin")
    return "https://" + parsed.hostname.lower().rstrip(".")


def config(args):
    values = {}
    for raw in args.env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip().removeprefix("export ")
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    ensure(values.get("FRAPPE_TEST_SITE") and values.get("FRAPPE_TEST_TOKEN"), "Missing dedicated TEST configuration")
    site = origin(values["FRAPPE_TEST_SITE"])
    denied = PROD_HOSTS | ({urlparse(origin(values["FRAPPE_PROD_SITE"])).hostname} if values.get("FRAPPE_PROD_SITE") else set())
    ensure(urlparse(site).hostname not in denied, "Refused production target")
    ensure(args.confirm_site and origin(args.confirm_site) == site, "--confirm-site must match FRAPPE_TEST_SITE")
    ensure(5 <= args.timeout <= 120 and 10 <= args.wait_seconds <= 900, "Invalid bounded timeout")
    ensure(args.namespace == "namar_test", "Only the TEST namespace is supported")
    return {"site": site, "token": values["FRAPPE_TEST_TOKEN"]}


def private_dir(path):
    ensure(not path.is_symlink(), "Private state path cannot be a symlink")
    path = path.expanduser().resolve()
    ensure(not any((parent / ".git").exists() for parent in (path, *path.parents)), "Journal must be outside Git")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    ensure(stat.S_IMODE(path.stat().st_mode) & 0o077 == 0, "Private journal directory must be 0700")
    return path


class Journal:
    def __init__(self, path, data):
        self.path, self.data, self.lock = path, data, RLock()

    def flush(self):
        with self.lock:
            temporary = self.path.with_name(self.path.name + ".pending-" + uuid4().hex)
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(redact(self.data), handle, ensure_ascii=False, indent=2)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.path)
            finally:
                if temporary.exists():
                    temporary.unlink()

    def event(self, event, **details):
        with self.lock:
            self.data["events"].append({"event": event, "at": utcnow(), **redact(details)})
            self.flush()


class Client:
    def __init__(self, runner, actor, token=""):
        self.runner, self.actor = runner, actor
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json", "User-Agent": "namar-approval-index-acceptance/1"})
        if token:
            self.session.headers["Authorization"] = token if token.lower().startswith("token ") else "token " + token

    def request(self, method, path, *, params=None, body=None, expected=(200,), html=False):
        self.runner.journal.event("http_before", actor=self.actor, method=method, path=path,
                                  query_keys=sorted((params or {}).keys()), body_keys=sorted((body or {}).keys()))
        start = time.monotonic()
        try:
            response = self.session.request(method, self.runner.env["site"] + path, params=params, json=body,
                                           timeout=self.runner.args.timeout, allow_redirects=False)
        except requests.RequestException as exc:
            self.runner.journal.event("http_failed", actor=self.actor, error=type(exc).__name__)
            raise Failure("Network outcome uncertain; request was not retried") from None
        self.runner.journal.event("http_after", actor=self.actor, method=method, path=path,
                                  status=response.status_code, seconds=round(time.monotonic() - start, 4))
        ensure(response.status_code in expected, f"{self.actor}: {method} {path}: HTTP {response.status_code}")
        if html:
            return response.text
        try:
            envelope = response.json() if response.content else {}
        except ValueError:
            raise Failure("Non-JSON API response") from None
        ensure(isinstance(envelope, dict), "Invalid API envelope")
        return envelope

    def call(self, method, args=None, *, post=False):
        return self.request("POST" if post else "GET", "/api/method/" + method,
                            **({"body": args} if post else {"params": args})).get("message")

    def doc(self, dt, name, *, missing=False):
        return self.request("GET", "/api/resource/" + quote(dt, safe="") + "/" + quote(name, safe=""),
                            expected=(200, 404) if missing else (200,)).get("data")

    def rows(self, dt, filters, *, fields=None):
        return self.request("GET", "/api/resource/" + quote(dt, safe=""), params={
            "fields": json.dumps(fields or (["name", "reference_name"] if dt == "Workflow Action" else ["name"])),
            "filters": json.dumps(filters), "limit_page_length": 50,
        }).get("data")

    def login(self, user, password):
        self.request("POST", "/api/method/login", body={"usr": user, "pwd": password})
        ensure(self.call("frappe.auth.get_logged_user") == user, "Temporary actor identity mismatch")
        page = self.request("GET", "/app", html=True)
        match = re.search(r'frappe\.csrf_token\s*=\s*[\"\x27]([^\"\x27]+)', page)
        ensure(match and match.group(1) not in ("None", "undefined"), "Missing session CSRF")
        self.session.headers["X-Frappe-CSRF-Token"] = match.group(1)


class Runner:
    stages = ("Owner", "Field", "Union", "NoSelf", "Hidden", "User", "End")

    def __init__(self, env, args, journal):
        self.env, self.args, self.journal = env, args, journal
        self.prefix = journal.data["prefix"]
        match = PREFIX_RE.fullmatch(self.prefix)
        ensure(match, "Invalid fixture prefix")
        self.users = {actor: f"index-{match[1]}-{match[2]}-{actor.lower()}@example.invalid" for actor in "ABC"}
        self.fixture, self.workflow = self.prefix, self.prefix + " Flow"
        self.review_role, self.read_role = self.prefix + " Review", self.prefix + " Read"
        self.api = args.namespace + ".followups.api"
        self.engine = args.namespace + ".followups.approval_index"
        self.admin = Client(self, "Administrator", env["token"])
        self.clients = {actor: Client(self, actor) for actor in self.users}
        self.sources = {actor: self.prefix + " Source " + actor for actor in self.users}

    def allowed(self, dt, name):
        if dt == "User":
            return name in self.users.values()
        return dt in {self.fixture, "Role", "DocType", "Workflow", "Workflow State", "Workflow Action Master"} and (
            name == self.prefix or name.startswith(self.prefix + " "))

    def target(self, dt, name):
        ensure(self.allowed(dt, name), "Mutation target is outside the isolated fixture namespace")
        target = next((row for row in self.journal.data["targets"] if row["doctype"] == dt and row["name"] == name), None)
        ensure(target and not target["deleted"], "Mutation target not present in the live journal")
        doc = self.admin.doc(dt, name, missing=True)
        if doc:
            ensure(all(doc.get(key) == value for key, value in target["fingerprint"].items()), "Live fixture fingerprint changed")
        return target, doc

    def create(self, dt, name, payload, fingerprint, *, client=None):
        ensure(self.allowed(dt, name) and len(self.journal.data["targets"]) < MAX_TARGETS, "Invalid or excessive fixture target")
        ensure(not self.admin.doc(dt, name, missing=True), "Fixture name already exists; never overwritten")
        self.journal.data["targets"].append({"doctype": dt, "name": name, "fingerprint": fingerprint, "deleted": False})
        self.journal.event("mutation_before", operation="create", doctype=dt, name=name, intended=payload)
        doc = (client or self.admin).request("POST", "/api/resource/" + quote(dt, safe=""), body=payload,
                                             expected=(200, 201)).get("data")
        ensure(doc and doc.get("name") == name, "Created fixture identity mismatch")
        self.journal.event("mutation_after", operation="create", doctype=dt, name=name, after=doc)
        return doc

    def update(self, dt, name, values):
        _, before = self.target(dt, name)
        ensure(before, "Missing fixture to update")
        self.journal.event("mutation_before", operation="update", doctype=dt, name=name, before=before, intended=values)
        doc = self.admin.request("PUT", "/api/resource/" + quote(dt, safe="") + "/" + quote(name, safe=""), body=values).get("data")
        self.journal.event("mutation_after", operation="update", doctype=dt, name=name, after=doc)
        return doc

    def delete(self, dt, name):
        target, before = self.target(dt, name)
        if before:
            self.journal.event("mutation_before", operation="delete", doctype=dt, name=name, before=before)
            self.admin.request("DELETE", "/api/resource/" + quote(dt, safe="") + "/" + quote(name, safe=""), expected=(200, 202))
            ensure(not self.admin.doc(dt, name, missing=True), "Fixture remained after deletion")
            self.journal.event("mutation_after", operation="delete", doctype=dt, name=name, after=None)
        target["deleted"] = True
        self.journal.flush()

    def wait_ready(self):
        end = time.monotonic() + self.args.wait_seconds
        while True:
            state = self.admin.call(self.engine + ".status")
            ensure(state and state.get("state") in ("ready", "updating", "error"), "Index is disabled or status is invalid")
            ensure(state.get("serving_enabled") is True, "TEST index serving was disabled during acceptance")
            counts = {actor: client.call(self.api + ".get_my_followups_counts") for actor, client in self.clients.items()}
            # The administrative status includes unrelated recipient cohorts.
            # Retain it as evidence but wait only for these actual actors. Their
            # own error/malformed/null contract still stops the test immediately.
            self.journal.event("index_wait", administrative_status=state, actor_counts=counts)
            actor_states = {actor: self.count_state(payload, actor) for actor, payload in counts.items()}
            if all(value == "ready" for value in actor_states.values()):
                return {**state, "actor_states": actor_states}
            ensure(time.monotonic() < end, "Bounded index readiness wait expired")
            time.sleep(2)

    @staticmethod
    def count_state(payload, actor):
        ensure(isinstance(payload, dict), f"{actor}: invalid count envelope")
        state = payload.get("approval_status")
        ensure(state in ("ready", "updating", "error"), f"{actor}: invalid approval status")
        counts, attention = payload.get("counts"), payload.get("attention_counts")
        ensure(isinstance(counts, dict) and isinstance(attention, dict), f"{actor}: invalid count maps")
        if state != "ready":
            ensure(all(key in values and values[key] is None for values in (counts, attention) for key in ("approvals", "total")),
                   f"{actor}: loading/error badge exposed a fake zero, stale total or missing null")
            ensure(state != "error", f"{actor}: index reported error; not a transient retry")
            return state
        ensure(all(type(values.get(key)) is int and values[key] >= 0 for values in (counts, attention)
                   for key in ("mentions", "followups", "approvals", "total")), f"{actor}: ready counts must be nonnegative integers")
        ensure(all(values["total"] == sum(values[key] for key in ("mentions", "followups", "approvals"))
                   for values in (counts, attention)), f"{actor}: ready count total disagrees")
        ensure(attention["approvals"] == counts["approvals"], f"{actor}: attention approval count differs")
        return state

    @staticmethod
    def require_page_ready(payload, actor):
        ensure(isinstance(payload, dict), f"{actor}: invalid list envelope")
        state = payload.get("status")
        ensure(state in ("ready", "updating", "error"), f"{actor}: invalid list status")
        counts = payload.get("counts")
        ensure(isinstance(counts, dict), f"{actor}: invalid list counts")
        if state != "ready":
            ensure(payload.get("items") == [] and "open" in counts and counts["open"] is None,
                   f"{actor}: unavailable list exposed rows, fake zero or missing null")
            ensure(state != "error", f"{actor}: list reported error; not a transient retry")
            raise TransientIndexUpdate(f"{actor}: list generation changed")
        ensure(type(counts.get("open")) is int and counts["open"] >= 0, f"{actor}: ready list count is invalid")

    @staticmethod
    def require_detail_ready(payload, actor):
        ensure(isinstance(payload, dict), f"{actor}: invalid detail envelope")
        if "status" in payload:
            ensure(payload["status"] == "updating", f"{actor}: detail reported error or invalid status")
            ensure(not any(key in payload for key in ("approval", "reference", "timeline", "available_actions")),
                   f"{actor}: updating detail exposed document information")
            raise TransientIndexUpdate(f"{actor}: detail generation changed")

    def setup(self):
        ensure(self.admin.call("frappe.auth.get_logged_user") == "Administrator", "Dedicated TEST token must be Administrator")
        state = self.admin.call(self.engine + ".status")
        ensure(state and state.get("state") != "disabled", "Deploy and enable TEST index first")
        ensure(state.get("serving_enabled") is True, "TEST index is shadow-only; no fixture was created")
        counts = self.admin.call(self.api + ".get_my_followups_counts")
        ensure(counts.get("approval_status") in ("ready", "updating", "error"), "Indexed HTTP service is not active")
        for role in (self.review_role, self.read_role):
            self.create("Role", role, {"doctype": "Role", "role_name": role, "desk_access": 1}, {"role_name": role})
        for actor, user in self.users.items():
            password = secrets.token_urlsafe(36)
            self.create("User", user, {"doctype": "User", "email": user, "first_name": self.prefix + " " + actor,
                "enabled": 1, "user_type": "System User", "send_welcome_email": 0, "new_password": password,
                "roles": [{"role": self.review_role}, {"role": self.read_role}]}, {"email": user})
            self.clients[actor].login(user, password)
        self.create("DocType", self.fixture, {"doctype": "DocType", "name": self.fixture, "custom": 1,
            "module": "Custom", "description": self.prefix, "autoname": "field:title", "title_field": "title",
            "fields": [{"fieldname": "title", "label": "Title", "fieldtype": "Data", "reqd": 1},
                       {"fieldname": "approver", "label": "Approver", "fieldtype": "Link", "options": "User"},
                       {"fieldname": "workflow_state", "label": "Workflow State", "fieldtype": "Link", "options": "Workflow State"}],
            "permissions": [{"role": self.read_role, "read": 1, "write": 1, "create": 1, "delete": 1}]},
            {"custom": 1, "description": self.prefix})
        for stage in self.stages:
            name = self.prefix + " " + stage
            self.create("Workflow State", name, {"doctype": "Workflow State", "workflow_state_name": name}, {"workflow_state_name": name})
        targets = {"Owner": [{"type": "owner"}], "Field": [{"type": "field", "field": "approver"}],
                   "Union": [{"type": "owner"}, {"type": "field", "field": "approver"},
                             {"type": "user", "user": self.users["A"]}, {"type": "role", "role": self.review_role}],
                   "User": [{"type": "user", "user": self.users["C"]}]}
        states = [{"state": self.prefix + " " + stage, "doc_status": "0", "allow_edit": self.read_role, "send_email": 0,
                   HIDE: int(stage == "Hidden"), FIELD: json.dumps({"version": 1, "targets": targets.get(stage, [])})} for stage in self.stages]
        transitions = []
        for left, right in zip(self.stages, self.stages[1:]):
            action = self.prefix + " To " + right
            self.create("Workflow Action Master", action, {"doctype": "Workflow Action Master", "workflow_action_name": action},
                        {"workflow_action_name": action})
            transitions.append({"state": self.prefix + " " + left, "action": action, "next_state": self.prefix + " " + right,
                                "allowed": self.review_role, "allow_self_approval": int(left != "NoSelf")})
        self.create("Workflow", self.workflow, {"doctype": "Workflow", "workflow_name": self.workflow, "document_type": self.fixture,
            "is_active": 1, "send_email_alert": 0, "workflow_state_field": "workflow_state", "states": states, "transitions": transitions},
            {"workflow_name": self.workflow, "document_type": self.fixture, "send_email_alert": 0})
        for actor, name in self.sources.items():
            self.create(self.fixture, name, {"doctype": self.fixture, "title": name, "approver": self.users["B"],
                "workflow_state": self.prefix + " Owner"}, {"title": name, "owner": self.users[actor]}, client=self.clients[actor])
        self.journal.event("fixtures_ready", fixture=self.fixture, users=self.users)

    def listing(self, client, **extra):
        return client.call(self.api + ".get_approvals", {"search": self.fixture, "search_scope": "doctype", "page_length": 25, **extra})

    def check(self, scenario, expected):
        # Never repeat a fixture write or silently swallow a correctness/error
        # failure. Only the typed, validated updating branch retries reads once.
        for attempt in range(2):
            try:
                return self.check_ready(scenario, expected)
            except TransientIndexUpdate as exc:
                self.journal.event("read_only_retry", scenario=scenario, attempt=attempt + 1, reason=str(exc))
                if attempt == 1:
                    raise Failure("Index changed during both bounded read-only attempts") from None

    def check_ready(self, scenario, expected):
        ready = self.wait_ready()
        actions = self.admin.rows("Workflow Action", {"reference_doctype": self.fixture, "status": "Open"})
        observation = {}
        for actor, client in self.clients.items():
            listing = self.listing(client)
            self.require_page_ready(listing, actor)
            rows = listing.get("items")
            ensure(isinstance(rows, list), "Invalid items contract")
            actual = {row["reference_name"] for row in rows}
            ensure(actual == expected[actor] and len(actual) == len(rows), f"{scenario}: visibility or duplicate mismatch for {actor}")
            for row in rows:
                ensure(not (row.get("routing") or {}).get("fallback"), "Index broadened an explicit recipient as fallback")
                detail = client.call(self.api + ".get_approval_detail", {"action_name": row["name"]})
                self.require_detail_ready(detail, actor)
                ensure(detail and detail.get("approval", {}).get("name") == row["name"], "Detail/list identity mismatch")
            for action in actions:
                if action["reference_name"] not in expected[actor]:
                    denied = client.request("GET", "/api/method/" + self.api + ".get_approval_detail",
                                            params={"action_name": action["name"]}, expected=(200, 403))
                    if "message" in denied and isinstance(denied["message"], dict):
                        self.require_detail_ready(denied["message"], actor)
                    ensure(denied.get("exc_type") == "PermissionError", "Hidden index detail did not fail closed")
            counts = client.call(self.api + ".get_my_followups_counts")
            if self.count_state(counts, actor) == "updating":
                raise TransientIndexUpdate(f"{actor}: count generation changed")
            ensure(counts["counts"]["approvals"] == listing["counts"]["open"], "Badge and list global counts disagree")
            ensure(counts["attention_counts"]["approvals"] == counts["counts"]["approvals"], "Badge attention count differs")
            ensure(counts["counts"]["total"] == sum(counts["counts"][key] for key in ("mentions", "followups", "approvals")),
                   "Unified source count total disagrees")
            observation[actor] = {"visible": sorted(actual), "count": counts["counts"]["approvals"]}
        self.journal.event("assertion_passed", scenario=scenario, generation=ready.get("generation"), observation=observation)

    def transition(self, actor, stage, *, client=None, denied=False):
        name = self.sources[actor]
        _, before = self.target(self.fixture, name)
        payload = {"doc": json.dumps(before), "action": self.prefix + " To " + stage}
        self.journal.event("mutation_before", operation="native_workflow", name=name, before=before, expected_denied=denied)
        response = (client or self.admin).request("POST", "/api/method/frappe.model.workflow.apply_workflow", body=payload,
                                                expected=(403, 417) if denied else (200,))
        after = self.admin.doc(self.fixture, name)
        if denied:
            ensure(response.get("exc_type") and after == before, "Denied native approval changed the document")
        else:
            ensure(after["workflow_state"] == self.prefix + " " + stage, "Native workflow did not advance")
        self.journal.event("mutation_after", operation="native_workflow", name=name, after=after, denied=denied)

    def advance(self, stage):
        for actor in self.sources:
            self.transition(actor, stage)

    def exercise(self):
        names = set(self.sources.values())
        own = {actor: {name} for actor, name in self.sources.items()}
        all_users = {actor: names for actor in self.users}
        nobody = {actor: set() for actor in self.users}
        self.check("owner_per_source", own)
        self.advance("Field")
        self.check("direct_user_field", {"A": set(), "B": names, "C": set()})
        self.update(self.fixture, self.sources["A"], {"approver": self.users["A"]})
        self.check("field_change_invalidates_only_source", {"A": own["A"], "B": names - own["A"], "C": set()})
        self.advance("Union")
        self.check("owner_field_user_role_union_deduplicated", all_users)
        for client in self.clients.values():
            pages = [self.listing(client, page_length=1, limit_start=index) for index in range(3)]
            ensure(all(len(row["items"]) == 1 for row in pages), "Incomplete indexed pagination")
            ensure({row["items"][0]["reference_name"] for row in pages} == names, "Pagination repeated or skipped source")
        self.advance("NoSelf")
        self.check("native_self_approval_blocked", {actor: names - own[actor] for actor in self.users})
        self.transition("A", "Hidden", client=self.clients["A"], denied=True)
        self.advance("Hidden")
        self.check("configured_hidden_stage", nobody)
        self.advance("User")
        only_c = {"A": set(), "B": set(), "C": names}
        self.check("specific_user", only_c)
        unread_before = {
            actor: self.admin.rows("Notification Log", {"for_user": user}, fields=["name", "read"])
            for actor, user in self.users.items()
        }
        self.update("User", self.users["C"], {"roles": [{"role": self.review_role}]})
        self.check("read_permission_revoked_no_role_broadcast", nobody)
        self.update("User", self.users["C"], {"roles": [{"role": self.review_role}, {"role": self.read_role}]})
        self.check("read_permission_restored", only_c)
        if self.args.policy_edit_case:
            for hidden, expected in ((1, nobody), (0, only_c)):
                doc = self.admin.doc("Workflow", self.workflow)
                for state in doc["states"]:
                    if state["state"] == self.prefix + " User":
                        state[HIDE] = hidden
                self.update("Workflow", self.workflow, {"states": doc["states"]})
                self.check("dynamic_hide" if hidden else "dynamic_unhide", expected)
        self.transition("A", "End", client=self.clients["C"])
        self.check("completed_action_not_resurrected", {"A": set(), "B": set(), "C": names - own["A"]})
        self.delete(self.fixture, self.sources["B"])
        self.check("deleted_source_not_resurrected", {"A": set(), "B": set(), "C": own["C"]})
        unread_after = {
            actor: self.admin.rows("Notification Log", {"for_user": user}, fields=["name", "read"])
            for actor, user in self.users.items()
        }
        ensure(unread_after == unread_before, "Approval index operations changed temporary actors' unread notifications")
        self.journal.event("assertion_passed", scenario="unread_notification_state_unchanged",
                           note="Compares only existing temporary-actor notification rows; does not create or mark a notification read",
                           before=unread_before, after=unread_after)
        # A small concurrency observation, not a synthetic load/stress generator.
        def observe(client):
            return [(client.call("frappe.auth.get_logged_user"), self.listing(client).get("status")) for _ in range(2)]
        with ThreadPoolExecutor(max_workers=3) as executor:
            observations = list(executor.map(observe, self.clients.values()))
        ensure(all(state == "ready" for rows in observations for _, state in rows), "Concurrent normal/indexed read failed")
        self.journal.event("assertion_passed", scenario="three_actor_concurrent_bounded_read", observations=observations)

    def cleanup(self):
        order = {self.fixture: 0, "Workflow": 1, "Workflow State": 2, "Workflow Action Master": 3,
                 "DocType": 4, "User": 5, "Role": 6}
        errors = []
        for target in sorted(self.journal.data["targets"], key=lambda row: order.get(row["doctype"], 99)):
            if target["deleted"]:
                continue
            try:
                self.delete(target["doctype"], target["name"])
            except Exception as exc:
                errors.append({"doctype": target["doctype"], "name": target["name"],
                               "error": str(exc) if isinstance(exc, Failure) else type(exc).__name__})
                break  # Preserve dependencies if a parent remains.
        try:
            ensure(not self.admin.rows("Workflow Action", {"reference_doctype": self.fixture}), "Fixture native actions remain")
        except Exception as exc:
            errors.append({"check": "native_actions", "error": str(exc) if isinstance(exc, Failure) else type(exc).__name__})
        self.journal.data.update(cleanup_errors=errors, cleanup_complete=not errors and all(row["deleted"] for row in self.journal.data["targets"]),
                                 standard_frappe_retention={"deleted_document_recovery_rows": True, "empty_custom_table": True, "sql_drop": False})
        self.journal.flush()
        ensure(self.journal.data["cleanup_complete"], "Cleanup incomplete; inspect journal and use --cleanup-manifest")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--confirm-site", default="")
    parser.add_argument("--namespace", default="namar_test")
    parser.add_argument("--env-file", type=Path, default=ROOT.parent.parent / "erpnex_codex/.env.local")
    parser.add_argument("--state-dir", type=Path, default=Path.home() / ".local/state/namar_test/approval_index_acceptance")
    parser.add_argument("--cleanup-manifest", type=Path)
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--wait-seconds", type=int, default=600)
    parser.add_argument("--policy-edit-case", action="store_true", help="Also test a live hide/unhide edit on the isolated Workflow")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if not args.run:
        print(json.dumps({"mode": "plan", "network": False, "writes": False, "actors": 3, "isolated_sources": 3,
            "changes_site_configuration": False, "invokes_global_rebuild": False,
            "scenarios": ["owner", "user_field_change", "owner_field_user_role_union", "pagination", "native_self_approval",
                          "hidden_stage", "specific_user", "read_revocation_and_restore", "completion", "deletion", "bounded_concurrency"],
            "optional_policy_edit": args.policy_edit_case, "cleanup": "fingerprinted finally + resumable private journal"}, indent=2))
        return 0
    runner = journal = None
    try:
        env = config(args)
        state_dir = private_dir(args.state_dir)
        if args.cleanup_manifest:
            path = args.cleanup_manifest.expanduser().resolve()
            ensure(path.parent == state_dir and not args.cleanup_manifest.is_symlink(), "Cleanup manifest must be in the private state directory")
            ensure(stat.S_IMODE(path.stat().st_mode) == 0o600, "Cleanup manifest must be 0600")
            data = json.loads(path.read_text(encoding="utf-8"))
            ensure(data.get("schema_version") == 1 and data.get("site") == env["site"] and PREFIX_RE.fullmatch(data.get("prefix", "")),
                   "Manifest does not match TEST and fixture schema")
            ensure(isinstance(data.get("targets"), list) and len(data["targets"]) <= MAX_TARGETS, "Invalid manifest targets")
        else:
            prefix = "NAI Smoke " + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + " " + uuid4().hex[:8]
            path = state_dir / (prefix.replace(" ", "-") + ".json")
            data = {"schema_version": 1, "site": env["site"], "prefix": prefix, "created_at": utcnow(),
                    "targets": [], "events": [], "cleanup_complete": False, "tests_passed": False}
        journal = Journal(path, data)
        journal.flush()
        runner = Runner(env, args, journal)
        ensure(runner.admin.call("frappe.auth.get_logged_user") == "Administrator", "TEST Administrator required")
        if args.cleanup_manifest:
            runner.cleanup()
        else:
            try:
                runner.setup()
                runner.exercise()
                journal.data["tests_passed"] = True
                journal.flush()
            finally:
                runner.cleanup()
        print(json.dumps({"status": "passed", "tests_passed": data["tests_passed"], "cleanup_complete": data["cleanup_complete"],
                          "manifest": str(path)}, indent=2))
        return 0
    except Exception as exc:
        error = str(exc) if isinstance(exc, Failure) else type(exc).__name__
        if journal:
            journal.event("failed", error=error)
        print(json.dumps({"status": "failed", "error": error, "manifest": str(journal.path) if journal else None}), file=sys.stderr)
        return 1
    finally:
        if runner:
            for client in (runner.admin, *runner.clients.values()):
                client.session.close()


if __name__ == "__main__":
    raise SystemExit(main())
