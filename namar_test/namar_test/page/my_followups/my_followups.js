frappe.pages["my-followups"].on_page_load = function (wrapper) {
	wrapper.my_followups = new NamarMyFollowups(wrapper);
};

frappe.pages["my-followups"].on_page_show = function (wrapper) {
	wrapper.my_followups?.show();
};

class NamarMyFollowups {
	constructor(wrapper) {
		this.wrapper = wrapper;
		const deep_link = this.read_deep_link();
		this.page = frappe.ui.make_app_page({
			parent: wrapper,
			title: __("متابعاتي"),
			single_column: true,
		});
		this.api = "namar_test.followups.api";
		this.mentions_api = "namar_test.mentions.api";
		this.state = {
			source: deep_link.source,
			bucket: deep_link.source === "mentions" ? "open" : "all",
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
			reply_request_id: null,
			action_busy: false,
		};
		this.selected_by_source = { mentions: null, followups: null, approvals: null };
		this.pending_thread = deep_link.source === "mentions" ? deep_link.thread : "";
		this.list_sequence = 0;
		this.detail_sequence = 0;
		this.last_loaded_at = 0;
		this.search_timer = null;
		this.mention_reply_control = null;
		this.applied_seen_events = new Set();
		this.build();
		this.bind_events();
	}

