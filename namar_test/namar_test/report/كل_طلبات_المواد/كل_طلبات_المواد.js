frappe.query_reports["كل طلبات المواد"] = {
  filters: [
    {
      fieldname: "view_mode",
      label: __("طريقة العرض"),
      fieldtype: "Select",
      options: [
        "طلبات المواد",
        "ملخص أمر البيع",
        "نتائج التخصيم",
        "التصنيع اليومي",
        "متابعة التصنيع",
        "تفاصيل المخازن",
        "حالات تشغيلية",
      ].join("\n"),
      default: "طلبات المواد",
      reqd: 1,
    },
    {
      fieldname: "material_request",
      label: __("طلب المواد"),
      fieldtype: "Link",
      options: "Material Request",
    },
    {
      fieldname: "sales_order",
      label: __("أمر البيع"),
      fieldtype: "Link",
      options: "Sales Order",
    },
    {
      fieldname: "customer",
      label: __("العميل"),
      fieldtype: "Link",
      options: "Customer",
      depends_on: "eval:doc.view_mode==='طلبات المواد'",
    },
    {
      fieldname: "customer_name",
      label: __("اسم العميل"),
      fieldtype: "Data",
      depends_on:
        "eval:['نتائج التخصيم','التصنيع اليومي','متابعة التصنيع','تفاصيل المخازن','حالات تشغيلية'].includes(doc.view_mode)",
    },
    {
      fieldname: "company",
      label: __("الشركة"),
      fieldtype: "Link",
      options: "Company",
      depends_on: "eval:doc.view_mode==='طلبات المواد'",
    },
    {
      fieldname: "from_date",
      label: __("من تاريخ"),
      fieldtype: "Date",
      depends_on:
        "eval:['طلبات المواد','نتائج التخصيم','التصنيع اليومي','متابعة التصنيع','تفاصيل المخازن'].includes(doc.view_mode)",
    },
    {
      fieldname: "to_date",
      label: __("إلى تاريخ"),
      fieldtype: "Date",
      depends_on:
        "eval:['طلبات المواد','نتائج التخصيم','التصنيع اليومي','متابعة التصنيع','تفاصيل المخازن'].includes(doc.view_mode)",
    },
    {
      fieldname: "workflow_state",
      label: __("حالة Workflow"),
      fieldtype: "Link",
      options: "Workflow State",
      depends_on:
        "eval:['طلبات المواد','نتائج التخصيم','التصنيع اليومي','تفاصيل المخازن','حالات تشغيلية'].includes(doc.view_mode)",
    },
    {
      fieldname: "workflow_style",
      label: __("لون الحالة"),
      fieldtype: "Select",
      options: "\nغير محدد\nred\norange\nyellow\ngreen\nblue\ngray",
      depends_on: "eval:doc.view_mode==='نتائج التخصيم'",
    },
    {
      fieldname: "branch",
      label: __("الفرع"),
      fieldtype: "Link",
      options: "Branch",
      depends_on: "eval:doc.view_mode==='طلبات المواد'",
    },
    {
      fieldname: "request_scenario",
      label: __("سيناريو الطلب"),
      fieldtype: "Select",
      options: "\nتصنيع\nاستبدال\nنواقص\nقطاعات",
      depends_on: "eval:doc.view_mode==='طلبات المواد'",
    },
    {
      fieldname: "manufacturing_status",
      label: __("حالة التصنيع"),
      fieldtype: "Select",
      options: "\nغير مصنع\nقيد التصنيع\nمصنع بالكامل",
      depends_on: "eval:doc.view_mode==='طلبات المواد'",
    },
    {
      fieldname: "delivery_readiness_status",
      label: __("جاهزية التوريد"),
      fieldtype: "Select",
      options: "\nغير جاهز\nجاهز للتوريد\nتم التوريد بالكامل",
      depends_on: "eval:doc.view_mode==='طلبات المواد'",
    },
    {
      fieldname: "item_code",
      label: __("الصنف"),
      fieldtype: "Link",
      options: "Item",
      depends_on:
        "eval:['طلبات المواد','التصنيع اليومي','متابعة التصنيع','حالات تشغيلية'].includes(doc.view_mode)",
    },
    {
      fieldname: "item_group",
      label: __("مجموعة الصنف"),
      fieldtype: "Link",
      options: "Item Group",
      depends_on: "eval:['نتائج التخصيم','التصنيع اليومي'].includes(doc.view_mode)",
    },
    {
      fieldname: "component",
      label: __("المكون"),
      fieldtype: "Link",
      options: "Store Component",
      depends_on: "eval:['نتائج التخصيم','تفاصيل المخازن'].includes(doc.view_mode)",
    },
    {
      fieldname: "line_type",
      label: __("نوع السطر"),
      fieldtype: "Select",
      options: "\nباب\nمكون",
      depends_on: "eval:doc.view_mode==='التصنيع اليومي'",
    },
    {
      fieldname: "manufactured_by",
      label: __("تم بواسطة"),
      fieldtype: "Link",
      options: "User",
      depends_on:
        "eval:['التصنيع اليومي','متابعة التصنيع'].includes(doc.view_mode) && (frappe.user.has_role('System Manager') || frappe.user.has_role('HR Manager') || frappe.user.has_role('HR User'))",
    },
    {
      fieldname: "operation_preset",
      label: __("الحالة التشغيلية"),
      fieldtype: "Select",
      options: [
        "جاري التصنيع",
        "توريدات معلقة",
        "مقاسات معلقة",
        "صيانة معلقة",
        "استحقاق خلال أسبوعين",
        "طلبات التصنيع",
      ].join("\n"),
      default: "جاري التصنيع",
      depends_on: "eval:doc.view_mode==='حالات تشغيلية'",
    },
    {
      fieldname: "customer_vip",
      label: __("VIP فقط"),
      fieldtype: "Check",
      depends_on: "eval:doc.view_mode==='طلبات المواد'",
    },
    {
      fieldname: "include_cancelled",
      label: __("إظهار الملغي"),
      fieldtype: "Check",
      depends_on: "eval:doc.view_mode==='طلبات المواد'",
    },
    {
      fieldname: "limit",
      label: __("الحد الأقصى"),
      fieldtype: "Int",
      default: 500,
      depends_on:
        "eval:['طلبات المواد','التصنيع اليومي','متابعة التصنيع','تفاصيل المخازن','حالات تشغيلية'].includes(doc.view_mode)",
    },
  ],

  formatter(value, row, column, data, default_formatter) {
    value = default_formatter(value, row, column, data);
    if (!data) {
      return value;
    }

    if (column.fieldname === "customer_vip" && data.customer_vip) {
      return `<span class="indicator-pill red" dir="rtl">${__("نعم")}</span>`;
    }

    if (column.fieldname === "is_urgent" && data.is_urgent) {
      return `<span class="indicator-pill orange" dir="rtl">${__("نعم")}</span>`;
    }

    if (column.fieldname === "balance" && flt(data.balance) < 0) {
      return `<span style="color: var(--red-600); font-weight: 600;">${value}</span>`;
    }

    return value;
  },
};
