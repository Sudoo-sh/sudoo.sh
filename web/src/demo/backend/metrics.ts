// The figures of the server and of every instance: one sample every few seconds, from smooth noise that is the same
// for the same instance and time, so the last hour (generated at once) runs on into the live samples without a seam.

import type { Capacity, Columns, Instance, Metrics, MetricsHistory, ServerFigures, Usage } from "@/api/types";
import { publish } from "./bus";
import { memoryLimit, reservations } from "./resources";
import { ASLEEP, instances, MEMORY_LIMIT, profiles, type Profile } from "./state";
import { clamp, DAY, hash, HOUR, noise, OPENED, round, SECOND } from "./util";

export const INTERVAL = 3; // seconds between samples (METRICS_INTERVAL_SECONDS)
const HISTORY = 3600;
const GIB = 1024 ** 3;
const MIB = 1024 ** 2;
const CORES = 8;
const MEMORY_TOTAL = 32 * GIB;
const DISK_TOTAL = 480 * 1000 ** 3;
const BOOT = OPENED - 23 * DAY - 7 * HOUR;

type Sample = Usage & { running: boolean };

/** One instance's figures at second `t`, or null while it was not running. */
function sample(profile: Profile, t: number, limit = MEMORY_LIMIT): Sample | null {
  if (t * 1000 < profile.since) return null;
  const s = profile.seed;
  const busy = t * 1000 >= profile.boostFrom && t * 1000 < profile.boostUntil ? 1 : 0;
  // traffic: a slow wave (people working), a faster ripple and now and then a burst
  const wave = 1 + 0.28 * Math.sin((2 * Math.PI * t) / 1500 + (s % 7)) + 0.12 * Math.sin((2 * Math.PI * t) / 230 + (s % 5));
  const burst = Math.max(0, noise(s ^ 0x55, t, 90) - 0.6) * 1.6;
  const rpm = Math.max(0, profile.rpm * (wave + burst) * (1 + 0.1 * noise(s ^ 0x11, t, 40)));
  const load = profile.rpm ? rpm / profile.rpm : 1;
  const cpu = clamp(profile.cpu * (0.55 + 0.45 * load) * (1 + 0.15 * noise(s ^ 0x22, t, 30)) + busy * (5 + 2 * noise(s, t, 15)), 0.05, 95);
  const memory = profile.memory * (1 + 0.035 * noise(s ^ 0x33, t, 120) + 0.01 * noise(s ^ 0x34, t, 45)) + busy * 180 * MIB;
  const avg = profile.ms * (0.8 + 0.25 * load) * (1 + 0.12 * noise(s ^ 0x44, t, 35)) + busy * 90;
  const errors = profile.errors ? Math.max(0, profile.errors * (1 + 1.5 * noise(s ^ 0x66, t, 20))) : 0;
  return {
    running: true,
    cpu_percent: round(cpu, 2),
    memory_bytes: Math.round(memory),
    memory_limit_bytes: limit,
    memory_percent: round((100 * memory) / limit, 1),
    requests_per_minute: round(rpm, 1),
    error_rate_percent: rpm > 0 ? round(errors, 2) : null,
    response_ms_avg: rpm > 0 ? round(avg, 1) : null,
    response_ms_p95: rpm > 0 ? round(avg * (2.2 + 0.35 * noise(s ^ 0x77, t, 40)), 1) : null,
    uptime_seconds: Math.round(t - profile.since / 1000),
    disk_bytes: null,
  };
}

const PANEL_MEMORY = 290 * MIB;

