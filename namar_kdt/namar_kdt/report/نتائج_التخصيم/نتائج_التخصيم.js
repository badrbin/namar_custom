const cuttingWorkflowStyleOptions = [
    { value: "Primary", label: "أزرق" },
    { value: "Success", label: "أخضر" },
    { value: "Warning", label: "أصفر" },
    { value: "Danger", label: "أحمر" },
    { value: "Info", label: "سماوي" },
    { value: "__blank__", label: "بدون لون" }
];

function getCuttingWorkflowStyleOptions(txt) {
    const query = (txt || "").toLowerCase();
    return cuttingWorkflowStyleOptions.filter((option) => {
        return !query
            || (option.label || "").toLowerCase().includes(query)
            || (option.value || "").toLowerCase().includes(query);
    });
}

function getCuttingWorkflowIndicatorColor(style) {
    const colorMap = {
        Primary: "blue",
        Success: "green",
        Warning: "orange",
        Danger: "red",
        Info: "cyan",
        __blank__: "gray"
    };
    return colorMap[style] || "gray";
}

function getCuttingReportFilters(report) {
    if (report && typeof report.get_filter_values === "function") {
        return report.get_filter_values(true) || {};
    }

    if (report && typeof report.get_values === "function") {
        return report.get_values() || {};
    }

    return {};
}

function getCuttingProductColorOptions(txt) {
    const report = frappe.query_report;
    const columns = [...(report?.columns || []), ...(report?.raw_data?.columns || [])];
    const cached = report?.__kdt_component_order?.available_colors;
    const values = Array.isArray(cached) ? cached.slice() : columns.flatMap(column => Array.isArray(column._kdt_available_colors) ? column._kdt_available_colors : []);
    // Keep color search available before the first explicitly generated report.
    values.push("أسود", "بني", "بني محروق", "فيراني", "دارك جراي", "فلاور", "رصاصي", "عسلي",
        "بدون لون", "نيو سيلفر", "خشبي", "سيلفر", "تك", "أبيض", "قشر الجوز", "وايت تك", "بيج",
        "بني 011", "وايت تك 022", "-", ...normalizeKdtPlannerFilterValues(getCuttingReportFilters(report).colors));
    const query = String(txt || "").trim().toLocaleLowerCase();
    const options = [...new Set(values.map(value => String(value || "").trim()).filter(Boolean))]
        .sort((left, right) => left.localeCompare(right, "ar"))
        .map(value => ({ value, label: value === "-" ? "غير محدد" : value }))
        .filter(option => !query || option.value.toLocaleLowerCase().includes(query) || option.label.toLocaleLowerCase().includes(query))
        .map(option => ({ value: option.value, label: kdtComponentOrderEscape(option.label) }));
    return options.length || !query ? options : [{ value: String(txt).trim(), label: kdtComponentOrderEscape(String(txt).trim()) }];
}

function installCuttingMultiSelectPresentation(report) {
    (report.filters || []).forEach(control => {
        if (control.df.fieldtype !== "MultiSelectList" || control.__cuttingMultiSelectPresentation) return;
        const label = control.df.label, getData = control.df.get_data;
        const fallback = control.df.fieldname === "workflow_style" ? cuttingWorkflowStyleOptions
            : control.df.fieldname === "colors" ? getCuttingProductColorOptions("") : [];
        const findOption = (value, choices = []) => choices.concat(control._selected_values || [], control._options || [], fallback)
            .find(option => option.value === value);
        control.df = { ...control.df, get_data: async function(txt) {
            const choices = (await getData.call(this, txt) || []).map(option => typeof option === "string"
                ? { value: option, label: kdtComponentOrderEscape(option), description: "" } : { description: "", ...option });
            const selected = (control.values || []).map(value => findOption(value, choices)
                || { value, label: kdtComponentOrderEscape(value), description: "" });
            return selected.concat(choices).filter((option, index, options) => options.findIndex(other => other.value === option.value) === index);
        } };
        control.update_status = function() {
            const selected = this.values || [];
            const detail = selected.length === 1 ? $("<div>").html(findOption(selected[0])?.label || kdtComponentOrderEscape(selected[0])).text()
                : selected.length ? `${selected.length} اختيارات` : "";
            this.$list_wrapper.find(".status-text").text(detail ? `${label}: ${detail}` : label);
            const selectedOptions = selected.map(value => findOption(value) || { value, label: kdtComponentOrderEscape(value), description: "" });
            this.set_selectable_items(selectedOptions.concat(this._options || [])
                .filter((option, index, options) => options.findIndex(other => other.value === option.value) === index));
        };
        $(control.wrapper).attr({ dir: "rtl", title: label }).css("text-align", "right");
        control.$list_wrapper.find(".select-all-options").remove();
        control.$list_wrapper.find(".clear-selections").text("مسح الاختيارات");
        control.$filter_input.attr({ placeholder: "بحث…", "aria-label": `بحث ${label}` });
        control.$list_wrapper.find("[data-toggle=dropdown]").attr("aria-label", label);
        control.update_status();
        control.__cuttingMultiSelectPresentation = true;
    });
}

function getCuttingManualReportState(report) {
    return report.__cutting_manual_report || (report.__cutting_manual_report = {
        installed: false, epoch: 0, dirty: true, has_result: false, active: null, applied_filters: null
    });
}

function cuttingReportNeedsGeneration(report) {
    if (report?.report_name !== "نتائج التخصيم") return false;
    const manual = report?.__cutting_manual_report;
    return Boolean(manual?.installed && (manual.dirty || !manual.has_result
        || manual.applied_filters !== JSON.stringify(getCuttingReportFilters(report))));
}

function isCuttingManualRunCurrent(report, run) {
    const manual = getCuttingManualReportState(report);
    return report.report_name === "نتائج التخصيم" && manual.active === run && manual.epoch === run.epoch
        && run.signature === JSON.stringify(getCuttingReportFilters(report));
}

function stopCuttingManualReportProgress(report) {
    if (report.report_name !== "نتائج التخصيم") return;
    if (report.interval != null) clearInterval(report.interval);
    report.interval = null;
    frappe.hide_progress?.();
}

function syncCuttingManualReportControls(report) {
    if (report.report_name !== "نتائج التخصيم") return;
    const manual = getCuttingManualReportState(report), state = getKdtComponentOrderState(report);
    manual.button?.prop?.("disabled", Boolean(manual.active));
    state.button?.prop?.("disabled", Boolean(cuttingReportNeedsGeneration(report) || state.report_loading
        || state.busy || state.mutating || state.status_pending || (state.report_status && !state.report_status.can_create)
        || (manual.has_result && !(report.data || []).some(isCuttingStoreSummaryRow))));
    if (cuttingReportNeedsGeneration(report) && !manual.active) {
        stopCuttingManualReportProgress(report);
        report.toggle_report?.(false);
        report.toggle_message?.(true);
        report.$message?.html?.('<div class="msg-box" dir="rtl" style="text-align:right">اختر الفلاتر ثم اضغط «توليد التقرير». عند تغيير الفلاتر يلزم توليده من جديد.</div>');
        clearCuttingComponentDashboard(report);
    }
}

function finishCuttingManualRun(report, run, success) {
    const manual = getCuttingManualReportState(report), state = getKdtComponentOrderState(report);
    if (manual.active === run) {
        const accepted = success && isCuttingManualRunCurrent(report, run);
        manual.active = null;
        manual.rendering_run = null;
        manual.dirty = !accepted;
        manual.has_result = Boolean(accepted);
        manual.applied_filters = accepted ? run.signature : null;
        state.report_loading = false;
        state.loaded_filters = accepted ? run.signature : null;
        if (report.report_name === "نتائج التخصيم") {
            report.hide_loading_screen?.();
            stopCuttingManualReportProgress(report);
        }
        syncCuttingManualReportControls(report);
    }
    run.resolve?.(Boolean(success));
}

function markCuttingReportFiltersDirty(report) {
    if (!report || report.report_name !== "نتائج التخصيم") return;
    const manual = getCuttingManualReportState(report), state = getKdtComponentOrderState(report);
    manual.epoch += 1;
    manual.dirty = true;
    manual.applied_filters = null;
    state.loaded_filters = null;
    invalidateKdtComponentOrderPreview(report);
    const active = manual.active;
    if (active) {
        finishCuttingManualRun(report, active, false);
        active.request?.abort?.();
    }
    syncCuttingManualReportControls(report);
}

