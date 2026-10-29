# Namar Customizations

## نسخة فالكنشتاين المستقلة على ERPNext 15

هذا الفرع `falkenstein-standby-20261029` مخصص للموقع المستقل
`namar15-falkenstein.frappe.cloud`. يستند إلى الكود المنشور على الإنتاج عند
`f81df137dac7c0610af0bec70e7098c419864ad7`، ويحتفظ بمتطلب Frappe
`>=15.0.0,<16.0.0` وبوظائف التطبيق نفسها. التغيير الوظيفي الوحيد هو حارس
عزل التكاملات المنسوخة أثناء ترحيل قاعدة الموقع الجديد.

- لا يُنشر هذا الفرع على `erp.namar.net` أو `zawaya7.frappe.cloud` أو نسخة
  فرانكفورت، ولا يُدمج في `production`. لا يتصل الحارس بالمصدر أو بأي خدمة خارجية.
- قبل استعادة قاعدة البيانات، يُضبط `namar_falkenstein_clone_isolation=1` على
  الوجهة. لا يعمل الحارس إلا عند تطابق اسم الموقع حرفيًا وتفعيل العلم معًا.
- يُضبط أيضًا `disable_scheduler=1` و`pause_scheduler=1` و`mute_emails=1`،
  ويُعطّل `server_script_enabled` و`followup_approval_index_enabled`
  و`followup_approval_index_build_enabled`. لا يُنسخ ملف إعدادات المصدر فوق
  إعدادات الوجهة أثناء الاستعادة.
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

اختبارات الحارس المحلية تستخدم قاعدة SQLite مؤقتة دون اتصال بمواقع ERP،
للتحقق من حدود الموقع والعلم، وتعطيل الحقول المحددة فقط، وإعادة التشغيل دون
تغيير الصفوف المعطلة، وإيقاف الترحيل عند فشل SQL. لا تغني هذه الاختبارات
عن التحقق من بناء البنش والاستعادة والعزل الفعلي على الوجهة.
اجتاز الفرع محليًا 396 اختبار Python وستة ملفات اختبار JavaScript.

```sh
python -m unittest discover -s tests -p 'test_clone_isolation.py'
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
