/** Resources of a project and of a branch (Odoo.sh's "Database Workers", "Workers Settings" and "Production Database
 *  Size"): the Resources section of a project's Settings tab and the override on a branch's Branch settings tab. A
 *  worker is "عامل", a timeout "مهلة", a scheduled action "إجراء مجدول", a quota "حصة". */
import { ltr } from "./plural.ts";
import type { ResourceKnob } from "@/api/types";

type Knob = { label: string; hint: string; unit?: string };

const en = {
  title: "Resources",
  text: "Workers, timeouts and memory of the project's instances, by stage. Each branch can set its own on its Branch settings tab. Applying restarts the instances that run; production is backed up first and the previous values come back if Odoo does not answer.",
  sizing:
    "About one worker for six people using Odoo at once. On the whole server, Odoo suggests no more workers than processor cores × 2 + 1.",
  readOnly: "You may see these values; changing them needs the Project resources permission.",
  loading: "Reading the resources…",
  notRead: "The resources could not be read.",
  instances: (n: number) => (n === 0 ? "No instance yet" : n === 1 ? "1 instance" : `${n} instances`),
  threaded: "Threaded mode",
  odooDefault: "Odoo's default",
  noLimit: "No limit",
  computed: "Computed from the workers",
  computedValue: (memory: string, cores: string) => `Odoo container: ${memory}, ${cores} cores`,
  follows: (where: string, value: string) => `${where}: ${value}`,
  source: {
    stage: "Stage default",
    project: "Project",
    branch: "This branch",
    instance: "This instance",
    own: "Set here",
  } as Record<string, string>,
  maximum: (value: string) => `At most ${value}`,
  more: "More limits",
  fewer: "Fewer limits",
  decrease: (what: string) => `Fewer ${what}`,
  increase: (what: string) => `More ${what}`,
  apply: "Apply",
  discard: "Discard",
  followDefaults: "Follow the defaults",
  serverLine: (reserved: string, usable: string) => `This server: ${reserved} of ${usable} reserved`,
  serverLineAfter: (reserved: string, usable: string) =>
    `This server: ${reserved} of ${usable} reserved after this change`,
  serverUnknown: "This server's memory has not been measured yet.",
  applyTitle: (stage: string) => `Apply the ${stage} resources?`,
  applyText: (n: number) =>
    n === 0
      ? "No instance uses these values yet: new instances get them."
      : n === 1
        ? "1 instance restarts with them."
        : `${n} instances restart with them.`,
  applyProduction:
    "Production instances are backed up first, then restarted. If Odoo does not answer with the new values, the previous ones are put back.",
  applied: "Resources applied",
  appliedText: (n: number) =>
    n === 0
      ? "New instances get them."
      : n === 1
        ? "1 instance restarts with them."
        : `${n} instances restart with them.`,
  waiting: (names: string) => `Busy or locked, applied at their next start: ${names}`,
  notApplied: "Resources not applied",
  pending: "Restart pending",
  storage: {
    title: "Storage",
    text: "Database and files of each instance, measured every hour. The quota is a soft limit: the administrators are alerted, nothing is stopped.",
    productionSize: "Production database size",
    total: "All instances",
    split: (database: string, files: string) => `Database ${database} · files ${files}`,
    used: (used: string, quota: string) => `${used} of ${quota}`,
    usedNoQuota: (used: string) => `${used}, no quota`,
    notMeasured: "Not measured yet: measured while the instance runs",
    measured: (when: string) => `Measured ${when}`,
    warning: "Near its quota",
    over: "Over its quota",
    none: "No instance yet",
  },
  knobs: {
    odoo_workers: { label: "Workers", hint: "HTTP workers. 0: threaded mode (one process)" },
    odoo_cron_threads: { label: "Cron workers", hint: "Run the scheduled actions" },
    odoo_limit_time_real: { label: "HTTP timeout", hint: "Seconds one request may take", unit: "s" },
    odoo_limit_time_real_cron: {
      label: "Scheduled actions timeout",
      hint: "Seconds one scheduled action may run",
      unit: "s",
    },
    odoo_limit_time_cpu: {
      label: "Processor time per request",
      hint: "Processor seconds one request may use",
      unit: "s",
    },
    odoo_limit_memory_soft_mb: {
      label: "Memory per worker before recycling",
      hint: "MB; the worker is replaced after its request",
      unit: "MB",
    },
    odoo_limit_memory_hard_mb: { label: "Memory per worker at most", hint: "MB; the request is stopped", unit: "MB" },
    odoo_memory_limit: { label: "Odoo container memory", hint: "512m, 4g. Empty with workers: computed" },
    odoo_cpu_limit: { label: "Odoo container cores", hint: "0.5, 2. Empty with workers: computed" },
    db_memory_limit: { label: "Database memory", hint: "512m, 4g" },
    db_cpu_limit: { label: "Database cores", hint: "0.5, 2" },
    db_max_connections: {
      label: "Database connections",
      hint: "PostgreSQL max_connections; shared out between Odoo's processes",
    },
  } as Record<ResourceKnob, Knob>,
  branch: {
    title: "Resources of this branch",
    follows: (stage: string) => `This branch follows the project's ${stage} resources.`,
    own: "This branch has its own values; the others follow the project.",
    override: "Override for this branch",
    stop: "Follow the project",
    text: "Values set here apply to this branch's instances only, within the administrator's maxima.",
  },
  branchOf: (branch: string) => `Resources of ${ltr(branch)}`,
};

