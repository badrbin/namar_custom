const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const assetPath = path.join(
  __dirname,
  "..",
  "namar_test",
  "public",
  "js",
  "comment_history.js"
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
  window: { cur_frm: null },
  frappe: {
    ui: { form: { Footer } },
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

console.log("comment_history.js supports the Frappe Deferred promise");
