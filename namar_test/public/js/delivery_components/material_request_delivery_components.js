(function() {
  var api = window.NAMAR_DELIVERY_COMPONENT_API = window.NAMAR_DELIVERY_COMPONENT_API || {
    sync: "namar_test.delivery_components.api.sync_delivery_component_packages",
    get: "namar_test.delivery_components.api.get_delivery_component_packages",
    markReady: "namar_test.delivery_components.api.mark_delivery_component_package_ready",
    markEvent: "namar_test.delivery_components.api.mark_delivery_component_package_event",
    fulfillment: "namar_test.delivery_components.api.get_material_request_fulfillment_readiness",
    resolve: "namar_test.delivery_components.api.resolve_delivery_tracking_code"
  };
  var realtimeBound = false;
  var focusBound = false;
  var lastFocusReloadAt = 0;
  var preparingPrint = false;

  function flt(value) {
    return frappe.utils && frappe.utils.flt ? frappe.utils.flt(value || 0) : Number(value || 0);
  }

  function escapeHtml(value) {
    if (frappe.utils && frappe.utils.escape_html) {
      return frappe.utils.escape_html(String(value === undefined || value === null ? "" : value));
    }
    return String(value === undefined || value === null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function formatCount(value) {
    var number = Number(value || 0);
    if (Math.abs(number - Math.round(number)) < 0.000001) return String(Math.round(number));
    return String(Math.round(number * 1000) / 1000);
  }

  function formHasUnsavedDeliveryChanges(frm) {
    if (!frm) return false;
    if (typeof frm.is_dirty === "function" && frm.is_dirty()) return true;
    return !!(frm.doc && frm.doc.__unsaved);
  }

  function rows(frm) {
    return ((frm && frm.doc && frm.doc.custom_delivery_component_packages) || []).filter(function(row) {
      var active = row.active;
      var isActive = active === undefined || active === null || active === "" || Number(active) === 1;
      return isActive && (row.tracking_route || "تصنيع وتغليف") !== "لا يتتبع";
    });
  }

  function pendingRows(frm) {
    return rows(frm).filter(function(row) {
      return (row.tracking_status || row.status || "غير جاهز") === "غير جاهز";
    });
  }

  function printUrl(frm) {
    return "/printview?doctype=Material%20Request&name="
      + encodeURIComponent(frm.doc.name || "")
      + "&format="
      + encodeURIComponent("ملصق مكونات التوريد 4x3")
      + "&no_letterhead=1";
  }

  function statCard(label, value, tone) {
    var bg = "var(--card-bg)";
    var border = "var(--border-color)";
    var valueColor = "var(--text-color)";
    if (tone === "green") {
      bg = "rgba(34,197,94,0.10)";
      border = "rgba(34,197,94,0.28)";
      valueColor = "#16a34a";
    } else if (tone === "amber") {
      bg = "rgba(245,158,11,0.10)";
      border = "rgba(245,158,11,0.28)";
      valueColor = "#f59e0b";
    }
    return ''
      + '<div style="flex:1 1 220px; min-width:180px; border:1px solid ' + border + '; border-radius:10px; background:' + bg + '; overflow:hidden;">'
      + '<div style="padding:8px 12px; background:rgba(148,163,184,0.10); color:var(--text-muted); font-weight:800;">' + escapeHtml(label) + '</div>'
      + '<div style="padding:18px 12px; font-size:30px; font-weight:900; color:' + valueColor + '; direction:ltr; text-align:right;">' + value + '</div>'
      + '</div>';
  }

  function applyDashboardSearch(wrapper) {
    if (!wrapper || !wrapper.length) return;
    var query = String(wrapper.find(".delivery-component-dashboard-search").val() || "").trim().toLowerCase();
    var visibleCount = 0;
    wrapper.find(".delivery-component-dashboard-row").each(function() {
      var row = $(this);
      var visible = !query || String(row.attr("data-search") || "").toLowerCase().indexOf(query) !== -1;
      row.toggle(visible);
      if (visible) visibleCount += 1;
    });
    wrapper.find(".delivery-component-dashboard-search-count").text(
      visibleCount ? "النتائج: " + visibleCount : "لا توجد نتائج مطابقة"
    );
  }

  function bindDashboardInteractions(field, frm) {
    if (!field || !field.$wrapper || !field.$wrapper.length) return;
    var wrapper = field.$wrapper;
    wrapper.off(".namarDeliveryComponents");
    wrapper.on(
      "click.namarDeliveryComponents",
      ".delivery-component-print-btn",
      function() { prepareAndPrintPackages(frm); }
    );
    wrapper.on(
      "click.namarDeliveryComponents",
      ".manual-delivery-component-package-btn",
      function() { openManualDialog(frm); }
    );
    wrapper.on(
      "input.namarDeliveryComponents keyup.namarDeliveryComponents change.namarDeliveryComponents search.namarDeliveryComponents",
      ".delivery-component-dashboard-search",
      function() { applyDashboardSearch(wrapper); }
    );
    applyDashboardSearch(wrapper);
  }

  function syncPackages(frm) {
    if (frm.is_new()) {
      frappe.msgprint("احفظ طلب المواد قبل تحديث حزم مكونات التوريد.");
      return;
    }
    if (formHasUnsavedDeliveryChanges(frm)) {
      frappe.msgprint("احفظ طلب المواد أولًا حتى يتم تحديث الحزم من آخر نتائج تخصيم محفوظة.");
      return;
    }
    frappe.call({
      method: api.sync,
      args: { mr: frm.doc.name },
      freeze: true,
      freeze_message: "جاري تحديث حزم مكونات التوريد...",
      callback: function(r) {
        var msg = r.message || {};
        frappe.show_alert({
          message: "تم تحديث " + (msg.package_count || 0) + " حزمة مكونات للتوريد.",
          indicator: "green"
        }, 7);
        frm.reload_doc();
      }
    });
  }

  function closePreparingWindow(printWindow) {
    if (!printWindow || printWindow.closed) return;
    try {
      printWindow.close();
    } catch (error) {
      // The browser may revoke access after navigation; there is nothing else to clean up.
    }
  }

  function showPrintFallback(frm) {
    var url = printUrl(frm);
    frappe.msgprint({
      title: "ملصقات مكونات التوريد جاهزة",
      indicator: "green",
      message: '<div dir="rtl" style="text-align:right;">منع المتصفح فتح نافذة الطباعة تلقائيًا. '
        + '<a class="btn btn-primary btn-sm" target="_blank" rel="noopener" href="' + escapeHtml(url) + '">فتح الملصقات</a></div>'
    });
  }

  function prepareAndPrintPackages(frm) {
    if (preparingPrint) return;
    if (frm.is_new()) {
      frappe.msgprint("احفظ طلب المواد قبل طباعة باركود مكونات التوريد.");
      return;
    }
    if (formHasUnsavedDeliveryChanges(frm)) {
      frappe.msgprint("احفظ طلب المواد أولًا حتى يتم تجهيز الحزم من آخر نتائج تخصيم محفوظة.");
      return;
    }

    preparingPrint = true;

    var printWindow = window.open("about:blank", "_blank");
    if (printWindow) {
      try {
        printWindow.document.open();
        printWindow.document.write(
          '<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">'
          + '<title>جاري تجهيز ملصقات المكونات</title></head>'
          + '<body style="font-family:sans-serif; padding:40px; text-align:center;">'
          + '<h2>جاري تجهيز حزم مكونات التوريد...</h2>'
          + '<p>ستفتح الملصقات تلقائيًا بعد اكتمال التجهيز.</p></body></html>'
        );
        printWindow.document.close();
      } catch (error) {
        // Continue with the sync; a fallback link is shown if navigation is unavailable.
      }
    }

    frappe.call({
      method: api.sync,
      args: { mr: frm.doc.name },
      freeze: true,
      freeze_message: "جاري تجهيز حزم المكونات للطباعة...",
      callback: function(r) {
        var msg = r.message || {};
        var packageCount = Number(msg.package_count || 0);
        if (!packageCount) {
          preparingPrint = false;
          closePreparingWindow(printWindow);
          frappe.msgprint("لا توجد مكونات قابلة للتغليف والطباعة في طلب المواد الحالي.");
          frm.reload_doc();
          return;
        }

        if (printWindow && !printWindow.closed) {
          try {
            printWindow.location.replace(printUrl(frm));
          } catch (error) {
            closePreparingWindow(printWindow);
            showPrintFallback(frm);
          }
        } else {
          showPrintFallback(frm);
        }

        frappe.show_alert({
          message: "تم تجهيز " + packageCount + " حزمة وفتح ملصقاتها للطباعة.",
          indicator: "green"
        }, 7);
        preparingPrint = false;
        frm.reload_doc();
      },
      error: function() {
        preparingPrint = false;
        closePreparingWindow(printWindow);
      }
    });
  }

  function renderManualRows(packageRows) {
    if (!packageRows || !packageRows.length) {
      return '<div style="padding:16px; color:var(--text-muted);">لا توجد حزم غير جاهزة للتسجيل اليدوي.</div>';
    }
    return packageRows.map(function(row) {
      var token = row.barcode_key || row.name || row.package_key || "";
      var loadingCode = row.loading_code || "";
      var itemText = row.item_code ? ' | الصنف: ' + escapeHtml(row.item_code) : "";
      var searchText = [
        loadingCode,
        row.component_label || row.component || "",
        row.package_label || "",
        row.package_no || "",
        row.package_count || "",
        row.item_code || "",
        formatCount(row.package_qty || 0)
      ].join(" ").toLowerCase();
      return ''
        + '<div class="manual-delivery-component-package-row" data-search="' + escapeHtml(searchText) + '" style="display:grid; grid-template-columns:24px minmax(0,1fr); align-items:center; gap:10px; padding:10px 0; border-bottom:1px solid var(--border-color);">'
        + '<input type="checkbox" class="manual-delivery-component-package-checkbox" value="' + escapeHtml(token) + '" style="width:18px; height:18px;">'
        + '<div style="min-width:0;">'
        + '<div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap; font-weight:800;">'
        + (loadingCode ? '<span style="direction:ltr; display:inline-flex; padding:2px 8px; border-radius:999px; background:var(--control-bg); border:1px solid var(--border-color); font-size:12px;">' + escapeHtml(loadingCode) + '</span>' : '')
        + '<span>' + escapeHtml(row.component_label || row.component || "-") + ' | ' + escapeHtml(row.package_label || "حزمة") + '</span>'
        + '</div>'
        + '<div style="color:var(--text-muted); font-size:12px; margin-top:3px;">'
        + 'الحزمة: ' + escapeHtml(row.package_no || "-") + '/' + escapeHtml(row.package_count || "-")
        + itemText
        + ' | عدد القطع: ' + formatCount(row.package_qty || 0)
        + '</div>'
        + '</div>'
        + '</div>';
    }).join("");
  }

  function bindManualSearch(dialog) {
    var helpWrapper = dialog.fields_dict.help_html.$wrapper;
    var rowsWrapper = dialog.fields_dict.rows_html.$wrapper;
    helpWrapper.find(".manual-delivery-component-search").on("input", function() {
      var query = String($(this).val() || "").trim().toLowerCase();
      var visibleCount = 0;
      rowsWrapper.find(".manual-delivery-component-package-row").each(function() {
        var row = $(this);
        var visible = !query || String(row.attr("data-search") || "").toLowerCase().indexOf(query) !== -1;
        row.toggle(visible);
        if (visible) visibleCount += 1;
      });
      helpWrapper.find(".manual-delivery-component-search-count").text(
        visibleCount ? "النتائج: " + visibleCount : "لا توجد نتائج مطابقة"
      );
    });
  }

  function selectedManualPackages(dialog) {
    var selected = [];
    var rowsField = dialog && dialog.fields_dict ? dialog.fields_dict.rows_html : null;
    if (!rowsField || !rowsField.$wrapper || !rowsField.$wrapper.length) return selected;
    rowsField.$wrapper.find(".manual-delivery-component-package-checkbox:checked").each(function() {
      var token = String($(this).val() || "").trim();
      if (token) selected.push({ token: token, mode: "full" });
    });
    return selected;
  }

  function submitManualPackages(frm, dialog, selected) {
    var total = selected.length;
    var updatedCount = 0;
    var failedCount = 0;
    var index = 0;
    function finish() {
      if (dialog) dialog.hide();
      frappe.show_alert({
        message: "تم تسجيل " + updatedCount + " من " + total + " حزمة يدويًا" + (failedCount ? "، وتعذر تسجيل " + failedCount + "." : "."),
        indicator: failedCount ? "orange" : "green"
      }, 7);
      frm.reload_doc();
    }
    function runNext() {
      if (index >= total) {
        finish();
        return;
      }
      var entry = selected[index];
      index += 1;
      frappe.call({
        method: api.markReady,
        args: {
          mr: frm.doc.name,
          component_package: entry.token,
          mode: "full",
          source: "يدوي"
        },
        freeze: true,
        freeze_message: "جاري تسجيل حزم مكونات التوريد يدويًا...",
        callback: function() {
          updatedCount += 1;
          runNext();
        },
        error: function() {
          failedCount += 1;
          runNext();
        }
      });
    }
    runNext();
  }

  function openManualDialog(frm) {
    if (frm.is_new()) {
      frappe.msgprint("احفظ طلب المواد قبل تسجيل حزم مكونات التوريد.");
      return;
    }
    if (formHasUnsavedDeliveryChanges(frm)) {
      frappe.msgprint("احفظ طلب المواد أولًا حتى يتم التسجيل اليدوي على آخر نتائج تخصيم محفوظة.");
      return;
    }
    var packageRows = pendingRows(frm);
    if (!packageRows.length) {
      frappe.msgprint("لا توجد حزم غير جاهزة. إذا تغيرت نتائج التخصيم، حدّث حزم المكونات أولًا.");
      return;
    }
    var dialog = new frappe.ui.Dialog({
      title: "تسجيل حزم التوريد يدويًا",
      fields: [
        { fieldtype: "HTML", fieldname: "help_html" },
        { fieldtype: "HTML", fieldname: "rows_html", label: "الحزم غير الجاهزة" }
      ],
      primary_action_label: "تسجيل المحدد",
      primary_action: function() {
        var selected = selectedManualPackages(dialog);
        if (!selected.length) {
          frappe.msgprint("حدد حزمة واحدة على الأقل.");
          return;
        }
        submitManualPackages(frm, dialog, selected);
      }
    });
    dialog.show();
    dialog.fields_dict.help_html.$wrapper.html(
      '<div style="margin-bottom:12px; color:var(--text-muted); line-height:1.8;">'
      + 'حدد الحزم الجاهزة للتوريد يدويًا. كل حزمة محددة ستسجل كاملة بدون أجزاء.'
      + '<input type="search" class="manual-delivery-component-search form-control" placeholder="بحث بالتكويد أو المكون أو التغليف أو الصنف..." style="margin-top:10px; height:34px;">'
      + '<div class="manual-delivery-component-search-count" style="margin-top:6px; font-size:12px;"></div>'
      + '<div style="margin-top:8px;">'
      + '<a href="#" class="manual-delivery-component-select-all">تحديد الكل</a>'
      + ' | '
      + '<a href="#" class="manual-delivery-component-clear-all">إلغاء التحديد</a>'
      + '</div>'
      + '</div>'
    );
    dialog.fields_dict.help_html.$wrapper.find(".manual-delivery-component-select-all").on("click", function(e) {
      e.preventDefault();
      dialog.fields_dict.rows_html.$wrapper.find(".manual-delivery-component-package-row:visible .manual-delivery-component-package-checkbox").prop("checked", true);
    });
    dialog.fields_dict.help_html.$wrapper.find(".manual-delivery-component-clear-all").on("click", function(e) {
      e.preventDefault();
      dialog.fields_dict.rows_html.$wrapper.find(".manual-delivery-component-package-checkbox").prop("checked", false);
    });
    dialog.fields_dict.rows_html.$wrapper.html(
      '<div style="max-height:460px; overflow:auto; border-top:1px solid var(--border-color); padding-top:8px;">'
      + renderManualRows(packageRows)
      + '</div>'
    );
    bindManualSearch(dialog);
    dialog.fields_dict.help_html.$wrapper.find(".manual-delivery-component-search").trigger("input");
  }

  function renderDashboard(frm) {
    var field = frm.fields_dict.custom_delivery_component_dashboard;
    if (!field) return true;
    if (frm.is_new()) {
      frm.set_df_property("custom_delivery_component_dashboard", "options", "");
      frm.refresh_field("custom_delivery_component_dashboard");
      return true;
    }
    var packageRows = rows(frm);
    var totalPackages = flt(frm.doc.custom_delivery_component_total_packages || 0) || packageRows.filter(function(row) {
      return parseInt(row.required_for_delivery || 0, 10);
    }).length;
    var remainingPackages = flt(frm.doc.custom_delivery_component_remaining_packages || 0);
    if (!remainingPackages && packageRows.length) {
      remainingPackages = packageRows.filter(function(row) {
        return parseInt(row.required_for_delivery || 0, 10) && flt(row.remaining_qty || 0) > 0;
      }).length;
    }
    var readyPackages = Math.max(totalPackages - remainingPackages, 0);
    var statusText = frm.doc.custom_delivery_component_status || (packageRows.length ? "غير جاهز" : "لا توجد مكونات");
    var loadedPackages = 0;
    var deliveredPackages = 0;
    var fullRoutePackages = 0;
    var overallStatus = frm.doc.custom_fulfillment_readiness_status || "غير جاهز";
    var overallSummary = frm.doc.custom_fulfillment_readiness_summary || "";
    var totalRequiredQty = 0;
    var readyRequiredQty = 0;
    packageRows.forEach(function(row) {
      if (!parseInt(row.required_for_delivery || 0, 10)) return;
      totalRequiredQty += flt(row.package_qty || 0);
      readyRequiredQty += flt(row.ready_qty || 0);
      var trackingStatus = row.tracking_status || (flt(row.remaining_qty || 0) > 0 ? "غير جاهز" : "جاهز");
      if ((row.tracking_route || "تصنيع وتغليف") !== "تجهيز فقط") fullRoutePackages += 1;
      if (trackingStatus === "محمل" || trackingStatus === "تم التوريد") loadedPackages += 1;
      if (trackingStatus === "تم التوريد") deliveredPackages += 1;
    });
    var loadingCode = frm.doc.custom_delivery_loading_code || "-";
    var statusBg = statusText === "مكتمل" ? "rgba(34,197,94,0.14)" : statusText === "جزئي" ? "rgba(245,158,11,0.14)" : "rgba(148,163,184,0.16)";
    var statusColor = statusText === "مكتمل" ? "#16a34a" : statusText === "جزئي" ? "#b45309" : "#475569";
    var overallComplete = ["جاهز بالكامل", "تم التحميل بالكامل", "تم التوريد بالكامل"].indexOf(overallStatus) !== -1;
    var overallBg = overallComplete ? "rgba(34,197,94,0.14)" : overallStatus === "جاهزية جزئية" ? "rgba(245,158,11,0.14)" : "rgba(239,68,68,0.10)";
    var overallColor = overallComplete ? "#16a34a" : overallStatus === "جاهزية جزئية" ? "#b45309" : "#dc2626";
    var packageRowsHtml = packageRows.map(function(row) {
      var remaining = flt(row.remaining_qty || 0);
      var trackingStatus = row.tracking_status || (remaining > 0 ? "غير جاهز" : "جاهز");
      var rowDone = trackingStatus === "جاهز" || trackingStatus === "محمل" || trackingStatus === "تم التوريد";
      var rowStatusBg = rowDone ? "rgba(34,197,94,0.14)" : "rgba(245,158,11,0.14)";
      var rowStatusColor = rowDone ? "#16a34a" : "#b45309";
      var rowLoadingCode = row.loading_code || "";
      var packageText = (row.package_no || "-") + "/" + (row.package_count || "-");
      var searchText = [
        rowLoadingCode,
        row.component_label || row.component || "",
        row.package_label || "",
        row.package_no || "",
        row.package_count || "",
        packageText,
        row.item_code || "",
        row.item_name || "",
        formatCount(row.package_qty || 0),
        trackingStatus
      ].join(" ").toLowerCase();
      return ''
        + '<tr class="delivery-component-dashboard-row" data-search="' + escapeHtml(searchText) + '">'
        + '<td style="padding:9px 8px; border-bottom:1px solid var(--border-color); text-align:center; direction:ltr; vertical-align:middle; font-weight:900; white-space:nowrap;">' + escapeHtml(rowLoadingCode || "-") + '</td>'
        + '<td style="padding:9px 8px; border-bottom:1px solid var(--border-color); text-align:center; direction:ltr; vertical-align:middle; font-weight:800; white-space:nowrap;">' + escapeHtml(packageText) + '</td>'
        + '<td style="padding:9px 8px; border-bottom:1px solid var(--border-color); text-align:right; direction:rtl; vertical-align:middle; font-weight:800; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;" title="' + escapeHtml(row.item_code || row.item_name || "") + '">' + escapeHtml(row.component_label || row.component || "-") + '</td>'
        + '<td style="padding:9px 8px; border-bottom:1px solid var(--border-color); text-align:center; direction:rtl; vertical-align:middle; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">' + escapeHtml(row.package_label || "حزمة") + '</td>'
        + '<td style="padding:9px 8px; border-bottom:1px solid var(--border-color); text-align:center; direction:ltr; vertical-align:middle; white-space:nowrap; font-weight:800;">' + formatCount(row.package_qty || 0) + '</td>'
        + '<td style="padding:9px 8px; border-bottom:1px solid var(--border-color); text-align:center; direction:rtl; vertical-align:middle;"><span style="display:inline-flex; justify-content:center; min-width:72px; padding:3px 9px; border-radius:999px; background:' + rowStatusBg + '; color:' + rowStatusColor + '; font-size:12px; font-weight:800; white-space:nowrap;">' + escapeHtml(trackingStatus) + '</span></td>'
        + '</tr>';
    }).join("");
    var tableHtml = packageRows.length ? ''
      + '<div style="margin-top:14px;">'
      + '<div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin-bottom:8px;">'
      + '<input type="search" class="delivery-component-dashboard-search form-control" placeholder="بحث بالتكويد أو المكون أو التغليف أو الصنف..." style="max-width:360px; height:34px; direction:rtl; text-align:right;">'
      + '<span class="delivery-component-dashboard-search-count" style="color:var(--text-muted); font-size:12px;"></span>'
      + '</div>'
      + '<div style="max-height:420px; overflow:auto; border:1px solid var(--border-color); border-radius:10px;">'
      + '<table style="width:100%; min-width:920px; border-collapse:collapse; table-layout:fixed; direction:ltr;">'
      + '<colgroup><col style="width:104px;"><col style="width:86px;"><col style="width:32%;"><col style="width:120px;"><col style="width:104px;"><col style="width:128px;"></colgroup>'
      + '<thead style="position:sticky; top:0; background:var(--control-bg); z-index:1;"><tr>'
      + '<th style="padding:9px 8px; text-align:center; direction:rtl; color:var(--text-muted); font-size:12px; white-space:nowrap;">تكويد التحميل</th>'
      + '<th style="padding:9px 8px; text-align:center; direction:rtl; color:var(--text-muted); font-size:12px; white-space:nowrap;">الحزمة</th>'
      + '<th style="padding:9px 8px; text-align:right; direction:rtl; color:var(--text-muted); font-size:12px; white-space:nowrap;">المكون</th>'
      + '<th style="padding:9px 8px; text-align:center; direction:rtl; color:var(--text-muted); font-size:12px; white-space:nowrap;">التغليف</th>'
      + '<th style="padding:9px 8px; text-align:center; direction:rtl; color:var(--text-muted); font-size:12px; white-space:nowrap;">عدد القطع</th>'
      + '<th style="padding:9px 8px; text-align:center; direction:rtl; color:var(--text-muted); font-size:12px; white-space:nowrap;">الحالة</th>'
      + '</tr></thead><tbody>' + packageRowsHtml + '</tbody></table></div></div>' : '<div style="margin-top:12px; color:var(--text-muted);">لا توجد حزم مكونات للتوريد بعد. اضغط طباعة باركود المكونات لتجهيزها وفتح الملصقات تلقائيًا.</div>';
    var html = ''
      + '<div class="delivery-component-wrap" data-delivery-tracking-source="custom-app" style="padding:16px; border:1px solid var(--border-color); border-radius:12px; background:var(--card-bg); margin-bottom:12px;">'
      + '<div style="display:flex; justify-content:space-between; align-items:center; gap:12px; flex-wrap:wrap; padding:12px 14px; margin-bottom:14px; border-radius:10px; background:' + overallBg + '; color:' + overallColor + ';">'
      + '<div><div style="font-size:13px; font-weight:800; margin-bottom:3px;">جاهزية الطلب الشاملة</div><div style="font-size:19px; font-weight:900;">' + escapeHtml(overallStatus) + '</div></div>'
      + '<div style="font-size:13px; font-weight:800; direction:rtl; text-align:right;">' + escapeHtml(overallSummary || "اطبع باركود المكونات لتجهيز الحزم واحتساب جاهزية الطلب") + '</div>'
      + '</div>'
      + '<div style="display:flex; justify-content:space-between; align-items:flex-start; gap:12px; flex-wrap:wrap;">'
      + '<div><div style="font-size:18px; font-weight:900; margin-bottom:4px;">جاهزية مكونات التوريد</div>'
      + '<div style="color:var(--text-muted);">تتبع الحزم من التجهيز إلى التحميل والتوريد، مع إبقاء أعدادها منفصلة عن عدد الأبواب.</div></div>'
      + '<span style="display:inline-flex; padding:5px 11px; border-radius:999px; background:' + statusBg + '; color:' + statusColor + '; font-size:12px; font-weight:900;">' + escapeHtml(statusText) + '</span>'
      + '</div>'
      + '<div style="display:flex; gap:10px; flex-wrap:wrap; margin-top:14px;">'
      + statCard("الحزم الجاهزة", formatCount(readyPackages) + " / " + formatCount(totalPackages), remainingPackages > 0 ? "amber" : "green")
      + (fullRoutePackages ? statCard("تم تحميلها", formatCount(loadedPackages) + " / " + formatCount(fullRoutePackages), loadedPackages >= fullRoutePackages ? "green" : "default") : "")
      + (fullRoutePackages ? statCard("تم توريدها", formatCount(deliveredPackages) + " / " + formatCount(fullRoutePackages), deliveredPackages >= fullRoutePackages ? "green" : "default") : "")
      + (packageRows.length ? statCard("الكمية الجاهزة", formatCount(readyRequiredQty) + " / " + formatCount(totalRequiredQty), statusText === "مكتمل" ? "green" : "amber") : "")
      + statCard("رمز التحميل", escapeHtml(loadingCode), "default")
      + '</div>'
      + '<div style="display:flex; gap:8px; flex-wrap:wrap; margin-top:14px;">'
      + '<button type="button" class="btn btn-primary btn-sm delivery-component-print-btn">طباعة باركود المكونات</button>'
      + (pendingRows(frm).length ? '<button type="button" class="btn btn-default btn-sm manual-delivery-component-package-btn">تسجيل حزمة يدويًا</button>' : '')
      + '</div>'
      + tableHtml
      + '</div>';
    frm.set_df_property("custom_delivery_component_dashboard", "options", html);
    frm.refresh_field("custom_delivery_component_dashboard");
    if (field.$wrapper) {
      bindDashboardInteractions(field, frm);
    }
    setupRealtime();
    setupFocusFallback();
    return true;
  }

  function renderUnifiedDashboard(frm) {
    var field = frm.fields_dict.custom_delivery_component_dashboard;
    if (!field) return true;
    if (frm.is_new()) {
      frm.set_df_property("custom_delivery_component_dashboard", "options", "");
      frm.refresh_field("custom_delivery_component_dashboard");
      return true;
    }

    var packageRows = rows(frm);
    var totalPackages = flt(frm.doc.custom_delivery_component_total_packages || 0) || packageRows.filter(function(row) {
      return parseInt(row.required_for_delivery || 0, 10);
    }).length;
    var registeredPackages = packageRows.filter(function(row) {
      if (!parseInt(row.required_for_delivery || 0, 10)) return false;
      var packageQty = flt(row.package_qty || 0);
      var readyQty = flt(row.ready_qty || 0);
      var trackingStatus = row.tracking_status || row.status || "غير جاهز";
      return readyQty >= packageQty - 0.000001
        || ["جاهز", "محمل", "تم التوريد"].indexOf(trackingStatus) !== -1;
    }).length;
    var remainingPackages = Math.max(totalPackages - registeredPackages, 0);
    var statusText = totalPackages <= 0
      ? "لا توجد مكونات"
      : remainingPackages <= 0
        ? "مكتمل"
        : registeredPackages > 0 ? "جزئي" : "غير جاهز";
    var requestCode = frm.doc.custom_delivery_loading_code || "-";
    var overallStatus = frm.doc.custom_fulfillment_readiness_status || "غير جاهز";
    var overallSummary = frm.doc.custom_fulfillment_readiness_summary || "";
    var overallComplete = ["جاهز بالكامل", "تم التحميل بالكامل", "تم التوريد بالكامل"].indexOf(overallStatus) !== -1;
    var overallBg = overallComplete ? "rgba(34,197,94,0.14)" : overallStatus === "جاهزية جزئية" ? "rgba(245,158,11,0.14)" : "rgba(239,68,68,0.10)";
    var overallColor = overallComplete ? "#16a34a" : overallStatus === "جاهزية جزئية" ? "#b45309" : "#dc2626";
    var statusBg = statusText === "مكتمل" ? "rgba(34,197,94,0.14)" : statusText === "جزئي" ? "rgba(245,158,11,0.14)" : "rgba(148,163,184,0.16)";
    var statusColor = statusText === "مكتمل" ? "#16a34a" : statusText === "جزئي" ? "#b45309" : "#475569";
    var hasColor = packageRows.some(function(row) { return !!String(row.color || "").trim(); });
    var hasRegistrationInfo = packageRows.some(function(row) { return !!(row.ready_at || row.ready_by); });

    var packageRowsHtml = packageRows.map(function(row) {
      var packageQty = flt(row.package_qty || 0);
      var readyQty = flt(row.ready_qty || 0);
      var trackingStatus = row.tracking_status || row.status || "غير جاهز";
      var registered = readyQty >= packageQty - 0.000001
        || ["جاهز", "محمل", "تم التوريد"].indexOf(trackingStatus) !== -1;
      var displayStatus = registered ? "مسجلة" : "غير مسجلة";
      var rowStatusBg = registered ? "rgba(34,197,94,0.14)" : "rgba(245,158,11,0.14)";
      var rowStatusColor = registered ? "#16a34a" : "#b45309";
      var searchText = [
        row.loading_code || "",
        row.component_label || row.component || "",
        row.color || "",
        row.item_code || "",
        formatCount(packageQty),
        displayStatus,
        row.ready_at || "",
        row.ready_by || ""
      ].join(" ").toLowerCase();
      return ''
        + '<tr class="delivery-component-dashboard-row" data-search="' + escapeHtml(searchText) + '">'
        + '<td style="padding:9px 8px; border-bottom:1px solid var(--border-color); text-align:center; direction:ltr; vertical-align:middle; font-weight:900; white-space:nowrap;">' + escapeHtml(row.loading_code || "-") + '</td>'
        + '<td style="padding:9px 8px; border-bottom:1px solid var(--border-color); text-align:right; direction:rtl; vertical-align:middle; font-weight:800;">' + escapeHtml(row.component_label || row.component || "-") + '</td>'
        + (hasColor ? '<td style="padding:9px 8px; border-bottom:1px solid var(--border-color); text-align:right; direction:rtl; vertical-align:middle;">' + escapeHtml(row.color || "-") + '</td>' : '')
        + '<td style="padding:9px 8px; border-bottom:1px solid var(--border-color); text-align:center; direction:ltr; vertical-align:middle; font-weight:800; white-space:nowrap;">' + formatCount(packageQty) + '</td>'
        + '<td style="padding:9px 8px; border-bottom:1px solid var(--border-color); text-align:center; direction:rtl; vertical-align:middle;"><span style="display:inline-flex; justify-content:center; min-width:82px; padding:3px 9px; border-radius:999px; background:' + rowStatusBg + '; color:' + rowStatusColor + '; font-size:12px; font-weight:800; white-space:nowrap;">' + displayStatus + '</span></td>'
        + (hasRegistrationInfo ? '<td style="padding:9px 8px; border-bottom:1px solid var(--border-color); text-align:center; direction:ltr; vertical-align:middle; white-space:nowrap;">' + escapeHtml(row.ready_at || "-") + '</td>' : '')
        + (hasRegistrationInfo ? '<td style="padding:9px 8px; border-bottom:1px solid var(--border-color); text-align:right; direction:ltr; vertical-align:middle;">' + escapeHtml(row.ready_by || "-") + '</td>' : '')
        + '</tr>';
    }).join("");

    var optionalHeaders = '';
    if (hasColor) optionalHeaders += '<th style="padding:9px 8px; text-align:right; direction:rtl; color:var(--text-muted); font-size:12px;">اللون</th>';
    var trailingHeaders = '';
    if (hasRegistrationInfo) {
      trailingHeaders += '<th style="padding:9px 8px; text-align:center; direction:rtl; color:var(--text-muted); font-size:12px;">وقت التسجيل</th>';
      trailingHeaders += '<th style="padding:9px 8px; text-align:right; direction:rtl; color:var(--text-muted); font-size:12px;">المستخدم</th>';
    }
    var tableHtml = packageRows.length ? ''
      + '<div style="margin-top:14px;">'
      + '<div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin-bottom:8px;">'
      + '<input type="search" class="delivery-component-dashboard-search form-control" placeholder="بحث بالرمز أو المكون أو اللون..." style="max-width:360px; height:34px; direction:rtl; text-align:right;">'
      + '<span class="delivery-component-dashboard-search-count" style="color:var(--text-muted); font-size:12px;"></span>'
      + '</div>'
      + '<div style="max-height:420px; overflow:auto; border:1px solid var(--border-color); border-radius:10px;">'
      + '<table style="width:100%; min-width:' + (hasRegistrationInfo ? '940' : '620') + 'px; border-collapse:collapse; table-layout:auto; direction:ltr;">'
      + '<thead style="position:sticky; top:0; background:var(--control-bg); z-index:1;"><tr>'
      + '<th style="padding:9px 8px; text-align:center; direction:rtl; color:var(--text-muted); font-size:12px; white-space:nowrap;">رمز الحزمة</th>'
      + '<th style="padding:9px 8px; text-align:right; direction:rtl; color:var(--text-muted); font-size:12px;">المكون</th>'
      + optionalHeaders
      + '<th style="padding:9px 8px; text-align:center; direction:rtl; color:var(--text-muted); font-size:12px; white-space:nowrap;">الكمية</th>'
      + '<th style="padding:9px 8px; text-align:center; direction:rtl; color:var(--text-muted); font-size:12px; white-space:nowrap;">الحالة</th>'
      + trailingHeaders
      + '</tr></thead><tbody>' + packageRowsHtml + '</tbody></table></div></div>'
      : '<div style="margin-top:12px; color:var(--text-muted);">لا توجد حزم بعد. استخدم زر طباعة باركود المكونات أعلى لوحة التصنيع لتجهيزها تلقائيًا.</div>';

    var html = ''
      + '<div class="delivery-component-wrap" data-delivery-tracking-source="custom-app" style="padding:16px; border:1px solid var(--border-color); border-radius:12px; background:var(--card-bg); margin-bottom:12px;">'
      + '<div style="display:flex; justify-content:space-between; align-items:center; gap:12px; flex-wrap:wrap; padding:12px 14px; margin-bottom:14px; border-radius:10px; background:' + overallBg + '; color:' + overallColor + ';">'
      + '<div><div style="font-size:13px; font-weight:800; margin-bottom:3px;">جاهزية الطلب الشاملة</div><div style="font-size:19px; font-weight:900;">' + escapeHtml(overallStatus) + '</div></div>'
      + '<div style="font-size:13px; font-weight:800; direction:rtl; text-align:right;">' + escapeHtml(overallSummary || "تكتمل الجاهزية بعد تصنيع الأبواب وتسجيل جميع الحزم") + '</div>'
      + '</div>'
      + '<div style="display:flex; justify-content:space-between; align-items:flex-start; gap:12px; flex-wrap:wrap;">'
      + '<div><div style="font-size:18px; font-weight:900; margin-bottom:4px;">تسجيل حزم المكونات</div>'
      + '<div style="color:var(--text-muted);">بعد إغلاق الكرتون، امسح الملصق مرة واحدة لتسجيل الحزمة كاملة.</div></div>'
      + '<span style="display:inline-flex; padding:5px 11px; border-radius:999px; background:' + statusBg + '; color:' + statusColor + '; font-size:12px; font-weight:900;">' + escapeHtml(statusText) + '</span>'
      + '</div>'
      + '<div style="display:flex; gap:10px; flex-wrap:wrap; margin-top:14px;">'
      + statCard("الحزم المسجلة", formatCount(registeredPackages) + " / " + formatCount(totalPackages), remainingPackages > 0 ? "amber" : "green")
      + statCard("المتبقي", formatCount(remainingPackages), remainingPackages > 0 ? "amber" : "green")
      + statCard("رمز الطلب", escapeHtml(requestCode), "default")
      + '</div>'
      + tableHtml
      + '</div>';
    frm.set_df_property("custom_delivery_component_dashboard", "options", html);
    frm.refresh_field("custom_delivery_component_dashboard");
    if (field.$wrapper) bindDashboardInteractions(field, frm);
    setupRealtime();
    setupFocusFallback();
    return true;
  }

  function refreshFulfillment(frm) {
    if (!frm || frm.is_new() || formHasUnsavedDeliveryChanges(frm)) return;
    frappe.call({
      method: api.fulfillment,
      args: { mr: frm.doc.name },
      callback: function(r) {
        var data = r.message || {};
        var fulfillment = data.fulfillment || {};
        frm.doc.custom_fulfillment_readiness_status = fulfillment.status || "غير جاهز";
        frm.doc.custom_fulfillment_readiness_summary = fulfillment.summary || "";
        frm.doc.custom_fulfillment_ready = fulfillment.is_ready ? 1 : 0;
        renderUnifiedDashboard(frm);
      }
    });
  }

  function reloadCurrentFormFromExternalEvent(eventData) {
    var frm = window.cur_frm;
    if (!frm || frm.doctype !== "Material Request" || !frm.doc || frm.doc.name !== eventData.material_request) return;
    if (formHasUnsavedDeliveryChanges(frm)) {
      frappe.show_alert({
        message: "تم تحديث حزمة مكونات في الخلفية. احفظ أو حدّث الصفحة لرؤية آخر حالة.",
        indicator: "orange"
      }, 8);
      return;
    }
    frm.reload_doc();
  }

  function setupRealtime() {
    if (realtimeBound || !frappe.realtime || typeof frappe.realtime.on !== "function") return;
    realtimeBound = true;
    frappe.realtime.on("delivery_component_package_changed", reloadCurrentFormFromExternalEvent);
  }

  function maybeReloadOnFocus() {
    var frm = window.cur_frm;
    var now = Date.now();
    if (!frm || frm.doctype !== "Material Request" || !frm.doc || !frm.doc.name) return;
    if (!frm.fields_dict || !frm.fields_dict.custom_delivery_component_dashboard) return;
    if (formHasUnsavedDeliveryChanges(frm)) return;
    if (now - lastFocusReloadAt < 30000) return;
    lastFocusReloadAt = now;
    frm.reload_doc();
  }

  function setupFocusFallback() {
    if (focusBound) return;
    focusBound = true;
    document.addEventListener("visibilitychange", function() {
      if (!document.hidden) maybeReloadOnFocus();
    });
    window.addEventListener("focus", maybeReloadOnFocus);
  }

  window.namar_delivery_tracking = window.namar_delivery_tracking || {};
  window.namar_delivery_tracking.delivery_components = {
    render_material_request: renderUnifiedDashboard,
    sync_packages: syncPackages,
    prepare_and_print: prepareAndPrintPackages
  };
  window.sync_delivery_component_packages_for_current_form = function() {
    if (window.cur_frm) syncPackages(window.cur_frm);
  };
  window.prepare_delivery_component_packages_for_print_for_current_form = function() {
    if (window.cur_frm) prepareAndPrintPackages(window.cur_frm);
  };
  frappe.ui.form.on("Material Request", {
    refresh: function(frm) {
      renderUnifiedDashboard(frm);
      refreshFulfillment(frm);
    }
  });
})();
