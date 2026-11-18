// Jobs as the panel runs them: queued, then one step after another with their log lines streaming, then done. Each
// change goes out as the live events the real server sends ("job", "job.output", "instance"), and the instance
// changes when the job ends (a new commit, paused, restored...).

import type { Instance, InstanceBackup, Job, JobRef } from "@/api/types";
import { publish } from "./bus";
import { stepLines, type LogContext } from "./logs";
import { listed } from "./metrics";
import { addJob, backups, contextOf, instances, jobs, logs, profiles, record, session } from "./state";
import { hex, iso, pause, SECOND } from "./util";

export const reference = (job: Job): JobRef & { links: { self: string } } => ({
  id: job.id,
  action: job.action,
  status: job.status,
  progress_percent: job.progress_percent,
  links: { self: `/api/v1/jobs/${job.id}` },
});

/** An instance as GET /instances/<name> answers: with its backups, recent commits and jobs. */
export function full(instance: Instance): Instance {
  const own: InstanceBackup[] = backups
    .filter((b) => b.instance === instance.name)
    .map((b) => ({ file: b.file, kind: b.kind, database: b.db, size_bytes: b.size, created_at: iso(b.created * 1000)! }));
  const restorable = own.find((b) => ["manual", "daily", "deletion"].includes(b.kind));
  return {
    ...instance,
    backups: own,
    restorable_backup: restorable?.file ?? null,
    recent_jobs: jobs.filter((j) => j.instance === instance.name).slice(0, 15).map(reference),
  };
}

export const announce = (instance: Instance) => publish("instance", instance.name, listed(instance));

export interface Plan {
  instance: string;
  action: string;
  kind: string;
  steps: string[];
  /** how long each step takes, in seconds */
  seconds?: number | number[];
  context?: LogContext;
  /** while it runs */
  status?: Instance["status"];
  notes?: Job["notes"];
  onStart?: () => void;
  onDone?: (job: Job) => void;
  /** the plugin jobs have no instance */
  panel?: boolean;
}

const progress = (job: Job, expected: number) => {
  const done = job.steps.filter((s) => s.status === "done").length;
  return Math.min(99, Math.round((100 * done) / Math.max(expected, job.steps.length)));
};

export function startJob(plan: Plan): Job {
  const job: Job = {
    id: hex(12),
    instance: plan.instance,
    action: plan.action,
    kind: plan.kind,
    status: "queued",
    progress_percent: 0,
    requested_by: session.user,
    started_at: iso(Date.now()),
    finished_at: null,
    steps: [],
    notes: [],
    error: null,
  };
  addJob(job, "");
  const instance = plan.panel ? null : instances.get(plan.instance);
  const context = plan.context ?? (instance ? contextOf(instance) : { name: plan.instance, database: "", version: "", edition: "community" as const });
  const lines = { count: 0 };

  const sync = () => {
    publish("job", job.id, job);
    if (instance && instances.has(instance.name)) {
      instance.current_job = job.status === "success" || job.status === "failed" ? null : { ...reference(job), steps: job.steps.map((s) => ({ ...s })) };
      announce(instance);
    }
  };
  const write = (text: string) => {
    const pieces = text.split("\n").length - 1;
    logs.set(job.id, (logs.get(job.id) ?? "") + text);
    publish("job.output", job.id, { job: job.id, offset: lines.count, next_offset: lines.count + pieces, text });
    lines.count += pieces;
  };

  if (instance) {
    record(session.user, `instance action: ${plan.action}`, { instance: instance.name });
    const profile = profiles.get(instance.name);
    if (profile) {
      profile.boostFrom = Date.now();
      profile.boostUntil = Date.now() + 40 * SECOND;
    }
  }
  sync();

  void (async () => {
    await pause(500);
    job.status = "running";
    if (instance && plan.status) instance.status = plan.status;
    plan.onStart?.();
    sync();
    for (const [i, title] of plan.steps.entries()) {
      for (const step of job.steps) if (step.status === "running") step.status = "done";
      job.steps.push({ title, status: "running" });
      job.progress_percent = progress(job, plan.steps.length);
      write(`\n== ${title}\n`);
      sync();
      const output = stepLines(title, context);
      const seconds = Array.isArray(plan.seconds) ? (plan.seconds[i] ?? 2) : (plan.seconds ?? 2.2);
      const each = (seconds * SECOND) / (output.length + 1);
      for (const line of output) {
        await pause(each * (0.6 + Math.random() * 0.8));
        write(`${line}\n`);
      }
      await pause(each);
    }
    for (const step of job.steps) step.status = "done";
    for (const note of plan.notes ?? []) {
      job.notes.push(note);
      write(`[${note.level}] ${note.text}\n`);
    }
    job.status = "success";
    job.progress_percent = 100;
    job.finished_at = iso(Date.now());
    if (!instance || instances.has(instance.name)) plan.onDone?.(job);
    sync();
  })();
  return job;
}
