const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const root = path.join(__dirname, "..");
const deskSource = fs.readFileSync(path.join(root, "namar_kdt/public/js/desk.js"), "utf8");
const reportSource = fs.readFileSync(path.join(root, "namar_kdt/namar_kdt/report/نتائج_التخصيم/نتائج_التخصيم.js"), "utf8");
const plain = value => JSON.parse(JSON.stringify(value));

function deskContext(enabled = true) {
    const registrations = [];
    const context = vm.createContext({
        frappe: {
            boot: { namar_kdt_enabled: enabled },
            ui: { form: { on: (...args) => registrations.push(args) } },
        },
    });
    vm.runInContext(deskSource, context);
    return { context, registrations, Desk: context.frappe.namar_cutting_desk?.Desk };
}

function reportContext() {
    const messages = [], routes = [];
    const context = vm.createContext({
        frappe: {
            query_reports: {},
            utils: { escape_html: String },
            msgprint: message => messages.push(message),
            show_alert: message => messages.push(message),
            set_route: (...route) => routes.push(route),
        },
        // Report helpers only need text extraction in these behavior tests.
        $: () => ({ html(value) { this.value = value; return this; }, text() { return String(this.value || "").replace(/<[^>]*>/g, ""); } }),
        __: value => value,
        crypto: { randomUUID: () => "test-request-id" },
    });
    vm.runInContext(reportSource, context);
    return { context, messages, routes };
}

function componentRow(component, request, row) {
    return {
        طلب_المواد: "ملخص", الحالة: "ملخص المخازن",
        _kdt_component_name: component,
        _kdt_component_key: JSON.stringify([component, "أبيض", "raw"]),
        _kdt_sources: [{ material_request: request, source_row: row }],
    };
}

function reportFixture() {
    return {
        report_name: "نتائج التخصيم",
        data: [componentRow("حلق", "MR-1", "ROW-1"), componentRow("بنل", "MR-2", "ROW-2"), { طلب_المواد: "MR-1", الحالة: "تصنيع" }],
        datatable: {},
        get_filter_values: () => ({ colors: ["أبيض"], material_requests: ["MR-1", "MR-2"] }),
        get_checked_items() { throw new Error("New orders must not depend on checkbox selection"); },
    };
}

test("inactive install registers nothing; active desk registers once even if loaded twice", () => {
    const inactive = deskContext(false);
    assert.equal(inactive.registrations.length, 0);
    assert.equal(inactive.context.frappe.__namar_kdt_desk_registered, undefined);
    inactive.context.frappe.boot.namar_kdt_enabled = true;
    vm.runInContext(deskSource, inactive.context);
    vm.runInContext(deskSource, inactive.context);
    assert.equal(inactive.registrations.length, 1);
    assert.equal(inactive.registrations[0][0], "KDT Cutting Desk");
    assert.deepEqual(Object.keys(inactive.registrations[0][1]), ["refresh"]);
});

test("desk and report RPCs use the native application endpoint", async () => {
    const { context, Desk } = deskContext();
    const requests = [];
    context.frappe.call = async args => { requests.push(args); return { message: { name: "CUT-1" } }; };
    await Desk.prototype.rpc.call({}, "order", { name: "CUT-1" });
    const report = reportContext().context;
    report.frappe.call = context.frappe.call;
    await report.callKdtComponentOrder("report_status", { request_names: ["MR-1"] });
    assert.equal(requests.length, 2);
    assert.ok(requests.every(request => request.method === "namar_kdt.api.call"));
    assert.deepEqual(JSON.parse(requests[1].args.payload), { request_names: ["MR-1"] });
});

test("new order scope includes all filtered component sources without requiring selection", () => {
    const { context } = reportContext();
    const report = reportFixture();
    const scope = context.getKdtComponentReportScope(report);
    assert.deepEqual(plain(scope.payload.request_names), ["MR-1", "MR-2"]);
    assert.equal(scope.payload.component_keys.length, 2);
    assert.deepEqual(plain(scope.payload.filters), report.get_filter_values());
    assert.equal(scope.rows.length, 2);
});

test("changed filters and incomplete source identities block creating against stale results", () => {
    const { context } = reportContext();
    const report = reportFixture();
    context.getKdtComponentOrderState(report).loaded_filters = JSON.stringify({ colors: ["أسود"] });
    assert.throws(() => context.getKdtComponentReportScope(report), /انتظر اكتمال/);
    delete report.__kdt_component_order.loaded_filters;
    delete report.data[1]._kdt_sources[0].source_row;
    assert.throws(() => context.getKdtComponentReportScope(report), /تعذر تحديد مصدر/);
});

test("normal report offers one create action and editing swaps it for save", () => {
    const { context } = reportContext();
    const buttons = new Map([
        ["تجهيز دفعة تشغيل", () => {}], ["إنشاء أمر قص من المحدد", () => {}],
    ]);
    const report = reportFixture();
    report.page = {
        add_inner_button(label, handler) { buttons.set(label, handler); return { attr() {} }; },
        remove_inner_button(label) { buttons.delete(label); }, remove_menu_item() {},
    };
    context.syncKdtComponentOrderButton(report);
    context.syncKdtComponentOrderButton(report);
    assert.deepEqual([...buttons.keys()], ["إنشاء أمر قص"]);
    context.getKdtComponentOrderState(report).edit = { name: "CUT-1" };
    context.syncKdtComponentOrderButton(report);
    assert.deepEqual([...buttons.keys()], ["حفظ تعديل أمر القص من المحدد"]);
});

