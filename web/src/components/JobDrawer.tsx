import { Check, ScrollText, X } from "lucide-react";
import { useEffect, useState } from "react";
import { ApiError } from "@/api/client";
import { useJob } from "@/api/queries";
import type { Job, JobStep } from "@/api/types";
import { useT } from "@/i18n";
import instanceWords from "@/i18n/instance";
import { jobTitle, progressText, stepTitle } from "@/i18n/jobs";
import { ago, elapsed } from "@/lib/format";
import { Callout, Empty, Progress, Spinner, Status } from "./app/basics";
import { Drawer } from "./app/overlay";
import { JobHero } from "./JobProgress";
import { WhyItFailed } from "./WhyItFailed";
import progressWords from "@/i18n/job-progress";

/** Re-render every second while `on`, so a running step's time keeps counting between events. */
function useSecondTick(on: boolean) {
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!on) return;
    const timer = window.setInterval(() => setTick((n) => n + 1), 1000);
    return () => window.clearInterval(timer);
  }, [on]);
}

/** The running step: how far it is in plain words (a download's parts, the module Odoo works on), its percentage
 *  when known, and how long it has run, so a long step never looks frozen. */
function StepProgress({ step }: { step: JobStep }) {
  useSecondTick(step.status === "running");
  if (step.status !== "running") return null;
  const parts = [
    step.progress ? progressText(step.progress) : null,
    step.progress_percent != null ? `${step.progress_percent}%` : null,
    step.started_at ? elapsed(step.started_at) : null,
  ].filter(Boolean);
  if (!parts.length) return null;
  return <span className="step-progress" aria-live="polite">{parts.join(" · ")}</span>;
}

/** A job's steps as a stepper: done, failed, or the one running now (highlighted) with its progress; when the job
 *  announced how many steps it takes, how many are left. */
export function JobSteps({ job }: { job: Pick<Job, "steps"> & { expected_steps?: number | null } }) {
  const t = useT(instanceWords).job;
  const p = useT(progressWords);
  const left = job.expected_steps ? Math.max(0, job.expected_steps - job.steps.length) : 0;
  const running = job.steps.some((step) => step.status === "running");
  return (
    <ol className="timeline job-steps">
      {job.steps.map((step, i) => (
        <li key={i} data-current={step.status === "running" || undefined} aria-current={step.status === "running" ? "step" : undefined}>
          <span className={`node ${step.status}`}>
            {step.status === "done" ? <Check /> : step.status === "failed" ? <X /> : <span className="spinner" />}
          </span>
          <span className={step.status === "running" ? "" : "muted"}>
            {stepTitle(step.title)}
            <StepProgress step={step} />
          </span>
        </li>
      ))}
      {!job.steps.length && <li className="faint">{t.waiting}</li>}
      {running && left > 0 && (
        <li className="upcoming"><span className="node" /><span className="faint small">{p.stepsLeft(left)}</span></li>
      )}
    </ol>
  );
}

/** A job: its steps as a timeline with the running step's progress, notes, and why it failed, all live. Its technical
 *  log is never shown: it stays in the server's log. */
export function JobDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  const { data: job, error } = useJob(id);
  // a job is kept for a while (the last 300): an older one, linked from a build's history, is gone
  const gone = error instanceof ApiError && error.status === 404;
  const t = useT(instanceWords).job;
  return (
    <Drawer
      onClose={onClose}
      title={
        <div className="stack-sm" style={{ gap: 4 }}>
          <div className="row">
            <h2>{job ? jobTitle(job.action) : t.activity}</h2>
            {job && <Status status={job.status} />}
          </div>
          {job && (
            <span className="small muted">
              {t.meta(job.instance, job.requested_by, ago(job.started_at))}
              {job.started_at && ` · ${elapsed(job.started_at, job.finished_at)}`}
            </span>
          )}
        </div>
      }
    >
      {gone ? (
        <Empty icon={<ScrollText />} title={t.gone}>{t.goneText}</Empty>
      ) : !job ? (
        <Spinner />
      ) : (
        <div className="stack">
          {job.status === "running" || job.status === "queued" ? (
            <JobHero job={job} />
          ) : (
            <Progress value={job.progress_percent} status={job.status} />
          )}
          {job.status === "failed" && (
            <WhyItFailed failure={job.failure} fallback={job.error} instance={job.instance.includes(":") ? null : job.instance} />
          )}
          {job.notes.map((note, i) => (
            <Callout key={i} tone={note.level === "warning" ? "warning" : "info"}>
              {note.text}
            </Callout>
          ))}
          <JobSteps job={job} />
        </div>
      )}
    </Drawer>
  );
}
