// The progress of the first-run setup wizard: one dot per step with its name, a check mark on the steps done (it
// pops in once), the current one in plum; steps already reached can be opened again. On a phone only the dots show,
// with "Step 3 of 8" and the step's name under them.

import { Check } from "lucide-react";
import type { SetupStep } from "@/api/types";
import { useT } from "@/i18n";
import words from "@/i18n/setup";
import { canOpen, indexOf, OPTIONAL, STEPS } from "@/lib/setup-steps";

export function SetupStepper({
  current,
  reached,
  onOpen,
}: {
  current: SetupStep;
  reached: SetupStep;
  onOpen: (step: SetupStep) => void;
}) {
  const t = useT(words);
  const at = indexOf(current);
  return (
    <nav className="setup-stepper" aria-label={t.stepOf(at + 1, STEPS.length)}>
      <ol>
        {STEPS.map((step, i) => {
          const state = i < at || current === "done" ? "done" : i === at ? "current" : "todo";
          const open = canOpen(step, current, reached) && step !== current;
          return (
            <li key={step} data-state={state} aria-current={state === "current" ? "step" : undefined}>
              <button type="button" disabled={!open} onClick={() => onOpen(step)}>
                <span className="dot">{state === "done" ? <Check aria-hidden="true" /> : i + 1}</span>
                <span className="name">
                  {t.steps[step]}
                  {OPTIONAL.has(step) && <span className="sr-only"> ({t.optional})</span>}
                </span>
              </button>
            </li>
          );
        })}
      </ol>
      <p className="setup-stepper-phone">
        <span className="faint">{t.stepOf(at + 1, STEPS.length)}</span> {t.steps[current]}
      </p>
    </nav>
  );
}