function generateCuttingReport(report) {
    if (!report || report.report_name !== "نتائج التخصيم") return Promise.resolve(false);
    const manual = getCuttingManualReportState(report), state = getKdtComponentOrderState(report);
    if (manual.active) return manual.active.promise;
    const run = { epoch: manual.epoch, signature: JSON.stringify(getCuttingReportFilters(report)), accepted: false };
    run.promise = new Promise(resolve => { run.resolve = resolve; });
    manual.active = run;
    manual.dirty = true;
    manual.has_result = false;
    manual.applied_filters = null;
    report.ignore_prepared_report = true;
    report.prepared_report_name = null;
    syncCuttingManualReportControls(report);
    // QueryReport creates its RPC synchronously. Guard that one callback before
    // core renders anything; restore the shared function before yielding control.
    const originalCall = frappe.call;
    let intercepted = false, result;
    frappe.call = function(options) {
        if (!intercepted && options?.method === "frappe.desk.query_report.run" && options.args?.report_name === "نتائج التخصيم") {
            intercepted = true;
            const guarded = { ...options,
                callback(response) {
                    if (!isCuttingManualRunCurrent(report, run) || response?.exc || !Array.isArray(response?.message?.result)) {
                        finishCuttingManualRun(report, run, false);
                        return;
                    }
                    run.accepted = true;
                    manual.rendering_run = run;
                    if (!response.message.result.length) report.data = [];
                    const columns = response.message.columns;
                    if (Array.isArray(columns)) state.available_colors = columns.flatMap(column => Array.isArray(column._kdt_available_colors) ? column._kdt_available_colors : []);
                    return options.callback?.apply(this, arguments);
                },
                error() {
                    const current = isCuttingManualRunCurrent(report, run);
                    finishCuttingManualRun(report, run, false);
                    if (current) return options.error?.apply(this, arguments);
                },
                always() {
                    try {
                        if (report.report_name === "نتائج التخصيم" && manual.epoch === run.epoch
                            && (!manual.active || manual.active === run)) return options.always?.apply(this, arguments);
                    }
                    finally { if (!run.accepted) finishCuttingManualRun(report, run, false); }
                }
            };
            run.request = originalCall.call(this, guarded);
            return run.request;
        }
        return originalCall.apply(this, arguments);
    };
    try { result = manual.run_refresh.call(report); }
    catch (error) { finishCuttingManualRun(report, run, false); throw error; }
    finally { frappe.call = originalCall; }
    if (!intercepted) {
        finishCuttingManualRun(report, run, false);
        return run.promise;
    }
    Promise.resolve(result).then(() => finishCuttingManualRun(report, run, run.accepted && isCuttingManualRunCurrent(report, run)),
        () => finishCuttingManualRun(report, run, false));
    return run.promise;
}

function installCuttingManualReport(report) {
    const manual = getCuttingManualReportState(report);
    if (!manual.installed) {
        manual.run_refresh = report.refresh;
        report.refresh = function() {
            if (this.report_name !== "نتائج التخصيم") return manual.run_refresh.apply(this, arguments);
            syncCuttingManualReportControls(this);
            return Promise.resolve(false);
        };
        if (typeof report.load_report === "function") {
            const originalLoad = report.load_report;
            report.load_report = function() {
                if (this.report_name === "نتائج التخصيم") markCuttingReportFiltersDirty(this);
                return originalLoad.apply(this, arguments);
            };
        }
        const originalExport = report.export_report;
        report.export_report = function(fileFormatType) {
            if (this.report_name !== "نتائج التخصيم") return originalExport?.apply(this, arguments);
            return exportCuttingReport(this, fileFormatType || "Excel");
        };
        manual.installed = true;
    }
    markCuttingReportFiltersDirty(report);
    (report.filters || []).forEach(control => {
        control.on_change = () => markCuttingReportFiltersDirty(report);
    });
    manual.button = report.page.set_primary_action("توليد التقرير", () => generateCuttingReport(report));
    report.refresh_button?.off?.("click").on?.("click", () => generateCuttingReport(report));
    report.page.remove_menu_item?.(__("Refresh"));
    report.page.add_menu_item?.(__("Refresh"), () => generateCuttingReport(report));
    syncCuttingManualReportControls(report);
}

function installCuttingSynchronousReportRefresh(report) {
    if (report.__cutting_sync_refresh) return;
    const coreRefresh = frappe.views?.QueryReport?.prototype?.refresh;
    if (typeof coreRefresh !== "function") return;
    const original = report.refresh;
    // QueryReport throttles its instance refresh. Manual generation already
    // deduplicates requests, and must start the guarded RPC synchronously.
    // Retain the framework's throttled refresh for every other report.
    report.refresh = function() {
        return (this.report_name === "نتائج التخصيم" ? coreRefresh : original).apply(this, arguments);
    };
    report.__cutting_sync_refresh = true;
}

function getCuttingVisibleRowIdx(report) {
    const visibleRowIndices = report?.datatable?.bodyRenderer?.visibleRowIndices;
    if (!Array.isArray(visibleRowIndices)) {
        return [];
    }

    const rowIdx = visibleRowIndices.slice();
    if (rowIdx.length + 1 === report?.data?.length) {
        rowIdx.push(rowIdx.length);
    }

    return rowIdx;
}

function getCuttingAppliedFilters(report, filters) {
    if (report && typeof report.get_applied_filters === "function") {
        return report.get_applied_filters(filters) || {};
    }

    return filters || {};
}

function downloadCuttingExportBlob(content, mimeType, filename) {
    const blob = content instanceof Blob ? content : new Blob([content], { type: mimeType });
    const url = window.URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    window.URL.revokeObjectURL(url);
}