test("checkbox selection appears only when editing an existing order", () => {
    const { context } = reportContext();
    context.frappe.query_report = reportFixture();
    const getOptions = context.frappe.query_reports["نتائج التخصيم"].get_datatable_options;
    assert.equal(getOptions({}).checkboxColumn, false);
    context.getKdtComponentOrderState(context.frappe.query_report).edit = { name: "CUT-1" };
    assert.equal(getOptions({}).checkboxColumn, true);
    assert.equal(getOptions({}).inlineFilters, true);
});

test("creating routes to the simple order, sending availability signatures and all report sources", async () => {
    const { context, routes } = reportContext();
    const report = reportFixture();
    const snapshot = context.getKdtComponentReportScope(report);
    Object.assign(context.getKdtComponentOrderState(report), {
        report_status: { can_create: true, report_signature: "report-v1", status_signature: "availability-v1", rows: [] },
        status_snapshot: snapshot,
    });
    context.paintKdtComponentReportStatus = () => {};
    const requests = [];
    context.frappe.call = async request => { requests.push(request); return { message: { created: true, order: { name: "CUT-1" } } }; };
    await context.createKdtComponentOrderFromResults(report);
    assert.equal(requests.length, 1);
    assert.equal(requests[0].args.action, "component_create_available");
    const payload = JSON.parse(requests[0].args.payload);
    assert.deepEqual(payload.request_names, ["MR-1", "MR-2"]);
    assert.equal(payload.report_signature, "report-v1");
    assert.equal(payload.status_signature, "availability-v1");
    assert.equal(payload.request_id, "test-request-id");
    assert.deepEqual(routes, [["Form", "KDT Cutting Desk"]]);
    assert.equal(context.frappe.route_options.cut_order, "CUT-1");
});

test("uncertain creation is not retried automatically and explicit retry keeps its command ID", async () => {
    const { context, routes } = reportContext();
    const report = reportFixture();
    Object.assign(context.getKdtComponentOrderState(report), {
        report_status: { can_create: true, report_signature: "report-v1", rows: [] },
        status_snapshot: context.getKdtComponentReportScope(report),
    });
    context.paintKdtComponentReportStatus = () => {};
    const requests = [];
    context.frappe.call = async request => { requests.push(request); throw new Error("Connection interrupted"); };
    await context.createKdtComponentOrderFromResults(report);
    assert.equal(requests.length, 1);
    assert.equal(routes.length, 0);
    await context.createKdtComponentOrderFromResults(report);
    assert.equal(requests.length, 2);
    assert.equal(JSON.parse(requests[0].args.payload).request_id, JSON.parse(requests[1].args.payload).request_id);
});

test("component toolbar exposes print and report editing, without execution or stock actions", () => {
    const { Desk } = deskContext();
    const slots = { toolbar: {}, "component-tools": {} };
    const desk = { slot: name => slots[name], componentRows: () => [{ quantity: 2 }], componentMachineItems: () => [] };
    Desk.prototype.renderComponentToolbar.call(desk);
    assert.match(slots.toolbar.innerHTML, /data-action="print-components"/);
    assert.match(slots["component-tools"].innerHTML, /data-action="edit"/);
    assert.doesNotMatch(slots.toolbar.innerHTML + slots["component-tools"].innerHTML, /data-action="(?:complete|recut|cancel|approve-print|correct)"/);
    assert.doesNotMatch(slots["component-tools"].innerHTML, /طباعة ملصقات KDT/);
    desk.componentMachineItems = () => [{ piece_key: "P-1" }];
    Desk.prototype.renderComponentToolbar.call(desk);
    assert.match(slots["component-tools"].innerHTML, /طباعة ملصقات KDT/);
});

test("component printing checks current revision, escapes source text, and does not complete cutting", async () => {
    const { Desk } = deskContext();
    const calls = [], documents = [];
    let printed = 0;
    const printWindow = {
        closed: false, document: { open() {}, write: value => documents.push(value), close() {} },
        focus() {}, print() { printed += 1; }, close() { throw new Error("Unexpected print failure"); },
    };
    const desk = {
        order: { name: "CUT-1", revision: 3 },
        rpc: async (action, payload) => { calls.push({ action, payload }); return { component_rows: [{ component_label: "<script>bad</script>", color: "أبيض", quantity: 2 }] }; },
        reportError: error => { throw error; },
    };
    await Desk.prototype.printComponents.call(desk, printWindow);
    assert.deepEqual(plain(calls), [{ action: "component_print_data", payload: { name: "CUT-1", revision: 3, allocation_keys: [] } }]);
    assert.equal(printed, 1);
    assert.match(documents[0], /اسم المكوّن الكامل/);
    assert.match(documents[0], /&lt;script&gt;bad&lt;\/script&gt;/);
    assert.doesNotMatch(documents[0], /<script>/);
});
