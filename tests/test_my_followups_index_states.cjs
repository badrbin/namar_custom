const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const assetPath = path.join(__dirname, "..", "namar_custom", "namar_custom", "page", "my_followups", "my_followups.js");
const source = fs.readFileSync(assetPath, "utf8");
const timers = new Map();
const events = new Map();
const emitted = [];
let nextTimer = 1;
let currentRoute = ["my-followups"];
const document = { hidden: false };
const context = {
  console, Date, Promise, URL, URLSearchParams, document,
  __: (text, args) => args ? text.replace(/\{(\d+)\}/g, (_, index) => args[index]) : text,
  $: () => ({
    trigger: (event, payload) => emitted.push({ event, payload }),
    on: (name, handler) => events.set(name, handler),
  }),
  frappe: {
    pages: { "my-followups": {} },
    get_route: () => currentRoute,
    realtime: { on: (name, handler) => events.set(name, handler) },
    utils: { escape_html: (value) => String(value).replace(/</g, "&lt;").replace(/>/g, "&gt;") },
  },
  window: {
    setTimeout: (callback, delay) => {
      const id = nextTimer++;
      timers.set(id, { callback, delay });
      return id;
    },
    clearTimeout: (id) => timers.delete(id),
    location: { href: "https://test.example.com/app/my-followups?source=approvals", search: "?source=approvals" },
  },
};
vm.runInNewContext(`${source}\nglobalThis.Page = NamarMyFollowups;`, context, { filename: assetPath });

function node() {
  const result = { value: "", attributes: {}, classes: {} };
  result.html = result.text = (value) => { result.value = value; return result; };
  result.attr = (key, value) => { result.attributes[key] = value; return result; };
  result.empty = () => { result.value = ""; return result; };
  result.addClass = result.removeClass = () => result;
  result.toggleClass = (key, value) => { result.classes[key] = value; return result; };
  return result;
}

function makePage() {
  timers.clear();
  emitted.length = 0;
  document.hidden = false;
  currentRoute = ["my-followups"];
  const page = Object.create(context.Page.prototype);
  page.api = "namar_custom.followups.api";
  page.state = {
    source: "approvals", bucket: "all", search: "", search_scope: "all", priority: "",
    items: [{ name: "OLD-ACTION" }], counts: { open: 78, all: 78 }, total: 78,
    selected_name: "OLD-ACTION", detail: { name: "OLD-ACTION", reference_name: "PRIVATE-OLD" },
    has_more: true, next_start: 25, page_length: 25, list_status: "ready", detail_status: "ready",
  };
  page.source_counts = { mentions: 9, followups: 3, approvals: 78 };
  page.source_count_status = { mentions: "ready", followups: "ready", approvals: "ready" };
  page.source_count_sequence = { mentions: 0, followups: 0, approvals: 0 };
  page.source_count_loaded_at = { mentions: 0, followups: 0, approvals: 0 };
  page.source_count_requests = {};
  page.selected_by_source = { mentions: null, followups: null, approvals: "OLD-ACTION" };
  page.list_sequence = 0;
  page.detail_sequence = 0;
  page.approval_retry_attempt = 0;
  page.approval_refresh_timer = null;
  page.$list = node();
  page.$detail = node();
  page.$filters = node();
  page.$pagination = node();
  page.badges = { mentions: node(), followups: node(), approvals: node() };
  page.$root = { find: (selector) => page.badges[/data-source-count="(\w+)"/.exec(selector)?.[1]] };
  page.icon = () => "";
  page.logs = [];
  page.log_error = (scope, error) => page.logs.push({ scope, error });
  return page;
}

function assertUnavailable(page, status) {
  assert.equal(page.source_counts.approvals, null);
  assert.equal(page.source_count_status.approvals, status);
  assert.equal(page.state.items.length, 0);
  assert.equal(page.state.detail, null);
  assert.equal(page.state.selected_name, null);
  assert.equal(page.selected_by_source.approvals, null);
  assert.equal(page.state.total, null);
  assert.equal(page.state.has_more, false);
  assert.equal(page.state.next_start, null);
  assert.equal(page.source_counts.mentions, 9);
  assert.equal(page.source_counts.followups, 3);
  assert.doesNotMatch(page.$list.value, /OLD-ACTION|PRIVATE-OLD|لا توجد موافقات/);
  assert.doesNotMatch(page.$detail.value, /OLD-ACTION|PRIVATE-OLD/);
  assert.doesNotMatch(page.$filters.value, /<strong>0<\/strong>/);
  assert.equal(page.badges.approvals.value, status === "error" ? "—" : "…");
}

