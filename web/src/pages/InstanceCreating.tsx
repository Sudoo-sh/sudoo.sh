// An instance while it is being created, and one whose creation failed. The API answers for a new instance from the
// moment its creation is accepted (status "creating", the job as current_job), so the page shows the creation as it
// happens instead of "does not exist", and turns into the instance's own page when the job succeeds.

import { useQueryClient } from "@tanstack/react-query";
import { Box, GitBranch, Plus } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router";
import { ApiError } from "@/api/client";
import { keys, useJob, useJobs } from "@/api/queries";
import type { Instance, Job } from "@/api/types";
import { Monogram } from "@/components/InstanceCard";
import { JobSteps } from "@/components/JobDrawer";
import { Page, useCan } from "@/components/Shell";
import { WhyItFailed } from "@/components/WhyItFailed";
import { Button, Callout, Progress, Skeleton, Status, Tag } from "@/components/app/basics";
import { useT } from "@/i18n";
import instanceWords from "@/i18n/instance";
import { jobTitle } from "@/i18n/jobs";
import { elapsed, percent as asPercent } from "@/lib/format";
import { NotFoundPage } from "./NotFound";

/** Re-render every `ms` while `on`, so a running time keeps counting between events. */
function useTick(ms: number, on: boolean) {
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!on) return;
    const timer = window.setInterval(() => setTick((n) => n + 1), ms);
    return () => window.clearInterval(timer);
  }, [ms, on]);
}

function Head({ name, status, instance }: { name: string; status: ReactNode; instance?: Instance }) {
  const plain = useT(instanceWords).head.plain;
  return (
    <div className="page-head">
      <div className="hero spacer">
        <Monogram name={name} large />
        <div className="titles" style={{ minWidth: 0 }}>
          <div className="row-wrap" style={{ gap: 12 }}>
            <h1 className="truncate">
              <bdi>{name}</bdi>
            </h1>
            {status}
          </div>
          {instance && (
            <div className="row-wrap small muted">
              <Tag mono>{instance.odoo.version}</Tag>
              <Tag>{instance.odoo.edition === "enterprise" ? "Enterprise" : "Community"}</Tag>
              {instance.source === "plain" ? (
                <span className="row xsmall" style={{ gap: 4 }}>
                  <Box size={14} strokeWidth={1.5} /> {plain}
                </span>
              ) : (
                <span className="row mono xsmall" style={{ gap: 4 }}>
                  <GitBranch size={14} strokeWidth={1.5} /> <bdi dir="ltr">{instance.git.branch}</bdi>
                </span>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/** The instance page while the instance is being created: its status, the job's steps and progress, live. */
export function CreatingInstance({ instance }: { instance: Instance }) {
  const reference = instance.current_job!;
  const { data: job } = useJob(reference.id);
  const status = job?.status ?? reference.status;
  const queued = status === "queued";
  useTick(1000, status === "queued" || status === "running");
  if (job?.status === "failed") return <CreationFailed name={instance.name} job={job} instance={instance} />;
  const percent = job?.progress_percent ?? reference.progress_percent;
  const steps = job?.steps ?? reference.steps;
  const t = useT(instanceWords).creating;
  return (
    <Page>
      <Head
        name={instance.name}
        instance={instance}
        status={queued ? <Status status="queued" label={t.waiting} /> : <Status status="creating" />}
      />
      <section className="card" aria-live="polite">
        <div className="card-head">
          <h2>{queued ? t.waiting : t.creating}</h2>
          <span className="spacer" />
          <span className="mono faint xsmall">
            {percent != null && !queued ? `${asPercent(percent)} · ` : ""}
            {elapsed(job?.started_at ?? instance.created_at)}
          </span>
        </div>
        <div className="card-body stack">
          <Progress value={queued ? null : percent} status={status} />
          <p className="small muted">{queued ? t.queuedText : t.runningText}</p>
          <JobSteps job={{ steps, expected_steps: job?.expected_steps ?? reference.expected_steps }} />
          {job?.notes.map((note, i) => (
            <Callout key={i} tone={note.level === "warning" ? "warning" : "info"}>
              {note.text}
            </Callout>
          ))}
        </div>
      </section>
    </Page>
  );
}

/** A creation that failed: why it failed (the step, the reason, the error details) and a way back. */
export function CreationFailed({ name, job, instance }: { name: string; job: Job; instance?: Instance }) {
  const can = useCan();
  const t = useT(instanceWords).creating;
  return (
    <Page>
      <Head name={name} instance={instance} status={<Status status="failed" label={t.notCreated} />} />
      <Callout tone="danger">
        <b>{t.failed}</b>
      </Callout>
      <WhyItFailed failure={job.failure} fallback={job.error ?? t.noReason} instance={null} />
      <section className="card">
        <div className="card-head">
          <h2>{jobTitle(job.action)}</h2>
          <span className="spacer" />
          <span className="mono faint xsmall">{elapsed(job.started_at, job.finished_at)}</span>
        </div>
        <div className="card-body stack">
          <JobSteps job={job} />
          <div className="row-wrap">
            <Button asChild>
              <Link to="/" viewTransition>
                {t.back}
              </Link>
            </Button>
            {can("instances.create") && (
              <Button asChild variant="primary">
                <Link to="/new" viewTransition>
                  <Plus /> {t.retry}
                </Link>
              </Button>
            )}
          </div>
        </div>
      </section>
    </Page>
  );
}

/** GET answered 404: a creation that failed and left nothing shows its job; anything else does not exist. */
export function MissingInstance({ name, error }: { name: string; error: unknown }) {
  const notFound = error instanceof ApiError && error.status === 404;
  const client = useQueryClient();
  const { data: jobs, isPending } = useJobs(name, notFound);
  const latest = jobs?.[0];
  const starting = latest?.kind === "create" && (latest.status === "queued" || latest.status === "running");
  useEffect(() => {
    // the creation was accepted just after the page asked: ask again, it answers with the instance being created
    if (starting) client.invalidateQueries({ queryKey: keys.instance(name) });
  }, [starting, client, name]);
  if (!notFound) return <NotFoundPage />;
  if (latest?.kind === "create" && latest.status === "failed") return <CreationFailed name={name} job={latest} />;
  if (isPending || starting)
    return (
      <Page>
        <Skeleton height={64} width="50%" />
        <Skeleton height={300} />
      </Page>
    );
  return <NotFoundPage />;
}
