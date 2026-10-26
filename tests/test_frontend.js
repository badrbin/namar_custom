"use strict";

const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const filename = path.join(__dirname, "../demo/public/js/cutting.js");
const source = fs.readFileSync(filename, "utf8");

// Captured by evaluating the original TEST Client Script without calling its handlers.
const originalRegistrations = [
    { doctype: "Material Request", events: ["before_save", "refresh"] },
    {
        doctype: "Material Request Item",
        events: [
            "item_code", "custom_leaf_count", "custom_split_type", "custom_fixed_leaf_width",
            "custom_net_leaf", "custom_parquet", "custom_taksiya_1", "custom_taksiya_2",
            "custom_exception_component_1", "custom_exception_component_2", "custom_no_qitaat",
            "custom_without_frame_components", "custom_without_kalon", "custom_square_count",
            "custom_glass_type", "custom_glass_model", "custom_component_exclusion_groups",
            "custom_frame_type", "custom_result_sliding_type", "form_render",
        ],
    },
    { doctype: "Material Request Item", events: ["custom_is_cancelled_row"] },
];
const originalExports = [
    "make_manufacturing_stock_entries_from_current_form",
    "open_manual_delivery_component_package_dialog_for_current_form",
    "open_manual_manufacturing_dialog_for_current_form",
    "prepare_delivery_component_packages_for_print_for_current_form",
    "sync_delivery_component_packages_for_current_form",
];

function sandbox(boot) {
    const registrations = [];
    const effects = [];
    const forbidden = (name) => (...args) => {
        effects.push({ name, args });
        throw new Error(`Unexpected load-time side effect: ${name}`);
    };
    const window = {};
    const context = vm.createContext({
        window,
        frappe: {
            boot,
            ui: { form: { on(doctype, handlers) {
                assert.ok(Object.values(handlers).every((handler) => typeof handler === "function"));
                registrations.push({ doctype, events: Object.keys(handlers) });
            } } },
            call: forbidden("frappe.call"),
            xcall: forbidden("frappe.xcall"),
            db: new Proxy({}, { get: (_target, key) => forbidden(`frappe.db.${String(key)}`) }),
            model: { set_value: forbidden("frappe.model.set_value") },
        },
        setTimeout: forbidden("setTimeout"),
        fetch: forbidden("fetch"),
        document: new Proxy({}, { get: (_target, key) => forbidden(`document.${String(key)}`) }),
    });
    return {
        window, registrations, effects,
        load(script = source) { vm.runInContext(script, context, { filename, timeout: 2000 }); },
    };
}

test("inactive or missing boot flag registers nothing and can activate on a later load", () => {
    for (const boot of [undefined, {}, { demo_cutting_enabled: false }, { demo_cutting_enabled: 0 }]) {
        const env = sandbox(boot);
        env.load();
        assert.deepEqual(env.registrations, []);
        assert.deepEqual(Object.keys(env.window), []);
        assert.deepEqual(env.effects, []);
    }
    const boot = { demo_cutting_enabled: false };
    const env = sandbox(boot);
    env.load();
    boot.demo_cutting_enabled = true;
    env.load();
    assert.deepEqual(env.registrations, originalRegistrations);
});

test("active script preserves original event signatures and performs no load-time work", () => {
    const env = sandbox({ demo_cutting_enabled: true });
    env.load();
    assert.deepEqual(env.registrations, originalRegistrations);
    assert.equal(env.window.__demo_cutting_loaded, true);
    assert.deepEqual(env.effects, []);
});

test("a second active load does not duplicate handlers or replace exported handlers", () => {
    const env = sandbox({ demo_cutting_enabled: 1 });
    env.load();
    const exports = Object.fromEntries(originalExports.map((name) => [name, env.window[name]]));
    env.load();
    assert.deepEqual(env.registrations, originalRegistrations);
    for (const name of originalExports) assert.equal(env.window[name], exports[name]);
    assert.deepEqual(env.effects, []);
});

test("Frappe doctype_js concatenation tolerates a preceding hook without a semicolon", () => {
    for (const enabled of [false, true]) {
        const env = sandbox({ demo_cutting_enabled: enabled });
        // Frappe inserts comments/newlines between hooks and evaluates one function.
        // The inactive IIFE must not return from that outer framework function.
        env.load(`(function () {
window.precedingHook = 7
/* Adding demo/public/js/cutting.js */
${source}
/* Adding a following app hook */
window.followingHook = true;
})();`);
        assert.equal(env.window.precedingHook, 7);
        assert.equal(env.window.followingHook, true);
        assert.deepEqual(env.registrations, enabled ? originalRegistrations : []);
        assert.deepEqual(env.effects, []);
    }
});

test("existing HTML button callbacks remain available under their original window names", () => {
    const env = sandbox({ demo_cutting_enabled: true });
    env.load();
    const functions = Object.keys(env.window).filter((name) => typeof env.window[name] === "function").sort();
    assert.deepEqual(functions, originalExports);
    // Existing callbacks safely do nothing when no form is currently open.
    for (const name of originalExports) env.window[name]();
    assert.deepEqual(env.effects, []);
});

test("migration preserves the original source except for endpoint and blank-line whitespace", () => {
    const start = "// BEGIN MIGRATED CLIENT SCRIPT\n";
    const end = "// END MIGRATED CLIENT SCRIPT\n";
    const body = source.slice(source.indexOf(start) + start.length, source.indexOf(end));
    assert.equal(body.split('method: "demo.api.calculate_cutting"').length - 1, 1);
    assert.equal(body.includes("get_cutting_values_bulk_exclusions_test"), false);
    const restored = body.replace("demo.api.calculate_cutting", "get_cutting_values_bulk_exclusions_test");
    assert.equal(
        crypto.createHash("sha256").update(restored).digest("hex"),
        "359f3ffc362671bcc951bf7a3384e100e2fcbbeeee4e974536f079ed8198cd08",
        "The captured live behavior must not change during this extraction",
    );
});
