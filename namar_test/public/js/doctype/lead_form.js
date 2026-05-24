/* Auto-generated from live Client Script records on testnamar.u.frappe.cloud. */
(function () {
  if (!window.frappe || !frappe.boot || !frappe.boot.namar_test_client_scripts_enabled) {
    return;
  }
  window.__namar_test_loaded_scripts = window.__namar_test_loaded_scripts || {};
  if (!window.__namar_test_loaded_scripts["Lead Google Map Coordinates"]) {
    window.__namar_test_loaded_scripts["Lead Google Map Coordinates"] = true;
    // BEGIN legacy Client Script: Lead Google Map Coordinates
    frappe.ui.form.on("Lead", {
        refresh: function(frm) {
            leadGoogleMapRememberLoadedValue(frm);
            if (frm.doc.custom_google_map) {
                frm.add_custom_button("تحديث الموقع من رابط قوقل", function() {
                    return leadGoogleMapResolve(frm, { force: true, notify: true });
                }, "خريطة الموقع");
            }
        },

        custom_google_map: function(frm) {
            return leadGoogleMapResolve(frm, { force: true, notify: true });
        },

        before_save: function(frm) {
            if (!frm.doc.custom_google_map) return;
            if (leadGoogleMapLinkChanged(frm) || !leadGoogleMapHasCoords(frm)) {
                return leadGoogleMapResolve(frm, { force: true, notify: false });
            }
        },
    });

    function leadGoogleMapRememberLoadedValue(frm) {
        frm.__lead_google_map_loaded_url = leadGoogleMapNormalize(frm.doc.custom_google_map);
        frm.__lead_google_map_loaded_lat = frm.doc.custom_latitude;
        frm.__lead_google_map_loaded_lng = frm.doc.custom_longitude;
    }

    function leadGoogleMapNormalize(value) {
        return (value || "").toString().trim();
    }

    function leadGoogleMapLinkChanged(frm) {
        return leadGoogleMapNormalize(frm.doc.custom_google_map) !== leadGoogleMapNormalize(frm.__lead_google_map_loaded_url);
    }

    function leadGoogleMapHasCoords(frm) {
        return leadGoogleMapIsValidCoord(parseFloat(frm.doc.custom_latitude), parseFloat(frm.doc.custom_longitude));
    }

    function leadGoogleMapIsValidCoord(lat, lng) {
        return !isNaN(lat) && !isNaN(lng) && lat >= 15 && lat <= 33 && lng >= 34 && lng <= 57;
    }

    function leadGoogleMapTryPair(text) {
        var parts = (text || "").trim().split(",");
        if (parts.length !== 2) return null;
        var lat = parseFloat(parts[0]);
        var lng = parseFloat(parts[1]);
        if (!leadGoogleMapIsValidCoord(lat, lng)) return null;
        return { lat: lat, lng: lng };
    }

    function leadGoogleMapDecode(value) {
        try {
            return decodeURIComponent((value || "").toString());
        } catch (e) {
            return (value || "").toString();
        }
    }

    function leadGoogleMapFindParam(text, param) {
        var marker = param + "=";
        var idx = text.indexOf(marker);
        if (idx < 0) return "";
        var rest = text.slice(idx + marker.length);
        ["&", "#", " ", ";"].some(function(delim) {
            var pos = rest.indexOf(delim);
            if (pos >= 0) {
                rest = rest.slice(0, pos);
                return true;
            }
            return false;
        });
        return rest.trim();
    }

    function leadGoogleMapExtractLocal(url) {
        var text = leadGoogleMapDecode(url).replace(/\+/g, " ").trim();
        if (!text) return null;

        var direct = leadGoogleMapTryPair(text);
        if (direct) return direct;

        var params = ["q", "query", "destination", "daddr", "ll", "saddr"];
        for (var i = 0; i < params.length; i++) {
            var pair = leadGoogleMapTryPair(leadGoogleMapDecode(leadGoogleMapFindParam(text, params[i])).replace(/\+/g, " "));
            if (pair) return pair;
        }

        var patterns = [
            /@(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)/,
            /!3d(-?\d+(?:\.\d+)?)!4d(-?\d+(?:\.\d+)?)/,
            /\/place\/(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)/,
        ];
        for (var j = 0; j < patterns.length; j++) {
            var match = text.match(patterns[j]);
            if (!match) continue;
            var lat = parseFloat(match[1]);
            var lng = parseFloat(match[2]);
            if (leadGoogleMapIsValidCoord(lat, lng)) return { lat: lat, lng: lng };
        }

        return null;
    }

    function leadGoogleMapApplyCoords(frm, coords, notify) {
        if (!coords || !leadGoogleMapIsValidCoord(parseFloat(coords.lat), parseFloat(coords.lng))) {
            return Promise.resolve(false);
        }
        return frm.set_value({
            custom_latitude: coords.lat,
            custom_longitude: coords.lng,
        }).then(function() {
            frm.__lead_google_map_loaded_url = leadGoogleMapNormalize(frm.doc.custom_google_map);
            if (notify) {
                frappe.show_alert({ message: "تم تحديث إحداثيات الليد من رابط قوقل ماب", indicator: "green" }, 5);
            }
            return true;
        });
    }

    function leadGoogleMapResolve(frm, options) {
        options = options || {};
        var url = leadGoogleMapNormalize(frm.doc.custom_google_map);
        if (!url) return Promise.resolve(false);

        var localCoords = leadGoogleMapExtractLocal(url);
        if (localCoords) {
            return leadGoogleMapApplyCoords(frm, localCoords, options.notify);
        }

        frm.__lead_google_map_resolving = true;
        return new Promise(function(resolve) {
            frappe.call({
                method: "resolve_map_url",
                args: { url: url },
                freeze: true,
                freeze_message: "جاري قراءة رابط قوقل ماب...",
                callback: function(r) {
                    var message = r && r.message ? r.message : {};
                    if (message.lat && message.lng) {
                        leadGoogleMapApplyCoords(frm, message, options.notify).then(resolve);
                        return;
                    }
                    if (options.notify) {
                        frappe.msgprint({
                            title: __("تعذر قراءة رابط قوقل ماب"),
                            indicator: "red",
                            message: '<div dir="rtl" style="text-align:right">لم أستطع استخراج الموقع من الرابط. تأكد أن الرابط يفتح موقعًا واضحًا في Google Maps.</div>',
                        });
                    }
                    resolve(false);
                },
                error: function() {
                    if (options.notify) {
                        frappe.msgprint({
                            title: __("تعذر قراءة رابط قوقل ماب"),
                            indicator: "red",
                            message: '<div dir="rtl" style="text-align:right">تعذر الاتصال بخدمة قراءة رابط قوقل ماب. حاول مرة أخرى.</div>',
                        });
                    }
                    resolve(false);
                },
            });
        }).then(function(result) {
            frm.__lead_google_map_resolving = false;
            return result;
        }, function(err) {
            frm.__lead_google_map_resolving = false;
            throw err;
        });
    }
    // END legacy Client Script: Lead Google Map Coordinates
  }
})();
