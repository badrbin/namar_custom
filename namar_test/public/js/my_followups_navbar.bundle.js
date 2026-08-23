(() => {
	"use strict";

	const GLOBAL_KEY = "__namar_my_followups_navbar";
	const EVENT_NAMESPACE = ".namarMyFollowupsNavbar";
	const COUNT_METHOD = "namar_test.followups.api.get_my_followups_counts";
	const SOURCE_KEYS = ["mentions", "followups", "approvals"];
	const REFRESH_TTL_MS = 2 * 60 * 1000;
	const POLL_INTERVAL_MS = 3 * 60 * 1000;

	function valid_count(value) {
		return Number.isInteger(value) && value >= 0;
	}

	function normalize_counts(response) {
		const message = response?.message ?? response;
		const raw = message?.counts;
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
			const $notifications = $("header .navbar .dropdown-notifications").first();
			if (!$notifications.length) return null;

			let $node = $("#namar-my-followups-nav");
			if ($node.length > 1) {
				$node.slice(1).remove();
				$node = $node.first();
			}
			if (!$node.length) {
				$node = $(
					`<li id="namar-my-followups-nav" class="nav-item namar-my-followups-nav">
						<a class="nav-link namar-my-followups-link" href="/app/my-followups" dir="rtl" aria-label="متابعاتي">
							<span class="namar-my-followups-icon" aria-hidden="true">
								<svg class="es-icon icon-sm"><use href="#es-line-inbox"></use></svg>
							</span>
							<span class="namar-my-followups-label">متابعاتي</span>
							<bdi class="namar-my-followups-badge" dir="ltr" hidden aria-hidden="true"></bdi>
						</a>
					</li>`
				);
				$notifications.after($node);
			}
			this.render();
			this.update_active_state();
			return $node;
		}

		update_active_state() {
			const active = frappe.get_route_str?.() === "my-followups";
			const $link = $("#namar-my-followups-nav .namar-my-followups-link");
			$link.toggleClass("is-active", active);
			if (active) $link.attr("aria-current", "page");
			else $link.removeAttr("aria-current");
		}

		merge_source_count(payload) {
			const source = payload?.source;
			const count = payload?.count;
			if (!SOURCE_KEYS.includes(source) || !valid_count(count)) return;
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
			const $link = $("#namar-my-followups-nav .namar-my-followups-link");
			if (!$link.length) return;
			const total = this.counts?.total;
			const visible = valid_count(total) && total > 0;
			const status_label = this.counts
				? (visible ? `متابعاتي، ${total} عناصر مفتوحة` : "متابعاتي، لا توجد عناصر مفتوحة")
				: (this.load_failed ? "متابعاتي، تعذر تحديث العداد" : "متابعاتي، جار تحديث العداد");
			$link.attr(
				"aria-label",
				status_label
			);
			$link.find(".namar-my-followups-badge")
				.prop("hidden", !visible)
				.text(visible ? badge_text(total) : "");
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
