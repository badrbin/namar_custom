const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const assetPath = path.join(
  __dirname,
  "..",
  "namar_custom",
  "public",
  "js",
  "comment_history.bundle.js"
);
const source = fs.readFileSync(assetPath, "utf8");

let callCount = 0;
let alwaysCount = 0;
let installCallback = null;
const warnings = [];

const frappePromise = {
  then(onSuccess) {
    if (onSuccess) onSuccess({ message: { histories: {} } });
    return this;
  },
  catch() {
    return this;
  },
  always(onSettled) {
    alwaysCount += 1;
    onSettled();
    return this;
  },
};

function Footer(frm) {
  this.frm = frm;
}
Footer.prototype.make_timeline = function () {};

const emptyCollection = {
  find() {
    return this;
  },
  remove() {
    return this;
  },
};

const context = {
  window: { cur_frm: null, __namar_comment_history_test__: {} },
  frappe: {
    ui: { form: { Footer } },
    user_info(user) {
      return { fullname: `الاسم الكامل: ${user}` };
    },
    call() {
      callCount += 1;
      return frappePromise;
    },
  },
  console: {
    warn(...args) {
      warnings.push(args);
    },
  },
  Promise,
  setInterval(callback) {
    installCallback = callback;
    return 1;
  },
  clearInterval() {},
  setTimeout(callback) {
    callback();
    return 1;
  },
  clearTimeout() {},
};

vm.runInNewContext(source, context, { filename: assetPath });
installCallback();

const frm = {
  doctype: "Note",
  docname: "TEST-NOTE",
  is_new() {
    return false;
  },
  get_docinfo() {
    return {
      comments: [{ name: "TEST-COMMENT", content: "نص", published: 0 }],
    };
  },
  timeline: {
    timeline_items_wrapper: emptyCollection,
    render_timeline_items() {},
  },
};

new Footer(frm).make_timeline();

assert.equal(typeof frappePromise.finally, "undefined");
assert.equal(callCount, 1);
assert.equal(alwaysCount, 1);
assert.deepEqual(warnings, []);

const testHooks = context.window.__namar_comment_history_test__;
const snapshots = testHooks.build_snapshots(
  {
    revisions: [
      {
        before_content: "<p>النسخة الثانية</p>",
        after_content: "<p>الحالية من السجل</p>",
        edited_by: "editor-2@example.com",
        edited_by_full_name: "المحرر الثاني",
        edited_at: "2026-08-15 12:00:00",
      },
      {
        before_content: "<p>النسخة الأولى</p>",
        after_content: "<p>النسخة الثانية</p>",
        edited_by: "editor-1@example.com",
        edited_by_full_name: "المحرر الأول",
        edited_at: "2026-08-15 11:00:00",
        audit_user: "auditor@example.com",
      },
    ],
  },
  {
    content: "<p>الحالية من التعليق</p>",
    owner: "creator@example.com",
    creation: "2026-08-15 10:00:00",
  }
);

assert.equal(snapshots.length, 3);
assert.equal(snapshots[0].kind, "current");
assert.equal(snapshots[0].label, "النسخة الحالية");
assert.equal(snapshots[0].content, "<p>الحالية من التعليق</p>");
assert.equal(snapshots[0].actor, "editor-2@example.com");
assert.equal(snapshots[1].kind, "previous");
assert.equal(snapshots[1].label, "النسخة 2 من 3");
assert.equal(snapshots[1].content, "<p>النسخة الثانية</p>");
assert.equal(snapshots[1].actor, "editor-1@example.com");
assert.equal(snapshots[1].audit_revision.audit_user, "auditor@example.com");
assert.equal(snapshots[2].kind, "oldest");
assert.equal(snapshots[2].label, "أقدم نسخة مسجلة");
assert.equal(snapshots[2].content, "<p>النسخة الأولى</p>");
assert.equal(snapshots[2].actor, "creator@example.com");
assert.equal(snapshots[2].actor_context, "صاحب التعليق");
assert.equal(snapshots[2].audit_revision, null);

