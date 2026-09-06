# حارس طلبات حساب الذكاء الاصطناعي

هذه نسخة مهيأة لتطبيق الأساسي `namar_custom` ووحدة `Namar Custom`، من مرجع `production` عند `862da557`. نُقلت ملفات الحارس المحددة فقط من commit التجريبي `ac8a730` مع تغيير namespace ووحدة الإعدادات؛ لم تُنقل بقية hooks أوإعدادات التجريبي. تبقى هذه النسخة دون نشر حتى نجاح الاختبار الحي على التجريبي ومراجعة النقل. مرجع تطوير الحارس الأصلي هو GitHub `namar_test` عند `0c1a895` في 6 سبتمبر 2026؛ ولا تعني الاختبارات المحلية اكتمال هدف الحساب الحي.

## نطاق التغيير

الإضافة الوحيدة إلى hooks هي `auth_hooks` للحارس. يعمل بعد تحديد الهوية من Frappe وقبل توجيه الطلب، للحسابات التي تحمل دور القراءة المحمي فقط. لا يغير All أو Guest أو Desk User ولا DocShare أو User Permissions أو حسابات المستخدمين. يستعمل واجهات القراءة القياسية مع سقف مستقل لأنواع المستندات وحقول دور AI ، ثم يفرض معاملة SQL للقراءة فقط. لا يعتمد على فعل HTTP وحده لأن GET وطرق المستندات قد تكتب أيضًا.

توجد إعدادات Single DocType باسم `AI Read Only Settings`، قابلة للقراءة والتعديل لـ System Manager فقط. يحدد `protected_role` الدور، وتحدد `policy_json` طرق القراءة المسموحة والتقارير وتنسيقات الطباعة ومصادر موارد الطباعة الخارجية وبصمات Git للتطبيقات وحد الصفحة. عند غياب السياسة أو عدم صحتها، تفشل طلبات الدور المحمي بالرفض؛ لا تمر إلى API الأصلي. لا توجد مفاتيح أو هويات مستخدمين داخل السياسة.

تقبل السياسة أسماء الطرق المعروفة في `policy.METHODS` فقط. إضافة اسم arbitrary إلى JSON لا تمنحه تنفيذ Python. إضافة قدرة جديدة تتطلب adapter مراجع؛ السماح أو الإلغاء للقدرات المعروفة وتحديث قائمة التقارير/التنسيقات يتم من الإعدادات.

```json
{
  "version": 1,
  "methods": ["frappe.auth.get_logged_user", "frappe.client.get", "frappe.client.get_list", "frappe.client.get_value", "frappe.client.get_count", "frappe.desk.search.search_link"],
  "reports": {},
  "print_formats": {},
  "print_resources": {"https://api.qrserver.com": ["/v1/create-qr-code/"], "https://quickchart.io": ["/qr"], "https://bwipjs-api.metafloor.com": ["/"]},
  "app_revisions": {"frappe": "REPLACE_WITH_LIVE_REVIEWED_REVISION"},
  "permission_review": {"sha256": "REPLACE_WITH_PERMISSION_SOURCE_SHA256", "reviewed_no_business_mutations": true},
  "max_rows": 1000
}
```

هذا قالب للتحضير بقيم مكانية يجب استبدالها ببصمات فعلية ومراجعة، فلا يُحفظ كما هو. لا يمثل السياسة النهائية المطلوبة التي يجب أن تشمل جميع التقارير والتصدير والطباعة المقبولة في المهمة. لا تعتمد المثال كبديل أصغر عن النتيجة المطلوبة.

## السلوك

