frappe.pages["my-followups"].on_page_load = function (wrapper) {
	wrapper.my_followups = new NamarMyFollowups(wrapper);
};

frappe.pages["my-followups"].on_page_show = function (wrapper) {
	wrapper.my_followups?.show();
};

class NamarMyFollowups {
	constructor(wrapper) {
		this.wrapper = wrapper;
		this.page = frappe.ui.make_app_page({
			parent: wrapper,
			title: __("متابعاتي"),
			single_column: true,
		});
		this.api = "namar_test.followups.api";
		this.state = {
			source: "followups",
			bucket: "all",
			search: "",
			priority: "",
			items: [],
			counts: {},
			total: null,
			has_more: false,
			next_start: null,
			limit_start: 0,
			page_length: 25,
			selected_name: null,
			detail: null,
			list_status: "idle",
			detail_status: "idle",
			loading_more: false,
			mobile_detail: false,
		};
		this.selected_by_source = { followups: null, approvals: null };
		this.list_sequence = 0;
		this.detail_sequence = 0;
		this.last_loaded_at = 0;
		this.search_timer = null;
		this.build();
		this.bind_events();
	}

	show() {
		if (this.state.list_status === "idle") {
			this.load_list();
			return;
		}

		if (Date.now() - this.last_loaded_at > 60 * 1000) {
			this.load_list({ preserve_selection: true });
		}
	}

	build() {
		$(this.wrapper).addClass("my-followups-page");
		this.page.main.empty().append(`
			<section class="mf-page" dir="rtl" aria-label="${this.escape(__("متابعاتي"))}">
				<header class="mf-page-intro">
					<div class="mf-page-copy">
						<p>${this.escape(__("أنجز المتابعة وسجّل النتيجة"))}</p>
					</div>
					<div class="mf-source-switch" role="tablist" aria-label="${this.escape(__("مصدر قائمة العمل"))}">
						<button type="button" class="mf-source-btn is-active" data-source="followups" role="tab" aria-selected="true">
							${this.icon("clipboard", "sm")}
							<span>${this.escape(__("المتابعات"))}</span>
						</button>
						<button type="button" class="mf-source-btn" data-source="approvals" role="tab" aria-selected="false">
							${this.icon("review", "sm")}
							<span>${this.escape(__("الموافقات"))}</span>
						</button>
					</div>
				</header>

				<div class="mf-workspace">
					<section class="mf-detail-panel" aria-label="${this.escape(__("تفاصيل عنصر العمل"))}">
						<div class="mf-detail-state" aria-live="polite"></div>
					</section>

					<aside class="mf-queue-panel" aria-label="${this.escape(__("قائمة العمل"))}">
						<div class="mf-queue-tools">
							<div class="mf-search-box" role="search">
								<span class="mf-search-icon">${this.icon("search", "sm")}</span>
								<label class="sr-only" for="mf-followups-search">${this.escape(__("البحث في قائمة العمل"))}</label>
								<input id="mf-followups-search" type="search" class="mf-search-input" placeholder="${this.escape(__("ابحث في المتابعات"))}" autocomplete="off" />
								<button type="button" class="mf-clear-search" aria-label="${this.escape(__("مسح البحث"))}" hidden>
									${this.icon("close", "xs")}
								</button>
							</div>
							<button type="button" class="mf-icon-button mf-filter-button" aria-label="${this.escape(__("تصفية حسب الأولوية"))}" title="${this.escape(__("تصفية حسب الأولوية"))}">
								${this.icon("filter", "sm")}
							</button>
						</div>
						<div class="mf-filter-bar" role="tablist" aria-label="${this.escape(__("تصفية المتابعات"))}"></div>
						<div class="mf-queue-list" role="listbox" aria-live="polite"></div>
						<div class="mf-pagination"></div>
					</aside>
				</div>
			</section>
		`);

		this.$root = this.page.main.find(".mf-page");
		this.$workspace = this.$root.find(".mf-workspace");
		this.$list = this.$root.find(".mf-queue-list");
		this.$detail = this.$root.find(".mf-detail-state");
		this.$filters = this.$root.find(".mf-filter-bar");
		this.$pagination = this.$root.find(".mf-pagination");
		this.$search = this.$root.find(".mf-search-input");
		this.$clear_search = this.$root.find(".mf-clear-search");
		this.render_filters();
		this.render_detail_empty();
	}

	bind_events() {
		this.$root.on("click", ".mf-source-btn", (event) => {
			this.change_source($(event.currentTarget).data("source"));
		});

		this.$root.on("click", ".mf-filter-btn", (event) => {
			this.change_bucket($(event.currentTarget).data("bucket"));
		});

		this.$root.on("keydown", ".mf-source-btn, .mf-filter-btn", (event) => {
			this.handle_tab_key(event);
		});

		this.$search.on("input", (event) => {
			const value = event.currentTarget.value.trim();
			this.$clear_search.prop("hidden", !value);
			window.clearTimeout(this.search_timer);
			this.search_timer = window.setTimeout(() => {
				this.state.search = value;
				this.load_list();
			}, 350);
		});

		this.$root.on("click", ".mf-clear-search", () => {
			window.clearTimeout(this.search_timer);
			this.$search.val("").trigger("focus");
			this.$clear_search.prop("hidden", true);
			this.state.search = "";
			this.load_list();
		});

		this.$root.on("click", ".mf-retry-list", () => {
			this.load_list({ preserve_selection: true });
		});
		this.$root.on("click", ".mf-filter-button", () => this.show_priority_filter());

		this.$root.on("click", ".mf-queue-item", (event) => {
			this.select_item($(event.currentTarget).data("name"), true);
		});

		this.$root.on("keydown", ".mf-queue-item", (event) => {
			if (event.key === "Enter" || event.key === " ") {
				event.preventDefault();
				this.select_item($(event.currentTarget).data("name"), true);
			}
		});

		this.$root.on("click", ".mf-load-more", () => this.load_more());
		this.$root.on("click", ".mf-retry-detail", () => this.load_detail(this.state.selected_name));
		this.$root.on("click", ".mf-mobile-back", () => this.show_mobile_queue());
		this.$root.on("click", ".mf-open-reference", () => this.open_reference());
		this.$root.on("click", ".mf-complete", () => this.complete_followup());
		this.$root.on("click", ".mf-complete-next", () => this.complete_and_schedule_next());
		this.$root.on("click", ".mf-reschedule", () => this.reschedule_followup());
		this.$root.on("click", ".mf-add-note", () => this.add_note());
		this.$root.on("click", ".mf-open-approval", () => this.open_approval());
	}

