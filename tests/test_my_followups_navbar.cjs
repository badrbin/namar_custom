const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const root = path.join(__dirname, "..");
const assetPath = path.join(root, "namar_test", "public", "js", "my_followups_navbar.bundle.js");
const cssPath = path.join(root, "namar_test", "public", "css", "my_followups_navbar.bundle.css");
const hooksPath = path.join(root, "namar_test", "hooks.py");
const source = fs.readFileSync(assetPath, "utf8");
const css = fs.readFileSync(cssPath, "utf8");
const hooks = fs.readFileSync(hooksPath, "utf8");

const testHooks = { skip_auto_start: true };
const calls = [];
const pendingResolvers = [];
const assignedHrefs = [];
const scheduledTimers = new Map();
const realtimeHandlers = new Map();
let timerSerial = 0;
let now = Date.now();
const linkState = { active: null, ariaCurrent: null };
const linkStub = {
  off: () => linkStub,
  on: () => linkStub,
  remove: () => linkStub,
  toggleClass: (_name, active) => {
    linkState.active = active;
    return linkStub;
  },
  attr: (_name, value) => {
    linkState.ariaCurrent = value;
    return linkStub;
  },
  removeAttr: () => {
    linkState.ariaCurrent = null;
    return linkStub;
  },
};
const context = {
  console,
  Date: { now: () => now },
  Math: Object.assign(Object.create(Math), { random: () => 0 }),
  Promise,
  setTimeout: (callback, delay) => {
    const id = ++timerSerial;
    scheduledTimers.set(id, { callback, delay });
    return id;
  },
  clearTimeout: (id) => scheduledTimers.delete(id),
  setInterval: () => ++timerSerial,
  clearInterval: () => {},
  document: { hidden: false },
  location: { assign: (href) => assignedHrefs.push(href) },
  frappe: {
    realtime: {
      on: (event, handler) => realtimeHandlers.set(event, handler),
      off: (event, handler) => {
        if (realtimeHandlers.get(event) === handler) realtimeHandlers.delete(event);
      },
    },
    call: (options) => {
      calls.push(options);
      return new Promise((resolve) => {
        pendingResolvers.push(resolve);
      });
    },
  },
  $: () => linkStub,
  __namar_my_followups_navbar_test__: testHooks,
};
context.window = context;
vm.runInNewContext(source, context, { filename: assetPath });

const {
  NamarMyFollowupsNavbar,
  badge_text,
  badge_view,
  is_plain_navigation,
  normalize_counts,
  valid_count,
} = testHooks;

