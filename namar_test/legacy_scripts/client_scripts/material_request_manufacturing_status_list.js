var mrStateDurationThresholds = {};
var mrStateDurationThresholdsLoaded = false;

function mrStateDurationDefaultThresholds() {
    return { warning: 3, overdue: 7 };
}

function mrStateDurationGetThresholds(state) {
    var defaults = mrStateDurationDefaultThresholds();
    var stateKey = state || "";
    var thresholds = mrStateDurationThresholds[stateKey] || {};
    var warning = parseFloat(thresholds.warning);
    var overdue = parseFloat(thresholds.overdue);

    if (isNaN(warning) || warning < 0) warning = defaults.warning;
    if (isNaN(overdue) || overdue <= 0) overdue = defaults.overdue;
    if (overdue < warning) overdue = warning;

    return { warning: warning, overdue: overdue };
}

function mrStateDurationIsExcluded(state) {
    var stateKey = state || "";
    var settings = mrStateDurationThresholds[stateKey] || {};
    return !!settings.excluded;
}

function mrStateDurationIsCompletedStatus(doc) {
    var status = doc && doc.status ? doc.status : "";
    return ["Completed", "مكتمل", "Cancelled", "Canceled", "ملغي", "Rejected", "مرفوض"].indexOf(status) !== -1;
}

function mrStateDurationLoadThresholds(listview) {
    if (mrStateDurationThresholdsLoaded) return;
    mrStateDurationThresholdsLoaded = true;

    frappe.call({
        method: "frappe.client.get_list",
        args: {
            doctype: "Workflow State",
            fields: ["name", "custom_mr_duration_warning_days", "custom_mr_duration_overdue_days", "custom_mr_duration_excluded"],
            limit_page_length: 500
        },
        callback: function(response) {
            var rows = response && response.message ? response.message : [];
            rows.forEach(function(row) {
                mrStateDurationThresholds[row.name || ""] = {
                    warning: row.custom_mr_duration_warning_days,
                    overdue: row.custom_mr_duration_overdue_days,
                    excluded: !!row.custom_mr_duration_excluded
                };
            });
            if (listview && typeof listview.refresh === "function") {
                listview.refresh();
            }
        }
    });
}

function mrStateDurationDaysFromDoc(doc) {
    var sourceDate = null;
    var sourceLabel = "";
    var cachedState = doc && doc.custom_workflow_state_duration_state ? doc.custom_workflow_state_duration_state : "";
    var currentState = doc && doc.workflow_state ? doc.workflow_state : "";

    if (cachedState && currentState && cachedState !== currentState && doc && doc.modified) {
        sourceDate = frappe.datetime.str_to_obj(doc.modified);
        sourceLabel = "آخر تعديل للحالة";
    } else if (doc && doc.custom_workflow_state_entered_at) {
        sourceDate = frappe.datetime.str_to_obj(doc.custom_workflow_state_entered_at);
        sourceLabel = "تاريخ دخول الحالة";
    } else if (doc && doc.creation) {
        sourceDate = frappe.datetime.str_to_obj(doc.creation);
        sourceLabel = "تاريخ إنشاء الطلب";
    }

    if (sourceDate) {
        var now = frappe.datetime.now_datetime
            ? frappe.datetime.str_to_obj(frappe.datetime.now_datetime())
            : new Date();
        var diffMs = now.getTime() - sourceDate.getTime();
        if (!isNaN(diffMs)) {
            return {
                days: Math.max(Math.floor(diffMs / 86400000), 0),
                sourceLabel: sourceLabel,
                sourceValue: (cachedState && currentState && cachedState !== currentState ? doc.modified : doc.custom_workflow_state_entered_at) || doc.creation || ""
            };
        }
    }

    var seconds = parseFloat(doc && doc.custom_workflow_state_duration);
    if (!isNaN(seconds)) {
        return {
            days: Math.max(Math.floor(seconds / 86400), 0),
            sourceLabel: "حقل المدة",
            sourceValue: ""
        };
    }

    return { days: 0, sourceLabel: "تاريخ اليوم", sourceValue: "" };
}