	change_source(source) {
		if (!['followups', 'approvals'].includes(source) || source === this.state.source) {
			return;
		}

		this.selected_by_source[this.state.source] = this.state.selected_name;
		this.state.source = source;
		this.state.bucket = "all";
		this.state.search = "";
		this.state.priority = "";
		this.state.selected_name = this.selected_by_source[source];
		this.state.detail = null;
		this.state.items = [];
		this.state.counts = {};
		this.state.total = null;
		this.state.has_more = false;
		this.state.next_start = null;
		this.state.limit_start = 0;
		this.detail_sequence += 1;
		this.$search.val("");
		this.$clear_search.prop("hidden", true);
		this.$root.find(".mf-filter-button")
			.prop("hidden", source === "approvals")
			.removeClass("is-active");
		this.$search.attr(
			"placeholder",
			source === "followups" ? __("ابحث في المتابعات") : __("ابحث في الموافقات")
		);
		this.$root.find(".mf-source-btn").each((_, button) => {
			const is_active = $(button).data("source") === source;
			$(button).toggleClass("is-active", is_active).attr("aria-selected", String(is_active));
		});
		this.show_mobile_queue();
		this.render_filters();
		this.render_detail_empty();
		this.load_list({ preserve_selection: true });
	}

	handle_tab_key(event) {
		if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
		const $tabs = $(event.currentTarget).closest('[role="tablist"]').find('[role="tab"]:visible');
		if (!$tabs.length) return;
		const current = $tabs.index(event.currentTarget);
		let next = current;
		if (event.key === "Home") next = 0;
		else if (event.key === "End") next = $tabs.length - 1;
		else if (event.key === "ArrowLeft") next = (current + 1) % $tabs.length;
		else next = (current - 1 + $tabs.length) % $tabs.length;
		event.preventDefault();
		$tabs.eq(next).trigger("focus").trigger("click");
	}

	show_priority_filter() {
		if (this.state.source !== "followups") return;
		const dialog = new frappe.ui.Dialog({
			title: __("تصفية المتابعات"),
			fields: [
				{
					fieldname: "priority",
					fieldtype: "Select",
					label: __("الأولوية"),
					options: [
						{ label: __("كل الأولويات"), value: "" },
						{ label: __("مرتفعة"), value: "High" },
						{ label: __("متوسطة"), value: "Medium" },
						{ label: __("منخفضة"), value: "Low" },
					],
					default: this.state.priority,
				},
			],
			primary_action_label: __("تطبيق"),
			primary_action: (values) => {
				this.state.priority = values.priority || "";
				this.$root.find(".mf-filter-button").toggleClass("is-active", Boolean(this.state.priority));
				dialog.hide();
				this.load_list();
			},
		});
		dialog.show();
	}

	change_bucket(bucket) {
		if (this.state.source !== "followups" || bucket === this.state.bucket) {
			return;
		}
		this.state.bucket = bucket;
		this.render_filters();
		this.load_list();
	}

	async load_list({ preserve_selection = false, append = false } = {}) {
		const sequence = ++this.list_sequence;
		const previous_selection = preserve_selection ? this.state.selected_name : null;
		const keep_mobile_detail = preserve_selection && this.state.mobile_detail;
		const limit_start = append ? this.state.next_start ?? this.state.items.length : 0;

		if (append) {
			this.state.loading_more = true;
			this.render_pagination();
		} else {
			if (!preserve_selection) {
				this.detail_sequence += 1;
				this.state.selected_name = null;
				this.state.detail = null;
				this.render_detail_empty();
			}
			this.state.list_status = "loading";
			this.state.limit_start = 0;
			this.render_list_loading();
		}

		try {
			const args = {
				search: this.state.search,
				limit_start,
				page_length: this.state.page_length,
			};
			const method = this.state.source === "followups" ? "get_followups" : "get_approvals";
			if (this.state.source === "followups") {
				args.bucket = this.state.bucket;
				args.priority = this.state.priority;
			}

			const response = await this.call(method, args);
			if (sequence !== this.list_sequence) return;

			const payload = this.normalize_list_response(response);
			this.state.items = append ? this.merge_items(this.state.items, payload.items) : payload.items;
			this.state.counts = payload.counts;
			this.state.total = payload.total;
			this.state.has_more = payload.has_more;
			this.state.next_start = payload.next_start;
			this.state.limit_start = this.state.items.length;
			this.state.list_status = "ready";
			this.state.loading_more = false;
			this.last_loaded_at = Date.now();
			this.render_filters();
			this.render_list();

			let selection = previous_selection;
			if (!selection || !this.state.items.some((item) => this.item_key(item) === selection)) {
				selection = this.state.items.length ? this.item_key(this.state.items[0]) : null;
			}

			if (selection) {
				this.select_item(selection, keep_mobile_detail);
			} else {
				this.state.selected_name = null;
				this.selected_by_source[this.state.source] = null;
				this.state.detail = null;
				this.render_detail_empty();
			}
		} catch (error) {
			if (sequence !== this.list_sequence) return;
			this.state.list_status = "error";
			this.state.loading_more = false;
			this.render_list_error();
			this.log_error("load_list", error);
		}
	}

