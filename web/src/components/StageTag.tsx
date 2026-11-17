import { FlaskConical, ShieldCheck, Wrench } from "lucide-react";
import type { Stage } from "@/api/types";
import { Tag } from "./ui/basics";

/** The stages in the order lists show them, with what each one means in one line. */
export const STAGES: { value: Stage; label: string; text: string }[] = [
  { value: "production", label: "Production", text: "The live database. Never paused or deleted automatically, backed up every day, updated safely." },
  { value: "staging", label: "Staging", text: "A rehearsal of production: tests before release, with a longer lifetime." },
  { value: "development", label: "Development", text: "Day-to-day work on a branch. Pauses when idle and has a lifetime." },
];

export const STAGE_LABEL: Record<Stage, string> = { production: "Production", staging: "Staging", development: "Development" };
export const STAGE_ORDER: Record<Stage, number> = { production: 0, staging: 1, development: 2 };
export const STAGE_ICON: Record<Stage, typeof ShieldCheck> = { production: ShieldCheck, staging: FlaskConical, development: Wrench };

/** The instance's stage as a tag: production stands out, the others stay quiet. */
export function StageTag({ stage }: { stage: Stage }) {
  const Icon = STAGE_ICON[stage] ?? Wrench;
  return (
    <Tag tone={stage === "production" ? "accent" : undefined} icon={<Icon strokeWidth={1.75} />}>
      {STAGE_LABEL[stage] ?? stage}
    </Tag>
  );
}
