// The demo's build history (src/sudoo/projects/builds.py): every job that builds an instance of a project's branch
// (its creation, Get latest code, a rebuild from production) is a build; restores and stage changes join the
// branch's timeline. Builds are read from the demo's jobs, so a job started in the demo shows up as a build at once
// (building, then its outcome), with the commit it brought. As on a real panel, an instance has one live build:
// a newer success drops the older ones. Builds of a branch whose test suite is on say how many tests passed (one of
// them failed a test); a branch that does nothing on new commits lists the commits it did not build.

import type {
  Build,
  BuildTests,
  BuildTrigger,
  CommitEntry,
  HistoryEntry,
  Job,
  RestoreEntry,
  Stage,
  StageChange,
} from "@/api/types";
import { effective, projectById } from "./build-options";
import { publish, subscribe } from "./bus";
import { instances, jobs } from "./state";
import { hash, iso, DAY, HOUR, MINUTE } from "./util";

const STAGE_MEANING: Record<Stage, string> = {
  development:
    "Development: for trying changes. New instances get demo data (when the project says so), catch every e-mail and are deleted when their lifetime ends.",
  staging:
    "Staging: for testing with real data. The instance runs on a neutralised copy of production's data (rebuilt on push when the project says so) and catches every e-mail.",
  production:
    "Production: the live database. The instance is kept, never paused when idle, backed up every day, and sends real e-mail.",
};
const DONE: Record<BuildTrigger, string> = {
  create: "Instance created",
  push: "Built from the pushed commit",
  manual: "Built from the latest commit",
  rebuild: "Rebuilt from a new copy of production",
};
const BUILDING_ACTIONS: Record<string, BuildTrigger | undefined> = {
  "Create instance": "create",
  "Get latest code": "manual",
  "Rebuild from production": "rebuild",
};
/** Jobs older than this have left the panel's memory (it keeps the last 300): their log is gone. */
const LOG_KEPT = 20 * DAY;

/** Stage changes recorded in the demo (seeded, then made with the stage dialog), per project. */
const stageChanges = new Map<string, StageChange[]>();
/** The commit each finished build brought, fixed once seen (newer commits must not move it). */
const commitOf = new Map<string, Build["commit"]>();

export function resetBuilds() {
  stageChanges.clear();
  commitOf.clear();
  for (const instance of instances.values()) {
    if (!instance.project || instance.stage !== "staging" || !instance.git.branch) continue;
    // the branch was pushed as development, then made staging before its instance was created
    const at = Date.parse(instance.created_at!) - 25 * MINUTE;
    stageChanges.set(instance.project.id, [
      ...(stageChanges.get(instance.project.id) ?? []),
      stageEntry(instance.project.id, instance.git.branch, "development", "staging", "omar", at),
    ]);
  }
}

function stageEntry(project: string, branch: string, from: Stage, to: Stage, by: string, at: number): StageChange {
  const id = `s${hash(`${project}${branch}${at}`).toString(16)}`;
  return {
    id,
    type: "stage",
    project,
    branch,
    at: iso(at),
    by,
    from,
    to,
    explanation: STAGE_MEANING[to],
    reason: null,
    links: { self: `/api/v1/projects/${project}/builds/${id}`, job: null },
  };
}

/** A branch's stage changed (PUT .../stage): it joins the timeline, live. */
export function recordStageChange(project: string, branch: string, from: Stage, to: Stage, by: string) {
  const entry = stageEntry(project, branch, from, to, by, Date.now());
  stageChanges.set(project, [...(stageChanges.get(project) ?? []), entry]);
  publish("build", entry.id, entry);
}

function trigger(job: Job): BuildTrigger | undefined {
  const found = BUILDING_ACTIONS[job.action];
  if (found === "manual" && job.requested_by === "GitHub push") return "push";
  return found;
}

const jobLink = (job: Job) => {
  const kept = Date.now() - Date.parse(job.started_at!) < LOG_KEPT;
  // an old job's id still names it, but the panel no longer has it
  const id = kept ? job.id : `${job.id}-gone`;
  return { id, link: `/api/v1/jobs/${id}` };
};