assert.equal(testHooks.format_edit_count(1), "تم التعديل مرة واحدة");
assert.equal(testHooks.format_edit_count(2), "تم التعديل مرتين");
assert.equal(testHooks.format_edit_count(4), "تم التعديل 4 مرات");
assert.match(source, /role="dialog"/);
assert.match(source, /aria-modal="true"/);
assert.match(source, /aria-haspopup="dialog"/);
assert.match(source, /aria-current/);
assert.match(source, /event\.stopPropagation\(\)/);
assert.match(source, /frappe\.avatar/);
assert.doesNotMatch(source, /\.finally\s*\(/);
assert.doesNotMatch(source, /قبل التعديل|بعد التعديل/);
assert.doesNotMatch(source, /namar-comment-history-changes/);

class ControlledDeferred {
  constructor() {
    this.handlers = [];
    this.settledHandlers = [];
  }

  then(onSuccess, onFailure) {
    this.handlers.push({ onSuccess, onFailure });
    return this;
  }

  always(onSettled) {
    this.settledHandlers.push(onSettled);
    return this;
  }

  resolve(value) {
    this.handlers.forEach(({ onSuccess }) => onSuccess && onSuccess(value));
    this.settledHandlers.forEach((onSettled) => onSettled());
  }

  reject(error) {
    this.handlers.forEach(({ onFailure }) => onFailure && onFailure(error));
    this.settledHandlers.forEach((onSettled) => onSettled());
  }
}

const deferreds = [];
const queuedTimers = [];
const cacheWarnings = [];
let timerId = 0;
const cacheContext = {
  window: { cur_frm: null, __namar_comment_history_test__: {} },
  frappe: {
    ui: {},
    call() {
      const deferred = new ControlledDeferred();
      deferreds.push(deferred);
      return deferred;
    },
  },
  console: {
    warn(...args) {
      cacheWarnings.push(args);
    },
  },
  Promise,
  setInterval() {
    return 1;
  },
  clearInterval() {},
  setTimeout(callback) {
    const timer = { callback, cancelled: false, id: ++timerId };
    queuedTimers.push(timer);
    return timer.id;
  },
  clearTimeout(id) {
    const timer = queuedTimers.find((candidate) => candidate.id === id);
    if (timer) timer.cancelled = true;
  },
};

vm.runInNewContext(source, cacheContext, { filename: assetPath });
const cacheHooks = cacheContext.window.__namar_comment_history_test__;
const cacheComments = [
  { name: "CACHE-COMMENT", content: "النص الأول", published: 0 },
];
const cacheFrm = {
  doctype: "Note",
  docname: "CACHE-NOTE",
  is_new() {
    return false;
  },
  get_docinfo() {
    return { comments: cacheComments };
  },
  timeline: {
    timeline_items_wrapper: emptyCollection,
  },
};

const firstRequest = cacheHooks.load_history(cacheFrm, false);
const deduplicatedRequest = cacheHooks.load_history(cacheFrm, false);
assert.equal(deferreds.length, 1);
assert.equal(deduplicatedRequest, firstRequest);

cacheHooks.load_history(cacheFrm, true);
assert.equal(deferreds.length, 1);
deferreds[0].resolve({ message: { histories: {} } });
const forcedTimer = queuedTimers.find((timer) => !timer.cancelled);
assert.ok(forcedTimer);
forcedTimer.cancelled = true;
forcedTimer.callback();
assert.equal(deferreds.length, 2);
deferreds[1].resolve({ message: { histories: {} } });

cacheHooks.load_history(cacheFrm, false);
assert.equal(deferreds.length, 2);

cacheComments[0].content = "النص الثاني";
cacheHooks.load_history(cacheFrm, false);
assert.equal(deferreds.length, 3);
cacheComments[0].content = "النص الثالث";
deferreds[2].resolve({ message: { histories: {} } });
const staleResponseTimer = queuedTimers.find((timer) => !timer.cancelled);
assert.ok(staleResponseTimer);
staleResponseTimer.cancelled = true;
staleResponseTimer.callback();
assert.equal(deferreds.length, 4);
deferreds[3].resolve({ message: { histories: {} } });

cacheComments[0].content = "النص الرابع";
cacheHooks.load_history(cacheFrm, false);
assert.equal(deferreds.length, 5);
deferreds[4].reject(new Error("expected test failure"));
cacheHooks.load_history(cacheFrm, false);
assert.equal(deferreds.length, 6);
assert.equal(cacheWarnings.length, 1);

let nativePromiseCallCount = 0;
const nativePromiseContext = {
  window: { cur_frm: null, __namar_comment_history_test__: {} },
  frappe: {
    ui: {},
    call() {
      nativePromiseCallCount += 1;
      return Promise.resolve({ message: { histories: {} } });
    },
  },
  console,
  Promise,
  setInterval() {
    return 1;
  },
  clearInterval() {},
  setTimeout,
  clearTimeout,
};

vm.runInNewContext(source, nativePromiseContext, { filename: assetPath });
const nativePromiseHooks = nativePromiseContext.window.__namar_comment_history_test__;
const nativePromiseFrm = {
  doctype: "Note",
  docname: "NATIVE-PROMISE-NOTE",
  is_new() {
    return false;
  },
  get_docinfo() {
    return {
      comments: [{ name: "NATIVE-COMMENT", content: "نص", published: 0 }],
    };
  },
  timeline: {
    timeline_items_wrapper: emptyCollection,
  },
};

(async function () {
  await nativePromiseHooks.load_history(nativePromiseFrm, false);
  await nativePromiseHooks.load_history(nativePromiseFrm, false);
  assert.equal(nativePromiseCallCount, 1);
  console.log("comment_history.bundle.js supports Deferred, Promise, cache, and drawer snapshots");
})().catch(function (error) {
  console.error(error);
  process.exitCode = 1;
});
