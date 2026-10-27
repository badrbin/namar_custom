const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const reportName = "نتائج التخصيم";
const source = fs.readFileSync(path.join(__dirname, "../namar_kdt/namar_kdt/report/نتائج_التخصيم/نتائج_التخصيم.js"), "utf8");
const plain = value => JSON.parse(JSON.stringify(value));
const flush = async () => { for (let i = 0; i < 12; i += 1) await Promise.resolve(); };

function element(length = 1) {
    const attributes = {}, properties = {}, events = new Map(), children = new Map();
    let text = "", html = "";
    return {
        length, attributes, properties, events,
        attr(key, value) { if (typeof key === "object") Object.assign(attributes, key); else if (value === undefined) return attributes[key]; else attributes[key] = value; return this; },
        prop(key, value) { if (value === undefined) return properties[key]; properties[key] = value; return this; },
        text(value) { if (value === undefined) return text || html.replace(/<[^>]*>/g, ""); text = String(value); return this; },
        html(value) { if (value === undefined) return html; html = String(value); return this; },
        css() { return this; }, remove() { return this; }, empty() { html = ""; return this; },
        toggle() { return this; }, show() { return this; }, hide() { return this; },
        addClass() { return this; }, removeClass() { return this; },
        append() { return this; }, before() { return this; }, after() { return this; },
        find(selector) { if (!children.has(selector)) children.set(selector, element()); return children.get(selector); },
        prev() { return element(0); }, clone() { return element(); }, replaceWith() { return this; },
        on(name, callback) { events.set(name, callback); return this; },
        off(name) { if (name) { for (const key of events.keys()) if (key === name || key.startsWith(name + ".")) events.delete(key); } else events.clear(); return this; },
        click(callback) { if (callback) return this.on("click", callback); return this.trigger("click"); },
        trigger(name) { let result; for (const [key, callback] of events) if (key === name || key.startsWith(name + ".")) result = callback({ preventDefault() {} }); return result; },
    };
}

function componentRow(name = "حلق", request = "MR-1") {
    return {
        طلب_المواد: "ملخص", الحالة: "ملخص المخازن", الكمية: 2,
        _kdt_component_name: name,
        _kdt_component_key: JSON.stringify([name, "أبيض", "raw"]),
        _kdt_sources: [{ material_request: request, source_row: "ROW-1" }],
    };
}