function server(samples: Record<string, Sample>, t: number, running: number, total: number): Partial<ServerFigures> {
  const list = Object.values(samples).filter((s) => s.running);
  const instanceCpu = list.reduce((sum, s) => sum + s.cpu_percent, 0);
  const instanceMemory = list.reduce((sum, s) => sum + s.memory_bytes, 0);
  const panelCpu = round(0.5 + 0.3 * Math.abs(noise(91, t, 8)), 2);
  const panelMemory = PANEL_MEMORY + 14 * MIB * noise(92, t, 60);
  const cpu = clamp(2.2 + instanceCpu + panelCpu + 0.8 * noise(93, t, 30), 0.5, 100);
  const used = 3.1 * GIB + instanceMemory + panelMemory;
  const diskUsed = 172 * 1000 ** 3 + ((t * 1000 - OPENED) / HOUR) * 0.4 * 1000 ** 3;
  return {
    cpu_percent: round(cpu, 1),
    cores: CORES,
    load_1m: round((cpu / 100) * CORES * 0.9, 2),
    memory_used_bytes: Math.round(used),
    memory_total_bytes: MEMORY_TOTAL,
    memory_percent: round((100 * used) / MEMORY_TOTAL, 1),
    disk_used_bytes: Math.round(diskUsed),
    disk_total_bytes: DISK_TOTAL,
    disk_percent: round((100 * diskUsed) / DISK_TOTAL, 1),
    uptime_seconds: Math.round(t - BOOT / 1000),
    instances_running: running,
    instances_total: total,
    panel_cpu_percent: panelCpu,
    panel_memory_bytes: Math.round(panelMemory),
    panel_cpu_peak_percent: 3.4,
    panel_memory_peak_bytes: 318 * MIB,
  };
}

const runs = (instance: Instance) => !ASLEEP.includes(instance.status) && instance.status !== "creating";

function at(t: number) {
  const samples: Record<string, Sample> = {};
  let running = 0;
  for (const [name, instance] of instances) {
    const profile = profiles.get(name);
    const figures = profile && runs(instance) ? sample(profile, t, memoryLimit(instance)) : null;
    if (figures) running += 1;
    samples[name] = figures ?? { running: false, cpu_percent: 0, memory_bytes: 0, memory_limit_bytes: MEMORY_LIMIT, memory_percent: 0, requests_per_minute: 0, error_rate_percent: null, response_ms_avg: null, response_ms_p95: null, uptime_seconds: null, disk_bytes: null };
  }
  return { samples, running, server: server(samples, t, running, instances.size) };
}

export function capacity(running = [...instances.values()].filter(runs).length): Capacity {
  const reserve = 1024 * MIB + MEMORY_TOTAL * 0.1;
  const usable = MEMORY_TOTAL - reserve - PANEL_MEMORY;
  const measured = [...profiles.values()];
  const perInstance = Math.round(measured.reduce((sum, p) => sum + p.memory, 0) / measured.length);
  const comfortable = Math.floor(usable / perInstance);
  const byVersion = new Map<string, number[]>();
  for (const [name, instance] of instances) {
    const p = profiles.get(name);
    if (!p) continue;
    byVersion.set(instance.odoo.version, [...(byVersion.get(instance.odoo.version) ?? []), p.memory]);
  }
  return {
    comfortable,
    running,
    headroom: comfortable - running,
    limited_by: "memory",
    warning: running + 1 > comfortable,
    message: null,
    overloaded: false,
    alert: null,
    memory_percent: null,
    warning_percent: 85,
    hard_limit: null,
    blocked: false,
    memory: {
      total_bytes: MEMORY_TOTAL,
      reserve_bytes: Math.round(reserve),
      panel_bytes: PANEL_MEMORY,
      usable_bytes: Math.round(usable),
      per_instance_bytes: perInstance,
      per_instance_source: "measured",
      instance_average_bytes: perInstance,
      instance_peak_bytes: Math.round(perInstance * 1.4),
      instance_largest_bytes: Math.round(Math.max(...measured.map((p) => p.memory)) * 1.1),
      measured_instances: measured.length,
      instances: comfortable,
    },
    processor: { cores: CORES, panel_percent: 0.6, per_instance_percent: 2.3, instances: 36 },
    disk: { free_bytes: DISK_TOTAL - 175 * 1000 ** 3, instance_average_bytes: Math.round(2.6 * GIB), more_instances: 109 },
    reservations: reservations(),
    by_version: [...byVersion].sort().map(([version, list]) => ({
      version,
      instances: list.length,
      average_bytes: Math.round(list.reduce((a, b) => a + b, 0) / list.length),
      peak_bytes: Math.round(Math.max(...list) * 1.3),
    })),
    panel: {
      cpu_percent: 0.6,
      cpu_peak_percent: 3.4,
      memory_bytes: PANEL_MEMORY,
      memory_peak_bytes: 318 * MIB,
      parts: {
        backend: { cpu_percent: 0.4, memory_bytes: 172 * MIB, containers: 1 },
        web: { cpu_percent: 0.0, memory_bytes: 21 * MIB, containers: 1 },
        gateway: { cpu_percent: 0.1, memory_bytes: 38 * MIB, containers: 1 },
        connectors: { cpu_percent: 0.1, memory_bytes: 59 * MIB, containers: 11 },
      },
    },
  };
}