	async load_more() {
		if (this.state.loading_more || !this.state.has_more) return;
		await this.load_list({ append: true, preserve_selection: true });
	}

	async select_item(name, mobile_detail) {
		if (!name) return;
		this.state.selected_name = name;
		this.selected_by_source[this.state.source] = name;
		this.state.mobile_detail = Boolean(mobile_detail);
		this.$workspace.toggleClass("is-mobile-detail", this.state.mobile_detail);
		this.$root.find(".mf-queue-item").each((_, item) => {
			const is_active = String($(item).data("name")) === String(name);
			$(item).toggleClass("is-active", is_active).attr("aria-selected", String(is_active));
		});
		await this.load_detail(name);
	}

	async load_detail(name) {
		const sequence = ++this.detail_sequence;
		this.state.detail_status = "loading";
		this.render_detail_loading();

		try {
			const method = this.state.source === "followups" ? "get_followup_detail" : "get_approval_detail";
			const key = this.state.source === "followups" ? "todo_name" : "action_name";
			const response = await this.call(method, { [key]: name });
			if (sequence !== this.detail_sequence || name !== this.state.selected_name) return;

			this.state.detail = this.normalize_detail_response(response);
			this.state.detail_status = "ready";
			this.render_detail();
		} catch (error) {
			if (sequence !== this.detail_sequence) return;
			this.state.detail_status = "error";
			this.render_detail_error();
			this.log_error("load_detail", error);
		}
	}

	render_filters() {
		if (this.state.source === "approvals") {
			const known_total = this.state.counts.all ?? this.state.total;
			const total = known_total === null || known_total === undefined
				? `${this.state.items.length}${this.state.has_more ? "+" : ""}`
				: this.number(known_total);
			this.$filters.addClass("is-approvals").html(`
				<button type="button" class="mf-filter-btn is-active" data-bucket="all" role="tab" aria-selected="true">
					<span>${this.escape(__("بانتظار مراجعتي"))}</span>
					<strong>${this.escape(total)}</strong>
				</button>
			`);
			return;
		}

		this.$filters.removeClass("is-approvals");
		const filters = [
			{ key: "all", label: __("الكل") },
			{ key: "overdue", label: __("متأخرة") },
			{ key: "today", label: __("اليوم") },
			{ key: "upcoming", label: __("قادمة") },
		];
		this.$filters.html(
			filters
				.map((filter) => {
					const active = filter.key === this.state.bucket;
					const count = this.count_for(filter.key);
					return `
						<button type="button" class="mf-filter-btn mf-filter-${filter.key} ${active ? "is-active" : ""}"
							data-bucket="${filter.key}" role="tab" aria-selected="${active}">
							<span>${this.escape(filter.label)}</span>
							<strong>${this.escape(count)}</strong>
						</button>
					`;
				})
				.join("")
		);
	}

	render_list_loading() {
		this.$list.attr("aria-busy", "true").html(
			Array.from({ length: 5 }, () => `
				<div class="mf-queue-skeleton" aria-hidden="true">
					<span class="mf-skeleton-icon"></span>
					<span class="mf-skeleton-copy">
						<span class="mf-skeleton-line is-title"></span>
						<span class="mf-skeleton-line"></span>
						<span class="mf-skeleton-line is-short"></span>
					</span>
				</div>
			`).join("")
		);
		this.$pagination.empty();
	}

	render_list() {
		this.$list.attr("aria-busy", "false");
		if (!this.state.items.length) {
			const is_search = Boolean(this.state.search);
			this.$list.html(this.state_markup({
				icon: "search",
				title: is_search ? __("لا توجد نتائج مطابقة") : __("قائمة العمل فارغة"),
				message: is_search
					? __("جرّب عبارة بحث أخرى أو امسح البحث.")
					: this.state.source === "followups"
						? __("لا توجد متابعات ضمن هذا التصنيف حاليًا.")
						: __("لا توجد موافقات بانتظار مراجعتك حاليًا."),
			}));
			this.$pagination.empty();
			return;
		}

		this.$list.html(this.state.items.map((item) => this.render_queue_item(item)).join(""));
		this.render_pagination();
	}

	render_queue_item(item) {
		const name = this.item_key(item);
		const title = this.item_title(item);
		const reference_name = this.first(item.reference_name, item.ref_name, item.document_name);
		const party = this.first(
			item.reference_title,
			item.party_name,
			item.supplier_name,
			item.customer_name,
			item.document_title
		);
		const display_party = party && party !== reference_name ? party : "";
		const requester = this.first(
			item.assigned_by_full_name,
			item.requested_by_name,
			item.owner_name,
			item.assigned_by,
			item.requested_by,
			item.user
		);
		const role = this.first(
			item.role,
			item.allocated_to_role,
			item.owner_role,
			item.department,
			item.allocated_to_full_name,
			item.assignee_name,
			this.user_display(item.allocated_to)
		);
		const due = this.due_meta(item);
		const is_active = String(name) === String(this.state.selected_name);
		const type_icon = this.state.source === "approvals" ? "review" : this.reference_icon(item);
		const requester_label = this.state.source === "approvals" ? __("مخصص إلى:") : __("طلب بواسطة:");

		return `
			<article class="mf-queue-item ${is_active ? "is-active" : ""}" data-name="${this.escape_attr(name)}"
				role="option" tabindex="0" aria-selected="${is_active}">
				<div class="mf-item-type-icon" aria-hidden="true">${this.icon(type_icon, "md")}</div>
				<div class="mf-item-copy">
					<h3>${this.escape(title)}</h3>
					${reference_name || display_party ? `<p class="mf-reference-line">${this.join_meta([reference_name, display_party])}</p>` : ""}
					${due.label ? `<p class="mf-due-line is-${due.tone}"><span class="mf-due-dot" aria-hidden="true"></span>${this.escape(due.label)}</p>` : ""}
					${role ? `<p class="mf-role-line">${this.escape(role)}</p>` : ""}
					${requester ? `
						<div class="mf-requester">
							${this.avatar(item, requester)}
							<span>${this.escape(requester_label)} ${this.escape(requester)}</span>
						</div>
					` : ""}
				</div>
			</article>
		`;
	}

