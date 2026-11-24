// A job under way, at a glance: the step it is on (highlighted, with a calm breathing mark), which step of how many,
// its progress in words and as a bar, and how long it has run. Used at the top of the job drawer and in an instance's
// header while a job runs on it. The steps themselves are JobSteps (JobDrawer.tsx); "Why it failed" stays below.

import { ChevronRight } from "lucide-react";
import { useEffect, useState } from "react";
import type { CurrentJob, Job, JobStep } from "@/api/types";
import { useT } from "@/i18n";
import words from "@/i18n/job-progress";
import { jobTitle, progressText, stepTitle } from "@/i18n/jobs";
import { elapsed, percent as asPercent } from "@/lib/format";
import { Progress } from "./app/basics";

type Shown = Pick<Job, "action" | "status" | "progress_percent" | "steps" | "expected_steps"> & {
  started_at?: string | null;
};

/** Re-render every second while `on`, so the time keeps counting between live events. */
function useSecondTick(on: boolean) {
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!on) return;
    const timer = window.setInterval(() => setTick((n) => n + 1), 1000);
    return () => window.clearInterval(timer);
  }, [on]);
}

/** The running step, its number and the number of steps (when the job announced it). */
export function currentStep(job: Pick<Shown, "steps" | "expected_steps">): {
  step: JobStep | null;
  index: number;
  total: number | null;
} {
  const steps = job.steps ?? [];
  const index = steps.findLastIndex((step) => step.status === "running");
  const total = job.expected_steps ? Math.max(job.expected_steps, steps.length) : null;
  return { step: steps[index] ?? null, index: index + 1, total };
}

/** The summary of a job under way: nothing once it has ended (the drawer shows its status and steps then). */
export function JobHero({ job, compact }: { job: Shown; compact?: boolean }) {
  const t = useT(words);
  const live = job.status === "running" || job.status === "queued";
  useSecondTick(live);
  if (!live) return null;
  const { step, index, total } = currentStep(job);
  const since = job.started_at ?? job.steps?.[0]?.started_at ?? null;
  const share = job.progress_percent ?? step?.progress_percent ?? null;
  const queued = job.status === "queued";
  return (
    <div className="job-hero" data-compact={compact || undefined} data-status={job.status}>
      <span className="job-hero-mark" aria-hidden="true">
        <span />
      </span>
      <div className="job-hero-text">
        <div className="job-hero-meta xsmall">
          {compact && <b>{jobTitle(job.action)}</b>}
          {!queued && step && <span>{total ? t.stepOf(index, total) : t.step(index)}</span>}
          {since && <span>{t.elapsed(elapsed(since))}</span>}
        </div>
        <div className="job-hero-title" aria-live="polite">
          {queued ? t.queued : step ? stepTitle(step.title) : t.starting}
        </div>
        {!queued && step?.progress && <div className="job-hero-progress small">{progressText(step.progress)}</div>}
      </div>
      {share != null && !queued && <span className="job-hero-share mono">{asPercent(share)}</span>}
      {compact && <ChevronRight className="flip-rtl job-hero-open" aria-hidden="true" />}
      <div className="job-hero-bar">
        <Progress value={queued ? null : share} status={job.status} />
      </div>
    </div>
  );
}

/** An instance's header while a job runs on it: the summary, as a button that opens the job's steps. */
export function JobBanner({ job, onOpen }: { job: CurrentJob; onOpen: () => void }) {
  const t = useT(words);
  return (
    <button
      type="button"
      className="card job-banner"
      onClick={onOpen}
      aria-label={`${jobTitle(job.action)}: ${t.open}`}
    >
      <JobHero job={job} compact />
    </button>
  );
}