/** Odoo's log lines for a build that loaded with warnings. */
function warningLines(database: string, at: number): string[] {
  const stamp = new Date(at).toISOString().replace("T", " ").replace("Z", "").replace(".", ",");
  return [
    `${stamp} 1 WARNING ${database} odoo.modules.loading: The models ['acme.loyalty.rule'] have no access rules in module acme_loyalty, consider adding some.`,
    `${stamp} 1 WARNING ${database} odoo.fields: Field acme.loyalty.rule.points_expire: unknown parameter 'tracking', if this is an actual parameter you may want to override the method _valid_field_parameter.`,
  ];
}

/** The builds and restores of one instance, newest first. */
function instanceEntries(projectId: string, name: string): HistoryEntry[] {
  const instance = instances.get(name);
  if (!instance?.git.branch) return [];
  const branch = instance.git.branch;
  const own = jobs
    .filter((j) => j.instance === name)
    .sort((a, b) => Date.parse(b.started_at!) - Date.parse(a.started_at!));
  const commits = instance.recent_commits ?? [];
  const entries: HistoryEntry[] = [];
  const project = projectById(projectId);
  const testing =
    project && instance.stage !== "production" ? effective(project, branch).values.test_suite !== "off" : false;
  let builds = 0;
  let live = false;
  for (const job of own) {
    const started = Date.parse(job.started_at!);
    const finished = job.finished_at ? Date.parse(job.finished_at) : null;
    const { id: jobId, link } = jobLink(job);
    if (job.kind === "restore" && job.action !== "Rebuild from production") {
      if (job.status !== "success" && job.status !== "failed") continue;
      const entry: RestoreEntry = {
        id: `r${job.id}`,
        type: "restore",
        project: projectId,
        branch,
        instance: name,
        at: job.finished_at,
        by: job.requested_by,
        status: job.status,
        action: job.action,
        result: job.status === "failed" ? (job.error ?? "The restore failed; the job's log says why.") : job.action,
        job: jobId,
        links: {
          self: `/api/v1/projects/${projectId}/builds/r${job.id}`,
          job: link,
          instance: `/api/v1/instances/${name}`,
        },
      };
      entries.push(entry);
      continue;
    }
    const why = trigger(job);
    if (!why) continue;
    const running = job.status === "queued" || job.status === "running";
    let commit = commitOf.get(job.id) ?? null;
    if (!commit && !running) {
      // the newest build has the instance's commit; older ones the branch's earlier commits, in an order of its own
      const found =
        builds === 0 || commits.length < 2
          ? commits[0]
          : commits[1 + ((builds - 1 + hash(name)) % (commits.length - 1))];
      commit = found
        ? {
            sha: found.sha.slice(0, 7),
            full_sha: found.full_sha ?? found.sha,
            subject: found.subject,
            author: found.author,
            when: found.when,
          }
        : null;
      if (commit) commitOf.set(job.id, commit);
    }
    builds += 1;
    // one warning now and then, on a build that is not the instance's first
    const warned = job.status === "success" && why !== "create" && (hash(name) + builds) % 4 === 1;
    let status: Build["status"] = running
      ? "building"
      : job.status === "failed"
        ? "failed"
        : warned
          ? "warning"
          : "success";
    let droppedAt: string | null = null;
    if (status === "success" || status === "warning") {
      if (live) {
        status = "dropped";
        droppedAt = iso(started + 60 * HOUR);
      }
      live = true;
    }
    let messages = warned ? warningLines(instance.odoo.database, started + 40_000) : [];
    // the tests of the build: one build of a tested branch failed a test, the others passed
    let tests: BuildTests | null = null;
    let testResult = "";
    if (testing && !running && job.status === "success") {
      const total = 40 + (hash(`${name}${builds}`) % 120);
      if (builds === 2) {
        const module = `${instance.git.branch?.replace(/-/g, "_") ?? "custom"}`.slice(0, 30);
        tests = { status: "failed", tests: total, failed: 1, errors: 0, warnings: 0, modules: [`acme_${module}`] };
        status = "failed";
        droppedAt = null;
        testResult = `At least one test failed when loading the modules (acme_${module}). 1 failed, 0 with errors, of ${total} tests.`;
        messages = [
          `ERROR ${instance.odoo.database} odoo.addons.acme_${module}.tests.test_flow: FAIL: TestFlow.test_totals | AssertionError: 118.0 != 120.0`,
        ];
      } else {
        tests = {
          status: warned ? "warning" : "success",
          tests: total,
          failed: 0,
          errors: 0,
          warnings: warned ? messages.length : 0,
          modules: [],
        };
        testResult = `${total} tests passed.`;
      }
    }
    entries.push({
      id: `b${job.id}`,
      type: "build",
      project: projectId,
      branch,
      instance: name,
      trigger: why,
      status,
      commit,
      by: job.requested_by === "GitHub push" ? "system" : job.requested_by,
      at: job.started_at,
      action: job.action,
      result: running
        ? ""
        : job.status === "failed"
          ? (job.error ?? "The build failed; the job's log says why.")
          : testResult && status === "failed"
            ? testResult
            : warned
              ? `${messages.length} warnings in Odoo's log while loading the modules`
              : testResult
                ? `${DONE[why]}. ${testResult}`
                : DONE[why],
      tests,
      messages,
      job: jobId,
      queued_at: job.started_at,
      started_at: job.started_at,
      finished_at: job.finished_at,
      duration_seconds: finished ? Math.round((finished - started) / 1000) : null,
      dropped_at: droppedAt,
      links: {
        self: `/api/v1/projects/${projectId}/builds/b${job.id}`,
        job: link,
        instance: `/api/v1/instances/${name}`,
      },
    });
  }
  return entries;
}