	render_pagination() {
		if (!this.state.items.length || !this.state.has_more) {
			this.$pagination.empty();
			return;
		}
		const progress = this.state.total === null || this.state.total === undefined
			? __("تم تحميل {0}", [this.state.items.length])
			: `${this.state.items.length} / ${this.state.total}`;
		this.$pagination.html(`
			<button type="button" class="mf-load-more" ${this.state.loading_more ? "disabled" : ""}>
				${this.state.loading_more ? this.icon("refresh", "xs") : ""}
				<span>${this.escape(this.state.loading_more ? __("جارٍ التحميل...") : __("تحميل المزيد"))}</span>
				<small>${this.escape(progress)}</small>
			</button>
		`);
	}

	render_list_error() {
		this.$list.attr("aria-busy", "false").html(this.state_markup({
			icon: "solid-warning",
			title: __("تعذّر تحميل قائمة العمل"),
			message: __("تحقق من الاتصال ثم حاول مرة أخرى."),
			action_class: "mf-retry-list",
			action_label: __("إعادة المحاولة"),
		}));
		this.$pagination.empty();
	}

	render_detail_loading() {
		this.$detail.attr("aria-busy", "true").html(`
			<div class="mf-detail-loading" aria-label="${this.escape(__("جارٍ تحميل التفاصيل"))}">
				<div class="mf-detail-skeleton-head"></div>
				<div class="mf-detail-skeleton-body">
					<span class="mf-skeleton-line is-title"></span>
					<span class="mf-skeleton-line"></span>
					<span class="mf-skeleton-block"></span>
					<span class="mf-skeleton-line is-title"></span>
					<span class="mf-skeleton-block is-tall"></span>
				</div>
			</div>
		`);
	}

	render_detail() {
		this.$detail.attr("aria-busy", "false");
		if (this.state.source === "approvals") {
			this.render_approval_detail();
			return;
		}
		this.render_followup_detail();
	}

	render_followup_detail() {
		const detail = this.state.detail || {};
		const title = this.item_title(detail);
		const due = this.due_meta(detail);
		const reference_type = this.reference_type(detail);
		const reference_name = this.reference_name(detail);
		const reference_title = this.first(
			detail.reference_title,
			detail.party_name,
			detail.supplier_name,
			detail.customer_name
		);
		const type = this.first(
			detail.followup_type,
			detail.type,
			detail.activity_type,
			this.priority_label(detail.priority)
		);
		const role = this.first(detail.role, detail.allocated_to_role, detail.department);
		const assigned_name = this.first(
			detail.allocated_to_full_name,
			detail.assignee_name,
			this.user_display(detail.allocated_to)
		);
		const requested = this.first(
			detail.required_action,
			detail.requested_action,
			detail.description,
			detail.summary
		);
		const show_requested = requested && this.plain_text(requested) !== this.plain_text(title);
		const status = this.first(detail.work_state_label, detail.status_label, detail.status, __("مفتوحة"));
		const timeline = this.first_array(detail.timeline, detail.activities, detail.activity);
		const permissions = detail.permissions || {};
		const can_complete = permissions.can_complete !== false;
		const can_reschedule = permissions.can_reschedule !== false;
		const can_add_note = permissions.can_add_note !== false;
		const can_schedule_next = can_complete && detail.has_linked_reference !== false;
		const display_reference_title = reference_title && reference_title !== reference_name
			? reference_title
			: "";

		this.$detail.html(`
			<div class="mf-detail-layout">
				<header class="mf-detail-reference-bar">
					<button type="button" class="mf-mobile-back" aria-label="${this.escape(__("العودة إلى القائمة"))}">
						${this.icon("arrow-right", "sm")}
						<span>${this.escape(__("القائمة"))}</span>
					</button>
					<div class="mf-reference-summary">
						<span class="mf-status-pill is-${due.tone}">${this.escape(due.is_overdue ? __("متأخرة") : status)}</span>
						${display_reference_title ? `<span>${this.escape(display_reference_title)}</span>` : ""}
						${display_reference_title && reference_name ? `<span class="mf-reference-divider" aria-hidden="true">·</span>` : ""}
						${reference_name ? `<strong>${this.escape(reference_name)}</strong>` : ""}
					</div>
					${reference_type && reference_name ? `
						<button type="button" class="mf-link-button mf-open-reference">
							${this.icon("external-link", "sm")}
							<span>${this.escape(__("فتح المستند"))}</span>
						</button>
					` : ""}
				</header>

				<div class="mf-detail-scroll">
					<section class="mf-detail-hero">
						<h2>${this.escape(title)}</h2>
						<div class="mf-detail-meta">
							${type ? `<span>${this.icon("clipboard", "xs")}${this.escape(type)}</span>` : ""}
							${role || assigned_name ? `<span>${this.icon("assign", "xs")}${this.escape(role || assigned_name)}</span>` : ""}
							${due.date ? `<span>${this.icon("calendar", "xs")}${this.escape(__("الاستحقاق"))} ${this.escape(this.format_date(due.date))}</span>` : ""}
						</div>
					</section>

					${show_requested ? `
						<section class="mf-section">
							<h3>${this.escape(__("المطلوب"))}</h3>
							<div class="mf-required-box">${this.escape(this.plain_text(requested))}</div>
						</section>
					` : ""}

					<section class="mf-section mf-timeline-section">
						<h3>${this.escape(__("سجل المتابعة"))}</h3>
						${this.render_timeline(timeline, {
							include_current: detail.status === "Open",
							current_label: this.first(detail.current_step, detail.work_state_label, __("بانتظار الإجراء")),
						})}
					</section>

					<section class="mf-section mf-result-section">
						<div class="mf-section-heading">
							<h3>${this.escape(__("نتيجة الإجراء"))}</h3>
							${can_add_note ? `<button type="button" class="mf-note-button mf-add-note">
								${this.icon("comment", "xs")}
								<span>${this.escape(__("إضافة ملاحظة"))}</span>
							</button>` : ""}
						</div>
						<label class="sr-only" for="mf-followup-result">${this.escape(__("نتيجة الإجراء"))}</label>
						<textarea id="mf-followup-result" class="mf-result-input" rows="4"
							placeholder="${this.escape(__("اكتب ما تم أو سبب التأجيل..."))}"></textarea>
					</section>
				</div>

				<footer class="mf-detail-actions">
					<button type="button" class="mf-action-btn is-secondary mf-reschedule" ${can_reschedule ? "" : "disabled"}>
						${this.icon("calendar", "sm")}
						<span>${this.escape(__("إعادة الجدولة"))}</span>
					</button>
					<button type="button" class="mf-action-btn is-secondary mf-complete" ${can_complete ? "" : "disabled"}>
						${this.icon("tick", "sm")}
						<span>${this.escape(__("تم"))}</span>
					</button>
					<button type="button" class="mf-action-btn is-primary mf-complete-next" ${can_schedule_next ? "" : "disabled"}
						title="${this.escape_attr(can_schedule_next ? __("إنجاز وجدولة متابعة تالية") : __("تتطلب المتابعة التالية مستندًا مرجعيًا"))}">
						${this.icon("calendar", "sm")}
						<span>${this.escape(__("تم وجدول التالية"))}</span>
					</button>
				</footer>
			</div>
		`);
	}

