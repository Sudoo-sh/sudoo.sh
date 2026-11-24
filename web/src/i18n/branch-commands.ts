/** The command toolbar of a project's branch (Odoo.sh's Clone, Fork, Merge, SSH, SQL, Submodule, Delete): a tab per
 *  task and the command for this branch, ready to copy. Commands stay in Latin script. */
import { ltr } from "./plural.ts";

const en = {
  label: "Commands for this branch",
  tabs: {
    clone: "Clone",
    fork: "Fork",
    merge: "Merge",
    ssh: "SSH",
    sql: "SQL",
    submodule: "Submodule",
    delete: "Delete",
  },
  example: "Example:",
  copy: "Copy the command",
  copied: "Copied",
  clone: "Download the branch with its submodules.",
  fork: "Make a new branch from this one and push it: it appears in Development.",
  forkName: "New branch",
  merge: "Merge another branch into this one and push it: the branch's instance gets the new code.",
  mergeFrom: "Branch to merge",
  ssh: "A shell in the instance's Odoo container, from your computer through SSH to the server.",
  sshNoHost: "Replace <server> with the server's SSH name or address.",
  sql: "The instance's database in psql, run on the server.",
  webShell: "Open the web shell",
  noNames: "Your access in this branch does not include the shell or the credentials.",
  submodule: "Add a repository of modules (OCA, a shared one) to this branch as a submodule, then push.",
  addSubmodule: "Add submodule",
  deleteText: (instance: string) =>
    `Deleting ${ltr(instance)} removes its containers, database and files after a last backup. The branch stays on GitHub; delete it there with:`,
  deleteInstance: "Delete instance",
  noRight: "Your role cannot do this in this branch.",
};

const ar: typeof en = {
  label: "أوامر هذا الفرع",
  tabs: {
    clone: "استنساخ",
    fork: "تفريع",
    merge: "دمج",
    ssh: "SSH",
    sql: "SQL",
    submodule: "وحدة فرعية",
    delete: "حذف",
  },
  example: "مثال:",
  copy: "نسخ الأمر",
  copied: "نُسخ",
  clone: "نزّل الفرع مع وحداته الفرعية.",
  fork: "أنشئ فرعًا جديدًا من هذا الفرع وادفعه: يظهر في التطوير.",
  forkName: "الفرع الجديد",
  merge: "ادمج فرعًا آخر في هذا الفرع وادفعه: تحصل بيئة الفرع على الشيفرة الجديدة.",
  mergeFrom: "الفرع المراد دمجه",
  ssh: "واجهة أوامر في حاوية Odoo الخاصة بالبيئة، من حاسوبك عبر SSH إلى الخادم.",
  sshNoHost: "استبدل <server> باسم الخادم أو عنوانه في SSH.",
  sql: "قاعدة بيانات البيئة في psql، تُشغَّل على الخادم.",
  webShell: "فتح واجهة الأوامر في المتصفح",
  noNames: "لا يشمل وصولك في هذا الفرع واجهة الأوامر ولا بيانات الدخول.",
  submodule: "أضف مستودع وحدات (من OCA أو مستودعًا مشتركًا) إلى هذا الفرع كوحدة فرعية، ثم ادفعه.",
  addSubmodule: "إضافة وحدة فرعية",
  deleteText: (instance) =>
    `يزيل حذف ${ltr(instance)} حاوياتها وقاعدة بياناتها وملفاتها بعد نسخة احتياطية أخيرة. يبقى الفرع على GitHub؛ احذفه هناك بالأمر:`,
  deleteInstance: "حذف البيئة",
  noRight: "لا يسمح دورك بهذا في هذا الفرع.",
};

export default { en, ar };