async function main() {
  {
    const page = makePage();
    const calls = [];
    page.call = async (method, args) => {
      calls.push({ method, args });
      return { approval_status: "updating", counts: { approvals: null } };
    };
    const summary = await page.fetch_source_summary("approvals");
    assert.equal(calls[0].method, "get_my_followups_counts");
    assert.equal(Object.keys(calls[0].args).length, 0);
    assert.equal(summary.status, "updating");
    assert.equal(summary.counts.open, null);
  }
  {
    const page = makePage();
    page.call = async () => ({ status: "updating", counts: { open: null, all: null }, items: [{ name: "STALE" }] });
    await page.load_list({ preserve_selection: true });
    assertUnavailable(page, "updating");
    assert.match(page.$list.value, /جار تحديث الموافقات/);
    assert.doesNotMatch(page.$list.value, /STALE/);
    assert.equal(timers.size, 1);
    assert.equal([...timers.values()][0].delay, 30000);
    assert.equal(emitted.at(-1).payload[0].approval_status, "updating");
  }
  {
    const page = makePage();
    page.call = async () => ({ status: "error", counts: { open: 0 }, items: [] });
    await page.load_list({ append: true, preserve_selection: true });
    assertUnavailable(page, "error");
    assert.match(page.$list.value, /تعذر تحديث الموافقات|إعادة المحاولة/);
    assert.equal(timers.size, 0);
  }
  {
    const page = makePage();
    page.call = async () => { throw new Error("404 unavailable API"); };
    await page.load_list({ preserve_selection: true });
    assertUnavailable(page, "error");
    assert.equal(page.logs.length, 1);
  }
  {
    const page = makePage();
    page.call = async () => ({ status: "ready", counts: { open: null }, items: [] });
    await page.load_list();
    assertUnavailable(page, "error");
    assert.equal(page.logs[0].error.message, "Missing open source count");
  }
  {
    const page = makePage();
    page.call = async () => ({ status: "ready", counts: { open: 0, all: 0 }, total: 0, items: [], has_more: false });
    page.schedule_approval_refresh();
    await page.load_list();
    assert.equal(page.source_counts.approvals, 0);
    assert.equal(page.source_count_status.approvals, "ready");
    assert.equal(page.badges.approvals.value, 0);
    assert.match(page.$filters.value, /<strong>0<\/strong>/);
    assert.match(page.$list.value, /لا توجد موافقات بانتظار مراجعتك/);
    assert.equal(timers.size, 0);
  }
  {
    const page = makePage();
    page.fetch_source_summary = async () => ({ status: "updating", counts: { open: null } });
    await page.load_source_count("approvals", { force: true });
    assertUnavailable(page, "updating");
    assert.equal(page.logs.length, 0);
  }
  {
    const page = makePage();
    let completeOldRequest;
    page.call = () => new Promise((resolve) => { completeOldRequest = resolve; });
    const pending = page.load_list({ preserve_selection: true });
    page.bind_approval_updates();
    events.get("namar_approvals_changed")({ status: "ready" });
    events.get("namar_approvals_changed")({ status: "ready" });
    assertUnavailable(page, "updating");
    assert.equal(timers.size, 1);
    completeOldRequest({ status: "ready", items: [], counts: { open: 78, all: 78 } });
    await pending;
    assertUnavailable(page, "updating");
  }
  {
    const page = makePage();
    let resolveSummary;
    page.fetch_source_summary = () => new Promise((resolve) => { resolveSummary = resolve; });
    const pending = page.load_source_count("approvals", { force: true });
    await Promise.resolve();
    page.invalidate_approval_data();
    resolveSummary({ status: "ready", counts: { open: 78 } });
    await pending;
    assertUnavailable(page, "updating");
  }
  {
    const page = makePage();
    let completeDetail;
    page.call = () => new Promise((resolve) => { completeDetail = resolve; });
    const pending = page.load_detail("OLD-ACTION");
    page.invalidate_approval_data();
    completeDetail({ approval: { name: "OLD-ACTION", reference_name: "PRIVATE-OLD" } });
    await pending;
    assertUnavailable(page, "updating");
  }
  {
    const page = makePage();
    let attempts = 0;
    page.load_list = () => { attempts += 1; page.schedule_approval_refresh(); };
    page.schedule_approval_refresh();
    for (const expected of [30000, 60000, 120000, 240000, 300000, 300000]) {
      const [id, timer] = [...timers.entries()][0];
      assert.equal(timer.delay, expected);
      timers.delete(id);
      timer.callback();
      assert.equal(timers.size, 1);
    }
    assert.equal(attempts, 6);
    page.reset_approval_refresh();
    document.hidden = true;
    page.schedule_approval_refresh();
    assert.equal(timers.size, 0);
    document.hidden = false;
    currentRoute = ["Form", "Material Request"];
    page.schedule_approval_refresh();
    assert.equal(timers.size, 0);
  }
  assert.doesNotMatch(source, /localStorage|sessionStorage/);
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
