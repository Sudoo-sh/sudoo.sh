/** An instance's Domains tab as the core shows it: the custom domains set up, and removing one (adding domains and
 *  their certificates come with the premium domains package, which has its own words). */
import type { ReactNode } from "react";
import { ltr } from "./plural.ts";

const en = {
  status: {
    waiting_dns: "Waiting for DNS",
    issuing: "Getting a certificate",
    active: "Active",
    renewing: "Renewing",
    expiring: "Expires soon",
    failed: "Failed",
  } as Record<string, string>,
  title: "Domains",
  none: "This instance has no custom domain.",
  primary: "Primary",
  remove: "Remove",
  removeLabel: (domain: string) => `Remove ${ltr(domain)}`,
  removeTitle: (domain: string) => `Remove ${ltr(domain)}?`,
  removeText: "Visitors can no longer reach the instance at this domain.",
  removed: (domain: string) => `${ltr(domain)} removed`,
  notRemoved: "Not removed",
  certificateDeleted: "Its certificate and key are deleted.",
  anotherPrimary: " Another domain becomes primary, or the default address is used again.",
  productionRemove: (name: ReactNode) => [
    name,
    " is a production instance: its users may be using this domain right now.",
  ],
  keepServing:
    "Domains set up before keep being served with the certificates they have; removing one is always possible.",
  movedTitle: (domain: string, server: string) => `${ltr(domain)} moved here from ${ltr(server)} with the instance`,
  oldServer: "the old server",
  movedText:
    "Its DNS still points at the old server. Update it as shown; visitors reach the instance here once the change is visible.",
};

const ar: typeof en = {
  status: {
    waiting_dns: "في انتظار DNS",
    issuing: "جارٍ الحصول على شهادة",
    active: "فعّال",
    renewing: "جارٍ التجديد",
    expiring: "تنتهي قريبًا",
    failed: "فشل",
  },
  title: "النطاقات",
  none: "لا نطاق مخصص لهذه البيئة.",
  primary: "أساسي",
  remove: "إزالة",
  removeLabel: (domain) => `إزالة ${ltr(domain)}`,
  removeTitle: (domain) => `إزالة ${ltr(domain)}؟`,
  removeText: "لن يتمكن الزوار بعد الآن من الوصول إلى البيئة عبر هذا النطاق.",
  removed: (domain) => `أُزيل ${ltr(domain)}`,
  notRemoved: "لم تتم الإزالة",
  certificateDeleted: "تُحذف شهادته ومفتاحها.",
  anotherPrimary: " يصبح نطاق آخر أساسيًا، أو يُستخدم العنوان الافتراضي من جديد.",
  productionRemove: (name) => [name, " بيئة إنتاج: قد يستخدم مستخدموها هذا النطاق الآن."],
  keepServing: "تبقى النطاقات المُعدّة من قبل تعمل بشهاداتها الحالية؛ وإزالة أي منها ممكنة دائمًا.",
  movedTitle: (domain, server) => `انتقل ${ltr(domain)} إلى هنا من ${ltr(server)} مع البيئة`,
  oldServer: "الخادم القديم",
  movedText:
    "ما زال DNS الخاص به يشير إلى الخادم القديم. حدّثه كما هو موضّح؛ يصل الزوار إلى البيئة هنا حالما يظهر التغيير.",
};

export default { en, ar };
