"""Arabic for the titles of jobs and their steps, for the pages the gateway writes (status_pages.py). The panel's own
screens use web/src/i18n/jobs.ts: the same words and patterns. tests/job_titles_ar.json holds the Arabic of a sample
of every title; this module and jobs.ts are both tested against it, so they cannot drift apart."""

from __future__ import annotations

import re
from collections.abc import Callable


def ltr(text: object) -> str:
    """A name inside an Arabic sentence, kept left to right (Unicode isolates)."""
    return f"\u2066{text}\u2069"


def _plural(n: int, forms: dict[str, str]) -> str:
    """Arabic plural forms (CLDR): zero, one, two, few for 3-10, many for 11-99, other."""
    if n == 0:
        key = "zero"
    elif n == 1:
        key = "one"
    elif n == 2:
        key = "two"
    elif 3 <= n % 100 <= 10:
        key = "few"
    elif 11 <= n % 100 <= 99:
        key = "many"
    else:
        key = "other"
    return forms.get(key, forms["other"]).replace("#", str(n))


HOURS = {"zero": "# ساعة", "one": "ساعة واحدة", "two": "ساعتين", "few": "# ساعات", "many": "# ساعة", "other": "# ساعة"}
DAYS = {"zero": "# يوم", "one": "يوم واحد", "two": "يومين", "few": "# أيام", "many": "# يومًا", "other": "# يوم"}


def ar_count(value: str, forms: dict[str, str]) -> str:
    if value.isdigit():
        return _plural(int(value), forms)
    return f"{value} {forms['other'].replace('# ', '')}"


AR_EXACT: dict[str, str] = {
    "Create instance": "إنشاء البيئة",
    "Get latest code": "جلب أحدث شيفرة",
    "Update submodules": "تحديث الوحدات الفرعية",
    "Restart": "إعادة التشغيل",
    "Start": "التشغيل",
    "Add submodule": "إضافة وحدة فرعية",
    "Pause": "الإيقاف المؤقت",
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
    "Rollback": "التراجع",
    "Restore": "الاستعادة",
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
    "Installing": "التثبيت",
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
    "Restore an off-site copy": "استعادة نسخة من خارج الخادم",
    "Off-site retention rules": "قواعد الاحتفاظ بالنسخ خارج الخادم",
    "Reading the backup files": "قراءة ملفات النسخة الاحتياطية",
    "Signing and writing the manifest": "توقيع البيان وكتابته",
    "Applying the off-site retention rules": "تطبيق قواعد الاحتفاظ بالنسخ خارج الخادم",
    "Checking the signature of the manifest": "التحقق من توقيع البيان",
}