	render_approval_detail() {
		const detail = this.state.detail || {};
		const title = this.item_title(detail);
		const reference_type = this.reference_type(detail);
		const reference_name = this.reference_name(detail);
		const reference_title = this.first(detail.reference_title, detail.document_title, detail.party_name);
		const display_reference_title = reference_title && reference_title !== reference_name
			? reference_title
			: "";
		const approver = this.first(detail.user_name, this.user_display(detail.user));
		const summary = this.first(detail.summary, detail.description, detail.message, detail.workflow_state);
		const timeline = this.first_array(detail.timeline, detail.activities, detail.activity);

		this.$detail.html(`
			<div class="mf-detail-layout is-approval">
				<header class="mf-detail-reference-bar">
					<button type="button" class="mf-mobile-back" aria-label="${this.escape(__("العودة إلى القائمة"))}">
						${this.icon("arrow-right", "sm")}
						<span>${this.escape(__("القائمة"))}</span>
					</button>
					<div class="mf-reference-summary">
						<span class="mf-status-pill is-review">${this.escape(__("بانتظار المراجعة"))}</span>
						${display_reference_title ? `<span>${this.escape(display_reference_title)}</span>` : ""}
						${display_reference_title && reference_name ? `<span class="mf-reference-divider" aria-hidden="true">·</span>` : ""}
						${reference_name ? `<strong>${this.escape(reference_name)}</strong>` : ""}
					</div>
				</header>

				<div class="mf-detail-scroll">
					<section class="mf-detail-hero">
						<h2>${this.escape(title)}</h2>
						<div class="mf-detail-meta">
							<span>${this.icon("review", "xs")}${this.escape(__("موافقة مطلوبة"))}</span>
							${approver ? `<span>${this.icon("assign", "xs")}${this.escape(__("مخصص إلى"))} ${this.escape(approver)}</span>` : ""}
							${detail.creation ? `<span>${this.icon("calendar", "xs")}${this.escape(this.format_datetime(detail.creation))}</span>` : ""}
						</div>
					</section>

					<section class="mf-section">
						<h3>${this.escape(__("المطلوب للمراجعة"))}</h3>
						<div class="mf-required-box">${this.escape(this.plain_text(summary || __("افتح المستند لمراجعة تفاصيل الموافقة.")))}</div>
					</section>

					${timeline.length ? `
						<section class="mf-section mf-timeline-section">
							<h3>${this.escape(__("سجل المستند"))}</h3>
							${this.render_timeline(timeline)}
						</section>
					` : ""}
				</div>

				<footer class="mf-detail-actions is-approval">
					<button type="button" class="mf-action-btn is-primary mf-open-approval"
						${reference_type && reference_name ? "" : `data-action-name="${this.escape_attr(this.state.selected_name)}"`}>
						${this.icon("review", "sm")}
						<span>${this.escape(__("فتح للمراجعة"))}</span>
					</button>
				</footer>
			</div>
		`);
	}

