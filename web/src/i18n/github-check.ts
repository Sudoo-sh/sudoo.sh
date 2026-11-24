// The words of a project's "Check GitHub connection" card (Settings tab) and of its link to the audit log.

const en = {
  title: "GitHub connection",
  text: "Checks every part of this project's link to GitHub, like Verify Deploy Key and Verify Webhook on Odoo.sh: nothing is changed.",
  check: "Check connection",
  again: "Check again",
  checking: "Checking with GitHub…",
  checked: (when: string) => `Checked ${when}`,
  overall: {
    ok: "Everything works",
    warning: "Works, with something to look at",
    error: "Something does not work",
  } as Record<string, string>,
  names: {
    repository: "Repository",
    installation: "GitHub App installation",
    permissions: "Permissions and events",
    webhook: "Webhook",
    submodules: "Submodules and deploy keys",
  } as Record<string, string>,
  statuses: { ok: "OK", warning: "Look at this", error: "Failed", skipped: "Not checked" } as Record<string, string>,
  failed: "The check could not run",
  auditLog: "Audit log",
  auditLogHint: "Everything done in this project",
};

const ar: typeof en = {
  title: "الاتصال بـ GitHub",
  text: "يفحص كل جزء من ربط هذا المشروع بـ GitHub، مثل Verify Deploy Key وVerify Webhook في Odoo.sh: دون تغيير أي شيء.",
  check: "افحص الاتصال",
  again: "افحص مجددًا",
  checking: "جارٍ الفحص مع GitHub…",
  checked: (when) => `فُحص ${when}`,
  overall: { ok: "كل شيء يعمل", warning: "يعمل، مع ما يستحق النظر", error: "شيء ما لا يعمل" },
  names: {
    repository: "المستودع",
    installation: "تثبيت تطبيق GitHub",
    permissions: "الصلاحيات والأحداث",
    webhook: "خطاف الويب (Webhook)",
    submodules: "الوحدات الفرعية ومفاتيح النشر",
  },
  statuses: { ok: "سليم", warning: "يستحق النظر", error: "فشل", skipped: "لم يُفحص" },
  failed: "تعذّر تشغيل الفحص",
  auditLog: "سجل التدقيق",
  auditLogHint: "كل ما جرى في هذا المشروع",
};

export default { en, ar };