const ar: typeof en = {
  title: "الموارد",
  text: "العمّال والمهل والذاكرة لبيئات المشروع، حسب المرحلة. ولكل فرع أن يضبط قيمه في تبويب إعدادات الفرع. يعيد التطبيق تشغيل البيئات العاملة؛ ويُنسخ الإنتاج احتياطيًا أولًا وتعود القيم السابقة إن لم يستجب Odoo.",
  sizing:
    "عامل واحد تقريبًا لكل ستة أشخاص يستخدمون Odoo في الوقت نفسه. وعلى مستوى الخادم كله، يقترح Odoo ألا يتجاوز عدد العمّال أنوية المعالج × 2 + 1.",
  readOnly: "يمكنك رؤية هذه القيم؛ ويتطلب تغييرها صلاحية موارد المشاريع.",
  loading: "جارٍ قراءة الموارد…",
  notRead: "تعذّرت قراءة الموارد.",
  instances: (n) =>
    n === 0 ? "لا بيئات بعد" : n === 1 ? "بيئة واحدة" : n === 2 ? "بيئتان" : n <= 10 ? `${n} بيئات` : `${n} بيئة`,
  threaded: "الوضع متعدد الخيوط",
  odooDefault: "الافتراضي في Odoo",
  noLimit: "بلا حد",
  computed: "محسوبة من العمّال",
  computedValue: (memory, cores) => `حاوية Odoo: ${memory}، ${cores} أنوية`,
  follows: (where, value) => `${where}: ${value}`,
  source: {
    stage: "افتراضي المرحلة",
    project: "المشروع",
    branch: "هذا الفرع",
    instance: "هذه البيئة",
    own: "مضبوطة هنا",
  },
  maximum: (value) => `${value} على الأكثر`,
  more: "حدود أخرى",
  fewer: "حدود أقل",
  decrease: (what) => `إنقاص ${what}`,
  increase: (what) => `زيادة ${what}`,
  apply: "تطبيق",
  discard: "تجاهل",
  followDefaults: "اتّباع القيم الافتراضية",
  serverLine: (reserved, usable) => `هذا الخادم: ${reserved} محجوزة من ${usable}`,
  serverLineAfter: (reserved, usable) => `هذا الخادم: ${reserved} محجوزة من ${usable} بعد هذا التغيير`,
  serverUnknown: "لم تُقس ذاكرة هذا الخادم بعد.",
  applyTitle: (stage) => `تطبيق موارد ${stage}؟`,
  applyText: (n) =>
    n === 0
      ? "لا تستخدم أي بيئة هذه القيم بعد: ستحصل عليها البيئات الجديدة."
      : n === 1
        ? "ستُعاد تشغيل بيئة واحدة بها."
        : n === 2
          ? "ستُعاد تشغيل بيئتين بها."
          : `ستُعاد تشغيل ${n} بيئات بها.`,
  applyProduction:
    "تُنسخ بيئات الإنتاج احتياطيًا أولًا ثم يُعاد تشغيلها. وإن لم يستجب Odoo بالقيم الجديدة، تُعاد القيم السابقة.",
  applied: "طُبّقت الموارد",
  appliedText: (n) =>
    n === 0
      ? "ستحصل عليها البيئات الجديدة."
      : n === 1
        ? "يُعاد تشغيل بيئة واحدة بها."
        : n === 2
          ? "يُعاد تشغيل بيئتين بها."
          : `يُعاد تشغيل ${n} بيئات بها.`,
  waiting: (names) => `مشغولة أو مقفلة، تُطبّق عند تشغيلها التالي: ${names}`,
  notApplied: "لم تُطبّق الموارد",
  pending: "بانتظار إعادة التشغيل",
  storage: {
    title: "التخزين",
    text: "قاعدة بيانات كل بيئة وملفاتها، تُقاس كل ساعة. الحصة حد مرن: يُنبَّه المسؤولون ولا يتوقف شيء.",
    productionSize: "حجم قاعدة بيانات الإنتاج",
    total: "كل البيئات",
    split: (database, files) => `قاعدة البيانات ${database} · الملفات ${files}`,
    used: (used, quota) => `${used} من ${quota}`,
    usedNoQuota: (used) => `${used}، بلا حصة`,
    notMeasured: "لم تُقس بعد: تُقاس أثناء عمل البيئة",
    measured: (when) => `قِيست ${when}`,
    warning: "قريبة من حصتها",
    over: "تجاوزت حصتها",
    none: "لا بيئات بعد",
  },
  knobs: {
    odoo_workers: { label: "العمّال", hint: "عمّال HTTP. ‏0: الوضع متعدد الخيوط (عملية واحدة)" },
    odoo_cron_threads: { label: "عمّال المهام المجدولة", hint: "تنفّذ الإجراءات المجدولة" },
    odoo_limit_time_real: { label: "مهلة HTTP", hint: "الثواني التي قد يستغرقها طلب واحد", unit: "ث" },
    odoo_limit_time_real_cron: {
      label: "مهلة الإجراءات المجدولة",
      hint: "الثواني التي قد يعملها إجراء مجدول واحد",
      unit: "ث",
    },
    odoo_limit_time_cpu: { label: "وقت المعالج لكل طلب", hint: "ثواني المعالج التي قد يستخدمها طلب واحد", unit: "ث" },
    odoo_limit_memory_soft_mb: {
      label: "ذاكرة العامل قبل استبداله",
      hint: "ميغابايت؛ يُستبدل العامل بعد طلبه",
      unit: "MB",
    },
    odoo_limit_memory_hard_mb: { label: "ذاكرة العامل القصوى", hint: "ميغابايت؛ يُوقف الطلب", unit: "MB" },
    odoo_memory_limit: { label: "ذاكرة حاوية Odoo", hint: "512m، 4g. فارغة مع العمّال: محسوبة" },
    odoo_cpu_limit: { label: "أنوية حاوية Odoo", hint: "0.5، 2. فارغة مع العمّال: محسوبة" },
    db_memory_limit: { label: "ذاكرة قاعدة البيانات", hint: "512m، 4g" },
    db_cpu_limit: { label: "أنوية قاعدة البيانات", hint: "0.5، 2" },
    db_max_connections: {
      label: "اتصالات قاعدة البيانات",
      hint: "max_connections في PostgreSQL؛ تُوزّع على عمليات Odoo",
    },
  },
  branch: {
    title: "موارد هذا الفرع",
    follows: (stage) => `يتبع هذا الفرع موارد ${stage} في المشروع.`,
    own: "لهذا الفرع قيمه الخاصة؛ والبقية تتبع المشروع.",
    override: "تجاوز القيم لهذا الفرع",
    stop: "اتّباع المشروع",
    text: "القيم المضبوطة هنا تخص بيئات هذا الفرع وحدها، ضمن الحدود القصوى التي وضعها المسؤول.",
  },
  branchOf: (branch) => `موارد ${ltr(branch)}`,
};

export default { en, ar };