function fixture(initial = { material_requests: ["MR-1"] }, options = {}) {
    const values = plain(initial), messages = [], calls = [], refreshes = [], buttons = new Map();
    const baseExports = [], loadingHidden = [], intervalsCleared = [], progressHidden = [];
    const context = vm.createContext({
        frappe: {
            query_reports: {}, utils: { escape_html: String },
            msgprint: value => messages.push(value), show_alert: value => messages.push(value),
            hide_progress: () => progressHidden.push(true),
            set_route() {}, datetime: { now_datetime: () => "2026-10-27 10:00:00" },
            call: args => {
                calls.push(args);
                if (args.method === "frappe.desk.query_report.run") {
                    let accept, reject;
                    const ajax = new Promise((yes, no) => { accept = yes; reject = no; });
                    // jQuery abort does not resolve the outer QueryReport callback promise.
                    // Keep delivery controllable to exercise a callback already queued in flight.
                    ajax.catch(() => {});
                    ajax.abort = () => {
                        ajax.aborted = true;
                        args.error?.({ statusText: "abort" });
                        args.always?.();
                        reject(new Error("abort"));
                    };
                    refreshes.push({
                        filters: plain(args.args.filters), ajax,
                        resolve(response) { args.callback?.(response); args.always?.(); accept(response); },
                        reject(error) { args.error?.(error); args.always?.(); reject(error); },
                    });
                    return ajax;
                }
                if (args.method !== "namar_kdt.api.call" || args.args.action !== "report_status") {
                    throw new Error("Unexpected RPC in report lifecycle test");
                }
                const payload = JSON.parse(args.args.payload);
                return Promise.resolve({ message: { can_create: true, report_signature: "current-report", status_signature: "current-status",
                    rows: payload.component_keys.map(component_key => ({ component_key, available_quantity: 2 })) } });
            },
            db: { get_link_options: async () => [] },
        },
        $: value => typeof value === "object" && value?.attr ? value : element(),
        __: value => value, crypto: { randomUUID: () => "manual-test" },
        setTimeout, clearTimeout, clearInterval: id => intervalsCleared.push(id), console,
    });
    vm.runInContext(source, context);
    // The dashboard layout has separate coverage; retain lifecycle/status logic here.
    context.renderCuttingComponentDashboard = () => {};
    const settings = context.frappe.query_reports[reportName];
    const report = {
        report_name: reportName, report_settings: settings, data: [], columns: [], raw_data: {},
        interval: options.progressInterval,
        refresh_button: element(),
        page: {
            btn_secondary: element(), btn_primary: element(),
            set_primary_action(label, handler) { this.btn_primary.text(label).off("click").on("click", handler); return this.btn_primary; },
            set_secondary_action(label, handler) { this.btn_secondary.text(label).off("click").on("click", handler); return this.btn_secondary; },
            add_inner_button(label, handler) { const button = element().text(label).on("click", handler); buttons.set(label, button); return button; },
            remove_inner_button(label) { buttons.delete(label); }, remove_menu_item() {},
            set_indicator() {},
        },
        $report: element(0), $message: element(), $status: element(),
        get_filter_values() { return plain(values); },
        get_filter_value(name) { return values[name]; },
        get_checked_items() { return this.data.filter(row => row._kdt_component_key); },
        set_route_filters(options) {
            for (const filter of settings.filters) if (Object.hasOwn(options || {}, filter.fieldname)) values[filter.fieldname] = options[filter.fieldname];
            return Promise.resolve();
        },
        toggle_message() {}, toggle_report() {}, show_status() {}, hide_status() {},
        show_loading_screen() {},
        hide_loading_screen() { loadingHidden.push(this.report_name); },
        export_report(format) { baseExports.push({ report_name: this.report_name, format }); return "base-export-result"; },
        load_report(name) { this.report_name = name; return "base-load-result"; },
        prepare_report_data(data) { this.raw_data = data; this.columns = data.columns || []; this.data = data.result || []; },
        render_datatable() {
            this.datatable = {
                options: settings.get_datatable_options({ events: {} }),
                rowmanager: { checkAll() {}, getCheckedRows: () => [], checkRow() {}, refreshRows() {} },
                datamanager: { rows: [] }, bodyRenderer: { visibleRowIndices: this.data.map((_, i) => i) },
                destroy() {},
            };
        },
        refresh() {
            const completion = new Promise(resolve => {
                this.last_ajax = context.frappe.call({
                    method: "frappe.desk.query_report.run", type: "GET",
                    args: { report_name: this.report_name, filters: plain(values) },
                    callback: resolve,
                    always: () => this.page.btn_secondary.prop("disabled", false),
                });
            });
            return completion.then(response => {
                // Frappe 15 clears rows but skips preparation/rendering for an empty response.
                if (response.message.result.length) {
                    this.prepare_report_data(response.message);
                    this.render_datatable();
                    settings.after_datatable_render(this.datatable);
                } else this.data = [];
                return response;
            });
        },
    };
    const rawRefresh = report.refresh;
    context.frappe.views = { QueryReport: { prototype: { refresh: rawRefresh, export_report: report.export_report } } };
    if (options.throttled) {
        let started = false;
        report.refresh = function(...args) {
            if (!started) { started = true; return rawRefresh.apply(this, args); }
            // Frappe throttles refresh before report-specific hooks run. A trailing
            // invocation occurs after a synchronous temporary RPC hook is restored.
            Promise.resolve().then(() => rawRefresh.apply(this, args));
            return undefined;
        };
    }
    context.frappe.query_report = report;
    report.filters = settings.filters.map(df => {
        const control = {
            df, fieldname: df.fieldname, fieldtype: df.fieldtype,
            values: plain(values[df.fieldname] || []), _selected_values: [], _options: [],
            wrapper: element(), $wrapper: element(), $list_wrapper: element(), $filter_input: element(),
            set_selectable_items(options) { this._options = options; },
            get_value() { return values[df.fieldname]; },
            set_value(value) { values[df.fieldname] = value; this.values = Array.isArray(value) ? value : []; return Promise.resolve(); },
            on_change: df.on_change,
        };
        return control;
    });
    settings.onload(report);
    const change = (name, value) => {
        values[name] = value;
        const control = report.filters.find(row => row.df.fieldname === name);
        if (!control) throw new Error("Unknown test filter: " + name);
        control.values = Array.isArray(value) ? value : [];
        // Equivalent to Frappe QueryReport's per-control onchange dispatch.
        if (control.on_change) return control.on_change(report);
        if (control.df.on_change) return control.df.on_change(report);
        return report.refresh(true);
    };
    const generate = () => report.page.btn_primary.trigger("click");
    const answer = async (index = refreshes.length - 1, rows = [componentRow()]) => {
        refreshes[index].resolve({ message: { columns: [{ fieldname: "المكون", _kdt_available_colors: ["أبيض"] }], result: rows } });
        await flush();
    };
    return { context, report, values, messages, calls, refreshes, buttons, change, generate, answer,
        baseExports, loadingHidden, intervalsCleared, progressHidden };
}