- يحافظ على REST `data` و RPC `message`، ويقبل أسماء قراءة REST v1/v2 والطرق المسموحة و`cmd` القديم وفق أولوية Frappe.
- يعزل المعاملات داخل خطة طلب محلية غير قابلة للإنشاء من API. استدعاء dispatcher مباشرة يفشل دون الخطة التي أنشأها hook. يرفض كذلك أي override أو API Server Script يحمل اسم dispatcher ، لأن Frappe.handler يفحصهما قبل استيراد دالة Python.
- يرفض تغيير HTTP والطرق غير المسموحة و run_method و query و ignore_permissions و ignore_user_permissions وطلب مستخدم آخر والتصدير الخلفي والمستند المصطنع والربط/التوسيع والتجميع غير المراجع.
- لا تمنح مشاركة مستند أو دور تلقائي قراءة لنوع غير مصرح به في دور AI. تُراجع الحقول والفلاتر والترتيب قبل الاستعلام، وتُضاف الملكية كشرط AND مستقل عندما تكون القراءة مشروطة بالمالك.
- يبحث Select عن أسماء الروابط وعناوين وحقول البحث المحددة في metadata ضمن مستويات الحقول المسموحة، باستخدام get_list الطبيعي وقيود المستخدم. لا ينفذ query أو standard_queries اختيارية. إذا كان عنوان أو حقل بحث مشروطًا بالمالك، يفصل البحث إلى مستندات المالك بكل حقولها المسموحة ومطابقات الآخرين بحقولهم العامة، ثم يدمج النتائج بترتيب ثابت. الفلتر الصريح أو الترتيب الذي يستخدم حقلًا مشروطًا يضيف قيد الملكية منعًا للاستدلال على قيم الآخرين.
- التقارير تعمل متزامنة مباشرة على نسخة Report في الذاكرة مع تفعيل `disable_prepared_report_automation=1` فيها فقط؛ يمنع ذلك Timer الأصلي الذي قد يعدل prepared_report باتصال جديد بعد 15 ثانية. لا تحفظ نسخة Report المعدلة. يتحقق adapter Report Builder من الأعمدة والفلاتر والفرز الفعلية، ويستبعد الأعمدة المحجوبة ويرفض الاستدلال بفلتر/فرز محجوب، ويمسح قيم الحقول المشروطة للآخرين قبل حساب الإجماليات. يحافظ على مساره الأصلي `run_standard_report`، وتستعمل التقارير الأخرى `generate_report_result` بعد فحص الصلاحيات والفلاتر. يستعمل التصدير serializers الأصلية بعد تشغيل التقرير المقيد، ولا يستدعي wrapper export_query الذي قد يعيد قراءة Prepared Report.
- التصدير المباشر يشمل حقول الأب و child tables المصرحة. يعيد استعمال DataExporter لتكوين الأعمدة/الملف، ويحمل كل مستند عبر get_doc لتثبيت parenttype الصحيح، ثم يصفّي الحقول حسب ملكية الأب ومستويات الحقول قبل بناء السطور. لا يتسرب الحقل المسموح للمالك فقط داخل سطر مستند يخص مستخدمًا آخر.
- قراءة مستند مسموح تتضمن child tables مع سقف مستويات الحقول وإزالة Password. القراءة المباشرة لنوع child أو virtual تحتاج adapter مراجع، وتفشل حاليًا بالرفض.
- get_value يدعم Single DocTypes وأسماء المستندات النصية وأسماء الحقول العادية، و as_dict=False ؛ لا يستعلم عن جدول SQL غير موجود للـ Single. يحافظ get_list كذلك على شكل as_dict=False مع إخفاء القيم المشروطة لغير المالك.
- الطباعة تستخدم Document مصفى للأب والأطفال بدل إعادة تحميل النسخة الخام، ثم تنقّي الحقول مرة ثانية بعد before_print مباشرة وقبل renderer ؛ حتى إذا أعاد controller ملء حقل أو إضافة طفل فلا يستعيد مستوى محجوبًا. تُثبت مستويات الحقول على ملكية المستند الأصلي. تدعم Standard بمفتاح سياسة `<DocType>::Standard` والتنسيقات المخصصة المعتمدة. Beta Print Builder يعيد تحميل المستند الخام داخل Frappe ، لذلك يُرفض. أثبت فحص قاعدة البيانات للقراءة فقط في 6 سبتمبر 2026 غياب أي Print Format يحمل هذا العلم على التجريبي والأساسي، فلا يستبعد الحارس تنسيقًا موجودًا حاليًا بسبب هذا الشرط.
- يمنع إنشاء اتصال replica جديد عبر decorator القراءة: يضبط read_from_replica=False داخل frappe.local.conf للطلب المحمي فقط، ويبدأ READ ONLY على جميع اتصالات primary/replica الموجودة ثم يستعمل primary. لا يحفظ إعداد الموقع. يحافظ ذلك على READ ONLY عند استدعاء write_only ؛ أما إنشاء اتصال مستقل صراحة من كود تقرير فيبقى مسارًا يتطلب مراجعة المصدر ورفضه.