AR_PATTERNS: list[tuple[re.Pattern[str], Callable[..., str]]] = [
    (re.compile(r"^Upgrade to Odoo (\S+)$"), lambda version: f"الترقية إلى Odoo {ltr(version)}"),
    (
        re.compile(r"^Run the Odoo (\S+) upgrade test again$"),
        lambda version: f"إعادة اختبار الترقية إلى Odoo {ltr(version)}",
    ),
    (re.compile(r"^Automatic upgrade test: (.+)$"), lambda reason: f"اختبار ترقية تلقائي: {reason}"),
    (re.compile(r"^Backing up (\S+)$"), lambda name: f"نسخ {ltr(name)} احتياطيًا"),
    (
        re.compile(r"^Upgrading the copy to Odoo (\S+) \(Odoo's upgrade service\)$"),
        lambda v: f"ترقية النسخة إلى Odoo {ltr(v)} (خدمة الترقية من Odoo)",
    ),
    (
        re.compile(r"^Upgrading the copy to Odoo (\S+) \(OpenUpgrade\)$"),
        lambda v: f"ترقية النسخة إلى Odoo {ltr(v)} (OpenUpgrade)",
    ),
    (
        re.compile(r"^Upgrading the database to Odoo (\S+) with Odoo's upgrade service$"),
        lambda v: f"ترقية قاعدة البيانات إلى Odoo {ltr(v)} بخدمة الترقية من Odoo",
    ),
    (
        re.compile(r"^Upgrading the database to Odoo (\S+) with OpenUpgrade$"),
        lambda v: f"ترقية قاعدة البيانات إلى Odoo {ltr(v)} باستخدام OpenUpgrade",
    ),
    (
        re.compile(r"^Switching to Odoo (\S+) and branch (\S+)$"),
        lambda v, b: f"التحويل إلى Odoo {ltr(v)} والفرع {ltr(b)}",
    ),
    (re.compile(r"^Switching to Odoo (\S+)$"), lambda v: f"التحويل إلى Odoo {ltr(v)}"),
    (re.compile(r"^Automatic update: new code on GitHub$"), lambda: "تحديث تلقائي: شيفرة جديدة على GitHub"),
    (re.compile(r"^Switch off (.+) \(licence ended\)$"), lambda name: f"إطفاء {ltr(name)} (انتهى الترخيص)"),
    (
        re.compile(r"^Maintenance: Update the panel to version (\S+)$"),
        lambda version: f"صيانة: تحديث اللوحة إلى الإصدار {ltr(version)}",
    ),
    (re.compile(r"^Automatic new build: (.+)$"), lambda reason: f"بناء جديد تلقائي: {reason}"),
    (
        re.compile(r"^Automatic update: pull request #(\d+) updated$"),
        lambda number: f"تحديث تلقائي: تحديث طلب الدمج {ltr("#" + number)}",
    ),
    (re.compile(r"^Copying the data of (.+) \(production\)$"), lambda name: f"نسخ بيانات {ltr(name)} (الإنتاج)"),
    (re.compile(r"^Creating a new database (.+)$"), lambda db: f"إنشاء قاعدة بيانات جديدة {ltr(db)}"),
    (re.compile(r"^Creating the instance on (.+)$"), lambda engine: f"إنشاء البيئة على {ltr(engine)}"),
    (re.compile(r"^Removing the instance from (.+)$"), lambda engine: f"إزالة البيئة من {ltr(engine)}"),
    (re.compile(r"^Putting the instance back on (.+)$"), lambda engine: f"إعادة البيئة إلى {ltr(engine)}"),
    (re.compile(r"^Downloading the latest code of (.+)$"), lambda branch: f"تنزيل أحدث شيفرة للفرع {ltr(branch)}"),
    (re.compile(r"^Importing (.+)$"), lambda file: f"استيراد {ltr(file)}"),
    (re.compile(r"^Installing modules \((\d+)\)$"), lambda n: f"تثبيت الوحدات ({n})"),
    (re.compile(r"^Installing the repository's modules \((\d+)\)$"), lambda n: f"تثبيت وحدات المستودع ({n})"),
    (re.compile(r"^Installing the chosen modules \((\d+)\)$"), lambda n: f"تثبيت الوحدات المختارة ({n})"),
    (re.compile(r"^Loading the copy into database (.+)$"), lambda db: f"تحميل النسخة في قاعدة البيانات {ltr(db)}"),
    (re.compile(r"^Upgrading the repository's modules \((\d+)\)$"), lambda n: f"ترقية وحدات المستودع ({n})"),
    (
        re.compile(r"^Using Odoo (\S+) \(each node of the cluster downloads it the first time it runs it\)$"),
        lambda version: f"استخدام Odoo {ltr(version)} (تنزّله كل عقدة في العنقود عند أول تشغيل له)",
    ),
    (
        re.compile(r"^Automatic update: push to (\S+) (\S+)$"),
        lambda repo, branch: f"تحديث تلقائي: دفع إلى {ltr(repo)} {ltr(branch)}",
    ),
    (re.compile(r"^Automatic update: (.+)$"), lambda reason: f"تحديث تلقائي: {reason}"),
    (
        re.compile(r"^Paused \(no visits for (\S+) hours\)$"),
        lambda hours: f"إيقاف مؤقت (لا زيارات منذ {ar_count(hours, HOURS)})",
    ),
    (
        re.compile(r"^Deleted automatically \((\S+) days after creation\)$"),
        lambda days: f"حذف تلقائي (بعد {ar_count(days, DAYS)} من إنشائها)",
    ),
    (re.compile(r"^Back up before (.+) stops$"), lambda name: f"نسخ احتياطي قبل توقف {ltr(name)}"),
    (re.compile(r"^Install (.+)$"), lambda name: f"تثبيت {ltr(name)}"),
    (re.compile(r"^Update (.+)$"), lambda name: f"تحديث {ltr(name)}"),
    (re.compile(r"^Uninstall (.+)$"), lambda name: f"إلغاء تثبيت {ltr(name)}"),
    (re.compile(r"^Adding submodule (.+)$"), lambda path: f"إضافة الوحدة الفرعية {ltr(path)}"),
    (re.compile(r"^Removing submodule (.+)$"), lambda path: f"إزالة الوحدة الفرعية {ltr(path)}"),
    (
        re.compile(r"^Creating the Odoo (\S+) database \(about 1-2 minutes\)$"),
        lambda edition: f"إنشاء قاعدة بيانات Odoo {edition} (دقيقة إلى دقيقتين تقريبًا)",
    ),
    (re.compile(r"^Downloading Odoo (\S+) image$"), lambda version: f"تنزيل صورة Odoo {ltr(version)}"),
    (
        re.compile(r"^Downloading Odoo (\S+), first time on this server$"),
        lambda version: f"تنزيل Odoo {ltr(version)}، أول مرة على هذا الخادم",
    ),
    (
        re.compile(r"^Using the Odoo (\S+) image on this server$"),
        lambda version: f"استخدام صورة Odoo {ltr(version)} الموجودة على هذا الخادم",
    ),
    (
        re.compile(r"^Downloading (.+): (\d+) of (\d+) parts$"),
        lambda image, done, total: f"تنزيل {ltr(image)}: {done} من {total} أجزاء",
    ),
    (
        re.compile(r"^Unpacking (.+): (\d+) of (\d+) parts$"),
        lambda image, done, total: f"فك ضغط {ltr(image)}: {done} من {total} أجزاء",
    ),
    (re.compile(r"^Setting up the module (\S+)$"), lambda module: f"تجهيز الوحدة {ltr(module)}"),
    (
        re.compile(r"^Downloading the code of (.+) \((.+)\) from GitHub$"),
        lambda repo, branch: f"تنزيل شيفرة {ltr(repo)} ({ltr(branch)}) من GitHub",
    ),
    (
        re.compile(r"^Loading the backup of (.+) of (.+) into database (.+)$"),
        lambda instance, taken, db: f"تحميل نسخة {ltr(instance)} الاحتياطية من {taken} في قاعدة البيانات {ltr(db)}",
    ),
    (
        re.compile(r"^Loading the backup of (.+) into database (.+)$"),
        lambda instance, db: f"تحميل نسخة {ltr(instance)} الاحتياطية في قاعدة البيانات {ltr(db)}",
    ),
    (
        re.compile(r"^Preparing the Odoo (\S+) Enterprise add-ons$"),
        lambda version: f"تجهيز إضافات Odoo {ltr(version)} Enterprise",
    ),
    (re.compile(r"^Putting the instance back to (.+)$"), lambda previous: f"إعادة البيئة إلى {ltr(previous)}"),
    (
        re.compile(r"^Restoring the backup of (.+) from (.+)$"),
        lambda taken, origin: f"استعادة النسخة الاحتياطية من {taken} من {ltr(origin)}",
    ),
    (re.compile(r"^Restoring the backup of (.+)$"), lambda taken: f"استعادة النسخة الاحتياطية من {taken}"),
    (re.compile(r"^Rolling back to (.+), before the restore$"), lambda taken: f"التراجع إلى {taken}، قبل الاستعادة"),
    (re.compile(r"^Switching the code to branch (.+)$"), lambda branch: f"تبديل الشيفرة إلى الفرع {ltr(branch)}"),
    (re.compile(r"^Upgrading modules: changed modules$"), lambda: "ترقية الوحدات: الوحدات التي تغيّرت"),
    (re.compile(r"^Upgrading modules: (.+)$"), lambda modules: f"ترقية الوحدات: {ltr(modules)}"),
    (re.compile(r"^Downloading (\S+) (\S+)$"), lambda plugin, version: f"تنزيل {ltr(plugin)} {ltr(version)}"),
    (re.compile(r"^Off-site copy of (.+) \(again\)$"), lambda name: f"نسخ {ltr(name)} خارج الخادم (مجددًا)"),
    (re.compile(r"^Off-site copy of (.+)$"), lambda name: f"نسخ {ltr(name)} خارج الخادم"),
    (re.compile(r"^Check of the off-site copy (.+)$"), lambda id: f"فحص النسخة خارج الخادم {ltr(id)}"),
    (re.compile(r"^Copying (.+) to (.+)$"), lambda file, where: f"نسخ {ltr(file)} إلى {ltr(where)}"),
    (re.compile(r"^Downloading and checking (.+)$"), lambda file: f"تنزيل {ltr(file)} وفحصه"),
    (
        re.compile(r"^Recreating (.+) from the panel's configuration$"),
        lambda services: f"إعادة إنشاء {ltr(services)} من إعدادات اللوحة",
    ),
]


def translate(text: str) -> str:
    """The Arabic for a job or step title, or the title as it is when there is none."""
    if not text:
        return text
    if text in AR_EXACT:
        return AR_EXACT[text]
    for pattern, make in AR_PATTERNS:
        found = pattern.match(text)
        if found:
            return make(*found.groups())
    return text
