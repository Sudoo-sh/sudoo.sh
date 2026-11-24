/** A branch's history (its builds, stage changes and restores, as on Odoo.sh) and a project's Builds tab. A build is
 *  "بناء", a commit "إيداع", a stage change "تغيير المرحلة". */
import { ltr } from "./plural.ts";

const en = {
  history: {
    title: "History",
    loading: "Reading the history…",
    empty: "No history yet",
    emptyText:
      "A build appears here each time the branch's instance is created or gets new code; stage changes and restores too.",
    noInstanceText: "This branch has no instance yet: its builds will appear here once one is created.",
    showOlder: "Show older",
    system: "the panel",
    stageChange: "Stage change",
    restore: "Restore",
    restoreFailed: "Restore failed",
    restoreInto: (instance: string) => `into ${ltr(instance)}`,
    viewSteps: "View steps",
    copySha: "Copy the commit's hash",
    copied: "Copied",
    onGitHub: "Open the commit on GitHub",
    openCommit: (sha: string) => `Open commit ${sha} on GitHub, in a new tab`,
    showMessages: (n: number) => (n === 1 ? "Show 1 message from Odoo's log" : `Show ${n} messages from Odoo's log`),
    hideMessages: "Hide the messages",
    unknownCommit: "Commit not known yet",
    lifetime: {
      staging: (date: string) => `This staging build will be deleted on ${date}.`,
      development: (date: string) => `This development build will be deleted on ${date}.`,
    },
    lifetimeHint: "Change it in the instance's settings, or in the project's lifetimes.",
    commitReceived: "Commit received",
    notBuilt: "No build: this branch does nothing on new commits.",
    notBuiltTag: "Do nothing",
    branchSettings: "Branch settings",
    testsPassed: (n: number) => (n === 1 ? "Tests: 1 passed" : `Tests: ${n} passed`),
    testsFailed: (bad: number, total: number) => `Tests: ${bad} of ${total} failed`,
    testsWarnings: (n: number) => (n === 1 ? "1 warning" : `${n} warnings`),
    failingIn: "Failing in",
  },
  status: {
    success: "Passed",
    warning: "Warning",
    failed: "Failed",
    building: "Building",
    dropped: "Replaced",
  },
  trigger: {
    create: "New instance",
    push: "Push",
    manual: "Get latest code",
    rebuild: "Rebuilt from production",
  },
  builds: {
    tab: "Builds",
    filter: "Search branches",
    noMatch: "No branch matches",
    none: "No builds yet",
    noneText: "Builds appear when an instance of a branch is created or gets new code.",
    github: "GitHub",
    rebuild: "Rebuild",
    age: "Age",
    duration: "Duration",
    noBuild: "No build yet",
    openHistory: (branch: string) => `Open the history of ${ltr(branch)}`,
    rowLabel: (branch: string) => `Builds of ${ltr(branch)}`,
  },
};

const ar: typeof en = {
  history: {
    title: "السجل",
    loading: "جارٍ قراءة السجل…",
    empty: "لا سجل بعد",
    emptyText:
      "يظهر هنا بناء في كل مرة تُنشأ فيها بيئة الفرع أو تحصل على شيفرة جديدة، وكذلك تغييرات المرحلة وعمليات الاستعادة.",
    noInstanceText: "ليس لهذا الفرع بيئة بعد: ستظهر أبنيته هنا بعد إنشاء واحدة.",
    showOlder: "عرض الأقدم",
    system: "اللوحة",
    stageChange: "تغيير المرحلة",
    restore: "استعادة",
    restoreFailed: "فشلت الاستعادة",
    restoreInto: (instance) => `إلى ${ltr(instance)}`,
    viewSteps: "عرض الخطوات",
    copySha: "نسخ معرّف الإيداع",
    copied: "نُسخ",
    onGitHub: "فتح الإيداع على GitHub",
    openCommit: (sha) => `فتح الإيداع ${ltr(sha)} على GitHub في علامة تبويب جديدة`,
    showMessages: (n) =>
      n === 1
        ? "عرض رسالة واحدة من سجل Odoo"
        : n === 2
          ? "عرض رسالتين من سجل Odoo"
          : n <= 10
            ? `عرض ${n} رسائل من سجل Odoo`
            : `عرض ${n} رسالة من سجل Odoo`,
    hideMessages: "إخفاء الرسائل",
    unknownCommit: "الإيداع غير معروف بعد",
    lifetime: {
      staging: (date) => `سيُحذف بناء التجهيز هذا في ${date}.`,
      development: (date) => `سيُحذف بناء التطوير هذا في ${date}.`,
    },
    lifetimeHint: "يمكنك تغيير ذلك في إعدادات البيئة، أو في مدد بقاء المشروع.",
    commitReceived: "وصل إيداع",
    notBuilt: "لا بناء: هذا الفرع لا يفعل شيئًا عند الإيداعات الجديدة.",
    notBuiltTag: "لا شيء",
    branchSettings: "إعدادات الفرع",
    testsPassed: (n) =>
      n === 1
        ? "الاختبارات: نجح اختبار واحد"
        : n === 2
          ? "الاختبارات: نجح اختباران"
          : n <= 10
            ? `الاختبارات: نجحت ${n} اختبارات`
            : `الاختبارات: نجح ${n} اختبارًا`,
    testsFailed: (bad, total) => `الاختبارات: فشل ${bad} من ${total}`,
    testsWarnings: (n) => (n === 1 ? "تحذير واحد" : n === 2 ? "تحذيران" : n <= 10 ? `${n} تحذيرات` : `${n} تحذيرًا`),
    failingIn: "يفشل في",
  },
  status: {
    success: "نجح",
    warning: "تحذير",
    failed: "فشل",
    building: "قيد البناء",
    dropped: "مُستبدَل",
  },
  trigger: {
    create: "بيئة جديدة",
    push: "دفع",
    manual: "جلب أحدث شيفرة",
    rebuild: "أُعيد بناؤه من الإنتاج",
  },
  builds: {
    tab: "الأبنية",
    filter: "ابحث في الفروع",
    noMatch: "لا فرع مطابق",
    none: "لا أبنية بعد",
    noneText: "تظهر الأبنية عند إنشاء بيئة لفرع أو حصولها على شيفرة جديدة.",
    github: "GitHub",
    rebuild: "إعادة البناء",
    age: "العمر",
    duration: "المدة",
    noBuild: "لا بناء بعد",
    openHistory: (branch) => `فتح سجل ${ltr(branch)}`,
    rowLabel: (branch) => `أبنية ${ltr(branch)}`,
  },
};

export default { en, ar };
