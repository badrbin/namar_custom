(function () {
  "use strict";

  const HISTORY_METHOD = "namar_test.comment_history.get_comment_edit_history";
  const INSTALL_FLAG = "__namar_comment_history_installed";
  const FOOTER_FLAG = "__namar_comment_history_patched";
  const STATE_KEY = "__namar_comment_history_state";

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

  function make_revision_content(label, content, modifier_class) {
    const wrapper = $(`<div class="namar-comment-history-change ${modifier_class}"></div>`);
    wrapper.append($('<div class="namar-comment-history-change-label"></div>').text(label));
    const content_wrapper = $('<div class="namar-comment-history-content"></div>');
    if (!content) {
      content_wrapper.addClass("namar-comment-history-content--empty").text("بدون نص");
      wrapper.append(content_wrapper);
      return wrapper;
    }
    content_wrapper.html(frappe.dom.remove_script_and_style(content));
    wrapper.append(content_wrapper);
    return wrapper;
  }

  function make_revision(revision) {
    const item = $('<section class="namar-comment-history-entry" dir="rtl"></section>');
    const header = $('<div class="namar-comment-history-entry-head"></div>');
    const label = `التعديل رقم ${revision.edit_number}`;
    const actor = revision.edited_by_full_name || revision.edited_by || "مستخدم غير معروف";
    const edited_at = format_datetime(revision.edited_at);

    header.append($("<strong></strong>").text(label));
    if (revision.is_earliest_recorded) {
      header.append(
        $('<span class="namar-comment-history-earliest"></span>').text("أقدم تعديل مسجل")
      );
    }
    header.append(
      $('<span class="namar-comment-history-meta"></span>').text(
        `بواسطة ${actor}${edited_at ? ` • ${edited_at}` : ""}`
      )
    );
    if (revision.impersonated_by) {
      header.append(
        $('<span class="namar-comment-history-audit"></span>').text(
          `دخول بالنيابة بواسطة ${
            revision.impersonated_by_full_name || revision.impersonated_by
          }`
        )
      );
    }
    if (revision.audit_user) {
      header.append(
        $('<span class="namar-comment-history-audit"></span>').text(
          `مستخدم التدقيق: ${revision.audit_user_full_name || revision.audit_user}`
        )
      );
    }
    item.append(header);
    const changes = $('<div class="namar-comment-history-changes"></div>');
    changes.append(
      make_revision_content(
        revision.is_earliest_recorded ? "أقدم نص مسجل قبل التعديل" : "قبل التعديل",
        revision.before_content,
        "namar-comment-history-change--before"
      )
    );
    changes.append(
      make_revision_content(
        "بعد التعديل",
        revision.after_content,
        "namar-comment-history-change--after"
      )
    );
    item.append(changes);
    return item;
  }

  function render_history(frm, histories) {
    if (!frm.timeline || !frm.timeline.timeline_items_wrapper) return;

    const timeline_wrapper = frm.timeline.timeline_items_wrapper;
    timeline_wrapper.find(".namar-comment-edited-badge, .namar-comment-history").remove();

    Object.entries(histories || {}).forEach(function ([comment_name, history]) {
      if (!history || !history.edit_count || !Array.isArray(history.revisions)) return;

      const timeline_item = find_comment_item(frm, comment_name);
      const message_box = timeline_item.find(".timeline-message-box").first();
      if (!message_box.length) return;

      const last_edited_at = format_datetime(history.last_edited_at);
      const last_editor = history.last_edited_by_full_name || history.last_edited_by || "";
      const badge = $('<span class="namar-comment-edited-badge" dir="rtl"></span>').text(
        `تم التعديل${last_edited_at ? ` • ${last_edited_at}` : ""}`
      );
      if (last_editor) {
        badge.attr("title", `آخر تعديل بواسطة ${last_editor}`);
      }
      message_box.children("span").first().find(".text-color").first().append(badge);

      const history_wrapper = $('<div class="namar-comment-history" dir="rtl"></div>');
      history_wrapper.append(
        $('<div class="namar-comment-history-title"></div>')
          .append($("<strong></strong>").text(`سجل التعديلات (${history.edit_count})`))
          .append($("<span></span>").text("يعرض التعديلات المتاحة والمسجلة في النظام"))
      );
      history.revisions.forEach(function (revision) {
        history_wrapper.append(make_revision(revision));
      });
      message_box.append(history_wrapper);
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
      .then(function (response) {
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
      })
      .catch(function (error) {
        console.warn("تعذر تحميل سجل تعديلات التعليقات", error);
      })
      .finally(function () {
        if (state.inflight === request) state.inflight = null;
        state.inflight_key = null;
        state.inflight_signature = null;
        if (state.pending_force) {
          state.pending_force = false;
          schedule_history_load(frm, true, true);
        }
      });
    state.inflight = request;
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

  let attempts = 0;
  const install_timer = setInterval(function () {
    attempts += 1;
    if (patch_form_footer() || attempts >= 100) clearInterval(install_timer);
  }, 50);
})();
