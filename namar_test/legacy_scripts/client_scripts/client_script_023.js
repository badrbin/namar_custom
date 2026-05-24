// =====================================================
// ورقة التصنيع المحدثة (الإصدار 7) - جدول التخصيم
// DocType: Material Request
// HTML Field: custom_factory_sheet
// الميزات: خيارات تفاعلية (Sliding Door, 6CM, Glass Decore) + حقول قابلة للتحرير
// =====================================================

(function() {
    const fsState = {
        rules: null,
        rulesLoaded: false,
        allItems: [],
        manualValues: {},
        // تخزين الاختيارات التي تتم داخل الجدول
        selections: {}
    };

    frappe.ui.form.on('Material Request', {
        refresh: function(frm) {
            resetState();
            renderFactorySheet(frm);
        },
        onload: function(frm) {
            resetState();
            window.requestAnimationFrame(() => renderFactorySheet(frm));
        }
    });

    frappe.ui.form.on('Material Request Item', {
        'item_code': (frm) => debouncedRender(frm),
        'عرض': (frm) => debouncedRender(frm),
        'طول': (frm) => debouncedRender(frm),
        items_add: (frm) => debouncedRender(frm),
        items_remove: (frm) => debouncedRender(frm),
        form_render: (frm) => debouncedRender(frm)
    });

    function resetState() {
        fsState.rulesLoaded = false;
        fsState.rules = null;
        fsState.manualValues = {};
        fsState.selections = {};
    }

    let renderTimeout;
    function debouncedRender(frm) {
        clearTimeout(renderTimeout);
        renderTimeout = setTimeout(() => renderFactorySheet(frm), 150);
    }

    async function renderFactorySheet(frm) {
        if (!frm.fields_dict.custom_factory_sheet) return;
        const $wrapper = frm.fields_dict.custom_factory_sheet.$wrapper;

        if (!fsState.rulesLoaded) {
            fsState.rulesLoaded = true;
            $wrapper.html('<div style="text-align:center;padding:40px;color:#8d99a6;font-family:Tajawal,sans-serif">⏳ جاري تحميل البيانات والخيارات...</div>');

            try {
                const r = await frappe.call({
                    method: 'frappe.client.get_list',
                    args: {
                        doctype: 'Deduction Rule',
                        fields: ['item_code', 'sliding_door', 'no_of_leaf', 'item_name', 'type', 'joint', '6cm', 'glass_decore', 'door_leaf_w', 'door_leaf_h', 'u_w', 'u_h', 'panel_w', 'panel_h'],
                        limit_page_length: 0
                    }
                });
                fsState.rules = r.message || [];
                renderFactorySheet(frm);
            } catch (e) {
                $wrapper.html('<div style="color:red;padding:20px;">خطأ في تحميل القواعد</div>');
            }
            return;
        }

        processItems(frm);
        $wrapper.html(buildHTML(frm));
        exposeFunctions(frm);
        window.fsRebuild();
    }

    function processItems(frm) {
        fsState.allItems = (frm.doc.items || []).map((row, idx) => {
            // إذا لم يكن هناك اختيار يدوي سابق، نأخذ القيمة من الصف
            if (!fsState.selections[idx]) {
                fsState.selections[idx] = {
                    sliding_door: (row.sliding_door || '').trim(),
                    six_cm: (row['6cm'] || '').trim(),
                    glass_decore: (row.glass_decore || '').trim()
                };
            }

            return {
                idx: idx + 1,
                item_code: (row.item_code || '').trim(),
                item_name: (row.item_name || '').trim(),
                no_of_leaf: (row.no_of_leaf || '').trim(),
                type: (row.type || '').trim(),
                joint: (row.joint || '').trim(),
                width: parseFloat(row['عرض']) || 0,
                height: parseFloat(row['طول']) || 0,
                qty: row.qty || 1
            };
        });
    }

    function findBestRule(item, sel) {
        if (!item.item_code) return null;

        // البحث باستخدام الاختيارات الحالية (sel)
        let match = fsState.rules.find(r =>
            r.item_code === item.item_code &&
            (r.sliding_door || '') === sel.sliding_door &&
            (r.six_cm || '') === sel.six_cm &&
            (r.glass_decore || '') === sel.glass_decore &&
            (r.no_of_leaf || '') === item.no_of_leaf &&
            (r.type || '') === item.type &&
            (r.joint || '') === item.joint
        );
        if (match) return { rule: match, level: 'كاملة' };

        match = fsState.rules.find(r => r.item_code === item.item_code && (r.sliding_door || '') === sel.sliding_door);
        if (match) return { rule: match, level: 'جزئية' };

        match = fsState.rules.find(r => r.item_code === item.item_code && !r.sliding_door);
        if (match) return { rule: match, level: 'افتراضية' };

        return null;
    }

    function calculate(base, deduction) {
        return base > 0 ? (base + (parseFloat(deduction) || 0)) : 0;
    }

    function buildHTML(frm) {
        return `
            <div id="fs-root">
                <style>
                    #fs-root{direction:rtl;font-family:Tajawal,sans-serif;--p:#1a5276;padding:4px 0}
                    .fs-hdr{background:linear-gradient(135deg,#1a5276,#1a6ea0);border-radius:10px;padding:15px;margin-bottom:10px;color:#fff;display:flex;justify-content:space-between;align-items:center}
                    .fs-t{width:100%;border-collapse:collapse;background:#fff;border:1px solid #d1d8dd;font-size:0.75rem}
                    .fs-t th{background:#4a5568;color:#fff;padding:8px;border:1px solid #edf2f7}
                    .fs-t td{padding:6px;text-align:center;border-bottom:1px solid #edf2f7}
                    .ed-cell{width:50px;padding:3px;border:1px solid #cbd5e0;border-radius:4px;text-align:center;font-weight:bold;color:var(--p)}
                    .sel-box{padding:3px;border:1px solid #a0aec0;border-radius:4px;font-size:0.7rem;width:90px;background:#f7fafc}
                    .match-tag{font-size:0.6rem;padding:1px 4px;border-radius:3px;display:block;margin-top:2px}
                    .tag-كاملة{background:#c6f6d5;color:#22543d}
                    .tag-جزئية{background:#feebc8;color:#744210}
                </style>
                <div class="fs-hdr">
                    <div><h3 style="margin:0">📐 ورقة التصنيع - خيارات تفاعلية</h3></div>
                    <button style="padding:5px 10px;cursor:pointer;border-radius:4px;border:none" onclick="fsRefreshRules()">🔄 تحديث</button>
                </div>
                <div style="overflow-x:auto">
                    <table class="fs-t">
                        <thead>
                            <tr>
                                <th>#</th><th>الصنف</th><th>الخيارات</th><th>المقاسات</th>
                                <th style="background:#2c7a7b">الدرفة (W×H)</th>
                                <th style="background:#2b6cb0">الحلق U (W×H)</th>
                                <th style="background:#975a16">البانل (W×H)</th>
                                <th>الكمية</th>
                            </tr>
                        </thead>
                        <tbody id="fs-tb"></tbody>
                    </table>
                </div>
            </div>
        `;
    }

    function exposeFunctions(frm) {
        window.fsRebuild = function() {
            const $tbody = $('#fs-tb');
            const fmt = (v) => (v <= 0 ? '' : (v % 1 === 0 ? v.toString() : v.toFixed(1)));

            // استخراج الخيارات المتاحة من القواعد
            const allSliding = [...new Set(fsState.rules.map(r => r.sliding_door || '').filter(Boolean))];
            const all6cm = [...new Set(fsState.rules.map(r => r['6cm'] || '').filter(Boolean))];
            const allGlass = [...new Set(fsState.rules.map(r => r.glass_decore || '').filter(Boolean))];

            let rows = '';
            fsState.allItems.forEach((d, i) => {
                const ri = d.idx - 1;
                const sel = fsState.selections[ri];
                const result = findBestRule(d, sel);
                const rule = result ? result.rule : null;

                const auto = rule ? {
                    dw: calculate(d.width, rule.door_leaf_w), dh: calculate(d.height, rule.door_leaf_h),
                    uw: calculate(d.width, rule.u_w), uh: calculate(d.height, rule.u_h),
                    pw: calculate(d.width, rule.panel_w), ph: calculate(d.height, rule.panel_h)
                } : { dw:0, dh:0, uw:0, uh:0, pw:0, ph:0 };

                const getVal = (key, autoVal) => {
                    const mKey = `${ri}_${key}`;
                    return fsState.manualValues[mKey] !== undefined ? fsState.manualValues[mKey] : fmt(autoVal);
                };

                // بناء القوائم المنسدلة
                const makeSelect = (field, current, options) => {
                    return `
                        <select class="sel-box" onchange="fsSelectChange(${ri}, '${field}', this.value)">
                            <option value="">— ${field} —</option>
                            ${options.map(opt => `<option value="${opt}" ${current === opt ? 'selected' : ''}>${opt}</option>`).join('')}
                        </select>
                    `;
                };

                rows += `
                    <tr>
                        <td>${d.idx}</td>
                        <td style="text-align:right"><b>${d.item_code}</b><br><small>${d.item_name}</small></td>
                        <td>
                            ${makeSelect('Sliding', sel.sliding_door, allSliding)}<br>
                            ${makeSelect('6CM', sel.six_cm, all6cm)}<br>
                            ${makeSelect('Glass', sel.glass_decore, allGlass)}
                            ${result ? `<span class="match-tag tag-${result.level.includes('كاملة') ? 'كاملة' : 'جزئية'}">${result.level}</span>` : ''}
                        </td>
                        <td>${d.width} × ${d.height}</td>
                        <td>
                            <input class="ed-cell" value="${getVal('dw', auto.dw)}" oninput="fsManualEdit(${ri}, 'dw', this.value)"> ×
                            <input class="ed-cell" value="${getVal('dh', auto.dh)}" oninput="fsManualEdit(${ri}, 'dh', this.value)">
                        </td>
                        <td>
                            <input class="ed-cell" value="${getVal('uw', auto.uw)}" oninput="fsManualEdit(${ri}, 'uw', this.value)"> ×
                            <input class="ed-cell" value="${getVal('uh', auto.uh)}" oninput="fsManualEdit(${ri}, 'uh', this.value)">
                        </td>
                        <td>
                            <input class="ed-cell" value="${getVal('pw', auto.pw)}" oninput="fsManualEdit(${ri}, 'pw', this.value)"> ×
                            <input class="ed-cell" value="${getVal('ph', auto.ph)}" oninput="fsManualEdit(${ri}, 'ph', this.value)">
                        </td>
                        <td style="font-weight:bold">${d.qty}</td>
                    </tr>
                `;
            });
            $tbody.html(rows);
        };

        window.fsSelectChange = function(rowIdx, field, val) {
            const fMap = { 'Sliding': 'sliding_door', '6CM': 'six_cm', 'Glass': 'glass_decore' };
            fsState.selections[rowIdx][fMap[field]] = val;
            // عند تغيير الخيار، نقوم بمسح التعديلات اليدوية لهذا الصف لإعادة الحساب التلقائي
            ['dw', 'dh', 'uw', 'uh', 'pw', 'ph'].forEach(k => delete fsState.manualValues[`${rowIdx}_${k}`]);
            window.fsRebuild();
        };

        window.fsManualEdit = (ri, k, v) => { fsState.manualValues[`${ri}_${k}`] = v; };
        window.fsRefreshRules = () => { resetState(); renderFactorySheet(frm); };
    }
})();
