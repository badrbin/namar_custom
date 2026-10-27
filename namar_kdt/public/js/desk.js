/* One-page cutting planning, execution and label printing. No stock movements. */
;(function () {
    "use strict";

    // The app installs inactive. Reload after activation to switch handlers once.
    if (!frappe.boot?.namar_kdt_enabled || frappe.__namar_kdt_desk_registered) return;
    frappe.__namar_kdt_desk_registered = true;

    const API = "namar_kdt.api.call";
    const MAX_REQUESTS = 40;
    const escapeHTML = value => String(value == null ? "" : value).replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
    const number = value => Number.isFinite(Number(value)) ? Number(value) : 0;
    const dimensions = part => [part.height_mm, part.width_mm, part.thickness_mm].filter(value => number(value) > 0).map(number).join(" × ");
    const stateLabel = state => ({ available: "متاح للقص", reserved: "محجوز لأمر قص", completed: "تم القص", blocked: "يحتاج مراجعة", released: "مستبعد", cancelled: "ملغي" }[state] || state || "");
    const orderLabel = status => ({ Reserved: "معتمد للقص", Approved: "معتمد للقص", "In Progress": "تم القص جزئيًا", Completed: "مكتمل", Cancelled: "ملغي", reserved: "معتمد للقص", completed: "مكتمل", partial: "تم القص جزئيًا" }[status] || status || "معتمد للقص");
    const partKey = part => String(part.piece_key || part.name || "");
    const allocationKey = part => String(part.allocation_key || part.piece_key || "");
    const componentLabel = row => row.component_label || row.component || "";
    const sortComponentRows = rows => (rows || []).slice().sort((a, b) => number(b.quantity) - number(a.quantity) || String(a.color || "").localeCompare(String(b.color || ""), "ar") || componentLabel(a).localeCompare(componentLabel(b), "ar"));
    const reportQuantity = value => value == null || value === "" || typeof value === "number" && !Number.isFinite(value) ? "غير متوفر" : typeof value === "number" ? value.toLocaleString("en-US", { useGrouping: false, maximumFractionDigits: 12 }) : String(value);
    const activePart = part => !["released", "cancelled"].includes(part.state);
    function groupParts(parts) {
        const groups = new Map();
        (parts || []).filter(activePart).forEach(part => {
            const key = JSON.stringify([part.color || "", part.component || "", number(part.height_mm), number(part.width_mm), number(part.thickness_mm)]);
            if (!groups.has(key)) groups.set(key, { key, color: part.color, component: part.component, height_mm: part.height_mm, width_mm: part.width_mm, thickness_mm: part.thickness_mm, total: 0, available: 0, reserved: 0, completed: 0, parts: [] });
            const group = groups.get(key);
            group.parts.push(part);
            group.total += 1;
            if (["available", "reserved", "completed"].includes(part.state)) group[part.state] += 1;
        });
        return Array.from(groups.values()).sort((a, b) => String(a.color || "").localeCompare(String(b.color || ""), "ar") || String(a.component || "").localeCompare(String(b.component || ""), "ar") || number(a.height_mm) - number(b.height_mm) || number(a.width_mm) - number(b.width_mm));
    }
    function eligible(part, mode, editingName) {
        if (!partKey(part) || !activePart(part)) return false;
        if (mode === "order") return ["reserved", "completed"].includes(part.state);
        if (part.eligible === 0 || part.eligible === "0" || part.blocker) return false;
        return part.state === "available" || (Boolean(editingName) && part.state === "reserved" && part.order === editingName);
    }

    const CSS = `
      .kcd{direction:rtl;text-align:right;color:var(--text-color,#182b39);font-size:14px;max-width:1500px;margin:auto}
      .kcd *{box-sizing:border-box}.kcd button,.kcd input{font:inherit}.kcd button{cursor:pointer}.kcd button:disabled{cursor:default;opacity:.48}
      .kcd h2,.kcd h3,.kcd p{margin:0}.kcd h2{font-size:22px;font-weight:750}.kcd h3{font-size:16px;font-weight:700}
      .kcd-head,.kcd-toolbar,.kcd-card-title,.kcd-actions,.kcd-summary{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}
      .kcd-head{margin:4px 0 20px}.kcd-subtitle,.kcd-muted{color:var(--text-muted,#617583);font-size:12px}.kcd-subtitle{margin-top:6px!important;line-height:1.7}
      .kcd-card{border:1px solid var(--border-color,#e1e7eb);border-radius:12px;background:var(--card-bg,#fff);margin-bottom:16px;padding:18px}
      .kcd-card-title{margin-bottom:14px}.kcd-btn{border:1px solid var(--border-color,#d7e0e6);border-radius:7px;padding:8px 12px;background:var(--card-bg,#fff);color:inherit;white-space:nowrap}
      .kcd-btn-primary{background:#126a68;color:white;border-color:#126a68;font-weight:650}.kcd-btn-soft{background:#edf6f5;color:#16615f;border-color:#d4e9e6}.kcd-btn-small{padding:4px 9px;font-size:12px}.kcd-btn-link{border:0;background:transparent;color:#126a68;padding:5px}
      .kcd-fields{display:flex;gap:10px;align-items:end;flex-wrap:wrap}.kcd-field{display:flex;flex-direction:column;gap:5px;min-width:135px;flex:1}.kcd-field-search{flex:3;min-width:230px}.kcd-field label{font-size:12px;color:var(--text-muted,#617583)}
      .kcd input[type=text],.kcd input[type=date],.kcd input[type=number]{border:1px solid var(--border-color,#d7e0e6);border-radius:7px;background:var(--control-bg,#f8fafb);color:inherit;min-height:36px;padding:7px 10px;width:100%}
      .kcd input:focus,.kcd button:focus-visible,.kcd summary:focus-visible{outline:2px solid #349e98;outline-offset:2px}.kcd input[type=checkbox]{width:16px;height:16px;accent-color:#126a68;vertical-align:middle;cursor:pointer}
      .kcd-requests{margin-top:12px;max-height:240px;overflow:auto}.kcd-request{display:grid;grid-template-columns:minmax(100px,1fr) minmax(90px,2fr) auto auto;gap:12px;align-items:center;padding:9px 0;border-bottom:1px solid var(--border-color,#eef1f3)}.kcd-request:last-child{border-bottom:0}
      .kcd-id{direction:ltr;unicode-bidi:isolate;display:inline-block;font-size:12px;font-family:var(--font-stack,inherit)}.kcd-request-title{overflow-wrap:anywhere}.kcd-chips{display:flex;gap:7px;flex-wrap:wrap;margin-top:14px}.kcd-chip{display:inline-flex;align-items:center;gap:9px;padding:5px 9px;border:1px solid #d4e9e6;background:#f0f8f6;color:#245b54;border-radius:6px;font-size:12px}.kcd-chip button{border:0;background:transparent;color:#526b6a;font-size:17px;line-height:1;padding:0 3px}
      .kcd-summary{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px;margin-bottom:16px}.kcd-stat{padding:13px 15px;background:var(--control-bg,#f7f9fa);border-radius:9px}.kcd-stat strong{font-size:23px;display:block;font-weight:700;line-height:1.5}.kcd-stat span{font-size:12px;color:var(--text-muted,#617583)}.kcd-stat-good{background:#edf7f3;color:#16694a}.kcd-stat-good span{color:#35694f}.kcd-stat-review{background:#fff3dd;color:#835609}.kcd-stat-review span{color:#835609}
      .kcd-table-wrap{overflow:auto}.kcd-scroll-hint{display:none}.kcd-table{width:100%;border-collapse:collapse;white-space:nowrap}.kcd-table th{font-size:12px;color:var(--text-muted,#617583);background:var(--control-bg,#f7f9fa);font-weight:600}.kcd-table th,.kcd-table td{padding:11px 10px;text-align:right;border-bottom:1px solid var(--border-color,#e8edf0);vertical-align:middle}.kcd-table th:first-child{border-radius:0 6px 0 0}.kcd-table th:last-child{border-radius:6px 0 0 0}
      .kcd-table td.kcd-number,.kcd-table th.kcd-number{text-align:center}.kcd-qty{width:76px!important;text-align:center;direction:ltr}.kcd-expander{background:none;border:0;color:#126a68;font-size:12px;padding:5px 0;text-decoration:underline;text-underline-offset:3px}.kcd-detail>td{padding:0!important}.kcd-detail-inner{padding:12px 18px;background:var(--control-bg,#f7f9fa)}.kcd-parts th{background:transparent}.kcd-parts td,.kcd-parts th{font-size:12px;padding:8px}.kcd-parts tr:last-child td{border:0}.kcd-parts .kcd-context{max-width:300px;white-space:normal;overflow-wrap:anywhere;line-height:1.5}
      .kcd-badge{display:inline-block;border-radius:5px;padding:3px 7px;font-size:11px;background:#eef2f4;color:#4b6472}.kcd-badge-completed{background:#e4f5ea;color:#226d40}.kcd-badge-reserved{background:#fff1d8;color:#946207}.kcd-badge-blocked{background:#fdeceb;color:#a23b32}.kcd-badge-available{background:#eaf5f3;color:#21726a}
      .kcd-toolbar{border-top:1px solid var(--border-color,#e1e7eb);margin-top:16px;padding-top:15px}.kcd-actions{justify-content:flex-start;gap:8px}.kcd-selection{font-size:13px}.kcd-empty{text-align:center;color:var(--text-muted,#617583);padding:34px 15px;line-height:1.9}.kcd-alert{padding:10px 14px;border-radius:8px;margin-bottom:14px;line-height:1.8;overflow-wrap:anywhere}.kcd-alert-info{background:#edf6fa;color:#245d76}.kcd-alert-error{background:#fff0ed;color:#9b3528}.kcd-alert-success{background:#edf7ef;color:#28643d}.kcd [hidden]{display:none!important}
      .kcd-orders{max-height:300px;overflow:auto}.kcd-order-row{display:flex;align-items:center;justify-content:space-between;gap:12px;border-bottom:1px solid var(--border-color,#e8edf0);padding:10px 0}.kcd-order-row:last-child{border:0}.kcd-order-info{display:flex;gap:12px;align-items:center;flex-wrap:wrap}.kcd-history{margin-top:20px}.kcd-history summary{cursor:pointer;color:#126a68;font-size:13px}.kcd-history-row{padding:10px 0;border-bottom:1px solid var(--border-color,#e8edf0);font-size:12px;line-height:1.8}.kcd-history-row p{white-space:pre-wrap;overflow-wrap:anywhere}.kcd-filter{margin:12px 0;max-width:380px}.kcd-order-heading{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
      .kcd .kcd-btn-link,.kcd .kcd-expander{color:var(--kcd-accent,#126a68)}[data-theme="dark"] .kcd{--kcd-accent:#8ed0c7}
      .kcd-preparation{padding:3px 0 20px;margin:12px 0 20px;border-bottom:1px solid var(--border-color,#e1e7eb)}.kcd-preparation .kcd-subtitle{margin-bottom:12px!important}.kcd-preparation .kcd-empty{padding:20px 10px}.kcd-preparation-table td:nth-child(2){white-space:normal;min-width:150px;overflow-wrap:anywhere}.kcd-cut-details{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}.kcd-cut-details .kcd-filter{flex:1;min-width:180px}
      .kcd-component-name{white-space:normal!important;min-width:180px;max-width:480px;overflow-wrap:anywhere;line-height:1.6}.kcd-source-name{white-space:normal!important;min-width:140px;overflow-wrap:anywhere}.kcd-component-note{margin:12px 0!important;line-height:1.8}
      .kcd-component-table{table-layout:fixed;white-space:normal}.kcd-component-table th:first-child{width:58%}.kcd-component-table td{overflow-wrap:anywhere}.kcd-component-table .kcd-component-name{min-width:0;max-width:none}.kcd-advanced{margin-top:20px;border-top:1px solid var(--border-color,#e1e7eb);padding-top:14px}.kcd-advanced>summary{cursor:pointer;color:var(--text-muted,#617583);font-size:13px}.kcd-advanced>.kcd-actions{margin:14px 0}.kcd-component-sources{margin-top:18px}.kcd-home{padding:28px 18px;line-height:1.9}.kcd-home .kcd-actions{margin-top:18px}.kcd-component-source-title{margin:18px 0 8px!important;font-size:13px!important}
      .kcd-filters{display:flex;align-items:end;gap:10px;flex-wrap:wrap;margin:14px 0 8px}.kcd-filters>.kcd-field{min-width:180px}.kcd-multi{position:relative;min-width:0}.kcd-multi>summary{list-style:none;border:1px solid var(--border-color,#d7e0e6);border-radius:7px;background:var(--control-bg,#f8fafb);padding:8px 10px;cursor:pointer;min-height:36px;overflow-wrap:anywhere}.kcd-multi>summary::-webkit-details-marker{display:none}.kcd-multi>summary:after{content:"⌄";float:left;margin-right:10px}.kcd-multi[open]>summary:after{content:"⌃"}.kcd-multi-menu{position:absolute;top:calc(100% + 5px);right:0;width:100%;min-width:220px;z-index:20;padding:10px;border:1px solid var(--border-color,#d7e0e6);border-radius:8px;background:var(--card-bg,#fff);box-shadow:0 7px 20px #0002}.kcd-multi-options{max-height:220px;overflow:auto;margin-top:8px}.kcd-multi-option{display:flex;align-items:center;gap:8px;padding:8px 3px;color:var(--text-color,#182b39)!important;cursor:pointer;overflow-wrap:anywhere}.kcd-multi-option input{flex-shrink:0}.kcd-filter-chips{margin:0 0 12px}.kcd-filter-caption{font-size:12px;color:var(--text-muted,#617583);margin-bottom:8px!important}.kcd-request-actions{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:12px}.kcd-hidden-selection{padding:7px 10px;background:#fff3dd;color:#835609;border-radius:6px;margin-top:7px;line-height:1.7}
      @media(max-width:700px){.kcd-card{padding:12px}.kcd-head{gap:14px}.kcd h2{font-size:20px}.kcd-summary{grid-template-columns:repeat(2,minmax(0,1fr))}.kcd-request{grid-template-columns:1fr auto}.kcd-request-title{grid-row:2}.kcd-request .kcd-request-date{display:none}.kcd-field-search{min-width:100%;flex-basis:100%}.kcd-toolbar{align-items:flex-start}.kcd-actions{width:100%}.kcd-btn{padding:8px 10px}.kcd-stat strong{font-size:21px}.kcd-scroll-hint{display:block;font-size:11px;color:var(--text-muted,#617583);margin:8px 0}}
    `;

    class Desk {
        constructor(frm, root) {
            this.frm = frm;
            this.root = root;
            this.requests = new Map();
            this.groups = [];
            this.preparation = null;
            this.preparationError = "";
            this.selected = new Set();
            this.expanded = new Set();
            this.filters = { colors: new Set(), components: new Set(), workflow_states: new Set() };
            this.filterOptions = {};
            this.mode = "home";
            this.order = null;
            this.editing = null;
            this.busy = false;
            this.loading = false;
            this.previewReady = false;
            this.searchSequence = 0;
            this.previewSequence = 0;
            this.navigationSequence = 0;
            this.mutationIds = new Map();
            this.build();
        }

        build() {
            this.root.innerHTML = `<style>${CSS}</style><div class="kcd" dir="rtl">
              <div class="kcd-head"><div><h2>أوامر القص</h2><p class="kcd-subtitle">اختر المكونات من نتائج التخصيم، ثم افتح أمر القص واطبعه.</p></div><div class="kcd-actions"><button type="button" class="kcd-btn" data-action="orders">الأوامر السابقة</button><button type="button" class="kcd-btn kcd-btn-soft" data-action="new">أمر جديد</button></div></div>
              <div data-slot="notice" aria-live="polite"></div>
              <section class="kcd-card" data-slot="orders-panel" hidden><div class="kcd-card-title"><h3>أوامر القص</h3><button type="button" class="kcd-btn-link" data-action="close-orders">إغلاق</button></div><div data-slot="orders" class="kcd-orders"></div></section>
              <section class="kcd-card kcd-home" data-slot="home-panel"><h3>إنشاء أمر قص من نتائج التخصيم</h3><p class="kcd-subtitle">صفِّ التقرير حسب الحالة والألوان والمكونات، ثم أنشئ الأمر من المكونات المتاحة في النتائج.</p><div class="kcd-actions"><button type="button" class="kcd-btn kcd-btn-primary" data-action="new">فتح نتائج التخصيم</button></div><details class="kcd-advanced"><summary>أدوات القطع السابقة</summary><div class="kcd-actions"><button type="button" class="kcd-btn" data-action="legacy-plan">فتح تخطيط القطع السابق</button></div></details></section>
              <section class="kcd-card" data-slot="requests-panel"><div class="kcd-card-title"><h3>اختر طلبات المواد</h3><span class="kcd-muted">يمكن جمع أكثر من طلب في أمر واحد</span></div>
                <div class="kcd-fields"><div class="kcd-field kcd-field-search"><label for="kcd-search">رقم الطلب أو اسم العميل</label><input id="kcd-search" data-filter="search" type="text" placeholder="ابحث عن طلب مواد…" autocomplete="off"></div><div data-slot="workflow-filter" class="kcd-field"></div><div class="kcd-field"><label for="kcd-from">من تاريخ</label><input id="kcd-from" data-filter="from_date" type="date"></div><div class="kcd-field"><label for="kcd-to">إلى تاريخ</label><input id="kcd-to" data-filter="to_date" type="date"></div><button type="button" class="kcd-btn" data-action="search">بحث</button></div>
                <div data-slot="request-actions" class="kcd-request-actions"></div><div data-slot="requests" class="kcd-requests" aria-live="polite"></div><div data-slot="chosen" class="kcd-chips"></div>
              </section>
              <section class="kcd-card" data-slot="desk-panel"><div class="kcd-card-title"><div class="kcd-order-heading" data-slot="heading"><h3>احتياج القص</h3></div><button type="button" class="kcd-btn-link" data-action="refresh">تحديث البيانات</button></div>
                <div data-slot="edit-note"></div>
                <div class="kcd-filters"><div data-slot="color-filter" class="kcd-field"></div><div data-slot="component-filter" class="kcd-field"></div><button type="button" class="kcd-btn-link" data-action="clear-filters">مسح التصفية</button></div>
                <div data-slot="filter-chips" class="kcd-chips kcd-filter-chips"></div>
                <section data-slot="preparation" class="kcd-preparation" aria-live="polite"></section>
                <div class="kcd-cut-details"><h3>تفاصيل قطع القص</h3><div class="kcd-field kcd-filter"><label for="kcd-dimensions">بحث في مقاسات قطع القص فقط</label><input id="kcd-dimensions" type="text" data-filter="groups" placeholder="مثال: 2100 × 900"><span data-slot="preparation-search-note" class="kcd-muted">لا يغيّر كشف تجهيز المكونات.</span></div></div>
                <p data-slot="summary-caption" class="kcd-filter-caption"></p><div data-slot="summary" class="kcd-summary"></div>
                <div data-slot="table" class="kcd-table-wrap" aria-live="polite"></div><p class="kcd-scroll-hint">اسحب الجدول أفقيًا لعرض الكمية وتفاصيل القطع.</p><div data-slot="toolbar" class="kcd-toolbar"></div><details data-slot="component-details" class="kcd-advanced" hidden><summary>تفاصيل الأمر</summary><div data-slot="component-tools" class="kcd-actions"></div><div data-slot="component-sources" class="kcd-component-sources"></div><div data-slot="component-history"></div></details><div data-slot="history"></div>
              </section>
            </div>`;
            this.root.addEventListener("click", event => this.handleClick(event));
            this.root.addEventListener("change", event => this.handleChange(event));
            this.root.addEventListener("input", event => {
                if (event.target.dataset.filter === "groups") this.renderFilteredView();
                if (event.target.dataset.optionSearch) this.filterOptionList(event.target.dataset.optionSearch, event.target.value);
            });
            this.root.addEventListener("keydown", event => {
                if (event.key === "Enter" && ["search", "from_date", "to_date"].includes(event.target.dataset.filter)) { event.preventDefault(); this.search(); }
            });
            this.render();
        }

        slot(name) { return this.root.querySelector(`[data-slot="${name}"]`); }
        async rpc(action, payload) {
            const response = await frappe.call({ method: API, args: { action, payload: JSON.stringify(payload || {}) }, freeze: false });
            const result = response && response.message;
            if (!result || result.error) throw new Error(result && result.error || "تعذر تحميل البيانات. حدّث الصفحة وحاول مجددًا.");
            return result;
        }
        notify(message, type = "info") {
            this.slot("notice").innerHTML = message ? `<div class="kcd-alert kcd-alert-${type}" role="${type === "error" ? "alert" : "status"}">${escapeHTML(message)}</div>` : "";
        }
        reportError(error) {
            let message = error && error.message;
            try {
                const messages = JSON.parse(error.responseJSON._server_messages);
                const last = JSON.parse(messages[messages.length - 1]);
                message = last.message || message;
            } catch (_) { /* Frappe already presents structured server errors when available. */ }
            this.notify(message || "تعذر إتمام العملية. لم يتم تأكيد النتيجة؛ حدّث البيانات قبل إعادة المحاولة.", "error");
        }
        setBusy(value) {
            this.busy = value;
            this.root.querySelectorAll("button,input").forEach(element => {
                if (value) { element.dataset.wasDisabled = element.disabled ? "1" : "0"; element.disabled = true; }
                else if (element.dataset.wasDisabled !== undefined) { element.disabled = element.dataset.wasDisabled === "1"; delete element.dataset.wasDisabled; }
            });
            this.root.querySelector(".kcd").setAttribute("aria-busy", String(value));
            if (!value && this.pendingRouteOrder) Promise.resolve().then(() => {
                if (this.busy || !this.pendingRouteOrder) return;
                const name = this.pendingRouteOrder; this.pendingRouteOrder = null;
                this.openOrder(name);
            });
        }
        openRouteOrder(name) {
            if (typeof name !== "string" || !name.trim()) return;
            if (this.busy) { this.pendingRouteOrder = name.trim(); return; }
            this.openOrder(name.trim());
        }

        async search() {
            const sequence = ++this.searchSequence;
            const filters = {};
            ["search", "from_date", "to_date"].forEach(key => { const value = this.root.querySelector(`[data-filter="${key}"]`).value.trim(); if (value) filters[key] = value; });
            if (this.filters.workflow_states.size) filters.workflow_states = Array.from(this.filters.workflow_states);
            if (filters.from_date && filters.to_date && filters.from_date > filters.to_date) { this.notify("تاريخ البداية يجب أن يكون قبل تاريخ النهاية.", "error"); return; }
            this.searchLoading = true;
            this.renderRequestActions();
            this.slot("requests").innerHTML = '<div class="kcd-empty">جارٍ البحث…</div>';
            try {
                const result = await this.rpc("search", { filters });
                if (sequence !== this.searchSequence) return;
                this.searchResults = result.requests || [];
                this.searchTruncated = result.truncated;
                this.workflowOptions = result.filter_options && result.filter_options.workflow_states || this.workflowOptions || [];
                this.searchLoading = false;
                this.renderWorkflowFilter();
                this.renderRequests();
            } catch (error) { if (sequence === this.searchSequence) { this.searchLoading = false; this.searchResults = []; this.renderRequestActions(); this.slot("requests").textContent = "تعذر تحميل الطلبات."; this.reportError(error); } }
        }
        renderRequestActions() {
            const remaining = (this.searchResults || []).filter(request => !this.requests.has(request.name)).length;
            this.slot("request-actions").innerHTML = `<button type="button" class="kcd-btn kcd-btn-small" data-action="add-results" ${!remaining || this.busy || this.searchLoading ? "disabled" : ""}>إضافة نتائج البحث (${remaining})</button><span class="kcd-muted"><bdi>${this.requests.size} / ${MAX_REQUESTS}</bdi> طلب مضاف${this.searchTruncated ? ` · المعروض ${(this.searchResults || []).length} نتيجة؛ توجد نتائج إضافية` : ""}</span>${this.requests.size ? '<span class="kcd-muted">تغيير البحث لا يزيل الطلبات المضافة.</span>' : ""}`;
        }
        renderRequests() {
            this.renderRequestActions();
            this.slot("requests").innerHTML = (this.searchResults || []).map((request, index) => `<div class="kcd-request"><span class="kcd-id">${escapeHTML(request.name)}</span><span class="kcd-request-title">${escapeHTML(request.title || "")}${request.status || request.workflow_state ? ` <span class="kcd-badge">${escapeHTML(request.status || request.workflow_state)}</span>` : ""}</span><span class="kcd-muted kcd-request-date">${escapeHTML(request.date || "")}</span><button type="button" class="kcd-btn kcd-btn-small" data-action="add-request" data-index="${index}" ${this.requests.has(request.name) || this.busy || this.searchLoading ? "disabled" : ""}>${this.requests.has(request.name) ? "مضاف" : "إضافة"}</button></div>`).join("") || '<div class="kcd-empty">لا توجد طلبات مطابقة للبحث.</div>';
            if (this.searchTruncated) this.slot("requests").insertAdjacentHTML("beforeend", '<p class="kcd-muted" style="padding-top:10px">توجد نتائج إضافية؛ حدّد رقم الطلب أو فترة أقصر.</p>');
            this.slot("chosen").innerHTML = Array.from(this.requests.values()).map((request, index) => `<span class="kcd-chip"><bdi>${escapeHTML(request.name)}</bdi><button type="button" data-action="remove-request" data-index="${index}" aria-label="إزالة ${escapeHTML(request.name)}" ${this.busy ? "disabled" : ""}>×</button></span>`).join("");
        }
        async loadPreview(preserve = true) {
            const sequence = ++this.previewSequence;
            const navigation = this.navigationSequence;
            this.loading = true;
            this.previewReady = false;
            this.preparation = null;
            this.preparationError = "";
            this.render();
            try {
                const result = this.requests.size ? await this.rpc("preview", { request_names: Array.from(this.requests.keys()) }) : { groups: [] };
                if (sequence !== this.previewSequence || navigation !== this.navigationSequence || this.mode !== "plan") return;
                const previous = this.selected;
                this.groups = result.groups || [];
                this.preparation = result.preparation || null;
                if (this.requests.size && !this.preparation) this.preparationError = "تعذر تحميل كشف تجهيز المكونات من نتائج التخصيم. حدّث البيانات للمحاولة مجددًا.";
                if (this.editing) {
                    const completed = (this.editing.items || []).filter(part => part.state === "completed");
                    const completedKeys = new Set(completed.map(partKey));
                    const liveParts = this.groups.flatMap(group => group.parts || []);
                    const liveByKey = new Map(liveParts.map(part => [partKey(part), part]));
                    // A completed piece keeps the dimensions recorded when it was cut.
                    this.groups = groupParts(liveParts.filter(part => !completedKeys.has(partKey(part))).concat(completed.map(part => ({ ...part, source_changed: !liveByKey.has(partKey(part)) || liveByKey.get(partKey(part)).source_changed }))));
                }
                this.selected = new Set(this.groups.flatMap(group => group.parts || []).filter(part => this.canSelect(part) && preserve && previous.has(partKey(part))).map(partKey));
                this.previewWarnings = result.warnings || [];
                this.permissions = result.permissions || this.permissions;
                this.previewReady = true;
            } catch (error) { if (sequence === this.previewSequence && navigation === this.navigationSequence) { this.preparationError = "تعذر تحميل كشف تجهيز المكونات. حدّث البيانات للمحاولة مجددًا."; this.reportError(error); } }
            finally { if (sequence === this.previewSequence && navigation === this.navigationSequence) { this.loading = false; this.render(); } }
        }
        isComponentOrder() { return Boolean(this.order && this.order.order_mode === "components"); }
        canSelect(part) { return this.isComponentOrder() ? Boolean(allocationKey(part)) && ["reserved", "completed"].includes(part.state) : eligible(part, this.mode, this.editing && this.editing.name); }
        allParts() { return this.isComponentOrder() ? (this.order.items || []).filter(activePart) : this.groups.flatMap(group => group.parts || []); }
        selectionKey(part) { return this.isComponentOrder() ? allocationKey(part) : partKey(part); }
        selectedParts() { return this.allParts().filter(part => this.selected.has(this.selectionKey(part)) && this.canSelect(part)); }
        componentRows() { return this.isComponentOrder() ? this.order.component_rows || [] : []; }
        componentSources(row) {
            const items = new Map(this.allParts().map(item => [allocationKey(item), item]));
            const sources = row.sources && row.sources.length ? row.sources : (row.allocation_keys || []).map(key => ({ allocation_key: key }));
            return sources.map(source => ({ ...source, ...(items.get(allocationKey(source)) || {}) })).filter(activePart);
        }
        visibleComponentRows() {
            return sortComponentRows(this.componentRows()).map(row => ({ row, index: this.componentRows().indexOf(row) }));
        }
        componentMachineItems(selectedOnly = true) {
            const selected = selectedOnly && this.selectedParts().length ? this.selectedParts() : this.allParts();
            const keys = new Set(selected.flatMap(item => item.linked_piece_keys || []));
            return (this.order.machine_items || []).filter(item => activePart(item) && keys.has(partKey(item)));
        }
        renderMultiFilter(slot, name, label, options, allLabel) {
            const previous = this.root.querySelector(`[data-multi="${name}"]`);
            const open = previous && previous.open;
            const search = previous && previous.querySelector('[data-option-search]').value || "";
            this.filterOptions[name] = Array.from(new Set(options.map(value => String(value || "")).filter(value => value.trim()))).sort((a, b) => a.localeCompare(b, "ar"));
            const selected = this.filters[name];
            this.slot(slot).innerHTML = `<label for="kcd-${name}">${label}</label><details class="kcd-multi" data-multi="${name}" ${open ? "open" : ""}><summary id="kcd-${name}" data-multi-summary="${name}" data-all-label="${allLabel}"></summary><div class="kcd-multi-menu"><div><button type="button" class="kcd-btn-link" data-action="select-multi" data-filter-name="${name}" ${this.busy ? "disabled" : ""}>اختيار الكل</button><button type="button" class="kcd-btn-link" data-action="clear-multi" data-filter-name="${name}" ${this.busy ? "disabled" : ""}>مسح الاختيار</button></div><input type="text" data-option-search="${name}" value="${escapeHTML(search)}" aria-label="بحث في ${label}" placeholder="بحث…" autocomplete="off" ${this.busy ? "disabled" : ""}><div class="kcd-multi-options">${this.filterOptions[name].map(value => `<label class="kcd-multi-option"><input type="checkbox" data-multi-filter="${name}" value="${escapeHTML(value)}" ${selected.has(value) ? "checked" : ""} ${this.busy ? "disabled" : ""}><span>${escapeHTML(value)}</span></label>`).join("") || '<p class="kcd-muted">لا توجد خيارات متاحة.</p>'}</div><p class="kcd-muted" data-option-empty hidden>لا توجد خيارات مطابقة.</p></div></details>`;
            this.updateMultiSummary(name);
            this.filterOptionList(name, search);
        }
        renderWorkflowFilter() {
            this.renderMultiFilter("workflow-filter", "workflow_states", "حالة طلب المواد", (this.workflowOptions || []).concat(Array.from(this.filters.workflow_states)), "كل الحالات");
        }
        renderFilters() {
            this.renderWorkflowFilter();
            const preparationRows = this.mode === "plan" && this.preparation && !this.preparation.error ? this.preparation.rows || [] : [];
            const rows = this.isComponentOrder() ? this.componentRows() : this.groups.concat(preparationRows);
            this.renderMultiFilter("color-filter", "colors", "الألوان", rows.map(row => row.color), "كل الألوان");
            this.renderMultiFilter("component-filter", "components", "المكوّن", rows.map(row => this.isComponentOrder() ? componentLabel(row) : row.component), "كل المكونات");
            this.renderFilterChips();
        }
        updateMultiSummary(name) {
            const summary = this.root.querySelector(`[data-multi-summary="${name}"]`);
            if (summary) summary.textContent = this.filters[name].size ? Array.from(this.filters[name]).join("، ") : summary.dataset.allLabel;
        }
        filterOptionList(name, search) {
            const menu = this.root.querySelector(`[data-multi="${name}"]`);
            if (!menu) return;
            const query = search.trim().toLocaleLowerCase();
            const options = Array.from(menu.querySelectorAll(".kcd-multi-option"));
            options.forEach(option => { option.hidden = !option.textContent.toLocaleLowerCase().includes(query); });
            menu.querySelector("[data-option-empty]").hidden = !options.length || options.some(option => !option.hidden);
            const selectAll = menu.querySelector('[data-action="select-multi"]');
            selectAll.textContent = query ? "اختيار كل النتائج" : "اختيار الكل";
            selectAll.disabled = this.busy || !options.some(option => !option.hidden);
        }
        renderFilterChips() {
            const labels = { colors: "اللون", components: "المكوّن" };
            this.slot("filter-chips").innerHTML = ["colors", "components"].flatMap(name => Array.from(this.filters[name]).map((value, index) => `<span class="kcd-chip">${labels[name]}: ${escapeHTML(value)}<button type="button" data-action="remove-filter" data-filter-name="${name}" data-index="${index}" aria-label="إزالة تصفية ${escapeHTML(value)}" ${this.busy ? "disabled" : ""}>×</button></span>`)).join("");
        }
        renderSummary() {
            if (this.isComponentOrder()) {
                const rows = this.visibleComponentRows();
                const sources = rows.flatMap(({ row }) => this.componentSources(row));
                const stats = [[rows.length, "مكونات ظاهرة"], [sources.filter(source => source.state === "reserved").length, "مصادر محجوزة"], [sources.filter(source => source.state === "completed").length, "مصادر تم قصها"]];
                this.slot("summary-caption").textContent = "الكميات مبينة لكل مكوّن كما وردت في نتائج التخصيم؛ العدادات أدناه لصفوف المصادر، وليست كميات ألواح أو قطع ماكينة.";
                this.slot("summary").innerHTML = stats.map(([count, label], index) => `<div class="kcd-stat ${index === 2 ? "kcd-stat-good" : ""}"><strong>${count}</strong><span>${label}</span></div>`).join("");
                return;
            }
            const parts = this.visibleGroups().flatMap(({ group }) => group.parts || []).filter(activePart);
            const total = this.allParts().filter(activePart).length;
            this.slot("summary-caption").textContent = `الملخص للقطع الظاهرة: ${parts.length} من ${total} قطعة ${this.mode === "order" ? "في الأمر" : "في الطلبات المضافة"}`;
            const counts = { total: parts.length, available: parts.filter(part => part.state === "available").length, reserved: parts.filter(part => part.state === "reserved").length, completed: parts.filter(part => part.state === "completed").length, blocked: parts.filter(part => part.state === "blocked").length };
            const stats = [["total", "القطع الظاهرة"], ["reserved", "محجوز للقص"], ["completed", "تم القص"], ["available", "متاح لأمر جديد"]];
            if (counts.blocked) stats.push(["blocked", "يحتاج مراجعة"]);
            this.slot("summary").innerHTML = stats.map(([key, label]) => `<div class="kcd-stat ${key === "completed" ? "kcd-stat-good" : key === "blocked" ? "kcd-stat-review" : ""}"><strong>${counts[key]}</strong><span>${label}</span></div>`).join("");
        }
        renderFilteredView() {
            this.renderPreparation(); this.renderSummary(); this.renderFilterChips(); this.renderTable(); this.renderToolbar();
        }
        renderPreparation() {
            const target = this.slot("preparation");
            target.hidden = this.mode !== "plan";
            if (target.hidden) { target.innerHTML = ""; return; }
            const heading = '<h3>كشف تجهيز المكونات</h3><p class="kcd-subtitle">إجمالي احتياج الطلبات المضافة من نتائج التخصيم؛ يشمل المحجوز والمقصوص.</p>';
            if (this.loading) { target.innerHTML = heading + '<div class="kcd-empty">جارٍ جلب كشف تجهيز المكونات من نتائج التخصيم…</div>'; return; }
            if (!this.requests.size) { target.innerHTML = heading + '<div class="kcd-empty">أضف طلب مواد لعرض احتياج المكونات.</div>'; return; }
            const error = this.preparationError || this.preparation && this.preparation.error;
            if (error) { target.innerHTML = heading + `<div class="kcd-alert kcd-alert-error" role="status">${escapeHTML(error)}</div>`; return; }
            const allRows = this.preparation && this.preparation.rows || [];
            const rows = allRows.filter(row => (!this.filters.colors.size || this.filters.colors.has(row.color)) && (!this.filters.components.size || this.filters.components.has(row.component))).slice().sort((a, b) => String(a.color || "").localeCompare(String(b.color || ""), "ar") || String(a.component || "").localeCompare(String(b.component || ""), "ar"));
            if (!rows.length) { target.innerHTML = heading + `<div class="kcd-empty">${allRows.length ? "لا توجد مكونات مطابقة لتصفية اللون والمكوّن." : "لا توجد مكونات في نتائج التخصيم للطلبات المضافة."}</div>`; return; }
            const hasUom = rows.some(row => row.uom != null && String(row.uom).trim());
            // Quantities are report output. Never derive them from cut-piece counts or sum unlike units.
            const body = rows.map(row => {
                const quantity = row.quantity == null || row.quantity === "" || typeof row.quantity === "number" && !Number.isFinite(row.quantity) ? "غير متوفر" : typeof row.quantity === "number" ? row.quantity.toLocaleString("en-US", { useGrouping: false, maximumFractionDigits: 12 }) : String(row.quantity);
                return `<tr><td>${escapeHTML(row.color || "غير محدد")}</td><td data-preparation-component>${escapeHTML(row.component || "غير محدد")}</td><td class="kcd-number" data-preparation-quantity><bdi>${escapeHTML(quantity)}</bdi></td>${hasUom ? `<td>${escapeHTML(row.uom || "")}</td>` : ""}</tr>`;
            }).join("");
            target.innerHTML = heading + `<div class="kcd-table-wrap"><table class="kcd-table kcd-preparation-table"><thead><tr><th>اللون</th><th>المكوّن</th><th class="kcd-number">كمية الاحتياج</th>${hasUom ? "<th>الوحدة</th>" : ""}</tr></thead><tbody>${body}</tbody></table></div>`;
        }
        visibleGroups() {
            const filter = this.root.querySelector('[data-filter="groups"]').value.trim().toLocaleLowerCase();
            return this.groups.map((group, index) => ({ group, index })).filter(({ group }) => (!this.filters.colors.size || this.filters.colors.has(group.color)) && (!this.filters.components.size || this.filters.components.has(group.component)) && (!filter || dimensions(group).toLocaleLowerCase().includes(filter)));
        }
        render() {
            const simple = this.isComponentOrder();
            this.slot("home-panel").hidden = this.mode !== "home";
            this.slot("desk-panel").hidden = this.mode === "home";
            this.slot("requests-panel").hidden = this.mode !== "plan";
            this.slot("preparation-search-note").hidden = this.mode !== "plan";
            this.root.querySelector(".kcd-cut-details").hidden = simple;
            this.root.querySelector(".kcd-scroll-hint").hidden = simple;
            this.root.querySelector(".kcd-filters").hidden = simple;
            ["filter-chips", "summary-caption", "summary", "edit-note"].forEach(name => { this.slot(name).hidden = simple; });
            this.slot("component-details").hidden = !simple;
            this.slot("heading").innerHTML = this.mode === "order" && this.order
                ? `<h3>أمر القص <bdi>${escapeHTML(this.order.name)}</bdi></h3>${simple ? "" : `<span class="kcd-badge">${escapeHTML(orderLabel(this.order.status))}</span><span class="kcd-muted">مراجعة ${escapeHTML(this.order.revision)}</span>`}`
                : `<h3>${this.editing ? `تعديل أمر القص <bdi>${escapeHTML(this.editing.name)}</bdi>` : "احتياج القص"}</h3>`;
            if (!simple) this.renderFilters();
            this.renderPreparation();
            if (!simple) this.renderSummary();
            this.slot("edit-note").innerHTML = !simple && this.editing ? '<div class="kcd-alert kcd-alert-info">يمكن إضافة قطع متاحة أو استبعاد قطع لم تُقص. القطع المنفذة محفوظة؛ استخدم «تصحيح تسجيل» أو «إعادة قص» لتعديلها مع حفظ السجل.</div>' : "";
            if (this.previewWarnings && this.mode === "plan") this.slot("edit-note").insertAdjacentHTML("beforeend", this.previewWarnings.map(warning => `<div class="kcd-alert kcd-alert-info">${escapeHTML(typeof warning === "string" ? warning : warning.message || "")}</div>`).join(""));
            this.renderRequests();
            this.renderTable();
            this.renderToolbar();
            this.renderHistory();
        }
        renderTable() {
            if (this.isComponentOrder()) { this.renderComponentTable(); return; }
            if (this.loading) { this.slot("table").innerHTML = '<div class="kcd-empty">جارٍ احتساب الاحتياج والتحقق من القص السابق…</div>'; return; }
            if (!this.groups.length) { this.slot("table").innerHTML = `<div class="kcd-empty">${this.requests.size || this.mode === "order" ? "لا توجد قطع قص لهذه الطلبات." : "أضف طلب مواد لتظهر المكونات والألوان والمقاسات المطلوبة."}</div>`; return; }
            const groups = this.visibleGroups();
            if (!groups.length) { this.slot("table").innerHTML = '<div class="kcd-empty">لا توجد مجموعات مطابقة للتصفية.</div>'; return; }
            const disabled = this.busy ? "disabled" : "";
            const hasBlocked = groups.some(({ group }) => (group.parts || []).some(part => part.state === "blocked"));
            this.slot("table").innerHTML = `<table class="kcd-table"><thead><tr><th>اللون</th><th>المكوّن</th><th>مقاس القص <span class="kcd-muted">مم</span></th><th class="kcd-number">المطلوب</th><th class="kcd-number">محجوز</th><th class="kcd-number">تم القص</th><th class="kcd-number">متاح</th>${hasBlocked ? '<th class="kcd-number">يحتاج مراجعة</th>' : ""}<th class="kcd-number">المحدد</th><th>القطع</th></tr></thead><tbody>${groups.map(({ group, index }) => {
                const selectable = (group.parts || []).filter(part => this.canSelect(part));
                const selected = selectable.filter(part => this.selected.has(partKey(part))).length;
                const counts = { total: (group.parts || []).filter(activePart).length, reserved: (group.parts || []).filter(part => part.state === "reserved").length, completed: (group.parts || []).filter(part => part.state === "completed").length, available: (group.parts || []).filter(part => part.state === "available").length, blocked: (group.parts || []).filter(part => part.state === "blocked").length };
                const open = this.expanded.has(group.key);
                return `<tr><td>${escapeHTML(group.color || "غير محدد")}</td><td>${escapeHTML(group.component || "غير محدد")}</td><td><bdi>${escapeHTML(dimensions(group) || "غير متوفر")}</bdi></td><td class="kcd-number">${counts.total}</td><td class="kcd-number">${counts.reserved}</td><td class="kcd-number">${counts.completed}</td><td class="kcd-number">${counts.available}</td>${hasBlocked ? `<td class="kcd-number">${counts.blocked ? `<span class="kcd-badge kcd-badge-blocked">${counts.blocked}</span>` : "0"}</td>` : ""}<td class="kcd-number"><input type="number" class="kcd-qty" min="0" max="${selectable.length}" step="1" value="${selected}" data-group-quantity="${index}" aria-label="كمية ${escapeHTML(group.component)} ${escapeHTML(group.color)}" ${!selectable.length ? "disabled" : disabled}></td><td><button type="button" class="kcd-expander" data-action="expand" data-index="${index}" aria-expanded="${open}">${open ? "إخفاء" : "عرض"} (${counts.total})</button></td></tr>${open ? `<tr class="kcd-detail"><td colspan="${hasBlocked ? 10 : 9}"><div class="kcd-detail-inner">${this.renderParts(group, index)}</div></td></tr>` : ""}`;
            }).join("")}</tbody></table>`;
        }
        renderComponentTable() {
            const rows = this.visibleComponentRows();
            this.slot("table").innerHTML = rows.length ? `<table class="kcd-table kcd-component-table"><thead><tr><th>اسم المكوّن الكامل</th><th>اللون</th><th class="kcd-number">الكمية</th></tr></thead><tbody>${rows.map(({ row }) => `<tr><td class="kcd-component-name" data-component-name>${escapeHTML(componentLabel(row) || "غير محدد")}</td><td>${escapeHTML(row.color || "غير محدد")}</td><td class="kcd-number" data-component-quantity><bdi>${escapeHTML(reportQuantity(row.quantity))}</bdi></td></tr>`).join("")}</tbody></table>` : '<div class="kcd-empty">لا توجد مكونات في هذا الأمر.</div>';
            this.slot("component-sources").innerHTML = rows.map(({ row }) => `<h3 class="kcd-component-source-title">${escapeHTML(componentLabel(row))} · ${escapeHTML(row.color || "غير محدد")}</h3><div class="kcd-table-wrap">${this.renderComponentSources(row)}</div>`).join("");
        }
        renderComponentSources(row) {
            const sources = this.componentSources(row);
            return `<table class="kcd-table kcd-parts"><thead><tr><th>طلب المواد</th><th>المصدر</th><th class="kcd-number">كمية المصدر</th></tr></thead><tbody>${sources.map(source => `<tr><td><bdi>${escapeHTML(source.material_request || "")}</bdi></td><td class="kcd-source-name">${escapeHTML(source.door_label || source.door_row || source.source_row || "")}</td><td class="kcd-number"><bdi>${escapeHTML(reportQuantity(source.quantity))}</bdi></td></tr>`).join("")}</tbody></table>`;
        }
        renderParts(group, groupIndex) {
            const parts = group.parts || [];
            const hasDoor = parts.some(part => part.door_label || part.door_row || part.unit_no || part.item_name);
            const rows = parts.map((part, index) => {
                const order = !part.order ? "" : part.legacy ? `<span class="kcd-muted">أمر سابق: <bdi>${escapeHTML(part.order)}</bdi></span>` : `<button type="button" class="kcd-btn-link" data-action="part-order" data-group="${groupIndex}" data-index="${index}"><bdi>${escapeHTML(part.order)}</bdi></button>`;
                const warning = part.source_changed ? '<div class="kcd-muted">تغيّر المقاس أو اللون في الطلب بعد اعتماد القص. راجع التعديل قبل التنفيذ أو الطباعة.</div>' : "";
                return `<tr><td><input type="checkbox" data-piece-group="${groupIndex}" data-piece-index="${index}" aria-label="تحديد القطعة ${escapeHTML(part.piece_id || part.part_no || index + 1)}" ${this.selected.has(partKey(part)) ? "checked" : ""} ${!this.canSelect(part) || this.busy ? "disabled" : ""}></td><td><bdi>${escapeHTML(part.material_request)}</bdi></td>${hasDoor ? `<td>${escapeHTML(part.door_label || part.door_row || "")}${part.unit_no ? ` <span class="kcd-muted">/ ${escapeHTML(part.unit_no)}</span>` : ""}${part.item_name ? `<div class="kcd-muted">${escapeHTML(part.item_name)}</div>` : ""}</td>` : ""}<td><bdi>${escapeHTML(part.piece_id || part.part_no || index + 1)}</bdi></td><td class="kcd-context"><span class="kcd-badge kcd-badge-${["available", "reserved", "completed", "blocked"].includes(part.state) ? part.state : "blocked"}">${escapeHTML(stateLabel(part.state))}</span> ${order}${part.blocker ? `<div class="kcd-muted">${escapeHTML(part.blocker)}</div>` : ""}${warning}${part.completed_at ? `<div class="kcd-muted">${escapeHTML(part.completed_at)}</div>` : ""}${number(part.attempt) > 1 ? `<div class="kcd-muted">محاولة القص رقم ${number(part.attempt)}</div>` : ""}</td></tr>`;
            }).join("");
            return `<table class="kcd-table kcd-parts"><thead><tr><th>تحديد</th><th>طلب المواد</th>${hasDoor ? "<th>الباب / الحبة</th>" : ""}<th>القطعة</th><th>الحالة والتتبع</th></tr></thead><tbody>${rows}</tbody></table>`;
        }
        renderToolbar() {
            if (this.isComponentOrder()) { this.renderComponentToolbar(); return; }
            const selected = this.selectedParts();
            const visible = new Set(this.visibleGroups().flatMap(({ group }) => group.parts || []).map(partKey));
            const hiddenSelected = selected.filter(part => !visible.has(partKey(part))).length;
            const disabled = this.busy || this.loading || this.mode === "plan" && !this.previewReady ? "disabled" : "";
            const allowed = permission => !this.permissions || this.permissions[permission] !== false;
            const button = (action, label, enabled, primary = false) => `<button type="button" class="kcd-btn ${primary ? "kcd-btn-primary" : ""}" data-action="${action}" ${!enabled || this.busy ? "disabled" : action === "cancel-edit" ? "" : disabled}>${label}</button>`;
            let actions;
            if (this.mode === "plan") actions = this.editing
                ? button("save-revision", "حفظ التعديل", allowed("can_edit"), true) + button("cancel-edit", "إلغاء التعديل", true)
                : button("approve-print", "اعتماد وطباعة", selected.length > 0 && allowed("can_approve"), true);
            else {
                const reservedOnly = selected.length > 0 && selected.every(part => part.state === "reserved");
                const completedOnly = selected.length > 0 && selected.every(part => part.state === "completed");
                actions = button("complete", "تم القص", reservedOnly && allowed("can_complete"), true) + button("print", selected.length ? "طباعة المحدد" : "طباعة الأمر", this.allParts().length > 0) + button("edit", "تعديل الأمر", allowed("can_edit")) + button("recut", "إعادة قص", completedOnly && allowed("can_recut")) + button("correct", "تصحيح تسجيل", completedOnly && allowed("can_correct")) + button("cancel", "إلغاء غير المقصوص", this.allParts().some(part => part.state === "reserved") && allowed("can_edit"));
            }
            this.slot("toolbar").innerHTML = `<div class="kcd-selection"><strong>${selected.length}</strong> قطعة محددة <button type="button" class="kcd-btn-link" data-action="select-all" ${disabled}>تحديد الظاهر</button><button type="button" class="kcd-btn-link" data-action="select-none" ${disabled}>مسح التحديد كله</button>${hiddenSelected ? `<div class="kcd-hidden-selection" role="status">منها ${hiddenSelected} خارج التصفية؛ ستبقى ضمن ${this.mode === "order" ? "القطع المحددة للإجراء والطباعة" : "الأمر عند الاعتماد أو حفظ التعديل"}.</div>` : ""}${this.mode === "order" ? '<div class="kcd-muted">حدّد قطعًا محجوزة لتسجيل القص، أو قطعًا مقصوصة للتصحيح وإعادة القص.</div>' : ""}</div><div class="kcd-actions">${actions}</div>`;
        }
        renderComponentToolbar() {
            const allowed = permission => !this.permissions || this.permissions[permission] !== false;
            const button = (action, label, enabled, primary = false) => `<button type="button" class="kcd-btn ${primary ? "kcd-btn-primary" : ""}" data-action="${action}" ${this.busy || !enabled ? "disabled" : ""}>${label}</button>`;
            this.slot("toolbar").innerHTML = `<div class="kcd-actions">${button("print-components", "طباعة", this.componentRows().length > 0, true)}</div>`;
            this.slot("component-tools").innerHTML = button("edit", "تعديل من التقرير", allowed("can_edit")) + (this.componentMachineItems(false).length ? button("print", "طباعة ملصقات KDT", true) : "");
        }
        renderHistory() {
            const target = this.slot(this.isComponentOrder() ? "component-history" : "history");
            this.slot(this.isComponentOrder() ? "history" : "component-history").innerHTML = "";
            if (this.mode !== "order" || !this.order) { target.innerHTML = ""; return; }
            const events = this.mode === "order" && this.order && this.order.history || [];
            const labels = { approve: "اعتماد الأمر", revise: "تعديل الأمر", complete: "تسجيل القص", correct: "تصحيح تسجيل", recut: "إعادة قص", print: "طباعة", cancel: "إلغاء غير المقصوص" };
            Object.keys(labels).forEach(action => { labels[`component_${action}`] = labels[action]; });
            const rows = events.map(event => `<div class="kcd-history-row"><strong>${escapeHTML(labels[event.event_action || event.action] || event.event_action || event.action || event.event || "تحديث")}</strong> <span class="kcd-muted">${escapeHTML(event.event_time || event.creation || event.timestamp || event.date || "")}${event.user || event.owner ? ` · ${escapeHTML(event.user || event.owner)}` : ""}</span>${event.piece_id ? `<div>القطعة: <bdi>${escapeHTML(event.piece_id)}</bdi></div>` : ""}${event.reason ? `<p>${escapeHTML(event.reason)}</p>` : ""}</div>`).join("");
            target.innerHTML = `<div class="kcd-history"><button type="button" class="kcd-btn-link" data-action="history" aria-expanded="${Boolean(this.historyOpen)}">${this.historyOpen ? "إخفاء سجل الأمر" : "سجل الأمر والتعديلات"}</button>${this.historyOpen ? (this.historyLoading ? '<p class="kcd-muted">جارٍ تحميل السجل…</p>' : rows || '<p class="kcd-muted">لا توجد أحداث مسجلة.</p>') : ""}</div>`;
        }

        async toggleHistory() {
            this.historyOpen = !this.historyOpen;
            if (!this.historyOpen || this.order.history) { this.renderHistory(); return; }
            const orderName = this.order.name;
            this.historyLoading = true;
            this.renderHistory();
            try { const result = await this.rpc("history", { name: orderName }); if (this.order && this.order.name === orderName) this.order.history = result.events || []; }
            catch (error) { this.reportError(error); }
            finally { this.historyLoading = false; this.renderHistory(); }
        }

        async showOrders() {
            this.slot("orders-panel").hidden = false;
            this.slot("orders").innerHTML = '<div class="kcd-empty">جارٍ تحميل الأوامر…</div>';
            try {
                const result = await this.rpc("orders", {});
                this.recentOrders = result.orders || [];
                this.slot("orders").innerHTML = this.recentOrders.map((order, index) => `<div class="kcd-order-row"><div class="kcd-order-info"><bdi>${escapeHTML(order.name)}</bdi>${order.order_mode === "components" ? "" : `<span class="kcd-badge">${escapeHTML(orderLabel(order.status))}</span><span class="kcd-muted"><bdi>${number(order.completed)} / ${number(order.quantity)}</bdi> قطعة</span>`}<span class="kcd-muted">${escapeHTML(order.modified || "")}</span></div><button type="button" class="kcd-btn kcd-btn-small" data-action="open-order" data-index="${index}">فتح</button></div>`).join("") || '<div class="kcd-empty">لا توجد أوامر قص حتى الآن.</div>';
            } catch (error) { this.reportError(error); this.slot("orders").textContent = "تعذر تحميل الأوامر."; }
        }
        applyOrder(result) {
            this.preparation = null;
            this.preparationError = "";
            this.order = result.order || result;
            this.permissions = this.order.permissions || this.permissions;
            this.mode = "order";
            this.editing = null;
            this.historyOpen = false;
            this.loading = false;
            ++this.previewSequence;
            ++this.navigationSequence;
            this.groups = this.isComponentOrder() ? [] : groupParts(this.order.items || []);
            this.selected = this.isComponentOrder() ? new Set() : new Set(this.allParts().filter(part => part.state === "reserved").map(part => this.selectionKey(part)));
            this.expanded.clear();
            this.slot("component-details").open = false;
            this.slot("orders-panel").hidden = true;
            this.root.querySelector('[data-filter="groups"]').value = "";
            this.filters.colors.clear(); this.filters.components.clear();
            this.render();
        }
        async openOrder(name) {
            if (!name || this.busy) return;
            const navigation = ++this.navigationSequence;
            this.setBusy(true);
            try { const result = await this.rpc("order", { name }); if (navigation === this.navigationSequence) { this.applyOrder(result); this.notify(""); } }
            catch (error) {
                // The navigation invalidated any pending preview, including its cleanup.
                if (navigation === this.navigationSequence && this.mode === "plan" && this.loading) {
                    this.loading = false;
                    this.previewReady = false;
                    this.preparation = null;
                    this.preparationError = "تعذر تحميل كشف تجهيز المكونات. حدّث البيانات للمحاولة مجددًا.";
                }
                this.reportError(error);
            }
            finally { this.setBusy(false); this.render(); }
        }
        async beginEdit() {
            if (this.isComponentOrder()) {
                frappe.route_options = { cut_order_edit: { name: this.order.name, revision: this.order.revision, component_scope: this.order.component_scope, completed_component_keys: this.componentRows().filter(row => this.componentSources(row).some(source => source.state === "completed")).map(row => row.component_key) } };
                return frappe.set_route("query-report", "نتائج التخصيم");
            }
            this.editing = this.order;
            this.mode = "plan";
            ++this.navigationSequence;
            this.requests = new Map((this.order.request_names || Array.from(new Set((this.order.items || []).map(part => part.material_request)))).filter(Boolean).map(name => [name, { name }]));
            this.selected = new Set((this.order.items || []).filter(part => part.state === "reserved").map(partKey));
            await this.loadPreview(true);
        }
        newOrder() {
            frappe.route_options = {};
            return frappe.set_route("query-report", "نتائج التخصيم");
        }
        startLegacyPlan() {
            ++this.navigationSequence;
            ++this.previewSequence;
            this.mode = "plan"; this.order = null; this.editing = null; this.loading = false;
            this.previewReady = false;
            this.preparation = null;
            this.preparationError = "";
            this.groups = []; this.requests.clear(); this.selected.clear(); this.expanded.clear(); this.previewWarnings = [];
            this.root.querySelector('[data-filter="groups"]').value = "";
            this.filters.colors.clear(); this.filters.components.clear();
            this.notify(""); this.render();
            return this.search();
        }
        requestReason(action) {
            const titles = { "save-revision": "سبب تعديل أمر القص", correct: "تصحيح تسجيل قص لم يُنفذ فعليًا", recut: "سبب إعادة قص القطع المحددة", cancel: "إلغاء حجز كل القطع غير المقصوصة" };
            return new Promise(resolve => {
                let resolved = false;
                const dialog = new frappe.ui.Dialog({ title: titles[action], fields: [{ fieldname: "reason", fieldtype: "Small Text", label: "السبب", reqd: 1, description: "يُحفظ السبب مع اسم المستخدم والتاريخ في سجل الأمر." }], primary_action_label: action === "recut" ? "إنشاء إعادة قص" : "حفظ", primary_action: values => { resolved = true; dialog.hide(); resolve(values.reason.trim()); } });
                dialog.$wrapper.attr("dir", "rtl").css("text-align", "right");
                dialog.$wrapper.on("hidden.bs.modal", () => { if (!resolved) resolve(null); });
                dialog.show();
            });
        }
        mutationId(action, payload) {
            const signature = JSON.stringify([action, payload]);
            if (!this.mutationIds.has(signature)) this.mutationIds.set(signature, window.crypto && window.crypto.randomUUID ? window.crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`);
            return this.mutationIds.get(signature);
        }
        openPrintWindow() {
            const printWindow = window.open("", "_blank");
            if (!printWindow) { this.notify("اسمح بالنوافذ المنبثقة لطباعة الملصقات، ثم اضغط الطباعة مجددًا.", "error"); return null; }
            printWindow.opener = null;
            printWindow.document.write('<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8"><title>ملصقات القص</title><body style="font-family:sans-serif;padding:30px;text-align:right">جارٍ تجهيز ملصقات KDT…</body></html>');
            printWindow.document.close();
            return printWindow;
        }
        async printOrder(printWindow, pieceKeys) {
            try {
                const componentOrder = this.isComponentOrder();
                const result = await this.rpc(componentOrder ? "component_print_data" : "print_data", { name: this.order.name, revision: this.order.revision, [componentOrder ? "allocation_keys" : "piece_keys"]: pieceKeys || [] });
                const html = result.html || result.print_html;
                if (!html) throw new Error(componentOrder ? "لا توجد ملصقات KDT لقطع البنل المحددة. استخدم طباعة كشف المكونات." : "تعذر تجهيز ملصقات KDT لهذا الأمر. حدّث البيانات وأعد الطباعة.");
                if (!printWindow || printWindow.closed) { this.notify("الأمر محفوظ. أعد فتح الطباعة من زر طباعة الأمر."); return; }
                printWindow.document.open(); printWindow.document.write(html); printWindow.document.close();
                const printStatus = printWindow.document.createElement("div");
                printStatus.dir = "rtl";
                printStatus.style.cssText = "font:14px sans-serif;text-align:right;padding:12px;background:#f0f6f5;color:#164c49;";
                const printStyle = printWindow.document.createElement("style");
                printStyle.textContent = "@media print{#kcd-print-controls{display:none!important}}";
                printWindow.document.head.appendChild(printStyle);
                printStatus.id = "kcd-print-controls";
                const repeatButton = printWindow.document.createElement("button");
                repeatButton.type = "button";
                repeatButton.textContent = "طباعة الملصقات";
                repeatButton.style.cssText = "font:inherit;cursor:pointer;padding:7px 14px;margin-left:12px;";
                const label = printWindow.document.createElement("span");
                printStatus.append(repeatButton, label);
                printWindow.document.body.prepend(printStatus);
                const readyToPrint = async () => {
                    if (printWindow.closed) return;
                    repeatButton.disabled = true;
                    label.textContent = "جارٍ تحميل الباركود…";
                    try {
                        const images = Array.from(printWindow.document.images);
                        if (!images.length) throw new Error("لا توجد ملصقات قابلة للطباعة لهذه القطع.");
                        await Promise.all(images.map(img => new Promise((resolve, reject) => {
                            let timer;
                            const cleanup = () => { clearTimeout(timer); img.removeEventListener("load", loaded); img.removeEventListener("error", failed); };
                            const loaded = () => { cleanup(); img.naturalWidth > 0 ? resolve() : reject(new Error("تعذر تحميل صورة الباركود.")); };
                            const failed = () => { cleanup(); reject(new Error("تعذر تحميل صورة الباركود.")); };
                            if (img.complete) { loaded(); return; }
                            img.addEventListener("load", loaded, { once: true });
                            img.addEventListener("error", failed, { once: true });
                            timer = setTimeout(failed, 25000);
                        })));
                        if (printWindow.closed) return;
                        label.textContent = "الملصقات جاهزة. إعادة الطباعة لا تسجّل قصًا جديدًا.";
                        printWindow.focus();
                        printWindow.print();
                    } catch (error) { label.textContent = `${error.message} لم تبدأ الطباعة؛ أعد تحميل نافذة الطباعة من الأمر بعد التحقق من الاتصال.`; this.notify(label.textContent, "error"); }
                    finally { repeatButton.disabled = false; }
                };
                repeatButton.addEventListener("click", readyToPrint);
                await readyToPrint();
            } catch (error) { if (printWindow && !printWindow.closed) printWindow.close(); this.reportError(error); }
        }
        async printComponents(printWindow) {
            try {
                const result = await this.rpc("component_print_data", { name: this.order.name, revision: this.order.revision, allocation_keys: [] });
                const rows = sortComponentRows(result.component_rows);
                if (!rows.length) throw new Error("لا توجد مكونات في هذا الأمر للطباعة.");
                if (!printWindow || printWindow.closed) { this.notify("أعد فتح أمر القص من زر الطباعة."); return; }
                const body = rows.map(row => `<tr><td>${escapeHTML(componentLabel(row) || "غير محدد")}</td><td>${escapeHTML(row.color || "غير محدد")}</td><td dir="ltr">${escapeHTML(reportQuantity(row.quantity))}</td></tr>`).join("");
                printWindow.document.open();
                printWindow.document.write(`<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><title>أمر قص ${escapeHTML(this.order.name)}</title><style>@page{size:A4;margin:14mm}body{font:14px Arial,sans-serif;color:#172b39;text-align:right}h1{font-size:22px}table{width:100%;border-collapse:collapse;table-layout:fixed}th,td{border:1px solid #aebdc8;padding:9px;text-align:right;vertical-align:top;overflow-wrap:anywhere}th{background:#edf3f5}th:first-child{width:58%}tr{break-inside:avoid}thead{display:table-header-group}@media print{button{display:none}}</style></head><body><h1>أمر قص <bdi>${escapeHTML(this.order.name)}</bdi></h1><table><thead><tr><th>اسم المكوّن الكامل</th><th>اللون</th><th>الكمية</th></tr></thead><tbody>${body}</tbody></table></body></html>`);
                printWindow.document.close(); printWindow.focus(); printWindow.print();
            } catch (error) { if (printWindow && !printWindow.closed) printWindow.close(); this.reportError(error); }
        }
        async mutateComponents(action) {
            if (this.busy || this.loading) return;
            if (!["complete", "correct", "recut", "cancel"].includes(action)) { this.notify("عدّل المكونات من تقرير نتائج التخصيم باستخدام زر تعديل الأمر.", "error"); return; }
            this.setBusy(true);
            try {
                const selected = this.selectedParts();
                const payload = { name: this.order.name, revision: this.order.revision };
                if (["correct", "recut", "cancel"].includes(action)) { payload.reason = await this.requestReason(action); if (!payload.reason) return; }
                if (action !== "cancel") {
                    const state = action === "complete" ? "reserved" : "completed";
                    if (!selected.length || selected.some(source => source.state !== state)) throw new Error(state === "reserved" ? "حدّد مصادر المكونات المحجوزة التي تم قصها فقط." : "حدّد مصادر المكونات المقصوصة المطلوب تعديلها فقط.");
                    payload.allocation_keys = selected.map(allocationKey);
                }
                const apiAction = `component_${action}`;
                payload.request_id = this.mutationId(apiAction, payload);
                const result = await this.rpc(apiAction, payload);
                this.applyOrder(result);
                this.notify({ complete: "تم تسجيل القص لمصادر المكونات المحددة.", correct: "تم تصحيح تسجيل القص، وأصبحت المصادر محجوزة من جديد.", recut: "تم تسجيل محاولة إعادة قص مع حفظ التنفيذ السابق.", cancel: "تم إلغاء غير المقصوص مع الاحتفاظ بالمصادر المنفذة وسجل الأمر." }[action], "success");
            } catch (error) { this.reportError(error); }
            finally { this.setBusy(false); this.render(); }
        }
        async mutate(action, printWindow) {
            if (this.isComponentOrder()) return this.mutateComponents(action);
            if (this.busy || this.loading || this.mode === "plan" && !this.previewReady) return;
            // Lock before asking for a reason so two clicks cannot create parallel dialogs.
            this.setBusy(true);
            const selected = this.selectedParts();
            const sourceOrder = this.editing || this.order;
            const revision = sourceOrder && sourceOrder.revision;
            const name = sourceOrder && sourceOrder.name;
            let payload;
            let apiAction = { "approve-print": "approve", "save-revision": "revise" }[action] || action;
            try {
                let reason;
                if (["save-revision", "correct", "recut", "cancel"].includes(action)) { reason = await this.requestReason(action); if (!reason) return; }
                if (action === "approve-print" || action === "save-revision") {
                    if (action === "approve-print" && !selected.length) throw new Error("حدّد قطعًا متاحة قبل اعتماد الأمر.");
                    payload = { request_names: Array.from(this.requests.keys()), piece_keys: selected.map(partKey) };
                    if (action === "save-revision") {
                        // Completed snapshots remain in the desired order even if a source row was later changed or removed.
                        payload.piece_keys = Array.from(new Set(payload.piece_keys.concat((sourceOrder.items || []).filter(part => part.state === "completed").map(partKey))));
                        Object.assign(payload, { name, revision, reason });
                        if (!payload.piece_keys.length) apiAction = "cancel";
                    }
                } else if (action === "cancel") {
                    payload = { name, revision, reason };
                } else {
                    const requiredState = action === "complete" ? "reserved" : "completed";
                    if (!selected.length || selected.some(part => part.state !== requiredState)) throw new Error(requiredState === "reserved" ? "حدّد القطع المحجوزة التي تم قصها فقط." : "حدّد القطع المقصوصة المطلوب تعديلها فقط.");
                    payload = { name, revision, piece_keys: selected.map(partKey) };
                    if (reason) payload.reason = reason;
                }
                payload.request_id = this.mutationId(apiAction, payload);
                const result = await this.rpc(apiAction, payload);
                this.applyOrder(result);
                this.notify(({ "approve-print": "تم اعتماد أمر القص وحجز القطع. سجّل «تم القص» عند التنفيذ الفعلي.", "save-revision": "تم حفظ التعديل وتحديث حجز القطع.", complete: "تم تسجيل القص للقطع المحددة.", correct: "تم تصحيح التسجيل. القطع محجوزة في الأمر ولم تُسجّل مقصوصة.", recut: "تم تسجيل محاولة إعادة قص مع الاحتفاظ بتاريخ القص السابق.", cancel: "تم إلغاء حجز القطع غير المقصوصة مع الاحتفاظ بالمنفذ وسجل الأمر." })[action], "success");
                if (printWindow) await this.printOrder(printWindow, []);
            } catch (error) { if (printWindow && !printWindow.closed) printWindow.close(); this.reportError(error); }
            finally { this.setBusy(false); this.render(); }
        }

        async handleClick(event) {
            const button = event.target.closest("[data-action]");
            if (!button || !this.root.contains(button) || button.disabled || this.busy) return;
            const action = button.dataset.action;
            const index = number(button.dataset.index);
            if (action === "search") return this.search();
            if (action === "orders") return this.showOrders();
            if (action === "history") return this.toggleHistory();
            if (action === "close-orders") { this.slot("orders-panel").hidden = true; return; }
            if (action === "new") return this.newOrder();
            if (action === "legacy-plan") return this.startLegacyPlan();
            if (action === "add-request") { const request = this.searchResults[index]; if (!request || this.searchLoading) return; if (this.requests.size >= MAX_REQUESTS) { this.notify(`الحد ${MAX_REQUESTS} طلبًا في أمر القص. أزل طلبًا مضافًا قبل إضافة طلب آخر.`, "error"); return; } this.requests.set(request.name, request); this.renderRequests(); return this.loadPreview(true); }
            if (action === "add-results") {
                if (this.searchLoading) return;
                const additions = (this.searchResults || []).filter(request => !this.requests.has(request.name));
                const remaining = MAX_REQUESTS - this.requests.size;
                if (additions.length > remaining) { this.notify(`نتائج البحث غير المضافة ${additions.length} طلبًا، والمتاح ${remaining} من حد ${MAX_REQUESTS}. ضيّق البحث أو أضف الطلبات فرديًا. لم تتم إضافة أي طلب.`, "error"); return; }
                if (!additions.length) return;
                additions.forEach(request => this.requests.set(request.name, request));
                this.notify(`تمت إضافة ${additions.length} طلبًا من نتائج البحث المعروضة${this.searchTruncated ? "؛ توجد نتائج أخرى لم تُعرض، ضيّق البحث للوصول إليها" : ""}.`);
                this.renderRequests(); return this.loadPreview(true);
            }
            if (action === "remove-request") { this.requests.delete(Array.from(this.requests.keys())[index]); this.renderRequests(); return this.loadPreview(true); }
            if (action === "expand-component") { const row = this.componentRows()[index]; if (!row) return; const key = `component:${row.component_key}`; this.expanded.has(key) ? this.expanded.delete(key) : this.expanded.add(key); this.renderTable(); return; }
            if (action === "expand") { const group = this.groups[index]; if (!group) return; this.expanded.has(group.key) ? this.expanded.delete(group.key) : this.expanded.add(group.key); this.renderTable(); return; }
            if (action === "select-all" || action === "select-none") { if (action === "select-none") this.selected.clear(); else if (this.isComponentOrder()) this.visibleComponentRows().flatMap(({ row }) => this.componentSources(row)).filter(source => this.canSelect(source)).forEach(source => this.selected.add(allocationKey(source))); else this.visibleGroups().forEach(({ group }) => (group.parts || []).filter(part => this.canSelect(part)).forEach(part => this.selected.add(partKey(part)))); this.renderTable(); this.renderToolbar(); return; }
            if (action === "select-multi") {
                const name = button.dataset.filterName;
                if (!this.filters[name]) return;
                const menu = this.root.querySelector(`[data-multi="${name}"]`);
                const matches = Array.from(menu.querySelectorAll(".kcd-multi-option")).filter(option => !option.hidden).map(option => option.querySelector("input"));
                const previousSize = this.filters[name].size;
                matches.forEach(input => { this.filters[name].add(input.value); input.checked = true; });
                if (this.filters[name].size === previousSize) return;
                this.updateMultiSummary(name);
                if (name === "workflow_states") return this.search();
                this.renderFilteredView();
                return;
            }
            if (["clear-filters", "clear-multi", "remove-filter"].includes(action)) {
                const name = button.dataset.filterName;
                if (action === "clear-filters") { this.filters.colors.clear(); this.filters.components.clear(); this.root.querySelector('[data-filter="groups"]').value = ""; }
                else if (action === "clear-multi") this.filters[name].clear();
                else this.filters[name].delete(Array.from(this.filters[name])[index]);
                this.renderFilters(); this.renderFilteredView();
                if (name === "workflow_states") return this.search();
                return;
            }
            if (action === "open-order") return this.openOrder(this.recentOrders[index] && this.recentOrders[index].name);
            if (action === "part-order") { const group = this.groups[number(button.dataset.group)]; return this.openOrder(group && group.parts[index] && group.parts[index].order); }
            if (action === "refresh") return this.mode === "order" ? this.openOrder(this.order.name) : this.loadPreview(true);
            if (action === "edit") return this.beginEdit();
            if (action === "cancel-edit") return this.applyOrder(this.editing);
            if (action === "print-components") {
                const printWindow = this.openPrintWindow(); if (!printWindow) return;
                this.setBusy(true);
                try { await this.printComponents(printWindow); }
                finally { this.setBusy(false); this.renderToolbar(); }
                return;
            }
            if (action === "print") {
                const printWindow = this.openPrintWindow(); if (!printWindow) return;
                this.setBusy(true);
                try { await this.printOrder(printWindow, this.isComponentOrder() ? [] : this.selectedParts().map(part => this.selectionKey(part))); }
                finally { this.setBusy(false); this.renderToolbar(); }
                return;
            }
            if (action === "approve-print") { const printWindow = this.openPrintWindow(); if (printWindow) return this.mutate(action, printWindow); return; }
            if (["complete", "save-revision", "correct", "recut", "cancel"].includes(action)) return this.mutate(action);
        }
        handleChange(event) {
            const target = event.target;
            if (this.busy) return;
            if (target.dataset.multiFilter) {
                const name = target.dataset.multiFilter;
                if (!this.filters[name] || !(this.filterOptions[name] || []).includes(target.value)) return;
                target.checked ? this.filters[name].add(target.value) : this.filters[name].delete(target.value);
                this.updateMultiSummary(name);
                if (name === "workflow_states") { this.search(); return; }
                this.renderFilteredView();
            } else if (this.loading) return;
            else if (target.dataset.componentSelect !== undefined) {
                const row = this.componentRows()[number(target.dataset.componentSelect)];
                if (!row) return;
                this.componentSources(row).filter(source => this.canSelect(source)).forEach(source => target.checked ? this.selected.add(allocationKey(source)) : this.selected.delete(allocationKey(source)));
                this.renderTable(); this.renderToolbar();
            } else if (target.dataset.allocationKey !== undefined) {
                const source = this.allParts().find(item => allocationKey(item) === target.dataset.allocationKey);
                if (!source || !this.canSelect(source)) return;
                target.checked ? this.selected.add(allocationKey(source)) : this.selected.delete(allocationKey(source));
                this.renderTable(); this.renderToolbar();
            }
            else if (target.dataset.groupQuantity !== undefined) {
                const group = this.groups[number(target.dataset.groupQuantity)]; if (!group) return;
                const selectable = (group.parts || []).filter(part => this.canSelect(part));
                const quantity = Math.min(selectable.length, Math.max(0, Math.floor(number(target.value))));
                // Preserve explicitly selected pieces when increasing or decreasing a group quantity.
                const ordered = selectable.filter(part => this.selected.has(partKey(part))).concat(selectable.filter(part => !this.selected.has(partKey(part))));
                ordered.forEach((part, index) => index < quantity ? this.selected.add(partKey(part)) : this.selected.delete(partKey(part)));
                this.renderTable(); this.renderToolbar();
            } else if (target.dataset.pieceGroup !== undefined) {
                const group = this.groups[number(target.dataset.pieceGroup)];
                const part = group && group.parts[number(target.dataset.pieceIndex)];
                if (!part || !this.canSelect(part)) return;
                target.checked ? this.selected.add(partKey(part)) : this.selected.delete(partKey(part));
                this.renderTable(); this.renderToolbar();
            }
        }
    }

    function dismissOutsideFilters(event) {
        document.querySelectorAll(".kcd .kcd-multi[open]").forEach(menu => {
            if (!menu.contains(event.target)) menu.open = false;
        });
    }
    if (typeof document !== "undefined") {
        const previous = frappe.namar_cutting_desk && frappe.namar_cutting_desk.dismissOutsideFilters;
        if (previous) document.removeEventListener("click", previous, true);
        // Capture before filter actions redraw their menus. Native clicks also
        // cover touch taps and keyboard activation; outside controls still act.
        document.addEventListener("click", dismissOutsideFilters, true);
    }
    frappe.namar_cutting_desk = { Desk, escapeHTML, groupParts, eligible, dimensions, sortComponentRows, dismissOutsideFilters };
    frappe.ui.form.on("KDT Cutting Desk", {
        refresh(frm) {
            frm.disable_save();
            const field = frm.fields_dict.workspace_html;
            const root = field && (field.wrapper || field.$wrapper && field.$wrapper[0]);
            if (!root) return;
            if (!frm.__cutting_desk || frm.__cutting_desk.root !== root || !root.querySelector(".kcd")) frm.__cutting_desk = new Desk(frm, root);
            const routeOrder = frappe.route_options && frappe.route_options.cut_order;
            if (routeOrder !== undefined) {
                delete frappe.route_options.cut_order;
                frm.__cutting_desk.openRouteOrder(routeOrder);
            }
        }
    });
})();
