"""Durable, versioned approval worklist; expensive evaluation is worker-only.

The three private tables are a projection, never an authority to approve.  HTTP
reads are read-only and fail closed while their generation is incomplete.  The
outbox is the Pending action rows plus the control row's unfinished seed cursor;
Redis/RQ loss therefore cannot lose an invalidation.  Normal source writes dirty
only their own actions.  Policy/permission changes invalidate a generation in one
UPDATE and are expanded into bounded batches by the worker, not by the writer.
"""

from __future__ import annotations

from contextlib import suppress
from hashlib import sha256
import json
from pathlib import Path
import time
from uuid import uuid4

import frappe


PROJECTION_SOURCE_FILES = (
    "approval_index.py", "approval_index_policy.py", "approval_index_cache.py",
    "approval_routing_settings.py", "approval_index_permission_events.py",
)


def _calculate_engine_revision():
    """Fingerprint the running projection implementation, once at import.

    Cloud can deploy pure Python changes without running migrate. No Git,
    subprocess, environment secrets or per-HTTP filesystem reads are involved.
    Labels/length prefixes make concatenation unambiguous across source files.
    """
    digest = sha256()
    digest.update(str(getattr(frappe, "__version__", "")).encode())
    root = Path(__file__).resolve().parent
    for name in PROJECTION_SOURCE_FILES:
        content = (root / name).read_bytes()
        digest.update(name.encode() + b"\0" + str(len(content)).encode() + b"\0" + content)
    return digest.hexdigest()


# Immutable in each process: an old worker must never claim that it runs a newer
# policy simply because another process changed the shared control record.
ENGINE_REVISION = _calculate_engine_revision()


def _revision_matches(control):
    return bool(control and control.get("engine_revision") == ENGINE_REVISION)


CONTROL = "Namar Approval Index Control"
ACTION = "Namar Approval Index Action"
RECIPIENT = "Namar Approval Index Recipient"
CONTROL_NAME = "current"
SELF_DOCTYPES = frozenset((CONTROL, ACTION, RECIPIENT))
GLOBAL_DEPENDENCIES = frozenset((
    "Workflow", "Workflow State", "User", "User Permission", "Role",
    "Role Profile", "DocType", "Custom DocPerm", "DocPerm",
    "Custom Field", "Property Setter", "Server Script", "Permission Type",
    "User Type", "System Settings", "Domain Settings",
))
ACTION_FIELDS = (
    "name", "status", "reference_doctype", "reference_name", "workflow_state",
    "user", "creation", "modified",
)
MAX_PAGE_LENGTH = 101  # includes the UI's pagination sentinel
SEED_BATCH = 100
ACTION_BATCH = 20
WORKER_SECONDS = 8
MAX_RECIPIENTS = 5000
MAX_RETRIES = 3
MESSAGES = {
    "ready": "",
    "updating": "جار تحديث الموافقات؛ سيظهر العدد عند اكتمال التحديث.",
    "error": "تعذر تجهيز بعض الموافقات. راجع مسؤول النظام؛ لم تُعرض أرقام غير مؤكدة.",
    "disabled": "فهرس الموافقات غير مفعّل.",
}


def enabled() -> bool:
    return frappe.conf.get("followup_approval_index_enabled") in (True, 1, "1")


def build_enabled() -> bool:
    # Shadow build is independent of HTTP serving. Native approvals stay in use
    # until an administrator explicitly enables the separately verified index.
    return enabled() or frappe.conf.get("followup_approval_index_build_enabled") in (True, 1, "1")


def _cache():
    return frappe.cache() if callable(frappe.cache) else frappe.cache


def _control(*, for_update=False):
    rows = frappe.db.sql(
        f"SELECT * FROM `tab{CONTROL}` WHERE name=%s" + (" FOR UPDATE" if for_update else ""),
        (CONTROL_NAME,), as_dict=True,
    )
    return rows[0] if rows else None


def _state(state, epoch=None, **extra):
    return {"state": state, "generation": epoch, "message": MESSAGES[state], **extra}


