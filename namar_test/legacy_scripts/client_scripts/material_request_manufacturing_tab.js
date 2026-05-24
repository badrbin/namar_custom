function manufacturingEscapeHtml(value) {
    return frappe.utils.escape_html(value == null ? '' : String(value));
}

function manufacturingFormatDateTime(value) {
    if (!value) return 'غير مسجل';
    try {
        return frappe.datetime.str_to_user(value);
    } catch (error) {
        return manufacturingEscapeHtml(value);
    }
}

function manufacturingShortName(name) {
    return manufacturingEscapeHtml((name || '').replace(/^MREQ-/i, ''));
}

function manufacturingToInt(value) {
    var numericValue = parseInt(value, 10);
    return isNaN(numericValue) ? 0 : numericValue;
}

function manufacturingStatusLabel(data) {
    var total = manufacturingToInt(data.total_items || 0);
    var remaining = manufacturingToInt(data.remaining_count || 0);
    var manufactured = manufacturingToInt(data.manufactured_count || 0);

    if (total > 0 && remaining <= 0) {
        return { text: 'مكتمل', indicatorClass: 'green' };
    }
    if (manufactured > 0) {
        return { text: 'قيد التصنيع', indicatorClass: 'yellow' };
    }
    return { text: 'غير مصنع', indicatorClass: 'red' };
}

function manufacturingRemainingTone(data) {
    var total = manufacturingToInt(data.total_items || 0);
    var remaining = manufacturingToInt(data.remaining_count || 0);
    var manufactured = manufacturingToInt(data.manufactured_count || 0);

    if (total > 0 && remaining <= 0) return 'green';
    if (manufactured > 0) return 'yellow';
    return 'red';
}

function manufacturingSummaryCard(label, value, valueColor) {
    return ''
        + '<div class="manufacturing-summary-card">'
        +   '<div class="manufacturing-summary-label">' + manufacturingEscapeHtml(label) + '</div>'
        +   '<div class="manufacturing-summary-value" style="color:' + manufacturingEscapeHtml(valueColor || 'var(--text-color)') + ';">' + manufacturingEscapeHtml(value) + '</div>'
        + '</div>';
}

function manufacturingSectionTone(mode, count, data) {
    if (mode === 'done') {
        return count > 0 ? 'green' : 'red';
    }
    if (count <= 0) {
        return manufacturingToInt(data.total_items || 0) > 0 ? 'green' : 'red';
    }
    return manufacturingRemainingTone(data);
}

function manufacturingItemRow(item, mode) {
    var badgeClass = mode === 'done' ? 'green' : 'yellow';
    var statusText = mode === 'done' ? 'تم تصنيعه' : 'متبقي';
    var timeText = mode === 'done' ? manufacturingFormatDateTime(item.manufactured_at) : 'بانتظار التصنيع';
    var byText = mode === 'done' ? manufacturingEscapeHtml(item.manufactured_by || 'غير مسجل') : '-';
    return ''
        + '<tr>'
        +   '<td style="padding: 8px 12px; border-bottom: 1px solid var(--border-color); text-align: right; font-weight: 600; white-space: nowrap;">' + manufacturingEscapeHtml(item.row || '-') + '</td>'
        +   '<td style="padding: 8px 12px; border-bottom: 1px solid var(--border-color); text-align: right;">' + manufacturingEscapeHtml(item.item_name || item.item_code || '-') + '</td>'
        +   '<td style="padding: 8px 12px; border-bottom: 1px solid var(--border-color); text-align: right; color: var(--text-muted); white-space: nowrap;">' + manufacturingEscapeHtml(item.item_code || '-') + '</td>'
        +   '<td style="padding: 8px 12px; border-bottom: 1px solid var(--border-color); text-align: right;">'
        +     '<div class="indicator-pill ' + badgeClass + '" style="white-space: nowrap;">' + statusText + '</div>'
        +     '<div style="margin-top: 6px; font-size: 12px; color: var(--text-muted); white-space: nowrap;">' + manufacturingEscapeHtml(timeText) + '</div>'
        +   '</td>'
        +   '<td style="padding: 8px 12px; border-bottom: 1px solid var(--border-color); text-align: right; white-space: nowrap;">' + byText + '</td>'
        + '</tr>';
}