function mrStateDurationFormat(value, field, doc) {
    if (mrStateDurationIsCompletedStatus(doc || {})) {
        return '<span class="indicator-pill green" title="حالة الطلب مكتملة">-</span>';
    }

    var workflowState = doc && doc.workflow_state ? doc.workflow_state : "";
    if (mrStateDurationIsExcluded(workflowState)) {
        var excludedTitle = workflowState ? "مستبعد من مؤشر المدة: " + workflowState : "مستبعد من مؤشر المدة";
        return '<span class="indicator-pill green" title="' + frappe.utils.escape_html(excludedTitle) + '">-</span>';
    }

    var durationInfo = mrStateDurationDaysFromDoc(doc || {});
    var days = durationInfo.days;

    var thresholds = mrStateDurationGetThresholds(workflowState);
    var color = "green";
    if (days >= thresholds.overdue) {
        color = "red";
    } else if (days >= thresholds.warning) {
        color = "orange";
    }

    var label = days === 1 ? "يوم واحد" : days + " يوم";
    var title = durationInfo.sourceLabel + ": " + (durationInfo.sourceValue || "-");
    return '<span class="indicator-pill ' + color + '" title="' + frappe.utils.escape_html(title) + '">' + frappe.utils.escape_html(label) + '</span>';
}

function mrManufacturingStatusFormat(value, field, doc) {
    if (!value) return "";

    var row = doc || {};
    var remainingCount = parseFloat(row.custom_manufacturing_remaining_count);
    var totalQty = parseFloat(row.custom_manufacturing_total_count);
    var hasRemaining = !isNaN(remainingCount) && remainingCount > 0;
    var hasTotalQty = !isNaN(totalQty) && totalQty > 0;
    var manufacturedCount = hasTotalQty && !isNaN(remainingCount) ? Math.max(totalQty - remainingCount, 0) : 0;
    var label = value;
    var indicatorColor = "blue";

    function formatCount(count) {
        if (isNaN(count)) return "";
        return count % 1 === 0 ? String(parseInt(count, 10)) : String(count);
    }

    function progressLabel(count) {
        var text = formatCount(count);
        if (hasTotalQty) {
            text += " / " + formatCount(totalQty);
        }
        return text;
    }

    if (value === "غير مصنع") {
        label = hasTotalQty ? progressLabel(0) : "0";
        indicatorColor = "red";
    } else if (value === "مصنع بالكامل") {
        label = hasTotalQty ? progressLabel(totalQty) : "1 / 1";
        indicatorColor = "green";
    } else if (value === "قيد التصنيع" && hasRemaining) {
        label = hasTotalQty ? progressLabel(manufacturedCount) : formatCount(remainingCount);
        indicatorColor = "yellow";
    } else if (value === "قيد التصنيع") {
        label = "قيد التصنيع";
        indicatorColor = "yellow";
    } else if (hasRemaining) {
        label = hasTotalQty ? progressLabel(manufacturedCount) : formatCount(remainingCount);
        indicatorColor = "yellow";
    } else if (!isNaN(remainingCount) && remainingCount <= 0) {
        label = hasTotalQty ? progressLabel(totalQty) : "1 / 1";
        indicatorColor = "green";
    }

    return '<span class="indicator-pill ' + indicatorColor + '" title="' + frappe.utils.escape_html(value) + '">' + frappe.utils.escape_html(label) + '</span>';
}

frappe.listview_settings["Material Request"] = {
    add_fields: [
        "creation",
        "modified",
        "status",
        "workflow_state",
        "custom_workflow_state_entered_at",
        "custom_workflow_state_duration",
        "custom_workflow_state_duration_state",
        "custom_manufacturing_status",
        "custom_manufacturing_remaining_count",
        "custom_manufacturing_total_count"
    ],
    onload: function(listview) {
        mrStateDurationLoadThresholds(listview);
    },
    formatters: {
        custom_workflow_state_duration: mrStateDurationFormat,
        custom_manufacturing_status: mrManufacturingStatusFormat
    }
};
