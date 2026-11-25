// The words of "Why it failed" (components/WhyItFailed.tsx): the failed step, the reason, the error details and, for
// a repository the panel cannot read, the fix next to it.
import type { ReactNode } from "react";

const en = {
  title: "Why it failed",
  step: "Failed at",
  details: "Error details",
  detailsHint:
    "Only the lines that explain the failure. The server's log has everything; your administrator can read it.",
  copy: "Copy the error details",
  copied: "Copied",
  noReason: "Something went wrong in this step. The server's log has the details; your administrator can read it.",
  kinds: {
    repository: "Repository access",
    git: "Git",
    requirements: "Python requirements",
    tests: "Tests",
    odoo: "Odoo",
    other: "Other",
  },
  fix: {
    title: "Give the panel access to the repository",
    intro: (repository: ReactNode) => [
      "The panel cannot read ",
      repository,
      ". Choose one of these, then check again.",
    ],
    appTitle: "Recommended: give the panel's GitHub App access to it",
    appText: "On GitHub, open the App's installation and add the repository under Repository access.",
    openInstallation: "Open the GitHub App's installation",
    keyTitle: "Otherwise: create a deploy key for it (read-only)",
    keyText:
      "The panel makes a key that can only read this repository; add it on GitHub under Settings, Deploy keys, without write access.",
    createKey: "Create deploy key",
    addOnGitHub: "Add it on GitHub",
    checkAgain: "Check again",
    checking: "Checking…",
    accessOk: "Access OK",
    noAccess: "No access",
    main: "main",
    submodule: "submodule",
    allOk: "The panel can read every repository now.",
    retry: "Retry the update",
    retried: "The update started again.",
  },
  notified: {
    emailed: "The person who pushed was e-mailed.",
    noAddress:
      "Nobody could be e-mailed: GitHub gave no usable address for the person who pushed. The project's notification channels were told.",
    mailFailed: "The e-mail to the person who pushed could not be sent.",
  },
  rules: {
    odoo_unmet_dependency: {
      title: "A module needs another module that is missing",
      explanation:
        "A module lists another module in its dependencies, but that module is not in the code the instance uses.",
      fix: "Add the missing module to the repository (or to the instance's repositories), or remove it from the module's dependencies.",
    },
    odoo_xml_parse_error: {
      title: "A module has an error in an XML file",
      explanation: "Odoo stopped while reading a view or data file of a module. The file and line are shown.",
      fix: "Open that file at that line and fix the XML (a field that does not exist, a bad inheritance, a missing tag).",
    },
    odoo_external_id_missing: {
      title: "A file refers to a record that does not exist",
      explanation: "A view, menu or data file refers to an external id that was not loaded before it.",
      fix: "Check the module's dependencies, and that the file defining the record comes earlier in the manifest 'data' list.",
    },
    python_syntax_error: {
      title: "A Python file has a syntax error",
      explanation: "Python could not read a file of a module.",
      fix: "Open that file at that line and fix the syntax.",
    },
    pip_no_matching_distribution: {
      title: "A Python package could not be found",
      explanation: "pip could not find the package (or the version) in a module's requirements.",
      fix: "Check the name and the version in requirements.txt, and that it supports the Python of this Odoo version.",
    },
    pip_conflict: {
      title: "Two Python requirements conflict",
      explanation: "pip cannot find versions of the requirements that work together.",
      fix: "Loosen or align the versions in requirements.txt files, then try again.",
    },
    python_module_missing: {
      title: "A Python library is missing",
      explanation: "Code imports a Python library that is not installed in this Odoo.",
      fix: "Add the library to requirements.txt of the module's repository.",
    },
    port_in_use: {
      title: "A port is already in use",
      explanation: "Another program on the server already uses a port this step needs.",
      fix: "Ask the administrator which program uses it, or choose another port.",
    },
    access_csv_error: {
      title: "The access rights file has a problem",
      explanation: "Odoo could not load the access rights of a module (ir.model.access.csv).",
      fix: "Check the model names and the group ids in that file.",
    },
  },
  ruleLabels: {
    found: "What we found",
    whatToDo: "What to do",
    module: "Module",
    file: "File",
    line: "Line in the file",
    excerptLine: "Line in the error details",
    showLine: "Show it",
    package: "Package",
    port: "Port",
  },
};

