// Resources of the demo's projects and branches (as src/sudoo/web/v1/project_resources.py): each stage's defaults,
// the project's values per stage, a branch's own, the maxima, the computed container limits, the memory reserved on
// the demo server and the storage of each instance against its stage's quota.

import {
  RESOURCE_KNOBS,
  type BranchResources,
  type Instance,
  type ProjectResources,
  type ResourceKnob,
  type ResourceLayerView,
  type ResourceLimits,
  type ResourcePlan,
  type Stage,
  type StorageUse,
} from "@/api/types";
import { cleanLimits, memoryBytes } from "./resources";
import { membersOf, stageOf, type DemoProject } from "./projects";
import { setting } from "./state";
import { Problem } from "./util";

const GIB = 1024 ** 3;
const STAGES: Stage[] = ["production", "staging", "development"];
const ODOO_DEFAULTS = {
  odoo_cron_threads: 2,
  odoo_limit_memory_soft_mb: 2048,
  odoo_limit_memory_hard_mb: 2560,
  odoo_limit_time_cpu: 60,
  odoo_limit_time_real: 120,
  odoo_limit_time_real_cron: 120,
  db_max_connections: 100,
};
const MEMORY = new Set<ResourceKnob>(["odoo_memory_limit", "db_memory_limit"]);
const CORES = new Set<ResourceKnob>(["odoo_cpu_limit", "db_cpu_limit"]);

const projectOwn = new Map<string, Partial<Record<Stage, Partial<ResourceLimits>>>>();
const branchOwn = new Map<string, Partial<ResourceLimits>>();
let seeded = false;

function seed(project: DemoProject) {
  if (seeded) return;
  seeded = true;
  // the first project sizes its production as Odoo.sh would: 2 workers, a longer timeout for its reports
  projectOwn.set(project.id, {
    production: {
      odoo_workers: 2,
      odoo_cron_threads: 1,
      odoo_limit_memory_soft_mb: 640,
      odoo_limit_memory_hard_mb: 768,
      odoo_limit_time_real: 300,
    },
  });
}

const stageDefaults = (stage: Stage) =>
  Object.fromEntries(RESOURCE_KNOBS.map((k) => [k, setting(`${stage}_${k}`) ?? 0])) as ResourceLimits;
export const maxima = () =>
  Object.fromEntries(RESOURCE_KNOBS.map((k) => [k, setting(`resources_max_${k}`) ?? 0])) as ResourceLimits;

const amount = (knob: ResourceKnob, value: unknown) =>
  MEMORY.has(knob) ? memoryBytes(String(value ?? "0")) : Number(value ?? 0);
function above(knob: ResourceKnob, value: unknown, maximum: unknown): boolean {
  const top = amount(knob, maximum);
  if (!top) return false;
  const found = amount(knob, value);
  return found > top || (!found && (MEMORY.has(knob) || CORES.has(knob)));
}
const clamp = (values: Partial<ResourceLimits>) => {
  const bounds = maxima();
  return Object.fromEntries(
    Object.entries(values).map(([k, v]) => [
      k,
      above(k as ResourceKnob, v, bounds[k as ResourceKnob]) ? bounds[k as ResourceKnob] : v,
    ]),
  ) as Partial<ResourceLimits>;
};

function computed(values: Partial<ResourceLimits>): Partial<ResourceLimits> {
  const workers = Number(values.odoo_workers ?? 0);
  if (!workers || setting("resources_computed_limits") === false) return {};
  const found: Partial<ResourceLimits> = {};
  if (!memoryBytes(String(values.odoo_memory_limit ?? "0"))) {
    const cron = Number(values.odoo_cron_threads) || 2;
    const soft = Number(values.odoo_limit_memory_soft_mb) || 2048;
    found.odoo_memory_limit = `${Number(setting("resources_base_memory_mb") ?? 512) + (workers + cron) * soft}m`;
  }
  if (!Number(values.odoo_cpu_limit ?? 0)) found.odoo_cpu_limit = Math.min(Math.max(1, workers), 8);
  return found;
}

