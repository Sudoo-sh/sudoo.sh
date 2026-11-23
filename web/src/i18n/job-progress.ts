/** A job under way, as the job drawer and an instance's header show it: the step it is on, how far, how long. */

const en = {
  stepOf: (n: number, total: number) => `Step ${n} of ${total}`,
  step: (n: number) => `Step ${n}`,
  stepsLeft: (n: number) => `Steps left: ${n}`,
  queued: "Waiting for another job on this instance to finish",
  starting: "Starting…",
  running: "In progress",
  open: "Show the steps",
  elapsed: (time: string) => `Running for ${time}`,
};

const ar: typeof en = {
  stepOf: (n: number, total: number) => `الخطوة ${n} من ${total}`,
  step: (n: number) => `الخطوة ${n}`,
  stepsLeft: (n: number) => `الخطوات المتبقية: ${n}`,
  queued: "في انتظار انتهاء مهمة أخرى على هذه البيئة",
  starting: "جارٍ البدء…",
  running: "قيد التنفيذ",
  open: "اعرض الخطوات",
  elapsed: (time: string) => `قيد التشغيل منذ ${time}`,
};

export default { en, ar };