test("opening a slow report clears the core progress timer while waiting for explicit generation", async () => {
    const f = fixture(undefined, { progressInterval: 71 });
    await f.report.refresh();
    assert.deepEqual(f.intervalsCleared, [71]);
    assert.ok(f.progressHidden.length > 0);
    assert.equal(f.report.interval, null);
    assert.equal(f.refreshes.length, 0);
});

test("opening and changing several filters does not execute a report; one explicit generation does", async () => {
    const f = fixture();
    await f.report.refresh(); // QueryReport refresh_report calls refresh after onload.
    f.change("from_date", "2026-10-01");
    f.change("to_date", "2026-10-27");
    f.change("colors", ["أبيض"]);
    assert.equal(f.refreshes.length, 0);
    assert.equal(f.calls.length, 0);
    assert.match(f.report.page.btn_primary.text(), /توليد/);
    const completion = f.generate();
    assert.equal(f.refreshes.length, 1);
    assert.deepEqual(f.refreshes[0].filters, f.values);
    await f.answer();
    await completion;
    assert.equal(f.calls.filter(call => call.args?.action === "report_status").length, 1);
    assert.equal(f.buttons.get("إنشاء أمر قص").prop("disabled"), false);
});

test("repeated generate clicks while a request runs do not start duplicate calculations", async () => {
    const f = fixture();
    const first = f.generate();
    const second = f.generate();
    assert.equal(f.refreshes.length, 1);
    await f.answer();
    await Promise.all([first, second]);
});

test("a failed generation remains unusable and only an explicit retry starts another request", async () => {
    const f = fixture();
    const first = f.generate();
    f.refreshes[0].reject(new Error("Network interrupted"));
    await Promise.resolve(first).catch(() => {});
    await flush();
    assert.equal(f.refreshes.length, 1);
    assert.throws(() => f.context.getKdtComponentReportScope(f.report), /توليد|تحديث|فلاتر|مكونات/);
    assert.equal(f.buttons.get("إنشاء أمر قص").prop("disabled"), true);
    const second = f.generate();
    assert.equal(f.refreshes.length, 2);
    await f.answer(1);
    await second;
    assert.equal(f.buttons.get("إنشاء أمر قص").prop("disabled"), false);
});

test("changing filters after generation blocks creation, editing and exporting stale rows", async () => {
    const f = fixture();
    const completion = f.generate();
    await f.answer(); await completion;
    f.change("colors", ["أسود"]);
    assert.equal(f.refreshes.length, 1);
    assert.equal(f.buttons.get("إنشاء أمر قص").prop("disabled"), true);
    assert.throws(() => f.context.getKdtComponentReportScope(f.report), /توليد|تحديث|فلاتر/);
    const callsBefore = f.calls.length;
    await f.context.createKdtComponentOrderFromResults(f.report);
    assert.equal(f.calls.length, callsBefore);
    f.context.getKdtComponentOrderState(f.report).edit = { name: "CUT-1", revision: 1, component_scope: {} };
    assert.throws(() => f.context.getKdtComponentOrderSelection(f.report), /توليد|تحديث|فلاتر/);
    await f.context.openKdtComponentOrderPreview(f.report);
    assert.equal(f.calls.length, callsBefore);
    await f.report.export_report("Excel");
    assert.equal(f.calls.length, callsBefore);
    assert.ok(f.messages.some(message => /توليد|تحديث|فلاتر/.test(String(message))));
});

