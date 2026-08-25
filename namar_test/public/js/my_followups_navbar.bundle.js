(() => {
	"use strict";

	const GLOBAL_KEY = "__namar_my_followups_navbar";
	const EVENT_NAMESPACE = ".namarMyFollowupsNavbar";
	const COUNT_METHOD = "namar_test.followups.api.get_my_followups_counts";
	const SOURCE_KEYS = ["mentions", "followups", "approvals"];
	const SOURCE_META = {
		mentions: {
			label: "الوارد",
			attention_label: "الوارد الذي يحتاج قرارًا",
			symbol: "و",
			href: "/app/my-followups?source=mentions",
		},
		followups: {
			label: "المتابعات",
			attention_label: "المتابعات المتأخرة",
			symbol: "ت",
			href: "/app/my-followups?source=followups&bucket=overdue",
		},
		approvals: {
			label: "الموافقات",
			attention_label: "الموافقات المعلقة",
			symbol: "م",
			href: "/app/my-followups?source=approvals",
		},
	};
	const REFRESH_TTL_MS = 2 * 60 * 1000;
	const POLL_INTERVAL_MS = 3 * 60 * 1000;

	function valid_count(value) {
		return Number.isInteger(value) && value >= 0;
	}

	function normalize_counts(response) {
		const message = response?.message ?? response;
		const raw = message?.attention_counts;
		if (!raw || typeof raw !== "object") return null;
		if (!SOURCE_KEYS.every((key) => valid_count(raw[key]))) return null;
		if (!valid_count(raw.total)) return null;
		const expected_total = SOURCE_KEYS.reduce((total, key) => total + raw[key], 0);
		if (raw.total !== expected_total) return null;
		return {
			mentions: raw.mentions,
			followups: raw.followups,
			approvals: raw.approvals,
			total: raw.total,
		};
	}

	function badge_text(total) {
		return total > 99 ? "99+" : String(total);
	}

	function source_status_label(source, count) {
		const meta = SOURCE_META[source];
		return `${meta.attention_label}: ${count}`;
	}

	function badge_view(counts) {
		if (!counts) return null;
		const sources = SOURCE_KEYS.map((source) => {
			const count = counts[source];
			return {
				source,
				count,
				visible: valid_count(count) && count > 0,
				text: valid_count(count) && count > 0 ? badge_text(count) : "",
				label: source_status_label(source, count),
				href: SOURCE_META[source].href,
			};
		});
		return {
			sources,
			visible: sources.some((source) => source.visible),
			status_label: `متابعاتي، ${sources.map((source) => source.label).join("، ")}`,
		};
	}

	class NamarMyFollowupsNavbar {
		constructor() {
			this.counts = null;
			this.load_failed = false;
			this.last_loaded_at = 0;
			this.pending = null;
			this.force_after_pending = false;
			this.request_serial = 0;
			this.timer = null;
			this.destroyed = false;
		}

		start() {
			$(document)
				.off(EVENT_NAMESPACE)
				.on(`toolbar_setup${EVENT_NAMESPACE} app_ready${EVENT_NAMESPACE}`, () => {
					this.ensure_node();
					this.refresh();
				})
				.on(`page-change${EVENT_NAMESPACE}`, () => {
					this.ensure_node();
					this.update_active_state();
					this.refresh();
				})
				.on(`visibilitychange${EVENT_NAMESPACE}`, () => {
					if (!document.hidden) this.refresh();
				})
				.on(`namar:my-followups:count-changed${EVENT_NAMESPACE}`, (_event, payload) => {
					this.merge_source_count(payload);
				});

			this.ensure_node();
			this.timer = window.setInterval(() => {
				if (!document.hidden) this.refresh();
			}, POLL_INTERVAL_MS);
		}

		ensure_node() {
			if (this.destroyed) return null;
			// Frappe v15 renders the header itself as `.navbar`; mirror the
			// framework's notifications lookup so the anchor is found reliably.
			const $notifications = $(".navbar").find(".dropdown-notifications").first();
			if (!$notifications.length) return null;

			let $node = $("#namar-my-followups-nav");
			if ($node.length > 1) {
				$node.slice(1).remove();
				$node = $node.first();
			}
			if (!$node.length) {
				const source_badges = SOURCE_KEYS.map((source) => {
					const meta = SOURCE_META[source];
					return `<a class="namar-my-followups-source-badge is-${source}"
						href="${meta.href}"
						data-source-badge="${source}"
						hidden>
						<span class="namar-my-followups-source-symbol" aria-hidden="true">${meta.symbol}</span>
						<bdi class="namar-my-followups-source-value" dir="ltr"></bdi>
					</a>`;
				}).join("");
				$node = $(
					`<li id="namar-my-followups-nav" class="nav-item namar-my-followups-nav">
						<a class="nav-link namar-my-followups-link" href="/app/my-followups" dir="rtl" aria-label="متابعاتي">
							<span class="namar-my-followups-icon" aria-hidden="true">
								<svg class="es-icon icon-sm"><use href="#es-line-inbox"></use></svg>
							</span>
							<span class="namar-my-followups-label">متابعاتي</span>
						</a>
						<span class="namar-my-followups-counts" dir="rtl" role="group" aria-label="عدادات الانتباه" hidden>
							${source_badges}
						</span>
					</li>`
				);
				$notifications.after($node);
			}
			this.render();
			this.update_active_state();
			return $node;
		}

		update_active_state() {
			const active = frappe.router?.current_route?.[0] === "my-followups";
			const $link = $("#namar-my-followups-nav .namar-my-followups-link");
			$link.toggleClass("is-active", active);
			if (active) $link.attr("aria-current", "page");
			else $link.removeAttr("aria-current");
		}

		merge_source_count(payload) {
			const source = payload?.source;
			const count = payload?.count;
			if (!SOURCE_KEYS.includes(source) || !valid_count(count)) return;
			// حدث المتابعات يحمل إجمالي المفتوح، بينما الشارة الصفراء تعرض
			// المتأخر فقط؛ لذلك نعيد قراءة العقد الموحد بدل دمج رقم مختلف المعنى.
			if (source === "followups") {
				if (!payload?.force) return;
				if (this.pending) {
					this.force_after_pending = true;
					return this.pending;
				}
				this.last_loaded_at = 0;
				return this.refresh(true);
			}
			if (!this.counts) {
				if (this.pending) {
					if (payload?.force) this.force_after_pending = true;
					return this.pending;
				}
				return this.refresh(true);
			}
			if (this.pending) this.force_after_pending = true;
			this.counts[source] = count;
			this.counts.total = SOURCE_KEYS.reduce((total, key) => total + this.counts[key], 0);
			this.last_loaded_at = Date.now();
			this.render();
		}

		refresh(force = false) {
			if (this.destroyed || document.hidden) return Promise.resolve(null);
			if (!force && this.counts && Date.now() - this.last_loaded_at < REFRESH_TTL_MS) {
				return Promise.resolve(this.counts);
			}
			if (this.pending) {
				if (force) this.force_after_pending = true;
				return this.pending;
			}

			const serial = ++this.request_serial;
			this.pending = Promise.resolve()
				.then(() => frappe.call({
					method: COUNT_METHOD,
					type: "GET",
					args: {},
					quiet: true,
				}))
				.then((response) => {
					if (this.destroyed || serial !== this.request_serial) return null;
					const counts = normalize_counts(response);
					if (!counts) throw new Error("Invalid My Followups counts contract");
					this.counts = counts;
					this.load_failed = false;
					this.last_loaded_at = Date.now();
					this.render();
					return counts;
				})
				.catch((error) => {
					if (!this.destroyed) {
						this.load_failed = true;
						this.render();
						console.warn("[my-followups-navbar] تعذر تحديث العداد", error);
					}
					return null;
				})
				.then((result) => {
					this.pending = null;
					if (this.force_after_pending && !this.destroyed) {
						this.force_after_pending = false;
						this.last_loaded_at = 0;
						this.refresh(true);
					}
					return result;
				});
			return this.pending;
		}

		render() {
			const $node = $("#namar-my-followups-nav");
			const $link = $("#namar-my-followups-nav .namar-my-followups-link");
			const $group = $node.find(".namar-my-followups-counts");
			if (!$link.length || !$group.length) return;
			const view = badge_view(this.counts);
			view?.sources.forEach(({ source, visible, text, label }) => {
				const $badge = $group.find(`[data-source-badge="${source}"]`);
				$badge
					.prop("hidden", !visible)
					.attr("aria-label", label)
					.attr("title", label)
					.find(".namar-my-followups-source-value")
					.text(text);
			});
			$node.toggleClass("has-visible-counts", Boolean(view?.visible));
			$group.prop("hidden", !view?.visible);

			const status_label = view
				? view.status_label
				: (this.load_failed ? "متابعاتي، تعذر تحديث العداد" : "متابعاتي، جار تحديث العداد");
			$link.attr("aria-label", status_label).attr("title", status_label);
		}

		destroy() {
			this.destroyed = true;
			this.request_serial += 1;
			$(document).off(EVENT_NAMESPACE);
			if (this.timer) window.clearInterval(this.timer);
			$("#namar-my-followups-nav").remove();
		}
	}

	const test_hooks = window.__namar_my_followups_navbar_test__;
	if (test_hooks && typeof test_hooks === "object") {
		Object.assign(test_hooks, {
			NamarMyFollowupsNavbar,
			badge_view,
			badge_text,
			normalize_counts,
			valid_count,
		});
		if (test_hooks.skip_auto_start) return;
	}

	window[GLOBAL_KEY]?.destroy?.();
	const controller = new NamarMyFollowupsNavbar();
	window[GLOBAL_KEY] = controller;
	controller.start();
})();