## المصادر والآثار التدقيقية

كل تقرير أو تنسيق طباعة يحتاج SHA256 و`reviewed_no_business_mutations=true` و`reviewed_read_scope=true`. البصمة دليل مطابقة المصدر، وليست مراجعة تلقائية لسلامة الكود أو نطاق الناتج. تراجع أي كتابة أعمال أو بريد أو مهمة خلفية مؤثرة أو اتصال DB مستقل. يقصد reviewed_read_scope مطابقة صلاحيات أسامة الفعلية وسلوك التقرير الأصلي؛ لا يُعد نطاق SQL الموجود أصلًا توسعة أدخلها حساب AI لمجرد أنه أوسع من get_list على الجداول الخام. تبقى شروط المالك ومستويات حقول الدور محفوظة. قراءات GET الخارجية المعتمدة، مثل أسعار الصرف وصور QR ، تبقى ضمن القدرة المعتادة ويُثبت إعدادها ومصدرها؛ لا تغير الأرقام لحظر قراءة خارجية سليمة. معاملة SQL للقراءة فقط لا تمنع الأثر الخارجي، لذلك يظل تصنيف الأثر ومراجعة المصدر ضروريين. تُثبت Git revisions لجميع التطبيقات المثبتة في جميع عمليات القراءة، إضافة لبصمة سكربتات Permission Query و hooks الصلاحيات والدخول والطلب؛ أي فرق أو غياب revision يمنع التنفيذ حتى المراجعة. يُثبت تعريف Report كاملًا، بما فيه columns/filters/flags/snapshot/references وملفاته. تثبت الطباعة تنسيقها وسكربتات Before Print و Letter Heads و Print Settings و Print Styles و metadata الأب والأطفال و hooks ذات العلاقة.

يعمل auth_hook قبل dispatch ؛ لكن Frappe ينفذ HTTPRequest/on_login و before_request قبله، ثم يكمل auth_hooks بالترتيب و after_request حتى عند رفض الطلب. يجب مراجعة هذه المسارات الحية وترتيبها قبل اعتماد الحارس، ولا تكفي بصمتها المتحققة لاحقًا لمنع أثر حدث قبل دخول الحارس. تبدأ SQL READ ONLY فور التعرف على الدور المحمي، حتى إذا كانت السياسة تالفة أو كانت العملية مرفوضة. المستخدمون الآخرون لا تُحلل سياسة JSON أثناء طلباتهم، ولا تتأثر طلباتهم بسياسة تالفة أو إعدادات غائبة.

Frappe قد يسجل Access Log و Error Log مؤجلًا خارج معاملة القراءة، كما يحفظ cache وجلسات الدخول. تلك آثار تدقيقية للنظام وليست إذنًا لتعديل بيانات الأعمال. لا يجوز وصف الحارس بأنه «صفر كتابة لأي جدول بأي شكل». يتطلب إثبات النتيجة فحص مستندات الأعمال والمشاركات والبريد والـ jobs ، مع توثيق سجلات التدقيق المتوقعة.

## المتبقي قبل النشر والاعتماد

1. استكمال المراجعة المستقلة والاختبارات المتكاملة للحارس، خصوصًا تصدير الأب والأطفال والحقول المشروطة بالمالك والطباعة والتقارير بمصادرها الفعلية. الاختبارات المحلية تثبت مسارات هذه الحالات بمكونات معزولة؛ لا تثبت اكتمال سلوك Frappe الحي.
2. تجهيز السياسة النهائية بجميع طرق القراءة المطلوبة والتقارير المقبولة الـ 81 والتنسيقات المتاحة ضمن المصدر، مع مراجعة فعلية للمصادر. لا تعتمد قائمة فارغة كتغطية لهذه المتطلبات.
3. اختبارات Frappe متكاملة على التجريبي بمصادقة AI: القراءة/Select والقيود الثلاثة وتقارير/تصدير/طباعة، وطلبات تعديل/إنشاء/حذف/إلغاء/مشاركة/بريد و custom API و GET run_method و v2 و cmd. فحص مستندات اختبار قبل/بعد ورفض الطلب قبل التنفيذ.
4. مقارنة إعدادات وسلوك مستخدم عادي قبل/بعد، وعدم تغيير جميع الأدوار والمشاركات الأخرى.
5. مراجعة Bench الحالي وموقعه ونسخ التطبيقات قبل أي نشر، ونقل الحارس وحده إلى namespace `namar_custom` بعد نجاح التجريبي. لا تنقل hooks التجريبي كاملة إلى الأساسي.

