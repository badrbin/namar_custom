const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const sourcePath = path.join(__dirname, "..", "namar_test", "public", "js", "doctype", "workflow_approval_routing.js");
const source = fs.readFileSync(sourcePath, "utf8");
const handlers = {};
const dialogs = [];
const writes = [];
const wrapper = () => ({
  html(value) { this.content = value; return this; },
  attr() { return this; }, css() { return this; },
  prop(key, value) { this[key] = value; return this; },
  find() { return this; },
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
    ui: { form: { on: (doctype, callbacks) => { handlers[doctype] = callbacks; } },
      Dialog: class {
        constructor(options) {
          Object.assign(this, options);
          this.$wrapper = wrapper();
          const table = options.fields.find((field) => field.fieldname === "targets");
          this.fields_dict = { targets: { grid: { get_data: () => table.data } } };
          dialogs.push(this);
        }
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
vm.runInNewContext(source, context, { filename: sourcePath });
const events = handlers["Workflow Document State"];
const normalized = (value) => JSON.parse(JSON.stringify(value));

(async () => {
  events.form_render(frm, row.doctype, row.name);
  assert.match(controls.custom_followups_routing_summary.$wrapper.content, /حسب أدوار المرحلة/);
  assert.match(controls.custom_followups_routing_summary.$wrapper.content, /dir="rtl"/);

  await events.custom_followups_routing_edit(frm, row.doctype, row.name);
  const first = dialogs.at(-1);
  const table = first.fields.find((field) => field.fieldname === "targets");
  const fieldSelector = table.fields.find((field) => field.fieldname === "field");
  assert.deepEqual(normalized(fieldSelector.options).map((option) => option.value), ["", "responsible"]);
  assert.match(fieldSelector.options[1].label, /المسؤول عن الطلب/);
  assert.deepEqual(normalized(table.fields.find((field) => field.fieldname === "user").get_query()), {
    filters: { enabled: 1, user_type: "System User" },
  });
  const inlineFields = table.fields.filter((field) => field.in_list_view);
  assert.ok(inlineFields.reduce((sum, field) => sum + field.columns, 0) <= 10, "all fields must fit Frappe grid columns");
  assert.deepEqual(normalized(inlineFields).map((field) => field.fieldname), ["type", "recipient_summary"]);
  assert.ok(inlineFields.every((field) => !field.depends_on), "dialog inline metadata must not leak row dependency state");
  assert.equal(table.in_place_edit, false, "row expansion must be available to edit recipient details");
  table.data.push({ type: "user", user: "one@example.com", role: "stale" }, { type: "owner" },
    { type: "role", role: "Accounts User" }, { type: "user", user: " one@example.com " });
  await first.primary_action();
  assert.deepEqual(JSON.parse(writes.at(-1).value), { version: 1, targets: [
    { type: "user", user: "one@example.com" }, { type: "owner" }, { type: "role", role: "Accounts User" },
  ] });
  assert.ok(first.hidden);

  // Editing a second mixed target row must refresh that row, not the first row.
  const refreshed = [];
  const secondControl = {
    doc: { type: "field" }, df: { fieldname: "type" },
    grid_row: { refresh_dependency: () => refreshed.push("second-dependencies"),
      refresh_field: () => refreshed.push("second-field"),
      grid_form: { layout: { refresh: (doc) => refreshed.push(doc.type) } } },
  };
  table.fields.find((field) => field.fieldname === "type").change.call(secondControl);
  assert.deepEqual(refreshed, ["second-dependencies", "field", "second-field", "second-field"]);
  refreshed.splice(0);
  const expandedControl = {
    doc: { type: "role", role: "Accounts User" }, df: { fieldname: "type" },
    layout: { grid_row: secondControl.grid_row },
  };
  table.fields.find((field) => field.fieldname === "type").change.call(expandedControl);
  assert.deepEqual(refreshed, ["second-dependencies", "role", "second-field", "second-field"]);
  assert.equal(expandedControl.doc.recipient_summary, "Accounts User");

  // Opening and cancelling works on a copy; it must not silently update the saved setting.
  const beforeCancel = row.custom_followups_routing_targets;
  await events.custom_followups_routing_edit(frm, row.doctype, row.name);
  const cancelDialog = dialogs.at(-1);
  cancelDialog.fields.find((field) => field.fieldname === "targets").data[0].user = "other@example.com";
  cancelDialog.hide();
  assert.equal(row.custom_followups_routing_targets, beforeCancel);

  row.custom_followups_routing_targets = JSON.stringify({ version: 1, targets: [{ type: "field", field: "removed_field" }] });
  await events.custom_followups_routing_edit(frm, row.doctype, row.name);
  const stale = dialogs.at(-1);
  assert.ok(stale.fields.find((f) => f.fieldname === "targets").fields.find((f) => f.fieldname === "field")
    .options.some((option) => option.value === "removed_field"), "preserve stale selection until user corrects it");
  await assert.rejects(stale.primary_action(), /حقل مستخدم صالحًا/);

  stale.fields.find((f) => f.fieldname === "targets").data.splice(0, 1, { type: "user" });
  await assert.rejects(stale.primary_action(), /أكمل بيانات المستلم/);
  const staleRows = stale.fields.find((f) => f.fieldname === "targets").data;
  staleRows.splice(0, staleRows.length);
  await stale.primary_action();
  assert.deepEqual(JSON.parse(writes.at(-1).value), { version: 1, targets: [] });

  row.custom_followups_routing_targets = JSON.stringify({ version: 1, targets: [{ type: "user", user: "<script>alert(1)</script>" }] });
  events.form_render(frm, row.doctype, row.name);
  assert.ok(!controls.custom_followups_routing_summary.$wrapper.content.includes("<script>"));
  assert.ok(controls.custom_followups_routing_summary.$wrapper.content.includes("&lt;script&gt;"));
  frm.perm[0].write = 0;
  const count = dialogs.length;
  await events.custom_followups_routing_edit(frm, row.doctype, row.name);
  events.form_render(frm, row.doctype, row.name);
  assert.equal(dialogs.length, count);
  assert.equal(controls.custom_followups_routing_edit.$input.disabled, true);
  console.log("Workflow approval routing UI tests passed.");
})().catch((error) => { console.error(error); process.exitCode = 1; });