def _assert_user(user):
    # This module is internal, but explicit checks prevent accidental future
    # exposure of the projection through a new whitelisted wrapper.
    if not user or user == "Guest" or user != frappe.session.user:
        frappe.throw("الموافقات متاحة للمستخدم المسجل فقط.", frappe.PermissionError)


def _cohort_clause(user):
    if not user or user == "Administrator":
        return "", ()
    # A changed action can acquire new recipients, so old recipient rows alone
    # are insufficient. Its native permitted-role cohort is a conservative
    # superset, resolved by indexed membership joins, not per-user document loads.
    return f""" AND (
        EXISTS (SELECT 1 FROM `tab{RECIPIENT}` old_r
                WHERE old_r.action_name=a.name AND old_r.for_user=%s)
        OR EXISTS (SELECT 1 FROM `tabWorkflow Action` w WHERE w.name=a.name
            AND w.status='Open' AND (w.user=%s OR EXISTS (
                SELECT 1 FROM `tabWorkflow Action Permitted Role` p
                WHERE p.parent=w.name AND (p.role IN ('All','Guest','Desk User') OR EXISTS (
                    SELECT 1 FROM `tabHas Role` h WHERE h.parenttype='User'
                      AND h.parent=%s AND h.role=p.role
                ))
            )))
    )""", (user, user, user)


def _ready_state(user=None):
    if not build_enabled():
        return _state("disabled")
    control = _control()
    if not _revision_matches(control) or not control.get("scan_complete"):
        return _state("updating", control.get("epoch") if control else None)
    epoch = int(control["epoch"])
    # Indexed existence probes, no business document loads and no per-user
    # evaluator. Only a pending action's possible native recipient cohort waits;
    # unrelated employees keep their ready counters during ordinary edits.
    cohort, cohort_values = _cohort_clause(user)
    pending = frappe.db.sql(
        f"SELECT a.name FROM `tab{ACTION}` a WHERE state='Pending' AND epoch=%s" + cohort + " LIMIT 1",
        (epoch, *cohort_values),
    )
    if pending:
        return _state("updating", epoch)
    failed = frappe.db.sql(
        f"SELECT a.name FROM `tab{ACTION}` a WHERE state='Error' AND epoch=%s" + cohort + " LIMIT 1",
        (epoch, *cohort_values),
    )
    return _state("error" if failed else "ready", epoch)


def _join_sql():
    # The native status join additionally handles native direct-SQL completion
    # and deletion (these do not invoke Workflow Action document hooks).
    return f"""
        FROM `tab{RECIPIENT}` r
        INNER JOIN `tab{ACTION}` a ON a.name=r.action_name
        INNER JOIN `tabWorkflow Action` w ON w.name=a.name AND w.status='Open'
        WHERE r.for_user=%(user)s AND r.epoch=%(epoch)s
          AND a.epoch=r.epoch AND a.state='Ready'
          AND a.built_revision=a.requested_revision
          AND r.revision=a.built_revision
    """


def read_counts(user, *, verify_current=True):
    _assert_user(user)
    state = _ready_state(user)
    if state["state"] != "ready":
        return {**state, "open": None}
    rows = frappe.db.sql(
        "SELECT COUNT(*) " + _join_sql(),
        {"user": user, "epoch": state["generation"]},
    )
    if verify_current and not verify_snapshot(state["generation"]):
        return {**_state("updating", state["generation"]), "open": None}
    return {**state, "open": int(rows[0][0] or 0)}


