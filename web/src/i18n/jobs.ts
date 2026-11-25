/**
 * Job titles ("Create instance") and step titles ("Starting Odoo") are written by the server in English. The panel
 * shows its own words for the ones it knows (exact titles, and patterns for titles with a value in them); anything
 * else is shown as the server sent it. Names, branches, versions and modules in a title stay left to right.
 */
import { countIn, ltr } from "./plural.ts";

const HOURS = { zero: "# ساعة", one: "ساعة واحدة", two: "ساعتين", few: "# ساعات", many: "# ساعة", other: "# ساعة" };
const DAYS = { zero: "# يوم", one: "يوم واحد", two: "يومين", few: "# أيام", many: "# يومًا", other: "# يوم" };
const arCount = (value: string, forms: typeof HOURS) =>
  Number.isInteger(Number(value)) ? countIn("ar", Number(value), forms) : `${value} ${forms.other.replace("# ", "")}`;

type Pattern = [RegExp, (...values: string[]) => string];

/** Arabic for exact English titles. */
const AR_EXACT: Record<string, string> = {
  // job titles (web/v1/instances.py ACTIONS, background.py, plugin_manager.py, integrity.py)
  "Create instance": "إنشاء البيئة",
  "Get latest code": "جلب أحدث شيفرة",
  "Update submodules": "تحديث الوحدات الفرعية",
  Restart: "إعادة التشغيل",
  Start: "التشغيل",
  "Add submodule": "إضافة وحدة فرعية",
  Pause: "الإيقاف المؤقت",
  "Switch off": "الإطفاء",
  "Stopped at the end of its lifetime": "الإيقاف عند انتهاء المدة",
  "Upgrade modules": "ترقية الوحدات",
  "Remove submodule": "إزالة وحدة فرعية",
  "Switch branch": "تبديل الفرع",
  "Connect a repository": "ربط مستودع",
  "Back up": "النسخ الاحتياطي",
  "Update Odoo and PostgreSQL images": "تحديث صور Odoo وPostgreSQL",
  "Restore the panel's configuration": "استعادة إعدادات اللوحة",
  "Restore a backup": "استعادة نسخة احتياطية",
  "Roll back the restore": "التراجع عن الاستعادة",
  Rollback: "التراجع",
  Restore: "الاستعادة",
  "Delete instance": "حذف البيئة",
  "Apply resources": "تطبيق الموارد",
  "Writing the new resources": "كتابة الموارد الجديدة",
  "Backing up the database and files before the change": "نسخ قاعدة البيانات والملفات احتياطيًا قبل التغيير",
  "Applying the new resources": "تطبيق الموارد الجديدة",
  "Putting the previous resources back": "إعادة الموارد السابقة",
  "Start (someone opened it)": "التشغيل (فتحها أحدهم)",
  "Daily backup": "النسخ الاحتياطي اليومي",
  "Back up before paid plugins are removed": "نسخ احتياطي قبل إزالة الإضافات المدفوعة",
  "Remove paid plugins (logged out of the account)": "إزالة الإضافات المدفوعة (فُصل الحساب)",
  "Cancel the Odoo upgrade": "إلغاء ترقية Odoo",
  "Cancel the Odoo upgrade (automatic)": "إلغاء ترقية Odoo (تلقائيًا)",
  "Putting Odoo into maintenance": "وضع Odoo في الصيانة",
  "Putting the instance back": "إعادة البيئة كما كانت",
  // steps (instances/operations.py, plugin_manager.py, and the demo's)
  "Asking the marketplace about the plugin": "الاستعلام عن الإضافة من متجر الإضافات",
  "Backing up the database and files": "نسخ قاعدة البيانات والملفات احتياطيًا",
  "Backing up the database and files before the update": "نسخ قاعدة البيانات والملفات احتياطيًا قبل التحديث",
  "Backing up the database first": "نسخ قاعدة البيانات احتياطيًا أولًا",
  "Backing up the instances the plugin can change": "نسخ البيئات التي قد تغيّرها الإضافة احتياطيًا",
  "Backing up the instances the plugins can change": "نسخ البيئات التي قد تغيّرها الإضافات احتياطيًا",
  "Backing up the panel's data": "نسخ بيانات اللوحة احتياطيًا",
  "Checking GitHub for new code": "البحث في GitHub عن شيفرة جديدة",
  "Checking that it suits this panel": "التحقق من ملاءمتها لهذه اللوحة",
  "Checking the signature": "التحقق من التوقيع",
  "Checking its signature": "التحقق من توقيعها",
  "Comparing the instance with the panel's configuration": "مقارنة البيئة بإعدادات اللوحة",
  "Creating the public link": "إنشاء الرابط العام",
  "Downloading the code from GitHub": "تنزيل الشيفرة من GitHub",
  "Downloading the images": "تنزيل الصور",
  "Downloading the latest code from GitHub": "تنزيل أحدث شيفرة من GitHub",
  "Downloading the package": "تنزيل الحزمة",
  Installing: "التثبيت",
  "Moving the submodules to their latest version": "نقل الوحدات الفرعية إلى أحدث إصدار",
  "Neutralising the copied data": "تحييد البيانات المنسوخة",
  "Pausing to free server resources": "الإيقاف المؤقت لتحرير موارد الخادم",
  "Preparing the instance": "تجهيز البيئة",
  "Putting the code into the instance": "وضع الشيفرة في البيئة",
  "Putting the panel's configuration back": "إعادة إعدادات اللوحة",
  "Removing Odoo, its database and its files": "إزالة Odoo وقاعدة بياناته وملفاته",
  "Removing the instance folder": "إزالة مجلد البيئة",
  "Removing the paid plugins": "إزالة الإضافات المدفوعة",
  "Removing the plugin": "إزالة الإضافة",
  "Restarting Odoo": "إعادة تشغيل Odoo",
  "Restarting the panel": "إعادة تشغيل اللوحة",
  "Restarting the backend": "إعادة تشغيل الخادم الخلفي",
  "Restarting the panel to load it": "إعادة تشغيل اللوحة لتحميلها",
  "Saving the change to GitHub": "حفظ التغيير في GitHub",
  "Saving the current state first": "حفظ الحالة الحالية أولًا",
  "Setting the admin password": "تعيين كلمة مرور المسؤول",
  "Starting Odoo": "تشغيل Odoo",
  "Starting Odoo with the new images": "تشغيل Odoo بالصور الجديدة",
  "Starting the database": "تشغيل قاعدة البيانات",
  "Stopping Odoo": "إيقاف Odoo",
  "Stopping the instance: its lifetime is over": "إيقاف البيئة: انتهت مدتها",
  "Switching off": "الإطفاء",
  "Switching the plugin off": "تعطيل الإضافة",
  "The repository's modules are all installed already": "كل وحدات المستودع مثبّتة بالفعل",
  "The chosen modules are all installed already": "كل الوحدات المختارة مثبّتة بالفعل",
  "Waiting for Odoo to be ready": "انتظار جاهزية Odoo",
  "Rolling back to the state before the restore": "التراجع إلى الحالة السابقة للاستعادة",
  // more job titles (projects/, web/v1/imports.py, premium/loader.py, instances/ACTIONS)
  "Archive (branch deleted on GitHub)": "الأرشفة (حُذف الفرع على GitHub)",
  "Delete (branch deleted on GitHub)": "الحذف (حُذف الفرع على GitHub)",
  "Delete (pull request closed)": "الحذف (أُغلق طلب الدمج)",
  "Import a database": "استيراد قاعدة بيانات",
  "Install premium features": "تثبيت الميزات المتقدمة",
  "Move to another engine": "النقل إلى محرك آخر",
  "Rebuild from production": "إعادة البناء من الإنتاج",
  "Scheduled refresh from production": "تحديث مجدول من الإنتاج",
  "Automatic new build": "بناء جديد تلقائي",
  "Maintenance: Update the Odoo images": "صيانة: تحديث صور Odoo",
  "Maintenance: Restart": "صيانة: إعادة التشغيل",
  "Maintenance: Maintenance work": "صيانة: أعمال صيانة",
  // more steps (instances/imports.py, projects/, premium/loader.py, maintenance.py)
  "Asking Sudoo.sh which premium features the plan includes": "سؤال Sudoo.sh عن الميزات المتقدمة التي تشملها الخطة",
  "Backing up first": "نسخ احتياطي أولًا",
  "Checking that Odoo answers": "التحقق من استجابة Odoo",
  "Checking the file": "فحص الملف",
  "Checking the repository's modules": "فحص وحدات المستودع",
  "Installing and updating": "التثبيت والتحديث",
  "Leaving out the backup": "تخطي النسخ الاحتياطي",
  "Neutralising the imported data": "تحييد البيانات المستوردة",
  "No modules to install": "لا توجد وحدات للتثبيت",
  "Restoring the backup": "استعادة النسخة الاحتياطية",
  "Running the tests": "تشغيل الاختبارات",
  "Running the tests on a copy of the database": "تشغيل الاختبارات على نسخة من قاعدة البيانات",
  "Switching off what the plan no longer includes": "إطفاء ما لم تعد الخطة تشمله",
  "Switching the instance off": "إطفاء البيئة",
  // off-site copies (offsite/)
  "Restore an off-site copy": "استعادة نسخة من خارج الخادم",
  "Off-site retention rules": "قواعد الاحتفاظ بالنسخ خارج الخادم",
  "Reading the backup files": "قراءة ملفات النسخة الاحتياطية",
  "Signing and writing the manifest": "توقيع البيان وكتابته",
  "Applying the off-site retention rules": "تطبيق قواعد الاحتفاظ بالنسخ خارج الخادم",
  "Checking the signature of the manifest": "التحقق من توقيع البيان",
};

