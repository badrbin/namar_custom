# namar_test

تطبيق Frappe مستقل لنقل سكربتات التجريبي من `Server Script` و`Client Script` إلى كود قابل للتثبيت على Frappe Cloud.

## المصدر

- الموقع المصدر: `testnamar.u.frappe.cloud`
- Server Scripts المصدرة: `58`
- Client Scripts المصدرة: `25`

## طريقة التشغيل العامة

1. ارفع هذا المستودع إلى GitHub.
2. أضف التطبيق `namar_test` في Frappe Cloud وثبته على موقع التجريبي.
3. بعد التثبيت، شغل أداة التعطيل أولًا من مستودع التطبيق:
   ```bash
   python3 scripts/manage_legacy_scripts.py --env test --action disable --execute
   ```
4. اختبر النماذج والـ APIs.
5. بعد نجاح الاختبار، احذف السكربتات القديمة:
   ```bash
   python3 scripts/manage_legacy_scripts.py --env test --action delete --execute
   ```

الأدوات تعمل بوضع `dry-run` افتراضيًا ما لم تمرر `--execute`.
