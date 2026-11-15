// An instance while it is being created, and one whose creation failed. The API answers for a new instance from the
// moment its creation is accepted (status "creating", the job as current_job), so the page shows the creation as it
// happens instead of "does not exist", and turns into the instance's own page when the job succeeds.

import { useQueryClient } from "@tanstack/react-query";
import { Box, GitBranch, Plus } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router";
import { ApiError } from "@/api/client";
import { keys, useJob, useJobLog, useJobs } from "@/api/queries";
import type { Instance, Job } from "@/api/types";
import { Monogram } from "@/components/InstanceCard";
import { JobSteps, Terminal } from "@/components/JobDrawer";
import { Page, useCan } from "@/components/Shell";
import { Callout, Progress, Skeleton, Status, Tag } from "@/components/ui/basics";
import { elapsed } from "@/lib/format";
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
  return (
    <div className="page-head">
      <div className="hero spacer">
        <Monogram name={name} large />
        <div className="titles" style={{ minWidth: 0 }}>
          <div className="row-wrap" style={{ gap: 12 }}>
            <h1 className="truncate">{name}</h1>
            {status}
          </div>
          {instance && (
            <div className="row-wrap small muted">
              <Tag mono>{instance.odoo.version}</Tag>
              <Tag>{instance.odoo.edition === "enterprise" ? "Enterprise" : "Community"}</Tag>
              {instance.source === "plain" ? (
                <span className="row xsmall" style={{ gap: 4 }}>
                  <Box size={14} strokeWidth={1.5} /> Plain Odoo, no repository
                </span>
              ) : (
                <span className="row mono xsmall" style={{ gap: 4 }}>
                  <GitBranch size={14} strokeWidth={1.5} /> {instance.git.branch}
                </span>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function TechnicalLog({ id, live }: { id: string; live: boolean }) {
  const can = useCan();
  const { data: log } = useJobLog(can("instances.logs") ? id : null);
  if (!can("instances.logs")) return null;
  return (
    <section className="card">
      <div className="card-head">
        <h2>Technical log</h2>
      </div>
      <div className="card-body">
        <Terminal text={log?.text ?? ""} live={live} />
      </div>
    </section>
  );
}

/** The instance page while the instance is being created: its status, the job's steps, progress and log, live. */
export function CreatingInstance({ instance }: { instance: Instance }) {
  const reference = instance.current_job!;
  const { data: job } = useJob(reference.id);
  const status = job?.status ?? reference.status;
  const queued = status === "queued";
  useTick(1000, status === "queued" || status === "running");
  if (job?.status === "failed") return <CreationFailed name={instance.name} job={job} instance={instance} />;
  const percent = job?.progress_percent ?? reference.progress_percent;
  const steps = job?.steps ?? reference.steps;
  return (
    <Page>
      <Head
        name={instance.name}
        instance={instance}
        status={queued ? <Status status="queued" label="Waiting to start" /> : <Status status="creating" />}
      />
      <section className="card" aria-live="polite">
        <div className="card-head">
          <h2>{queued ? "Waiting to start" : "Creating the instance"}</h2>
          <span className="spacer" />
          <span className="mono faint xsmall">
            {percent != null && !queued ? `${percent}% · ` : ""}
            {elapsed(job?.started_at ?? instance.created_at)}
          </span>
        </div>
        <div className="card-body stack">
          <Progress value={queued ? null : percent} status={status} />
          <p className="small muted">
            {queued
              ? "Another job on this name has to finish first; the creation starts right after it."
              : "This page follows the creation and opens the instance when it is ready. You can leave it: the creation goes on."}
          </p>
          <JobSteps job={{ steps }} />
          {job?.notes.map((note, i) => (
            <Callout key={i} tone={note.level === "warning" ? "warning" : "info"}>
              {note.text}
            </Callout>
          ))}
        </div>
      </section>
      <TechnicalLog id={reference.id} live />
    </Page>
  );
}

/** A creation that failed: why (the job's hint), the step where it stopped, its log, and a way back. */
export function CreationFailed({ name, job, instance }: { name: string; job: Job; instance?: Instance }) {
  const can = useCan();
  return (
    <Page>
      <Head name={name} instance={instance} status={<Status status="failed" label="Not created" />} />
      <Callout tone="danger">
        <b>The instance could not be created.</b> {job.error ?? "The technical log says why."}
      </Callout>
      <section className="card">
        <div className="card-head">
          <h2>{job.action}</h2>
          <span className="spacer" />
          <span className="mono faint xsmall">{elapsed(job.started_at, job.finished_at)}</span>
        </div>
        <div className="card-body stack">
          <JobSteps job={job} />
          <div className="row-wrap">
            <Link className="btn" to="/" viewTransition>
              Back to instances
            </Link>
            {can("instances.create") && (
              <Link className="btn primary" to="/new" viewTransition>
                <Plus /> Try again
              </Link>
            )}
          </div>
        </div>
      </section>
      <TechnicalLog id={job.id} live={false} />
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
