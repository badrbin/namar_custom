#!/usr/bin/env node
import fs from "node:fs";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const ROOT = new URL("..", import.meta.url).pathname.replace(/\/$/, "");
const DEFAULT_ENV = `${ROOT}/../erpnex_codex/.env.local`;

function loadEnv(file) {
  if (!fs.existsSync(file)) return;
  for (const raw of fs.readFileSync(file, "utf8").split(/\r?\n/)) {
    let line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    if (line.startsWith("export ")) line = line.slice(7).trim();
    const idx = line.indexOf("=");
    if (idx < 0) continue;
    const key = line.slice(0, idx).trim();
    const value = line.slice(idx + 1).trim().replace(/^['"]|['"]$/g, "");
    if (!process.env[key]) process.env[key] = value;
  }
}

function argValue(name, fallback = "") {
  const index = process.argv.indexOf(name);
  if (index >= 0 && process.argv[index + 1]) return process.argv[index + 1];
  return fallback;
}

function requireCondition(condition, message) {
  if (!condition) throw new Error(message);
}

loadEnv(argValue("--env-file", DEFAULT_ENV));

let base = argValue("--site", process.env.FRAPPE_TEST_SITE || process.env.FRAPPE_SITE || "");
base = base.replace(/\/$/, "").replace(/\/login$/, "");
if (base && !base.startsWith("http")) base = `https://${base}`;

const email = process.env.BROWSER_LOGIN_EMAIL;
const password = process.env.BROWSER_LOGIN_PASSWORD;
const materialRequest = argValue("--mr", "MREQ-06077-1");
const clickSync = process.argv.includes("--click-sync");
const chromeExecutable =
  process.env.CHROME_EXECUTABLE ||
  (fs.existsSync("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    ? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    : undefined);

requireCondition(base, "FRAPPE_TEST_SITE or --site is required");
requireCondition(email && password, "BROWSER_LOGIN_EMAIL and BROWSER_LOGIN_PASSWORD are required");

const launchOptions = { headless: true };
if (chromeExecutable) launchOptions.executablePath = chromeExecutable;

const browser = await chromium.launch(launchOptions);
const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
const consoleMessages = [];
page.on("console", (msg) => {
  if (["error", "warning"].includes(msg.type())) {
    consoleMessages.push(`${msg.type()}: ${msg.text()}`);
  }
});

async function gotoApp(path) {
  await page.goto(`${base}${path}`, { waitUntil: "domcontentloaded", timeout: 90000 }).catch(() => {});
  await page.waitForTimeout(5000);
}

await page.goto(`${base}/login`, { waitUntil: "domcontentloaded", timeout: 60000 }).catch(() => {});
if (await page.locator("#login_email").count()) {
  await page.fill("#login_email", email);
  await page.fill("#login_password", password);
  await page.click('button[type="submit"], .btn-login');
  await page.waitForTimeout(5000);
}

await gotoApp(`/app/material-request/${encodeURIComponent(materialRequest)}`);
await page.waitForSelector(".page-title, .form-layout", { timeout: 60000 });
await page.waitForTimeout(2500);

const materialRequestState = await page.evaluate(() => {
  const loaded = window.__namar_test_loaded_scripts || {};
  const dashboard = document.querySelector(".delivery-component-wrap");
  const dashboardText = dashboard ? dashboard.innerText || "" : "";
  return {
    url: location.href,
    title: document.title,
    bootFlag: !!(window.frappe && frappe.boot && frappe.boot.namar_test_client_scripts_enabled),
    loadedNames: Object.keys(loaded).sort(),
    hasDeliveryApi: !!(
      window.namar_delivery_tracking &&
      window.namar_delivery_tracking.delivery_components &&
      typeof window.namar_delivery_tracking.delivery_components.sync_packages === "function"
    ),
    hasDeliveryDashboard: !!dashboard,
    deliverySource: dashboard ? dashboard.getAttribute("data-delivery-tracking-source") : "",
    hasSyncButton: dashboardText.includes("تحديث حزم المكونات"),
    hasManualButton: dashboardText.includes("تسجيل حزمة يدويًا"),
    hasPrintLink: dashboardText.includes("طباعة باركود المكونات"),
  };
});

requireCondition(materialRequestState.bootFlag, "boot flag did not confirm migrated Client Scripts are enabled from app");
requireCondition(materialRequestState.hasDeliveryApi, "delivery component browser API is missing");
requireCondition(materialRequestState.hasDeliveryDashboard, "delivery component dashboard is missing");
requireCondition(materialRequestState.deliverySource === "custom-app", "delivery dashboard is not marked as custom-app");
requireCondition(materialRequestState.hasSyncButton, "delivery sync button is missing");
requireCondition(materialRequestState.hasManualButton, "manual delivery button is missing");
requireCondition(materialRequestState.hasPrintLink, "delivery print link is missing");

let manualDialogOpened = false;
const manualButton = page.locator(".manual-delivery-component-package-btn").first();
if (await manualButton.count()) {
  await page.evaluate(() => document.querySelector(".manual-delivery-component-package-btn")?.click());
  await page
    .waitForSelector(".modal:has-text('تسجيل حزم التوريد يدويًا')", { timeout: 7000 })
    .catch(() => {});
  manualDialogOpened = await page
    .locator(".modal")
    .filter({ hasText: "تسجيل حزم التوريد يدويًا" })
    .count()
    .then((count) => count > 0);
  await page.keyboard.press("Escape").catch(() => {});
}
requireCondition(manualDialogOpened, "manual delivery dialog did not open");

let syncClicked = false;
if (clickSync) {
  await page.getByRole("button", { name: /تحديث حزم المكونات/ }).first().click();
  await page.waitForTimeout(5000);
  syncClicked = true;
}

const salesOrderName = await page
  .evaluate(async () => {
    const result = await frappe.call({
      method: "frappe.client.get_list",
      args: {
        doctype: "Sales Order",
        fields: ["name"],
        filters: { docstatus: 1 },
        limit_page_length: 1,
        order_by: "modified desc",
      },
    });
    return (result.message && result.message[0] && result.message[0].name) || "";
  })
  .catch(() => "");

let salesOrderHasButton = false;
if (salesOrderName) {
  await gotoApp(`/app/sales-order/${encodeURIComponent(salesOrderName)}`);
  salesOrderHasButton = await page.getByText("Material Request").count().then((count) => count > 0);
  requireCondition(salesOrderHasButton, "Sales Order Material Request button is missing");
}

const leadName = await page
  .evaluate(async () => {
    const result = await frappe.call({
      method: "frappe.client.get_list",
      args: { doctype: "Lead", fields: ["name"], limit_page_length: 1, order_by: "modified desc" },
    });
    return (result.message && result.message[0] && result.message[0].name) || "";
  })
  .catch(() => "");

let leadHasMapButton = false;
if (leadName) {
  await gotoApp(`/app/lead/${encodeURIComponent(leadName)}`);
  leadHasMapButton = await page.getByText("تحديث الموقع من رابط قوقل").count().then((count) => count > 0);
  requireCondition(leadHasMapButton, "Lead Google Map button is missing");
}

const cuttingTemplateName = await page
  .evaluate(async () => {
    const result = await frappe.call({
      method: "frappe.client.get_list",
      args: { doctype: "Cutting Template", fields: ["name"], limit_page_length: 1, order_by: "modified desc" },
    });
    return (result.message && result.message[0] && result.message[0].name) || "";
  })
  .catch(() => "");

let cuttingHasButtons = false;
if (cuttingTemplateName) {
  await gotoApp(`/app/cutting-template/${encodeURIComponent(cuttingTemplateName)}`);
  const bodyText = await page.locator("body").innerText();
  cuttingHasButtons = bodyText.includes("إضافة أصناف متعددة") && bodyText.includes("إضافة نطاقات متعددة");
  requireCondition(cuttingHasButtons, "Cutting Template bulk buttons are missing");
}

await browser.close();

console.log(
  JSON.stringify(
    {
      site: base,
      materialRequest: materialRequestState,
      manualDialogOpened,
      syncClicked,
      salesOrder: { name: salesOrderName, hasMaterialRequestButton: salesOrderHasButton },
      lead: { name: leadName, hasMapButton: leadHasMapButton },
      cuttingTemplate: { name: cuttingTemplateName, hasBulkButtons: cuttingHasButtons },
      consoleMessages: consoleMessages.slice(0, 20),
    },
    null,
    2,
  ),
);