def read_page(user, search="", search_field="all", start=0, page_length=25):
    _assert_user(user)
    # The service fences the full page only after its bounded native permission
    # revalidation/hydration. Do not hold the control lock during that work.
    counts = read_counts(user, verify_current=False)
    result = {**counts, "items": [], "total": counts["open"]}
    if counts["state"] != "ready":
        return result
    fields = {
        "document": ("reference_name",), "doctype": ("reference_doctype",),
        "state": ("workflow_state",),
        "all": ("reference_doctype", "reference_name", "workflow_state"),
    }
    if search_field not in fields:
        frappe.throw("خيار البحث في الموافقات غير صحيح.", frappe.ValidationError)
    search = str(search or "").strip()[:140]
    start = max(0, min(int(start or 0), 100000))
    length = max(1, min(int(page_length or 25), MAX_PAGE_LENGTH))
    params = {"user": user, "epoch": counts["generation"], "start": start, "length": length}
    query = _join_sql()
    if search:
        params["search"] = "%" + search + "%"
        query += " AND (" + " OR ".join(f"w.`{field}` LIKE %(search)s" for field in fields[search_field]) + ")"
        result["total"] = int(frappe.db.sql("SELECT COUNT(*) " + query, params)[0][0])
    rows = frappe.db.sql(
        "SELECT " + ",".join(f"w.`{field}`" for field in ACTION_FIELDS)
        + ", a.routing, a.projection, a.built_revision AS _index_revision " + query
        + " ORDER BY w.modified DESC,w.name DESC LIMIT %(length)s OFFSET %(start)s",
        params, as_dict=True,
    )
    for row in rows:
        row["routing"] = json.loads(row.get("routing") or "{}")
        projection = json.loads(row.pop("projection", None) or "{}")
        row["reference_title"] = projection.get("title") or row["reference_name"]
    result["items"] = rows
    return result


def assert_visible(user, action_name, *, with_snapshot=False):
    _assert_user(user)
    state = _ready_state(user)
    if state["state"] != "ready":
        frappe.throw(state["message"], frappe.PermissionError)
    rows = frappe.db.sql(
        "SELECT a.routing,a.built_revision AS revision " + _join_sql() + " AND a.name=%(action)s LIMIT 1",
        {"user": user, "epoch": state["generation"], "action": action_name},
        as_dict=True,
    )
    if not rows:
        frappe.throw("الموافقة غير متاحة لك أو لم تعد مفتوحة.", frappe.PermissionError)
    routing = json.loads(rows[0].get("routing") or "{}")
    if with_snapshot:
        return {"routing": routing, "generation": state["generation"], "revision": rows[0]["revision"]}
    return routing


def verify_snapshot(generation, action_revisions=None):
    """Short current-read fence at the END of an HTTP response construction.

    MariaDB's REPEATABLE READ otherwise reuses the first SELECT snapshot. These
    locking reads see the latest committed generation/revisions, and keep them
    stable until the request transaction ends. Acquire control then actions in
    the same order as worker publication. Never call before source hydration.
    Counts are exact for their request snapshot; this additionally prevents a
    permission-generation revocation committed mid-request from leaking a count.
    """
    try:
        epoch = int(generation)
        revisions = dict(action_revisions or {})
        if epoch < 1 or len(revisions) > MAX_PAGE_LENGTH:
            return False
        for name, revision in revisions.items():
            if not isinstance(name, str) or not name or len(name) > 140 or int(revision) < 1:
                return False
    except (TypeError, ValueError):
        return False
    control = frappe.db.sql(
        f"SELECT epoch,scan_complete,engine_revision FROM `tab{CONTROL}` WHERE name=%s LOCK IN SHARE MODE",
        (CONTROL_NAME,), as_dict=True,
    )
    if not control or not _revision_matches(control[0]) or int(control[0]["epoch"]) != epoch or not control[0].get("scan_complete"):
        return False
    if not revisions:
        return True
    rows = frappe.db.sql(
        f"SELECT a.name,a.epoch,a.state,a.built_revision,a.requested_revision,w.status AS native_status "
        f"FROM `tab{ACTION}` a LEFT JOIN `tabWorkflow Action` w ON w.name=a.name "
        "WHERE a.name IN (" + ",".join(["%s"] * len(revisions)) + ") LOCK IN SHARE MODE",
        tuple(revisions), as_dict=True,
    )
    if len(rows) != len(revisions):
        return False
    return all(
        int(row["epoch"]) == epoch and row["state"] == "Ready" and row["native_status"] == "Open"
        and int(row["built_revision"]) == int(row["requested_revision"]) == int(revisions[row["name"]])
        for row in rows
    )


