/* My Followups routing changes visibility only; Workflow permissions stay native. */
(function () {
	"use strict";

	const TARGETS_FIELD = "custom_followups_routing_targets";
	const HIDE_FIELD = "custom_followups_hide_from_approvals";
	const SUMMARY_FIELD = "custom_followups_routing_summary";
	const EDIT_FIELD = "custom_followups_routing_edit";
	const MAX_TARGETS = 50;
	const TYPES = [
		{ value: "user", label: "موظف محدد" },
		{ value: "owner", label: "منشئ المستند" },
		{ value: "role", label: "دور معين" },
		{ value: "field", label: "موظف من حقل" },
	];
	const VALUE_FIELD = { user: "user", role: "role", field: "field" };
	const esc = (value) => frappe.utils.escape_html(String(value || ""));
	const rtl = (text) => `<div dir="rtl" style="text-align:right">${text}</div>`;

	function can_edit(frm) {
		return !frm.read_only && Boolean(frm.perm?.[0]?.[frm.is_new() ? "create" : "write"]);
	}

	function is_hidden(row) {
		return Boolean(Number(row?.[HIDE_FIELD] || 0));
	}

	function can_edit_recipients(frm, row) {
		return can_edit(frm) && Boolean(row) && !is_hidden(row);
	}

	function read_targets(row) {
		if (!row[TARGETS_FIELD]) return [];
		const parsed = JSON.parse(row[TARGETS_FIELD]);
		if (parsed.version !== 1 || !Array.isArray(parsed.targets)) {
			throw new Error("إعداد المستلمين غير صالح");
		}
		return parsed.targets;
	}

	function user_fields(frm) {
		if (!frm.doc.document_type) return [];
		return (frappe.get_meta(frm.doc.document_type)?.fields || []).filter(
			(field) => field.fieldtype === "Link" && field.options === "User" && field.fieldname
		);
	}

	function grid_row(frm, name) {
		return frm.fields_dict.states?.grid?.grid_rows_by_docname?.[name];
	}

	function recipient_label(target, frm) {
		if (target.type === "owner") return "منشئ المستند";
		let value = target[VALUE_FIELD[target.type]] || "";
		if (target.type === "user") value = frappe.user_info?.(value)?.fullname || value;
		if (target.type === "field") {
			const field = user_fields(frm).find((item) => item.fieldname === value);
			value = field ? __(field.label || field.fieldname, null, frm.doc.document_type) : value;
		}
		return value || "لم يُحدد المستلم";
	}

	function keep_editor_state_on_refresh(control, frm, cdt, cdn) {
		if (!control) return;
		control._namar_followups_context = { frm, cdt, cdn };
		control._namar_followups_apply_state = () => {
			const context = control._namar_followups_context;
			const current_row = locals[context.cdt]?.[context.cdn];
			const reason = is_hidden(current_row)
				? "ألغِ الإخفاء لتعديل المستلمين أو إظهار موافقات هذه المرحلة." : "";
			control.$input?.prop("disabled", !can_edit_recipients(context.frm, current_row)).attr("title", reason);
		};
		if (!control._namar_followups_native_refresh_input) {
			// ControlButton refresh enables its input again; restore this button's state afterwards.
			control._namar_followups_native_refresh_input = control.refresh_input;
			control.refresh_input = function (...args) {
				const result = this._namar_followups_native_refresh_input.apply(this, args);
				this._namar_followups_apply_state();
				return result;
			};
		}
		control._namar_followups_apply_state();
	}

	function render_summary(frm, cdt, cdn) {
		const row = locals[cdt]?.[cdn];
		const controls = grid_row(frm, cdn)?.grid_form?.fields_dict;
		if (!row || !controls?.[SUMMARY_FIELD]) return;
		let html;
		try {
			const targets = read_targets(row);
			const labels = targets.map((target) => {
				const type = TYPES.find((item) => item.value === target.type);
				if (!type) throw new Error("نوع المستلم غير معروف");
				if (target.type === "owner") return esc(type.label);
				return `${esc(type.label)}: ${esc(recipient_label(target, frm))}`;
			});
			html = labels.length ? labels.join("<br>") : "حسب أدوار المرحلة";
		} catch (_error) {
			html = '<span class="text-danger">راجع إعداد مستلمي الموافقة قبل حفظ سير العمل.</span>';
		}
		const hidden = is_hidden(row);
		const disabled_reason = "ألغِ الإخفاء لتعديل المستلمين أو إظهار موافقات هذه المرحلة.";
		if (hidden) {
			html = `<p><strong>مخفية من موافقات متابعاتي</strong><br>
				<span class="text-muted">اختيارات المستلمين محفوظة. ${disabled_reason}</span></p>${html}`;
		}
		// HTML controls re-render df.options later; keep literal user text out of the template parser.
		controls[SUMMARY_FIELD].set_value(rtl(html).replace(/[{}]/g, (char) => char === "{" ? "&#123;" : "&#125;"));
		if (controls[HIDE_FIELD]?.$wrapper) {
			controls[HIDE_FIELD].$wrapper.attr("dir", "rtl").css("text-align", "right");
		}
		controls[EDIT_FIELD]?.$wrapper.attr("dir", "rtl").css("text-align", "right");
		keep_editor_state_on_refresh(controls[EDIT_FIELD], frm, cdt, cdn);
	}

	function normalize_targets(rows, allowed_fields) {
		if (rows.length > MAX_TARGETS) {
			frappe.throw(rtl(`يمكن تحديد ${MAX_TARGETS} مستلمًا أو قاعدة كحد أقصى لكل مرحلة.`));
		}
		const seen = new Set();
		return rows.reduce((targets, row, index) => {
			if (!TYPES.some((type) => type.value === row.type)) {
				frappe.throw(rtl(`اختر نوع المستلم في الصف ${index + 1}.`));
			}
			const target = { type: row.type };
			const value_field = VALUE_FIELD[row.type];
			if (value_field) {
				const value = String(row[value_field] || "").trim();
				if (!value) frappe.throw(rtl(`أكمل بيانات المستلم في الصف ${index + 1}.`));
				if (row.type === "field" && !allowed_fields.has(value)) {
					frappe.throw(rtl(`اختر حقل مستخدم صالحًا في الصف ${index + 1}.`));
				}
				target[value_field] = value;
			}
			const key = JSON.stringify(target);
			if (!seen.has(key)) {
				seen.add(key);
				targets.push(target);
			}
			return targets;
		}, []);
	}

	async function open_editor(frm, cdt, cdn) {
		const row = locals[cdt]?.[cdn];
		if (!can_edit_recipients(frm, row)) return;
		if (!frm.doc.document_type) {
			frappe.throw(rtl("اختر نوع المستند أولًا لتحديد مستلمي الموافقة."));
		}
		await frappe.model.with_doctype(frm.doc.document_type);
		if (!can_edit_recipients(frm, row)) return;
		let targets;
		let invalid_config = false;
		try {
			targets = read_targets(row).map((target) => ({ ...target }));
		} catch (_error) {
			targets = [];
			invalid_config = true;
		}
		const fields = user_fields(frm);
		const allowed_fields = new Set(fields.map((field) => field.fieldname));
		const field_options = [
			{ value: "", label: "" },
			...fields.map((field) => ({
				value: field.fieldname,
				label: `${__(field.label || field.fieldname, null, frm.doc.document_type)} (${field.fieldname})`,
			})),
		];
		// Preserve a stale selection visibly so refreshing the control cannot change it silently.
		for (const target of targets) {
			if (target.type === "field" && target.field && !field_options.some((o) => o.value === target.field)) {
				field_options.push({ value: target.field, label: `حقل غير متاح (${target.field})` });
			}
		}
		const explanation = "أضف موظفًا أو دورًا أو قاعدة، ويمكنك الجمع بين أكثر من اختيار. " +
			"تظهر الموافقة لمن يطابق أي اختيار ويملك صلاحية المرحلة. " +
			"ترك القائمة فارغة يعيد العرض حسب أدوار المرحلة. إذا تعذر تحديد مستلم مؤهل تُسجّل مشكلة توجيه ولا يتوسع العرض إلى الأدوار تلقائيًا. " +
			"لإشراك دور مع المستلمين أضفه صراحةً إلى القائمة. " +
			"هذه الإعدادات تخص متابعاتي؛ صلاحيات الاعتماد داخل المستند لا تتغير.";

		function render_targets() {
			const rows = targets.map((target, index) => {
				const type_label = TYPES.find((type) => type.value === target.type)?.label || "اختيار غير صالح";
				return `<tr>
					<td style="vertical-align:middle">${esc(type_label)}</td>
					<td style="vertical-align:middle;overflow-wrap:anywhere">${esc(recipient_label(target, frm))}</td>
					<td style="white-space:nowrap;text-align:left">
						<button type="button" class="btn btn-default btn-xs" data-routing-action="edit" data-index="${index}">تعديل</button>
						<button type="button" class="btn btn-default btn-xs text-danger" data-routing-action="remove" data-index="${index}">إزالة</button>
					</td>
				</tr>`;
			}).join("");
			const html = rows ? `<div class="table-responsive"><table class="table table-bordered">
				<thead><tr><th style="text-align:right">نوع المستلم</th><th style="text-align:right">المستلم</th><th></th></tr></thead>
				<tbody>${rows}</tbody></table></div>`
				: '<p class="text-muted">حسب أدوار المرحلة. أضف مستلمًا لتخصيص ظهور الموافقة.</p>';
			dialog.fields_dict.targets_list.$wrapper.html(rtl(html));
		}

		async function edit_recipient(index = null) {
			if (!can_edit_recipients(frm, row)) return;
			if (index === null && targets.length >= MAX_TARGETS) {
				frappe.throw(rtl(`يمكن تحديد ${MAX_TARGETS} مستلمًا أو قاعدة كحد أقصى لكل مرحلة.`));
			}
			const original = index === null ? { type: "user" } : { ...targets[index] };
			const recipient_dialog = new frappe.ui.Dialog({
				title: index === null ? __("إضافة مستلم") : __("تعديل المستلم"),
				fields: [
					{
						fieldname: "type", fieldtype: "Select", label: __("نوع المستلم"), options: TYPES, reqd: 1,
						change: function () { this.layout?.refresh_dependency(); },
					},
					{
						fieldname: "user", fieldtype: "Link", label: __("الموظف"), options: "User",
						depends_on: 'eval:doc.type==="user"', mandatory_depends_on: 'eval:doc.type==="user"',
						get_query: () => ({ filters: { enabled: 1, user_type: "System User" } }),
					},
					{
						fieldname: "role", fieldtype: "Link", label: __("الدور"), options: "Role",
						depends_on: 'eval:doc.type==="role"', mandatory_depends_on: 'eval:doc.type==="role"',
					},
					{
						fieldname: "field", fieldtype: "Select", label: __("حقل المسؤول"), options: field_options,
						depends_on: 'eval:doc.type==="field"', mandatory_depends_on: 'eval:doc.type==="field"',
					},
				],
				primary_action_label: __("حفظ المستلم"),
				primary_action(values) {
					if (!can_edit_recipients(frm, row)) return;
					const [target] = normalize_targets([values], allowed_fields);
					if (index === null) targets.push(target);
					else targets[index] = target;
					render_targets();
					recipient_dialog.hide();
				},
			});
			// Explicit values avoid FieldGroup interpreting the string "user" as the session user.
			await recipient_dialog.set_values(original);
			recipient_dialog.refresh_dependency();
			recipient_dialog.$wrapper.attr("dir", "rtl");
			recipient_dialog.$wrapper.find(".modal-body, .modal-title, .control-label").css("text-align", "right");
			recipient_dialog.show();
		}

		const dialog = new frappe.ui.Dialog({
			title: __("مستلمو الموافقة") + (row.state ? ` — ${esc(row.state)}` : ""),
			size: "large",
			fields: [
				{
					fieldname: "explanation", fieldtype: "HTML",
					options: rtl(explanation + (invalid_config
						? '<p class="text-danger">الإعداد السابق غير صالح. حدد المستلمين ثم احفظ سير العمل لتصحيحه.</p>' : "")),
				},
				{ fieldname: "targets_list", fieldtype: "HTML" },
				{ fieldname: "add_target", fieldtype: "Button", label: __("إضافة مستلم"), click: () => edit_recipient() },
				{
					fieldname: "save_note", fieldtype: "HTML",
					options: rtl("بعد تطبيق الاختيارات، احفظ سير العمل لتفعيلها."),
				},
			],
			primary_action_label: __("تطبيق الاختيارات"),
			async primary_action() {
				if (!can_edit_recipients(frm, row)) return;
				const normalized = normalize_targets(targets, allowed_fields);
				await frappe.model.set_value(cdt, cdn, TARGETS_FIELD,
					JSON.stringify({ version: 1, targets: normalized }));
				render_summary(frm, cdt, cdn);
				dialog.hide();
			},
		});
		dialog.fields_dict.targets_list.$wrapper.on("click.namar-routing", "button[data-routing-action]", (event) => {
			if (!can_edit_recipients(frm, row)) return;
			const index = Number(event.currentTarget.dataset.index);
			if (!Number.isInteger(index) || index < 0 || index >= targets.length) return;
			if (event.currentTarget.dataset.routingAction === "edit") return edit_recipient(index);
			if (event.currentTarget.dataset.routingAction === "remove") {
				targets.splice(index, 1);
				render_targets();
			}
		});
		render_targets();
		dialog.$wrapper.attr("dir", "rtl");
		dialog.$wrapper.find(".modal-body, .modal-title, .control-label").css("text-align", "right");
		dialog.show();
	}

	async function refresh_summaries(frm) {
		if (frm.doc.document_type) await frappe.model.with_doctype(frm.doc.document_type);
		for (const row of frm.doc.states || []) render_summary(frm, row.doctype, row.name);
	}

	frappe.ui.form.on("Workflow", { refresh: refresh_summaries, document_type: refresh_summaries });
	frappe.ui.form.on("Workflow Document State", {
		form_render: render_summary,
		custom_followups_routing_targets: render_summary,
		custom_followups_hide_from_approvals: render_summary,
		custom_followups_routing_edit: open_editor,
	});
})();
