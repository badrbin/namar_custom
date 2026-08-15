(function () {
  "use strict";

  const HISTORY_METHOD = "namar_custom.comment_history.get_comment_edit_history";
  const INSTALL_FLAG = "__namar_comment_history_installed";
  const FOOTER_FLAG = "__namar_comment_history_patched";
  const STATE_KEY = "__namar_comment_history_state";
  const DRAWER_ID = "namar-comment-history-drawer";
  const DRAWER_TITLE_ID = "namar-comment-history-drawer-title";
  const drawer_state = {
    overlay: null,
    drawer: null,
    body: null,
    summary: null,
    close_button: null,
    trigger: null,
    close_timer: null,
  };

  function get_state(frm) {
    if (!frm[STATE_KEY]) {
      frm[STATE_KEY] = {
        timer: null,
        request_serial: 0,
        cache_key: null,
        cache_signature: null,
        cached_histories: null,
        inflight: null,
        inflight_key: null,
        inflight_signature: null,
        pending_force: false,
      };
    }
    return frm[STATE_KEY];
  }

  function get_comment_count(frm) {
    const docinfo = frm && frm.get_docinfo ? frm.get_docinfo() : null;
    return docinfo && Array.isArray(docinfo.comments) ? docinfo.comments.length : 0;
  }

  function get_comment_signature(frm) {
    const docinfo = frm && frm.get_docinfo ? frm.get_docinfo() : null;
    const comments = docinfo && Array.isArray(docinfo.comments) ? docinfo.comments : [];
    return JSON.stringify(
      comments.map(function (comment) {
        return [comment.name || "", comment.content || "", comment.published || 0];
      })
    );
  }

  function get_comment(frm, comment_name) {
    const docinfo = frm && frm.get_docinfo ? frm.get_docinfo() : null;
    const comments = docinfo && Array.isArray(docinfo.comments) ? docinfo.comments : [];
    return (
      comments.find(function (comment) {
        return comment.name === comment_name;
      }) || null
    );
  }

  function get_user_full_name(user) {
    if (!user || typeof frappe.user_info !== "function") return "";
    const user_info = frappe.user_info(user);
    return (user_info && user_info.fullname) || "";
  }

  function format_datetime(value) {
    if (!value) return "";
    try {
      return frappe.datetime.str_to_user(value);
    } catch (error) {
      return String(value);
    }
  }

  function find_comment_item(frm, comment_name) {
    if (!frm.timeline || !frm.timeline.timeline_items_wrapper) return $();
    return frm.timeline.timeline_items_wrapper
      .find('.timeline-item[data-doctype="Comment"]')
      .filter(function () {
        return $(this).attr("data-name") === comment_name;
      })
      .first();
  }

  function format_edit_count(count) {
    if (count === 1) return "تم التعديل مرة واحدة";
    if (count === 2) return "تم التعديل مرتين";
    if (count >= 3 && count <= 10) return `تم التعديل ${count} مرات`;
    return `تم التعديل ${count} مرة`;
  }

  function build_snapshots(history, comment) {
    const revisions = Array.isArray(history && history.revisions)
      ? history.revisions.filter(Boolean)
      : [];
    if (!revisions.length) return [];

    const total_versions = revisions.length + 1;
    const latest_revision = revisions[0];
    const snapshots = [
      {
        kind: "current",
        label: "النسخة الحالية",
        content: (comment && comment.content) || latest_revision.after_content,
        actor: latest_revision.edited_by,
        actor_full_name: latest_revision.edited_by_full_name,
        created_at: latest_revision.edited_at,
        audit_revision: latest_revision,
      },
    ];

    revisions.forEach(function (revision, index) {
      const producer_candidate = revisions[index + 1] || null;
      const producer =
        producer_candidate && producer_candidate.after_content === revision.before_content
          ? producer_candidate
          : null;
      const is_oldest = index === revisions.length - 1;
      const oldest_owner = is_oldest && comment ? comment.owner : "";
      snapshots.push({
        kind: is_oldest ? "oldest" : "previous",
        label: is_oldest
          ? "أقدم نسخة مسجلة"
          : `النسخة ${total_versions - index - 1} من ${total_versions}`,
        content: revision.before_content,
        actor: producer ? producer.edited_by : oldest_owner,
        actor_full_name: producer
          ? producer.edited_by_full_name
          : (comment && comment.user_full_name) || get_user_full_name(oldest_owner),
        created_at: producer ? producer.edited_at : comment && comment.creation,
        actor_context: is_oldest && oldest_owner ? "صاحب التعليق" : "",
        audit_revision: producer,
      });
    });

    return snapshots;
  }

  function make_avatar(user, full_name, neutral) {
    const wrapper = $('<span class="namar-comment-history-avatar" aria-hidden="true"></span>');
    const label = neutral ? "نسخة مسجلة" : full_name || user || "مستخدم غير معروف";
    let avatar_html = "";
    if (!neutral && user && typeof frappe.avatar === "function") {
      avatar_html = frappe.avatar(user, "avatar-medium", label);
    } else if (typeof frappe.get_avatar === "function") {
      avatar_html = frappe.get_avatar("avatar-medium", label, null, neutral);
    }
    if (avatar_html) wrapper.append(avatar_html);
    return wrapper;
  }

  function append_audit_note(wrapper, revision) {
    if (!revision) return;
    if (revision.impersonated_by) {
      wrapper.append(
        $('<div class="namar-comment-history-audit"></div>').text(
          `دخول بالنيابة بواسطة ${
            revision.impersonated_by_full_name || revision.impersonated_by
          }`
        )
      );
    }
    if (revision.audit_user) {
      wrapper.append(
        $('<div class="namar-comment-history-audit"></div>').text(
          `مستخدم التدقيق: ${revision.audit_user_full_name || revision.audit_user}`
        )
      );
    }
  }

  function make_snapshot(snapshot) {
    const item = $('<li class="namar-comment-history-snapshot" dir="rtl"></li>');
    item.addClass(`namar-comment-history-snapshot--${snapshot.kind}`);
    if (snapshot.kind === "current") item.attr("aria-current", "true");
    const article = $("<article></article>");

    const header = $('<div class="namar-comment-history-snapshot-head"></div>');
    const identity = $('<div class="namar-comment-history-identity"></div>');
    const actor = snapshot.actor_full_name || snapshot.actor || "نسخة مسجلة";
    const created_at = format_datetime(snapshot.created_at);
    identity.append(make_avatar(snapshot.actor, snapshot.actor_full_name, !snapshot.actor));

    const identity_text = $('<div class="namar-comment-history-identity-text"></div>');
    identity_text.append($("<strong></strong>").text(actor));
    const meta = $('<span class="namar-comment-history-meta"></span>');
    if (snapshot.actor_context) {
      meta.append($("<span></span>").text(snapshot.actor_context));
    }
    if (snapshot.actor_context && created_at) {
      meta.append($('<span aria-hidden="true">•</span>'));
    }
    if (created_at) {
      meta.append(
        $("<time></time>")
          .attr("datetime", String(snapshot.created_at).replace(" ", "T"))
          .text(created_at)
      );
    }
    if (!snapshot.actor_context && !created_at) meta.text("وقت هذه النسخة غير مسجل");
    identity_text.append(meta);
    identity.append(identity_text);
    header.append(identity);
    header.append(
      $('<span class="namar-comment-history-version-label"></span>').text(snapshot.label)
    );
    article.append(header);

    const content_wrapper = $('<div class="namar-comment-history-content"></div>');
    if (!snapshot.content) {
      content_wrapper.addClass("namar-comment-history-content--empty").text("بدون نص");
    } else {
      content_wrapper.html(frappe.dom.remove_script_and_style(snapshot.content));
    }
    article.append(content_wrapper);
    append_audit_note(article, snapshot.audit_revision);
    item.append(article);
    return item;
  }

  function get_drawer_focusables() {
    if (!drawer_state.drawer) return $();
    return drawer_state.drawer
      .find('button:not([disabled]), a[href], input:not([disabled]), [tabindex]:not([tabindex="-1"])')
      .filter(":visible");
  }

  function close_history_drawer(restore_focus, immediate) {
    if (!drawer_state.overlay) return;
    if (drawer_state.close_timer) clearTimeout(drawer_state.close_timer);

    const trigger = drawer_state.trigger;
    let finished = false;
    drawer_state.drawer.off("transitionend.namarCommentHistory");
    drawer_state.overlay.removeClass("is-open");

    const finish_close = function () {
      if (finished) return;
      finished = true;
      drawer_state.drawer.off("transitionend.namarCommentHistory");
      if (drawer_state.close_timer) clearTimeout(drawer_state.close_timer);
      drawer_state.overlay.attr("aria-hidden", "true");
      drawer_state.overlay.attr("hidden", true);
      drawer_state.body.empty();
      if (trigger) trigger.attr("aria-expanded", "false");
      $(document.body).removeClass("namar-comment-history-open");
      if (drawer_state.trigger === trigger) drawer_state.trigger = null;
      drawer_state.close_timer = null;
      if (restore_focus && trigger && $.contains(document, trigger[0])) {
        trigger.trigger("focus");
      }
    };
    if (immediate) {
      finish_close();
    } else {
      drawer_state.drawer.on("transitionend.namarCommentHistory", function (event) {
        const transition_event = event.originalEvent || event;
        if (event.target !== drawer_state.drawer[0]) return;
        if (transition_event.propertyName !== "transform") return;
        finish_close();
      });
      drawer_state.close_timer = setTimeout(finish_close, 240);
    }
  }

  function ensure_drawer() {
    if (drawer_state.overlay && drawer_state.overlay.length) return;

    const overlay = $(
      '<div class="namar-comment-history-overlay" aria-hidden="true" hidden></div>'
    );
    const backdrop = $('<div class="namar-comment-history-backdrop" aria-hidden="true"></div>');
    const drawer = $(
      `<aside class="namar-comment-history-drawer" id="${DRAWER_ID}" role="dialog" aria-modal="true" aria-labelledby="${DRAWER_TITLE_ID}" dir="rtl" tabindex="-1"></aside>`
    );
    const header = $('<div class="namar-comment-history-drawer-head"></div>');
    const title_group = $('<div class="namar-comment-history-drawer-title"></div>');
    title_group.append($(`<h2 id="${DRAWER_TITLE_ID}"></h2>`).text("سجل التعديلات"));
    const summary = $('<p class="namar-comment-history-drawer-summary"></p>');
    title_group.append(summary);

    const close_button = $(
      '<button type="button" class="namar-comment-history-close" aria-label="إغلاق سجل التعديلات"></button>'
    );
    if (frappe.utils && typeof frappe.utils.icon === "function") {
      close_button.html(frappe.utils.icon("close", "sm"));
    } else {
      close_button.text("إغلاق");
    }
    header.append(title_group, close_button);

    const body = $('<ol class="namar-comment-history-drawer-body"></ol>');
    drawer.append(header, body);
    overlay.append(backdrop, drawer);
    $(document.body).append(overlay);

    backdrop.on("click", function () {
      close_history_drawer(true, false);
    });
    close_button.on("click", function () {
      close_history_drawer(true, false);
    });
    overlay.on("keydown", function (event) {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        close_history_drawer(true, false);
        return;
      }
      if (event.key !== "Tab") return;
      const focusables = get_drawer_focusables();
      if (!focusables.length) {
        event.preventDefault();
        drawer.trigger("focus");
        return;
      }
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        $(last).trigger("focus");
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        $(first).trigger("focus");
      }
    });

    drawer_state.overlay = overlay;
    drawer_state.drawer = drawer;
    drawer_state.body = body;
    drawer_state.summary = summary;
    drawer_state.close_button = close_button;
  }

  function open_history_drawer(history, comment, trigger) {
    ensure_drawer();
    if (drawer_state.close_timer) {
      clearTimeout(drawer_state.close_timer);
      drawer_state.close_timer = null;
    }
    drawer_state.drawer.off("transitionend.namarCommentHistory");
    if (drawer_state.trigger && drawer_state.trigger[0] !== trigger[0]) {
      drawer_state.trigger.attr("aria-expanded", "false");
    }

    drawer_state.trigger = trigger;
    drawer_state.body.empty();
    drawer_state.summary.text(`${format_edit_count(history.edit_count)} • الأحدث أولًا`);
    build_snapshots(history, comment).forEach(function (snapshot) {
      drawer_state.body.append(make_snapshot(snapshot));
    });
    trigger.attr("aria-expanded", "true");
    drawer_state.overlay.removeAttr("hidden").attr("aria-hidden", "false");
    $(document.body).addClass("namar-comment-history-open");
    drawer_state.overlay[0].offsetWidth;
    drawer_state.overlay.addClass("is-open");
    drawer_state.close_button.trigger("focus");
  }

  function render_history(frm, histories) {
    if (!frm.timeline || !frm.timeline.timeline_items_wrapper) return;

    const timeline_wrapper = frm.timeline.timeline_items_wrapper;
    close_history_drawer(false, true);
    timeline_wrapper
      .find(
        ".namar-comment-edited-badge, .namar-comment-history, .namar-comment-history-trigger"
      )
      .remove();

    Object.entries(histories || {}).forEach(function ([comment_name, history]) {
      if (!history || !history.edit_count || !Array.isArray(history.revisions)) return;

      const timeline_item = find_comment_item(frm, comment_name);
      const message_box = timeline_item.find(".timeline-message-box").first();
      if (!message_box.length) return;
      const timeline_content = timeline_item.find(".timeline-content.frappe-card").first();
      const comment = get_comment(frm, comment_name);

      ensure_drawer();
      const trigger = $(
        `<button type="button" class="namar-comment-history-trigger" aria-haspopup="dialog" aria-controls="${DRAWER_ID}" aria-expanded="false" dir="rtl"></button>`
      );
      trigger
        .append(
          $('<span class="namar-comment-history-trigger-count"></span>').text(
            format_edit_count(history.edit_count)
          )
        )
        .append(
          $('<span class="namar-comment-history-trigger-separator" aria-hidden="true">•</span>')
        )
        .append(
          $('<span class="namar-comment-history-trigger-action"></span>').text("عرض السجل")
        );
      const last_editor = history.last_edited_by_full_name || history.last_edited_by || "";
      if (last_editor) trigger.attr("title", `آخر تعديل بواسطة ${last_editor}`);
      trigger.on("click", function () {
        open_history_drawer(history, comment, trigger);
      });
      if (timeline_content.length) {
        trigger.insertAfter(timeline_content);
      } else {
        message_box.append(trigger);
      }
    });
  }

  function load_history(frm, force) {
    if (!frm) return Promise.resolve();
    const state = get_state(frm);
    if (
      (typeof frm.is_new === "function" && frm.is_new()) ||
      !frm.doctype ||
      !frm.docname ||
      !get_comment_count(frm)
    ) {
      state.cache_key = null;
      state.cache_signature = null;
      state.cached_histories = null;
      if (frm && frm.timeline) render_history(frm, {});
      return Promise.resolve();
    }

    const document_key = `${frm.doctype}::${frm.docname}`;
    const comment_signature = get_comment_signature(frm);
    if (
      !force &&
      state.cache_key === document_key &&
      state.cache_signature === comment_signature &&
      state.cached_histories !== null
    ) {
      render_history(frm, state.cached_histories);
      return Promise.resolve();
    }

    if (state.inflight) {
      if (
        force ||
        state.inflight_key !== document_key ||
        state.inflight_signature !== comment_signature
      ) {
        state.pending_force = true;
      }
      return state.inflight;
    }

    const request_serial = ++state.request_serial;
    state.inflight_key = document_key;
    state.inflight_signature = comment_signature;

    const request = frappe
      .call({
        method: HISTORY_METHOD,
        args: {
          reference_doctype: frm.doctype,
          reference_name: frm.docname,
        },
        freeze: false,
      })
      .then(
        function (response) {
          if (get_comment_signature(frm) !== comment_signature) {
            state.pending_force = true;
            return;
          }
          if (state.pending_force) return;
          if (request_serial !== state.request_serial) return;
          if (`${frm.doctype}::${frm.docname}` !== document_key) return;
          const histories = (response.message && response.message.histories) || {};
          state.cache_key = document_key;
          state.cache_signature = comment_signature;
          state.cached_histories = histories;
          render_history(frm, histories);
        },
        function (error) {
          console.warn("تعذر تحميل سجل تعديلات التعليقات", error);
        }
      );
    state.inflight = request;

    const finish_request = function () {
      if (state.inflight === request) state.inflight = null;
      state.inflight_key = null;
      state.inflight_signature = null;
      if (state.pending_force) {
        state.pending_force = false;
        schedule_history_load(frm, true, true);
      }
    };
    if (typeof request.always === "function") {
      request.always(finish_request);
    } else {
      request.then(finish_request, finish_request);
    }
    return request;
  }

  function schedule_history_load(frm, immediate, force) {
    const state = get_state(frm);
    if (state.timer) clearTimeout(state.timer);
    state.timer = setTimeout(
      function () {
        state.timer = null;
        load_history(frm, force);
      },
      immediate ? 0 : 80
    );
  }

  function install_on_form(frm) {
    if (!frm || !frm.timeline || frm[INSTALL_FLAG]) return;

    const timeline = frm.timeline;
    if (typeof timeline.render_timeline_items !== "function") return;
    frm[INSTALL_FLAG] = true;

    const original_render = timeline.render_timeline_items.bind(timeline);
    timeline.render_timeline_items = function () {
      const result = original_render.apply(timeline, arguments);
      schedule_history_load(frm, false, false);
      return result;
    };

    if (typeof timeline.add_timeline_item === "function") {
      const original_add = timeline.add_timeline_item.bind(timeline);
      timeline.add_timeline_item = function (item) {
        const result = original_add.apply(timeline, arguments);
        if (item && item.doctype === "Comment") schedule_history_load(frm, false, false);
        return result;
      };
    }

    if (typeof timeline.update_comment === "function") {
      const original_update = timeline.update_comment.bind(timeline);
      timeline.update_comment = function () {
        return original_update.apply(timeline, arguments).then(function (result) {
          schedule_history_load(frm, true, true);
          return result;
        });
      };
    }

    schedule_history_load(frm, true, false);
  }

  function patch_form_footer() {
    if (!frappe.ui || !frappe.ui.form || !frappe.ui.form.Footer) return false;

    const footer = frappe.ui.form.Footer.prototype;
    if (!footer[FOOTER_FLAG]) {
      const original_make_timeline = footer.make_timeline;
      if (typeof original_make_timeline !== "function") return false;
      footer.make_timeline = function () {
        const result = original_make_timeline.apply(this, arguments);
        install_on_form(this.frm);
        return result;
      };
      footer[FOOTER_FLAG] = true;
    }

    if (window.cur_frm && window.cur_frm.timeline) install_on_form(window.cur_frm);
    return true;
  }

  if (window.__namar_comment_history_test__) {
    Object.assign(window.__namar_comment_history_test__, {
      build_snapshots,
      format_edit_count,
      load_history,
    });
  }

  let attempts = 0;
  const install_timer = setInterval(function () {
    attempts += 1;
    if (patch_form_footer() || attempts >= 100) clearInterval(install_timer);
  }, 50);
})();
