// The steps of the first-run setup wizard (pages/Setup.tsx): their order, which are optional, where a resumed setup
// starts again, and what may be entered. Kept apart from the page so the rules are tested on their own.

import type { SetupDraft, SetupStep } from "@/api/types";

export const STEPS: readonly SetupStep[] = ["welcome", "server", "address", "email", "github", "account", "admin", "done"];

/** Steps that can be skipped: nothing is set up when they are left as they are. */
export const OPTIONAL: ReadonlySet<SetupStep> = new Set(["email", "github", "account"]);

export const indexOf = (step: SetupStep) => STEPS.indexOf(step);
export const next = (step: SetupStep): SetupStep => STEPS[Math.min(indexOf(step) + 1, STEPS.length - 1)]!;
export const back = (step: SetupStep): SetupStep => STEPS[Math.max(indexOf(step) - 1, 0)]!;

/** Where a setup opened again starts: the step saved in the draft, but never past the licence when it has not been
 *  accepted in this browser, and never at "done" (the final confirm has not happened while a draft exists). */
export function resumeAt(saved: SetupStep | undefined, licenceAccepted: boolean): SetupStep {
  if (!licenceAccepted || !saved) return "welcome";
  const at = indexOf(saved);
  if (at < 0) return "welcome";
  return STEPS[Math.min(at, indexOf("admin"))]!;
}

/** A step can be opened from the stepper when it is one already reached (going back) and is not "done". */
export const canOpen = (step: SetupStep, current: SetupStep, reached: SetupStep) =>
  step !== "done" && current !== "done" && indexOf(step) <= indexOf(reached);

/** What keeps Next from going on, by step: a key of the page's words, or null. */
export function blocker(step: SetupStep, draft: SetupDraft): "nameNeeded" | "mailIncomplete" | "linkCodeNeeded" | null {
  if (step === "address") {
    if (draft.exposure === "proxy" && draft.name.kind !== "domain") return "nameNeeded";
    if (draft.name.kind === "domain" && !/^([a-z0-9-]+\.)+[a-z0-9-]{2,}$/i.test(draft.name.domain.trim())) return "nameNeeded";
  }
  if (step === "email" && draft.mail.enabled) {
    const { host, sender, username } = draft.mail;
    if (!host.trim() || !`${sender}${username}`.includes("@")) return "mailIncomplete";
  }
  if (step === "account" && draft.account.link && !/^[A-Z0-9][A-Z0-9-]{3,31}$/.test(draft.account.link_code.trim().toUpperCase()))
    return "linkCodeNeeded";
  return null;
}

/** The setup code from the setup link the installer prints (…/setup#code=XXXX-XXXX-XXXX): it is in the part after
 *  "#", which the browser never sends to a server. */
export function codeFromHash(hash: string): string {
  const match = /(?:^#|&)code=([A-Za-z0-9-]{4,20})(?:&|$)/.exec(hash);
  return match ? match[1]!.toUpperCase() : "";
}