const ar: typeof en = {
  title: "سبب الفشل",
  step: "فشلت في",
  details: "تفاصيل الخطأ",
  detailsHint: "الأسطر التي تشرح الفشل فقط. في سجل الخادم كل شيء؛ ويستطيع مسؤول اللوحة قراءته.",
  copy: "نسخ تفاصيل الخطأ",
  copied: "نُسخت",
  noReason: "حدث خطأ في هذه الخطوة. في سجل الخادم التفاصيل؛ ويستطيع مسؤول اللوحة قراءته.",
  kinds: {
    repository: "الوصول إلى المستودع",
    git: "Git",
    requirements: "متطلبات Python",
    tests: "الاختبارات",
    odoo: "Odoo",
    other: "أخرى",
  },
  fix: {
    title: "امنح اللوحة صلاحية الوصول إلى المستودع",
    intro: (repository) => ["لا تستطيع اللوحة قراءة ", repository, ". اختر إحدى الطريقتين، ثم تحقّق من جديد."],
    appTitle: "الطريقة الموصى بها: امنح تطبيق GitHub الخاص باللوحة صلاحية الوصول إليه",
    appText: "في GitHub، افتح إعدادات تثبيت التطبيق وأضف المستودع ضمن Repository access.",
    openInstallation: "افتح إعدادات تثبيت تطبيق GitHub",
    keyTitle: "وإلا: أنشئ له مفتاح نشر (للقراءة فقط)",
    keyText:
      "تُنشئ اللوحة مفتاحًا لا يستطيع إلا قراءة هذا المستودع؛ أضفه في GitHub ضمن Settings ثم Deploy keys، دون صلاحية الكتابة.",
    createKey: "إنشاء مفتاح نشر",
    addOnGitHub: "أضفه في GitHub",
    checkAgain: "تحقّق من جديد",
    checking: "جارٍ التحقق…",
    accessOk: "الوصول متاح",
    noAccess: "لا وصول",
    main: "الرئيسي",
    submodule: "وحدة فرعية",
    allOk: "تستطيع اللوحة الآن قراءة كل المستودعات.",
    retry: "أعد محاولة التحديث",
    retried: "بدأ التحديث من جديد.",
  },
  notified: {
    emailed: "أُرسل بريد إلى من دفع الإيداع.",
    noAddress: "تعذّر مراسلة أحد: لم يقدّم GitHub عنوانًا صالحًا لمن دفع الإيداع. أُبلغت قنوات إشعارات المشروع.",
    mailFailed: "تعذّر إرسال البريد إلى من دفع الإيداع.",
  },
  rules: {
    odoo_unmet_dependency: {
      title: "وحدة تحتاج وحدة أخرى غير موجودة",
      explanation: "تذكر إحدى الوحدات وحدة أخرى في اعتمادياتها، لكن تلك الوحدة ليست ضمن الشيفرة التي يستخدمها المثيل.",
      fix: "أضف الوحدة الناقصة إلى المستودع (أو إلى مستودعات المثيل)، أو احذفها من اعتماديات الوحدة.",
    },
    odoo_xml_parse_error: {
      title: "خطأ في ملف XML داخل وحدة",
      explanation: "توقف Odoo أثناء قراءة ملف عرض أو بيانات في إحدى الوحدات. يظهر الملف والسطر.",
      fix: "افتح ذلك الملف عند ذلك السطر وأصلح الـ XML (حقل غير موجود، وراثة خاطئة، وسم ناقص).",
    },
    odoo_external_id_missing: {
      title: "ملف يشير إلى سجل غير موجود",
      explanation: "يشير عرض أو قائمة أو ملف بيانات إلى معرّف خارجي لم يُحمَّل قبله.",
      fix: "تحقق من اعتماديات الوحدة، ومن أن الملف الذي يعرّف السجل يأتي قبله في قائمة data في ملف manifest.",
    },
    python_syntax_error: {
      title: "خطأ في صياغة ملف بايثون",
      explanation: "تعذّر على بايثون قراءة ملف في إحدى الوحدات.",
      fix: "افتح ذلك الملف عند ذلك السطر وأصلح الصياغة.",
    },
    pip_no_matching_distribution: {
      title: "تعذّر العثور على حزمة بايثون",
      explanation: "لم يجد pip الحزمة (أو الإصدار) المذكورة في متطلبات إحدى الوحدات.",
      fix: "تحقق من الاسم والإصدار في requirements.txt، ومن أنها تدعم إصدار بايثون في هذا الإصدار من Odoo.",
    },
    pip_conflict: {
      title: "تعارض بين متطلبات بايثون",
      explanation: "لا يجد pip إصدارات للمتطلبات تعمل معًا.",
      fix: "خفّف الإصدارات أو وحّدها في ملفات requirements.txt ثم حاول مجددًا.",
    },
    python_module_missing: {
      title: "مكتبة بايثون ناقصة",
      explanation: "تستورد الشيفرة مكتبة بايثون غير مثبّتة في Odoo.",
      fix: "أضف المكتبة إلى requirements.txt في مستودع الوحدة.",
    },
    port_in_use: {
      title: "منفذ مستخدم بالفعل",
      explanation: "برنامج آخر على الخادم يستخدم منفذًا تحتاجه هذه الخطوة.",
      fix: "اسأل مسؤول الخادم أي برنامج يستخدمه، أو اختر منفذًا آخر.",
    },
    access_csv_error: {
      title: "مشكلة في ملف صلاحيات الوصول",
      explanation: "تعذّر على Odoo تحميل صلاحيات الوصول في إحدى الوحدات (ir.model.access.csv).",
      fix: "تحقق من أسماء النماذج ومعرّفات المجموعات في ذلك الملف.",
    },
  },
  ruleLabels: {
    found: "ما وجدناه",
    whatToDo: "ما يمكن فعله",
    module: "الوحدة",
    file: "الملف",
    line: "السطر في الملف",
    excerptLine: "السطر في تفاصيل الخطأ",
    showLine: "أظهره",
    package: "الحزمة",
    port: "المنفذ",
  },
};

export default { en, ar };