/** Arabic for titles with values: the English pattern and the Arabic sentence made from its groups. */
const AR_PATTERNS: Pattern[] = [
  [/^Upgrade to Odoo (\S+)$/, (version) => `الترقية إلى Odoo ${ltr(version!)}`],
  [/^Run the Odoo (\S+) upgrade test again$/, (version) => `إعادة اختبار الترقية إلى Odoo ${ltr(version!)}`],
  [/^Automatic upgrade test: (.+)$/, (reason) => `اختبار ترقية تلقائي: ${reason}`],
  [/^Backing up (\S+)$/, (name) => `نسخ ${ltr(name!)} احتياطيًا`],
  [
    /^Upgrading the copy to Odoo (\S+) \(Odoo's upgrade service\)$/,
    (v) => `ترقية النسخة إلى Odoo ${ltr(v!)} (خدمة الترقية من Odoo)`,
  ],
  [/^Upgrading the copy to Odoo (\S+) \(OpenUpgrade\)$/, (v) => `ترقية النسخة إلى Odoo ${ltr(v!)} (OpenUpgrade)`],
  [
    /^Upgrading the database to Odoo (\S+) with Odoo's upgrade service$/,
    (v) => `ترقية قاعدة البيانات إلى Odoo ${ltr(v!)} بخدمة الترقية من Odoo`,
  ],
  [
    /^Upgrading the database to Odoo (\S+) with OpenUpgrade$/,
    (v) => `ترقية قاعدة البيانات إلى Odoo ${ltr(v!)} باستخدام OpenUpgrade`,
  ],
  [/^Switching to Odoo (\S+) and branch (\S+)$/, (v, b) => `التحويل إلى Odoo ${ltr(v!)} والفرع ${ltr(b!)}`],
  [/^Switching to Odoo (\S+)$/, (v) => `التحويل إلى Odoo ${ltr(v!)}`],
  [/^Automatic update: new code on GitHub$/, () => "تحديث تلقائي: شيفرة جديدة على GitHub"],
  [/^Switch off (.+) \(licence ended\)$/, (name) => `إطفاء ${ltr(name!)} (انتهى الترخيص)`],
  [/^Maintenance: Update the panel to version (\S+)$/, (version) => `صيانة: تحديث اللوحة إلى الإصدار ${ltr(version!)}`],
  [/^Automatic new build: (.+)$/, (reason) => `بناء جديد تلقائي: ${reason}`],
  [/^Automatic update: pull request #(\d+) updated$/, (number) => `تحديث تلقائي: تحديث طلب الدمج ${ltr(`#${number}`)}`],
  [/^Copying the data of (.+) \(production\)$/, (name) => `نسخ بيانات ${ltr(name!)} (الإنتاج)`],
  [/^Creating a new database (.+)$/, (db) => `إنشاء قاعدة بيانات جديدة ${ltr(db!)}`],
  [/^Creating the instance on (.+)$/, (engine) => `إنشاء البيئة على ${ltr(engine!)}`],
  [/^Removing the instance from (.+)$/, (engine) => `إزالة البيئة من ${ltr(engine!)}`],
  [/^Putting the instance back on (.+)$/, (engine) => `إعادة البيئة إلى ${ltr(engine!)}`],
  [/^Downloading the latest code of (.+)$/, (branch) => `تنزيل أحدث شيفرة للفرع ${ltr(branch!)}`],
  [/^Importing (.+)$/, (file) => `استيراد ${ltr(file!)}`],
  [/^Installing modules \((\d+)\)$/, (n) => `تثبيت الوحدات (${n})`],
  [/^Installing the repository's modules \((\d+)\)$/, (n) => `تثبيت وحدات المستودع (${n})`],
  [/^Installing the chosen modules \((\d+)\)$/, (n) => `تثبيت الوحدات المختارة (${n})`],
  [/^Loading the copy into database (.+)$/, (db) => `تحميل النسخة في قاعدة البيانات ${ltr(db!)}`],
  [/^Upgrading the repository's modules \((\d+)\)$/, (n) => `ترقية وحدات المستودع (${n})`],
  [
    /^Using Odoo (\S+) \(each node of the cluster downloads it the first time it runs it\)$/,
    (version) => `استخدام Odoo ${ltr(version!)} (تنزّله كل عقدة في العنقود عند أول تشغيل له)`,
  ],
  [/^Automatic update: push to (\S+) (\S+)$/, (repo, branch) => `تحديث تلقائي: دفع إلى ${ltr(repo!)} ${ltr(branch!)}`],
  [/^Automatic update: (.+)$/, (reason) => `تحديث تلقائي: ${reason}`],
  [/^Paused \(no visits for (\S+) hours\)$/, (hours) => `إيقاف مؤقت (لا زيارات منذ ${arCount(hours!, HOURS)})`],
  [
    /^Deleted automatically \((\S+) days after creation\)$/,
    (days) => `حذف تلقائي (بعد ${arCount(days!, DAYS)} من إنشائها)`,
  ],
  [/^Back up before (.+) stops$/, (name) => `نسخ احتياطي قبل توقف ${ltr(name!)}`],
  [/^Install (.+)$/, (name) => `تثبيت ${ltr(name!)}`],
  [/^Update (.+)$/, (name) => `تحديث ${ltr(name!)}`],
  [/^Uninstall (.+)$/, (name) => `إلغاء تثبيت ${ltr(name!)}`],
  [/^Adding submodule (.+)$/, (path) => `إضافة الوحدة الفرعية ${ltr(path!)}`],
  [/^Removing submodule (.+)$/, (path) => `إزالة الوحدة الفرعية ${ltr(path!)}`],
  [
    /^Creating the Odoo (\S+) database \(about 1-2 minutes\)$/,
    (edition) => `إنشاء قاعدة بيانات Odoo ${edition} (دقيقة إلى دقيقتين تقريبًا)`,
  ],
  [/^Downloading Odoo (\S+) image$/, (version) => `تنزيل صورة Odoo ${ltr(version!)}`],
  [
    /^Downloading Odoo (\S+), first time on this server$/,
    (version) => `تنزيل Odoo ${ltr(version!)}، أول مرة على هذا الخادم`,
  ],
  [
    /^Using the Odoo (\S+) image on this server$/,
    (version) => `استخدام صورة Odoo ${ltr(version!)} الموجودة على هذا الخادم`,
  ],
  // a running step's progress (Job.progress: integrity.pull_progress, Odoo's module loading)
  [
    /^Downloading (.+): (\d+) of (\d+) parts$/,
    (image, done, total) => `تنزيل ${ltr(image!)}: ${done} من ${total} أجزاء`,
  ],
  [
    /^Unpacking (.+): (\d+) of (\d+) parts$/,
    (image, done, total) => `فك ضغط ${ltr(image!)}: ${done} من ${total} أجزاء`,
  ],
  [/^Setting up the module (\S+)$/, (module) => `تجهيز الوحدة ${ltr(module!)}`],
  [
    /^Downloading the code of (.+) \((.+)\) from GitHub$/,
    (repo, branch) => `تنزيل شيفرة ${ltr(repo!)} (${ltr(branch!)}) من GitHub`,
  ],
  [
    /^Loading the backup of (.+) of (.+) into database (.+)$/,
    (instance, taken, db) => `تحميل نسخة ${ltr(instance!)} الاحتياطية من ${taken} في قاعدة البيانات ${ltr(db!)}`,
  ],
  [
    /^Loading the backup of (.+) into database (.+)$/,
    (instance, db) => `تحميل نسخة ${ltr(instance!)} الاحتياطية في قاعدة البيانات ${ltr(db!)}`,
  ],
  [/^Preparing the Odoo (\S+) Enterprise add-ons$/, (version) => `تجهيز إضافات Odoo ${ltr(version!)} Enterprise`],
  [/^Putting the instance back to (.+)$/, (previous) => `إعادة البيئة إلى ${ltr(previous!)}`],
  [
    /^Restoring the backup of (.+) from (.+)$/,
    (taken, origin) => `استعادة النسخة الاحتياطية من ${taken} من ${ltr(origin!)}`,
  ],
  [/^Restoring the backup of (.+)$/, (taken) => `استعادة النسخة الاحتياطية من ${taken}`],
  [/^Rolling back to (.+), before the restore$/, (taken) => `التراجع إلى ${taken}، قبل الاستعادة`],
  [/^Switching the code to branch (.+)$/, (branch) => `تبديل الشيفرة إلى الفرع ${ltr(branch!)}`],
  [/^Upgrading modules: changed modules$/, () => "ترقية الوحدات: الوحدات التي تغيّرت"],
  [/^Upgrading modules: (.+)$/, (modules) => `ترقية الوحدات: ${ltr(modules!)}`],
  [/^Downloading (\S+) (\S+)$/, (plugin, version) => `تنزيل ${ltr(plugin!)} ${ltr(version!)}`],
  [/^Off-site copy of (.+) \(again\)$/, (name) => `نسخ ${ltr(name!)} خارج الخادم (مجددًا)`],
  [/^Off-site copy of (.+)$/, (name) => `نسخ ${ltr(name!)} خارج الخادم`],
  [/^Check of the off-site copy (.+)$/, (id) => `فحص النسخة خارج الخادم ${ltr(id!)}`],
  [/^Copying (.+) to (.+)$/, (file, where) => `نسخ ${ltr(file!)} إلى ${ltr(where!)}`],
  [/^Downloading and checking (.+)$/, (file) => `تنزيل ${ltr(file!)} وفحصه`],
  [/^Recreating (.+) from the panel's configuration$/, (services) => `إعادة إنشاء ${ltr(services!)} من إعدادات اللوحة`],
];

function translate(text: string): string {
  // the language shown is the page's (i18n/locale.ts sets <html lang> first); read when called, so this module
  // has no browser state of its own and the dictionary check (scripts/check-i18n.mjs) can load it
  if (document.documentElement.lang !== "ar" || !text) return text;
  const exact = AR_EXACT[text];
  if (exact) return exact;
  for (const [pattern, make] of AR_PATTERNS) {
    const found = pattern.exec(text);
    if (found) return make(...found.slice(1));
  }
  return text;
}

/** A job's title (its action), in the panel's language when known. */
export const jobTitle = (action: string) => translate(action);
/** A job step's title, in the panel's language when known. */
export const stepTitle = (title: string) => translate(title);
/** A running step's progress ("Downloading Odoo 19.0: 2 of 5 parts"), in the panel's language when known. */
export const progressText = (text: string) => translate(text);

export default { en: {}, ar: {} };
