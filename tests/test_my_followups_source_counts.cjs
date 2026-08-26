const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const assetPath = path.join(
  __dirname,
  "..",
  "namar_test",
  "namar_test",
  "page",
  "my_followups",
  "my_followups.js"
);
const source = fs.readFileSync(assetPath, "utf8");
const cssPath = path.join(path.dirname(assetPath), "my_followups.css");
const css = fs.readFileSync(cssPath, "utf8");
const context = {
  frappe: {
    pages: { "my-followups": {} },
    utils: {},
    show_alert: () => {},
  },
  __: (text) => text,
  console,
  Date,
  Promise,
  URL,
  URLSearchParams,
  window: {
    clearTimeout,
    setTimeout,
    location: {
      href: "https://test.example.com/app/my-followups",
      search: "",
    },
    history: {
      state: null,
      replaceState: () => {},
    },
  },
};
vm.runInNewContext(
  `${source}\nglobalThis.__NamarMyFollowupsTest = NamarMyFollowups;`,
  context,
  { filename: assetPath }
);
const NamarMyFollowups = context.__NamarMyFollowupsTest;

function makePage() {
  const page = Object.create(NamarMyFollowups.prototype);
  page.api = "namar_test.followups.api";
  page.mentions_api = "namar_test.mentions.api";
  page.state = {
    source: "mentions",
    bucket: "open",
    priority: "",
    search_scope: "all",
    counts: { unread: 7 },
  };
  page.source_counts = { mentions: null, followups: null, approvals: null };
  page.source_count_status = { mentions: "idle", followups: "idle", approvals: "idle" };
  page.source_count_loaded_at = { mentions: 0, followups: 0, approvals: 0 };
  page.source_count_sequence = { mentions: 0, followups: 0, approvals: 0 };
  page.source_count_requests = {};
  page.render_source_counts = () => {};
  page.log_error = (scope, error) => {
    throw new Error(`${scope}: ${error?.message || error}`);
  };
  return page;
}

function renderMentionDetail(detail) {
  const page = makePage();
  let html = "";
  page.state.detail = {
    reference_doctype: "Sales Order",
    reference_name: "SO-1",
    messages: [],
    ...detail,
  };
  page.$detail = {
    html: (value) => {
      html = value;
    },
  };
  page.icon = () => "";
  page.escape = (value) => String(value ?? "");
  page.escape_attr = (value) => String(value ?? "");
  page.user_display = () => "";
  page.mention_avatar = () => "";
  page.render_mention_messages = () => "";
  page.init_mention_reply_editor = () => {};
  page.render_mention_detail();
  return html;
}

