// The words of "Explain with AI" (components/ExplainWithAi.tsx): the button of a failed build, the preview of exactly
// what would be sent, the answer, and the administrator's settings. Off until an administrator turns it on.

const en = {
  button: "Explain with AI",
  working: "Preparing the preview…",
  previewTitle: "Review what will be sent",
  previewText: (host: string, model: string) =>
    `Nothing has been sent yet. If you confirm, this exact text goes to ${host} (model ${model}).`,
  hidden: (n: number) =>
    n === 0 ? "The scrubber changed nothing in this excerpt." : `The scrubber hid ${n} item${n === 1 ? "" : "s"}.`,
  size: (bytes: number) => `${bytes} bytes in total`,
  privacy:
    "Pattern scrubbing hides what it recognises (keys, passwords, tokens, addresses), but it cannot guarantee that no secret or personal text is left in. Read the text first. A hosted service receives it under its own terms; nothing is promised from the kind of endpoint alone.",
  expires: "This preview works once and for five minutes.",
  confirm: "Send and explain",
  cancel: "Cancel",
  sending: "Sending…",
  answerTitle: "Explanation",
  generated: (model: string) =>
    `Written by a model (${model}). It can be wrong or be misled by text in the log. Check it before you act on it; nothing was run or changed.`,
  close: "Close",
  noPayload: "The text to send could not be shown.",
  reasons: {
    off: "AI explanations are off.",
    not_configured: "AI explanations are not set up yet.",
    project_off: "AI explanations are not turned on for this project.",
    not_failed: "Only a failed build can be explained.",
    no_details: "This build has no saved error lines to explain.",
  } as Record<string, string>,
  turnOn: "Turn on for this project",
  turnOff: "Turn off for this project",
  settings: {
    title: "AI explanations of failed builds",
    text: "Optional. When on for a project, a person who can read that branch's logs can send a short scrubbed excerpt of a failed build to the service below, after seeing exactly what is sent and confirming. Nothing is sent automatically.",
    enabled: "Allow AI explanations",
    endpoint: "Endpoint (chat completions URL)",
    endpointHint:
      "Must be listed, as host:port, in EXPLAIN_ALLOWED_DESTINATIONS in the server's settings, and must be a public address. Servers on a private network are not supported in this version.",
    model: "Model name",
    key: "API key",
    keySet: "A key is saved. Leave empty to keep it.",
    keyNone: "No key saved.",
    keyHint: "Saved in the panel's vault and never shown again.",
    save: "Save",
    saved: "Saved",
    problem: "The endpoint is not allowed",
    limits: (user: number, project: number) =>
      `Limits per hour: ${user} per person and ${project} per project, one request at a time. These limit requests; they are not a cap on cost.`,
    projects: (n: number) => `Turned on for ${n} project${n === 1 ? "" : "s"}.`,
  },
};

const ar: typeof en = {
  button: "اشرح بالذكاء الاصطناعي",
  working: "جارٍ تجهيز المعاينة…",
  previewTitle: "راجع ما سيُرسل",
  previewText: (host, model) => `لم يُرسل شيء بعد. إذا أكدت، سيُرسل هذا النص نفسه إلى ${host} (النموذج ${model}).`,
  hidden: (n) =>
    n === 0 ? "لم يغيّر المنقّي شيئًا في هذا المقتطف." : n === 1 ? "أخفى المنقّي عنصرًا واحدًا." : `أخفى المنقّي ${n} عناصر.`,
  size: (bytes) => `${bytes} بايت في المجموع`,
  privacy:
    "يخفي التنقية بالأنماط ما يتعرّف عليه (المفاتيح وكلمات المرور والرموز والعناوين)، لكنه لا يضمن ألا يبقى سرّ أو نص شخصي. اقرأ النص أولًا. الخدمة المستضافة تستلمه وفق شروطها هي، ولا يُوعد بشيء بناءً على نوع الوجهة وحده.",
  expires: "هذه المعاينة تعمل مرة واحدة ولمدة خمس دقائق.",
  confirm: "أرسل واشرح",
  cancel: "إلغاء",
  sending: "جارٍ الإرسال…",
  answerTitle: "الشرح",
  generated: (model) =>
    `كتبه نموذج (${model}). قد يخطئ أو ينخدع بنص في السجل. تحقق منه قبل أن تعمل به؛ لم يُنفَّذ أو يُغيَّر شيء.`,
  close: "إغلاق",
  noPayload: "تعذّر عرض النص المراد إرساله.",
  reasons: {
    off: "الشرح بالذكاء الاصطناعي متوقف.",
    not_configured: "الشرح بالذكاء الاصطناعي لم يُعدّ بعد.",
    project_off: "الشرح بالذكاء الاصطناعي غير مفعّل لهذا المشروع.",
    not_failed: "يمكن شرح البناء الفاشل فقط.",
    no_details: "لا توجد أسطر خطأ محفوظة في هذا البناء لشرحها.",
  },
  turnOn: "فعّله لهذا المشروع",
  turnOff: "أوقفه لهذا المشروع",
  settings: {
    title: "شرح البناءات الفاشلة بالذكاء الاصطناعي",
    text: "اختياري. عند تفعيله لمشروع، يستطيع من يقرأ سجلات الفرع إرسال مقتطف قصير منقّى من بناء فاشل إلى الخدمة أدناه، بعد أن يرى ما سيُرسل بالضبط ويؤكد. لا يُرسل شيء تلقائيًا.",
    enabled: "السماح بالشرح بالذكاء الاصطناعي",
    endpoint: "الوجهة (رابط chat completions)",
    endpointHint:
      "يجب أن تكون مدرجة بصيغة host:port في EXPLAIN_ALLOWED_DESTINATIONS ضمن إعدادات الخادم، وأن تكون عنوانًا عامًا. الخوادم في شبكة خاصة غير مدعومة في هذا الإصدار.",
    model: "اسم النموذج",
    key: "مفتاح API",
    keySet: "المفتاح محفوظ. اتركه فارغًا للإبقاء عليه.",
    keyNone: "لا يوجد مفتاح محفوظ.",
    keyHint: "يُحفظ في خزنة اللوحة ولا يُعرض مرة أخرى.",
    save: "حفظ",
    saved: "تم الحفظ",
    problem: "الوجهة غير مسموحة",
    limits: (user, project) =>
      `الحدود في الساعة: ${user} لكل شخص و${project} لكل مشروع، وطلب واحد في كل مرة. هذه حدود للطلبات وليست سقفًا للتكلفة.`,
    projects: (n) => (n === 1 ? "مفعّل لمشروع واحد." : `مفعّل لـ ${n} مشاريع.`),
  },
};

export default { en, ar };
