// Resources of the demo's instances (as src/sudoo/instances/resources.py): no limits by default, the stage's weights
// under load, and each instance's own limits. The first production instance has a few limits set, as an
// administrator might.

import { RESOURCE_KNOBS, type Instance, type InstanceResources, type ResourceKnob, type ResourceLimits, type Stage } from "@/api/types";
import { instances, setting } from "./state";
import { Problem } from "./util";
import { storageOf } from "./project-resources";

const GIB = 1024 ** 3;
const CORES = 8;
const MEMORY_TOTAL = 32 * GIB;
const USABLE = MEMORY_TOTAL - (1024 * 1024 ** 2 + MEMORY_TOTAL * 0.1) - 290 * 1024 ** 2;
const RANK: Record<Stage, 1 | 2 | 3> = { production: 1, staging: 2, development: 3 };
const ODOO_DEFAULTS = { odoo_cron_threads: 2, odoo_limit_memory_soft_mb: 2048, odoo_limit_memory_hard_mb: 2560, odoo_limit_time_cpu: 60, odoo_limit_time_real: 120, odoo_limit_time_real_cron: 120, db_max_connections: 100 };
const INTEGER: Partial<Record<ResourceKnob, number>> = {
  odoo_workers: 256, odoo_cron_threads: 64, odoo_limit_memory_soft_mb: 1048576, odoo_limit_memory_hard_mb: 1048576, odoo_limit_time_cpu: 86400, odoo_limit_time_real: 86400,
  odoo_limit_time_real_cron: 86400, db_max_connections: 10000,
};

const own = new Map<string, Partial<ResourceLimits>>();
const pending = new Set<string>();
let seeded = false;

function seed() {
  if (seeded) return;
  seeded = true;
  const first = [...instances.values()].find((i) => i.stage === "production");
  if (first) own.set(first.name, { odoo_memory_limit: "6g", odoo_limit_time_real: 300 });
}

export function memoryBytes(text: string | number): number {
  const match = /^(\d+(?:\.\d+)?)([bkmg])$/i.exec(String(text).trim());
  return match ? Number(match[1]) * 1024 ** "bkmg".indexOf(match[2]!.toLowerCase()) : 0;
}

const stageDefaults = (stage: Stage) => Object.fromEntries(RESOURCE_KNOBS.map((k) => [k, setting(`${stage}_${k}`) ?? 0])) as ResourceLimits;
const priority = (stage: Stage) => ({
  rank: RANK[stage],
  cpu_weight: Number(setting(`priority_cpu_weight_${stage}`)),
  oom_score_adj: Number(setting(`priority_oom_score_${stage}`)),
  memory_reservation: String(setting(`priority_memory_reservation_${stage}`)),
  db_memory_reservation: String(setting(`priority_db_memory_reservation_${stage}`)),
});

export function reservations() {
  const by_stage: Record<Stage, number> = { production: 0, staging: 0, development: 0 };
  for (const i of instances.values()) {
    const p = priority(i.stage);
    by_stage[i.stage] += memoryBytes(p.memory_reservation) + memoryBytes(p.db_memory_reservation);
  }
  return { total_bytes: by_stage.production + by_stage.staging + by_stage.development, by_stage };
}

/** The Odoo container's memory limit in bytes, or the server's memory when there is none (what Docker reports). */
export function memoryLimit(instance: Instance): number {
  seed();
  const value = own.get(instance.name)?.odoo_memory_limit ?? stageDefaults(instance.stage).odoo_memory_limit;
  return memoryBytes(value) || MEMORY_TOTAL;
}