	render_timeline(items, { include_current = false, current_label = "" } = {}) {
		const entries = [...items];
		if (include_current) {
			entries.push({ label: current_label || __("بانتظار الإجراء"), is_current: true });
		}
		if (!entries.length) {
			return `<div class="mf-inline-empty">${this.escape(__("لا يوجد سجل متابعة بعد."))}</div>`;
		}

		return `<ol class="mf-timeline">
			${entries.map((entry) => {
				const label = this.first(entry.label, entry.title, entry.description, entry.content, __("تحديث"));
				const date = this.first(entry.date, entry.creation, entry.timestamp, entry.modified);
				const is_complete = Boolean(
					entry.completed || entry.is_completed || entry.status === "Closed" || entry.comment_type === "Assignment"
				);
				const is_current = Boolean(entry.is_current);
				const icon = is_complete ? "tick" : is_current ? "primitive-dot" : "comment";
				return `
					<li class="mf-timeline-item ${is_complete ? "is-complete" : ""} ${is_current ? "is-current" : ""}">
						<span class="mf-timeline-icon">${this.icon(icon, "xs")}</span>
						<div class="mf-timeline-copy">
							<p>${this.escape(this.plain_text(label))}</p>
							${date ? `<time>${this.escape(this.format_timeline_date(date))}</time>` : ""}
						</div>
					</li>
				`;
			}).join("")}
		</ol>`;
	}

	render_detail_empty() {
		const approvals = this.state.source === "approvals";
		this.$detail.attr("aria-busy", "false").html(this.state_markup({
			icon: approvals ? "review" : "clipboard",
			title: approvals ? __("اختر موافقة لمراجعتها") : __("اختر متابعة من القائمة"),
			message: approvals
				? __("ستظهر هنا بيانات المستند المطلوب مراجعته.")
				: __("ستظهر هنا التفاصيل وسجل المتابعة وإجراءات الإنجاز."),
		}));
	}

	render_detail_error() {
		this.$detail.attr("aria-busy", "false").html(this.state_markup({
			icon: "solid-warning",
			title: __("تعذّر تحميل التفاصيل"),
			message: __("حاول فتح العنصر مرة أخرى."),
			action_class: "mf-retry-detail",
			action_label: __("إعادة المحاولة"),
			show_mobile_back: true,
		}));
	}

	state_markup({ icon, title, message, action_class, action_label, show_mobile_back = false }) {
		return `
			<div class="mf-state-card">
				${show_mobile_back ? `
					<button type="button" class="mf-mobile-back mf-state-back">
						${this.icon("arrow-right", "sm")}
						<span>${this.escape(__("القائمة"))}</span>
					</button>
				` : ""}
				<span class="mf-state-icon">${this.icon(icon, "lg")}</span>
				<h3>${this.escape(title)}</h3>
				<p>${this.escape(message)}</p>
				${action_class ? `<button type="button" class="mf-state-action ${action_class}">${this.escape(action_label)}</button>` : ""}
			</div>
		`;
	}

	async complete_followup() {
		const result = this.get_result();
		if (!result) return;
		await this.run_action({
			method: "complete_followup",
			args: { todo_name: this.state.selected_name, result },
			success_message: __("تم إنجاز المتابعة"),
		});
	}

	complete_and_schedule_next() {
		const result = this.get_result();
		if (!result) return;

		const todo_name = this.state.selected_name;
		const detail = this.state.detail || {};
		const dialog = new frappe.ui.Dialog({
			title: __("جدولة المتابعة التالية"),
			fields: [
				{
					fieldname: "next_date",
					fieldtype: "Date",
					label: __("تاريخ المتابعة التالية"),
					reqd: 1,
					default: frappe.datetime.add_days(frappe.datetime.now_date(), 1),
				},
				{
					fieldname: "description",
					fieldtype: "Small Text",
					label: __("المطلوب في المتابعة التالية"),
					default: this.plain_text(this.first(detail.next_description, detail.description, "")),
				},
				{
					fieldname: "priority",
					fieldtype: "Select",
					label: __("الأولوية"),
					options: ["High", "Medium", "Low"],
					default: this.first(detail.priority, "Medium"),
				},
			],
			primary_action_label: __("إنجاز وجدولة التالية"),
			primary_action: async (values) => {
				dialog.disable_primary_action();
				const success = await this.run_action({
					method: "complete_and_schedule_next",
					args: {
						todo_name,
						result,
						next_date: values.next_date,
						description: values.description || null,
						priority: values.priority || null,
					},
					success_message: __("تم الإنجاز وجدولة المتابعة التالية"),
				});
				if (success) dialog.hide();
				else dialog.enable_primary_action();
			},
		});
		dialog.show();
	}

	reschedule_followup() {
		const todo_name = this.state.selected_name;
		const due = this.due_meta(this.state.detail || {});
		const dialog = new frappe.ui.Dialog({
			title: __("إعادة جدولة المتابعة"),
			fields: [
				{
					fieldname: "new_date",
					fieldtype: "Date",
					label: __("التاريخ الجديد"),
					reqd: 1,
					default: due.date || frappe.datetime.now_date(),
				},
			],
			primary_action_label: __("حفظ الموعد"),
			primary_action: async (values) => {
				dialog.disable_primary_action();
				const success = await this.run_action({
					method: "reschedule_followup",
					args: { todo_name, new_date: values.new_date },
					success_message: __("تم تحديث موعد المتابعة"),
					preserve_selection: true,
				});
				if (success) dialog.hide();
				else dialog.enable_primary_action();
			},
		});
		dialog.show();
	}

