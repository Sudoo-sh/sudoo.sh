/** The panel's first-visit tour (components/Tour.tsx, lib/tour.ts). The names of the pages are the sidebar's own words. */
const en = {
  stepOf: (n: number, of: number) => `Step ${n} of ${of}`,
  back: "Back",
  next: "Next",
  done: "Done",
  skip: "Skip",
  learnMore: "Learn more",
  label: "Tour of the panel",
  replay: "Show the tour again",
  replayHint: "The welcome tour shows once for your account. This shows it again.",
  narrowTitle: "The tour needs a wider screen",
  narrowText:
    "The tour points at the side menu, which is closed on a narrow screen. Widen the window or turn your device sideways, then choose Show the tour again.",
  notSaved: "Not saved",
  notSavedText: "The tour may show again the next time you sign in.",
  checkFailed: "Could not start the tour",
  checkFailedText: "The panel could not check the tour settings just now. Try again in a moment.",
  off: "The administrator has turned the tours off for this panel.",
  takeTour: "Take the tour",
  nothingTitle: "Nothing to show yet",
  nothingText: "This page has nothing for the tour to point at yet. Come back when it has some content.",
  steps: {
    "projects-new": {
      title: "New project",
      body: "Connect a GitHub repository here. Each project is one repository.",
    },
    "projects-grid": {
      title: "Your projects",
      body: "Each card is a project, with its branches and the instances they run.",
    },
    "backups-list": {
      title: "Backups of an instance",
      body: "Backups are grouped by instance, newest first, with the kind, the time taken and the size.",
    },
    "instances-new": {
      title: "New instance",
      body: "Start a new Odoo instance here. You choose its version and options on the next page.",
    },
    "instances-card": {
      title: "Your instances",
      body: "Each card is one instance, with its state, version and address. Open it for its own page.",
    },
    "settings-tabs": {
      title: "Settings tabs",
      body: "Settings are grouped in tabs. Pick a tab to see its groups of settings.",
    },
    "settings-section": {
      title: "A group of settings",
      body: "Each group has a title and a short note on what it controls, then its settings one by one.",
    },
    "plugins-tabs": {
      title: "Plugin lists",
      body: "Switch between the plugins you can add and the ones already installed.",
    },
    "plugins-card": {
      title: "A plugin",
      body: "Each card is one plugin. Open it to read about it before you install anything.",
    },
    "accounts-new": {
      title: "Add an account",
      body: "Add a person here, with a role that decides what they can do. This only opens the form.",
    },
    "accounts-rows": {
      title: "Your accounts",
      body: "Each row is a person, with their role and when they last signed in. The menu at the end of a row lists what you can do for that person.",
    },
    "tokens-new": {
      title: "Make a token",
      body: "A token lets a script or another tool use the panel's API. This only opens the form.",
    },
    "tokens-rows": {
      title: "Your tokens",
      body: "Each row is one token, with when it was made and last used. Revoke stops a token working, and asks you first.",
    },
    "templates-list": {
      title: "Pick an e-mail",
      body: "Each item is an e-mail the panel sends, and who gets it. One you have changed is marked.",
    },
    "templates-editor": {
      title: "Its text",
      body: "Read or change the wording here. A change is kept only when you save it.",
    },
    "github-app": {
      title: "The GitHub App",
      body: "The App lets the panel read your private repositories. Public repositories need nothing.",
    },
    "github-keys": {
      title: "Deploy keys",
      body: "One key per repository, for servers that do not use the App.",
    },
    "maintenance-card": {
      title: "A planned change",
      body: "Each card says why a change is needed, what it works on, and its window.",
    },
    "audit-filters": {
      title: "Find an entry",
      body: "Search the log, or narrow it by who, what, which instance and when.",
    },
    "audit-export": {
      title: "Export the log",
      body: "Download what you see as a CSV or an Excel file. Opening this menu changes nothing.",
    },
    "audit-rows": {
      title: "Who did what",
      body: "Each row is one recorded action, newest first, with the person, the instance and the address.",
    },
    "backups-actions": {
      title: "Download or delete",
      body: "Download a backup here. The bin deletes it for good, and asks you first. To restore one, open the instance's page.",
    },
    intro: {
      title: "Welcome to your panel",
      body: "A short tour shows where the main things are. You can skip it now and show it again from Help.",
    },
    projects: {
      title: "Projects",
      body: "A project is one Git repository. Each of its branches gets its own Odoo. Start here to connect GitHub.",
    },
    "new-instance": {
      title: "New instance",
      body: "Create an Odoo from a branch of your GitHub repository, or a plain Odoo with no repository.",
    },
    backups: {
      title: "Backups",
      body: "Every backup of every instance is listed here, with a restore for each.",
    },
    help: {
      title: "Help",
      body: "The manual is here, with a page for each common job, in English and Arabic.",
    },
  },
};

