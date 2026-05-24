frappe.query_reports["كل طلبات المواد"] = {
  filters: [
    {
      fieldname: "view_mode",
      label: __("طريقة العرض"),
      fieldtype: "Select",
      options: ["طلبات المواد", "ملخص أمر البيع"].join("\n"),
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
      depends_on: "eval:doc.view_mode==='طلبات المواد'",
    },
    {
      fieldname: "to_date",
      label: __("إلى تاريخ"),
      fieldtype: "Date",
      depends_on: "eval:doc.view_mode==='طلبات المواد'",
    },
    {
      fieldname: "workflow_state",
      label: __("حالة Workflow"),
      fieldtype: "Link",
      options: "Workflow State",
      depends_on: "eval:doc.view_mode==='طلبات المواد'",
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
      depends_on: "eval:doc.view_mode==='طلبات المواد'",
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
      depends_on: "eval:doc.view_mode==='طلبات المواد'",
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