function manufacturingSection(title, count, items, mode, emptyText, data) {
    var toneClass = manufacturingSectionTone(mode, count, data || {});
    var rows = (items || []).length
        ? items.map(function(item) { return manufacturingItemRow(item, mode); }).join('')
        : '<tr><td colspan="5" style="text-align:center; padding: 15px; color: var(--text-muted);">' + manufacturingEscapeHtml(emptyText) + '</td></tr>';
    return ''
        + '<div class="manufacturing-section-box">'
        +   '<div class="manufacturing-section-head">'
        +     '<div class="manufacturing-section-title">' + manufacturingEscapeHtml(title) + '</div>'
        +     '<div class="indicator-pill ' + toneClass + '" style="white-space: nowrap;">' + manufacturingEscapeHtml(count) + '</div>'
        +   '</div>'
        +   '<div class="scroll-box manufacturing-scroll-box">'
        +     '<table class="manufacturing-table">'
        +       '<thead>'
        +         '<tr>'
        +           '<th>رقم الباب</th>'
        +           '<th>الصنف</th>'
        +           '<th>الكود</th>'
        +           '<th>الحالة</th>'
        +           '<th>بواسطة</th>'
        +         '</tr>'
        +       '</thead>'
        +       '<tbody>' + rows + '</tbody>'
        +     '</table>'
        +   '</div>'
        + '</div>';
}

function manufacturingOpenFactoryLink(docname) {
    var shortName = (docname || '').replace(/^MREQ-/i, '');
    return '/factory?mr=' + encodeURIComponent(shortName) + '&v=202604091012';
}

function renderManufacturingDashboard(frm, data) {
    var field = frm.fields_dict.custom_manufacturing_dashboard;
    if (!field || !field.$wrapper) return;

    var status = manufacturingStatusLabel(data);
    var remainingTone = manufacturingRemainingTone(data);
    var total = manufacturingToInt(data.total_items || 0);
    var manufactured = manufacturingToInt(data.manufactured_count || 0);
    var remaining = manufacturingToInt(data.remaining_count || 0);
    var percent = data.completion_percent || 0;
    var requestName = manufacturingShortName(frm.doc.name || data.material_request || '');

    var html = ''
        + '<style>'
        + '.manufacturing-dashboard{padding: 8px 0 4px; color: var(--text-color);}'
        + '.manufacturing-shell{border: 1px solid var(--border-color); border-radius: var(--border-radius); background-color: var(--card-bg); overflow: hidden;}'
        + '.manufacturing-header{padding: 12px 15px; border-bottom: 1px solid var(--border-color); display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;}'
        + '.manufacturing-title{font-weight: 600; font-size: var(--text-md);}'
        + '.manufacturing-subtitle{font-size: 12px; color: var(--text-muted); margin-top: 4px;}'
        + '.manufacturing-actions{display: flex; flex-direction: column; align-items: flex-end; gap: 6px; flex-shrink: 0;}'
        + '.manufacturing-link{white-space: nowrap;}'
        + '.manufacturing-body{padding: 15px;}'
        + '.manufacturing-grid{display: flex; gap: 12px; margin-bottom: 18px; flex-wrap: wrap;}'
        + '.manufacturing-summary-card{flex: 1; min-width: 120px; border: 1px solid var(--border-color); border-radius: var(--border-radius); overflow: hidden; background-color: var(--card-bg);}'
        + '.manufacturing-summary-label{background: var(--bg-light-gray); padding: 6px 12px; font-size: 11px; color: var(--text-muted); font-weight: 600; border-bottom: 1px solid var(--border-color);}'
        + '.manufacturing-summary-value{padding: 10px 12px; font-size: 20px; font-weight: 700; line-height: 1.3;}'
        + '.manufacturing-progress-box{margin-bottom: 16px; padding: 10px 12px; border: 1px solid var(--border-color); border-radius: var(--border-radius); background: var(--bg-light-gray); display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap;}'
        + '.manufacturing-progress-label{font-size: 11px; color: var(--text-muted); font-weight: 600;}'
        + '.manufacturing-progress-value{font-size: 14px; font-weight: 600; color: var(--text-color);}'
        + '.manufacturing-sections-title{margin-bottom: 8px; font-weight: 600; font-size: 11px; color: var(--text-muted);}'
        + '.manufacturing-sections{display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 16px;}'
        + '.manufacturing-section-box{border: 1px solid var(--border-color); border-radius: var(--border-radius); background-color: var(--card-bg); overflow: hidden;}'
        + '.manufacturing-section-head{padding: 12px 14px; border-bottom: 1px solid var(--border-color); display: flex; justify-content: space-between; align-items: center; gap: 8px;}'
        + '.manufacturing-section-title{font-weight: 600; font-size: var(--text-sm);}'
        + '.manufacturing-scroll-box{max-height: 360px; overflow-y: auto;}'
        + '.manufacturing-table{width: 100%; border-collapse: collapse; font-size: var(--text-sm); margin-bottom: 0;}'
        + '.manufacturing-table thead{position: sticky; top: 0; z-index: 1;}'
        + '.manufacturing-table tr{background: var(--card-bg);}'
        + '.manufacturing-table th{padding: 8px 12px; text-align: right; font-weight: 600; font-size: 11px; color: var(--text-muted); border-bottom: 1px solid var(--border-color); background: var(--bg-light-gray); white-space: nowrap;}'
        + '@media (max-width: 767px){'
        +   '.manufacturing-shell{border-radius: 12px;}'
        +   '.manufacturing-header{align-items: flex-start;}'
        +   '.manufacturing-actions{align-items: stretch; width: 100%;}'
        +   '.manufacturing-link{width: 100%; text-align: center;}'
        +   '.manufacturing-sections{grid-template-columns: 1fr;}'
        +   '.manufacturing-table{font-size: 12px;}'
        + '}'
        + '</style>'
        + '<div class="manufacturing-dashboard">'
        +   '<div class="manufacturing-shell">'
        +   '<div class="manufacturing-header">'
        +     '<div>'
        +       '<div class="manufacturing-title">لوحة التصنيع: ' + requestName + '</div>'
        +       '<div class="manufacturing-subtitle">عرض سريع لما تم تصنيعه وما تبقى داخل هذا الطلب</div>'
        +     '</div>'
        +     '<div class="manufacturing-actions">'
        +       '<a href="' + manufacturingOpenFactoryLink(frm.doc.name) + '" target="_blank" class="btn btn-xs btn-default manufacturing-link">فتح شاشة التصنيع</a>'
        +       '<div class="indicator-pill ' + status.indicatorClass + '" style="white-space: nowrap;">حالة التصنيع: ' + manufacturingEscapeHtml(status.text) + '</div>'
        +       '<div class="indicator-pill ' + remainingTone + '" style="white-space: nowrap;">المتبقي: ' + manufacturingEscapeHtml(remaining) + '</div>'
        +     '</div>'
        +   '</div>'
        +   '<div class="manufacturing-body">'
        +   '<div class="manufacturing-grid">'
        +     manufacturingSummaryCard('المتبقي', remaining, 'var(--yellow-700, #b7791f)')
        +     manufacturingSummaryCard('تم تصنيعه', manufactured, 'var(--green-700, #15803d)')
        +     manufacturingSummaryCard('الإجمالي', total, 'var(--text-color)')
        +   '</div>'
        +   '<div class="manufacturing-progress-box">'
        +     '<div class="manufacturing-progress-label">نسبة الإنجاز</div>'
        +     '<div class="manufacturing-progress-value">' + manufacturingEscapeHtml(percent) + '%</div>'
        +   '</div>';

    if (!total) {
        html += '<div style="text-align:center; padding: 15px; color: var(--text-muted); border: 1px solid var(--border-color); border-radius: var(--border-radius);">لا توجد أصناف تصنيع متتبعة داخل هذا الطلب حتى الآن.</div>';
    } else {
        html += ''
            + '<div class="manufacturing-sections-title">تفاصيل التصنيع</div>'
            + '<div class="manufacturing-sections">'
            +   manufacturingSection('المتبقي للتصنيع', remaining, data.pending_items || [], 'pending', 'لا توجد أصناف متبقية للتصنيع', data)
            +   manufacturingSection('تم تصنيعه', manufactured, data.items || [], 'done', 'لا توجد أصناف مصنعة حتى الآن', data)
            + '</div>';
    }

    html += '</div></div></div>';
    field.$wrapper.html(html);
}