	show() {
		const deep_link = this.read_deep_link();
		if (deep_link.source !== this.state.source) {
			this.pending_thread = deep_link.source === "mentions" ? deep_link.thread : "";
			this.change_source(deep_link.source, { update_url: false });
			return;
		}

		if (this.state.list_status === "idle") {
			this.load_list();
			return;
		}

		if (deep_link.source === "mentions" && deep_link.thread && deep_link.thread !== this.state.selected_name) {
			this.pending_thread = "";
			this.select_item(deep_link.thread, true);
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
						<p>${this.escape(this.source_intro())}</p>
					</div>
					<div class="mf-source-switch" role="tablist" aria-label="${this.escape(__("مصدر قائمة العمل"))}">
						<button type="button" class="mf-source-btn ${this.state.source === "mentions" ? "is-active" : ""}" data-source="mentions" role="tab" aria-selected="${this.state.source === "mentions"}">
							${this.icon("mail", "sm")}
							<span>${this.escape(__("الوارد"))}</span>
						</button>
						<button type="button" class="mf-source-btn ${this.state.source === "followups" ? "is-active" : ""}" data-source="followups" role="tab" aria-selected="${this.state.source === "followups"}">
							${this.icon("clipboard", "sm")}
							<span>${this.escape(__("المتابعات"))}</span>
						</button>
						<button type="button" class="mf-source-btn ${this.state.source === "approvals" ? "is-active" : ""}" data-source="approvals" role="tab" aria-selected="${this.state.source === "approvals"}">
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
								<input id="mf-followups-search" type="search" class="mf-search-input" placeholder="${this.escape(this.search_placeholder())}" autocomplete="off" />
								<button type="button" class="mf-clear-search" aria-label="${this.escape(__("مسح البحث"))}" hidden>
									${this.icon("close", "xs")}
								</button>
							</div>
							<button type="button" class="mf-icon-button mf-filter-button" aria-label="${this.escape(__("تصفية حسب الأولوية"))}" title="${this.escape(__("تصفية حسب الأولوية"))}">
								${this.icon("filter", "sm")}
							</button>
						</div>
						<div class="mf-filter-bar" role="tablist" aria-label="${this.escape(__("تصفية قائمة العمل"))}"></div>
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
		this.$root.find(".mf-filter-button").prop("hidden", this.state.source !== "followups");
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
				if (this.state.action_busy) return;
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
		this.$root.on("click", ".mf-mention-picker-trigger", () => this.open_reply_mention_picker());
		this.$root.on("click", ".mf-mention-reply", () => this.reply_to_mention(false));
		this.$root.on("click", ".mf-mention-reply-close", () => this.reply_to_mention(true));
		this.$root.on("click", ".mf-mention-close", () => this.close_mention());
		this.$root.on("click", ".mf-mention-reopen", () => this.reopen_mention());
		this.$root.on("click", ".mf-mention-convert", () => this.convert_mention());
		this.$root.on("click", ".mf-open-converted-followup", () => this.open_converted_followup());
	}

	change_source(source, { update_url = true } = {}) {
		if (this.state.action_busy) return;
		if (!["mentions", "followups", "approvals"].includes(source) || source === this.state.source) {
			return;
		}

		this.selected_by_source[this.state.source] = this.state.selected_name;
		this.state.source = source;
		if (source !== "mentions" || update_url) this.pending_thread = "";
		this.state.bucket = source === "mentions" ? "open" : "all";
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
		this.state.reply_request_id = null;
		this.detail_sequence += 1;
		this.$search.val("");
		this.$clear_search.prop("hidden", true);
		this.$root.find(".mf-filter-button")
			.prop("hidden", source !== "followups")
			.removeClass("is-active");
		this.$search.attr("placeholder", this.search_placeholder());
		this.$root.find(".mf-page-copy p").text(this.source_intro());
		this.$root.find(".mf-source-btn").each((_, button) => {
			const is_active = $(button).data("source") === source;
			$(button).toggleClass("is-active", is_active).attr("aria-selected", String(is_active));
		});
		if (update_url) this.sync_url_state(source);
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
		if (this.state.action_busy) return;
		const allowed = this.state.source === "mentions"
			? ["open", "unread", "converted", "closed"]
			: this.state.source === "followups"
				? ["all", "overdue", "today", "upcoming"]
				: ["all"];
		if (!allowed.includes(bucket) || bucket === this.state.bucket) {
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
			let method = "get_approvals";
			let api = this.api;
			if (this.state.source === "mentions") {
				method = "get_mentions";
				api = this.mentions_api;
				args.bucket = this.state.bucket;
			} else if (this.state.source === "followups") {
				method = "get_followups";
				args.bucket = this.state.bucket;
				args.priority = this.state.priority;
			}

			const response = await this.call(method, args, api);
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

			let selection = this.state.source === "mentions" && this.pending_thread
				? this.pending_thread
				: previous_selection;
			const keep_displayed_mention = this.state.source === "mentions"
				&& preserve_selection
				&& Boolean(previous_selection)
				&& Boolean(this.state.detail)
				&& this.item_key(this.state.detail) === String(previous_selection);
			const allow_external_selection = this.state.source === "mentions"
				&& Boolean(this.pending_thread || keep_displayed_mention);
			this.pending_thread = "";
			if (!selection || (!allow_external_selection && !this.state.items.some((item) => this.item_key(item) === selection))) {
				selection = this.state.items.length ? this.item_key(this.state.items[0]) : null;
			}

			const is_mobile = typeof window.matchMedia === "function" && window.matchMedia("(max-width: 991px)").matches;
			const skip_initial_mobile_mention = this.state.source === "mentions"
				&& is_mobile
				&& !previous_selection
				&& !allow_external_selection;
			if (selection && !skip_initial_mobile_mention) {
				this.select_item(selection, keep_mobile_detail || allow_external_selection, { force: true });
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
		if (this.state.action_busy || this.state.loading_more || !this.state.has_more) return;
		await this.load_list({ append: true, preserve_selection: true });
	}

	async select_item(name, mobile_detail, { force = false } = {}) {
		if ((!force && this.state.action_busy) || !name) return;
		this.state.selected_name = name;
		this.selected_by_source[this.state.source] = name;
		this.state.mobile_detail = Boolean(mobile_detail);
		this.state.reply_request_id = null;
		this.$workspace.toggleClass("is-mobile-detail", this.state.mobile_detail);
		this.$root.find(".mf-queue-item").each((_, item) => {
			const is_active = String($(item).data("name")) === String(name);
			$(item).toggleClass("is-active", is_active).attr("aria-selected", String(is_active));
		});
		if (this.state.source === "mentions") this.sync_url_state("mentions", name);
		await this.load_detail(name);
	}

	async load_detail(name) {
		const sequence = ++this.detail_sequence;
		this.state.detail_status = "loading";
		this.render_detail_loading();

		try {
			let method = "get_approval_detail";
			let key = "action_name";
			let api = this.api;
			if (this.state.source === "mentions") {
				method = "get_mention_detail";
				key = "thread_name";
				api = this.mentions_api;
			} else if (this.state.source === "followups") {
				method = "get_followup_detail";
				key = "todo_name";
			}
			const response = await this.call(method, { [key]: name }, api);
			if (sequence !== this.detail_sequence || name !== this.state.selected_name) return;

			const detail = this.normalize_detail_response(response);
			const should_mark_seen = this.state.source === "mentions" && Boolean(detail.unread);
			this.state.detail = detail;
			this.state.detail_status = "ready";
			this.render_detail();

			if (should_mark_seen) {
				try {
					await this.call("mark_mention_seen", {
						thread_name: name,
						seen: 1,
						expected_last_event_key: detail.last_event_key,
					}, this.mentions_api);
					detail.unread = 0;
					this.apply_seen_state(name, detail.last_event_key);
				} catch (error) {
					if (sequence !== this.detail_sequence || name !== this.state.selected_name) return;
					if (await this.handle_mention_conflict(error, name)) return;
					this.log_error("mark_mention_seen", error);
				}
			}
		} catch (error) {
			if (sequence !== this.detail_sequence) return;
			this.state.detail_status = "error";
			this.render_detail_error();
			this.log_error("load_detail", error);
		}
	}

	render_filters() {
		if (this.state.source === "mentions") {
			this.$filters.removeClass("is-approvals").addClass("is-mentions");
			const filters = [
				{ key: "open", label: __("تحتاج قرارًا") },
				{ key: "unread", label: __("غير مقروءة") },
				{ key: "converted", label: __("محوّلة") },
				{ key: "closed", label: __("مغلقة") },
			];
			this.$filters.html(filters.map((filter) => {
				const active = filter.key === this.state.bucket;
				return `
					<button type="button" class="mf-filter-btn mf-filter-mention-${filter.key} ${active ? "is-active" : ""}"
						data-bucket="${filter.key}" role="tab" aria-selected="${active}">
						<span>${this.escape(filter.label)}</span>
						<strong>${this.escape(this.count_for(filter.key))}</strong>
					</button>
				`;
			}).join(""));
			return;
		}

		if (this.state.source === "approvals") {
			const known_total = this.state.counts.all ?? this.state.total;
			const total = known_total === null || known_total === undefined
				? `${this.state.items.length}${this.state.has_more ? "+" : ""}`
				: this.number(known_total);
			this.$filters.removeClass("is-mentions").addClass("is-approvals").html(`
				<button type="button" class="mf-filter-btn is-active" data-bucket="all" role="tab" aria-selected="true">
					<span>${this.escape(__("بانتظار مراجعتي"))}</span>
					<strong>${this.escape(total)}</strong>
				</button>
			`);
			return;
		}

		this.$filters.removeClass("is-approvals is-mentions");
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
				<div class="mf-queue-skeleton ${this.state.source === "mentions" ? "is-mention" : ""}" aria-hidden="true">
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
			const empty_title = this.state.source === "mentions" ? __("صندوق الوارد فارغ") : __("قائمة العمل فارغة");
			const empty_message = this.state.source === "mentions"
				? this.mention_empty_message()
				: this.state.source === "followups"
					? __("لا توجد متابعات ضمن هذا التصنيف حاليًا.")
					: __("لا توجد موافقات بانتظار مراجعتك حاليًا.");
			this.$list.html(this.state_markup({
				icon: this.state.source === "mentions" ? "mail" : "search",
				title: is_search ? __("لا توجد نتائج مطابقة") : empty_title,
				message: is_search
					? __("جرّب عبارة بحث أخرى أو امسح البحث.")
					: empty_message,
			}));
			this.$pagination.empty();
			return;
		}

		this.$list.html(this.state.items.map((item) => this.render_queue_item(item)).join(""));
		this.render_pagination();
	}

	render_queue_item(item) {
		if (this.state.source === "mentions") return this.render_mention_queue_item(item);

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

	render_mention_queue_item(item) {
		const name = this.item_key(item);
		const sender = this.first(item.latest_from_user_name, this.user_display(item.latest_from_user), __("مرسل غير معروف"));
		const preview = this.plain_text(this.first(item.latest_preview_plain, __("ذكرك في تعليق")));
		const reference_type = this.reference_type(item);
		const reference_name = this.reference_name(item);
		const reference_title = this.first(item.reference_title, reference_name);
		const reference_label = this.join_meta([reference_type, reference_title]);
		const count = Math.max(1, this.number(item.mention_count));
		const unread = Boolean(item.unread);
		const is_active = String(name) === String(this.state.selected_name);
		const status = this.mention_status(item.status);
		const time = this.format_relative_datetime(item.latest_mentioned_at);
		const aria_label = this.mention_aria_label(item, sender, preview, reference_title);

		return `
			<article class="mf-queue-item mf-mention-item ${unread ? "is-unread" : ""} ${is_active ? "is-active" : ""}"
				data-name="${this.escape_attr(name)}" role="option" tabindex="0" aria-selected="${is_active}"
				aria-label="${this.escape_attr(aria_label)}">
				<span class="mf-mention-unread-dot" aria-hidden="true"></span>
				<div class="mf-mention-avatar">${this.mention_avatar(item.latest_from_user, sender, "avatar-medium")}</div>
				<div class="mf-item-copy mf-mention-copy">
					<div class="mf-mention-item-head">
						<h3>${this.escape(sender)}</h3>
						${time ? `<time datetime="${this.escape_attr(item.latest_mentioned_at)}">${this.escape(time)}</time>` : ""}
					</div>
					<p class="mf-mention-subject">${this.escape(__("ذكرك في تعليق"))}</p>
					<p class="mf-mention-preview">${this.escape(preview)}</p>
					<div class="mf-mention-item-foot">
						${reference_label ? `<span class="mf-mention-reference">${this.icon("file", "xs")}${reference_label}</span>` : ""}
						${count > 1 ? `<span class="mf-mention-count">${this.escape(__("{0} رسائل", [count]))}</span>` : ""}
						${status.key !== "open" ? `<span class="mf-mention-status is-${status.key}">${this.escape(status.label)}</span>` : ""}
					</div>
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
		if (this.state.source === "mentions") {
			this.render_mention_detail();
			return;
		}
		if (this.state.source === "approvals") {
			this.render_approval_detail();
			return;
		}
		this.render_followup_detail();
	}

	render_mention_detail() {
		this.mention_reply_control = null;
		const detail = this.state.detail || {};
		const messages = this.first_array(detail.messages);
		const permissions = detail.permissions || {};
		const status = this.mention_status(detail.status);
		const latest_comment = this.first(detail.latest_comment);
		const latest_message = messages.find((message) => (
			message.comment === latest_comment || message.event_key === latest_comment
		)) || messages[messages.length - 1] || {};
		const sender = this.first(
			detail.latest_from_user_name,
			this.user_display(detail.latest_from_user),
			latest_message.from_user_name,
			this.user_display(latest_message.from_user),
			__("مرسل غير معروف")
		);
		const reference_type = this.reference_type(detail);
		const reference_name = this.reference_name(detail);
		const reference_title = this.first(detail.reference_title, reference_name);
		const display_reference_title = reference_title && reference_title !== reference_name ? reference_title : "";
		const can_reply = permissions.can_reply === true;
		const can_close = permissions.can_close === true;
		const can_reopen = permissions.can_reopen === true;
		const can_convert = permissions.can_convert === true;
		const converted_to_todo = this.first(detail.converted_to_todo, detail.followup_name);
		const has_actions = can_close || can_reopen || can_convert;

		this.$detail.html(`
			<div class="mf-detail-layout is-mention">
				<header class="mf-detail-reference-bar mf-mention-reference-bar">
					<button type="button" class="mf-mobile-back" aria-label="${this.escape(__("العودة إلى القائمة"))}">
						${this.icon("arrow-right", "sm")}
						<span>${this.escape(__("القائمة"))}</span>
					</button>
					<div class="mf-reference-summary">
						<span class="mf-status-pill mf-read-pill ${detail.unread ? "is-unread" : "is-read"}">
							${this.escape(detail.unread ? __("غير مقروءة") : __("مقروءة"))}
						</span>
						<span class="mf-status-pill mf-mention-state-pill is-${status.key}">${this.escape(status.label)}</span>
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

				<div class="mf-detail-scroll mf-mention-detail-scroll">
					<section class="mf-detail-hero mf-mention-hero">
						<div class="mf-mention-hero-avatar">${this.mention_avatar(this.first(detail.latest_from_user, latest_message.from_user), sender, "avatar-medium")}</div>
						<div>
							<h2>${this.escape(__("ذكرك {0} في تعليق", [sender]))}</h2>
							<div class="mf-detail-meta">
								${reference_type ? `<span>${this.icon("file", "xs")}${this.escape(reference_type)}</span>` : ""}
								${this.first(detail.latest_mentioned_at, latest_message.mentioned_at) ? `<span>${this.icon("calendar", "xs")}${this.escape(this.format_datetime(this.first(detail.latest_mentioned_at, latest_message.mentioned_at)))}</span>` : ""}
								${this.number(detail.mention_count) > 1 ? `<span>${this.icon("comment", "xs")}${this.escape(__("{0} رسائل", [this.number(detail.mention_count)]))}</span>` : ""}
							</div>
						</div>
					</section>

					<section class="mf-section mf-mention-thread-section" aria-labelledby="mf-mention-thread-heading">
						<div class="mf-section-heading">
							<h3 id="mf-mention-thread-heading">${this.escape(__("المحادثة والسياق"))}</h3>
							<span class="mf-thread-count">${this.escape(__("{0} رسائل", [messages.length]))}</span>
						</div>
						${this.render_mention_messages(messages, detail)}
					</section>

					${converted_to_todo ? `
						<section class="mf-section mf-converted-followup-card">
							<span class="mf-converted-followup-icon">${this.icon("clipboard", "sm")}</span>
							<div>
								<strong>${this.escape(__("حُوّلت إلى متابعة"))}</strong>
								<span>${this.escape(converted_to_todo)}</span>
							</div>
							<button type="button" class="mf-link-button mf-open-converted-followup" data-todo-name="${this.escape_attr(converted_to_todo)}">
								${this.icon("external-link", "xs")}
								<span>${this.escape(__("فتح المتابعة"))}</span>
							</button>
						</section>
					` : ""}

					${can_reply ? `
						<section class="mf-section mf-mention-reply-section">
							<div class="mf-mention-reply-heading">
								<h3>${this.escape(__("الرد"))}</h3>
								<button type="button" class="mf-mention-picker-trigger" aria-label="${this.escape(__("ذكر موظف في الرد"))}">
									<span aria-hidden="true">@</span>
									<span>${this.escape(__("ذكر موظف"))}</span>
								</button>
							</div>
							<div id="mf-mention-reply" class="mf-mention-reply-editor" dir="rtl"></div>
							<p class="mf-mention-reply-hint">${this.escape(__("اكتب @ أو اضغط «ذكر موظف» لإضافة منشن."))}</p>
							<div class="mf-mention-reply-actions">
								<button type="button" class="mf-action-btn is-primary mf-mention-reply">
									${this.icon("send", "sm")}
									<span>${this.escape(__("إرسال الرد"))}</span>
								</button>
								${can_close ? `
									<button type="button" class="mf-action-btn is-secondary mf-mention-reply-close">
										${this.icon("tick", "sm")}
										<span>${this.escape(__("إرسال وإغلاق"))}</span>
									</button>
								` : ""}
							</div>
						</section>
					` : ""}
				</div>

				${has_actions ? `
					<footer class="mf-detail-actions is-mention">
						${can_reopen ? `
							<button type="button" class="mf-action-btn is-secondary mf-mention-reopen">
								${this.icon("refresh", "sm")}
								<span>${this.escape(__("إعادة فتح"))}</span>
							</button>
						` : ""}
						${can_close ? `
							<button type="button" class="mf-action-btn is-secondary mf-mention-close">
								${this.icon("close", "sm")}
								<span>${this.escape(__("إغلاق الرسالة"))}</span>
							</button>
						` : ""}
						${can_convert ? `
							<button type="button" class="mf-action-btn is-primary mf-mention-convert">
								${this.icon("clipboard", "sm")}
								<span>${this.escape(__("تحويل إلى متابعة"))}</span>
							</button>
						` : ""}
					</footer>
				` : ""}
			</div>
		`);

		if (can_reply) this.init_mention_reply_editor();
	}

	init_mention_reply_editor() {
		const $parent = this.$detail.find(".mf-mention-reply-editor");
		const thread_name = this.state.selected_name;
		if (!$parent.length || !thread_name) return;

		const control = frappe.ui.form.make_control({
			parent: $parent,
			df: {
				fieldtype: "Comment",
				fieldname: "mention_reply",
			},
			enable_mentions: true,
			render_input: true,
			only_input: true,
			no_wrapper: true,
			on_submit: () => {
				if (this.mention_reply_control === control && thread_name === this.state.selected_name) {
					this.reply_to_mention(false);
				}
			},
		});
		this.mention_reply_control = control;

		const quill = control.quill;
		if (!quill) return;
		quill.root.id = "mf-mention-reply-input";
		quill.root.setAttribute("dir", "auto");
		quill.root.setAttribute("aria-label", __("اكتب ردك"));
		quill.root.dataset.placeholder = __("اكتب ردك على المستند...");
		quill.on("text-change", () => {
			if (this.mention_reply_control === control) this.state.reply_request_id = null;
		});

		const mention_module = quill.getModule("mention");
		if (!mention_module) return;
		let search_sequence = 0;
		mention_module.options.source = frappe.utils.debounce(async (search_term, render_list) => {
			const sequence = ++search_sequence;
			if (this.mention_reply_control !== control || thread_name !== this.state.selected_name) {
				render_list([], search_term);
				return;
			}
			try {
				const response = await this.call("search_reply_mentions", {
					thread_name,
					search_term,
				}, this.mentions_api);
				if (
					sequence === search_sequence
					&& this.mention_reply_control === control
					&& thread_name === this.state.selected_name
				) {
					render_list(Array.isArray(response) ? response : [], search_term);
				}
			} catch (error) {
				this.log_error("search_reply_mentions", error);
				render_list([], search_term);
			}
		}, 300);
	}

	open_reply_mention_picker() {
		const quill = this.mention_reply_control?.quill;
		if (!quill || this.state.action_busy) return;
		const range = quill.getSelection(true) || { index: Math.max(0, quill.getLength() - 1), length: 0 };
		const index = range.index + range.length;
		const previous = index > 0 ? quill.getText(index - 1, 1) : "";
		const inserted = previous && !/\s/.test(previous) ? " @" : "@";
		quill.setSelection(index, 0, "silent");
		quill.insertText(index, inserted, "user");
		quill.setSelection(index + inserted.length, 0, "user");
		quill.focus();
	}

	render_mention_messages(messages, detail) {
		if (!messages.length) {
			return `<div class="mf-inline-empty">${this.escape(__("لا توجد رسائل سياق متاحة."))}</div>`;
		}
		const latest_comment = this.first(detail.latest_comment);
		const has_latest_match = Boolean(latest_comment) && messages.some((message) => message.comment === latest_comment || message.event_key === latest_comment);

		return `<ol class="mf-mention-messages">
			${messages.map((message, index) => {
				const sender = this.first(message.from_user_name, this.user_display(message.from_user), __("مرسل غير معروف"));
				const content = this.plain_multiline(message.content_plain);
				const is_current = has_latest_match
					? message.comment === latest_comment || message.event_key === latest_comment
					: index === messages.length - 1;
				return `
					<li class="mf-mention-message ${is_current ? "is-current" : ""}">
						<div class="mf-message-avatar">${this.mention_avatar(message.from_user, sender, "avatar-medium")}</div>
						<article class="mf-message-card">
							<header>
								<strong>${this.escape(sender)}</strong>
								<div class="mf-message-meta">
									${is_current ? `<span>${this.escape(__("ذكرك هنا"))}</span>` : ""}
									${message.mentioned_at ? `<time datetime="${this.escape_attr(message.mentioned_at)}">${this.escape(this.format_datetime(message.mentioned_at))}</time>` : ""}
								</div>
							</header>
							<p dir="auto">${this.escape(content || __("رسالة بلا نص"))}</p>
						</article>
					</li>
				`;
			}).join("")}
		</ol>`;
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
		const mentions = this.state.source === "mentions";
		this.$detail.attr("aria-busy", "false").html(this.state_markup({
			icon: mentions ? "mail" : approvals ? "review" : "clipboard",
			title: mentions ? __("اختر رسالة من الوارد") : approvals ? __("اختر موافقة لمراجعتها") : __("اختر متابعة من القائمة"),
			message: mentions
				? __("ستظهر هنا المحادثة والرد وخيارات الإغلاق أو التحويل إلى متابعة.")
				: approvals
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

	async reply_to_mention(close_after_reply) {
		if (
			this.state.action_busy
			|| this.state.source !== "mentions"
			|| !this.state.selected_name
		) return;
		const control = this.mention_reply_control;
		const reply_html = String(control?.get_value?.() || "").trim();
		const reply = strip_html(reply_html).replace(/\s+/g, " ").trim();
		if (!reply) {
			frappe.show_alert({ message: __("اكتب الرد أولًا"), indicator: "orange" });
			control?.quill?.focus();
			return;
		}

		const request_id = this.state.reply_request_id || this.make_request_id();
		this.state.reply_request_id = request_id;
		const success = await this.run_mention_action({
			method: close_after_reply ? "reply_and_close" : "reply_mention",
			args: {
				thread_name: this.state.selected_name,
				reply,
				reply_html,
				request_id,
				expected_last_event_key: this.state.detail?.last_event_key,
			},
			success_message: close_after_reply ? __("تم إرسال الرد وإغلاق الرسالة") : __("تم إرسال الرد"),
			preserve_selection: !close_after_reply,
		});
		if (success) this.state.reply_request_id = null;
	}

	async close_mention() {
		if (this.state.source !== "mentions" || !this.state.selected_name) return;
		await this.run_mention_action({
			method: "close_mention",
			args: {
				thread_name: this.state.selected_name,
				expected_last_event_key: this.state.detail?.last_event_key,
			},
			success_message: __("تم إغلاق الرسالة"),
		});
	}

	async reopen_mention() {
		if (this.state.source !== "mentions" || !this.state.selected_name) return;
		await this.run_mention_action({
			method: "reopen_mention",
			args: {
				thread_name: this.state.selected_name,
				expected_last_event_key: this.state.detail?.last_event_key,
			},
			success_message: __("تمت إعادة فتح الرسالة"),
		});
	}

	convert_mention() {
		if (this.state.source !== "mentions" || !this.state.selected_name) return;
		const thread_name = this.state.selected_name;
		const detail = this.state.detail || {};
		const expected_last_event_key = detail.last_event_key;
		const dialog = new frappe.ui.Dialog({
			title: __("تحويل الرسالة إلى متابعة"),
			fields: [
				{
					fieldname: "due_date",
					fieldtype: "Date",
					label: __("تاريخ الاستحقاق"),
					reqd: 1,
					default: frappe.datetime.add_days(frappe.datetime.now_date(), 1),
				},
				{
					fieldname: "priority",
					fieldtype: "Select",
					label: __("الأولوية"),
					options: [
						{ label: __("مرتفعة"), value: "High" },
						{ label: __("متوسطة"), value: "Medium" },
						{ label: __("منخفضة"), value: "Low" },
					],
					default: "Medium",
				},
				{
					fieldname: "description",
					fieldtype: "Small Text",
					label: __("وصف المتابعة"),
					reqd: 1,
					default: this.plain_multiline(this.first(detail.latest_preview_plain, this.last_message_content(detail.messages))),
				},
			],
			primary_action_label: __("إنشاء المتابعة"),
			primary_action: async (values) => {
				dialog.disable_primary_action();
				try {
					await this.call("convert_mention_to_followup", {
						thread_name,
						due_date: values.due_date,
						priority: values.priority || "Medium",
						description: values.description || "",
						expected_last_event_key,
					}, this.mentions_api);
					dialog.hide();
					frappe.show_alert({ message: __("تم تحويل الرسالة إلى متابعة"), indicator: "green" });
					if (this.state.source === "mentions") {
						this.state.selected_name = null;
						this.selected_by_source.mentions = null;
						this.sync_url_state("mentions");
						await this.load_list();
					}
				} catch (error) {
					if (this.is_mention_conflict_error(error)) {
						dialog.hide();
						await this.handle_mention_conflict(error, thread_name);
						return;
					}
					dialog.enable_primary_action();
					this.show_action_error(error);
				}
			},
		});
		dialog.show();
		this.prepare_dialog_rtl(dialog);
	}

	async run_mention_action({ method, args, success_message, preserve_selection = false }) {
		this.set_action_busy(true);
		try {
			await this.call(method, args, this.mentions_api);
			frappe.show_alert({ message: success_message, indicator: "green" });
			if (!preserve_selection) {
				this.state.selected_name = null;
				this.selected_by_source.mentions = null;
				this.sync_url_state("mentions");
			}
			await this.load_list({ preserve_selection });
			return true;
		} catch (error) {
			if (await this.handle_mention_conflict(error, args.thread_name)) return false;
			this.show_action_error(error);
			return false;
		} finally {
			this.set_action_busy(false);
		}
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
		this.state.action_busy = busy;
		if (busy) {
			window.clearTimeout(this.search_timer);
			this.$root.find("button:not(:disabled), input:not(:disabled), textarea:not(:disabled)")
				.attr("data-mf-busy-disabled", "1")
				.prop("disabled", true);
		} else {
			this.$root.find('[data-mf-busy-disabled="1"]')
				.prop("disabled", false)
				.removeAttr("data-mf-busy-disabled");
		}
		if (this.mention_reply_control) {
			if (busy) this.mention_reply_control.disable();
			else this.mention_reply_control.enable();
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
		if (doctype && name) {
			frappe.set_route("Form", doctype, name);
			return;
		}
		if (detail.reference_route) {
			const route = String(detail.reference_route).replace(/^\/?app\//, "").split("/").filter(Boolean);
			if (route.length) frappe.set_route(...route);
		}
	}

	open_converted_followup() {
		const todo_name = this.first(
			this.$detail.find(".mf-open-converted-followup").data("todo-name"),
			this.state.detail?.converted_to_todo,
			this.state.detail?.followup_name
		);
		if (todo_name) frappe.set_route("Form", "ToDo", todo_name);
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

	async call(method, args, api = this.api) {
		const response = await frappe.call({
			method: `${api}.${method}`,
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
		const bucket_total = ["mentions", "followups"].includes(this.state.source)
			? counts[this.state.bucket]
			: counts.all;
		const total_value = payload.total ?? bucket_total;
		const total = total_value === undefined || total_value === null ? null : this.number(total_value);
		const has_more = Boolean(payload.has_more ?? (total !== null && items.length < total));
		const next_start = payload.next_start ?? (has_more ? this.state.limit_start + items.length : null);
		return { items, counts, total, has_more, next_start };
	}

	normalize_detail_response(response) {
		const payload = response || {};
		if (this.state.source === "mentions") {
			const record = payload.mention || payload;
			const reference = payload.reference || {};
			return {
				...record,
				reference_type: this.first(
					record.reference_type,
					record.reference_doctype,
					reference.reference_type,
					reference.reference_doctype,
					reference.doctype
				),
				reference_name: this.first(record.reference_name, reference.reference_name, reference.name),
				reference_title: this.first(record.reference_title, reference.reference_title, reference.title),
				reference_route: this.first(record.reference_route, reference.reference_route, reference.route),
				messages: this.first_array(payload.messages, record.messages),
				permissions: payload.permissions || record.permissions || {},
			};
		}
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

	source_intro() {
		if (this.state.source === "mentions") return __("راجع ما وصلك واتخذ قرارًا واضحًا");
		if (this.state.source === "approvals") return __("راجع المستندات التي تنتظر موافقتك");
		return __("أنجز المتابعة وسجّل النتيجة");
	}

	search_placeholder() {
		if (this.state.source === "mentions") return __("ابحث في الوارد");
		if (this.state.source === "approvals") return __("ابحث في الموافقات");
		return __("ابحث في المتابعات");
	}

	mention_empty_message() {
		const messages = {
			open: __("لا توجد رسائل تحتاج قرارًا حاليًا."),
			unread: __("اطلعت على كل الرسائل الواردة."),
			converted: __("لم تُحوّل أي رسالة إلى متابعة بعد."),
			closed: __("لا توجد رسائل مغلقة حاليًا."),
		};
		return messages[this.state.bucket] || messages.open;
	}

	mention_status(value) {
		const status = String(value || "Open").toLowerCase();
		if (status === "converted") return { key: "converted", label: __("محوّلة لمتابعة") };
		if (status === "closed") return { key: "closed", label: __("مغلقة") };
		return { key: "open", label: __("تحتاج قرارًا") };
	}

	mention_aria_label(item, sender = "", preview = "", reference_title = "") {
		const display_sender = sender || this.first(item.latest_from_user_name, this.user_display(item.latest_from_user));
		const display_preview = preview || this.plain_text(item.latest_preview_plain);
		const display_reference = reference_title || this.first(item.reference_title, item.reference_name);
		return [
			item.unread ? __("غير مقروءة") : __("مقروءة"),
			display_sender,
			display_preview,
			display_reference,
		].filter(Boolean).join("، ");
	}

	read_deep_link() {
		let source = "followups";
		let thread = "";
		try {
			const params = new URLSearchParams(window.location.search || "");
			const requested_source = params.get("source");
			if (["mentions", "followups", "approvals"].includes(requested_source)) source = requested_source;
			thread = source === "mentions" ? String(params.get("thread") || "").trim() : "";
		} catch (error) {
			this.log_error("read_deep_link", error);
		}
		return { source, thread };
	}

	sync_url_state(source, thread = "") {
		try {
			const url = new URL(window.location.href);
			url.searchParams.set("source", source);
			if (source === "mentions" && thread) url.searchParams.set("thread", thread);
			else url.searchParams.delete("thread");
			window.history.replaceState(window.history.state, "", url.toString());
		} catch (error) {
			this.log_error("sync_url_state", error);
		}
	}

	apply_seen_state(name, event_key = "") {
		if (this.state.source !== "mentions") return;
		const item_index = this.state.items.findIndex((entry) => this.item_key(entry) === String(name));
		const item = item_index >= 0 ? this.state.items[item_index] : null;
		const seen_identity = `${String(name)}:${String(event_key)}`;
		const is_first_application = !this.applied_seen_events.has(seen_identity);
		this.applied_seen_events.add(seen_identity);
		const was_unread = item ? Boolean(item.unread) : is_first_application;
		if (item) item.unread = 0;
		if (was_unread && this.state.counts.unread !== undefined) {
			this.state.counts.unread = Math.max(0, this.number(this.state.counts.unread) - 1);
		}

		if (this.state.bucket === "unread" && item_index >= 0) {
			this.state.items.splice(item_index, 1);
			if (this.state.total !== null && this.state.total !== undefined) {
				this.state.total = Math.max(0, this.number(this.state.total) - 1);
			}
			if (this.state.next_start !== null && this.state.next_start !== undefined) {
				this.state.next_start = Math.max(0, this.number(this.state.next_start) - 1);
				this.state.has_more = this.state.total === null || this.state.total === undefined
					? this.state.has_more
					: this.state.next_start < this.state.total;
			}
			this.state.limit_start = this.state.items.length;
			this.render_filters();
			this.render_list();
		} else {
			this.$root.find(".mf-queue-item").each((_, element) => {
				if (String($(element).data("name")) === String(name)) {
					$(element)
						.removeClass("is-unread")
						.attr("aria-label", item ? this.mention_aria_label(item) : __("مقروءة"));
				}
			});
			this.render_filters();
		}

		if (String(this.state.selected_name) === String(name)) {
			this.$root.find(".mf-read-pill")
				.removeClass("is-unread")
				.addClass("is-read")
				.text(__("مقروءة"));
		}
	}

	make_request_id() {
		if (window.crypto?.randomUUID) return window.crypto.randomUUID();
		const suffix = typeof frappe.utils.get_random === "function"
			? frappe.utils.get_random(12)
			: Math.random().toString(36).slice(2, 14);
		return `mention-${Date.now()}-${suffix}`;
	}

	prepare_dialog_rtl(dialog) {
		if (!dialog?.$wrapper) return;
		dialog.$wrapper.addClass("mf-rtl-dialog").attr("dir", "rtl");
	}

	last_message_content(messages) {
		const entries = this.first_array(messages);
		return entries.length ? this.first(entries[entries.length - 1].content_plain) : "";
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
		if (this.state.source === "mentions") {
			if (this.state.counts[bucket] !== undefined) return this.number(this.state.counts[bucket]);
			return bucket === this.state.bucket ? this.number(this.state.total) : 0;
		}
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

	format_relative_datetime(value) {
		const parsed = this.parse_date(value);
		if (!parsed) return value ? String(value) : "";
		const delta_seconds = Math.round((parsed.getTime() - Date.now()) / 1000);
		const absolute_seconds = Math.abs(delta_seconds);
		let amount;
		let unit;
		if (absolute_seconds < 60) {
			amount = delta_seconds;
			unit = "second";
		} else if (absolute_seconds < 3600) {
			amount = Math.round(delta_seconds / 60);
			unit = "minute";
		} else if (absolute_seconds < 86400) {
			amount = Math.round(delta_seconds / 3600);
			unit = "hour";
		} else if (absolute_seconds < 604800) {
			amount = Math.round(delta_seconds / 86400);
			unit = "day";
		} else {
			return this.format_timeline_date(value);
		}
		try {
			return new Intl.RelativeTimeFormat("ar", { numeric: "auto" }).format(amount, unit);
		} catch (error) {
			return this.format_timeline_date(value);
		}
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

	mention_avatar(user, display_name, size = "avatar-xs") {
		try {
			return frappe.avatar(user || null, size, display_name || user || "", null);
		} catch (error) {
			return frappe.avatar(null, size, display_name || "", null);
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

	plain_multiline(value) {
		const text = String(value || "")
			.replace(/<br\s*\/?>/gi, "\n")
			.replace(/<\/p>/gi, "\n");
		const stripped = typeof strip_html === "function" ? strip_html(text) : text.replace(/<[^>]*>/g, " ");
		return frappe.utils.unescape_html(stripped)
			.replace(/\r\n?/g, "\n")
			.replace(/[\t ]+/g, " ")
			.replace(/\n{3,}/g, "\n\n")
			.trim();
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

	is_mention_conflict_error(error) {
		const parts = [
			error?.message,
			error?.exc_type,
			error?.responseJSON?.exc_type,
			error?.responseJSON?.exception,
			error?.responseJSON?._server_messages,
		].filter(Boolean).map(String);
		return parts.some((value) => value.includes("تم تحديث هذه الإشارة منذ عرضها"));
	}

	async handle_mention_conflict(error, thread_name = this.state.selected_name) {
		if (!this.is_mention_conflict_error(error)) return false;
		this.log_error("mention_conflict", error);
		frappe.show_alert({
			message: __("وصل تحديث جديد لهذه الرسالة؛ تم تحميل أحدث نسخة."),
			indicator: "orange",
		});
		if (this.state.source === "mentions" && thread_name === this.state.selected_name) {
			await this.load_list({ preserve_selection: true });
		}
		return true;
	}

	log_error(context, error) {
		console.error(`[my-followups:${context}]`, error);
	}
}
