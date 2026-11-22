// The demo's promotion tools (as src/sudoo/web/v1/promotion.py): the pre-promotion checklist of a branch, comparing two
// branches, the scheduled refresh of staging and pull-request previews, made from the demo's repositories.

import type { BranchCompare, CheckItem, ModuleChanges, Preview, PromotionCheck, StagingRefresh } from "@/api/types";
import { branchList, membersOf, productionInstance, type DemoProject } from "./projects";
import { backups, commitsOf, setting } from "./state";
import { DAY, HOUR, iso, MINUTE, OPENED } from "./util";

const series = (version: string) => /^(\d+\.\d+)/.exec(version)?.[1] ?? version;

/** The modules changed between production and a branch, as the manifests of the demo's repository say. */
function moduleChanges(project: DemoProject, head: string): { modules: ModuleChanges; migrations: BranchCompare["migrations"] } {
  const odoo = series(project.version);
  const names = project.repo?.modules ?? [];
  if (!names.length || head === project.repo?.defaultBranch) return { modules: { added: [], removed: [], changed: [], known: true }, migrations: [] };
  const [first, second, third] = names;
  const changed: ModuleChanges["changed"] = [];
  if (first) changed.push({ name: first, from: `${odoo}.1.4.0`, to: `${odoo}.1.5.0`, bump: "up" });
  if (third) changed.push({ name: third, from: `${odoo}.1.0.2`, to: `${odoo}.1.0.2`, bump: "same" });
  const added = second ? [{ name: `${second}_extra`, version: `${odoo}.1.0.0` }] : [];
  const migrations = first ? [{ module: first, version: `${odoo}.1.5.0`, scripts: [`${first}/migrations/${odoo}.1.5.0/pre-migrate.py`] }] : [];
  return { modules: { added, removed: [], changed, known: true }, migrations };
}

function productionBranch(project: DemoProject, branch: string): string | null {
  return branchList(project, true).find((b) => b.stage === "production" && b.name !== branch)?.name ?? null;
}

export function promotionCheck(project: DemoProject, branch: string, merge = false): PromotionCheck {
  const production = productionInstance(project);
  const base = productionBranch(project, branch);
  const hours = Number(setting("project_promotion_backup_hours") ?? 24);
  const newest = production ? backups.filter((b) => b.instance === production.name).sort((a, b) => b.created - a.created)[0] : undefined;
  const age = newest ? (Date.now() - newest.created * 1000) / HOUR : null;
  const { modules, migrations } = moduleChanges(project, branch);
  const commit = membersOf(project.id).find((i) => i.git.branch === branch)?.recent_commits?.[0] ?? (project.repo ? commitsOf(project.repo, branch)[0] : undefined);
  const items: CheckItem[] = [
    {
      key: "build",
      status: "ok",
      text: `The latest build of ${branch} succeeded and its tests passed.`,
      build: { id: "demo-build", status: "success", at: iso(OPENED - 2 * HOUR), commit: commit ? { sha: commit.sha.slice(0, 7), subject: commit.subject } : null, tests: { status: "success", tests: 148, failed: 0 } },
    },
    production
      ? age === null || (hours && age > hours)
        ? { key: "backup", status: "warning", instance: production.name, max_hours: hours, backup: newest ? { file: newest.file, created: newest.created } : null,
            text: newest ? `The newest backup of ${production.name} is ${Math.round(age!)} hours old (more than ${hours}). Back it up now.` : `${production.name} (production) has no backup. Back it up before the change.` }
        : { key: "backup", status: "ok", instance: production.name, max_hours: hours, backup: { file: newest!.file, created: newest!.created }, text: `${production.name} (production) was backed up ${Math.round(age!)} hours ago.` }
      : { key: "backup", status: "ok", text: "There is no production instance yet, so there is no production data to protect." },
    {
      key: "modules",
      status: "ok",
      modules,
      ahead: 3,
      behind: 0,
      text: modules.changed.length || modules.added.length
        ? `Modules: ${[modules.added.length && `${modules.added.length} added`, modules.changed.filter((c) => c.bump === "up").length && `${modules.changed.filter((c) => c.bump === "up").length} with a new version`, modules.changed.filter((c) => c.bump === "same").length && `${modules.changed.filter((c) => c.bump === "same").length} changed without a new version`].filter(Boolean).join(", ")}.`
        : "No module changes between production and this branch.",
    },
    migrations.length
      ? { key: "migrations", status: "warning", migrations, text: `1 migration script runs on the next upgrade (${migrations[0]!.module} ${migrations[0]!.version}). Run them on staging first.` }
      : { key: "migrations", status: "ok", migrations: [], text: "No pending migrations." },
    { key: "odoo", status: "ok", branch_series: [series(project.version)], production_series: [series(project.version)], text: `${branch} and production both run Odoo ${series(project.version)}.` },
    project.access.locks[branch]
      ? { key: "lock", status: "warning", branches: [branch], text: `Locked (${branch}); the lock lets you through. Tell its other users.` }
      : { key: "lock", status: "ok", text: "Neither this branch nor production's is locked." },
    ...(merge ? [] : [{ key: "capacity", status: "ok", text: "The plan, the project and the server have room for this production branch." } as CheckItem]),
  ];
  const blocked = items.filter((i) => i.status === "blocked").map((i) => i.key);
  return {
    branch,
    mode: merge ? "merge" : "move",
    production: { branch: base, instance: production?.name ?? null },
    items,
    blocked,
    blocking: blocked.length > 0 && setting("project_promotion_blocks") !== false,
    compare: base ? { ahead: 3, behind: 0, files_changed: 14, github_url: `https://github.com/${project.repository}/compare/${base}...${branch}`, source: "git" } : null,
    checked_at: iso(Date.now()),
  };
}