async function main() {
  assert.equal(valid_count(0), true);
  assert.equal(valid_count(99), true);
  assert.equal(valid_count(true), false);
  assert.equal(valid_count("3"), false);
  assert.equal(valid_count(-1), false);

  assert.equal(badge_text(0), "0");
  assert.equal(badge_text(99), "99");
  assert.equal(badge_text(100), "99+");
  assert.deepEqual(
    { ...normalize_counts({ message: {
      counts: { mentions: 2, followups: 9, approvals: 6, total: 17 },
      attention_counts: { mentions: 2, followups: 4, approvals: 6, total: 12, unread: 999 },
    } }) },
    { mentions: 2, followups: 4, approvals: 6, total: 12, approval_status: "ready" }
  );
  assert.equal(normalize_counts({ counts: { mentions: 2, followups: 4, approvals: 6, total: 12 } }), null);
  assert.equal(normalize_counts({ attention_counts: { mentions: 2, followups: 4, approvals: 6, total: 11 } }), null);
  assert.equal(normalize_counts({ attention_counts: { mentions: true, followups: 4, approvals: 6, total: 11 } }), null);
  assert.equal(normalize_counts({ attention_counts: { mentions: 2, followups: -1, approvals: 6, total: 7 } }), null);
  for (const approval_status of ["updating", "error"]) {
    const normalized = normalize_counts({ approval_status,
      attention_counts: { mentions: 2, followups: 4, approvals: null, total: null } });
    assert.deepEqual({ ...normalized }, { mentions: 2, followups: 4, approvals: null, total: null, approval_status });
    const unknown = badge_view(normalized);
    assert.equal(unknown.sources[2].visible, true);
    assert.equal(unknown.sources[2].text, approval_status === "error" ? "—" : "…");
    assert.equal(unknown.sources[2].count, null);
    assert.doesNotMatch(unknown.sources[2].label, /: 0|: null/);
    assert.equal(unknown.sources[0].text, "2");
    assert.equal(unknown.sources[1].text, "4");
    assert.equal(normalize_counts({ approval_status,
      attention_counts: { mentions: 2, followups: 4, approvals: 0, total: 6 } }), null);
    assert.equal(normalize_counts({ approval_status,
      attention_counts: { mentions: 2, followups: 4, approvals: 100, total: 106 } }), null);
  }
  assert.equal(normalize_counts({ approval_status: "ready",
    attention_counts: { mentions: 2, followups: 4, approvals: null, total: null } }), null);
  assert.equal(normalize_counts({ approval_status: "other",
    attention_counts: { mentions: 2, followups: 4, approvals: 0, total: 6 } }), null);

  const view = badge_view({ mentions: 0, followups: 5, approvals: 100, total: 105 });
  assert.equal(view.visible, true);
  assert.equal(view.sources[0].visible, false);
  assert.equal(view.sources[0].text, "");
  assert.equal(view.sources[0].label, "الوارد الذي يحتاج قرارًا: 0");
  assert.equal(view.sources[1].visible, true);
  assert.equal(view.sources[1].text, "5");
  assert.equal(view.sources[1].href, "/app/my-followups?source=followups&bucket=overdue");
  assert.equal(view.sources[2].text, "99+");
  assert.equal(view.sources[2].label, "الموافقات المعلقة: 100");
  assert.match(view.status_label, /المتابعات المتأخرة: 5/);
  assert.equal(badge_view({ mentions: 0, followups: 0, approvals: 0, total: 0 }).visible, false);
  assert.equal(badge_view(null), null);
  assert.equal(is_plain_navigation({ button: 0 }), true);
  assert.equal(is_plain_navigation({ button: 0, metaKey: true }), false);
  assert.equal(is_plain_navigation({ button: 0, ctrlKey: true }), false);
  assert.equal(is_plain_navigation({ button: 1 }), false);

  const controller = new NamarMyFollowupsNavbar();
  controller.render = () => {};
  const navigationHandlers = {};
  const navigationNode = {
    off: () => navigationNode,
    on: (_eventName, selector, handler) => {
      navigationHandlers[selector] = handler;
      return navigationNode;
    },
  };
  controller.bind_navigation(navigationNode);
  assert.equal(typeof navigationHandlers[".namar-my-followups-link"], "function");
  assert.equal(typeof navigationHandlers[".namar-my-followups-source-badge"], "function");
  let prevented = false;
  let stopped = false;
  navigationHandlers[".namar-my-followups-link"]({
    button: 0,
    currentTarget: { getAttribute: () => "/app/my-followups" },
    preventDefault: () => { prevented = true; },
    stopImmediatePropagation: () => { stopped = true; },
    stopPropagation: () => {},
  });
  assert.equal(prevented, true);
  assert.equal(stopped, true);
  assert.deepEqual(assignedHrefs, ["/app/my-followups"]);
  navigationHandlers[".namar-my-followups-link"]({
    button: 0,
    ctrlKey: true,
    currentTarget: { getAttribute: () => "/app/my-followups" },
  });
  assert.deepEqual(assignedHrefs, ["/app/my-followups"]);
  context.frappe.router = { current_route: null };
  assert.doesNotThrow(() => controller.update_active_state());
  assert.equal(linkState.active, false);
  assert.equal(linkState.ariaCurrent, null);
  context.frappe.router.current_route = ["my-followups"];
  controller.update_active_state();
  assert.equal(linkState.active, true);
  assert.equal(linkState.ariaCurrent, "page");
  context.frappe.router.current_route = ["Form", "Material Request", "MREQ-05408"];
  controller.update_active_state();
  assert.equal(linkState.active, false);
  assert.equal(linkState.ariaCurrent, null);
  assert.equal(controller.load_failed, false);
  const first = controller.refresh(true);
  const duplicate = controller.refresh();
  await Promise.resolve();
  assert.equal(first, duplicate);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].method, "namar_test.followups.api.get_my_followups_counts");
  assert.equal(calls[0].type, "GET");
  assert.deepEqual({ ...calls[0].args }, {});
  pendingResolvers.shift()({ message: {
    counts: { mentions: 1, followups: 7, approvals: 5, total: 13 },
    attention_counts: { mentions: 1, followups: 3, approvals: 5, total: 9 },
  } });
  await first;
  assert.equal(controller.load_failed, false);
  assert.deepEqual({ ...controller.counts }, { mentions: 1, followups: 3, approvals: 5, total: 9, approval_status: "ready" });

  controller.merge_source_count({ source: "mentions", count: 4 });
  assert.deepEqual({ ...controller.counts }, { mentions: 4, followups: 3, approvals: 5, total: 12, approval_status: "ready" });
  controller.merge_source_count({ source: "mentions", count: true });
  assert.equal(controller.counts.mentions, 4);

  const raceController = new NamarMyFollowupsNavbar();
  raceController.render = () => {};
  raceController.counts = { mentions: 1, followups: 1, approvals: 1, total: 3 };
  raceController.last_loaded_at = 0;
  const staleRequest = raceController.refresh(true);
  await Promise.resolve();
  raceController.merge_source_count({ source: "mentions", count: 8, force: true });
  assert.equal(raceController.force_after_pending, true);
  pendingResolvers.shift()({ message: {
    counts: { mentions: 2, followups: 9, approvals: 2, total: 13 },
    attention_counts: { mentions: 2, followups: 2, approvals: 2, total: 6 },
  } });
  await staleRequest;
  await Promise.resolve();
  assert.equal(calls.length, 3);
  const latestRequest = raceController.pending;
  pendingResolvers.shift()({ message: {
    counts: { mentions: 8, followups: 10, approvals: 6, total: 24 },
    attention_counts: { mentions: 8, followups: 4, approvals: 6, total: 18 },
  } });
  await latestRequest;
  assert.deepEqual(
    { ...raceController.counts },
    { mentions: 8, followups: 4, approvals: 6, total: 18, approval_status: "ready" }
  );

  const followupEventController = new NamarMyFollowupsNavbar();
  followupEventController.render = () => {};
  followupEventController.counts = { mentions: 1, followups: 2, approvals: 3, total: 6 };
  let forcedRefreshes = 0;
  followupEventController.refresh = (force) => {
    assert.equal(force, true);
    forcedRefreshes += 1;
    return Promise.resolve(null);
  };
  followupEventController.merge_source_count({ source: "followups", count: 99 });
  assert.equal(forcedRefreshes, 0);
  followupEventController.merge_source_count({ source: "followups", count: 99, force: true });
  assert.equal(forcedRefreshes, 1);
  assert.deepEqual(
    { ...followupEventController.counts },
    { mentions: 1, followups: 2, approvals: 3, total: 6 }
  );

  const pendingFollowupController = new NamarMyFollowupsNavbar();
  pendingFollowupController.render = () => {};
  pendingFollowupController.counts = { mentions: 1, followups: 2, approvals: 3, total: 6 };
  pendingFollowupController.pending = Promise.resolve(null);
  pendingFollowupController.merge_source_count({ source: "followups", count: 99, force: true });
  assert.equal(pendingFollowupController.force_after_pending, true);
  assert.equal(pendingFollowupController.counts.followups, 2);

  const indexController = new NamarMyFollowupsNavbar();
  indexController.render = () => {};
  indexController.ensure_node = () => {};
  indexController.start();
  assert.equal(typeof realtimeHandlers.get("namar_approvals_changed"), "function");
  indexController.counts = { mentions: 2, followups: 3, approvals: 9, total: 14, approval_status: "ready" };
  const indexRequest = indexController.refresh(true);
  await Promise.resolve();
  const indexCallsBeforeEvent = calls.length;
  // Minimal, possibly global invalidation payload never contains trusted counts.
  realtimeHandlers.get("namar_approvals_changed")({ approvals: 999 });
  assert.equal(indexController.counts.approvals, null);
  assert.equal(indexController.counts.total, null);
  assert.equal(indexController.counts.approval_status, "updating");
  assert.equal(indexController.counts.mentions, 2);
  const eventTimer = indexController.approval_refresh_timer;
  for (let i = 0; i < 20; i += 1) realtimeHandlers.get("namar_approvals_changed")();
  assert.equal(indexController.approval_refresh_timer, eventTimer);
  assert.equal(scheduledTimers.get(eventTimer).delay, 30000);
  assert.equal(calls.length, indexCallsBeforeEvent);
  pendingResolvers.shift()({ message: {
    attention_counts: { mentions: 5, followups: 4, approvals: 9, total: 18 }, approval_status: "ready",
  } });
  await indexRequest;
  // A response started before invalidation cannot resurrect the old number.
  assert.equal(indexController.counts.approvals, null);
  assert.equal(indexController.counts.approval_status, "updating");
  assert.equal(indexController.counts.mentions, 5);
  assert.equal(indexController.counts.followups, 4);
  indexController.merge_source_count({ source: "mentions", count: 6 });
  assert.equal(indexController.counts.total, null);
  indexController.merge_source_count({ source: "approvals", count: 99 });
  assert.equal(indexController.counts.approvals, null);
  await indexController.refresh(true);
  assert.equal(calls.length, indexCallsBeforeEvent);
  // One coalesced request after the floor accepts a fresh complete generation.
  now += 30000;
  scheduledTimers.get(eventTimer).callback();
  await Promise.resolve();
  const readyIndexRequest = indexController.pending;
  pendingResolvers.shift()({ message: {
    attention_counts: { mentions: 6, followups: 4, approvals: 0, total: 10 }, approval_status: "ready",
  } });
  await readyIndexRequest;
  assert.equal(indexController.counts.approvals, 0);
  assert.equal(indexController.counts.approval_status, "ready");
  assert.equal(badge_view(indexController.counts).sources[2].visible, false);
  assert.equal(indexController.approval_refresh_timer, null);

  // Unknown and failed endpoints cannot silently become zero or retain a number.
  now += 30000;
  const updatingRequest = indexController.refresh(true);
  await Promise.resolve();
  pendingResolvers.shift()({ message: {
    attention_counts: { mentions: 6, followups: 4, approvals: null, total: null }, approval_status: "updating",
  } });
  await updatingRequest;
  assert.equal(indexController.counts.approvals, null);
  assert.equal(scheduledTimers.get(indexController.approval_refresh_timer).delay, 30000);
  now += 30000;
  const unavailableRequest = indexController.refresh(true);
  await Promise.resolve();
  const previousWarn = context.console.warn;
  context.console.warn = () => {};
  pendingResolvers.shift()({ exception: "Not found", http_status_code: 404 });
  await unavailableRequest;
  context.console.warn = previousWarn;
  assert.equal(indexController.counts.approval_status, "error");
  assert.equal(indexController.counts.approvals, null);
  assert.equal(indexController.counts.mentions, 6);
  assert.equal(indexController.counts.followups, 4);
  assert.equal(badge_view(indexController.counts).sources[2].text, "—");
  assert.equal(scheduledTimers.get(indexController.approval_refresh_timer).delay, 60000);
  context.document.hidden = true;
  now += 60000;
  const callsBeforeHidden = calls.length;
  await indexController.refresh(true);
  assert.equal(calls.length, callsBeforeHidden);
  context.document.hidden = false;
  indexController.destroy();
  assert.equal(realtimeHandlers.has("namar_approvals_changed"), false);
  assert.equal(indexController.approval_refresh_timer, null);

  const firstFailureController = new NamarMyFollowupsNavbar();
  firstFailureController.render = () => {};
  firstFailureController.mark_approvals_unavailable("error");
  assert.equal(firstFailureController.counts.mentions, null);
  assert.equal(firstFailureController.counts.followups, null);
  const firstFailureView = badge_view(firstFailureController.counts);
  assert.equal(firstFailureView.sources[0].visible, false);
  assert.equal(firstFailureView.sources[1].visible, false);
  assert.equal(firstFailureView.sources[2].text, "—");
  assert.doesNotMatch(firstFailureView.status_label, /: 0|: null/);

  assert.match(source, /toolbar_setup\$\{EVENT_NAMESPACE\}/);
  assert.doesNotMatch(source, /get_route_str/);
  assert.match(source, /\$\("\.navbar"\)\.find\("\.dropdown-notifications"\)\.first\(\)/);
  assert.doesNotMatch(source, /header \.navbar \.dropdown-notifications/);
  assert.match(source, /href="\/app\/my-followups"/);
  assert.match(source, /href="\$\{meta\.href\}"/);
  assert.match(source, /data-source-badge="\$\{source\}"/);
  assert.match(source, /\["\.namar-my-followups-link", "\.namar-my-followups-source-badge"\]/);
  assert.doesNotMatch(source, /namar-my-followups-source-symbol/);
  assert.doesNotMatch(source, /symbol:\s*"[وتم]"/);
  assert.match(source, /window\.location\.assign\(href\)/);
  assert.match(source, /stopImmediatePropagation/);
  assert.match(source, /attention_counts/);
  assert.match(source, /الوارد/);
  assert.match(source, /الوارد الذي يحتاج قرارًا/);
  assert.match(source, /المتابعات/);
  assert.match(source, /متأخرة/);
  assert.match(source, /الموافقات/);
  assert.match(source, /معلقة/);
  assert.match(source, /namar:my-followups:count-changed/);
  assert.match(source, /جار تحديث العداد/);
  assert.match(source, /تعذر تحديث العداد/);
  assert.doesNotMatch(source, /custom-menu/);
  assert.match(hooks, /"my_followups_navbar\.bundle\.js"/);
  assert.match(hooks, /"my_followups_navbar\.bundle\.css"/);
  assert.match(hooks, /namar_test\.comment_mentions\.notify_new_mentions_on_comment_update/);
  assert.match(hooks, /namar_test\.mentions\.events\.capture_mentions_on_update/);
  assert.match(css, /direction:\s*rtl/);
  assert.match(css, /\.namar-my-followups-source-badge[\s\S]*?direction:\s*ltr/);
  assert.match(css, /\.namar-my-followups-source-badge\.is-mentions[\s\S]*?--red-600/);
  assert.match(css, /\.namar-my-followups-source-badge\.is-followups[\s\S]*?--namar-followups-badge-bg,\s*#f4b400/);
  assert.match(css, /\.namar-my-followups-source-badge\.is-followups[\s\S]*?--namar-followups-badge-border,\s*#d89b00/);
  assert.match(css, /\.namar-my-followups-source-badge\.is-followups[\s\S]*?--namar-followups-badge-text,\s*#332600/);
  assert.doesNotMatch(css, /\.namar-my-followups-source-badge\.is-followups[\s\S]*?--orange-700/);
  assert.match(css, /\.namar-my-followups-source-badge\.is-approvals[\s\S]*?--purple-600/);
  assert.doesNotMatch(css, /namar-my-followups-source-symbol/);
  assert.match(css, /@media \(max-width: 767px\)[\s\S]*?\.namar-my-followups-label \{[\s\S]*?display:\s*none/);
  assert.doesNotMatch(css, /\.namar-my-followups-source-badge[^{]*\{[^}]*position:\s*absolute/);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
