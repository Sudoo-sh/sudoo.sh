/**
 * When something goes wrong: the error screen shown instead of a blank page when a page fails to draw, and the
 * administrators' banner listing what is wrong with the panel (GET /health). The problems' own text comes from the
 * server and is shown as sent.
 */

const en = {
  screen: {
    title: "This page could not be shown",
    text: "Something went wrong while drawing it. The panel and your instances keep running; reload the page, or go back to the dashboard.",
    reload: "Reload the page",
    home: "Go to the dashboard",
    details: "Technical details",
  },
  banner: {
    title: "Something is wrong with the panel",
    text: "The panel keeps working around it. What happened:",
    more: (n: number) => (n === 1 ? "and 1 more" : `and ${n} more`),
    switchOn: "Switch on again",
    switchedOn: (name: string) => `${name} is switched on again`,
    failures: (n: number) => (n === 1 ? "1 failure in a row" : `${n} failures in a row`),
    switchedOff: "switched off",
    kinds: {
      config: "Setting",
      plugin: "Plugin",
      startup: "Start-up",
      background: "Background work",
    } as Record<string, string>,
  },
};

const ar: typeof en = {
  screen: {
    title: "تعذّر عرض هذه الصفحة",
    text: "حدث خطأ أثناء رسمها. تبقى اللوحة وبيئاتك تعمل؛ أعد تحميل الصفحة أو ارجع إلى لوحة المعلومات.",
    reload: "أعد تحميل الصفحة",
    home: "اذهب إلى لوحة المعلومات",
    details: "التفاصيل التقنية",
  },
  banner: {
    title: "هناك خلل في اللوحة",
    text: "تواصل اللوحة العمل متجاوزةً إياه. ما حدث:",
    more: (n: number) => (n === 1 ? "وواحد آخر" : `و${n} أخرى`),
    switchOn: "أعد التشغيل",
    switchedOn: (name: string) => `أُعيد تشغيل ${name}`,
    failures: (n: number) => (n === 1 ? "عطل واحد متتالٍ" : `${n} أعطال متتالية`),
    switchedOff: "متوقفة",
    kinds: {
      config: "إعداد",
      plugin: "إضافة",
      startup: "بدء التشغيل",
      background: "عمل الخلفية",
    } as Record<string, string>,
  },
};

export default { en, ar };