function getCuttingExportFilename(report, extension) {
    const reportName = (report.report_name || "نتائج التخصيم")
        .trim()
        .replace(/[\\/:*?"<>|]+/g, "_")
        .replace(/\s+/g, "_");
    const now = new Date();
    const pad = (value) => String(value).padStart(2, "0");
    const date = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
    const time = `${pad(now.getHours())}-${pad(now.getMinutes())}-${pad(now.getSeconds())}`;
    return `${reportName}_${date}_${time}.${extension}`;
}

function exportCuttingReport(report, fileFormatType) {
    if (cuttingReportNeedsGeneration(report) || getKdtComponentOrderState(report).report_loading) {
        frappe.msgprint("ولّد التقرير بالفلاتر الحالية قبل التصدير.");
        return;
    }
    const filters = getCuttingReportFilters(report);
    const selectedFormat = fileFormatType === "CSV" ? "CSV" : "Excel";

    return frappe.call({
        method: "frappe.desk.query_report.run",
        args: {
            report_name: report.report_name || "نتائج التخصيم",
            filters: filters,
            ignore_prepared_report: report.ignore_prepared_report ? 1 : 0,
        },
        freeze: true,
        freeze_message: __("جارٍ تجهيز التصدير..."),
        callback: async function(response) {
            if (cuttingReportNeedsGeneration(report) || JSON.stringify(filters) !== JSON.stringify(getCuttingReportFilters(report))) {
                frappe.msgprint("تغيرت الفلاتر أثناء تجهيز التصدير. ولّد التقرير ثم أعد التصدير.");
                return;
            }
            const message = response && response.message ? response.message : {};
            const rows = Array.isArray(message.result) ? message.result : [];

            if (!rows.length) {
                frappe.msgprint(__("لا توجد بيانات للتصدير."));
                return;
            }

            const args = {
                cmd: "frappe.desk.query_report.export_query",
                report_name: report.report_name || "نتائج التخصيم",
                custom_columns: report.custom_columns?.length ? report.custom_columns : [],
                file_format_type: selectedFormat,
                filters: filters,
                applied_filters: filters,
                visible_idx: rows.map((row, index) => index),
                include_indentation: 0,
                include_filters: 0,
                include_hidden_columns: 0,
            };
            const formData = new URLSearchParams();
            Object.keys(args).forEach((key) => {
                const value = args[key];
                formData.set(key, typeof value === "string" ? value : JSON.stringify(value));
            });

            frappe.dom.freeze(__("جارٍ إنشاء ملف التصدير..."));
            try {
                const exportResponse = await fetch(frappe.request.url, {
                    method: "POST",
                    credentials: "same-origin",
                    headers: {
                        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                        "X-Frappe-CSRF-Token": frappe.csrf_token,
                    },
                    body: formData.toString(),
                });
                if (!exportResponse.ok) {
                    throw new Error(__("تعذر إنشاء ملف التصدير."));
                }

                const blob = await exportResponse.blob();
                const extension = selectedFormat === "CSV" ? "csv" : "xlsx";
                downloadCuttingExportBlob(
                    blob,
                    blob.type || "application/octet-stream",
                    getCuttingExportFilename(report, extension)
                );
            } catch (error) {
                frappe.msgprint(error && error.message ? error.message : __("تعذر إنشاء ملف التصدير."));
            } finally {
                frappe.dom.unfreeze();
            }
        },
    });
}

function ensureCuttingComponentDashboardStyles() {
    if ($("#cutting-component-dashboard-style").length) {
        return;
    }

    $("<style>", {
        id: "cutting-component-dashboard-style",
        text: `
            .cutting-component-dashboard {
                direction: rtl;
                text-align: right;
                margin: 0 0 12px;
                padding: 12px;
                border: 1px solid var(--border-color);
                border-radius: 8px;
                background: var(--card-bg);
            }
            .cutting-component-dashboard__header {
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 12px;
                margin-bottom: 10px;
            }
            .cutting-component-dashboard__title {
                margin: 0;
                font-size: 14px;
                font-weight: 700;
                color: var(--text-color);
            }
            .cutting-component-dashboard__meta {
                display: flex;
                flex-wrap: wrap;
                gap: 8px;
                font-size: 12px;
                color: var(--text-muted);
            }
            .cutting-component-dashboard__grid {
                display: grid;
                grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
                gap: 8px;
            }
            .cutting-component-card {
                padding: 10px;
                border: 1px solid var(--border-color);
                border-radius: 8px;
                background: var(--fg-color);
            }
            .cutting-component-card__label {
                color: var(--text-color);
                font-size: 12px;
                font-weight: 600;
                line-height: 1.5;
                overflow-wrap: anywhere;
            }
            .cutting-component-card__color {
                margin-top: 5px;
                color: var(--text-muted);
                font-size: 12px;
                line-height: 1.5;
                overflow-wrap: anywhere;
            }
            .cutting-component-card__qty {
                margin-top: 6px;
                color: var(--text-color);
                font-size: 22px;
                font-weight: 700;
                line-height: 1.1;
            }
            @media (max-width: 767px) {
                .cutting-component-dashboard {
                    padding: 10px;
                }
                .cutting-component-dashboard__header {
                    align-items: flex-start;
                    flex-direction: column;
                }
                .cutting-component-dashboard__grid {
                    grid-template-columns: repeat(auto-fill, minmax(130px, 1fr));
                }
            }
        `,
    }).appendTo("head");
}

function stripCuttingDashboardHtml(value) {
    if (value === null || value === undefined) {
        return "";
    }

    if (typeof value !== "string") {
        return String(value);
    }

    return $("<div>").html(value).text();
}

function escapeCuttingDashboardHtml(value) {
    return String(value || "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/\"/g, "&quot;");
}

function parseCuttingDashboardQty(value) {
    if (value === null || value === undefined || value === "") {
        return 0;
    }

    if (typeof value === "number") {
        return value;
    }

    const text = stripCuttingDashboardHtml(value).replace(/,/g, "").trim();
    if (frappe.utils.flt) {
        return frappe.utils.flt(text);
    }

    const number = parseFloat(text);
    return Number.isFinite(number) ? number : 0;
}

function formatCuttingDashboardQty(value) {
    const rounded = Math.round((parseCuttingDashboardQty(value) + Number.EPSILON) * 1000) / 1000;
    if (Number.isInteger(rounded)) {
        return String(rounded);
    }
    return rounded.toFixed(3).replace(/0+$/, "").replace(/\.$/, "");
}

function getCuttingDashboardText(value) {
    return stripCuttingDashboardHtml(value).trim();
}

function isCuttingStoreSummaryRow(row) {
    if (!row || typeof row !== "object" || Array.isArray(row)) {
        return false;
    }

    return getCuttingDashboardText(row["طلب_المواد"]) === "ملخص"
        && getCuttingDashboardText(row["الحالة"]) === "ملخص المخازن";
}

function isCuttingDashboardColumnVisible(column) {
    const toInt = (value) => frappe.utils.cint ? frappe.utils.cint(value) : parseInt(value, 10) || 0;
    return !(toInt(column?.hidden) || toInt(column?.hidden_due_to_user_permissions));
}

function getCuttingDashboardComponentOrder(report) {
    const columns = Array.isArray(report?.columns) ? report.columns : [];
    const componentOrder = [];
    let afterStoreDetails = false;

    columns.forEach((column) => {
        if (!isCuttingDashboardColumnVisible(column)) {
            return;
        }

        const fieldname = getCuttingDashboardText(column?.fieldname);
        const label = getCuttingDashboardText(column?.label || column?.name || column?.id);

        if (fieldname === "بيانات_المخازن" || label === "تفاصيل المخازن") {
            afterStoreDetails = true;
            return;
        }

        if (afterStoreDetails && label && !componentOrder.includes(label)) {
            componentOrder.push(label);
        }
    });

    return componentOrder;
}

function sortCuttingComponentDashboardTotals(totals, componentOrder) {
    if (!Array.isArray(componentOrder) || !componentOrder.length) {
        return totals;
    }

    const orderIndex = {};
    componentOrder.forEach((component, index) => {
        orderIndex[component] = index;
    });

    return totals.slice().sort((left, right) => {
        const leftIndex = Object.prototype.hasOwnProperty.call(orderIndex, left.component)
            ? orderIndex[left.component]
            : Number.MAX_SAFE_INTEGER;
        const rightIndex = Object.prototype.hasOwnProperty.call(orderIndex, right.component)
            ? orderIndex[right.component]
            : Number.MAX_SAFE_INTEGER;

        if (leftIndex !== rightIndex) {
            return leftIndex - rightIndex;
        }

        return left.original_order - right.original_order;
    });
}

function buildCuttingComponentDashboardTotals(rows, componentOrder) {
    const totals = new Map();

    (Array.isArray(rows) ? rows : []).forEach((row) => {
        if (!isCuttingStoreSummaryRow(row)) {
            return;
        }

        const component = String(row["المكون"] || row._component_label || row._kdt_component_name || "").trim();
        if (!component) {
            return;
        }

        let identity;
        try { identity = JSON.parse(row._kdt_component_key || "null"); } catch (_) { identity = null; }
        const componentId = row._kdt_component_name || (Array.isArray(identity) && identity[0]) || component;
        // Empty color is a generic component; '-' can be a colored component whose product color is unknown.
        const color = Array.isArray(identity) ? String(identity[1] || "") : String(row["اللون"] || "").trim();
        const key = JSON.stringify([componentId, color]);
        if (!totals.has(key)) {
            totals.set(key, {
                component_id: componentId,
                component: component,
                color: color,
                qty: 0,
                original_order: totals.size,
            });
        }

        totals.get(key).qty += parseCuttingDashboardQty(row["الكمية"]);
    });

    return sortCuttingComponentDashboardTotals(
        [...totals.values()],
        componentOrder
    );
}

function renderCuttingComponentDashboardHtml(totals, report) {
    const filters = getCuttingReportFilters(report);
    const selectedColors = normalizeKdtPlannerFilterValues(filters.colors);
    const resultColors = [...new Set((report?.data || []).filter(row => row._kdt_material_request_item).map(row => String(row["اللون"] || "").trim()).filter(Boolean))];
    const colors = selectedColors.length ? selectedColors : resultColors;
    const colorNames = colors.map(color => color === "-" ? "غير محدد" : color);
    const productScope = colorNames.length ? `${colorNames.length === 1 ? "لون المنتج" : "ألوان المنتجات"}: ${colorNames.join("، ")}` : "كل ألوان النتائج";
    const workflowStyles = normalizeKdtPlannerFilterValues(filters.workflow_style).map(value => cuttingWorkflowStyleOptions.find(option => option.value === value)?.label || value);
    const componentCount = new Set(totals.map(row => row.component_id)).size;
    const cards = totals.map(row => `<div class="cutting-component-card">
        <div class="cutting-component-card__label">${escapeCuttingDashboardHtml(row.component)}</div>
        <div class="cutting-component-card__color">${row.color ? `اللون: ${escapeCuttingDashboardHtml(row.color === "-" ? "غير محدد" : row.color)}` : `مكوّن دون لون مستقل<br>${selectedColors.length ? "ضمن منتجات الألوان المختارة" : "ضمن جميع ألوان النتائج"}`}</div>
        <div class="cutting-component-card__qty"><bdi>${escapeCuttingDashboardHtml(formatCuttingDashboardQty(row.qty))}</bdi></div>
        ${renderKdtComponentCreationStatus((report?.data || []).filter(source => {
            if (!source._kdt_creation_status) return false;
            try { const key = JSON.parse(source._kdt_component_key); return key[0] === row.component_id && key[1] === row.color; } catch (_) { return false; }
        }).map(source => source._kdt_creation_status))}
    </div>`).join("");
    return `<div class="cutting-component-dashboard__header">
        <h3 class="cutting-component-dashboard__title">ملخص المكونات بحسب اللون</h3>
        <div class="cutting-component-dashboard__meta"><span>${escapeCuttingDashboardHtml(productScope)}</span>
            ${workflowStyles.length ? `<span>لون الحالة: ${escapeCuttingDashboardHtml(workflowStyles.join("، "))}</span>` : ""}
            <span>عدد المكونات: ${componentCount}</span>
        </div>
    </div>${renderKdtComponentReportNotice(report)}<div class="cutting-component-dashboard__grid">${cards}</div>`;
}

function getCuttingComponentDashboardWrapper(report, datatable) {
    const $anchor = report?.$report?.length
        ? report.$report
        : $(datatable?.wrapper || datatable?.datatableWrapper || []);

    if (!$anchor.length) {
        return $();
    }

    let $wrapper = $anchor.prev(".cutting-component-dashboard");
    if (!$wrapper.length) {
        $wrapper = $('<div class="cutting-component-dashboard" dir="rtl"></div>');
        $anchor.before($wrapper);
    }

    return $wrapper;
}

function clearCuttingComponentDashboard(report) {
    const $report = report?.$report;
    if ($report?.length) {
        $report.prev(".cutting-component-dashboard").remove();
    }
}

function renderCuttingComponentDashboard(datatable) {
    const report = frappe.query_report;
    const totals = buildCuttingComponentDashboardTotals(
        report?.data || [],
        getCuttingDashboardComponentOrder(report)
    );
    const $wrapper = getCuttingComponentDashboardWrapper(report, datatable);

    if (!$wrapper.length) {
        return;
    }

    if (!totals.length) {
        $wrapper.remove();
        return;
    }

    ensureCuttingComponentDashboardStyles();

    $wrapper.html(renderCuttingComponentDashboardHtml(totals, report));
}

function installCuttingComponentDashboardRefreshGuard(report) {
    if (!report || report.__cutting_component_dashboard_refresh_guard) {
        return;
    }

    const originalRefresh = report.refresh;
    if (typeof originalRefresh !== "function") {
        return;
    }

    report.refresh = function() {
        clearCuttingComponentDashboard(report);
        return originalRefresh.apply(this, arguments);
    };
    report.__cutting_component_dashboard_refresh_guard = true;
}

/* KDT_CUTTING_BATCH_EXTENSION_START */
const KDT_CUTTING_PLANNER_PREFILL_KEY = "namar_kdt_cutting_planner_prefill";

function normalizeKdtPlannerFilterValues(value) {
    const source = Array.isArray(value)
        ? value
        : String(value || "").replace(/\n/g, ",").split(",");
    const values = [];
    source.forEach(function(entry) {
        const normalized = String(
            typeof entry === "string"
                ? entry
                : (entry && (entry.value || entry.name || entry.label)) || ""
        ).trim();
        if (normalized && !values.includes(normalized)) {
            values.push(normalized);
        }
    });
    return values;
}

function getKdtCuttingPlannerPrefill(report) {
    const filters = getCuttingReportFilters(report);
    const materialRequests = normalizeKdtPlannerFilterValues(filters.material_requests || filters.material_request);
    const itemGroups = normalizeKdtPlannerFilterValues(filters.item_groups || filters.item_group);

    return {
        material_requests: materialRequests,
        workflow_state: normalizeKdtPlannerFilterValues(filters.workflow_state),
        from_date: filters.from_date || "",
        to_date: filters.to_date || "",
        planned_from_date: filters.planned_date || "",
        planned_to_date: filters.planned_date || "",
        item_groups: itemGroups,
        prefill_components: normalizeKdtPlannerFilterValues(filters.component),
    };
}

function openKdtCuttingPlannerFromReport(report) {
    window.sessionStorage.setItem(
        KDT_CUTTING_PLANNER_PREFILL_KEY,
        JSON.stringify(getKdtCuttingPlannerPrefill(report))
    );
    frappe.set_route("Form", "KDT Cutting Planner");
}

function installKdtCuttingBatchButton(report) {
    if (!report || !report.page || report.__kdt_cutting_batch_button) {
        return;
    }
    report.page.add_inner_button(__("تجهيز دفعة تشغيل"), function() {
        openKdtCuttingPlannerFromReport(report);
    });
    report.__kdt_cutting_batch_button = true;
}
/* KDT_CUTTING_BATCH_EXTENSION_END */

/* KDT_COMPONENT_ORDER_EXTENSION_START */
function getKdtComponentOrderState(report) {
    return report.__kdt_component_order || (report.__kdt_component_order = { generation: 0, busy: false, edit: null });
}

function kdtComponentOrderEscape(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
}

function kdtComponentOrderQty(value) {
    const number = Number(value);
    return Number.isFinite(number) ? number.toLocaleString("en-US", { useGrouping: false, maximumFractionDigits: 12 }) : String(value || "");
}

function getKdtComponentOrderSelection(report) {
    const currentState = getKdtComponentOrderState(report);
    if (cuttingReportNeedsGeneration(report) || currentState.report_loading
        || (currentState.loaded_filters && currentState.loaded_filters !== JSON.stringify(getCuttingReportFilters(report)))) {
        throw new Error("ولّد التقرير بالفلاتر الحالية قبل تعديل أمر القص.");
    }
    const selected = typeof report.get_checked_items === "function" ? report.get_checked_items(false) : [];
    const rows = [], keys = new Set(), requests = new Set();
    for (const row of selected) {
        if (!row || !Object.values(row).some(value => value !== null && value !== undefined && String(value).trim())) continue;
        if (!isCuttingStoreSummaryRow(row)) throw new Error("حدد صفوف ملخص المكونات فقط؛ الاختيار الحالي يحتوي صفوف تفاصيل الأبواب. ألغِ تحديد صفوف التفاصيل ثم أعد المحاولة.");
        if (!row._kdt_component_key || !row._kdt_component_name || !Array.isArray(row._kdt_sources) || !row._kdt_sources.length) {
            throw new Error("بيانات مصادر المكونات غير مكتملة. حدّث التقرير ثم أعد تحديد صفوف المكونات.");
        }
        for (const source of row._kdt_sources) {
            if (!source.material_request || !source.source_row) throw new Error("تعذر تحديد مصدر أحد المكونات. حدّث التقرير ثم أعد المحاولة.");
            requests.add(source.material_request);
        }
        if (!keys.has(row._kdt_component_key)) { keys.add(row._kdt_component_key); rows.push(row); }
    }
    const state = getKdtComponentOrderState(report);
    const retainedKeys = state.edit?.completed_component_keys || [];
    if (state.edit) {
        // Completed allocations retain their original snapshot even if the live report group disappeared.
        retainedKeys.forEach(key => keys.add(key));
        (state.edit.component_scope?.request_names || []).forEach(name => requests.add(name));
    }
    if (!keys.size) throw new Error("حدد صف مكوّن واحدًا على الأقل من ملخص المكونات لإنشاء أمر القص.");
    if (requests.size > 40) throw new Error("المحدد يشمل أكثر من 40 طلب مواد. قلّل نطاق التقرير ثم أعد الاختيار.");
    const payload = { filters: JSON.parse(JSON.stringify(getCuttingReportFilters(report))), component_keys: [...keys], request_names: [...requests].sort() };
    if (state.edit) {
        payload.name = state.edit.name; payload.revision = state.edit.revision;
        if (Array.isArray(state.edit.component_scope?.allocation_keys)) payload.allocation_keys = state.edit.component_scope.allocation_keys.slice();
    }
    const visible = report.datatable?.bodyRenderer?.visibleRowIndices;
    const checked = report.datatable?.rowmanager?.getCheckedRows() || [];
    const visibleRows = new Set((visible || []).map(Number));
    const hidden = Array.isArray(visible) ? checked.filter(index => !visibleRows.has(Number(index)) && keys.has(report.data[index]?._kdt_component_key)).length : 0;
    return { payload, rows, hidden, retainedKeys, generation: state.generation, data: report.data,
        signature: JSON.stringify({ ...payload, component_keys: payload.component_keys.slice().sort() }) };
}

function isKdtComponentOrderSelectionCurrent(report, snapshot) {
    const state = getKdtComponentOrderState(report);
    if (report.report_name !== "نتائج التخصيم" || state.generation !== snapshot.generation || report.data !== snapshot.data) return false;
    try { return getKdtComponentOrderSelection(report).signature === snapshot.signature; } catch (_) { return false; }
}

function invalidateKdtComponentOrderPreview(report) {
    const state = getKdtComponentOrderState(report);
    state.generation += 1;
    state.report_status = null; state.status_snapshot = null; state.status_pending = null;
    state.status_error = ""; state.create_attempt = null;
    (report.data || []).forEach(row => { delete row._kdt_creation_status; });
    if (state.preview) { state.preview.cancelled = true; state.preview.dialog?.hide(); state.preview = null; }
}

function keepKdtCompletedComponentsSelected(report) {
    const state = getKdtComponentOrderState(report), manager = report.datatable?.rowmanager;
    if (state.clearing || !manager || !state.edit) return;
    const required = new Set(state.edit.completed_component_keys || []), checked = new Set(manager.getCheckedRows().map(Number));
    state.clearing = true;
    try { (report.data || []).forEach((row, index) => { if (required.has(row._kdt_component_key) && !checked.has(index)) manager.checkRow(index, true); }); }
    finally { state.clearing = false; }
}

function getKdtComponentPreviewIssues(rows) {
    const groups = new Map();
    rows.forEach(row => {
        const issues = row.issues?.length ? row.issues : (row.blocker ? [{ code: "legacy", message: row.blocker, sources: row.sources || [] }] : []);
        issues.forEach(issue => {
            const message = String(issue.message || row.blocker || "راجع بيانات المصدر قبل الاعتماد.");
            const key = `${issue.code || ""}\n${message}`;
            if (!groups.has(key)) groups.set(key, { code: issue.code, message, sources: new Map(), components: new Set() });
            const group = groups.get(key);
            group.components.add(row.component_label || row.component || "");
            (issue.sources || []).forEach(source => {
                const sourceKey = JSON.stringify([source.material_request, source.source_row, source.expected_component || source.expected_component_label]);
                if (!group.sources.has(sourceKey)) group.sources.set(sourceKey, source);
            });
        });
    });
    return [...groups.values()];
}

function renderKdtComponentIssueSource(source, issueCode) {
    const esc = kdtComponentOrderEscape;
    const expected = source.expected_component_label || source.expected_component;
    const saved = (source.saved_components || []).map(item => typeof item === "string" ? item : item.label || item.component).filter(Boolean);
    const fieldLabels = { height_mm: "الارتفاع", width_mm: "العرض", thickness_mm: "السماكة", component: "المكوّن", color: "اللون", raw_item: "الصنف الخام", quantity: "الكمية" };
    const missing = [...new Set((source.missing_field_labels || source.missing_fields || []).map(field => fieldLabels[field] || (/[^\x00-\x7F]/.test(field) ? field : "بيانات القص")))];
    const showPartCounts = ["panel_part_count_missing", "panel_part_count_mismatch", "panel_part_scope_mismatch"].includes(issueCode);
    return `<div class="kdt-preview-issue-source"><div><bdi>${esc(source.material_request)}</bdi>${source.door_row ? ` · الباب ${esc(source.door_row)}` : ""}${source.source_row ? ` · المصدر <bdi>${esc(source.source_row)}</bdi>` : ""}</div>
        ${expected ? `<div><span class="kdt-preview-muted">المكوّن المطلوب:</span> ${esc(expected)}</div>` : ""}
        ${saved.length ? `<div><span class="kdt-preview-muted">المكوّن المحفوظ:</span> ${esc(saved.join("، "))}</div>` : ""}
        ${showPartCounts && source.expected_part_count != null ? `<div><span class="kdt-preview-muted">عدد الأجزاء المطلوب:</span> <bdi>${esc(kdtComponentOrderQty(source.expected_part_count))}</bdi></div>` : ""}
        ${showPartCounts && source.saved_part_count != null ? `<div><span class="kdt-preview-muted">عدد الأجزاء المحفوظ:</span> <bdi>${esc(kdtComponentOrderQty(source.saved_part_count))}</bdi></div>` : ""}
        ${missing.length ? `<div><span class="kdt-preview-muted">البيانات الناقصة:</span> ${esc(missing.join("، "))}</div>` : ""}</div>`;
}

function renderKdtComponentOrderPreview(preview, snapshot) {
    const esc = kdtComponentOrderEscape, issues = getKdtComponentPreviewIssues(preview.rows);
    const rows = preview.rows.map(row => {
        const blocked = Boolean(row.blocker || row.issues?.length || row.state === "blocked");
        const status = blocked ? "يحتاج مراجعة" : ({ available: "متاح", reserved: "محجوز", completed: "تم القص" }[row.state] || "يحتاج مراجعة");
        return `<tr><td class="kdt-preview-component">${esc(row.component_label || row.component)}</td><td data-label="اللون">${esc(row.color || "بدون لون")}</td><td data-label="الكمية"><bdi>${esc(kdtComponentOrderQty(row.quantity))}</bdi></td><td data-label="المصادر">${esc((row.sources || []).length)}</td><td class="kdt-preview-status-cell"><span data-kdt-status class="kdt-preview-status${blocked ? " kdt-preview-status-blocked" : ""}">${esc(status)}</span></td></tr>`;
    }).join("");
    const issueHtml = issues.map(issue => `<details class="kdt-preview-issue"><summary><span class="kdt-preview-issue-message">${esc(issue.message)}</span><span class="kdt-preview-issue-count">عرض المصادر المتأثرة (${issue.sources.size})</span></summary><div class="kdt-preview-issue-components">المكوّن: ${esc([...issue.components].join("، "))}</div>${[...issue.sources.values()].map(source => renderKdtComponentIssueSource(source, issue.code)).join("")}</details>`).join("");
    const sourceHtml = preview.rows.map(row => {
        const orders = [...new Set((row.orders || []).map(order => typeof order === "string" ? order : order.name || order.order || "").filter(Boolean))];
        return `<details class="kdt-preview-source-group"><summary>${esc(row.component_label || row.component)} · ${esc(row.color || "بدون لون")} · ${(row.sources || []).length} مصادر</summary>
            ${orders.length ? `<div class="kdt-preview-orders">أوامر مرتبطة: ${esc(orders.join("، "))}</div>` : ""}
            ${(row.sources || []).map(source => `<div class="kdt-preview-source"><bdi>${esc(source.material_request)}</bdi> · ${esc(source.door_row ? `الباب ${source.door_row}` : source.source_row)} · الكمية <bdi>${esc(kdtComponentOrderQty(source.quantity))}</bdi></div>`).join("")}</details>`;
    }).join("");
    return `<div class="kdt-component-preview" dir="rtl"><style>
        .kdt-component-preview{direction:rtl;text-align:right;min-width:0;font-size:13px;line-height:1.6;color:var(--text-color,#263445)}
        .kdt-component-preview p{margin:0 0 10px}.kdt-preview-scroll{max-height:50vh;overflow:auto;overscroll-behavior:contain;padding:1px 2px 1px 0}
        .kdt-preview-table{width:100%;border-collapse:collapse;margin-bottom:12px}.kdt-preview-table th,.kdt-preview-table td{padding:9px 10px;border-bottom:1px solid var(--border-color,#e1e5ea);vertical-align:top;text-align:right}
        .kdt-preview-table th{white-space:nowrap;background:var(--subtle-fg,#f4f6f8);font-weight:600}.kdt-preview-component{min-width:150px;overflow-wrap:anywhere}
        .kdt-preview-status{display:inline-block;white-space:nowrap;padding:3px 8px;border-radius:5px;background:var(--subtle-fg,#edf1f5);font-size:12px;line-height:20px}.kdt-preview-status-blocked{background:#fff0db;color:#8a4900}
        .kdt-preview-issues{margin:12px 0;padding:12px;border:1px solid #edc786;background:#fffaf1;border-radius:8px;color:#66410a}.kdt-preview-issues h5{font-size:14px;margin:0 0 8px;color:inherit}
        .kdt-preview-issue{margin-top:10px}.kdt-preview-issue summary{cursor:pointer;line-height:1.75}.kdt-preview-issue-message{overflow-wrap:anywhere}.kdt-preview-issue-count{display:block;font-size:12px;margin-top:4px;text-decoration:underline}
        .kdt-preview-issue-components{margin-top:10px;overflow-wrap:anywhere}.kdt-preview-issue-source{margin-top:8px;padding:9px 10px;border:1px solid #ead8ba;border-radius:6px;background:var(--card-bg,#fff);color:var(--text-color,#263445);overflow-wrap:anywhere}
        .kdt-preview-source-group{padding:10px 0;border-top:1px solid var(--border-color,#e1e5ea)}.kdt-preview-source-group summary{cursor:pointer;font-weight:600;overflow-wrap:anywhere}.kdt-preview-source,.kdt-preview-orders{padding:7px 0;overflow-wrap:anywhere}
        .kdt-preview-muted,.kdt-preview-footnote{color:var(--text-muted,#576575)}.kdt-preview-footnote{font-size:12px;margin-top:10px!important}.kdt-preview-notice{color:#8a4900}
        @media(max-width:640px){.kdt-preview-table,.kdt-preview-table tbody{display:block}.kdt-preview-table thead{display:none}.kdt-preview-table tr{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);padding:9px 10px;margin-bottom:8px;border:1px solid var(--border-color,#e1e5ea);border-radius:7px;gap:6px 10px}.kdt-preview-table td{display:block;border:0;padding:0;min-width:0}.kdt-preview-component{grid-column:1/-1;font-weight:600}.kdt-preview-table td[data-label]:before{content:attr(data-label) ": ";color:var(--text-muted,#576575)}.kdt-preview-status-cell{grid-column:2;grid-row:3;text-align:left!important}.kdt-preview-issues{padding:10px}.kdt-preview-scroll{max-height:48vh}}
        </style><p>المكونات المحددة بكمياتها ومصادرها من نتائج التخصيم.</p>
        <div class="kdt-preview-scroll">
        ${snapshot.hidden ? `<p class="kdt-preview-notice">${esc(snapshot.hidden)} من الصفوف المحددة خارج العرض الحالي بسبب فلتر الجدول، وهي ظاهرة في المعاينة أدناه.</p>` : ""}
        ${snapshot.payload.name ? `<p>تعديل الأمر <b>${esc(snapshot.payload.name)}</b>؛ تبقى المكونات التي تم قصها محفوظة ضمنه.</p>` : ""}
        ${snapshot.retainedKeys?.length ? '<p>المكونات التي سبق قصها محفوظة بالكميات والألوان المسجلة وقت القص، حتى لو تغيرت نتائج التقرير الحالية.</p>' : ""}
        <table class="kdt-preview-table"><thead><tr><th>المكوّن</th><th>اللون</th><th>الكمية</th><th>المصادر</th><th>الحالة</th></tr></thead><tbody>${rows}</tbody></table>
        ${!preview.can_approve || issues.length ? `<section class="kdt-preview-issues"><h5>ما يحتاج مراجعة قبل الاعتماد</h5><p>لن يُنشأ أمر جزئي؛ راجع المصادر الموضحة ثم أعد المعاينة.</p>${issueHtml || '<p>لا تتوفر تفاصيل إضافية. حدّث التقرير وأعد المعاينة.</p>'}</section>` : ""}
        <div class="kdt-preview-sources">${sourceHtml}</div></div>
        <p class="kdt-preview-footnote">تخطيط وتتبع وطباعة فقط؛ دون حركة مخزنية. ملصقات KDT للقطع المؤهلة فقط.</p></div>`;
}

async function callKdtComponentOrder(action, payload) {
    const response = await frappe.call({ method: "namar_kdt.api.call", args: { action, payload: JSON.stringify(payload) } });
    if (response.exc || !response.message) throw new Error("تعذر إتمام العملية. أعد المحاولة.");
    return response.message;
}

function getKdtComponentReportScope(report) {
    const state = getKdtComponentOrderState(report), filters = getCuttingReportFilters(report);
    if (cuttingReportNeedsGeneration(report)) throw new Error("ولّد التقرير بالفلاتر الحالية قبل إنشاء أمر القص.");
    if (state.report_loading || (state.loaded_filters && state.loaded_filters !== JSON.stringify(filters))) throw new Error("انتظر اكتمال تحديث نتائج التقرير قبل إنشاء أمر القص.");
    const rows = (report.data || []).filter(isCuttingStoreSummaryRow), keys = new Set(), requests = new Set();
    if (!rows.length) throw new Error("لا توجد مكونات في نتائج التقرير. اختر فلاتر التقرير لعرض المكونات المطلوبة.");
    rows.forEach(row => {
        if (!row._kdt_component_key || !row._kdt_component_name || !Array.isArray(row._kdt_sources) || !row._kdt_sources.length) throw new Error("بيانات مصادر المكونات غير مكتملة. حدّث التقرير ثم أعد المحاولة.");
        keys.add(row._kdt_component_key);
        row._kdt_sources.forEach(source => {
            if (!source.material_request || !source.source_row) throw new Error("تعذر تحديد مصدر أحد المكونات. حدّث التقرير ثم أعد المحاولة.");
            requests.add(source.material_request);
        });
    });
    if (requests.size > 40) throw new Error("النتائج تشمل أكثر من 40 طلب مواد. قلّل النطاق باستخدام فلاتر التقرير.");
    const payload = { filters: JSON.parse(JSON.stringify(filters)), component_keys: [...keys], request_names: [...requests].sort() };
    return { payload, rows, data: report.data, datatable: report.datatable, generation: getKdtComponentOrderState(report).generation,
        signature: JSON.stringify({ ...payload, component_keys: payload.component_keys.slice().sort() }) };
}

function isKdtComponentReportScopeCurrent(report, snapshot) {
    const state = getKdtComponentOrderState(report);
    if (report.report_name !== "نتائج التخصيم" || state.edit || state.generation !== snapshot.generation || report.data !== snapshot.data || report.datatable !== snapshot.datatable) return false;
    try { return getKdtComponentReportScope(report).signature === snapshot.signature; } catch (_) { return false; }
}

function kdtReportOrderLink(order) {
    const name = typeof order === "string" ? order : order?.name;
    if (!name) return "";
    const supplied = typeof order === "object" ? order.url : "";
    const url = typeof supplied === "string" && /^\/app\/[a-z0-9/?%=&_.-]+$/i.test(supplied) ? supplied
        : order?.doctype === "KDT Cutting Batch" ? `/app/kdt-cutting-batch/${encodeURIComponent(name)}` : `/app/kdt-cutting-desk?cut_order=${encodeURIComponent(name)}`;
    return `<a href="${kdtComponentOrderEscape(url)}" class="kdt-report-order-link"><bdi>${kdtComponentOrderEscape(name)}</bdi></a>`;
}

function hasKdtComponentBlockedSources(row) {
    return row.creation_state === "blocked" || (row.sources || []).some(source => source.creation_state === "blocked") || Number(row.blocked_quantity) > 0;
}

function renderKdtComponentCreationStatus(rows) {
    if (!rows?.length) return "";
    const sum = field => rows.reduce((total, row) => total + (Number(row[field]) || 0), 0);
    const available = sum("available_quantity"), created = sum("already_created_quantity"), blocked = sum("blocked_quantity");
    const orders = new Map();
    rows.forEach(row => (row.orders || []).forEach(order => { const name = typeof order === "string" ? order : order.name; if (name) orders.set(`${order.doctype || ""}:${name}`, order); }));
    const needsReview = rows.some(hasKdtComponentBlockedSources);
    const issueGroups = new Map();
    rows.filter(hasKdtComponentBlockedSources).forEach(row => {
        const sources = (row.sources || []).filter(source => source.creation_state === "blocked");
        const issues = sources.length ? sources.flatMap(source => (source.issues || []).map(issue => ({ ...source, ...issue }))) : row.issues || [];
        issues.forEach(issue => {
            const key = `${issue.code || ""}:${issue.message || ""}`;
            if (!issueGroups.has(key)) issueGroups.set(key, { code: issue.code, message: issue.message || "راجع بيانات المصدر.", sources: new Map() });
            const group = issueGroups.get(key);
            if (issue.material_request) group.sources.set(`${issue.material_request}:${issue.source_row}`, issue);
            (issue.sources || []).forEach(source => group.sources.set(`${source.material_request}:${source.source_row}`, source));
        });
    });
    return `<div class="kdt-report-creation-status" dir="rtl" style="font-size:12px;line-height:1.7;text-align:right;overflow-wrap:anywhere;margin-top:6px">
        ${created ? `<div>سبق إنشاؤه: <bdi>${kdtComponentOrderQty(created)}</bdi></div>` : ""}
        <div>المتاح لإنشاء أمر: <bdi>${kdtComponentOrderQty(available)}</bdi></div>
        ${needsReview ? `<div>يحتاج مراجعة${blocked ? `: <bdi>${kdtComponentOrderQty(blocked)}</bdi>` : ""}</div>` : ""}
        ${orders.size ? `<div>أمر القص: ${[...orders.values()].map(kdtReportOrderLink).join("، ")}</div>` : ""}
        ${issueGroups.size ? `<details><summary>سبب المراجعة</summary>${[...issueGroups.values()].map(issue => `<div style="margin-top:8px">${kdtComponentOrderEscape(issue.message)}${[...issue.sources.values()].map(source => renderKdtComponentIssueSource(source, issue.code)).join("")}</div>`).join("")}</details>` : ""}</div>`;
}

function renderKdtComponentReportNotice(report) {
    const state = report?.__kdt_component_order;
    if (!state || state.edit) return "";
    const status = state.report_status, rows = status?.rows || [];
    const created = rows.filter(row => Number(row.already_created_quantity) > 0).length;
    const blocked = rows.filter(hasKdtComponentBlockedSources).length;
    return `<div class="kdt-report-creation-notice" dir="rtl" style="text-align:right;font-size:13px;line-height:1.7;margin:0 0 12px;overflow-wrap:anywhere">
        <div>ينشئ أمرًا للمكونات المتاحة في نتائج التقرير.</div>
        ${state.status_pending ? "<div>جارٍ التحقق من أوامر القص المرتبطة…</div>" : ""}
        ${state.status_error ? `<div role="alert">${kdtComponentOrderEscape(state.status_error)}</div>` : ""}
        ${created ? `<div>${created} من صفوف المكونات سبق إنشاء أمر لها كليًا أو جزئيًا؛ تبقى ظاهرة، ولن تُضاف كمياتها السابقة مرة أخرى.</div>` : ""}
        ${blocked ? `<div role="alert">${blocked} من صفوف المكونات تحتاج مراجعة بيانات المصدر قبل إنشاء الأمر.</div>` : ""}
        ${status && !status.can_create && !blocked && !created ? "<div>لا تتوفر صلاحية أو مكونات متاحة لإنشاء الأمر.</div>" : ""}</div>`;
}

function paintKdtComponentReportStatus(report) {
    if (report.report_name !== "نتائج التخصيم") return;
    renderCuttingComponentDashboard(report.datatable);
    const table = report.datatable;
    (table?.datamanager?.rows || []).forEach(row => row.forEach(cell => { if (cell.column?.id === "المكون") delete cell.html; }));
    table?.rowmanager?.refreshRows?.();
    const state = getKdtComponentOrderState(report);
    state.button?.prop("disabled", Boolean(cuttingReportNeedsGeneration(report) || state.report_loading || state.busy || state.mutating || state.status_pending || (state.report_status && !state.report_status.can_create)));
}

function applyKdtComponentReportStatus(report, snapshot, status) {
    const received = (status?.rows || []).map(row => row.component_key).sort();
    if (!status?.report_signature || JSON.stringify(received) !== JSON.stringify(snapshot.payload.component_keys.slice().sort())) throw new Error("لم تكتمل قراءة حالة جميع المكونات. حدّث التقرير ثم أعد المحاولة.");
    if (!isKdtComponentReportScopeCurrent(report, snapshot)) return false;
    const state = getKdtComponentOrderState(report), byKey = new Map(status.rows.map(row => [row.component_key, row]));
    snapshot.rows.forEach(row => { row._kdt_creation_status = byKey.get(row._kdt_component_key); });
    state.report_status = status; state.status_snapshot = snapshot; state.status_error = "";
    return true;
}

async function refreshKdtComponentReportStatus(report) {
    const state = getKdtComponentOrderState(report);
    if (state.edit) return null;
    let snapshot;
    try { snapshot = getKdtComponentReportScope(report); }
    catch (error) { state.status_error = error.message; paintKdtComponentReportStatus(report); return null; }
    if (state.status_pending?.snapshot.signature === snapshot.signature && isKdtComponentReportScopeCurrent(report, state.status_pending.snapshot)) return state.status_pending.promise;
    const pending = { snapshot };
    state.status_pending = pending; state.status_error = "";
    pending.promise = (async () => {
        try {
            const status = await callKdtComponentOrder("report_status", snapshot.payload);
            if (!isKdtComponentReportScopeCurrent(report, snapshot) || state.status_pending !== pending) return null;
            return applyKdtComponentReportStatus(report, snapshot, status) ? status : null;
        } catch (error) {
            if (isKdtComponentReportScopeCurrent(report, snapshot) && state.status_pending === pending) state.status_error = error.message || "تعذر التحقق من أوامر القص. أعد المحاولة.";
            return null;
        } finally {
            if (state.status_pending === pending) { state.status_pending = null; paintKdtComponentReportStatus(report); }
        }
    })();
    paintKdtComponentReportStatus(report);
    return pending.promise;
}

async function createKdtComponentOrderFromResults(report) {
    const state = getKdtComponentOrderState(report);
    if (state.busy || state.mutating || state.edit) return;
    let snapshot;
    try { snapshot = getKdtComponentReportScope(report); }
    catch (error) { frappe.msgprint(kdtComponentOrderEscape(error.message)); return; }
    state.busy = true; state.button?.prop("disabled", true);
    try {
        let status = state.report_status;
        if (!status || !state.status_snapshot || !isKdtComponentReportScopeCurrent(report, state.status_snapshot)) status = await refreshKdtComponentReportStatus(report);
        if (!isKdtComponentReportScopeCurrent(report, snapshot)) return;
        if (!status) throw new Error(state.status_error || "تعذر التحقق من حالة المكونات. أعد المحاولة.");
        if (!status.can_create) {
            const blocked = status.rows.some(hasKdtComponentBlockedSources);
            const message = blocked ? "تحتاج بعض المكونات إلى مراجعة بيانات المصدر. تظهر الأسباب مع المكونات في التقرير." : "لا توجد مكونات متاحة لإنشاء أمر جديد، أو لا تتوفر صلاحية الإنشاء.";
            if (frappe.show_alert) frappe.show_alert({ message, indicator: "orange" }); else frappe.msgprint(message);
            return;
        }
        const fingerprint = JSON.stringify([snapshot.signature, status.report_signature, status.status_signature]);
        if (state.create_attempt?.fingerprint !== fingerprint) state.create_attempt = { fingerprint, request_id: typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : `report-${Date.now()}-${Math.random().toString(36).slice(2)}` };
        state.mutating = true;
        const result = await callKdtComponentOrder("component_create_available", { ...snapshot.payload, report_signature: status.report_signature,
            ...(status.status_signature ? { status_signature: status.status_signature } : {}), request_id: state.create_attempt.request_id });
        const order = result.order;
        if (result.created && !order?.name) throw new Error("تعذر قراءة رقم أمر القص. حدّث التقرير للتحقق من الأوامر المرتبطة قبل إعادة المحاولة.");
        if (!isKdtComponentReportScopeCurrent(report, snapshot)) {
            if (order?.name) frappe.msgprint(`تم إنشاء أمر القص ${kdtReportOrderLink(order)}.`);
            return;
        }
        if (result.report_status) applyKdtComponentReportStatus(report, snapshot, result.report_status);
        state.create_attempt = null;
        if (result.created && order?.name) {
            frappe.route_options = { cut_order: order.name }; frappe.set_route("Form", "KDT Cutting Desk");
        } else {
            const message = kdtComponentOrderEscape(result.message || "سبق إنشاء أمر للمكونات المتاحة. حُدثت روابط الأوامر في التقرير.");
            if (frappe.show_alert) frappe.show_alert({ message, indicator: "blue" }); else frappe.msgprint(message);
        }
    } catch (error) { if (isKdtComponentReportScopeCurrent(report, snapshot)) frappe.msgprint(kdtComponentOrderEscape(error.message || "تعذر إنشاء أمر القص. أعد المحاولة.")); }
    finally { state.busy = false; state.mutating = false; if (report.report_name === "نتائج التخصيم") paintKdtComponentReportStatus(report); }
}

async function openKdtComponentOrderPreview(report) {
    const state = getKdtComponentOrderState(report);
    if (state.busy || state.mutating || state.preview?.submitting) return;
    let snapshot;
    try { snapshot = getKdtComponentOrderSelection(report); }
    catch (error) { frappe.msgprint(kdtComponentOrderEscape(error.message)); return; }
    if (state.preview) { state.preview.cancelled = true; state.preview.dialog?.hide(); state.preview = null; }
    state.busy = true; state.button?.prop("disabled", true);
    try {
        const preview = await callKdtComponentOrder("component_preview", snapshot.payload);
        if (!isKdtComponentOrderSelectionCurrent(report, snapshot)) return;
        const received = (preview.rows || []).map(row => row.component_key).sort();
        if (JSON.stringify(received) !== JSON.stringify(snapshot.payload.component_keys.slice().sort()) || !preview.preview_signature) {
            throw new Error("لم تكتمل معاينة جميع المكونات المحددة. حدّث التقرير ثم أعد المحاولة.");
        }
        const session = { cancelled: false, submitting: false, request_id: (typeof crypto !== "undefined" && crypto.randomUUID) ? crypto.randomUUID() : `component-${Date.now()}-${Math.random().toString(36).slice(2)}` };
        const isEdit = Boolean(snapshot.payload.name);
        const dialog = new frappe.ui.Dialog({ title: isEdit ? "مراجعة تعديل أمر القص" : "مراجعة أمر القص من المحدد", size: "large",
            fields: [{ fieldname: "preview", fieldtype: "HTML" }, ...(isEdit ? [{ fieldname: "reason", fieldtype: "Small Text", label: "سبب التعديل", reqd: 1 }] : [])],
            primary_action_label: isEdit ? "حفظ تعديل الأمر" : "إنشاء أمر القص",
            primary_action: async function() {
                if (session.cancelled || session.submitting || !preview.can_approve) return;
                if (!isKdtComponentOrderSelectionCurrent(report, snapshot)) {
                    frappe.msgprint("تغير التحديد أو نطاق التقرير. أغلق المعاينة وأعد مراجعة المحدد قبل الاعتماد."); return;
                }
                const values = dialog.get_values();
                if (!values || (isEdit && !String(values.reason || "").trim())) return;
                session.submitting = true; state.mutating = true; dialog.get_primary_btn().prop("disabled", true);
                try {
                    const order = await callKdtComponentOrder(isEdit ? "component_revise" : "component_approve", {
                        ...snapshot.payload, preview_signature: preview.preview_signature, request_id: session.request_id,
                        ...(isEdit ? { reason: String(values.reason).trim() } : {})
                    });
                    if (!order.name) throw new Error("لم يُرجع النظام اسم أمر القص. حدّث قائمة الأوامر للتحقق.");
                    if (session.cancelled || !isKdtComponentOrderSelectionCurrent(report, snapshot)) {
                        const message = `تم حفظ أمر القص ${kdtComponentOrderEscape(order.name)}. يمكنك فتحه من قائمة الأوامر داخل مكتب القص.`;
                        if (frappe.show_alert) frappe.show_alert({ message, indicator: "green" }); else frappe.msgprint(message);
                        return;
                    }
                    session.cancelled = true; dialog.hide(); state.preview = null;
                    frappe.route_options = { cut_order: order.name };
                    frappe.set_route("Form", "KDT Cutting Desk");
                } catch (error) { if (!session.cancelled) frappe.msgprint(kdtComponentOrderEscape(error.message || "تعذر حفظ أمر القص. أعد المحاولة.")); }
                finally { session.submitting = false; state.mutating = false; if (!session.cancelled) dialog.get_primary_btn().prop("disabled", false); }
            }
        });
        session.dialog = dialog; state.preview = session;
        dialog.on_hide = function() { session.cancelled = true; if (state.preview === session) state.preview = null; };
        dialog.$wrapper.attr("dir", "rtl").css("text-align", "right");
        dialog.fields_dict.preview.$wrapper.html(renderKdtComponentOrderPreview(preview, snapshot));
        dialog.show(); dialog.get_primary_btn().prop("disabled", !preview.can_approve);
    } catch (error) { if (isKdtComponentOrderSelectionCurrent(report, snapshot)) frappe.msgprint(kdtComponentOrderEscape(error.message || "تعذرت معاينة المحدد.")); }
    finally {
        state.busy = false;
        if (report.__cutting_manual_report?.installed) syncCuttingManualReportControls(report);
        else state.button?.prop("disabled", false);
    }
}

function afterKdtComponentOrderRender(report) {
    if (!report || report.report_name !== "نتائج التخصيم") return;
    const state = getKdtComponentOrderState(report), manager = report.datatable?.rowmanager;
    const manual = report.__cutting_manual_report;
    if (manual?.installed) {
        const run = manual.rendering_run;
        if (!run || !isCuttingManualRunCurrent(report, run)) {
            syncCuttingManualReportControls(report);
            return;
        }
        manual.dirty = false;
        manual.has_result = true;
        manual.applied_filters = run.signature;
        state.loaded_filters = run.signature;
    } else {
        state.loaded_filters = JSON.stringify(getCuttingReportFilters(report));
    }
    state.report_loading = false;
    if (!manager) return;
    // Frappe DataTable preserves checkMap across refresh; indices must not move to new records.
    state.clearing = true;
    try {
        manager.checkAll(false);
        if (state.restore_edit_selection && state.edit) {
            const keys = new Set(state.edit.component_scope.component_keys || []);
            (report.data || []).forEach((row, index) => { if (keys.has(row._kdt_component_key)) manager.checkRow(index, true); });
            state.restore_edit_selection = false;
        }
    } finally { state.clearing = false; }
    keepKdtCompletedComponentsSelected(report);
    syncKdtComponentOrderButton(report);
    if (!state.edit) return refreshKdtComponentReportStatus(report);
}

function syncKdtComponentOrderButton(report) {
    const state = getKdtComponentOrderState(report);
    const createLabel = "إنشاء أمر قص", editLabel = "حفظ تعديل أمر القص من المحدد";
    const label = state.edit ? editLabel : createLabel, otherLabel = state.edit ? createLabel : editLabel;
    report.page.remove_inner_button?.(otherLabel);
    report.page.remove_menu_item?.(otherLabel);
    ["إنشاء أمر قص من المحدد", "تجهيز دفعة تشغيل"].forEach(oldLabel => { report.page.remove_inner_button?.(oldLabel); report.page.remove_menu_item?.(oldLabel); });
    state.button = report.page.add_inner_button(label, () => state.edit ? openKdtComponentOrderPreview(report) : createKdtComponentOrderFromResults(report));
    state.button?.attr("title", state.edit ? "حدد صفوف ملخص المكونات المطلوب حفظها في الأمر." : "ينشئ أمرًا للمكونات المتاحة في نتائج التقرير.");
    state.button?.prop?.("disabled", Boolean(cuttingReportNeedsGeneration(report) || state.report_loading || state.busy || state.mutating));
}

function installKdtComponentOrderRouteGuard(report) {
    if (!report || report.__kdt_component_route_guard || typeof report.set_route_filters !== "function") return;
    const original = report.set_route_filters;
    report.set_route_filters = function(options) {
        const route = options || frappe.route_options;
        if (this.report_name === "نتائج التخصيم" && route) {
            const state = getKdtComponentOrderState(this), edit = route.cut_order_edit;
            invalidateKdtComponentOrderPreview(this);
            state.edit = edit?.name && edit.revision != null && Array.isArray(edit.component_scope?.component_keys) ? JSON.parse(JSON.stringify(edit)) : null;
            state.restore_edit_selection = Boolean(state.edit);
            if (this.__cutting_manual_report?.installed) markCuttingReportFiltersDirty(this);
            if (state.edit) options = { ...state.edit.component_scope.filters, ignore_prepared_report: true };
        }
        return original.call(this, options);
    };
    report.__kdt_component_route_guard = true;
}

function installKdtComponentOrderReport(report) {
    installKdtComponentOrderRouteGuard(report);
    const state = getKdtComponentOrderState(report);
    if (!state.installed) {
        if (typeof report.render_datatable === "function") {
            const originalRender = report.render_datatable;
            report.render_datatable = function() {
                if (this.report_name === "نتائج التخصيم" && this.datatable) {
                    const edit = Boolean(getKdtComponentOrderState(this).edit);
                    if (this.datatable.options.checkboxColumn !== edit || this.datatable.options.inlineFilters !== edit) {
                        // DataTable.destroy leaves delegated listeners on its host; replace that host when changing modes.
                        this.datatable.destroy(); this.datatable = null;
                        const replacement = this.$report.clone(false).empty();
                        this.$report.replaceWith(replacement); this.$report = replacement;
                    }
                }
                return originalRender.apply(this, arguments);
            };
        }
        const originalRefresh = report.refresh;
        report.refresh = function() {
            const filtersAtRequest = JSON.stringify(getCuttingReportFilters(this));
            if (this.report_name === "نتائج التخصيم") {
                invalidateKdtComponentOrderPreview(this);
                state.report_loading = true; state.button?.prop("disabled", true);
                state.clearing = true;
                try { this.datatable?.rowmanager?.checkAll(false); } finally { state.clearing = false; }
            }
            const result = originalRefresh.apply(this, arguments);
            const request = this.last_ajax, generation = state.generation;
            if (this.report_name === "نتائج التخصيم" && !this.__cutting_manual_report?.installed && typeof request?.then === "function") {
                // Frappe skips prepare_report_data on empty results, so preserve color metadata from the response itself.
                request.then(response => {
                    if (this.report_name !== "نتائج التخصيم" || this.last_ajax !== request || state.generation !== generation) return;
                    const columns = response?.message?.columns;
                    if (Array.isArray(columns)) state.available_colors = columns.flatMap(column => Array.isArray(column._kdt_available_colors) ? column._kdt_available_colors : []);
                    if (Array.isArray(response?.message?.result) && !response.message.result.length) {
                        state.report_loading = false; state.loaded_filters = filtersAtRequest;
                        state.button?.prop("disabled", true);
                    }
                }, () => {});
            }
            return result;
        };
        state.installed = true;
    }
    // QueryReport reuses its instance but clears the toolbar when switching reports.
    syncKdtComponentOrderButton(report);
}
/* KDT_COMPONENT_ORDER_EXTENSION_END */

frappe.query_reports["نتائج التخصيم"] = {
    filters: [
        {
            fieldname: "from_date",
            label: "من تاريخ",
            fieldtype: "Date"
        },
        {
            fieldname: "to_date",
            label: "إلى تاريخ",
            fieldtype: "Date"
        },
        {
            fieldname: "planned_date",
            label: "تاريخ التوريد المتوقع",
            fieldtype: "Date"
        },
        {
            fieldname: "workflow_state",
            label: "الحالة",
            fieldtype: "MultiSelectList",
            get_data: function (txt) {
                return frappe.db.get_link_options("Workflow State", txt, {}, 100);
            }
        },
        {
            fieldname: "workflow_style",
            label: "لون الحالة",
            fieldtype: "MultiSelectList",
            get_data: function (txt) {
                return getCuttingWorkflowStyleOptions(txt);
            }
        },
        {
            fieldname: "colors",
            label: "لون المنتج",
            fieldtype: "MultiSelectList",
            get_data: getCuttingProductColorOptions
        },
        {
            fieldname: "component",
            label: "المكون",
            fieldtype: "MultiSelectList",
            get_data: function (txt) {
                return frappe.db.get_link_options("Store Component", txt, {}, 100);
            }
        },
        {
            fieldname: "item_group",
            label: "الايتم قروب",
            fieldtype: "Link",
            options: "Item Group"
        },
        {
            fieldname: "material_requests",
            label: "طلب المواد",
            fieldtype: "MultiSelectList",
            get_data: function (txt) {
                const workflowStates = normalizeKdtPlannerFilterValues(
                    frappe.query_report && typeof frappe.query_report.get_filter_value === "function"
                        ? frappe.query_report.get_filter_value("workflow_state")
                        : []
                );
                const filters = workflowStates.length
                    ? { workflow_state: ["in", workflowStates] }
                    : {};
                return frappe.db.get_link_options("Material Request", txt, filters, 100);
            }
        },
    ],
    formatter: function (value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);
        if (column.fieldname === "المكون" && data?._kdt_creation_status) {
            const links = (data._kdt_creation_status.orders || []).map(kdtReportOrderLink).filter(Boolean);
            return `${value}${links.length ? ` <span class="kdt-report-cell-order">· أمر القص: ${links.join("، ")}</span>` : ""}`;
        }
        if (column.fieldname === "الحالة" && data) {
            const style = data._workflow_style || "__blank__";
            const color = getCuttingWorkflowIndicatorColor(style);
            const text = frappe.utils.escape_html(data["الحالة"] || "-");
            return `<span class="indicator-pill ${color}">${text}</span>`;
        }
        return value;
    },
    onload: function (report) {
        report.ignore_prepared_report = true;
        installCuttingSynchronousReportRefresh(report);
        installCuttingComponentDashboardRefreshGuard(report);
        installKdtComponentOrderReport(report);
        installCuttingMultiSelectPresentation(report);
        installCuttingManualReport(report);

    },
    after_datatable_render: function(datatable) {
        renderCuttingComponentDashboard(datatable);
        afterKdtComponentOrderRender(frappe.query_report);
    },
    get_datatable_options: function(options) {
        const previousCheck = options.events?.onCheckRow;
        const edit = Boolean(frappe.query_report?.__kdt_component_order?.edit);
        return { ...options, checkboxColumn: edit, inlineFilters: edit, events: { ...options.events, onCheckRow: function() {
            if (typeof previousCheck === "function") previousCheck.apply(this, arguments);
            if (frappe.query_report?.report_name === "نتائج التخصيم") keepKdtCompletedComponentsSelected(frappe.query_report);
        } } };
    },
};

// QueryReport consumes route options before onload, including when reopening this report.
installKdtComponentOrderRouteGuard(frappe.query_report);