function renderManufacturingLoading(frm, text, tone) {
    var field = frm.fields_dict.custom_manufacturing_dashboard;
    if (!field || !field.$wrapper) return;

    var palette = tone || {
        background: 'var(--card-bg)',
        border: 'var(--border-color)',
        color: 'var(--text-muted)'
    };

    field.$wrapper.html(
        '<div style="padding:16px 18px;border-radius:14px;border:1px solid ' + palette.border + ';background:' + palette.background + ';color:' + palette.color + ';">'
        + manufacturingEscapeHtml(text)
        + '</div>'
    );
}

function refreshManufacturingTab(frm) {
    if (!frm.fields_dict.custom_manufacturing_dashboard) return;

    if (frm.is_new() || !frm.doc.name) {
        renderManufacturingLoading(frm, 'احفظ طلب المواد أولًا حتى تظهر لوحة التصنيع.');
        return;
    }

    renderManufacturingLoading(frm, 'جاري تحميل بيانات التصنيع...');

    frappe.call({
        method: 'get_manufactured_items',
        args: {
            mr: frm.doc.name
        },
        freeze: false,
        callback: function(response) {
            if (!response || !response.message) {
                renderManufacturingLoading(frm, 'تعذر تحميل بيانات التصنيع.', {
                    background: '#fef2f2',
                    border: '#fecaca',
                    color: '#991b1b'
                });
                return;
            }
            renderManufacturingDashboard(frm, response.message);
        },
        error: function() {
            renderManufacturingLoading(frm, 'تعذر تحميل بيانات التصنيع.', {
                background: '#fef2f2',
                border: '#fecaca',
                color: '#991b1b'
            });
        }
    });
}

frappe.ui.form.on('Material Request', {
    refresh: function(frm) {
        refreshManufacturingTab(frm);
    }
});
