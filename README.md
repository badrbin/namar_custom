# namar_test

تطبيق Frappe مستقل لنقل سكربتات التجريبي من `Server Script` و`Client Script` إلى كود قابل للتثبيت على Frappe Cloud.

## المصدر

- الموقع المصدر: `testnamar.u.frappe.cloud`
- Server Scripts المؤرشفة: `60`
- Client Scripts المؤرشفة: `26`
- آخر مراجعة دمجت سكربتات التصنيع `v2` وسكربت إخفاء إلغاء أمر البيع داخل التطبيق بدل إبقائها كسكربتات حية منفصلة.

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