let latest: Metrics | null = null;

function measure(t: number): Metrics {
  const { samples, running, server: figures } = at(t);
  // each instance shows its newest figures too (GET /instances and the "instance" events)
  for (const [name, instance] of instances) {
    const s = samples[name]!;
    const { running: _running, ...usage } = s;
    void _running;
    instance.usage = s.running ? { ...usage, disk_bytes: (instance.disk?.database_bytes ?? 0) + (instance.disk?.files_bytes ?? 0) } : { cpu_percent: 0, memory_bytes: 0 };
    instance.uptime = s.running && s.uptime_seconds != null ? `Up ${uptimeText(s.uptime_seconds)}` : null;
  }
  latest = { at: Math.floor(t), interval_seconds: INTERVAL, server: figures, instances: samples, capacity: capacity(running) };
  return latest;
}

function uptimeText(seconds: number) {
  if (seconds < 3600) return `${Math.max(1, Math.round(seconds / 60))} minutes`;
  if (seconds < 2 * 86400) return `${Math.round(seconds / 3600)} hours`;
  return `${Math.round(seconds / 86400)} days`;
}

const tick = () => Math.floor(Date.now() / 1000 / INTERVAL) * INTERVAL;

export function currentMetrics(): Metrics {
  return latest ?? measure(tick());
}

export function history(minutes = 60): MetricsHistory {
  const end = tick();
  const start = end - Math.min(HISTORY, minutes * 60);
  const serverColumns: Columns = { t: [] } as unknown as Columns;
  const instanceColumns: Record<string, Columns> = {};
  for (let t = start; t <= end; t += INTERVAL) {
    const { samples, server: figures } = at(t);
    serverColumns.t.push(t);
    for (const [key, value] of Object.entries(figures)) (serverColumns[key] ??= []).push(typeof value === "number" ? value : null);
    for (const [name, s] of Object.entries(samples)) {
      const columns = (instanceColumns[name] ??= { t: [] } as unknown as Columns);
      columns.t.push(t);
      for (const key of ["cpu_percent", "memory_percent", "memory_bytes", "requests_per_minute", "error_rate_percent", "response_ms_avg", "response_ms_p95"] as const) {
        (columns[key] ??= []).push(s.running ? (s[key] ?? null) : null);
      }
    }
  }
  return { server: serverColumns, instances: instanceColumns };
}

/** Starts the stream of samples: a "metrics" event every INTERVAL seconds, and the instances' new figures. */
export function startMetrics() {
  measure(tick()); // the first answers already carry the instances' figures
  let count = 0;
  const timer = setInterval(() => {
    const metrics = measure(tick());
    publish("metrics", "", metrics);
    count += 1;
    // the cards' figures (CPU, memory, requests) follow every other sample
    if (count % 2 === 0) {
      for (const instance of instances.values()) {
        if (runs(instance)) publish("instance", instance.name, listed(instance));
      }
    }
  }, INTERVAL * SECOND);
  return () => clearInterval(timer);
}

/** An instance as GET /instances lists it: without what only GET /instances/<name> adds. */
export function listed(instance: Instance): Instance {
  const { disk: _d, recent_commits: _c, submodules: _s, recent_jobs: _j, backups: _b, restorable_backup: _r, rollback: _o, ...rest } = instance;
  void [_d, _c, _s, _j, _b, _r, _o];
  return rest;
}

/** The figures of one instance for its seed, at most a little random: used when a new instance appears. */
export function newProfile(name: string, production: boolean): Profile {
  const h = hash(name);
  return {
    seed: h,
    cpu: production ? 3 : 0.8 + (h % 10) / 10,
    memory: (production ? 1.2 : 0.65) * GIB * (1 + (h % 20) / 100),
    rpm: production ? 30 : 3 + (h % 6),
    ms: 55 + (h % 40),
    errors: 0,
    since: Number.POSITIVE_INFINITY,
    boostFrom: 0,
    boostUntil: 0,
  };
}
