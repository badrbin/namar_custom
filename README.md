# Namar Customizations

## نسخة فرانكفورت المستقلة على ERPNext 15

هذا الفرع `frankfurt-standby-20261028` مخصص للموقع المستقل
`namar15-frankfurt.f.frappe.cloud`. يستند إلى الكود المنشور على الإنتاج عند
`f81df137dac7c0610af0bec70e7098c419864ad7`، ويحتفظ بمتطلب Frappe
`>=15.0.0,<16.0.0` وبوظائف التطبيق نفسها. التغيير الوظيفي الوحيد هو حارس
عزل التكاملات المنسوخة أثناء ترحيل قاعدة الموقع الجديد.

- لا يُنشر هذا الفرع على `erp.namar.net` أو `zawaya7.frappe.cloud`، ولا يُدمج
  في `production`. لا يتصل الحارس بالمصدر أو بأي خدمة خارجية.
- قبل استعادة قاعدة البيانات، يُضبط `namar_frankfurt_clone_isolation=1` على
  الوجهة. لا يعمل الحارس إلا عند تطابق اسم الموقع حرفيًا وتفعيل العلم معًا.
- يُضبط أيضًا `disable_scheduler=1` و`pause_scheduler=1` و`mute_emails=1`،
  ويُعطّل `followup_approval_index_enabled` و`followup_approval_index_build_enabled`.
  لا يُنسخ ملف إعدادات المصدر فوق إعدادات الوجهة أثناء الاستعادة.
- يعطّل `before_migrate` أعلام ZATCA في الشركات، وإرسال واستقبال حسابات البريد،
  و`Notification.enabled` و`Webhook.enabled` قبل ترقيعات الترحيل. يعيد
  `after_migrate` العزل بعد الترحيل مع الاحتفاظ بخطاف فهرس الموافقات الحالي.
- يستخدم الحارس تحديثات SQL محصورة في الحقول المذكورة دون أحداث حفظ المستندات
  أو `commit` مستقل. لا يحذف سجلات أو طابور بريد ولا يعدّل قيودًا محاسبية.
  يتجاوز الجداول والأعمدة غير الموجودة في الموقع الفارغ، وتوقف أخطاء التنفيذ الترحيل.
- يجب فحص حالة العزل الفعلية بعد الاستعادة وقبل الاستخدام. هذه الضوابط ليست
  حاجز شبكة شاملًا لتكامل مخصص آخر؛ لا تُجرى تجارب إرسال أو اعتماد فواتير حقيقية.
- النسخة المستعادة تمثل وقت النسخ الاحتياطي، وليست مزامنة أو تحويلًا تلقائيًا
  عند تعطل الإنتاج. تعطيل علم الحارس لا يعيد تفعيل التكاملات تلقائيًا.
- التراجع يخص الموقع المستقل وفرعه فقط؛ لا تُستعاد بيانات النسخة فوق الإنتاج.

اختبارات محلية دون اتصال بمواقع ERP:

اجتاز الفرع 395 اختبار Python على Python 3.12.14 (322 + 59 + 14)، وستة ملفات
اختبار JavaScript. تتطلب أدوات الاختبار `requests` و`beautifulsoup4`؛ ثبتتا في
بيئة اختبار محلية مستقلة، دون تغيير اعتماديات التطبيق. الاختبارات المحلية لا تغني
عن التحقق من بناء البنش واستعادة النسخة وحالة العزل الفعلية على الوجهة.

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
