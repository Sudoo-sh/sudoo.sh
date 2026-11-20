// Short messages the demo's backend shows the visitor (a toast), for what a real server would tell them elsewhere:
// a new server's agent printing its fingerprint, for example. The banner (../index.tsx) says how to show them.

type Notify = (title: string, text: string) => void;
let notify: Notify = () => undefined;

export const setBackendNotifier = (fn: Notify) => {
  notify = fn;
};

export const tell = (title: string, text: string) => notify(title, text);
