import { FlaskConical, ShieldCheck, Wrench } from "lucide-react";
import type { Stage } from "@/api/types";
import { useT, words } from "@/i18n";
import common from "@/i18n/common";
import { Tag } from "./app/basics";

const ORDER: Stage[] = ["production", "staging", "development"];
const stageWords = (stage: Stage) => words(common).stages[stage];

/** The stages in the order lists show them, with what each one means in one line, in the panel's language (read
 *  when used: pages are drawn again when the language changes). */
export const STAGES: { value: Stage; label: string; text: string }[] = ORDER.map((value) => ({
  value,
  get label() {
    return stageWords(value).label;
  },
  get text() {
    return stageWords(value).text;
  },
}));

export const STAGE_LABEL = Object.defineProperties(
  {} as Record<Stage, string>,
  Object.fromEntries(ORDER.map((stage) => [stage, { enumerable: true, get: () => stageWords(stage).label }])),
);
export const STAGE_ORDER: Record<Stage, number> = { production: 0, staging: 1, development: 2 };
export const STAGE_ICON: Record<Stage, typeof ShieldCheck> = { production: ShieldCheck, staging: FlaskConical, development: Wrench };

/** The instance's stage as a tag: production stands out, the others stay quiet. */
export function StageTag({ stage }: { stage: Stage }) {
  const Icon = STAGE_ICON[stage] ?? Wrench;
  const label = useT(common).stages[stage]?.label;
  return (
    <Tag tone={stage === "production" ? "accent" : undefined} icon={<Icon strokeWidth={1.75} />}>
      {label ?? stage}
    </Tag>
  );
}
