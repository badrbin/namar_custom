frappe.ui.form.on("Material Request", {
    custom_google_map: function(frm) {
        if (!frm.doc.custom_google_map) {
            frm.set_value("custom_latitude", 0);
            frm.set_value("custom_longitude", 0);
            if (frm.doc.docstatus === 1) frm.save("Update");
            return;
        }
        var url = frm.doc.custom_google_map.trim();
        var coords = extract_coordinates(url);
        if (coords) {
            frm.set_value("custom_latitude", coords.lat);
            frm.set_value("custom_longitude", coords.lng);
            if (frm.doc.docstatus === 1) frm.save("Update");
            return;
        }
        frappe.call({
            method: "resolve_map_url",
            args: { url: url },
            callback: function(r) {
                if (r.message && r.message.lat && r.message.lng) {
                    frm.set_value("custom_latitude", r.message.lat);
                    frm.set_value("custom_longitude", r.message.lng);
                    if (frm.doc.docstatus === 1) frm.save("Update");
                } else {
                    frappe.msgprint({
                        title: __("تنبيه"),
                        indicator: "orange",
                        message: '<div dir="rtl">تعذر استخراج الإحداثيات من الرابط. تأكد أن الرابط صحيح.</div>'
                    });
                }
            }
        });
    }
});

function extract_coordinates(url) {
    if (!url) return null;
    var patterns = [
        /daddr=([-\d.]+),([-\d.]+)/,
        /!3d([-\d.]+)!4d([-\d.]+)/,
        /[?&]q=([-\d.]+),([-\d.]+)/,
        /@([-\d.]+),([-\d.]+)/,
        /place\/([-\d.]+),([-\d.]+)/,
        /ll=([-\d.]+),([-\d.]+)/,
        /saddr=([-\d.]+),([-\d.]+)/,
        /^([-\d.]+),\s*([-\d.]+)$/
    ];
    for (var i = 0; i < patterns.length; i++) {
        var match = url.match(patterns[i]);
        if (match) {
            var lat = parseFloat(match[1]), lng = parseFloat(match[2]);
            if (lat >= 15 && lat <= 33 && lng >= 34 && lng <= 57) {
                return { lat: lat, lng: lng };
            }
        }
    }
    return null;
}