def _emit_changed():
    # No counts, names, titles or users in the broadcast. Each authenticated
    # browser fetches its own authorized projection after this invalidation.
    if enabled():
        frappe.publish_realtime("namar_approvals_changed", {}, after_commit=True)


def _enqueue(*, successor=False, after_commit=True):
    if not build_enabled():
        return
    frappe.enqueue(
        "namar_test.followups.approval_index.process_pending",
        queue=frappe.conf.get("followup_approval_index_queue") or "long",
        timeout=90, enqueue_after_commit=after_commit,
        job_id="namar-approval-index-" + (uuid4().hex if successor else "dispatch"),
        deduplicate=True,
    )


def _schedule():
    # Coalesce all invalidations in this transaction, including nested native
    # Workflow Action insert + parent on_change. Queues are not the durable log.
    if getattr(frappe.local, "namar_approval_index_dispatch", False):
        return
    frappe.local.namar_approval_index_dispatch = True
    # A queue outage must not turn a successfully committed business save into a
    # 500 response. The SQL outbox is already durable; the minute tick retries.
    frappe.db.after_commit.add(_dispatch_after_commit)
    frappe.db.after_rollback.add(_reset_dispatch)


def _reset_dispatch():
    frappe.local.namar_approval_index_dispatch = False
    frappe.local.namar_approval_index_epoch_dirty = False


def _dispatch_after_commit():
    _reset_dispatch()
    try:
        _enqueue(after_commit=False)
        if enabled():
            frappe.publish_realtime("namar_approvals_changed", {}, after_commit=False)
    except Exception:
        # Logging itself may rely on unavailable infrastructure. Never discard
        # the pending SQL rows or compromise the successful source transaction.
        with suppress(Exception):
            frappe.log_error(title="Approval index dispatch deferred; durable recovery pending")


def request_rebuild(reason="configuration_changed"):
    """Internal transaction-safe invalidation, never a synchronous rebuild."""
    if not build_enabled():
        return {"state": "disabled"}
    if getattr(frappe.local, "namar_approval_index_epoch_dirty", False):
        return _state("updating", _control()["epoch"])
    frappe.db.sql(
        f"UPDATE `tab{CONTROL}` SET epoch=epoch+1, scan_cursor='', scan_complete=0, "
        "requested_at=NOW(6), finished_at=NULL,last_error=%s,modified=NOW(6) WHERE name=%s",
        (str(reason)[:140], CONTROL_NAME),
    )
    frappe.local.namar_approval_index_epoch_dirty = True
    _schedule()
    return _state("updating", _control()["epoch"])


def _adopt_runtime_revision():
    """Scheduler/migration/explicit rebuild: adopt code and invalidate once.

    Ordinary evaluator workers NEVER call this function. An older in-flight
    worker may finish its source read but cannot seed or publish into this stamp.
    """
    # A still-running scheduler from before an in-place source update must not
    # replace a newer stamp with its old imported implementation. This disk
    # check runs only at adoption, never in HTTP count/page reads or evaluators.
    if _calculate_engine_revision() != ENGINE_REVISION:
        return False
    control = _control(for_update=True)
    if not control or _revision_matches(control):
        return False
    frappe.db.sql(
        f"UPDATE `tab{CONTROL}` SET epoch=epoch+1,engine_revision=%s,scan_cursor='',scan_complete=0,"
        "requested_at=NOW(6),finished_at=NULL,last_error='engine_revision_changed',modified=NOW(6) WHERE name=%s",
        (ENGINE_REVISION, CONTROL_NAME),
    )
    frappe.local.namar_approval_index_epoch_dirty = True
    _schedule()
    return True


def invalidate_action(action_name):
    """Dirty precisely one existing native action in the source transaction."""
    if not build_enabled() or not action_name:
        return
    control = _control()
    if not control:
        return
    _dirty_where("w.name=%s", (action_name,), int(control["epoch"]))
    _schedule()