export function compare(project: DemoProject, base: string, head: string): BranchCompare {
  const commits = project.repo ? commitsOf(project.repo, head).slice(0, 3) : [];
  const { modules, migrations } = moduleChanges(project, head === project.repo?.defaultBranch ? base : head);
  return {
    ahead: commits.length,
    behind: head === project.repo?.defaultBranch ? 0 : 1,
    commits: commits.map((c) => ({ sha: c.sha.slice(0, 7), full_sha: c.sha, subject: c.subject, author: c.author, when: c.when })),
    files_changed: 6 + commits.length * 3,
    modules,
    migrations,
    source: "git",
    base: { branch: base, sha: (project.repo ? commitsOf(project.repo, base)[0]?.sha : "") ?? "" },
    head: { branch: head, sha: commits[0]?.sha ?? "" },
    github_url: `https://github.com/${project.repository}/compare/${encodeURIComponent(base)}...${encodeURIComponent(head)}`,
    odoo_series: series(project.version),
  };
}

// ---------------------------------------------------------------- the scheduled refresh

const schedules = new Map<string, StagingRefresh>();

function nextRun(found: StagingRefresh, now = Date.now()): string | null {
  if (found.frequency === "off") return null;
  const [h, m] = found.time.split(":").map(Number);
  const next = new Date(now);
  next.setUTCHours(h!, m!, 0, 0);
  if (next.getTime() <= now) next.setUTCDate(next.getUTCDate() + 1);
  if (found.frequency === "weekly") while ((next.getUTCDay() + 6) % 7 !== found.weekday) next.setUTCDate(next.getUTCDate() + 1);
  return next.toISOString();
}

export function schedule(project: DemoProject): StagingRefresh {
  const found = schedules.get(project.id) ?? (project.id === "acme-retail"
    ? { frequency: "weekly", time: "03:00", weekday: 6, branches: [], last_run: iso(OPENED - 5 * DAY), next_run: null, changed_by: "omar" }
    : { frequency: "off", time: "03:00", weekday: 6, branches: [], last_run: null, next_run: null, changed_by: null });
  return { ...found, next_run: nextRun(found) };
}

export function saveSchedule(project: DemoProject, values: Pick<StagingRefresh, "frequency" | "time" | "weekday" | "branches">, user: string): StagingRefresh {
  // saving refreshes nothing: the last refresh stays what it was
  schedules.set(project.id, { ...values, last_run: schedule(project).last_run, next_run: null, changed_by: user });
  return schedule(project);
}

export function stagingBranches(project: DemoProject) {
  return branchList(project, true).filter((b) => b.stage === "staging").map((b) => ({ name: b.name, instances: b.instances }));
}

// ---------------------------------------------------------------- pull-request previews

export function previews(project: DemoProject): Preview[] {
  if (project.id !== "acme-retail") return [];
  return [
    {
      number: 42, title: "Refund to the original payment method from the POS", url: `https://github.com/${project.repository}/pull/42`,
      branch: "pos-refunds", repository: project.repository, fork: false, head_sha: "9f1c2ab7d4e5", instance: "acme-retail-pos-refunds",
      instance_exists: true, existing: true, status: "ready", reason: null, commented: true, opened_at: iso(OPENED - 2 * DAY - 3 * HOUR),
    },
    {
      number: 45, title: "Bundle prices on the shop page", url: `https://github.com/${project.repository}/pull/45`,
      branch: "ecommerce-bundles", repository: project.repository, fork: false, head_sha: "c41d7e0b98a2", instance: "acme-retail-ecommerce-bundles",
      instance_exists: false, existing: false, status: "building", reason: null, commented: true, opened_at: iso(OPENED - 40 * MINUTE),
    },
  ];
}
