/** The Kubernetes engine (beta): the engine choice of a new instance, the instance's engine and its move, and the
 *  cluster's card in Settings › Servers. "Engine" is "المحرّك", a node "عقدة", a cluster "عنقود". */

const en = {
  // New instance
  engine: "Engine",
  engineText: "Where the instance's Odoo and database run on this server.",
  docker: "Docker",
  dockerText: "One Docker Compose project. The default, with every feature.",
  kubernetes: "Kubernetes",
  kubernetesText:
    "Its own namespace on the k3s cluster, isolated by network policies. Beta: Community only, no mail catcher yet.",
  beta: "Beta",
  // the instance
  onKubernetes: "Runs on Kubernetes (beta)",
  move: "Move to another engine",
  moveTitle: (to: string) => `Move to ${to}`,
  moveText: (name: string, to: string) =>
    `${name} is switched off, backed up, removed from its engine and created on ${to} from that backup. Its name, link, database, files and passwords stay. If it does not start there, it is put back where it was.`,
  moveDowntime: "Visitors see the “switched off” page until the move ends: about as long as a backup and a restore.",
  moveProduction: "This is a production instance.",
  moveConfirm: "Move",
  moveJob: "Move to another engine",
  // Settings › Servers
  cluster: "Kubernetes (beta)",
  clusterText: "The k3s cluster the Kubernetes engine runs instances on, and its nodes.",
  off: (script: string, setting: string) =>
    `The Kubernetes engine is off. Run ${script} on the server: it installs k3s and sets ${setting} in the panel's .env.`,
  unreachable: "The panel cannot reach the cluster:",
  nodes: "Nodes",
  node: "Node",
  roles: "Roles",
  status: "Status",
  version: "Version",
  cpu: "Processor",
  memory: "Memory",
  ready: "Ready",
  notReady: "Not ready",
  paused: "Not taking instances",
  cores: (used: string | null, total: string) => (used == null ? `${total} cores` : `${used} of ${total} cores`),
  used: (used: string | null, total: string) => (used == null ? total : `${used} of ${total}`),
  addNode: "Add a node",
  addTitle: "Add a node to the cluster",
  addText:
    "Run this command as root on the new server. It installs k3s and joins this cluster; the node shows here within a minute.",
  addOnce: "The command holds a join token: it is shown only now, and the panel does not keep it.",
  expires: (when: string) => `The token works until ${when}.`,
  ports: "Open TCP 6443 and UDP 8472 between your servers only, never to the internet.",
  needsUrl: (setting: string) => `Set ${setting} (the address new nodes reach this server at) to add nodes.`,
  notAdded: "The join command could not be made",
  done: "Done",
  help: "How it works",
};

const ar: typeof en = {
  engine: "المحرّك",
  engineText: "أين يعمل Odoo وقاعدة بيانات المثيل على هذا الخادم.",
  docker: "Docker",
  dockerText: "مشروع Docker Compose واحد. الخيار الافتراضي، بكل الميزات.",
  kubernetes: "Kubernetes",
  kubernetesText:
    "نطاق أسماء خاص به على عنقود k3s، معزول بسياسات الشبكة. تجريبي: Community فقط، ولا يدعم ملتقط البريد بعد.",
  beta: "تجريبي",
  onKubernetes: "يعمل على Kubernetes (تجريبي)",
  move: "النقل إلى محرّك آخر",
  moveTitle: (to: string) => `النقل إلى ${to}`,
  moveText: (name: string, to: string) =>
    `يُطفأ ${name} ثم تُؤخذ له نسخة احتياطية ويُزال من محرّكه، ثم يُنشأ على ${to} من تلك النسخة. يبقى اسمه ورابطه وقاعدة بياناته وملفاته وكلمات مروره كما هي. وإن لم يعمل هناك يُعاد إلى حيث كان.`,
  moveDowntime: "يرى الزوار صفحة «مطفأ» حتى ينتهي النقل: نحو مدة نسخة احتياطية واستعادتها.",
  moveProduction: "هذا مثيل إنتاج.",
  moveConfirm: "انقل",
  moveJob: "النقل إلى محرّك آخر",
  cluster: "Kubernetes (تجريبي)",
  clusterText: "عنقود k3s الذي يشغّل عليه محرّك Kubernetes المثيلات، وعُقده.",
  off: (script: string, setting: string) =>
    `محرّك Kubernetes متوقف. شغّل الأمر ${script} على الخادم: يثبّت k3s ويضبط ${setting} في ملف ‎.env للوحة.`,
  unreachable: "لا تصل اللوحة إلى العنقود:",
  nodes: "العُقد",
  node: "العقدة",
  roles: "الأدوار",
  status: "الحالة",
  version: "الإصدار",
  cpu: "المعالج",
  memory: "الذاكرة",
  ready: "جاهزة",
  notReady: "غير جاهزة",
  paused: "لا تستقبل مثيلات",
  cores: (used: string | null, total: string) => (used == null ? `${total} أنوية` : `${used} من ${total} أنوية`),
  used: (used: string | null, total: string) => (used == null ? total : `${used} من ${total}`),
  addNode: "إضافة عقدة",
  addTitle: "إضافة عقدة إلى العنقود",
  addText:
    "شغّل هذا الأمر بصلاحية الجذر على الخادم الجديد. يثبّت k3s وينضم إلى هذا العنقود، وتظهر العقدة هنا خلال دقيقة.",
  addOnce: "يحمل الأمر رمز انضمام: يظهر الآن فقط، ولا تحتفظ به اللوحة.",
  expires: (when: string) => `يعمل الرمز حتى ${when}.`,
  ports: "افتح المنفذ TCP 6443 والمنفذ UDP 8472 بين خوادمك فقط، ولا تفتحهما للإنترنت أبدًا.",
  needsUrl: (setting: string) => `اضبط ${setting} (العنوان الذي تصل به العُقد الجديدة إلى هذا الخادم) لإضافة عُقد.`,
  notAdded: "تعذّر إنشاء أمر الانضمام",
  done: "تم",
  help: "كيف يعمل",
};

export default { en, ar };