test("response for previous filters cannot become a current cutting scope", async () => {
    const f = fixture();
    const pending = f.generate();
    f.change("material_requests", ["MR-2"]);
    await f.answer(0, [componentRow("حلق", "MR-1")]);
    await pending;
    assert.equal(f.refreshes.length, 1);
    assert.throws(() => f.context.getKdtComponentReportScope(f.report), /توليد|تحديث|فلاتر|مكونات/);
    assert.equal(f.buttons.get("إنشاء أمر قص").prop("disabled"), true);
    assert.equal(f.calls.filter(call => call.args?.action === "report_status").length, 0);
    const fresh = f.generate();
    await f.answer(1, [componentRow("حلق", "MR-2")]);
    await fresh;
    assert.deepEqual(plain(f.context.getKdtComponentReportScope(f.report).payload.request_names), ["MR-2"]);
    assert.equal(f.buttons.get("إنشاء أمر قص").prop("disabled"), false);
});

test("rapid regeneration bypasses a trailing core throttle and rejects the older callback", async () => {
    const f = fixture({ material_requests: ["MR-1"] }, { throttled: true });
    const old = f.generate();
    f.change("material_requests", ["MR-2"]);
    const latest = f.generate();
    await flush();
    assert.equal(f.refreshes.length, 2);
    await f.answer(1, [componentRow("حلق", "MR-2")]);
    await latest;
    await f.answer(0, [componentRow("حلق", "MR-1")]);
    await old;
    assert.deepEqual(plain(f.context.getKdtComponentReportScope(f.report).payload.request_names), ["MR-2"]);
    assert.equal(f.calls.filter(call => call.args?.action === "report_status").length, 1);
    assert.equal(f.buttons.get("إنشاء أمر قص").prop("disabled"), false);
});

test("empty results from an old filter generation also stay stale", async () => {
    const f = fixture();
    const pending = f.generate();
    f.change("colors", ["أسود"]);
    await f.answer(0, []); await pending;
    const state = f.context.getKdtComponentOrderState(f.report);
    assert.notEqual(state.loaded_filters, JSON.stringify(f.values));
    assert.equal(f.buttons.get("إنشاء أمر قص").prop("disabled"), true);
});

test("a fresh empty result cannot leave the previous report rows available for cutting", async () => {
    const f = fixture();
    const initial = f.generate();
    await f.answer(0, [componentRow("حلق", "MR-1")]); await initial;
    assert.equal(f.buttons.get("إنشاء أمر قص").prop("disabled"), false);
    f.change("material_requests", ["MR-NO-MATCH"]);
    const pending = f.generate();
    await f.answer(1, []); await pending;
    assert.throws(() => f.context.getKdtComponentReportScope(f.report), /توليد|تحديث|فلاتر|مكونات/);
    assert.equal(f.buttons.get("إنشاء أمر قص").prop("disabled"), true);
    const callsBefore = f.calls.length;
    await f.context.createKdtComponentOrderFromResults(f.report);
    assert.equal(f.calls.length, callsBefore);
});

test("manual gating does not block another report when Frappe reuses its report instance", async () => {
    const f = fixture();
    f.report.report_name = "Stock Balance";
    const result = f.report.refresh(true);
    assert.equal(f.refreshes.length, 1);
    f.refreshes[0].resolve({ message: { columns: [], result: [] } });
    await result;
    assert.equal(f.calls.filter(call => call.method === "namar_kdt.api.call").length, 0);
});