def _dirty_where(where, values, epoch):
    # where is exclusively one of the static predicates in this module. INSERT
    # SELECT handles multiple historical actions without a Python hydration loop.
    frappe.db.sql(
        f"""INSERT INTO `tab{ACTION}`
            (name,creation,modified,owner,modified_by,docstatus,idx,
             requested_revision,built_revision,epoch,state,reference_doctype,
             reference_name,workflow_state,source_modified,retry_count)
            SELECT w.name,NOW(6),NOW(6),'Administrator','Administrator',0,0,
                   1,0,%s,'Pending',w.reference_doctype,w.reference_name,
                   w.workflow_state,w.modified,0
            FROM `tabWorkflow Action` w WHERE {where}
            ON DUPLICATE KEY UPDATE requested_revision=requested_revision+1,
              epoch=VALUES(epoch),state='Pending',modified=NOW(6),retry_count=0,
              retry_after=NULL,reason='',reference_doctype=VALUES(reference_doctype),
              reference_name=VALUES(reference_name),workflow_state=VALUES(workflow_state)
        """,
        (epoch, *values),
    )


def _purge_reference(doctype, name):
    if not frappe.db.exists(ACTION, {"reference_doctype": doctype, "reference_name": name}):
        return False
    frappe.db.sql(
        f"DELETE r FROM `tab{RECIPIENT}` r INNER JOIN `tab{ACTION}` a ON a.name=r.action_name "
        "WHERE a.reference_doctype=%s AND a.reference_name=%s", (doctype, name),
    )
    frappe.db.sql(
        f"DELETE FROM `tab{ACTION}` WHERE reference_doctype=%s AND reference_name=%s",
        (doctype, name),
    )
    return True


def _dirty_reference(doctype, name):
    if not doctype or not name or not frappe.db.exists(
        "Workflow Action", {"reference_doctype": doctype, "reference_name": name}
    ):
        return
    control = _control()
    if control:
        _dirty_where("w.reference_doctype=%s AND w.reference_name=%s", (doctype, name), int(control["epoch"]))
        _schedule()


def on_document_change(doc, method=None):
    """Cheap transactional invalidation; never load a business Doc here."""
    if not build_enabled() or doc.doctype in SELF_DOCTYPES or getattr(frappe.flags, "in_migrate", False):
        return
    doctype, name = doc.doctype, doc.name
    if doctype == "DocShare":
        # Assignment auto-sharing is common. Only the shared reference changes
        # visibility; it must never restart the whole site's projection.
        _dirty_reference(doc.get("share_doctype"), doc.get("share_name"))
        return
    if doctype in GLOBAL_DEPENDENCIES:
        # User saving last_active/IP does not change authorization. User.on_update
        # also handles roles/profile changes; native db_set on_change is skipped
        # only when the document provides trustworthy old-value comparison.
        if doctype == "User" and method in ("on_change", "on_update") and hasattr(doc, "has_value_changed"):
            before = doc.get_doc_before_save()
            permission_fields = ("enabled", "user_type", "roles", "role_profile_name", "user_permissions")
            if before is not None and not any(doc.has_value_changed(field) for field in permission_fields):
                return
        request_rebuild("permission_or_policy_changed")
        return
    meta = getattr(doc, "meta", None)
    if meta and callable(getattr(meta, "is_nested_set", None)) and meta.is_nested_set():
        # Moving a Warehouse/Territory/etc. can change descendant UserPermission
        # matches on unrelated business references. This is not a local edit.
        tree_permissions = frappe.db.sql(
            "SELECT name FROM `tabUser Permission` WHERE allow=%s "
            "AND (hide_descendants=0 OR hide_descendants IS NULL) LIMIT 1", (doctype,),
        )
        if tree_permissions:
            if method in ("on_trash", "after_delete"):
                _purge_reference(doctype, name)
            request_rebuild("permission_tree_changed")
            return
    if _nonstandard_user_type_changed(doc):
        request_rebuild("user_type_mapping_changed")
        return
    if doctype == "Workflow Action":
        if method in ("on_trash", "after_delete"):
            frappe.db.sql(f"DELETE FROM `tab{RECIPIENT}` WHERE action_name=%s", (name,))
            frappe.db.sql(f"DELETE FROM `tab{ACTION}` WHERE name=%s", (name,))
            _schedule()
        else:
            invalidate_action(name)
        return
    if method in ("on_trash", "after_delete"):
        changed = _purge_reference(doctype, name)
        # Native may already have deleted the Workflow Actions. No upsert on a
        # delete event, and no delayed job can resurrect the deleted index row.
        if changed:
            _schedule()
        return
    # One indexed native existence probe, even when the doctype has no workflow.
    _dirty_reference(doctype, name)