const NOT_BUILT = "New commit; this branch builds nothing on new commits (its Behaviour upon new commits).";
const UNBUILT = [
  { subject: "Points expire after a year unless the customer buys again", author: "Ines Haddad", hours: 3 },
  { subject: "Show the points balance on the receipt", author: "Ines Haddad", hours: 26 },
];

/** The commits a branch that does nothing on new commits did not build (they wait for Get latest code). */
function commitEntries(projectId: string, branch: string, members: string[]): CommitEntry[] {
  const project = projectById(projectId);
  if (!project) return [];
  const found = effective(project, branch);
  if (found.stage === "production" || found.values.on_new_commit !== "nothing") return [];
  return UNBUILT.map((c, i) => {
    const full = hash(`${projectId}${branch}${i}`).toString(16).padStart(8, "0").repeat(5).slice(0, 40);
    const at = Date.now() - c.hours * HOUR;
    const id = `c${full.slice(0, 12)}`;
    return {
      id,
      type: "commit",
      project: projectId,
      branch,
      at: iso(at),
      by: "system",
      instance: members[0] ?? null,
      status: "skipped",
      commit: { sha: full.slice(0, 7), full_sha: full, subject: c.subject, author: c.author, when: iso(at)! },
      result: NOT_BUILT,
      links: { self: `/api/v1/projects/${projectId}/builds/${id}`, job: null },
    };
  });
}

/** A branch's timeline, newest first. */
export function branchHistory(projectId: string, branch: string, members: string[]): HistoryEntry[] {
  const entries: HistoryEntry[] = [
    ...members.flatMap((name) => instanceEntries(projectId, name)),
    ...commitEntries(projectId, branch, members),
  ];
  for (const change of stageChanges.get(projectId) ?? []) if (change.branch === branch) entries.push(change);
  return entries.sort((a, b) => Date.parse(b.at ?? "") - Date.parse(a.at ?? ""));
}

export const branchBuilds = (projectId: string, branch: string, members: string[]): Build[] =>
  branchHistory(projectId, branch, members).filter((e): e is Build => e.type === "build");

/** One entry, wherever it is in the project. */
export function findEntry(
  projectId: string,
  id: string,
  branches: { name: string; instances: string[] }[],
): HistoryEntry | null {
  for (const branch of branches) {
    const found = branchHistory(projectId, branch.name, branch.instances).find((e) => e.id === id);
    if (found) return found;
  }
  return null;
}

// a job that builds or restores an instance of a project changes that branch's history: tell the web app, as the
// real panel's "build" event does
subscribe((topic, key, data) => {
  if (topic !== "job") return;
  const job = data as Job;
  const instance = instances.get(job.instance);
  if (!instance?.project || !instance.git.branch) return;
  if (!trigger(job) && job.kind !== "restore") return;
  const entry = instanceEntries(instance.project.id, instance.name).find(
    (e) => e.id === `b${key}` || e.id === `r${key}`,
  );
  if (entry) publish("build", entry.id, entry);
});