الاختبارات المحلية الحالية: 68 اختبارًا ناجحًا، مع نجاح compileall و git diff --check. تشمل اختبار Before Print يعيد ملء حقول الأب والطفل ويضيف طفلًا، اختبار child export لمالكين مختلفين، منع إخفاء dispatcher بسكربت أو override ، تغير app revisions ، وتثبيت اتصالات replica/primary. تُشغل عبر:

```bash
python3 -m unittest discover -s tests -p 'test_ai_readonly_boundary.py' -v
```

## إثبات النشر والاستعادة

النشر التاريخي في فرعي التطبيق يستعمل `press-deploy-bench-13170`. توثيق Frappe Cloud الرسمي يؤكد إمكان استهداف Bench بهذه العلامة، لكن المعرف التاريخي وحده لا يثبت Bench الحالي أو أن نسختي الموقع حدثتا: [توثيق تحديث Bench](https://docs.frappe.io/cloud/benches/updating_a_bench). لا توجد Cloud credentials في `.env.local`، و GitHub commit `0c1a895` لا يحمل check-runs أو status links تعين Bench الحي. أكد زميل فحص Cloud للمهمة من واجهة Safari أن TEST نشط في المجموعة `sector-realtime-20260826` وعلى Bench `bench-46656-000002-f1uae`؛ لذلك علامة 13170 التاريخية ليست مرجع نشر التجريبي الحالي. يتولى زميل Cloud تثبيت معرف المجموعة/الموقع و app candidate ونسخ التطبيقات الحالية قبل تحديد marker أو عملية ترقية ضيقة. لا تستنتج marker من اسم instance وحده.

بعد النشر، endpoint إداري للقراءة فقط `<app>.ai_readonly.boundary.inspect_boundary` يعطي تسجيل hook الفعلي وبصمة المصادر والسياسة و Git revisions للتطبيقات؛ طابقها بالمستودع. ثم اختبر طلب AI فعليًا؛ نجاح endpoint الإداري لا يثبت المنع الوظيفي وحده. endpoint `inspect_review_sources` يساعد تجهيز بصمات التقارير والتنسيقات ولا يضع اعتماد مراجعتها تلقائيًا.

الاستعادة من commit الحارس والسياسة الخاصة قبلها. إزالة hook تعيد احتمال الكتابة من الأدوار/المشاركات، ولذلك يوقف ربط AI قبل استعادة الحارس. لا تعدل صلاحيات بقية المستخدمين كوسيلة استعادة.

## تهيئة السياسة بعد نشر الحارس

1. تُحفظ النسخة القبلية الخاصة وتُنشر ملفات الحارس و DocType الجديد فقط، مع تثبيت بقية تطبيقات Bench على إصداراتها الحالية. تبقى طلبات الدور المحمي مرفوضة إلى أن توجد سياسة صحيحة. الحساب الإداري المستقل الذي لا يحمل الدور المحمي يستطيع الفحص وحفظ Settings.
2. يستدعي الحساب الإداري inspect_boundary ويطابق namespace و source_sha256 وتسجيل auth_hook. تُؤخذ app_revisions من هذه الاستجابة بعد النشر، بالقيم النصية نفسها التي يعيدها get_app_last_commit_ref. تشمل القائمة تطبيقات الموقع المثبتة فقط، بما فيها تطبيق الحارس؛ لا تنسخ قائمة كل تطبيقات Bench. أثبت جرد SQL في 6 سبتمبر أن TEST لديه 8 تطبيقات و PROD لديه 6 ، بينما Bench PROD يتضمن 11.
3. يستدعي inspect_review_sources مع include_sources=1 ويحفظ الاستجابة محليًا فقط. تتضمن تعريفات التقارير/التنسيقات ومصادرها و hooks ؛ يمكن أن تحتوي منطق أعمال خاصًا أو بيانات داخل تعريفات، فلا تُرفع الاستجابة إلى GitHub. تقارير Query Report تثبت SQL المخزن وتعريف Report ولا تفترض وجود مجلد مشتق من اسم العرض؛ Script Report القياسي يثبت ملفاته التنفيذية أيضًا.
4. يطابق مولّد السياسة سجل مراجعة لكل مصدر بالـ SHA256 الفعلي. يلزم في سجل المراجعة مرجع دليل لمصادر البيانات والأعمدة والفلاتر و chart/summary وقيود المستخدم/المالك، وتصنيف قراءة الشبكة المسموحة مقابل كتابة الأعمال/البريد/jobs/اتصال DB مستقل. وجود hash أو خلو فحص نصي من كلمة خطرة لا ينشئ موافقة. المصدر غير المغطى يخرج في تقرير استثناءات، ولا يتحول علم مراجعته إلى true آليًا.
5. تُحفظ policy_json من الحساب الإداري، ثم يعاد inspect_boundary لمطابقة البصمة، ثم اختبارات AI الفعلية. حفظ Settings يغير بيانات الموقع فقط، ولا يغير commit التطبيق؛ لذا لا توجد حلقة بين بصمة الحارس وتفعيل السياسة. عند تغير commit لأي تطبيق أو المصادر الديناميكية، يُجمع دليل جديد وتُراجع الفروقات ثم تحدث السياسة.

لا يثبت نجاح هذه الخطوات سلامة تقرير لم تُراجع مصادره. راجعت المهمة GET أسعار الصرف إلى https://api.frankfurter.dev/v1/{transaction_date} بمعاملات base/symbols ، وهو قراءة خدمة أسعار عامة بلا إرسال بيانات أعمال أو API key. يحفظ الحارس Currency Exchange Settings ضمن بصمة permission_review ، بما فيها endpoint/params ، وتبقى أرقام التقرير وسلوكه المعتاد. حظر Timer حفظ Report.prepared_report مستقل عن هذه القراءة الخارجية.

## جرد hooks المرتبط بالمراجع الحية

طابقت قراءة Cloud للمهمة المراجع الحالية، ثم جُلب 49 ملف مصدر hooks ومساراتها من تلك Git commits. الدليل الخاص محليًا في tmp/ai_boundary_hook_review_20260906 داخل مستودع erpnex_codex ، ومرجع pins في tmp/ai_cloud_discovery_20260906/deployment_pins.json. لا يغني جرد Git عن مطابقة get_hooks الحية بعد النشر.

| المصدر عند commit الحالي | المسار المراجع | الأثر للحساب المستقل |
| --- | --- | --- |
| Frappe TEST fdd46ac / PROD95444a1 | before_request: recorder.record و monitor.start و rate_limiter.apply ؛ after_request: monitor.stop | تسجيل أداء و Redis وحد معدل؛ ليست تعديلات مستندات أعمال. recorder قد يحفظ headers/معاملات ضمن تدقيقه، فتظل لقطاته خاصة. |
| Frappe | on_login: Note._get_unseen_notes ؛ on_session_creation: login_feed و notify_admin_access_to_system_manager | قراءة ملاحظات وكاش؛ Activity Log للمصادقة. البريد الإداري مشروط باسم Administrator ، وليس حساب AI المستقل. |
| ERPNext TEST945e825 / PROD7098602 | on_session_creation: create_customer_or_supplier | يرجع قبل إنشاء أي طرف عندما user_type ليس Website User. تثبيت حساب AI كـ System User شرط الاختبار؛ لا تُعمم النتيجة على Website User. |
| Gameplan30d4f16 على TEST فقط | on_login: gameplan.www.g.on_login | SELECT لوجود GP Team ثم تعيين default_route في الرد. |
| Raven3514bac على TEST فقط | on_session_creation: set_user_active | Redis presence لمدة 900 ثانية؛ لا تعديل مستند أعمال. |
| HRMS و Builder و Zatca عند pins المجموعة الحالية | hooks المشار إليها أعلاه | لا تعريف فعّال لـ before_request/after_request/auth_hooks/on_login/on_session_creation/permission hooks ضمن ملفاتها المراجعة. |
| namar_test0c1a895 قبل الإضافة | permission hooks لـ Namar Mention Thread/Event | شروط 1=0 ورفض القراءة؛ لا أثر خارجي. يجب مطابقة namespace ونفس الدوال في PROD عند النقل. |

راجعت دوال permission_query_conditions و has_permission المجمعة من Frappe و Raven و Gameplan: تستعمل شروط SQL/قراءة أدوار ومستندات وكاش وصلاحيات مرتبطة؛ لم يظهر في أجسام الدوال المراجعة طلب شبكة أو enqueue أو بريد أوحفظ أعمال. هذه نتيجة مراجعة المسارات المحددة، وليست برهانًا عامًا أن كل دالة متعدية أو controller أو تقرير داخل التطبيقات خالٍ من الآثار. يُراجع المصدر الحي لسكربتات Permission Query المخصصة وترتيب hooks مع إعداد السياسة، بعد أي تغير في مصادر الصلاحيات أو الإعدادات المرتبطة بها.

مراجع المصدر المباشرة: [Frappe request hooks](https://github.com/frappe/frappe/blob/fdd46acbcc0db650aa6d86b1378a70a0f919e1cb/frappe/hooks.py)، [ترتيب execute_cmd](https://github.com/frappe/frappe/blob/fdd46acbcc0db650aa6d86b1378a70a0f919e1cb/frappe/handler.py)، [اتصالات read_only/write_only](https://github.com/frappe/frappe/blob/fdd46acbcc0db650aa6d86b1378a70a0f919e1cb/frappe/__init__.py)، [Access Log المؤجل](https://github.com/frappe/frappe/blob/fdd46acbcc0db650aa6d86b1378a70a0f919e1cb/frappe/core/doctype/access_log/access_log.py)، [ERPNext session hook](https://github.com/frappe/erpnext/blob/945e825bee3d0d645f6cb59bcaab90fcbfb98ce3/erpnext/portal/utils.py)، [Gameplan login](https://github.com/frappe/gameplan/blob/30d4f16f9f2c5b18ad684252c61e851228943162/gameplan/www/g.py)، [Raven presence](https://github.com/The-Commit-Company/raven/blob/3514bac29d3e4fbfbcd39a2f0f79b5ee22f104e9/raven/api/user_availability.py).

## موارد الطباعة قبل PDF

يولد الحارس HTML الأصلي عبر get_print(as_pdf=False) من المستند المصفي، ثم يفحص الموارد قبل استدعاء get_pdf. تبقى روابط التنقل العادية مثل href إلى Google Maps دون حظر لأنها لا تُجلب أثناء الطباعة. يقبل موارد /assets/ وملفات الموقع وبيانات الصور النقطية، ويقبل صور QR عند origin/path المراجعين في print_resources فقط. تُرفض مسارات /api حتى لو جاءت داخل src أو CSS أو SVG image ، مع رفض iframe/object/embed/base و meta-refresh و SVG animation و srcset غير المراجع و CSS remote ؛ ويعطّل get_pdf الأصلي JavaScript والوصول إلى الملفات المحلية. لا يوجد monkey patch عام. الاختبار المحلي يثبت أن HTML المحظور لا يصل أصلًا إلى renderer.

بعد الفحص، تتحول صور img/SVG image والخلفيات من File أو خدمة QR إلى PNG data URIs قبل wkhtml. تُقرأ الملفات المحلية عبر find_file_by_url/is_downloadable ، وتُجلب الصور الخارجية بلا cookies أو Authorization وبـ allow_redirects=False ؛ أي redirect يُرفض قبل قراءة الجسم. يتحقق Pillow من PNG/JPEG/GIF/WEBP ويعيد ترميز الصورة، فيرفض SVG/XML حتى لو سمي الملف.png ويسقط المحتوى الملحق وال metadata. يحافظ جلب QR على ترميز معاملات URL كيلا تتغير البيانات المشفرة. يرفض SVG use من ملف مستخدم أو خدمة خارجية؛ مراجع /assets/ المثبتة و fragment المحلي تبقى متاحة. CSS مستخدم في /files لا يُحمّل ك stylesheet ، وتبقى CSS/fonts وأيقونات /assets/ ضمن مصادر التطبيقات المثبتة المراجعة.

لا يمثل ذلك sandbox شبكيًا عامًا؛ حد الثقة المتبقي هو مصدر /assets/ المثبت و hooks الطباعة المراجعة. يجب اختبار PNG الشعار الحالي و QR الفعلية على TEST قبل الاعتماد. لا يسمح SVG كصورة خارجية غير مراجعة؛ إذا كشف الاختبار اعتماد تنسيق قائم عليه، يعالج المصدر المحدد فقط دون فتح SVG عام.
