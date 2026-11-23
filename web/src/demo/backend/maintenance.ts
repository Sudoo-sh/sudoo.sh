// Maintenance windows in the demo (src/sudoo/maintenance.py, web/v1/maintenance.py): Sudoo.sh planned new Odoo images
// for Acme Retail (nobody chose a time yet: the picker shows Tonight, This weekend and the default window), and a
// panel restart last week is done. Times are counted in the demo's TIMEZONE, as the panel counts them.

import type { MaintenanceItem, MaintenanceMeta, MaintenancePreset, Permission } from "@/api/types";
import { CLIENT_ME } from "./access";
import { ok, route } from "./api";
import { membersOf, projects } from "./projects";
import { ME, record, roleInfo, session, setting } from "./state";
import { DAY, HOUR, MINUTE, Problem } from "./util";

const items: MaintenanceItem[] = [];
const DAYS = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"];

const zone = () => String(setting("timezone") ?? "UTC");
const clientChoice = () => Boolean(setting("maintenance_client_choice"));
const can = (permission: Permission) => roleInfo(session.role).permissions.includes(permission);

/** The zone's offset from UTC at `at`, in milliseconds. */
function offset(at: number, timeZone: string): number {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-US", { timeZone, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" })
      .formatToParts(new Date(at))
      .map((p) => [p.type, p.value]),
  );
  const n = (key: string) => Number(parts[key] ?? 0);
  return Date.UTC(n("year"), n("month") - 1, n("day"), n("hour"), n("minute"), n("second")) - at;
}

/** An ISO time with the zone's offset, as the panel sends it. */
function iso(at: number | null | undefined): string | null {
  if (at == null) return null;
  const shift = offset(at, zone());
  const local = new Date(at + shift).toISOString().slice(0, 19);
  const minutes = Math.round(shift / MINUTE);
  const sign = minutes < 0 ? "-" : "+";
  const abs = Math.abs(minutes);
  return `${local}${sign}${String(Math.floor(abs / 60)).padStart(2, "0")}:${String(abs % 60).padStart(2, "0")}`;
}

/** The first HH:MM in the zone, on one of `days`, between `after` and `until`. */
function nextAt(clock: string, after: number, until: number, days?: string[]): number | null {
  const [h = 0, m = 0] = clock.split(":").map(Number);
  for (let i = 0; i < 400; i++) {
    const day = new Date(after + i * DAY + offset(after, zone()));
    const wall = Date.UTC(day.getUTCFullYear(), day.getUTCMonth(), day.getUTCDate(), h, m);
    const at = wall - offset(wall, zone());
    if (at > until) return null;
    if (at >= after && (!days || days.includes(DAYS[new Date(wall).getUTCDay()] ?? ""))) return at;
  }
  return null;
}

const ms = (value: string | null) => (value ? new Date(value).getTime() : 0);

function bounds(item: MaintenanceItem): [number, number] {
  const lead = Number(setting("maintenance_lead_minutes") ?? 30) * MINUTE;
  return [Math.max(ms(item.earliest_start), Date.now() + lead), ms(item.deadline)];
}

function refresh(item: MaintenanceItem): MaintenanceItem {
  const choosable = item.status === "proposed" || item.status === "scheduled";
  const [lo, hi] = bounds(item);
  const night = String(setting("maintenance_tonight_time") ?? "02:00");
  const window = String(setting("maintenance_default_window") ?? "01:00-05:00").split("-")[0] ?? "01:00";
  const days = String(setting("maintenance_default_days") ?? DAYS.join(",")).split(",");
  const starts: Record<MaintenancePreset, number | null> = {
    tonight: nextAt(night, lo, hi),
    weekend: nextAt(night, lo, hi, [String(setting("maintenance_weekend_day") ?? "sat")]),
    default: nextAt(window, lo, hi, days),
  };
  return {
    ...item,
    choose_from: choosable ? iso(lo) : null,
    choose_until: choosable ? iso(hi) : null,
    presets: choosable
      ? (Object.entries(starts) as [MaintenancePreset, number | null][])
          .filter(([, at]) => at != null)
          .map(([id, at]) => ({ id, start: iso(at)!, end: iso(at! + item.estimated_minutes * MINUTE)! }))
      : [],
    default_start: item.status === "proposed" ? iso(starts.default) : null,
  };
}

