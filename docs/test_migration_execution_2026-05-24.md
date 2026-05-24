# تقرير تنفيذ نقل سكربتات التجريبي إلى `namar_test`

تاريخ التنفيذ: 2026-05-24

## النطاق

- البيئة: `testnamar.u.frappe.cloud`
- التطبيق: `namar_test`
- فرع GitHub: `namar_test`
- آخر commit منشور وقت التنفيذ: `371c85a` - `إصلاح بنية تطبيق نمار التجريبي`
- الإنتاج لم يتم تعديله.

## التسلسل

1. تم نشر التطبيق على Frappe Cloud بنجاح عبر Deploy `deploy-13170-000140`.
2. تم تثبيت التطبيق على موقع التجريبي عبر Job `Install App on Site`.
3. تم تشغيل smoke test قبل تعطيل السكربتات القديمة:
   - Server Script وClient Script القديمة موجودة: `83`
   - المفعلة: `78`
   - المعطلة مسبقًا: `5`
   - دوال `namar_test.api` نجحت.
4. تم تصدير نسخة حيّة قبل التعطيل:
   - `exports/live_scripts_test_20260524_074244`
   - Server Script: `58`
   - Client Script: `25`
5. تم تعطيل كل السكربتات القديمة ثم تشغيل smoke test:
   - الموجودة: `83`
   - المفعلة: `0`
   - المعطلة: `83`
   - دوال `namar_test.api` نجحت.
6. تم حذف كل السكربتات القديمة ثم تشغيل smoke test نهائي:
   - الموجودة: `0`
   - المحذوفة/المفقودة حسب manifest: `83`
   - دوال `namar_test.api` نجحت.

## نسخ الرجوع المحلية

الأداة أنشأت نسخ رجوع محلية قبل العمليات المؤثرة:

- قبل فحص الحالة: `backups/legacy_scripts_test_20260524_074306`
- قبل التعطيل: `backups/legacy_scripts_test_20260524_074338`
- قبل الحذف: `backups/legacy_scripts_test_20260524_074532`

النسخة الدائمة داخل GitHub موجودة ضمن:

- `namar_test/legacy_scripts/server_scripts_manifest.json`
- `namar_test/legacy_scripts/client_scripts_manifest.json`
- `namar_test/legacy_scripts/server_scripts/`
- `namar_test/legacy_scripts/client_scripts/`

## التحقق النهائي

أوامر التحقق التي نجحت:

```bash
python3 scripts/smoke_test.py --env test --expect-legacy deleted --app-installed
```

نتائج إضافية:

- أسماء API القديمة بدون prefix ما زالت تعمل عبر aliases، مثل:
  - `get_workflow_transitions`
  - `get_cutting_values_bulk`
  - `get_mr_full_data`
  - `get_sales_dashboard`
  - `get_purchase_dashboard`
  - `get_customer_summary`
  - `get_supplier_summary`
  - `get_related_items`
- أصول JavaScript المنشورة من التطبيق ترجع `200`:
  - `/assets/namar_test/js/doctype/material_request_form.js`
  - `/assets/namar_test/js/doctype/material_request_list.js`
  - `/assets/namar_test/js/doctype/sales_order_form.js`
