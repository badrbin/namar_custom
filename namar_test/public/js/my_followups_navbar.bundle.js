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
			href: "/app/my-followups?source=mentions",
		},
		followups: {
			label: "المتابعات",
			attention_label: "المتابعات المتأخرة",
			href: "/app/my-followups?source=followups&bucket=overdue",
		},
		approvals: {
			label: "الموافقات",
			attention_label: "الموافقات المعلقة",
			href: "/app/my-followups?source=approvals",
		},
	};
	const REFRESH_TTL_MS = 2 * 60 * 1000;
	const POLL_INTERVAL_MS = 3 * 60 * 1000;
	const APPROVAL_REFRESH_EVENT = "namar_approvals_changed";
	const APPROVAL_RETRY_MIN_MS = 30 * 1000;
	const APPROVAL_RETRY_MAX_MS = 2 * 60 * 1000;

	function valid_count(value) {
		return Number.isInteger(value) && value >= 0;
	}

	function normalize_counts(response) {
		const message = response?.message ?? response;
		const raw = message?.attention_counts;
		if (!raw || typeof raw !== "object") return null;
		const approval_status = message.approval_status === undefined ? "ready" : message.approval_status;
		if (!["ready", "updating", "error"].includes(approval_status)) return null;
		if (!["mentions", "followups"].every((key) => valid_count(raw[key]))) return null;
		if (approval_status === "ready") {
			if (!valid_count(raw.approvals) || !valid_count(raw.total)) return null;
			const expected_total = SOURCE_KEYS.reduce((total, key) => total + raw[key], 0);
			if (raw.total !== expected_total) return null;
		} else if (raw.approvals !== null || raw.total !== null) {
			// An unavailable generation must never masquerade as zero or an old count.
			return null;
		}
		return {
			mentions: raw.mentions,
			followups: raw.followups,
			approvals: raw.approvals,
			total: raw.total,
			approval_status,
		};
	}

	function badge_text(total) {
		return total > 99 ? "99+" : String(total);
	}

	function source_status_label(source, count) {
		const meta = SOURCE_META[source];
		return valid_count(count) ? `${meta.attention_label}: ${count}` : `${meta.label}: لم يُحدّث العداد بعد`;
	}

	function badge_view(counts) {
		if (!counts) return null;
		const sources = SOURCE_KEYS.map((source) => {
			const count = counts[source];
			const approval_status = counts.approval_status ?? "ready";
			if (source === "approvals" && approval_status !== "ready") {
				return {
					source,
					count: null,
					visible: true,
					text: approval_status === "error" ? "—" : "…",
					label: approval_status === "error" ? "الموافقات: تعذر تحديث العداد" : "الموافقات: جار تحديث الموافقات",
					href: SOURCE_META[source].href,
				};
			}
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

	function is_plain_navigation(event) {
		if (!event) return true;
		return (event.button === undefined || event.button === 0)
			&& !event.metaKey
			&& !event.ctrlKey
			&& !event.shiftKey
			&& !event.altKey;
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
			this.approval_revision = 0;
			this.approval_refresh_timer = null;
			this.approval_retry_attempt = 0;
			this.last_requested_at = 0;
			this.realtime_handler = () => this.invalidate_approvals();
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
			frappe.realtime?.on?.(APPROVAL_REFRESH_EVENT, this.realtime_handler);
			this.timer = window.setInterval(() => {
				if (!document.hidden) this.refresh();
			}, POLL_INTERVAL_MS);
		}

		bind_navigation($node) {
			[".namar-my-followups-link", ".namar-my-followups-source-badge"].forEach((selector) => {
				$node
					.off(`click${EVENT_NAMESPACE}`, selector)
					.on(`click${EVENT_NAMESPACE}`, selector, (event) => {
						if (!is_plain_navigation(event)) return;
						const href = event.currentTarget?.getAttribute?.("href");
						if (!href) return;
						event.preventDefault();
						event.stopImmediatePropagation?.();
						event.stopPropagation?.();
						window.location.assign(href);
					});
			});
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
			this.bind_navigation($node);
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
			if (source === "approvals" && ["updating", "error"].includes(payload?.approval_status)) {
				this.invalidate_approvals(payload.approval_status);
				return;
			}
			if (!SOURCE_KEYS.includes(source) || !valid_count(count)) return;
			if (source === "approvals") {
				// Page totals may be filtered or belong to a superseded generation.
				// Only the unified counter endpoint may publish the navbar number.
				this.invalidate_approvals();
				return;
			}
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
			this.counts.total = SOURCE_KEYS.every((key) => valid_count(this.counts[key]))
				? SOURCE_KEYS.reduce((total, key) => total + this.counts[key], 0) : null;
			this.last_loaded_at = Date.now();
			this.render();
		}

		mark_approvals_unavailable(status) {
			this.counts = {
				mentions: this.counts?.mentions ?? null,
				followups: this.counts?.followups ?? null,
				approvals: null,
				total: null,
				approval_status: status,
			};
			this.render();
		}

		invalidate_approvals(status = "updating") {
			if (this.destroyed) return;
			this.approval_revision += 1;
			this.mark_approvals_unavailable(status);
			this.last_loaded_at = 0;
			this.schedule_approval_refresh();
		}

		clear_approval_refresh() {
			if (this.approval_refresh_timer !== null) window.clearTimeout(this.approval_refresh_timer);
			this.approval_refresh_timer = null;
		}

		schedule_approval_refresh(retry = false) {
			if (this.destroyed || this.approval_refresh_timer !== null) return;
			// Coalesce event bursts, add per-tab jitter, and never retry an unavailable
			// generation faster than once per 30 seconds. No private counts are stored.
			const backoff = retry
				? Math.min(APPROVAL_RETRY_MAX_MS, APPROVAL_RETRY_MIN_MS * (2 ** this.approval_retry_attempt++))
				: 1000;
			const delay = Math.max(backoff, this.last_requested_at + APPROVAL_RETRY_MIN_MS - Date.now())
				+ Math.floor(Math.random() * 5000);
			this.approval_refresh_timer = window.setTimeout(() => {
				this.approval_refresh_timer = null;
				this.refresh(true);
			}, delay);
		}

		refresh(force = false) {
			if (this.destroyed || document.hidden) return Promise.resolve(null);
			if (this.counts?.approval_status && this.counts.approval_status !== "ready"
				&& Date.now() - this.last_requested_at < APPROVAL_RETRY_MIN_MS) {
				this.schedule_approval_refresh();
				return this.pending || Promise.resolve(this.counts);
			}
			if (!force && this.counts && (this.counts.approval_status ?? "ready") === "ready"
				&& Date.now() - this.last_loaded_at < REFRESH_TTL_MS) {
				return Promise.resolve(this.counts);
			}
			if (this.pending) {
				if (force) this.force_after_pending = true;
				return this.pending;
			}

			const serial = ++this.request_serial;
			const approval_revision = this.approval_revision;
			this.last_requested_at = Date.now();
			this.clear_approval_refresh();
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
					if (approval_revision !== this.approval_revision) {
						// A notification received during the request invalidates its approval
						// snapshot, but does not throw away the other two valid counters.
						counts.approvals = this.counts?.approvals ?? null;
						counts.approval_status = this.counts?.approval_status ?? "updating";
						counts.total = valid_count(counts.approvals)
							? counts.mentions + counts.followups + counts.approvals : null;
					}
					this.counts = counts;
					this.load_failed = false;
					this.last_loaded_at = Date.now();
					this.render();
					if (counts.approval_status !== "ready") this.schedule_approval_refresh(true);
					else {
						this.approval_retry_attempt = 0;
						this.clear_approval_refresh();
					}
					return counts;
				})
				.catch((error) => {
					if (!this.destroyed && serial === this.request_serial) {
						this.load_failed = true;
						if (approval_revision === this.approval_revision) this.mark_approvals_unavailable("error");
						this.schedule_approval_refresh(true);
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
			this.clear_approval_refresh();
			frappe.realtime?.off?.(APPROVAL_REFRESH_EVENT, this.realtime_handler);
			$("#namar-my-followups-nav").remove();
		}
	}

	const test_hooks = window.__namar_my_followups_navbar_test__;
	if (test_hooks && typeof test_hooks === "object") {
		Object.assign(test_hooks, {
			NamarMyFollowupsNavbar,
			badge_view,
			badge_text,
			is_plain_navigation,
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
