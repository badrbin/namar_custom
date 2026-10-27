# Namar Customizations

## فرع تجربة ERPNext 16

هذا الفرع `v16-trial-20261027` مخصص لنسخة مستقلة من الموقع الأساسي على
ERPNext/Frappe 16. مرجعه هو الكود المنشور فعليًا
`f81df137dac7c0610af0bec70e7098c419864ad7` بعد التحقق من تحديث المصدر خارجيًا
أثناء التحضير، وليس اعتمادًا على آخر تحديث في فرع `production` وحده.
تحدّد النسخة اعتماد Frappe بالنطاق `>=16,<17` وتضيف عزل التكاملات قبل الترحيل
للموقع التجريبي المحدد أدناه. تبقى وظائف التطبيق الأخرى كما في المرجع.

- لا يُنشر هذا الفرع على `erp.namar.net` ولا على بنشه، ولا يُدمج في فرع الإنتاج.
- تُستعاد نسخة احتياطية موجودة إلى الموقع الجديد فقط، دون تحديث الموقع المصدر.
- تُعزل النسخة الجديدة عن إرسال البريد والرسائل والفواتير الإلكترونية والتكاملات
  الخارجية، ويظل المجدول متوقفًا أثناء الاستعادة والفحص.
- قبل الاستعادة، يجب تفعيل `namar_v16_clone_isolation=1` في إعدادات الوجهة
  `namar16-trial.k.frappe.cloud`. لا يعمل خطاف العزل إلا عند تطابق اسم الموقع
  حرفيًا وتفعيل هذا الإعداد معًا، ولا ينشئ اتصالًا بالمصدر.
- يعمل الخطاف `before_migrate` قبل ترقيعات Frappe: يعطّل أعلام إرسال ZATCA في
  الشركات، وإرسال واستقبال حسابات البريد، و`Notification.enabled` و`Webhook.enabled`
  الموجودة في قاعدة النسخة. يستخدم تحديثات قاعدة البيانات دون أحداث الحفظ، ويحفظ
  بقية الحقول والسجلات وطابور البريد. يتجاوز الجداول والأعمدة غير الموجودة، ويوقف
  الترحيل عند أخطاء التنفيذ؛ لا يبتلع الخطأ أو ينفّذ `commit` مستقلًا.
- يُعاد نفس الحارس في `after_migrate` لضبط الحالة النهائية إذا أضاف ترحيل أو
  fixture إعدادًا مفعّلًا، مع الاحتفاظ بخطاف فهرس الموافقات الحالي.
- يجب إبقاء `pause_scheduler=1` و`mute_emails=1` وفحص أعلام التكاملات بعد الاستعادة
  قبل فتح النسخة للاستخدام. الخطاف ليس حاجز شبكة شاملًا ولا يغطي تكاملًا مخصصًا آخر.
- تعطيل علم العزل مستقبلًا يحتاج مراجعة نطاق النسخة؛ تعطيله لا يعيد تفعيل أي
  حساب بريد أو إشعار أو Webhook أو إعداد ZATCA تلقائيًا.
- لتعطيل بناء فهرس الموافقات مؤقتًا على النسخة الجديدة، يُضبط كل من
  `followup_approval_index_enabled` و`followup_approval_index_build_enabled` إلى `0`.
  هذا لا يعزل بقية التكاملات؛ تُراجع إعداداتها على النسخة بصورة مستقلة.
- نجاح الاختبارات المحلية لا يكفي لاعتماد التوافق: يلزم نجاح البناء والاستعادة
  والترحيل على Frappe 16، ثم فحص الدخول والتقارير والمتابعات والتخصيصات على النسخة.
- الرجوع يكون إلى نسخة احتياطية من موقع التجربة وبنشه الموافق لها؛ لا يُخفَّض
  إصدار قاعدة بيانات رُحّلت إلى 16 فوق موقع الإنتاج أو فوق قاعدة النسخة نفسها.

اختبارات التطبيق المحلية:

تحتاج أدوات الاختبار إلى `requests` و`beautifulsoup4`، وهما متوفران أصلًا مع
Frappe 16. فُحص هذا التحضير على Python 3.14.7: نجحت 395 تجربة Python وستة
ملفات اختبار JavaScript. هذه اختبارات محلية تستخدم بدائل Frappe في الذاكرة؛
لم تُتصل بالموقع الأساسي أو تُشغّل ترحيل قاعدة بيانات.

```sh
python -m unittest discover -s tests -p 'test_*.py'
python -m unittest discover -s namar_custom/delivery_components -p 'test_*.py'
python -m unittest namar_custom.followups.test_logic
node --test tests/test_*.cjs
```

## وصف التطبيق المرجعي

Production-safe ERPNext/Frappe customizations for Namar.

This production branch is intentionally narrow. It currently includes only the edited-comment mention notification hook.

## Comment edit mention notifications

Frappe sends `@mention` notifications when a comment is created, but not when an existing comment is edited. This app adds a `Comment.on_update` hook so any text edit to a comment that currently contains mentions notifies all current mentioned users.

A quick kill-switch is available via site config:

```text
namar_enable_comment_edit_mentions = 0
```

Default behavior is enabled.
