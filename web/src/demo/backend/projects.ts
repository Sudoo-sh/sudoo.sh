// The demo's projects (src/sudoo/projects): one per client repository, made from the seeded instances as the real
// panel adopts existing ones, plus one linked repository without instances. Branches come from the demo's
// repositories; their stages from what was recorded, else from their instances.

import type { Instance, Project, ProjectBranch, ProjectOptions, Stage } from "@/api/types";
import { described, levelIn, permissionsIn, PROJECT_PERMISSIONS, seededAccess, setLocks, type DemoAccess } from "./access";
import { commitsOf, instances, OWNER, PROJECT_NAMES, REPOS, setting, type DemoRepo } from "./state";
import { resetBuilds } from "./builds";
import { iso, OPENED, DAY } from "./util";

export interface DemoProject {
  id: string;
  name: string;
  repository: string; // "owner/name"
  repo: DemoRepo | null; // null: a repository made from the panel (empty until a branch is pushed)
  version: string;
  edition: "community" | "enterprise";
  origin: Project["origin"];
  created_at: number;
  created_by: string;
  own: Partial<ProjectOptions>;
  stages: Record<string, Stage>;
  autoCreated: Record<string, string>;
  access: DemoAccess;
}

export const projects = new Map<string, DemoProject>();
const RANK: Record<Stage, number> = { development: 0, staging: 1, production: 2 };

export function seedProjects() {
  projects.clear();
  for (const id of Object.keys(PROJECT_NAMES)) {
    const repo = REPOS.find((r) => r.name === id)!;
    const members = [...instances.values()].filter((i) => i.project?.id === id);
    const lead = [...members].sort((a, b) => RANK[b.stage] - RANK[a.stage])[0];
    const stages: Record<string, Stage> = {};
    for (const instance of members) {
      const branch = instance.git.branch ?? "";
      if (!stages[branch] || RANK[instance.stage] > RANK[stages[branch]!]) stages[branch] = instance.stage;
    }
    projects.set(id, {
      id,
      name: PROJECT_NAMES[id]!,
      repository: `${OWNER}/${id}`,
      repo,
      version: lead?.odoo.version ?? "19.0",
      edition: lead?.odoo.edition ?? "community",
      origin: members.length ? "adopted" : "linked",
      created_at: OPENED - (members.length ? 74 : 4) * DAY,
      created_by: members.length ? "system" : "omar",
      own: id === "nile-logistics" ? { max_auto_development: 5, branch_deleted: "keep" } : {},
      stages,
      autoCreated: id === "acme-retail" ? { "pos-refunds": "acme-retail-pos-refunds" } : {},
      access: seededAccess(id),
    });
    setLocks(id, projects.get(id)!.access, () => undefined);
  }
  resetBuilds();
}

const OPTION_SETTINGS: Record<keyof ProjectOptions, string> = {
  auto_development: "project_auto_development",
  max_auto_development: "project_max_auto_development",
  development_demo_data: "project_development_demo_data",
  staging_on_push: "project_staging_on_push",
  branch_deleted: "project_branch_deleted",
  upgrade_test_on_push: "project_upgrade_test_on_push",
  development_lifetime_days: "delete_after_days",
  staging_lifetime_days: "staging_delete_after_days",
};

export function options(project: DemoProject): ProjectOptions {
  return Object.fromEntries(
    (Object.keys(OPTION_SETTINGS) as (keyof ProjectOptions)[]).map((key) => [key, project.own[key] ?? setting(OPTION_SETTINGS[key])]),
  ) as unknown as ProjectOptions;
}

export const membersOf = (id: string): Instance[] => [...instances.values()].filter((i) => i.project?.id === id).sort((a, b) => a.name.localeCompare(b.name));

export function branchNames(project: DemoProject): string[] {
  const names = new Set(project.repo?.branches ?? []);
  for (const instance of membersOf(project.id)) if (instance.git.branch) names.add(instance.git.branch);
  for (const branch of Object.keys(project.stages)) names.add(branch);
  return [...names];
}

export function stageOf(project: DemoProject, branch: string): Stage {
  if (project.stages[branch]) return project.stages[branch]!;
  const found = membersOf(project.id).filter((i) => i.git.branch === branch).map((i) => i.stage);
  return found.sort((a, b) => RANK[b] - RANK[a])[0] ?? "development";
}

export function branchList(project: DemoProject, everything = false): ProjectBranch[] {
  const members = membersOf(project.id);
  return branchNames(project)
    .filter((name) => everything || levelIn(project.access, name) !== "none")
    .map((name) => {
      const own = members.filter((i) => i.git.branch === name);
      const commit = own[0]?.recent_commits?.[0] ?? (project.repo ? commitsOf(project.repo, name)[0] : undefined);
      return {
        name,
        stage: stageOf(project, name),
        default: name === project.repo?.defaultBranch,
        on_github: project.repo ? project.repo.branches.includes(name) : true,
        commit: commit ? { sha: commit.sha, full_sha: commit.sha, subject: commit.subject, when: commit.when, author: commit.author } : null,
        instances: own.map((i) => i.name),
        auto_created: project.autoCreated[name] ?? null,
        lock: project.access.locks[name] ? { ...project.access.locks[name]!, allow: described(project.access.locks[name]!.allow) } : null,
        restricted: Boolean(project.access.branches[name]?.restricted),
        permissions: permissionsIn(project.access, name).filter((p) => PROJECT_PERMISSIONS.includes(p)),
      };
    })
    .sort((a, b) => RANK[b.stage] - RANK[a.stage] || Number(b.default) - Number(a.default) || a.name.localeCompare(b.name));
}

export function projectResource(project: DemoProject): Project {
  const listed = branchList(project);
  const shown = new Set(listed.map((b) => b.name));
  const members = membersOf(project.id).filter((i) => !i.git.branch || shown.has(i.git.branch));
  const byStatus: Project["instances"]["by_status"] = {};
  for (const instance of members) byStatus[instance.status] = (byStatus[instance.status] ?? 0) + 1;
  return {
    id: project.id,
    name: project.name,
    repository: { full_name: project.repository, url: `https://github.com/${project.repository}` },
    odoo: { version: project.version, edition: project.edition },
    origin: project.origin,
    created_at: iso(project.created_at),
    created_by: project.created_by,
    branches: {
      total: listed.length,
      production: listed.filter((b) => b.stage === "production").length,
      staging: listed.filter((b) => b.stage === "staging").length,
      development: listed.filter((b) => b.stage === "development").length,
    },
    instances: { total: members.length, by_status: byStatus, names: members.map((i) => i.name) },
    attention: members.filter((i) => i.update_failure || i.drift || i.git.branch_deleted_on_github).map((i) => i.name),
    production_branches: listed.filter((b) => b.stage === "production").map((b) => b.name),
    options: options(project),
    own_options: { ...project.own },
    access: {
      mode: project.access.mode,
      grants: project.access.grants.length,
      restricted_branches: Object.entries(project.access.branches).filter(([b, v]) => v.restricted && shown.has(b)).map(([b]) => b).sort(),
      locked_branches: Object.keys(project.access.locks).filter((b) => shown.has(b)).sort(),
    },
    links: { self: `/api/v1/projects/${project.id}` },
  };
}

/** The production instance staging copies: the first production branch's production instance. */
export function productionInstance(project: DemoProject): Instance | null {
  for (const branch of branchList(project).filter((b) => b.stage === "production"))
    for (const name of branch.instances) {
      const instance = instances.get(name);
      if (instance?.stage === "production" && instance.status !== "creating") return instance;
    }
  return null;
}
