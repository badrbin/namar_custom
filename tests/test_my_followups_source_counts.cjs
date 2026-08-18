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
  },
  __: (text) => text,
  console,
  Date,
  Promise,
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
    priority: "",
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

async function main() {
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

assert.match(source, /data-source-count="mentions"/);
assert.match(source, /data-source-count="followups"/);
assert.match(source, /data-source-count="approvals"/);
assert.match(css, /@media \(max-width: 620px\)[\s\S]*?\.mf-source-btn \{[\s\S]*?min-width: 0;/);
assert.match(css, /@media \(max-width: 620px\)[\s\S]*?\.mf-source-btn \.icon \{\s*display: none;/);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