	add_note() {
		const todo_name = this.state.selected_name;
		const dialog = new frappe.ui.Dialog({
			title: __("إضافة ملاحظة"),
			fields: [
				{
					fieldname: "note",
					fieldtype: "Small Text",
					label: __("الملاحظة"),
					reqd: 1,
				},
			],
			primary_action_label: __("إضافة"),
			primary_action: async (values) => {
				dialog.disable_primary_action();
				try {
					await this.call("add_followup_note", {
						todo_name,
						note: values.note,
					});
					dialog.hide();
					frappe.show_alert({ message: __("تمت إضافة الملاحظة"), indicator: "green" });
					if (this.state.selected_name === todo_name) {
						await this.load_detail(todo_name);
					}
				} catch (error) {
					dialog.enable_primary_action();
					this.show_action_error(error);
				}
			},
		});
		dialog.show();
	}

	async run_action({ method, args, success_message, preserve_selection = false }) {
		this.set_action_busy(true);
		try {
			await this.call(method, args);
			frappe.show_alert({ message: success_message, indicator: "green" });
			if (!preserve_selection) {
				this.state.selected_name = null;
				this.selected_by_source.followups = null;
			}
			await this.load_list({ preserve_selection });
			return true;
		} catch (error) {
			this.show_action_error(error);
			return false;
		} finally {
			this.set_action_busy(false);
		}
	}

	set_action_busy(busy) {
		if (busy) {
			this.$detail.find("button:not(:disabled), textarea:not(:disabled)")
				.attr("data-mf-busy-disabled", "1")
				.prop("disabled", true);
		} else {
			this.$detail.find('[data-mf-busy-disabled="1"]')
				.prop("disabled", false)
				.removeAttr("data-mf-busy-disabled");
		}
		this.$detail.toggleClass("is-action-busy", busy);
	}

	get_result() {
		const $input = this.$detail.find(".mf-result-input");
		const value = ($input.val() || "").trim();
		if (!value) {
			frappe.show_alert({ message: __("اكتب نتيجة الإجراء أولًا"), indicator: "orange" });
			$input.trigger("focus");
			return null;
		}
		return value;
	}

	open_reference() {
		const detail = this.state.detail || {};
		const doctype = this.reference_type(detail);
		const name = this.reference_name(detail);
		if (doctype && name) frappe.set_route("Form", doctype, name);
	}

	open_approval() {
		const detail = this.state.detail || {};
		const doctype = this.reference_type(detail);
		const name = this.reference_name(detail);
		if (doctype && name) {
			frappe.set_route("Form", doctype, name);
			return;
		}
		if (this.state.selected_name) {
			frappe.set_route("Form", "Workflow Action", this.state.selected_name);
		}
	}

	show_mobile_queue() {
		this.state.mobile_detail = false;
		this.$workspace.removeClass("is-mobile-detail");
	}

	async call(method, args) {
		const response = await frappe.call({
			method: `${this.api}.${method}`,
			args,
		});
		return response?.message ?? response ?? {};
	}

	normalize_list_response(response) {
		if (Array.isArray(response)) {
			return {
				items: response,
				counts: { all: response.length },
				total: response.length,
				has_more: false,
				next_start: null,
			};
		}
		const payload = response || {};
		const items = Array.isArray(payload.items) ? payload.items : [];
		const counts = payload.counts && typeof payload.counts === "object" ? payload.counts : {};
		const bucket_total = this.state.source === "followups" ? counts[this.state.bucket] : counts.all;
		const total_value = payload.total ?? bucket_total;
		const total = total_value === undefined || total_value === null ? null : this.number(total_value);
		const has_more = Boolean(payload.has_more ?? (total !== null && items.length < total));
		const next_start = payload.next_start ?? (has_more ? this.state.limit_start + items.length : null);
		return { items, counts, total, has_more, next_start };
	}

	normalize_detail_response(response) {
		const payload = response || {};
		const record = this.state.source === "approvals"
			? payload.approval || payload
			: payload.followup || payload;
		const reference = payload.reference || {};
		const timeline = this.first_array(payload.timeline, record.timeline);

		return {
			...record,
			has_linked_reference: Boolean(
				record.reference_type && record.reference_name
			),
			reference_type: this.first(
				record.reference_type,
				record.reference_doctype,
				reference.doctype
			),
			reference_name: this.first(record.reference_name, reference.name),
			reference_title: this.first(reference.title, record.reference_title),
			reference_route: this.first(reference.route, record.reference_route),
			reference_status: this.first(reference.workflow_state, reference.status),
			timeline: [...timeline].reverse(),
			permissions: payload.permissions || record.permissions || {},
			available_actions: payload.available_actions || record.available_actions || [],
			permitted_roles: payload.permitted_roles || record.permitted_roles || [],
		};
	}

	merge_items(current, incoming) {
		const merged = new Map(current.map((item) => [this.item_key(item), item]));
		incoming.forEach((item) => merged.set(this.item_key(item), item));
		return [...merged.values()];
	}

	item_key(item) {
		return String(
			this.state.source === "approvals"
				? this.first(item.action_name, item.name, item.workflow_action)
				: this.first(item.todo_name, item.name)
		);
	}

	item_title(item) {
		return this.plain_text(
			this.first(
				item.title,
				item.subject,
				item.followup_title,
				item.action_title,
				item.description,
				item.workflow_state,
				this.state.source === "approvals" ? __("موافقة مطلوبة") : __("متابعة")
			)
		);
	}

	reference_type(item) {
		return this.first(item.reference_type, item.reference_doctype, item.ref_doctype, item.document_type);
	}

	reference_name(item) {
		return this.first(item.reference_name, item.ref_name, item.document_name);
	}