function meta(client: boolean): MaintenanceMeta {
  const [start = "01:00", end = "05:00"] = String(setting("maintenance_default_window") ?? "01:00-05:00").split("-");
  const length = (((+end.slice(0, 2) * 60 + +end.slice(3)) - (+start.slice(0, 2) * 60 + +start.slice(3)) + 1440) % 1440) || 1440;
  return {
    timezone: zone(),
    lead_minutes: Number(setting("maintenance_lead_minutes") ?? 30),
    default_window: String(setting("maintenance_default_window") ?? "01:00-05:00"),
    default_days: String(setting("maintenance_default_days") ?? ""),
    tonight_time: String(setting("maintenance_tonight_time") ?? "02:00"),
    weekend_day: String(setting("maintenance_weekend_day") ?? "sat"),
    reminder_hours: String(setting("maintenance_reminder_hours") ?? "24,1").split(",").map(Number),
    client_choice: clientChoice(),
    default_minutes: length,
    default_start: start,
    may_choose: client ? clientChoice() : can("settings.manage"),
    ...(client ? { confirmations: false } : {}),
  };
}

export function seedMaintenance() {
  items.length = 0;
  const now = Date.now();
  const acme = membersOf("acme-retail").map((i) => ({ name: i.name, stage: i.stage, project: "acme-retail", held: null }));
  const stageRank: Record<string, number> = { development: 0, staging: 1, production: 2 };
  acme.sort((a, b) => (stageRank[a.stage] ?? 1) - (stageRank[b.stage] ?? 1) || a.name.localeCompare(b.name));
  const lastWeek = now - 6 * DAY;
  items.push(
    {
      id: "6f1c2a7e-3b9d-4c51-8e2a-1d7f0b9a4c33", kind: "odoo_images", scope: "project", targets: ["acme-retail"], version: null,
      title: { en: "Update the Odoo images", ar: "تحديث صور Odoo" },
      reason: {
        en: "A security fix in the Odoo 18.0 image. Each instance restarts once, for about a minute; production is backed up first.",
        ar: "إصلاح أمني في صورة Odoo 18.0. تُعاد تشغيل كل بيئة مرة واحدة لنحو دقيقة؛ ويُنسخ الإنتاج احتياطيًا أولًا.",
      },
      estimated_minutes: 30, earliest_start: iso(now + 2 * HOUR)!, deadline: iso(now + 6 * DAY)!, required: true,
      status: "proposed", window: null, choose_from: null, choose_until: null, presets: [],
      auto_at: iso(now + 40 * HOUR), default_start: null, instances: acme, approvals: [], steps: [], detail: null,
      received_at: iso(now - 8 * HOUR), started_at: null, finished_at: null,
    },
    {
      id: "0b8e5d21-7a64-4f0c-9d3e-5c2b1a8f7e10", kind: "restart", scope: "panel", targets: [], version: null,
      title: { en: "Restart", ar: "إعادة تشغيل" },
      reason: { en: "A kernel update on the server.", ar: "تحديث نواة النظام على الخادم." },
      estimated_minutes: 10, earliest_start: iso(lastWeek - DAY)!, deadline: iso(lastWeek + DAY)!, required: true,
      status: "done", window: { start: iso(lastWeek)!, end: iso(lastWeek + 10 * MINUTE)!, source: "default", chosen_by: null, chosen_at: iso(lastWeek - DAY) },
      choose_from: null, choose_until: null, presets: [], auto_at: null, default_start: null, instances: [], approvals: [],
      steps: [{ name: "Restarting the panel (instances keep serving)", status: "done" }], detail: "The panel restarted.",
      received_at: iso(lastWeek - 2 * DAY), started_at: iso(lastWeek), finished_at: iso(lastWeek + 2 * MINUTE),
    },
  );
}

