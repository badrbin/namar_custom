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

test("inactive global asset leaves the document and form framework untouched", () => {
    const context = vm.createContext({
        frappe: { boot: { namar_kdt_enabled: false } },
        document: new Proxy({}, { get() { throw new Error("Inactive asset touched the document"); } }),
    });
    vm.runInContext(deskSource, context);
    assert.equal(context.frappe.namar_cutting_desk, undefined);
    assert.equal(context.frappe.__namar_kdt_desk_registered, undefined);
});

test("global asset registers before opening a form without changing unrelated form handlers or routing", () => {
    const unrelatedRefresh = () => {};
    const handlers = new Map([["Material Request", { refresh: unrelatedRefresh }]]);
    const listeners = [], selectors = [];
    const context = vm.createContext({
        frappe: {
            boot: { namar_kdt_enabled: true },
            ui: { form: { on: (doctype, events) => handlers.set(doctype, events) } },
            call() { throw new Error("Loading an asset must not request data"); },
            set_route() { throw new Error("Loading an asset must not navigate"); },
        },
        document: {
            addEventListener: (...args) => listeners.push(args),
            removeEventListener() { throw new Error("First load has no prior handler"); },
            querySelectorAll(selector) { selectors.push(selector); return []; },
        },
    });
    vm.runInContext(deskSource, context);
    vm.runInContext(deskSource, context);
    assert.deepEqual([...handlers.keys()], ["Material Request", "KDT Cutting Desk"]);
    assert.equal(handlers.get("Material Request").refresh, unrelatedRefresh);
    assert.deepEqual(Object.keys(handlers.get("KDT Cutting Desk")), ["refresh"]);
    assert.equal(listeners.length, 1);
    assert.equal(listeners[0][0], "click");
    listeners[0][1]({ target: {}, preventDefault() { throw new Error("Unrelated click was blocked"); } });
    assert.deepEqual(selectors, [".kcd .kcd-multi[open]"]);
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

function manufacturingSource(required = 5, done = 2, changes = {}) {
    return { material_request: "MR-1", source_row: "ROW-1", door_row: 1, required_qty: required,
        manufactured_qty: done, remaining_qty: Math.max(required - done, 0),
        state: done >= required ? "completed" : "partial", excluded: true, ...changes };
}

function manufacturedRow(row, source, changes = {}) {
    return { ...row, creation_state: "manufactured", available_quantity: 0, already_created_quantity: 0,
        manufactured_quantity: row.quantity, blocked_quantity: 0, manufacturing_sources: [source],
        sources: [{ material_request: source.material_request, source_row: source.source_row,
            creation_state: "manufactured", manufacturing: source, issues: [] }], orders: [], ...changes };
}

function manufacturingReportFixture() {
    const { context, messages, routes } = reportContext();
    const report = reportFixture(), calls = [];
    report.data[0].الكمية = 4;
    report.data[1].الكمية = 1.25;
    const status = { can_create: true, report_signature: "report-v1", status_signature: "availability-v1",
        rows: report.data.slice(0, 2).map(row => ({ component_key: row._kdt_component_key,
            quantity: row.الكمية, available_quantity: row.الكمية, creation_state: "available", orders: [] })) };
    const button = { disabled: false, prop(name, value) { this[name] = value; } };
    context.getKdtComponentOrderState(report).button = button;
    context.renderCuttingComponentDashboard = () => {};
    context.frappe.call = async request => {
        assert.equal(request.method, "namar_kdt.api.call");
        calls.push({ action: request.args.action, payload: JSON.parse(request.args.payload) });
        return { message: request.args.action === "report_status" ? status : { created: true, order: { name: "CUT-NEW" } } };
    };
    return { context, report, status, button, calls, messages, routes };
}

test("fully manufactured sources stay visible and cannot create an empty or invented previous cutting order", async () => {
    const f = manufacturingReportFixture();
    f.status.can_create = false;
    f.status.rows = f.status.rows.map(row => manufacturedRow(row, manufacturingSource(5, 5)));
    await f.context.refreshKdtComponentReportStatus(f.report);
    const html = f.context.renderKdtComponentCreationStatus([f.status.rows[0]]);
    assert.match(html, /سبق تصنيعه؛ يُعتبر مقصوصًا/);
    assert.match(html, /كمية المكوّن المستبعدة بسبب التصنيع: <bdi>4<\/bdi>/);
    assert.doesNotMatch(html, /href=|أمر القص:|سبق إنشاؤه|يحتاج مراجعة/);
    assert.equal(f.report.data[0].الكمية, 4);
    assert.equal(f.button.disabled, true);
    const formatter = f.context.frappe.query_reports["نتائج التخصيم"].formatter;
    const formatted = formatter("حلق", null, { fieldname: "المكون" }, f.report.data[0], value => value);
    assert.match(formatted, /مستبعدة بسبب التصنيع/);
    assert.doesNotMatch(formatted, /أمر القص:/);
    await f.context.createKdtComponentOrderFromResults(f.report);
    assert.deepEqual(f.calls.map(call => call.action), ["report_status"]);
    assert.equal(f.routes.length, 0);
    assert.match(f.messages.at(-1).message, /مستبعدة بسبب التصنيع/);
});

test("partial manufacturing separates door counts from excluded component quantity and preserves real order links", () => {
    const { context, status } = manufacturingReportFixture();
    const row = manufacturedRow(status.rows[0], manufacturingSource(), {
        quantity: 0.375, manufactured_quantity: 0.375, orders: [{ name: "CUT-REAL", doctype: "KDT Cut Order" }],
    });
    const html = context.renderKdtComponentCreationStatus([row]);
    assert.match(html, /تم تصنيع <bdi>2<\/bdi> من <bdi>5<\/bdi> — المتبقي <bdi>3<\/bdi>/);
    assert.match(html, /كمية المكوّن المستبعدة بسبب التصنيع: <bdi>0\.375<\/bdi>/);
    assert.match(html, /السطر مستبعد مؤقتًا/);
    assert.match(html, /href="\/app\/kdt-cutting-desk\?cut_order=CUT-REAL"/);
    assert.match(html, /الباب 1/);
    assert.doesNotMatch(html, /يحتاج مراجعة|اعتماد|الباركود/);
});

test("manufacturing notice counts each door source once across components and raw variants", () => {
    const f = manufacturingReportFixture(), source = manufacturingSource();
    f.status.can_create = false;
    f.status.rows = f.status.rows.map(row => manufacturedRow(row, source));
    f.status.rows.push(manufacturedRow({ component_key: "another-raw-variant", quantity: 100 }, source));
    f.context.getKdtComponentOrderState(f.report).report_status = f.status;
    const notice = f.context.renderKdtComponentReportNotice(f.report);
    assert.match(notice, /1 من سطور الأبواب مستبعدة بسبب التصنيع/);
    assert.match(notice, /السطر المصنّع جزئيًا مستبعد كاملًا مؤقتًا/);
    assert.doesNotMatch(notice, /3 من سطور|100|لا تتوفر صلاحية/);
    assert.equal(f.context.getKdtComponentManufacturingSources(f.status.rows).length, 1);
    const html = f.context.renderKdtComponentManufacturingStatus(f.status.rows);
    assert.equal((html.match(/class="kdt-report-manufacturing-source"/g) || []).length, 1);
});

test("manufacturing fallback is HTML-safe and zero component quantities do not hide excluded sources", () => {
    const { context, status } = manufacturingReportFixture();
    const source = manufacturingSource(5, 2, {
        material_request: "<img src=x onerror=bad>", source_row: "ROW-<script>", door_label: 'باب <خاص> "أبيض"',
    });
    const row = manufacturedRow({ quantity: 0 }, source);
    delete row.manufacturing_sources;
    const html = context.renderKdtComponentCreationStatus([row]);
    assert.match(html, /مستبعدة بسبب التصنيع: <bdi>0<\/bdi>/);
    assert.match(html, /تم تصنيع/);
    assert.match(html, /&lt;img/);
    assert.match(html, /باب &lt;خاص&gt; &quot;أبيض&quot;/);
    assert.doesNotMatch(html, /<img|<script|<خاص>/);
    assert.equal(context.getKdtComponentManufacturingSources([row]).length, 1);
    assert.doesNotMatch(context.renderKdtComponentCreationStatus([status.rows[0]]), /تصنيع|المتبقي/);
});

test("mixed manufactured and available sources use direct creation and keep report quantities and app routing", async () => {
    const f = manufacturingReportFixture();
    f.status.rows[0] = manufacturedRow(f.status.rows[0], manufacturingSource());
    await f.context.refreshKdtComponentReportStatus(f.report);
    assert.equal(f.button.disabled, false);
    await f.context.createKdtComponentOrderFromResults(f.report);
    assert.deepEqual(f.calls.map(call => call.action), ["report_status", "component_create_available"]);
    assert.deepEqual(f.calls[1].payload.component_keys, f.report.data.slice(0, 2).map(row => row._kdt_component_key));
    assert.equal(f.calls[1].payload.status_signature, "availability-v1");
    assert.equal(f.report.data[0].الكمية, 4);
    assert.deepEqual(f.routes, [["Form", "KDT Cutting Desk"]]);
    assert.equal(f.context.frappe.route_options.cut_order, "CUT-NEW");
});

test("manufacturing completed during creation refreshes the report without opening an empty order", async () => {
    const f = manufacturingReportFixture();
    await f.context.refreshKdtComponentReportStatus(f.report);
    const after = { ...f.status, can_create: false, status_signature: "availability-v2",
        rows: f.status.rows.map(row => manufacturedRow(row, manufacturingSource(5, 5))) };
    f.context.frappe.call = async request => {
        assert.equal(request.method, "namar_kdt.api.call");
        assert.equal(request.args.action, "component_create_available");
        return { message: { created: false, order: null, report_status: after, availability_changed: true,
            message: "المصادر مستبعدة بسبب التصنيع؛ لم يُنشأ أمر جديد." } };
    };
    await f.context.createKdtComponentOrderFromResults(f.report);
    assert.equal(f.routes.length, 0);
    assert.equal(f.button.disabled, true);
    assert.equal(f.report.__kdt_component_order.report_status.rows[0].creation_state, "manufactured");
    assert.equal(f.report.data[0].الكمية, 4);
    assert.match(f.messages.at(-1).message, /مستبعدة بسبب التصنيع/);
});

test("manufacturing annotations cannot authorize stale manually generated results", async () => {
    const f = manufacturingReportFixture();
    f.report.__cutting_manual_report = { installed: true, has_result: true, dirty: false,
        applied_filters: JSON.stringify(f.report.get_filter_values()) };
    f.status.rows[0] = manufacturedRow(f.status.rows[0], manufacturingSource());
    await f.context.refreshKdtComponentReportStatus(f.report);
    assert.equal(f.button.disabled, false);
    f.report.__cutting_manual_report.dirty = true;
    f.context.paintKdtComponentReportStatus(f.report);
    assert.equal(f.button.disabled, true);
    await f.context.createKdtComponentOrderFromResults(f.report);
    assert.deepEqual(f.calls.map(call => call.action), ["report_status"]);
    assert.equal(f.routes.length, 0);
    assert.match(f.messages.at(-1), /ولّد التقرير بالفلاتر الحالية/);
});
