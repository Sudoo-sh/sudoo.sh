/** The "Waiting for capacity" card (CAPACITY_QUEUE): requests that wait for room under a running-instances limit. */
const en = {
  title: "Waiting for capacity",
  text: "Requests that can start do so by themselves, in queue order, when a running-instances limit has room.",
  textAll:
    "Everyone's waiting requests. Requests that can start do so by themselves, in queue order, when a running-instances limit has room.",
  position: (n: number | null) => `Position ${n ?? "?"}`,
  create: "New instance",
  start: "Start",
  by: (user: string) => `asked by ${user}`,
  cancel: "Cancel",
  cancelLabel: (name: string) => `Cancel the waiting request for ${name}`,
  front: "Move to front",
  frontLabel: (name: string) => `Move ${name} to the front of the line`,
  cancelled: (name: string) => `${name} left the line.`,
  moved: (name: string) => `${name} is first in line.`,
  failed: "Could not change the line",
  queued: (name: string, position: number | null) => `${name} is waiting for capacity (position ${position ?? "?"}).`,
  queuedText: "It starts by itself when there is room.",
};
const ar: typeof en = {
  title: "بانتظار السعة",
  text: "الطلبات التي يمكنها البدء تبدأ تلقائياً، بترتيب الطابور، عندما يتوفر مكان تحت حد البيئات العاملة.",
  textAll:
    "طلبات الجميع المنتظرة. الطلبات التي يمكنها البدء تبدأ تلقائياً، بترتيب الطابور، عندما يتوفر مكان تحت حد البيئات العاملة.",
  position: (n) => `الترتيب ${n ?? "؟"}`,
  create: "بيئة جديدة",
  start: "تشغيل",
  by: (user) => `طلبها ${user}`,
  cancel: "إلغاء",
  cancelLabel: (name) => `إلغاء طلب الانتظار الخاص بـ ${name}`,
  front: "نقل إلى المقدمة",
  frontLabel: (name) => `نقل ${name} إلى مقدمة الطابور`,
  cancelled: (name) => `غادرت ${name} الطابور.`,
  moved: (name) => `${name} الأولى في الطابور.`,
  failed: "تعذّر تغيير الطابور",
  queued: (name, position) => `${name} بانتظار السعة (الترتيب ${position ?? "؟"}).`,
  queuedText: "تبدأ تلقائياً عندما يتوفر مكان.",
};
export default { en, ar };