function find(id: string): MaintenanceItem {
  const found = items.find((i) => i.id === id);
  if (!found) throw new Problem(404, "There is no such maintenance.");
  return found;
}

function startOf(item: MaintenanceItem, body: Record<string, unknown>): number {
  const fresh = refresh(item);
  if (body.preset) {
    const preset = fresh.presets.find((p) => p.id === body.preset);
    if (!preset) throw new Problem(422, "This choice does not fit before the deadline: choose a time.");
    return ms(preset.start);
  }
  const text = String(body.start ?? "");
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(text)) throw new Problem(400, "Give a start time, such as 2026-11-29T02:00.");
  const wall = Date.parse(`${text.slice(0, 16)}:00Z`);
  const at = wall - offset(wall, zone());
  const [lo, hi] = bounds(item);
  if (at < lo || at > hi) throw new Problem(422, "Choose a start between the earliest start and the deadline.");
  return at;
}

function choose(item: MaintenanceItem, at: number, who: string, source: "panel" | "client") {
  if (item.status !== "proposed" && item.status !== "scheduled") throw new Problem(409, "This maintenance can no longer be moved.");
  item.status = "scheduled";
  item.window = { start: iso(at)!, end: iso(at + item.estimated_minutes * MINUTE)!, source, chosen_by: who, chosen_at: iso(Date.now()) };
  item.auto_at = null;
  record(source === "client" ? CLIENT_ME : ME, "maintenance window chosen", { detail: `${item.title.en}: ${item.window.start}` });
}

const clientsItems = () =>
  items.filter((i) => i.instances.some((x) => x.project && projects.get(x.project)?.access.client?.clients.some((c) => c.id === CLIENT_ME)));

route("GET", "/maintenance", () => {
  if (session.role === "client") throw new Problem(403, "Client accounts use the client portal only.");
  return ok(items.map(refresh), meta(false) as unknown as Record<string, unknown>);
});

route("PUT", "/maintenance/:id/window", ({ params, body }) => {
  if (!can("settings.manage")) throw new Problem(403, "Your role cannot choose the time of maintenance.");
  const item = find(params.id!);
  choose(item, startOf(item, body), "Sara Nassar", "panel");
  return ok(refresh(item));
});

route("GET", "/client/maintenance", () => {
  if (session.role !== "client") throw new Problem(404, "Only clients have a client portal.");
  const shown = clientsItems().map((item) => {
    const mine = item.instances.filter((x) => x.project && projects.get(x.project)?.access.client?.clients.some((c) => c.id === CLIENT_ME));
    const ids = [...new Set(mine.map((x) => x.project!))];
    return { ...refresh(item), targets: [], instances: mine, projects: ids.map((id) => ({ id, name: projects.get(id)?.name ?? id })), held: ids.filter((id) => projects.get(id)?.access.client?.sw.state === "closed" || Object.values(projects.get(id)?.access.client?.branches ?? {}).some((b) => b.state === "closed")) };
  });
  return ok(shown, meta(true) as unknown as Record<string, unknown>);
});

route("PUT", "/client/maintenance/:id/window", ({ params, body }) => {
  const item = clientsItems().find((i) => i.id === params.id);
  if (!item || session.role !== "client") throw new Problem(404, "There is no such maintenance.");
  if (!clientChoice()) throw new Problem(403, "Your provider chooses the time of maintenance.");
  choose(item, startOf(item, body), "Maya Rahman", "client");
  item.approvals = [...new Set([...item.approvals, "acme-retail"])];
  return ok({ done: true });
});

route("POST", "/client/maintenance/:id/approve", ({ params, body }) => {
  const item = clientsItems().find((i) => i.id === params.id);
  if (!item || session.role !== "client") throw new Problem(404, "There is no such maintenance.");
  const project = String(body.project ?? "acme-retail");
  item.approvals = [...new Set([...item.approvals, project])];
  record(CLIENT_ME, "maintenance approved by the client", { detail: `${project}: ${item.title.en}` });
  return ok({ done: true });
});
