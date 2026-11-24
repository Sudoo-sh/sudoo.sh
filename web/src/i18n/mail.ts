/** An instance's Mail tab: the e-mails a staging or development instance sent, caught by the mail catcher. */
import { countIn, type Plural } from "./plural.ts";

const p = (forms: Plural) => forms;
const EN_EMAIL = p({ one: "# e-mail", other: "# e-mails" });
const AR_EMAIL = p({
  zero: "# رسالة",
  one: "رسالة واحدة",
  two: "رسالتان",
  few: "# رسائل",
  many: "# رسالة",
  other: "# رسالة",
});

const en = {
  title: "Caught e-mail",
  sub: "This instance never sends real e-mail: everything Odoo sends stops here.",
  search: "Search subject, addresses, text",
  searchLabel: "Search caught e-mail",
  usage: (count: number, size: string, days: number) =>
    `${countIn("en", count, EN_EMAIL)} · ${size} · kept ${days} ${days === 1 ? "day" : "days"}`,
  deleteAll: "Delete all",
  refresh: "Refresh",
  catcherStopped:
    "The mail catcher is not running, so Odoo cannot send e-mail now (nothing goes out either). An administrator can start it: see the administrator's guide, Mail catcher.",
  catcherMissing:
    "This server has no mail catcher yet (an older compose.yaml). Odoo cannot send e-mail from this instance, and nothing goes out. An administrator can add it: see the administrator's guide, Mail catcher.",
  emptyTitle: "No e-mail caught",
  empty:
    "When Odoo sends an e-mail from this instance (a quotation, an invoice, a password reset), it appears here instead of reaching anyone.",
  noMatch: "No caught e-mail matches this search.",
  more: "Show more",
  noSubject: "(no subject)",
  to: "To",
  cc: "Cc",
  from: "From",
  envelope: "Delivered to",
  received: "Received",
  size: "Size",
  attachments: "Attachments",
  html: "HTML",
  text: "Text",
  headers: "Headers",
  noText: "This e-mail has no text part.",
  noHtml: "This e-mail has no HTML part.",
  frameTitle: (subject: string) => `The e-mail "${subject}", as HTML`,
  blocked: "Scripts, forms and images from other sites are blocked.",
  download: "Download .eml",
  release: "Release…",
  delete: "Delete",
  back: "Back to the list",
  pick: "Choose an e-mail to read it.",
  deleted: "E-mail deleted",
  allDeleted: "Caught e-mail deleted",
  notDeleted: "Could not delete",
  deleteAllTitle: "Delete every caught e-mail?",
  deleteAllText: (count: number) =>
    `${countIn("en", count, EN_EMAIL)} of this instance will be deleted. This cannot be undone.`,
  deleteTitle: "Delete this e-mail?",
  deleteText: "It is deleted from the mail catcher. This cannot be undone.",
  releaseTitle: "Release a copy",
  releaseText:
    "A copy of this e-mail, with its attachments, is sent from the panel's own address to the address you type. The original sender becomes Reply-To. This is recorded in the audit log.",
  releaseTo: "Send the copy to",
  releaseSend: "Send the copy",
  released: (to: string) => `Copy sent to ${to}`,
  notReleased: "The copy was not sent",
  releaseOff: "Releasing is switched off on this panel.",
  badAddress: "Type one e-mail address.",
};

const ar: typeof en = {
  title: "البريد الملتقط",
  sub: "لا ترسل هذه البيئة بريدًا حقيقيًا أبدًا: كل ما يرسله Odoo يتوقف هنا.",
  search: "ابحث في الموضوع والعناوين والنص",
  searchLabel: "البحث في البريد الملتقط",
  usage: (count: number, size: string, days: number) =>
    `${countIn("ar", count, AR_EMAIL)} · ${size} · يُحفظ ${countIn("ar", days, p({ zero: "# يوم", one: "يومًا واحدًا", two: "يومين", few: "# أيام", many: "# يومًا", other: "# يوم" }))}`,
  deleteAll: "حذف الكل",
  refresh: "تحديث",
  catcherStopped:
    "ملتقط البريد لا يعمل، لذا لا يستطيع Odoo إرسال البريد الآن (ولا يخرج أي شيء أيضًا). يمكن للمسؤول تشغيله: راجع دليل المسؤول، ملتقط البريد.",
  catcherMissing:
    "لا يوجد ملتقط بريد على هذا الخادم بعد (ملف compose.yaml قديم). لا يستطيع Odoo إرسال البريد من هذه البيئة ولا يخرج أي شيء. يمكن للمسؤول إضافته: راجع دليل المسؤول، ملتقط البريد.",
  emptyTitle: "لم يُلتقط أي بريد",
  empty:
    "عندما يرسل Odoo رسالة من هذه البيئة (عرض سعر أو فاتورة أو إعادة تعيين كلمة مرور) تظهر هنا بدلًا من أن تصل إلى أحد.",
  noMatch: "لا يوجد بريد ملتقط يطابق هذا البحث.",
  more: "عرض المزيد",
  noSubject: "(دون موضوع)",
  to: "إلى",
  cc: "نسخة إلى",
  from: "من",
  envelope: "سُلّمت إلى",
  received: "وصلت",
  size: "الحجم",
  attachments: "المرفقات",
  html: "HTML",
  text: "نص",
  headers: "الترويسات",
  noText: "لا تحتوي هذه الرسالة على جزء نصي.",
  noHtml: "لا تحتوي هذه الرسالة على جزء HTML.",
  frameTitle: (subject: string) => `الرسالة «${subject}» بصيغة HTML`,
  blocked: "حُظرت البرامج النصية والنماذج والصور القادمة من مواقع أخرى.",
  download: "تنزيل ملف ‎.eml",
  release: "إطلاق…",
  delete: "حذف",
  back: "العودة إلى القائمة",
  pick: "اختر رسالة لقراءتها.",
  deleted: "حُذفت الرسالة",
  allDeleted: "حُذف البريد الملتقط",
  notDeleted: "تعذّر الحذف",
  deleteAllTitle: "حذف كل البريد الملتقط؟",
  deleteAllText: (count: number) => `سيُحذف ${countIn("ar", count, AR_EMAIL)} من هذه البيئة. لا يمكن التراجع عن ذلك.`,
  deleteTitle: "حذف هذه الرسالة؟",
  deleteText: "تُحذف من ملتقط البريد. لا يمكن التراجع عن ذلك.",
  releaseTitle: "إطلاق نسخة",
  releaseText:
    "تُرسل نسخة من هذه الرسالة مع مرفقاتها من عنوان اللوحة نفسها إلى العنوان الذي تكتبه، ويصبح المرسل الأصلي عنوان الرد. يُسجَّل ذلك في سجل التدقيق.",
  releaseTo: "أرسل النسخة إلى",
  releaseSend: "أرسل النسخة",
  released: (to: string) => `أُرسلت النسخة إلى ${to}`,
  notReleased: "لم تُرسل النسخة",
  releaseOff: "الإطلاق معطّل في هذه اللوحة.",
  badAddress: "اكتب عنوان بريد واحدًا.",
};

export default { en, ar };