test("the dirty target report does not override another report's export behavior", async () => {
    const f = fixture();
    f.change("colors", ["أسود"]);
    f.report.report_name = "Stock Balance";
    const result = await f.report.export_report("Excel");
    assert.equal(result, "base-export-result");
    assert.deepEqual(f.baseExports, [{ report_name: "Stock Balance", format: "Excel" }]);
    assert.equal(f.calls.length, 0);
    assert.equal(f.messages.length, 0);
});

test("a pending target response cannot hide another report's loading screen or replace its rows", async () => {
    const f = fixture();
    const pending = f.generate();
    f.report.report_name = "Stock Balance";
    f.report.interval = 99;
    f.report.page.btn_secondary.prop("disabled", true);
    const hiddenBefore = f.progressHidden.length;
    f.report.data = [{ item_code: "OTHER-REPORT-ROW" }];
    await f.answer(0, [componentRow()]);
    await pending;
    assert.deepEqual(f.loadingHidden, []);
    assert.equal(f.progressHidden.length, hiddenBefore);
    assert.equal(f.report.interval, 99);
    assert.equal(f.report.page.btn_secondary.prop("disabled"), true);
    assert.ok(!f.intervalsCleared.includes(99));
    assert.deepEqual(f.report.data, [{ item_code: "OTHER-REPORT-ROW" }]);
    assert.equal(f.calls.filter(call => call.method === "namar_kdt.api.call").length, 0);
});

test("navigation cancels the target generation before another report begins loading", async () => {
    const f = fixture();
    const pending = f.generate();
    assert.equal(f.report.load_report("Stock Balance"), "base-load-result");
    assert.equal(await pending, false);
    assert.equal(f.refreshes[0].ajax.aborted, true);
    assert.equal(f.context.getCuttingManualReportState(f.report).active, null);
    f.report.data = [{ item_code: "OTHER-REPORT-ROW" }];
    const hiddenBefore = f.loadingHidden.length;
    await f.answer(0, [componentRow()]);
    assert.equal(f.loadingHidden.length, hiddenBefore);
    assert.deepEqual(f.report.data, [{ item_code: "OTHER-REPORT-ROW" }]);
});

test("reopening the target report does not stack refresh wrappers or run automatically", async () => {
    const f = fixture();
    f.report.report_settings.onload(f.report);
    f.report.report_settings.onload(f.report);
    await f.report.refresh();
    assert.equal(f.refreshes.length, 0);
    const pending = f.generate();
    assert.equal(f.refreshes.length, 1);
    await f.answer(); await pending;
    assert.equal(f.calls.filter(call => call.args?.action === "report_status").length, 1);
});

test("an existing-order route prefills filters but requires generation before selected rows are usable", async () => {
    const f = fixture();
    const row = componentRow("حلق", "MR-EDIT");
    await f.report.set_route_filters({ cut_order_edit: {
        name: "CUT-1", revision: 2, completed_component_keys: [],
        component_scope: { filters: { material_requests: ["MR-EDIT"] }, component_keys: [row._kdt_component_key], request_names: ["MR-EDIT"] },
    } });
    assert.deepEqual(plain(f.values.material_requests), ["MR-EDIT"]);
    assert.equal(f.refreshes.length, 0);
    assert.throws(() => f.context.getKdtComponentOrderSelection(f.report), /توليد|تحديث|فلاتر|مكونات/);
    const pending = f.generate();
    await f.answer(0, [row]); await pending;
    const selected = f.context.getKdtComponentOrderSelection(f.report);
    assert.equal(selected.payload.name, "CUT-1");
    assert.equal(selected.payload.revision, 2);
    assert.deepEqual(plain(selected.payload.filters.material_requests), ["MR-EDIT"]);
});

test("product colors can be searched before the first generation without loading a report", () => {
    const f = fixture({});
    const options = f.context.getCuttingProductColorOptions("أبيض");
    assert.ok(options.some(option => option.value === "أبيض"));
    const unknown = f.context.getCuttingProductColorOptions("لون خاص");
    assert.ok(unknown.some(option => option.value === "لون خاص"));
    assert.equal(f.refreshes.length, 0);
    assert.equal(f.calls.length, 0);
});