def _nonstandard_user_type_changed(doc):
    # Mirrors Frappe's cached metadata lookup, not an all-document scan. Its
    # native on_update handler can db.set_value UserPermission without hooks.
    if not hasattr(doc, "get"):
        return False
    from frappe.core.doctype.user_type.user_type import get_non_standard_user_types
    mapping = _cache().get_value("non_standard_user_types", get_non_standard_user_types) or {}
    for doctype, field in mapping.values():
        if doc.doctype != doctype:
            continue
        before = doc.get_doc_before_save() if hasattr(doc, "get_doc_before_save") else None
        if before is None or before.get(field) != doc.get(field):
            return True
    return False


def on_document_rename(doc, method=None, *args, **kwargs):
    # Native rename rewrites links in SQL, so dependent references cannot be
    # inferred from the renamed document's own Workflow Actions alone.
    if build_enabled() and doc.doctype not in SELF_DOCTYPES:
        request_rebuild("reference_renamed")


def _seed_batch(control):
    if not _revision_matches(control):
        return
    epoch = int(control["epoch"])
    rows = frappe.db.sql(
        "SELECT name FROM `tabWorkflow Action` WHERE status='Open' AND name>%s ORDER BY name LIMIT %s",
        (control.get("scan_cursor") or "", SEED_BATCH), as_dict=True,
    )
    # Serialize only the short seed publication against a concurrent policy
    # update. No recipient/document evaluation while holding the control lock.
    locked = _control(for_update=True)
    if not _revision_matches(locked) or int(locked["epoch"]) != epoch:
        return
    if rows:
        names = tuple(row["name"] for row in rows)
        _dirty_where("w.name IN (" + ",".join(["%s"] * len(names)) + ")", names, epoch)
    frappe.db.sql(
        f"UPDATE `tab{CONTROL}` SET scan_cursor=%s,scan_complete=%s,modified=NOW(6) WHERE name=%s AND epoch=%s",
        (rows[-1]["name"] if rows else control.get("scan_cursor") or "", int(len(rows) < SEED_BATCH), CONTROL_NAME, epoch),
    )


