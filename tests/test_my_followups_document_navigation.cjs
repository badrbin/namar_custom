const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const root = path.join(__dirname, "..");
const app = fs.existsSync(path.join(root, "namar_custom")) ? "namar_custom" : "namar_test";
const sourcePath = path.join(root, app, app, "page", "my_followups", "my_followups.js");
const opened = [];
const formLinks = [];
const origin = "https://test.example.com";
const context = {
  __: (value) => value,
  URL,
  frappe: {
    pages: { "my-followups": {} },
    set_route: () => { throw new Error("Opening a document must not replace My Followups"); },
    call: () => { throw new Error("Opening a document must not apply an approval or mutate records"); },
    utils: {
      // The native Frappe get_form_link contract URI-encodes both path segments.
      get_form_link: (doctype, name) => {
        formLinks.push({ doctype, name });
        return `/app/${encodeURIComponent(doctype.toLowerCase().replace(/ /g, "-"))}/${encodeURIComponent(name)}`;
      },
    },
  },
  window: {
    location: { origin, href: `${origin}/app/my-followups?source=approvals` },
    open: (...args) => { opened.push(args); return null; },
  },
};
vm.runInNewContext(`${fs.readFileSync(sourcePath, "utf8")}\nglobalThis.Page = NamarMyFollowups;`, context, { filename: sourcePath });

function page(detail, selected = "ACTION-1") {
  opened.length = 0;
  formLinks.length = 0;
  const instance = Object.create(context.Page.prototype);
  instance.state = { source: "approvals", selected_name: selected, detail };
  return instance;
}

function expectNewTab(href) {
  assert.deepEqual(opened, [[href, "_blank", "noopener,noreferrer"]]);
  const target = new URL(href, origin);
  assert.equal(target.origin, origin);
  assert.ok(target.pathname.startsWith("/app/"));
}

{
  const detail = { reference_doctype: "Material Request", reference_name: "MREQ-11118" };
  const instance = page(detail);
  instance.open_approval();
  expectNewTab("/app/material-request/MREQ-11118");
  assert.deepEqual(formLinks, [{ doctype: "Material Request", name: "MREQ-11118" }]);
  assert.equal(instance.state.detail, detail);
  assert.equal(instance.state.selected_name, "ACTION-1");
}
{
  const instance = page({ document_type: "Sales Order", document_name: "SO / #?طلب" });
  instance.open_approval();
  expectNewTab("/app/sales-order/" + encodeURIComponent("SO / #?طلب"));
}
{
  const instance = page(null, "ACTION / #?123");
  instance.open_approval();
  expectNewTab("/app/workflow-action/" + encodeURIComponent("ACTION / #?123"));
  assert.deepEqual(formLinks, [{ doctype: "Workflow Action", name: "ACTION / #?123" }]);
  assert.equal(instance.state.detail, null);
  assert.equal(instance.state.selected_name, "ACTION / #?123");
}
{
  const instance = page({}, "");
  instance.open_approval();
  assert.deepEqual(opened, []);
}
{
  const instance = page({ reference_doctype: "//outside.example", reference_name: "javascript:alert(1)/../" });
  instance.open_approval();
  expectNewTab("/app/" + encodeURIComponent("//outside.example") + "/" + encodeURIComponent("javascript:alert(1)/../"));
}
for (const route of [
  "https://outside.example/app/sales-order/SO-1",
  "//outside.example/app/sales-order/SO-1",
  "javascript:alert(1)",
  "data:text/html,<script>alert(1)</script>",
  "https://otheruser@test.example.com/app/sales-order/SO-1",
  "/api/method/write",
  "/app/../api/method/write",
]) {
  const instance = page({ reference_route: route });
  instance.open_reference();
  assert.deepEqual(opened, [], `Unsafe reference route must be rejected: ${route}`);
}
for (const route of ["/app/sales-order/SO-1", "app/sales-order/SO-1", "https://test.example.com/app/sales-order/SO-1"]) {
  const instance = page({ reference_route: route });
  instance.open_reference();
  expectNewTab(origin + "/app/sales-order/SO-1");
}
{
  const instance = page({ reference_doctype: "Purchase Invoice", reference_name: "PINV260115" });
  instance.open_reference();
  expectNewTab("/app/purchase-invoice/PINV260115");
}

console.log("My Followups document navigation new-tab tests passed.");