const ar: typeof en = {
  stepOf: (n, of) => `الخطوة ${n} من ${of}`,
  back: "السابق",
  next: "التالي",
  done: "تم",
  skip: "تخطَّ",
  learnMore: "اعرف المزيد",
  label: "جولة في اللوحة",
  replay: "أظهر الجولة مرة أخرى",
  replayHint: "تظهر جولة الترحيب مرة واحدة لحسابك. هذا الزر يظهرها من جديد.",
  narrowTitle: "الجولة تحتاج شاشة أعرض",
  narrowText:
    "تشير الجولة إلى القائمة الجانبية، وهي مغلقة في الشاشة الضيقة. وسّع النافذة أو أدر جهازك أفقيًا، ثم اختر أظهر الجولة مرة أخرى.",
  notSaved: "لم يُحفظ",
  notSavedText: "قد تظهر الجولة من جديد في المرة القادمة التي تسجّل فيها الدخول.",
  checkFailed: "تعذّر بدء الجولة",
  checkFailedText: "تعذّر على اللوحة التحقق من إعدادات الجولة الآن. حاول مرة أخرى بعد قليل.",
  off: "أوقف المسؤول الجولات في هذه اللوحة.",
  takeTour: "ابدأ الجولة",
  nothingTitle: "لا شيء لعرضه بعد",
  nothingText: "لا يوجد في هذه الصفحة ما تشير إليه الجولة بعد. عُد حين يظهر فيها محتوى.",
  steps: {
    "projects-new": {
      title: "مشروع جديد",
      body: "اربط مستودعًا من GitHub من هنا. كل مشروع هو مستودع واحد.",
    },
    "projects-grid": {
      title: "مشاريعك",
      body: "كل بطاقة مشروع، مع فروعه والبيئات التي تعمل منها.",
    },
    "backups-list": {
      title: "النسخ الاحتياطية لبيئة",
      body: "تُجمَّع النسخ حسب البيئة، الأحدث أولًا، مع النوع ووقت الأخذ والحجم.",
    },
    "instances-new": {
      title: "بيئة جديدة",
      body: "ابدأ بيئة Odoo جديدة من هنا. تختار إصدارها وخياراتها في الصفحة التالية.",
    },
    "instances-card": {
      title: "بيئاتك",
      body: "كل بطاقة بيئة واحدة، مع حالتها وإصدارها وعنوانها. افتحها لترى صفحتها.",
    },
    "settings-tabs": {
      title: "تبويبات الإعدادات",
      body: "الإعدادات مجمَّعة في تبويبات. اختر تبويبًا لترى مجموعات إعداداته.",
    },
    "settings-section": {
      title: "مجموعة إعدادات",
      body: "لكل مجموعة عنوان وملاحظة قصيرة عمّا تتحكم به، ثم إعداداتها واحدًا واحدًا.",
    },
    "plugins-tabs": {
      title: "قوائم الإضافات",
      body: "انتقل بين الإضافات التي يمكنك إضافتها والإضافات المثبَّتة.",
    },
    "plugins-card": {
      title: "إضافة",
      body: "كل بطاقة إضافة واحدة. افتحها لتقرأ عنها قبل أن تثبّت أي شيء.",
    },
    "accounts-new": {
      title: "أضف حسابًا",
      body: "أضف شخصًا من هنا، بدور يحدد ما يستطيع فعله. هذا يفتح النموذج فقط.",
    },
    "accounts-rows": {
      title: "حساباتك",
      body: "كل صف شخص، مع دوره وآخر تسجيل دخول له. قائمة نهاية الصف تعرض ما يمكنك فعله لهذا الشخص.",
    },
    "tokens-new": {
      title: "أنشئ رمزًا",
      body: "الرمز يتيح لسكربت أو أداة أخرى استخدام واجهة اللوحة البرمجية. هذا يفتح النموذج فقط.",
    },
    "tokens-rows": {
      title: "رموزك",
      body: "كل صف رمز واحد، مع وقت إنشائه وآخر استخدام له. الإلغاء يوقف عمل الرمز ويسألك أولًا.",
    },
    "templates-list": {
      title: "اختر رسالة",
      body: "كل عنصر رسالة بريد ترسلها اللوحة، ومن يستلمها. التي غيّرتها تظهر عليها علامة.",
    },
    "templates-editor": {
      title: "نصها",
      body: "اقرأ الصياغة هنا أو غيّرها. لا يُحفظ التغيير إلا عندما تحفظه.",
    },
    "github-app": {
      title: "تطبيق GitHub",
      body: "يتيح التطبيق للوحة قراءة مستودعاتك الخاصة. المستودعات العامة لا تحتاج إلى شيء.",
    },
    "github-keys": {
      title: "مفاتيح النشر",
      body: "مفتاح لكل مستودع، للخوادم التي لا تستخدم التطبيق.",
    },
    "maintenance-card": {
      title: "تغيير مخطّط",
      body: "كل بطاقة تذكر لماذا يلزم التغيير، وعلى ماذا يعمل، ونافذته.",
    },
    "audit-filters": {
      title: "ابحث عن سجل",
      body: "ابحث في السجل أو ضيّقه حسب الشخص والحدث والبيئة والوقت.",
    },
    "audit-export": {
      title: "صدّر السجل",
      body: "نزّل ما تراه بصيغة CSV أو Excel. فتح هذه القائمة لا يغيّر شيئًا.",
    },
    "audit-rows": {
      title: "من فعل ماذا",
      body: "كل صف إجراء مسجَّل، الأحدث أولًا، مع الشخص والبيئة والعنوان.",
    },
    "backups-actions": {
      title: "التنزيل أو الحذف",
      body: "نزّل نسخة من هنا. وزر سلة المهملات يحذفها نهائيًا ويسألك أولًا. ولاستعادة نسخة افتح صفحة البيئة.",
    },
    intro: {
      title: "مرحبًا بك في لوحتك",
      body: "جولة قصيرة تريك أين أهم الأشياء. يمكنك تخطيها الآن وإظهارها من جديد من المساعدة.",
    },
    projects: {
      title: "المشاريع",
      body: "المشروع هو مستودع Git واحد. يحصل كل فرع فيه على Odoo خاص به. ابدأ من هنا لربط GitHub.",
    },
    "new-instance": {
      title: "بيئة جديدة",
      body: "أنشئ Odoo من فرع في مستودعك على GitHub، أو Odoo عادي بلا مستودع.",
    },
    backups: {
      title: "النسخ الاحتياطية",
      body: "تجد هنا كل نسخة احتياطية لكل بيئة، مع استعادة لكل واحدة.",
    },
    help: {
      title: "المساعدة",
      body: "الدليل هنا، وفيه صفحة لكل مهمة شائعة، بالإنجليزية والعربية.",
    },
  },
};

export default { en, ar };
