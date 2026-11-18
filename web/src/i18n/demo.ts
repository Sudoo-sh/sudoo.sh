/** The demo's banner (demo.sudoo.sh): what this is, where to install it, and whose eyes to look with. */

const en = {
  about: "About this demo",
  demo: "Demo",
  signInAny: "Sign in with any e-mail address and password.",
  sampleData: "with sample data. Nothing here is real.",
  viewingAs: "Viewing as",
  viewingAsRole: (role: string) => `Viewing as ${role}`,
  install: "Install on your server",
  installShort: "Install",
  roles: { super_admin: "Super admin", admin: "Admin", developer: "Developer", viewer: "Viewer" } as Record<string, string>,
};

const ar: typeof en = {
  about: "عن هذا العرض التجريبي",
  demo: "عرض تجريبي",
  signInAny: "سجّل الدخول بأي بريد إلكتروني وكلمة مرور.",
  sampleData: "ببيانات تجريبية. لا شيء هنا حقيقي.",
  viewingAs: "العرض بصفة",
  viewingAsRole: (role) => `العرض بصفة ${role}`,
  install: "ثبّته على خادمك",
  installShort: "ثبّت",
  roles: { super_admin: "مسؤول أعلى", admin: "مسؤول", developer: "مطوّر", viewer: "مشاهد" },
};

export default { en, ar };
