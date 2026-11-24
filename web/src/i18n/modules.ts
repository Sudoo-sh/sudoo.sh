/** The module picker of a new instance (New instance page, Create instance of a project's branch): Odoo's apps and
 *  other modules installed besides the repository's. */
import { ltr } from "./plural.ts";

const en = {
  title: "Odoo apps to install",
  hint: "Installed with the repository's modules, in the same run. Odoo installs what each module needs (its dependencies) by itself.",
  search: "Search apps",
  selected: (n: number) => (n === 0 ? "None selected" : n === 1 ? "1 selected" : `${n} selected`),
  none: "No Odoo apps",
  remove: (name: string) => `Remove ${name}`,
  odoo: "Odoo apps",
  other: "Other",
  otherLabel: "Other modules",
  otherPlaceholder: "web_responsive, l10n_fr",
  otherHint: "Technical names, separated by commas: an OCA module of a submodule, or a standard module not listed.",
  add: "Add",
  wrongName: (name: string) => `"${name}" is not a technical module name: lower-case letters, digits and _.`,
  noMatch: "No app matches.",
  enterprise: "Enterprise",
  tooMany: (max: number) => `Choose at most ${max} modules.`,
  empty: "No Odoo app chosen.",
  fromBackup: "Modules the backup already has are kept as they are.",
};

const ar: typeof en = {
  title: "تطبيقات Odoo المطلوب تثبيتها",
  hint: "تُثبَّت مع وحدات المستودع في التشغيل نفسه. يثبّت Odoo بنفسه ما تحتاجه كل وحدة (اعتمادياتها).",
  search: "ابحث في التطبيقات",
  selected: (n) =>
    n === 0
      ? "لم يُختر شيء"
      : n === 1
        ? "واحد مختار"
        : n === 2
          ? "اثنان مختاران"
          : n <= 10
            ? `${n} مختارة`
            : `${n} مختارًا`,
  none: "دون تطبيقات Odoo",
  remove: (name) => `إزالة ${ltr(name)}`,
  odoo: "تطبيقات Odoo",
  other: "أخرى",
  otherLabel: "وحدات أخرى",
  otherPlaceholder: "web_responsive, l10n_fr",
  otherHint: "الأسماء التقنية، تفصل بينها فواصل: وحدة OCA من مستودع فرعي، أو وحدة قياسية غير مذكورة.",
  add: "إضافة",
  wrongName: (name) => `«${ltr(name)}» ليس اسمًا تقنيًا لوحدة: حروف إنجليزية صغيرة وأرقام والرمز _.`,
  noMatch: "لا تطبيق يطابق البحث.",
  enterprise: "Enterprise",
  tooMany: (max) => `اختر ${max} وحدة على الأكثر.`,
  empty: "لم يُختر أي تطبيق من Odoo.",
  fromBackup: "تبقى الوحدات الموجودة في النسخة الاحتياطية كما هي.",
};

export default { en, ar };