def _dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _publish(snapshot, evaluation):
    """Atomic compare-and-publish: obsolete jobs cannot resurrect recipients."""
    epoch = int(snapshot["epoch"])
    control = _control(for_update=True)
    rows = frappe.db.sql(
        f"SELECT requested_revision,epoch FROM `tab{ACTION}` WHERE name=%s FOR UPDATE",
        (snapshot["name"],), as_dict=True,
    )
    if not publication_matches(control, rows[0] if rows else None, snapshot):
        return False
    recipients = tuple(sorted(set(evaluation.get("recipients") or ())))
    state = str(evaluation.get("state") or "error").title()
    if state not in ("Ready", "Excluded", "Error") or len(recipients) > MAX_RECIPIENTS:
        state, recipients = "Error", ()
        evaluation = {**evaluation, "reason": "invalid_or_oversized_recipient_projection"}
    if state != "Ready":
        recipients = ()
    # A correctly resolved policy with no eligible explicit recipient is a
    # settled routing exception, not an endlessly retrying technical failure.
    if state == "Error" and evaluation.get("reason") in ("no_eligible_recipient", "no_eligible_recipients"):
        state = "Excluded"
    frappe.db.sql(f"DELETE FROM `tab{RECIPIENT}` WHERE action_name=%s", (snapshot["name"],))
    if recipients:
        values = []
        for user in recipients:
            values.extend((recipient_key(snapshot["name"], user), snapshot["name"], user, epoch, int(snapshot["requested_revision"])))
        frappe.db.sql(
            f"INSERT INTO `tab{RECIPIENT}` (name,action_name,for_user,epoch,revision,creation,modified,owner,modified_by,docstatus,idx) VALUES "
            + ",".join(["(%s,%s,%s,%s,%s,NOW(6),NOW(6),'Administrator','Administrator',0,0)"] * len(recipients)),
            tuple(values),
        )
    # Store presentation data only; never a full business document/child table.
    projection = {key: evaluation.get(key) for key in ("title", "party", "subject", "fingerprint", "reference_modified", "workflow_name", "workflow_modified")}
    frappe.db.sql(
        f"UPDATE `tab{ACTION}` SET built_revision=requested_revision,state=%s,projection=%s,routing=%s,reason=%s,"
        "retry_after=NULL,source_modified=%s,modified=NOW(6) WHERE name=%s",
        (state, _dump(projection), _dump(evaluation.get("routing") or {}), str(evaluation.get("reason") or "")[:500],
         evaluation.get("action_modified") or snapshot.get("source_modified"), snapshot["name"]),
    )
    return True


def recipient_key(action, user):
    return sha256((str(action) + "\0" + str(user)).encode()).hexdigest()


def publication_matches(control, current, snapshot):
    """Pure CAS predicate also exercised without a Frappe installation."""
    return bool(
        control and current
        and _revision_matches(control)
        and snapshot.get("engine_revision") == ENGINE_REVISION
        and int(control["epoch"]) == int(snapshot["epoch"]) == int(current["epoch"])
        and int(current["requested_revision"]) == int(snapshot["requested_revision"])
    )


def _technical_failure(snapshot):
    # No exception text/business data is published to users. Keep a bounded
    # durable retry, then surface unavailable rather than retrying forever.
    retry = int(snapshot.get("retry_count") or 0) + 1
    state = "Error" if retry >= MAX_RETRIES else "Pending"
    frappe.db.sql(
        f"UPDATE `tab{ACTION}` SET state=%s,retry_count=%s,reason='evaluation_failed',"
        "retry_after=DATE_ADD(NOW(6),INTERVAL 60 SECOND),modified=NOW(6) "
        "WHERE name=%s AND epoch=%s AND requested_revision=%s",
        (state, retry, snapshot["name"], snapshot["epoch"], snapshot["requested_revision"]),
    )


def _has_pending_work():
    control = _control()
    if not _revision_matches(control):
        return False
    if not control.get("scan_complete"):
        return True
    return bool(frappe.db.sql(
        f"SELECT name FROM `tab{ACTION}` WHERE epoch=%s AND state='Pending' "
        "AND (retry_after IS NULL OR retry_after<=NOW(6)) LIMIT 1", (control["epoch"],),
    ))


