const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const sourcePath = path.join(__dirname, "..", "namar_test", "public", "js", "doctype", "workflow_approval_routing.js");
const handlers = {};
const dialogs = [];
const writes = [];
const wrapper = () => ({
  html(value) { this.content = value; return this; },
  attr() { return this; }, css() { return this; },
  prop(key, value) { this[key] = value; return this; },
  find() { return this; },
  on(event, selector, handler) { this.click = handler; return this; },
});
const row = { name: "ROW-1", doctype: "Workflow Document State", state: "المراجعة" };
const controls = {
  custom_followups_routing_summary: { $wrapper: wrapper() },
  custom_followups_routing_edit: { $wrapper: wrapper(), $input: wrapper() },
};
const frm = {
  doc: { document_type: "Material Request", states: [row] },
  perm: [{ write: 1 }], is_new: () => false,
  fields_dict: { states: { grid: { grid_rows_by_docname: { "ROW-1": { grid_form: { fields_dict: controls } } } } } },
};
const context = {
  __: (value) => value,
  locals: { "Workflow Document State": { "ROW-1": row } },
  frappe: {
    ui: { form: {
      on: (doctype, callbacks) => { handlers[doctype] = callbacks; },
      get_open_grid_form: () => { throw new Error("Recipient editor must not operate on global GridRow state"); },
    },
      Dialog: class {
        constructor(options) {
          Object.assign(this, options);
          assert.ok(options.fields.every((field) => field.fieldtype !== "Table"), "No nested Frappe Grid inside recipient dialogs");
          this.$wrapper = wrapper();
          this.fields_dict = {};
          this.values = {};
          for (const field of options.fields) this.fields_dict[field.fieldname] = { df: field, $wrapper: wrapper() };
          dialogs.push(this);
        }
        async set_values(values) { this.values = { ...values }; }
        refresh_dependency() { this.dependenciesRefreshed = true; }
        show() { this.shown = true; }
        hide() { this.hidden = true; }
      },
    },
    utils: { escape_html: (value) => String(value).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[c]) },
    get_meta: () => ({ fields: [
      { fieldname: "responsible", fieldtype: "Link", options: "User", label: "المسؤول عن الطلب" },
      { fieldname: "employee", fieldtype: "Link", options: "Employee", label: "موظف" },
      { fieldname: "child", fieldtype: "Table", options: "Child", label: "الجدول" },
    ] }),
    model: {
      with_doctype: async () => {},
      set_value: async (cdt, cdn, field, value) => { writes.push({ cdt, cdn, field, value }); row[field] = value; },
    },
    throw: (message) => { throw new Error(message); },
  },
};
vm.runInNewContext(fs.readFileSync(sourcePath, "utf8"), context, { filename: sourcePath });
const events = handlers["Workflow Document State"];
const plain = (value) => JSON.parse(JSON.stringify(value));
const html = (dialog) => dialog.fields_dict.targets_list.$wrapper.content;
const add = async (dialog) => {
  await dialog.fields_dict.add_target.df.click();
  return dialogs.at(-1);
};
const click = async (dialog, routingAction, index) => {
  await dialog.fields_dict.targets_list.$wrapper.click({ currentTarget: { dataset: { routingAction, index: String(index) } } });
  return dialogs.at(-1);
};
const open = async () => {
  await events.custom_followups_routing_edit(frm, row.doctype, row.name);
  return dialogs.at(-1);
};