async function main() {
{
  const page = makePage();
  context.window.location.search = "";
  assert.deepEqual(
    { ...page.read_deep_link() },
    { source: "mentions", bucket: "open", thread: "" }
  );
  context.window.location.search = "?source=followups&bucket=overdue";
  assert.deepEqual(
    { ...page.read_deep_link() },
    { source: "followups", bucket: "overdue", thread: "" }
  );
  context.window.location.search = "?source=followups&bucket=invalid";
  assert.deepEqual(
    { ...page.read_deep_link() },
    { source: "followups", bucket: "all", thread: "" }
  );
  context.window.location.search = "?source=mentions&bucket=unread&thread=THREAD-1";
  assert.deepEqual(
    { ...page.read_deep_link() },
    { source: "mentions", bucket: "unread", thread: "THREAD-1" }
  );
  context.window.location.search = "";
}

{
  const page = makePage();
  let replaced = "";
  page.state.source = "followups";
  page.state.bucket = "overdue";
  context.window.location.href = "https://test.example.com/app/my-followups?source=followups";
  context.window.history.replaceState = (_state, _title, value) => {
    replaced = String(value);
  };
  page.sync_url_state("followups");
  assert.equal(new URL(replaced).searchParams.get("bucket"), "overdue");
}

{
  const page = makePage();
  const events = [];
  page.state.source = "followups";
  page.state.bucket = "all";
  page.state.action_busy = false;
  page.sync_url_state = (source) => events.push(["sync", source]);
  page.render_filters = () => events.push(["filters"]);
  page.load_list = () => events.push(["list"]);
  page.change_bucket("overdue");
  assert.equal(page.state.bucket, "overdue");
  assert.deepEqual(events, [["sync", "followups"], ["filters"], ["list"]]);
}

{
  const page = makePage();
  const calls = [];
  const resolvers = {};
  page.call = (method, args, api = page.api) => {
    calls.push({ method, args: { ...args }, api });
    return new Promise((resolve) => {
      resolvers[method] = resolve;
    });
  };

  const pending = page.load_source_summaries({ exclude_source: "mentions" });
  await Promise.resolve();

  assert.deepEqual(
    calls.map(({ method }) => method),
    ["get_followups", "get_approvals"]
  );
  assert.equal(calls[0].args.page_length, 1);
  assert.equal(calls[0].args.priority, "");
  assert.equal(calls[1].args.page_length, 1);
  assert.equal(calls.some(({ method }) => method === "get_mention_detail"), false);
  assert.equal(calls.some(({ method }) => method === "mark_mention_seen"), false);

  resolvers.get_followups({ counts: { open: 4 }, items: [] });
  resolvers.get_approvals({ counts: { open: 6 }, items: [] });
  await pending;

  assert.deepEqual(
    { ...page.source_counts },
    { mentions: null, followups: 4, approvals: 6 }
  );
  assert.deepEqual(page.state.counts, { unread: 7 });
}

{
  const page = makePage();
  const events = [];
  page.read_deep_link = () => ({ source: "mentions", thread: "" });
  page.load_source_summaries = (options) => events.push(["summaries", options]);
  page.load_list = () => events.push(["list"]);
  page.state.list_status = "idle";

  page.show();

  assert.equal(events.length, 2);
  assert.equal(events[0][0], "summaries");
  assert.equal(events[0][1].exclude_source, "mentions");
  assert.deepEqual(events[1], ["list"]);
}

{
  const page = makePage();
  const calls = [];
  page.call = async (method, args, api) => {
    calls.push({ method, args, api });
    return { counts: { open: 3 } };
  };

  await page.fetch_source_summary("mentions");

  assert.equal(calls.length, 1);
  assert.equal(calls[0].method, "get_mentions");
  assert.equal(calls[0].args.bucket, "open");
  assert.equal(calls[0].args.page_length, 1);
  assert.equal(calls[0].api, page.mentions_api);
}

{
  const page = makePage();
  let resolveSummary;
  page.call = () => new Promise((resolve) => {
    resolveSummary = resolve;
  });

  const staleSummary = page.load_source_count("mentions");
  await Promise.resolve();
  page.sync_source_count_from_list("mentions", { counts: { open: 12 } });
  resolveSummary({ counts: { open: 3 } });
  await staleSummary;

  assert.equal(page.source_counts.mentions, 12);
  assert.equal(page.source_count_status.mentions, "ready");
}

{
  const page = makePage();
  let resolveSummary;
  let summarySource = "";
  page.fetch_source_summary = (source) => {
    summarySource = source;
    return new Promise((resolve) => {
      resolveSummary = resolve;
    });
  };
  page.state.priority = "High";

  const accepted = page.sync_source_count_from_list("followups", { counts: { open: 1 } });
  const globalSummary = page.source_count_requests.followups;
  await Promise.resolve();

  assert.equal(accepted, false);
  assert.equal(summarySource, "followups");
  assert.equal(page.source_count_status.followups, "loading");
  resolveSummary({ counts: { open: 9 } });
  await globalSummary;
  assert.equal(page.source_counts.followups, 9);
}

{
  const page = makePage();
  page.sync_source_count_from_list("mentions", { counts: { open: 3897, unread: 2 } });
  assert.equal(page.source_counts.mentions, 3897);
  assert.deepEqual(page.state.counts, { unread: 7 });

  page.source_counts.followups = 9;
  page.source_count_status.followups = "ready";
  page.source_count_loaded_at.followups = Date.now();
  page.state.priority = "High";
  page.sync_source_count_from_list("followups", { counts: { open: 1 } });
  assert.equal(page.source_counts.followups, 9);
}

{
  const page = makePage();
  const events = [];
  page.state.bucket = "closed";
  page.state.selected_name = "THREAD-1";
  page.state.mobile_detail = true;
  page.selected_by_source = { mentions: "THREAD-1", followups: null, approvals: null };
  page.set_action_busy = (value) => events.push(["busy", value]);
  page.call = async () => ({});
  page.render_filters = () => events.push(["filters"]);
  page.sync_url_state = () => events.push(["sync"]);
  page.show_mobile_queue = () => {
    page.state.mobile_detail = false;
    events.push(["queue"]);
  };
  page.load_list = async () => events.push(["list"]);
  page.handle_mention_conflict = async () => false;
  page.show_action_error = (error) => {
    throw error;
  };

  const success = await page.run_mention_action({
    method: "reopen_mention",
    args: { thread_name: "THREAD-1" },
    success_message: "تمت إعادة فتح الرسالة",
    next_bucket: "open",
  });

  assert.equal(success, true);
  assert.equal(page.state.bucket, "open");
  assert.equal(page.state.mobile_detail, false);
  assert.equal(page.state.selected_name, null);
  assert.deepEqual(events, [["busy", true], ["filters"], ["sync"], ["queue"], ["list"], ["busy", false]]);
}

{
  const page = makePage();
  let action = null;
  page.state.source = "mentions";
  page.state.selected_name = "THREAD-2";
  page.state.detail = { last_event_key: "EVENT-9" };
  page.run_mention_action = async (options) => {
    action = options;
    return true;
  };

  await page.reopen_mention();

  assert.equal(action.method, "reopen_mention");
  assert.equal(action.next_bucket, "open");
  assert.deepEqual({ ...action.args }, {
    thread_name: "THREAD-2",
    expected_last_event_key: "EVENT-9",
  });
}

{
  const page = makePage();
  assert.deepEqual(
    Array.from(page.search_scope_options(), ([value]) => String(value)),
    ["all", "document", "title", "employee", "content"]
  );
  page.state.source = "followups";
  assert.deepEqual(
    Array.from(page.search_scope_options(), ([value]) => String(value)),
    ["all", "document", "doctype", "employee", "content"]
  );
  page.state.source = "approvals";
  assert.deepEqual(
    Array.from(page.search_scope_options(), ([value]) => String(value)),
    ["all", "document", "doctype", "state"]
  );
}

{
  const page = makePage();
  page.state.source = "followups";
  page.state.bucket = "all";
  page.state.search = "PINV260115";
  const payload = page.normalize_list_response({
    items: [{ name: "TODO-1" }],
    counts: { all: 3897 },
    has_more: true,
    next_start: 1,
  });
  assert.equal(payload.total, null);
  assert.equal(payload.has_more, true);
  assert.equal(payload.next_start, 1);
}

{
  const page = makePage();
  assert.equal(page.mention_status({ status: "Open" }).label, "تحتاج قرارًا");
  assert.equal(page.mention_status({ status: "Converted" }).label, "قيد المتابعة");
  assert.equal(page.mention_status({ status: "Closed" }).label, "مغلقة");
  assert.equal(
    page.mention_status({ status: "Closed", closed_via_followup: 1 }).label,
    "أُنجزت عبر متابعة"
  );

  page.state.bucket = "converted";
  assert.equal(page.mention_empty_message(), "لا توجد رسائل قيد المتابعة حاليًا.");
  assert.match(
    page.mention_aria_label(
      { status: "Converted", unread: true },
      "خالد",
      "راجع الطلب",
      "SO-1"
    ),
    /^غير مقروءة، قيد المتابعة،/
  );
}

{
  const convertedHtml = renderMentionDetail({
    status: "Converted",
    converted_to_todo: "TODO-1",
    permissions: {
      can_reply: true,
      can_close: false,
      can_reopen: false,
      can_convert: false,
    },
  });
  assert.match(convertedHtml, /قيد المتابعة/);
  assert.match(convertedHtml, /data-todo-name="TODO-1"/);
  assert.match(convertedHtml, /إرسال الرد/);
  assert.doesNotMatch(convertedHtml, /إرسال وإغلاق/);
  assert.doesNotMatch(convertedHtml, /إغلاق الرسالة/);

  const directClosedHtml = renderMentionDetail({
    status: "Closed",
    converted_to_todo: "TODO-OLD",
    permissions: { can_reopen: true },
  });
  assert.match(directClosedHtml, /مغلقة/);
  assert.match(directClosedHtml, /إعادة فتح الرسالة/);
  assert.doesNotMatch(directClosedHtml, /أُنجزت عبر متابعة/);
  assert.doesNotMatch(directClosedHtml, /data-todo-name=/);

  const completedHtml = renderMentionDetail({
    status: "Closed",
    closed_via_followup: 1,
    converted_to_todo: "TODO-2",
    permissions: { can_reopen: true },
  });
  assert.match(completedHtml, /أُنجزت عبر متابعة/);
  assert.match(completedHtml, /data-todo-name="TODO-2"/);

  const deletedCompletedHtml = renderMentionDetail({
    status: "Closed",
    closed_via_followup: 1,
    converted_to_todo: null,
    permissions: { can_reopen: true },
  });
  assert.match(deletedCompletedHtml, /أُنجزت عبر متابعة/);
  assert.match(deletedCompletedHtml, /سجل المتابعة غير متاح/);
  assert.doesNotMatch(deletedCompletedHtml, /data-todo-name=/);
}

{
  const page = makePage();
  let route = null;
  page.state.detail = { converted_to_todo: "TODO-3" };
  page.$detail = {
    find: () => ({ data: () => "TODO-3" }),
  };
  context.frappe.set_route = (...parts) => {
    route = parts;
  };

  page.open_converted_followup();

  assert.deepEqual(route, ["Form", "ToDo", "TODO-3"]);
}

assert.match(source, /data-source-count="mentions"/);
assert.match(source, /data-source-count="followups"/);
assert.match(source, /data-source-count="approvals"/);
assert.match(css, /@media \(max-width: 620px\)[\s\S]*?\.mf-source-btn \{[\s\S]*?min-width: 0;/);
assert.match(css, /@media \(max-width: 620px\)[\s\S]*?\.mf-source-btn \.icon \{\s*display: none;/);
assert.match(source, /class="mf-search-scope"/);
assert.match(source, /search_scope: this\.state\.search_scope/);
assert.match(css, /\.mf-search-scope-field \{[\s\S]*?direction: rtl;/);
assert.match(css, /@media \(min-width: 992px\) and \(max-width: 1279px\)[\s\S]*?\.mf-search-controls \{[\s\S]*?flex-direction: column;/);
assert.match(css, /\[data-theme="dark"\] \.mf-mention-message\.is-current \.mf-message-card \{[\s\S]*?background: var\(--mf-soft\);/);
assert.match(css, /\[data-theme="dark"\] \.mf-message-meta span \{[\s\S]*?color: var\(--blue-300, var\(--mf-ink\)\);/);
assert.match(source, /\{ key: "unread", label: __\("غير مقروءة"\) \}/);
assert.match(source, /\{ key: "converted", label: __\("قيد المتابعة"\) \}/);
assert.doesNotMatch(source, /__\("محوّلة"\)/);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