	reference_icon(item) {
		const activity = String(this.first(
			item.followup_type,
			item.activity_type,
			item.type,
			item.title,
			item.description
		) || "").toLowerCase();
		if (["call", "phone", "contact", "اتصال", "تواصل", "هاتف"].some((word) => activity.includes(word))) {
			return "call";
		}
		const doctype = String(this.reference_type(item) || "").toLowerCase();
		if (doctype.includes("material request")) return "clipboard";
		if (doctype.includes("sales order")) return "tick";
		return "file";
	}

	due_meta(item) {
		const date = this.first(item.due_date, item.date, item.next_date);
		const provided_bucket = String(this.first(item.bucket, item.due_bucket, "")).toLowerCase();
		let tone = "neutral";
		let label = this.first(item.due_label, item.relative_due, item.due_text);
		let is_overdue = provided_bucket === "overdue" || Boolean(item.is_overdue);

		if (provided_bucket === "today") tone = "today";
		else if (provided_bucket === "upcoming") tone = "upcoming";
		else if (is_overdue) tone = "overdue";

		if (date) {
			const today = frappe.datetime.now_date();
			const difference = frappe.datetime.get_diff(date, today);
			if (difference < 0) {
				tone = "overdue";
				is_overdue = true;
				if (!label) {
					const days = Math.abs(difference);
					label = days === 1 ? __("متأخرة بيوم") : __("متأخرة بـ {0} أيام", [days]);
				}
			} else if (difference === 0) {
				tone = "today";
				if (!label) label = item.due_time ? __("اليوم {0}", [item.due_time]) : __("اليوم");
			} else {
				tone = "upcoming";
				if (!label) label = difference === 1 ? __("غدًا") : this.format_date(date);
			}
		}

		return { date, label, tone, is_overdue };
	}

	count_for(bucket) {
		const aliases = {
			all: ["all", "total", "open"],
			overdue: ["overdue", "late"],
			today: ["today", "due_today"],
			upcoming: ["upcoming", "future"],
		};
		for (const key of aliases[bucket] || [bucket]) {
			if (this.state.counts[key] !== undefined) return this.number(this.state.counts[key]);
		}
		return bucket === "all" ? this.number(this.state.total) : 0;
	}

	format_date(value) {
		if (!value) return "";
		const parsed = this.parse_date(value);
		if (!parsed) return String(value);
		return new Intl.DateTimeFormat("ar-EG-u-ca-gregory-nu-latn", {
			day: "numeric",
			month: "long",
			year: "numeric",
		}).format(parsed);
	}

	format_datetime(value) {
		if (!value) return "";
		const parsed = this.parse_date(value);
		if (!parsed) return String(value);
		return new Intl.DateTimeFormat("ar-EG-u-ca-gregory-nu-latn", {
			day: "numeric",
			month: "long",
			year: "numeric",
			hour: "numeric",
			minute: "2-digit",
		}).format(parsed);
	}

	format_timeline_date(value) {
		if (!value) return "";
		const parsed = this.parse_date(value);
		if (!parsed) return String(value);
		return new Intl.DateTimeFormat("ar-EG-u-ca-gregory-nu-latn", {
			day: "numeric",
			month: "long",
		}).format(parsed);
	}

	parse_date(value) {
		if (value instanceof Date && !Number.isNaN(value.getTime())) return value;
		const normalized = String(value).includes("T") ? String(value) : String(value).replace(" ", "T");
		const parsed = new Date(normalized.length === 10 ? `${normalized}T00:00:00` : normalized);
		return Number.isNaN(parsed.getTime()) ? null : parsed;
	}

	avatar(item, display_name) {
		const user = this.first(item.assigned_by, item.requested_by, item.owner);
		const image = this.first(item.user_image, item.requested_by_image, item.owner_image);
		try {
			return user
				? frappe.avatar(user, "avatar-xs", display_name, image || null)
				: frappe.avatar(null, "avatar-xs", display_name, image || null);
		} catch (error) {
			return frappe.avatar(null, "avatar-xs", display_name, image || null);
		}
	}

	user_display(user) {
		if (!user) return "";
		try {
			return frappe.user_info(user)?.fullname || user;
		} catch (error) {
			return user;
		}
	}

	icon(name, size = "sm") {
		return frappe.utils.icon(name, size);
	}

	join_meta(values) {
		return [...new Set(values.filter(Boolean).map(String))]
			.map((value) => this.escape(value))
			.join('<span aria-hidden="true">·</span>');
	}

	plain_text(value) {
		const text = String(value || "");
		const stripped = typeof strip_html === "function" ? strip_html(text) : text.replace(/<[^>]*>/g, " ");
		return frappe.utils.unescape_html(stripped).replace(/\s+/g, " ").trim();
	}

	first(...values) {
		return values.find((value) => value !== undefined && value !== null && value !== "") ?? "";
	}

	first_array(...values) {
		return values.find((value) => Array.isArray(value)) || [];
	}

	number(value) {
		const parsed = Number(value);
		return Number.isFinite(parsed) ? parsed : 0;
	}

	priority_label(priority) {
		const labels = {
			High: __("أولوية مرتفعة"),
			Medium: __("أولوية متوسطة"),
			Low: __("أولوية منخفضة"),
		};
		return labels[priority] || "";
	}

	escape(value) {
		return frappe.utils.escape_html(String(value ?? ""));
	}

	escape_attr(value) {
		return this.escape(value).replace(/`/g, "&#96;");
	}

	show_action_error(error) {
		this.log_error("action", error);
		frappe.msgprint({
			title: __("تعذّر تنفيذ الإجراء"),
			message: __("لم يتم حفظ أي تغيير. حاول مرة أخرى أو تواصل مع مسؤول النظام."),
			indicator: "red",
		});
	}

	log_error(context, error) {
		console.error(`[my-followups:${context}]`, error);
	}
}