function view(below: Record<string, Partial<ResourceLimits>>, own: Partial<ResourceLimits>): ResourceLayerView {
  const inherited = Object.assign({}, ...Object.values(below)) as ResourceLimits;
  const values = { ...inherited, ...own } as ResourceLimits;
  const layers = Object.keys(below).reverse();
  const sources = Object.fromEntries(
    RESOURCE_KNOBS.map((k) => [k, k in own ? "own" : (layers.find((l) => l !== "stage" && k in below[l]!) ?? "stage")]),
  ) as ResourceLayerView["sources"];
  // as the backend: only workers a project or a branch sets get container limits computed from them
  const extra = sources.odoo_workers === "stage" ? {} : computed(values);
  return {
    own,
    inherited,
    applies: { ...values, ...extra },
    computed: Object.keys(extra).sort() as ResourceKnob[],
    sources,
  };
}

const quota = (stage: Stage) => Number(setting(`storage_quota_gb_${stage}`) ?? 0) * GIB;
export function storageOf(instance: Instance): StorageUse {
  const database = instance.disk?.database_bytes ?? null;
  const files = instance.disk?.files_bytes ?? null;
  const total = database == null && files == null ? null : (database ?? 0) + (files ?? 0);
  const q = quota(instance.stage);
  const warn = Number(setting("storage_warning_percent") ?? 80);
  const level = !q || total == null ? "none" : total > q ? "over" : total * 100 >= q * warn ? "warning" : "ok";
  return {
    database_bytes: database,
    files_bytes: files,
    total_bytes: total,
    measured_at: total == null ? null : Date.now() / 1000 - 1800,
    quota_bytes: q || null,
    percent: q && total != null ? Math.round((1000 * total) / q) / 10 : null,
    level,
  };
}

/** The demo server, as an instance's Resources section shows it. */
const server = () => ({
  cores: 8,
  memory_bytes: 32 * GIB,
  usable_bytes: 32 * GIB - (1024 ** 3 + 3.2 * GIB) - 290 * 1024 ** 2,
  reserved_bytes: reserved(),
});

function limitsOf(
  instance: Instance,
  project: DemoProject,
  override?: { stage?: Partial<ResourceLimits>; branch?: Partial<ResourceLimits> },
): ResourceLimits {
  seed(project);
  const branch = instance.git.branch ?? "";
  const below = {
    stage: stageDefaults(instance.stage),
    project: clamp(override?.stage ?? projectOwn.get(project.id)?.[instance.stage] ?? {}),
    branch: clamp(override?.branch ?? branchOwn.get(`${project.id} ${branch}`) ?? {}),
  };
  return view(below, {}).applies;
}

function reservedBy(stage: Stage, limits: Partial<ResourceLimits>): number {
  const odoo =
    memoryBytes(String(limits.odoo_memory_limit ?? "0")) ||
    memoryBytes(String(setting(`priority_memory_reservation_${stage}`) ?? "0"));
  const db =
    memoryBytes(String(limits.db_memory_limit ?? "0")) ||
    memoryBytes(String(setting(`priority_db_memory_reservation_${stage}`) ?? "0"));
  return stage === "production" ? odoo + db : Math.round((odoo + db) * Number(setting("capacity_overcommit") ?? 0.5));
}

let projectsOf: () => DemoProject[] = () => [];
export const knowProjects = (all: () => DemoProject[]) => (projectsOf = all);
function reserved(changes: Map<string, ResourceLimits> = new Map()): number {
  let total = 0;
  for (const project of projectsOf())
    for (const i of membersOf(project.id)) total += reservedBy(i.stage, changes.get(i.name) ?? limitsOf(i, project));
  return total;
}

function plan(changes: Map<string, ResourceLimits>): ResourcePlan {
  const before = reserved();
  const after = reserved(changes);
  const usable = server().usable_bytes;
  const refusal =
    after > before && after > usable
      ? `This change would reserve ${(after / GIB).toFixed(1)} GB of memory for instances, more than the ${(usable / GIB).toFixed(1)} GB this server can spare for them. Lower the workers or memory limits, pause or delete instances, or give the server more memory.`
      : null;
  return { before_bytes: before, after_bytes: after, usable_bytes: usable, refusal };
}

const instanceView = (project: DemoProject) => (i: Instance) => ({
  name: i.name,
  branch: i.git.branch ?? "",
  stage: i.stage,
  limits: limitsOf(i, project),
  pending: false,
  storage: storageOf(i),
});

