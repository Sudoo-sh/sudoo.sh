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
};

export default { en, ar };