(async () => {
  events.form_render(frm, row.doctype, row.name);
  assert.match(controls.custom_followups_routing_summary.$wrapper.content, /حسب أدوار المرحلة/);
  assert.match(controls.custom_followups_routing_summary.$wrapper.content, /dir="rtl"/);

  const group = await open();
  const first = await add(group);
  assert.equal(first.values.type, "user", "type is assigned after construction instead of Frappe's user default keyword");
  assert.equal(first.fields_dict.type.df.default, undefined);
  assert.ok(first.dependenciesRefreshed);
  assert.deepEqual(plain(first.fields_dict.field.df.options).map((option) => option.value), ["", "responsible"]);
  assert.match(first.fields_dict.field.df.options[1].label, /المسؤول عن الطلب/);
  assert.deepEqual(plain(first.fields_dict.user.df.get_query()), { filters: { enabled: 1, user_type: "System User" } });
  for (const type of ["user", "role", "field"]) {
    assert.ok(first.fields_dict[type].df.depends_on.includes(type));
    assert.ok(first.fields_dict[type].df.mandatory_depends_on.includes(type));
  }
  let refreshed = 0;
  first.fields_dict.type.df.change.call({ layout: { refresh_dependency: () => refreshed++ } });
  assert.equal(refreshed, 1);
  first.primary_action({ type: "user", user: "one@example.com", role: "stale" });
  assert.ok(first.hidden);
  assert.match(html(group), /one@example.com/);
  assert.equal(writes.length, 0, "Saving recipient changes only group draft");

  const cancelledRecipient = await add(group);
  cancelledRecipient.values = { type: "user", user: "cancelled@example.com" };
  cancelledRecipient.hide();
  assert.doesNotMatch(html(group), /cancelled@example.com/);
  const roleEditor = await add(group);
  roleEditor.primary_action({ type: "role", role: "Accounts User" });
  const duplicateEditor = await add(group);
  duplicateEditor.primary_action({ type: "user", user: " one@example.com " });
  const editedSecond = await click(group, "edit", 1);
  assert.deepEqual(plain(editedSecond.values), { type: "role", role: "Accounts User" });
  editedSecond.primary_action({ type: "owner", role: "Accounts User" });
  assert.match(html(group), /منشئ المستند/);
  assert.doesNotMatch(html(group), /Accounts User/);
  assert.match(html(group), /one@example.com/);
  assert.equal(writes.length, 0, "Editing another target cannot update stored configuration");
  await group.primary_action();
  assert.deepEqual(JSON.parse(writes.at(-1).value), { version: 1, targets: [
    { type: "user", user: "one@example.com" }, { type: "owner" },
  ] });
  assert.ok(group.hidden);

  const stored = row.custom_followups_routing_targets;
  const cancelledGroup = await open();
  const editedFirst = await click(cancelledGroup, "edit", 0);
  editedFirst.primary_action({ type: "user", user: "other@example.com" });
  await click(cancelledGroup, "remove", 1);
  cancelledGroup.hide();
  assert.equal(row.custom_followups_routing_targets, stored, "Cancelling group discards all draft additions edits and removals");
  const reopened = await open();
  assert.match(html(reopened), /one@example.com/);
  assert.match(html(reopened), /منشئ المستند/);
  assert.doesNotMatch(html(reopened), /other@example.com/);
  reopened.hide();

  row.custom_followups_routing_targets = JSON.stringify({ version: 1, targets: [{ type: "field", field: "removed_field" }] });
  const staleGroup = await open();
  const staleRecipient = await click(staleGroup, "edit", 0);
  assert.ok(staleRecipient.fields_dict.field.df.options.some((option) => option.value === "removed_field"));
  assert.throws(() => staleRecipient.primary_action({ type: "field", field: "removed_field" }), /حقل مستخدم صالحًا/);
  assert.throws(() => staleRecipient.primary_action({ type: "user" }), /أكمل بيانات المستلم/);
  await assert.rejects(staleGroup.primary_action(), /حقل مستخدم صالحًا/);
  staleRecipient.hide();
  await click(staleGroup, "remove", 0);
  await staleGroup.primary_action();
  assert.deepEqual(JSON.parse(writes.at(-1).value), { version: 1, targets: [] });

  row.custom_followups_routing_targets = JSON.stringify({ version: 1, targets: Array.from({ length: 50 }, () => ({ type: "owner" })) });
  const fullGroup = await open();
  await assert.rejects(add(fullGroup), /50/);
  fullGroup.hide();
  row.custom_followups_routing_targets = JSON.stringify({ version: 1, targets: [{ type: "user", user: "<script>alert(1)</script>" }] });
  events.form_render(frm, row.doctype, row.name);
  assert.ok(!controls.custom_followups_routing_summary.$wrapper.content.includes("<script>"));
  const escapedGroup = await open();
  assert.ok(!html(escapedGroup).includes("<script>"));
  assert.ok(html(escapedGroup).includes("&lt;script&gt;"));
  escapedGroup.hide();
  frm.perm[0].write = 0;
  const count = dialogs.length;
  await events.custom_followups_routing_edit(frm, row.doctype, row.name);
  events.form_render(frm, row.doctype, row.name);
  assert.equal(dialogs.length, count);
  assert.equal(controls.custom_followups_routing_edit.$input.disabled, true);
  console.log("Workflow approval routing standalone recipient dialog tests passed.");
})().catch((error) => { console.error(error); process.exitCode = 1; });