def process_pending():
    """One bounded worker slice; scheduler recovers lost enqueue/crashed workers."""
    if not build_enabled():
        return {"state": "disabled", "processed": 0}
    cache = _cache()
    lock = cache.lock(cache.make_key("namar-approval-index-worker"), timeout=120, blocking_timeout=0)
    if not lock.acquire(blocking=False):
        return {"state": "busy", "processed": 0}
    processed = 0
    more = False
    started = time.monotonic()
    try:
        control = _control()
        if not control:
            return {"state": "schema_missing", "processed": 0}
        if not _revision_matches(control):
            return {"state": "engine_revision_mismatch", "processed": 0}
        if not control.get("scan_complete"):
            _seed_batch(control)
            frappe.db.commit()
        from namar_test.followups.approval_index_policy import ApprovalIndexPolicyEvaluator
        from namar_test.followups.approval_index_cache import fresh_permission_cache
        evaluator = ApprovalIndexPolicyEvaluator(frappe)

        control = _control()
        if not _revision_matches(control):
            return {"state": "engine_revision_mismatch", "processed": 0}
        pending = frappe.db.sql(
            f"SELECT * FROM `tab{ACTION}` WHERE epoch=%s AND state='Pending' "
            "AND (retry_after IS NULL OR retry_after<=NOW(6)) ORDER BY modified,name LIMIT %s",
            (control["epoch"], ACTION_BATCH), as_dict=True,
        )
        # End the candidate-selection snapshot. Each action evaluates one current
        # committed source snapshot, with its own CAS and publication transaction.
        frappe.db.commit()
        with fresh_permission_cache(frappe):
            for snapshot in pending:
                if time.monotonic() - started >= WORKER_SECONDS:
                    break
                try:
                    snapshot["engine_revision"] = control["engine_revision"]
                    evaluation = evaluator.evaluate_action(snapshot["name"])
                    _publish(snapshot, evaluation)
                    frappe.db.commit()
                    processed += 1
                except Exception:
                    frappe.db.rollback()
                    _technical_failure(snapshot)
                    frappe.db.commit()
                    frappe.log_error(title="Approval index evaluation failed")
        more = _has_pending_work()
        if not more:
            state = _ready_state()
            if state["state"] == "ready":
                frappe.db.sql(
                    f"UPDATE `tab{CONTROL}` SET finished_at=NOW(6),last_error='' WHERE name=%s AND epoch=%s",
                    (CONTROL_NAME, state["generation"]),
                )
        _emit_changed()
        frappe.db.commit()
    finally:
        with suppress(Exception):
            lock.release()
    if more:
        # Different id permits a successor while the current RQ job is finishing;
        # the distributed lock still admits only one evaluator across workers.
        _enqueue(successor=True)
        frappe.db.commit()
    return {"state": "updating" if more else _ready_state()["state"], "processed": processed}


def recover_pending():
    """Minute scheduler: cheap durable-outbox recovery, never evaluate inline."""
    if any(frappe.conf.get(key) in (True, 1, "1") for key in (
        "maintenance_mode", "pause_scheduler", "disable_scheduler",
    )):
        return
    if build_enabled():
        # A Pull deployment does not invoke after_migrate. Detection is isolated
        # here; HTTP reads only report updating until the new stamp is built.
        if _adopt_runtime_revision():
            return
        if _has_pending_work():
            _enqueue()


def invalidate_after_migrate():
    if build_enabled():
        if not _adopt_runtime_revision():
            request_rebuild("application_or_metadata_changed")


@frappe.whitelist(methods=["POST"])
def rebuild():
    frappe.only_for("System Manager")
    if build_enabled() and _adopt_runtime_revision():
        return _state("updating", _control()["epoch"])
    return request_rebuild("administrator_requested")


@frappe.whitelist()
def status():
    frappe.only_for("System Manager")
    if not build_enabled():
        return {**_state("disabled"), "serving_enabled": False, "build_enabled": False,
                "runtime_engine_revision": ENGINE_REVISION, "stored_engine_revision": None}
    control = _control()
    states = frappe.db.sql(
        f"SELECT state,COUNT(*) AS count FROM `tab{ACTION}` WHERE epoch=%s GROUP BY state",
        (control["epoch"],), as_dict=True,
    ) if control else []
    reasons = frappe.db.sql(
        f"SELECT state,reason,COUNT(*) AS count FROM `tab{ACTION}` "
        "WHERE epoch=%s AND reason!='' GROUP BY state,reason",
        (control["epoch"],), as_dict=True,
    ) if control else []
    return {
        **_ready_state(), "control": control, "states": states, "reasons": reasons,
        "serving_enabled": enabled(), "build_enabled": build_enabled(),
        "runtime_engine_revision": ENGINE_REVISION,
        "stored_engine_revision": control.get("engine_revision") if control else None,
    }
