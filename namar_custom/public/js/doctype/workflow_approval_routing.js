/* My Followups routing changes visibility only; Workflow permissions stay native. */
(function () {
	"use strict";

	const TARGETS_FIELD = "custom_followups_routing_targets";
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
		return value || "افتح الصف لتحديد المستلم";
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
		controls[SUMMARY_FIELD].$wrapper.html(rtl(html));
		controls[EDIT_FIELD]?.$wrapper.attr("dir", "rtl").css("text-align", "right");
		controls[EDIT_FIELD]?.$input.prop("disabled", !can_edit(frm));
	}

	function target_change(frm) {
		this.doc.recipient_summary = recipient_label(this.doc, frm);
		const current_row = this.grid_row || this.layout?.grid_row;
		current_row?.refresh_dependency();
		current_row?.grid_form?.layout?.refresh(this.doc);
		current_row?.refresh_field(this.df.fieldname);
		current_row?.refresh_field("recipient_summary");
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
		if (!can_edit(frm)) return;
		if (!frm.doc.document_type) {
			frappe.throw(rtl("اختر نوع المستند أولًا لتحديد مستلمي الموافقة."));
		}
		await frappe.model.with_doctype(frm.doc.document_type);
		const row = locals[cdt]?.[cdn];
		if (!row) return;
		let targets;
		let invalid_config = false;
		try {
			targets = read_targets(row).map((target) => ({ ...target }));
		} catch (_error) {
			targets = [];
			invalid_config = true;
		}
		const fields = user_fields(frm);
		for (const target of targets) target.recipient_summary = recipient_label(target, frm);
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
		const explanation = "أضف سطرًا لكل موظف أو دور أو قاعدة، وافتح الصف لتحديد المستلم. " +
			"تظهر الموافقة لمن يطابق أي سطر ويملك صلاحية المرحلة. " +
			"ترك القائمة فارغة يعيد العرض حسب أدوار المرحلة. إذا تعذر تحديد جميع المستلمين المؤهلين يعود العرض للأدوار. " +
			"هذه الإعدادات تخص متابعاتي؛ صلاحيات الاعتماد داخل المستند لا تتغير.";
		const dialog = new frappe.ui.Dialog({
			title: __("مستلمو الموافقة") + (row.state ? ` — ${row.state}` : ""),
			size: "extra-large",
			fields: [
				{
					fieldname: "explanation", fieldtype: "HTML",
					options: rtl(explanation + (invalid_config
						? '<p class="text-danger">الإعداد السابق غير صالح. حدد المستلمين ثم احفظ سير العمل لتصحيحه.</p>' : "")),
				},
				{
					fieldname: "targets", fieldtype: "Table", label: __("المستلمون"),
					in_place_edit: false, data: targets, cannot_add_rows: false,
					description: rtl("افتح الصف لتحديد المستلم."),
					fields: [
						{
							fieldname: "type", fieldtype: "Select", label: __("نوع المستلم"),
							options: TYPES, default: "user", in_list_view: 1, columns: 4,
							formatter: (value) => esc(TYPES.find((type) => type.value === value)?.label || value),
							change: function () { target_change.call(this, frm); },
						},
						{
							fieldname: "recipient_summary", fieldtype: "Data", label: __("المستلم"),
							in_list_view: 1, columns: 6, read_only: 1,
							default: "افتح الصف لتحديد المستلم", formatter: (value) => esc(value),
						},
						{
							fieldname: "user", fieldtype: "Link", label: __("الموظف"), options: "User",
							in_list_view: 0, depends_on: 'eval:doc.type==="user"',
							get_query: () => ({ filters: { enabled: 1, user_type: "System User" } }),
							change: function () { target_change.call(this, frm); },
						},
						{
							fieldname: "role", fieldtype: "Link", label: __("الدور"), options: "Role",
							in_list_view: 0, depends_on: 'eval:doc.type==="role"',
							change: function () { target_change.call(this, frm); },
						},
						{
							fieldname: "field", fieldtype: "Select", label: __("حقل المسؤول"), options: field_options,
							in_list_view: 0, depends_on: 'eval:doc.type==="field"',
							change: function () { target_change.call(this, frm); },
						},
					],
				},
				{
					fieldname: "save_note", fieldtype: "HTML",
					options: rtl("بعد تطبيق الاختيارات، احفظ سير العمل لتفعيلها."),
				},
			],
			primary_action_label: __("تطبيق الاختيارات"),
			async primary_action() {
				const normalized = normalize_targets(dialog.fields_dict.targets.grid.get_data(), allowed_fields);
				await frappe.model.set_value(cdt, cdn, TARGETS_FIELD,
					JSON.stringify({ version: 1, targets: normalized }));
				render_summary(frm, cdt, cdn);
				dialog.hide();
			},
		});
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
		custom_followups_routing_edit: open_editor,
	});
})();