export function projectResources(project: DemoProject): ProjectResources {
  seed(project);
  const members = membersOf(project.id);
  const byStage = { production: 0, staging: 0, development: 0 } as Record<Stage, number>;
  const storage: ProjectResources["storage"]["instances"] = {};
  for (const i of members) {
    const use = storageOf(i);
    storage[i.name] = { ...use, stage: i.stage };
    byStage[i.stage] += use.total_bytes ?? 0;
  }
  return {
    project: project.id,
    stages: Object.fromEntries(
      STAGES.map((stage) => [
        stage,
        {
          ...view({ stage: stageDefaults(stage) }, clamp(projectOwn.get(project.id)?.[stage] ?? {})),
          quota_bytes: quota(stage) || null,
        },
      ]),
    ) as ProjectResources["stages"],
    maxima: maxima(),
    odoo_defaults: ODOO_DEFAULTS,
    instances: members.map(instanceView(project)),
    storage: {
      instances: storage,
      by_stage: byStage,
      total_bytes: byStage.production + byStage.staging + byStage.development,
    },
    server: server(),
    changed: null,
  };
}

export function branchResources(project: DemoProject, branch: string): BranchResources {
  seed(project);
  const stage = stageOf(project, branch);
  const members = membersOf(project.id).filter((i) => i.git.branch === branch);
  return {
    project: project.id,
    branch,
    stage,
    ...view(
      { stage: stageDefaults(stage), project: clamp(projectOwn.get(project.id)?.[stage] ?? {}) },
      clamp(branchOwn.get(`${project.id} ${branch}`) ?? {}),
    ),
    maxima: maxima(),
    odoo_defaults: ODOO_DEFAULTS,
    instances: members.map(instanceView(project)),
    quota_bytes: quota(stage) || null,
    server: server(),
    changed: null,
  };
}

/** A project's or a branch's new values, checked as the backend checks them (maxima included). */
function clean(raw: unknown): Partial<ResourceLimits> {
  const found = cleanLimits(raw);
  const bounds = maxima();
  for (const [knob, value] of Object.entries(found)) {
    if (above(knob as ResourceKnob, value, bounds[knob as ResourceKnob]))
      throw new Problem(400, `${knob}: at most ${bounds[knob as ResourceKnob]}, the administrator's maximum.`);
  }
  return found;
}

/** PUT: preview, or save and say which instances restart. */
export function change(project: DemoProject, body: Record<string, unknown>, branch?: string) {
  seed(project);
  const values = clean(body.limits);
  const stage = branch == null ? (body.stage as Stage) : stageOf(project, branch);
  if (!STAGES.includes(stage))
    throw new Problem(400, "Send the stage to change: one of production, staging, development.");
  const members = membersOf(project.id).filter((i) => (branch == null ? i.stage === stage : i.git.branch === branch));
  const after = new Map(
    members.map((i) => [i.name, limitsOf(i, project, branch == null ? { stage: values } : { branch: values })]),
  );
  const changed = members
    .filter((i) => JSON.stringify(limitsOf(i, project)) !== JSON.stringify(after.get(i.name)))
    .map((i) => i.name);
  const found = plan(new Map(changed.map((n) => [n, after.get(n)!])));
  const below: Record<string, Partial<ResourceLimits>> = { stage: stageDefaults(stage) };
  if (branch != null) below.project = clamp(projectOwn.get(project.id)?.[stage] ?? {});
  if (body.preview === true)
    return { preview: true as const, data: { plan: found, changes: changed, view: view(below, values) } };
  if (found.refusal) throw new Problem(409, found.refusal);
  if (
    changed.some((n) => members.find((i) => i.name === n)?.stage === "production") &&
    body.confirm_production !== true
  )
    throw new Problem(400, 'This change restarts production instances. Confirm it ("confirm_production": true).');
  if (branch == null) projectOwn.set(project.id, { ...projectOwn.get(project.id), [stage]: values });
  else branchOwn.set(`${project.id} ${branch}`, values);
  return { preview: false as const, changed: members.filter((i) => changed.includes(i.name)), plan: found };
}
