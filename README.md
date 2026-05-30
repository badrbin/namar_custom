# namar_test

تطبيق Frappe مستقل لنقل سكربتات التجريبي من `Server Script` و`Client Script` إلى كود قابل للتثبيت على Frappe Cloud.

## المصدر

- الموقع المصدر: `testnamar.u.frappe.cloud`
- Server Scripts المؤرشفة: `60`
- Client Scripts المؤرشفة: `26`
- آخر مراجعة دمجت سكربتات التصنيع `v2` وسكربت إخفاء إلغاء أمر البيع داخل التطبيق بدل إبقائها كسكربتات حية منفصلة.
- البيئة التجريبية يجب أن تعمل من التطبيق فقط: لا تبقى أي `Server Script` أو `Client Script` مفعلة بعد النقل.

## طريقة التشغيل العامة

1. ارفع هذا المستودع إلى GitHub.
2. أضف التطبيق `namar_test` في Frappe Cloud وثبته على موقع التجريبي.
3. قبل التعطيل، تحقق أن التطبيق المثبت يعمل وأن السكربتات القديمة ما زالت موجودة:
   ```bash
   python3 scripts/smoke_test.py --env test --expect-legacy any --app-installed
   ```
4. بعد التثبيت، شغل أداة التعطيل أولًا من مستودع التطبيق:
   ```bash
   python3 scripts/manage_legacy_scripts.py --env test --action disable --execute
   ```
5. اختبر أن السكربتات القديمة أصبحت معطلة وأن التطبيق ما زال يرد:
   ```bash
   python3 scripts/smoke_test.py --env test --expect-legacy disabled --app-installed
   ```
6. بعد نجاح الاختبار، احذف السكربتات القديمة:
   ```bash
   python3 scripts/manage_legacy_scripts.py --env test --action delete --execute
   ```
7. تحقق أن السكربتات القديمة حذفت وأن التطبيق ما زال يرد:
   ```bash
   python3 scripts/smoke_test.py --env test --expect-legacy deleted --app-installed
   ```

الأدوات تعمل بوضع `dry-run` افتراضيًا ما لم تمرر `--execute`.

## اختبار النقل بعد التطبيق

استخدم `.env.local` الموجود في المستودع المجاور `erpnex_codex` لقراءة:

- `FRAPPE_TEST_SITE`
- `FRAPPE_TEST_TOKEN`
- `BROWSER_LOGIN_EMAIL`
- `BROWSER_LOGIN_PASSWORD`

الفحص السريع غير المؤثر:

```bash
python3 scripts/smoke_test.py --env test --expect-legacy deleted --app-installed --report-installed --strict-no-unmanaged-live-scripts
```

الفحص الحي ببيانات فعلية على التجريبي:

```bash
python3 scripts/live_migration_regression.py --env test
```

هذا الاختبار ينشئ ويحذف بيانات اختبار، ويغيّر بيانات تشغيلية محددة في التجريبي للتأكد من أن المسارات المنقولة تعمل من التطبيق:

- يتحقق أن `namar_test` مثبت وأن أصول JavaScript الخاصة بالتطبيق منشورة.
- يتحقق أن كل سكربتات `Server Script` و`Client Script` القديمة محذوفة، ولا توجد سكربتات حية غير مدارة.
- يشغل API القراءة والتقرير الموحد `كل طلبات المواد`.
- يختبر منع إنشاء عميل بلا رقم جوال.
- ينشئ Lead اختبار، يحدّث بيانات الخريطة، يسجل زيارة، ثم يحذف بيانات الاختبار.
- يشغل مسار حزم مكونات التوريد على `MREQ-06077-1` ويسجل حزمة جاهزة.
- يشغل مسار تسجيل التصنيع اليدوي على طلب فيه كمية متبقية.
- ينشئ Material Request من Sales Order بكميات متبقية ثم يحذفه.
- إذا لم تظهر أزرار Doctype بعد حذف Client Scripts، افحص تحميل ملفات `namar_test/js/doctype/*.js` من `app_include_js`. التطبيق يحمّلها عالميًا مع guard داخلي حتى تبقى الواجهة عاملة حتى لو لم يحمّل `doctype_js` أحد الملفات على موقع Frappe Cloud.

فحص أزرار الواجهة من المتصفح:

```bash
NODE_PATH=/path/to/node_modules node scripts/browser_smoke_test.mjs --mr MREQ-06077-1
```

إذا كان `playwright` مثبتًا محليًا داخل المستودع، لا تحتاج إلى `NODE_PATH`. داخل Codex Desktop يمكن استخدام حزمة Node المرفقة. الاختبار يفتح التجريبي، يسجل الدخول ببيانات `.env.local`، ويتأكد أن أزرار Material Request وSales Order وLead وCutting Template تظهر من التطبيق بعد حذف السكربتات الحية.

لجعل فحص الأزرار إلزاميًا على مستندات معروفة بدل الاعتماد على قدرة جلسة المتصفح على جلب آخر مستند، مرر أسماء المستندات صراحة:

```bash
NODE_PATH=/path/to/node_modules node scripts/browser_smoke_test.mjs \
  --mr MREQ-06077-1 \
  --sales-order SO253900 \
  --lead LEAD260040 \
  --cutting-template "فلات درفتين"
```

هذا المسار يفشل الاختبار إذا اختفى زر `Material Request` في Sales Order، أو زر تحديث موقع Google Map في Lead، أو أزرار الإضافة المتعددة في Cutting Template. أزرار Cutting Template تُفحص من `cur_frm.custom_buttons` لأن Frappe يضعها تحت قائمة `إضافة`، ثم يفتح الاختبار حواري `إضافة أصناف` و`إضافة نطاقات` للتأكد أن الزرين يعملان.