export function resourcesOf(instance: Instance): InstanceResources {
  seed();
  const defaults = stageDefaults(instance.stage);
  const mine = own.get(instance.name) ?? {};
  const limits = { ...defaults, ...mine };
  const warnings: InstanceResources["warnings"] = [];
  const reserved = reservations().total_bytes;
  if (reserved > USABLE) warnings.push({ code: "reserved_over_usable", text: "The memory reserved for instances is more than this server can spare for them.", values: { reserved_bytes: reserved, usable_bytes: USABLE } });
  const workers = Number(limits.odoo_workers);
  if (workers > 2 * CORES + 1)
    warnings.push({ code: "workers_over_cores", text: `${workers} workers on ${CORES} cores.`, values: { workers, cores: CORES, suggested: 2 * CORES + 1 } });
  const container = memoryBytes(limits.odoo_memory_limit);
  const cron = Number(limits.odoo_cron_threads) || 2;
  const soft = (Number(limits.odoo_limit_memory_soft_mb) || 2048) * 1024 ** 2;
  if (container && workers && container < ((workers + cron) * soft) / 2)
    warnings.push({ code: "workers_memory", text: "The workers may need more memory than the limit.", values: { workers, cron, limit_bytes: container } });
  return {
    stage: instance.stage,
    priority: priority(instance.stage),
    limits,
    own: mine,
    stage_defaults: defaults,
    odoo_defaults: ODOO_DEFAULTS,
    unlimited: !memoryBytes(limits.odoo_memory_limit) && !memoryBytes(limits.db_memory_limit) && !Number(limits.odoo_cpu_limit) && !Number(limits.db_cpu_limit),
    pending: pending.has(instance.name),
    storage: storageOf(instance),
    warnings,
    server: { cores: CORES, memory_bytes: MEMORY_TOTAL, usable_bytes: USABLE, reserved_bytes: reserved },
  };
}

/** The instance's own limits from a request, checked as the backend checks them (resources.clean). */
export function cleanLimits(data: unknown): Partial<ResourceLimits> {
  if (!data || typeof data !== "object") throw new Problem(400, 'Send the limits to set: {"limits": {"odoo_workers": 2}}.');
  const found: Partial<ResourceLimits> = {};
  for (const [knob, value] of Object.entries(data as Record<string, unknown>)) {
    if (!RESOURCE_KNOBS.includes(knob as ResourceKnob)) throw new Problem(400, `Unknown limits: ${knob}.`);
    if (value == null || value === "") continue;
    const k = knob as ResourceKnob;
    const text = String(value).trim().toLowerCase();
    if (k in INTEGER) {
      if (!/^\d+$/.test(text)) throw new Problem(400, `${k}: a whole number from 0 is expected.`);
      if (Number(text) > INTEGER[k]!) throw new Problem(400, `${k}: at most ${INTEGER[k]}.`);
      found[k] = Number(text);
    } else if (k.endsWith("cpu_limit")) {
      const cores = Number(text);
      if (!Number.isFinite(cores) || cores < 0) throw new Problem(400, `${k}: a number of processor cores from 0 is expected.`);
      if (cores > CORES) throw new Problem(400, `${k}: this server has ${CORES} cores; Docker refuses more.`);
      found[k] = Math.round(cores * 100) / 100;
    } else {
      if (text !== "0" && !/^\d+(\.\d+)?[bkmg]$/.test(text)) throw new Problem(400, `${k}: a quantity of memory in Docker units is expected (512m, 2g), or 0.`);
      if (text !== "0" && memoryBytes(text) < 128 * 1024 ** 2) throw new Problem(400, `${k}: at least 128m, or 0 for no limit.`);
      found[k] = text;
    }
  }
  const soft = Number(found.odoo_limit_memory_soft_mb ?? 0);
  const hard = Number(found.odoo_limit_memory_hard_mb ?? 0);
  if (soft && hard && soft > hard) throw new Problem(400, "The soft memory limit per worker is above the hard one: Odoo would stop requests first.");
  return found;
}

export function setOwn(instance: Instance, limits: Partial<ResourceLimits>) {
  seed();
  own.set(instance.name, limits);
}

/** After a change of stage: the containers keep the old weights until the instance restarts. */
export const markPending = (name: string) => pending.add(name);
export const applied = (name: string) => pending.delete(name);
