// The demo's project status and notifications (src/sudoo/status, src/sudoo/notifications): 30 days of seeded checks
// for each production instance, its backups and the server (the same past on every visit), a few incidents, the
// public status page's switch, and notification channels kept in memory. A test notification is never sent anywhere.

import type { Channel, ChannelType, Notifications, NotificationEvent, ProjectStatus, PublicStatus, StatusDay, StatusTarget } from "@/api/status";
import { branchList, projects, type DemoProject } from "./projects";
import { instances, ME } from "./state";
import { DAY, hash, hex, HOUR, MINUTE, OPENED, Problem, seeded } from "./util";

const EVENTS: NotificationEvent[] = ["build_failed", "build_warning", "build_recovered", "stage_changed", "promoted", "production_down", "production_up", "backup_failed", "restore_done", "storage_over"];
const DEFAULT_EVENTS: NotificationEvent[] = ["build_failed", "build_recovered", "promoted", "production_down", "production_up", "backup_failed", "storage_over"];
const TYPES: ChannelType[] = ["slack", "discord", "teams", "telegram", "webhook", "email"];
const LIMIT = 10;
const now = () => Math.floor(Date.now() / 1000);

/** Uptime rounded down, as the server does (99.9996 % is never 100 %). */
export const floorPercent = (up: number, total: number) => (total > 0 ? Math.floor((up / total) * 100_000) / 1000 : null);

function productionOf(project: DemoProject): { name: string; branch: string }[] {
  const found: { name: string; branch: string }[] = [];
  for (const branch of branchList(project))
    for (const name of branch.instances) {
      const instance = instances.get(name);
      if (instance?.stage === "production" && instance.status !== "creating") found.push({ name, branch: branch.name });
    }
  return found;
}

/** 30 seeded days of one target: minutes down per day, and the average response time. */
function history(target: string): { days: StatusDay[]; downMinutes: number[] } {
  const random = seeded(hash(target));
  const today = Math.floor(now() / 86400) * 86400;
  const days: StatusDay[] = [];
  const downMinutes: number[] = [];
  for (let offset = 29; offset >= 0; offset--) {
    const roll = random();
    const down = roll > 0.96 ? Math.round(5 + random() * 30) : roll > 0.92 ? Math.round(1 + random() * 2) : 0;
    const checked = offset === 0 ? Math.max(1, Math.floor((now() - today) / 60)) : 1440;
    downMinutes.push(down);
    days.push({
      day: today - offset * 86400,
      uptime: floorPercent(checked - Math.min(down, checked), checked),
      avg_ms: target.startsWith("instance:") ? Math.round(180 + random() * 140 + (down ? 260 : 0)) : null,
      degraded: roll > 0.8 ? Math.round(random() * 6) : 0,
    });
  }
  return { days, downMinutes };
}

function window(days: StatusDay[], count: number): number | null {
  const chosen = days.slice(-count).filter((d) => d.uptime != null);
  if (!chosen.length) return null;
  return Math.floor((chosen.reduce((sum, d) => sum + d.uptime!, 0) / chosen.length) * 1000) / 1000;
}

function target(id: string, kind: StatusTarget["kind"], name: string, branch: string | null): StatusTarget {
  const { days } = history(id);
  const instance = kind === "instance" ? instances.get(name) : undefined;
  const asleep = instance && ["paused", "switched_off", "stopped"].includes(instance.status);
  const state = asleep ? "down" : kind === "backups" && hash(id) % 5 === 0 ? "degraded" : "up";
  return {
    id,
    kind,
    name,
    branch,
    state,
    since: now() - (state === "up" ? 3 * DAY / 1000 + (hash(id) % 50000) : 40 * 60),
    checked_at: now() - 20,
    detail: kind === "instance" ? (asleep ? "Switched off" : "HTTP 200") : kind === "backups" ? (state === "up" ? "Latest backup 9 h ago" : "Latest backup 27 h ago: the daily backup is late") : "Measured",
    response_ms: kind === "instance" && !asleep ? 160 + (hash(id) % 120) : null,
    uptime: { day: days.at(-1)?.uptime ?? null, week: window(days, 7), month: window(days, 30) },
    days,
    hourly_ms: kind === "instance" ? Array.from({ length: 24 }, (_, i) => 170 + ((hash(id) + i * 37) % 90)) : null,
  };
}

// kept for the visit (sessionStorage), so the link still opens after the page is reloaded or the link followed
const PAGES_KEY = "sudoo.demo.status-pages";
const pages = new Map<string, { enabled: boolean; token: string }>(
  (() => {
    try {
      return Object.entries(JSON.parse(sessionStorage.getItem(PAGES_KEY) ?? "{}") as Record<string, { enabled: boolean; token: string }>);
    } catch {
      return [];
    }
  })(),
);
const savePages = () => {
  try {
    sessionStorage.setItem(PAGES_KEY, JSON.stringify(Object.fromEntries(pages)));
  } catch {
    // private window: the page lasts until the next reload
  }
};

function publicView(projectId: string) {
  const page = pages.get(projectId);
  return { enabled: !!page?.enabled, allowed: true, path: page?.enabled ? `/status/${page.token}` : null };
}

export function projectStatus(project: DemoProject, mayManage: boolean): ProjectStatus {
  const targets: StatusTarget[] = [];
  for (const { name, branch } of productionOf(project)) {
    targets.push(target(`instance:${name}`, "instance", name, branch));
    targets.push(target(`backups:${name}`, "backups", name, branch));
  }
  targets.push(target("server", "server", "server", null));
  const incidents = targets.flatMap((found) => {
    const { days, downMinutes } = history(found.id);
    return days
      .map((d, i) => ({ d, down: downMinutes[i]! }))
      .filter(({ down }) => down > 0)
      .map(({ d, down }) => {
        const start = d.day + 3600 + (hash(`${found.id}${d.day}`) % 70000);
        return {
          id: hex(12),
          target: found.id,
          kind: found.kind,
          name: found.name,
          start,
          end: start + down * 60,
          duration: down * 60,
          severity: down > 4 ? ("down" as const) : ("degraded" as const),
          what: found.kind === "instance" ? (down > 4 ? "HTTP 502" : "Slow answer: 3400 ms") : found.kind === "backups" ? "The daily backup is late" : "Memory high: 91% used",
        };
      });
  });
  const essential = targets.filter((t) => t.kind === "instance");
  const state = essential.some((t) => t.state === "down") ? "down" : targets.some((t) => t.state !== "up") ? "degraded" : "operational";
  return {
    project: project.id,
    state,
    checks: true,
    interval_seconds: 60,
    slow_ms: 3000,
    production: essential.length > 0,
    targets,
    incidents: incidents.sort((a, b) => b.start - a.start).slice(0, 100),
    public: mayManage ? publicView(project.id) : null,
  };
}

export function changePublic(project: DemoProject, body: Record<string, unknown>) {
  const enabled = body.enabled === true;
  const current = pages.get(project.id);
  const token = !current || body.new_link === true ? `${hex(22)}${hex(21)}` : current.token;
  pages.set(project.id, { enabled, token });
  savePages();
  return publicView(project.id);
}

export function publicStatus(token: string, find: (id: string) => DemoProject | undefined): PublicStatus {
  const entry = [...pages.entries()].find(([, page]) => page.enabled && page.token === token);
  const project = entry ? find(entry[0]) : undefined;
  if (!project) throw new Problem(404, "There is no such status page.");
  const full = projectStatus(project, false);
  return { name: project.name, state: full.state, targets: full.targets.map(({ kind, name, state, uptime, days }) => ({ kind, name, state, uptime, days: days.map((d) => ({ day: d.day, uptime: d.uptime })) })), at: now() };
}

// ---------------------------------------------------------------- notifications

const channels = new Map<string, Channel>();
let seededChannels = false;

/** Three channels on the first project, made the first time anyone looks. */
function seedChannels() {
  if (seededChannels) return;
  seededChannels = true;
  const first = [...projects.keys()][0];
  if (!first) return;
  const at = Math.floor((OPENED - 6 * DAY) / 1000);
  const slack: Channel = {
    id: hex(12), project: first, type: "slack", name: "#ops", events: [...DEFAULT_EVENTS], enabled: true, config: {},
    secrets: { url: "…Qx7d" }, created_at: at, created_by: ME,
    last: { at: Math.floor((OPENED - 3 * HOUR) / 1000), ok: true, result: "HTTP 200", event: "build_failed", attempts: 1 },
  };
  const mail: Channel = {
    id: hex(12), project: first, type: "email", name: "On-call mailbox", events: ["production_down", "production_up", "backup_failed"], enabled: true,
    config: { addresses: ["oncall@example.com"] }, secrets: {}, created_at: at, created_by: ME, last: null,
  };
  const hook: Channel = {
    id: hex(12), project: first, type: "webhook", name: "Incident tracker", events: ["production_down", "production_up"], enabled: false, config: {},
    secrets: { url: "…/inc", signing_secret: "…k2Pa" }, created_at: at, created_by: ME,
    last: { at: Math.floor((OPENED - 26 * MINUTE) / 1000), ok: false, result: "answered HTTP 503", event: "production_down", attempts: 2, retry_at: Math.floor((OPENED + 4 * MINUTE) / 1000) },
  };
  for (const channel of [slack, mail, hook]) channels.set(channel.id, channel);
}

export function notificationsOf(projectId: string): Notifications {
  seedChannels();
  return {
    project: projectId,
    enabled: true,
    channels: [...channels.values()].filter((c) => c.project === projectId),
    events: EVENTS,
    default_events: DEFAULT_EVENTS,
    types: TYPES,
    limit: LIMIT,
  };
}

function cleanEvents(value: unknown): NotificationEvent[] {
  if (!Array.isArray(value)) throw new Problem(400, `Choose the events as a list: ${EVENTS.join(", ")}.`);
  const unknown = value.find((e) => !EVENTS.includes(e as NotificationEvent));
  if (unknown !== undefined) throw new Problem(400, `Unknown event: ${String(unknown).slice(0, 40)}.`);
  return EVENTS.filter((e) => value.includes(e));
}

const hint = (value: string) => (value.length > 8 ? `…${value.slice(-4)}` : "…");

function secretsFrom(type: ChannelType, given: Record<string, unknown>, current: Record<string, string | null>) {
  const found = { ...current };
  if (["slack", "discord", "teams", "webhook"].includes(type) && typeof given.url === "string" && given.url.trim()) {
    if (!/^https:\/\/[^/@\s]+/.test(given.url.trim())) throw new Problem(400, "The address must start with https://.");
    found.url = hint(given.url.trim());
  }
  if (type === "telegram" && typeof given.bot_token === "string" && given.bot_token.trim()) {
    if (!/^\d{3,15}:[A-Za-z0-9_-]{30,64}$/.test(given.bot_token.trim())) throw new Problem(400, "The bot token looks like 123456789:AA… (from @BotFather).");
    found.bot_token = hint(given.bot_token.trim());
  }
  return found;
}

function configFrom(type: ChannelType, given: Record<string, unknown>, current: Channel["config"]): Channel["config"] {
  const config = { ...current };
  if (type === "telegram" && "chat_id" in given) {
    const chat = String(given.chat_id ?? "").trim();
    if (!/^(-?\d{1,20}|@[A-Za-z][A-Za-z0-9_]{4,31})$/.test(chat)) throw new Problem(400, "The chat id is a number (such as -1001234567890) or a channel's @name.");
    config.chat_id = chat;
  }
  if (type === "email" && "addresses" in given) {
    const list = Array.isArray(given.addresses) ? given.addresses.map(String) : [];
    const bad = list.find((a) => !/^[^@\s,]+@[^@\s,]+\.[A-Za-z]{2,}$/.test(a));
    if (bad) throw new Problem(400, `${bad.slice(0, 80)} is not an e-mail address.`);
    if (!list.length) throw new Problem(400, "Add at least one e-mail address.");
    config.addresses = list.slice(0, 20);
  }
  return config;
}

export function addChannel(projectId: string, body: Record<string, unknown>): Channel {
  const type = body.type as ChannelType;
  if (!TYPES.includes(type)) throw new Problem(400, `Choose the kind of channel: ${TYPES.join(", ")}.`);
  if (notificationsOf(projectId).channels.length >= LIMIT) throw new Problem(400, `A project may have ${LIMIT} channels.`);
  const secrets = secretsFrom(type, (body.secrets ?? {}) as Record<string, unknown>, {});
  if (["slack", "discord", "teams", "webhook"].includes(type) && !secrets.url) throw new Problem(400, "Add the channel's address.");
  if (type === "telegram" && !secrets.bot_token) throw new Problem(400, "Add the bot token.");
  const config = configFrom(type, (body.config ?? {}) as Record<string, unknown>, {});
  if (type === "telegram" && !config.chat_id) throw new Problem(400, "Add the chat id the bot writes to.");
  if (type === "email" && !config.addresses?.length) throw new Problem(400, "Add at least one e-mail address.");
  let signing: string | undefined;
  if (type === "webhook") {
    signing = `whsec_${hex(43)}`;
    secrets.signing_secret = hint(signing);
  }
  const channel: Channel = {
    id: hex(12), project: projectId, type, name: String(body.name ?? "").trim().slice(0, 60) || type[0]!.toUpperCase() + type.slice(1),
    events: "events" in body ? cleanEvents(body.events) : [...DEFAULT_EVENTS], enabled: true, config, secrets,
    created_at: now(), created_by: ME, last: null,
  };
  channels.set(channel.id, channel);
  return signing ? { ...channel, signing_secret: signing } : channel;
}

export function channelOf(projectId: string, id: string): Channel {
  seedChannels();
  const found = channels.get(id);
  if (!found || found.project !== projectId) throw new Problem(404, "This channel does not exist.");
  return found;
}

export function changeChannel(channel: Channel, body: Record<string, unknown>): Channel {
  const next: Channel = { ...channel };
  if ("name" in body) next.name = String(body.name ?? "").trim().slice(0, 60) || channel.name;
  if ("events" in body) next.events = cleanEvents(body.events);
  if ("enabled" in body) next.enabled = body.enabled === true;
  if ("config" in body) next.config = configFrom(channel.type, (body.config ?? {}) as Record<string, unknown>, channel.config);
  if (body.secrets && typeof body.secrets === "object") next.secrets = secretsFrom(channel.type, body.secrets as Record<string, unknown>, channel.secrets);
  channels.set(next.id, next);
  return next;
}

export function removeChannel(id: string) {
  channels.delete(id);
}

export function testChannel(channel: Channel) {
  const last = { at: now(), ok: true, result: channel.type === "email" ? "e-mailed to 1 of 1 (the demo sends nothing)" : "HTTP 200 (the demo sends nothing)", event: "test", attempts: 1, test: true };
  const next = { ...channel, last };
  channels.set(channel.id, next);
  return { channel: next, delivery: last };
}

export function newSigningSecret(channel: Channel): Channel {
  if (channel.type !== "webhook") throw new Problem(400, "Only a webhook has a signing secret.");
  const signing = `whsec_${hex(43)}`;
  const next = { ...channel, secrets: { ...channel.secrets, signing_secret: hint(signing) } };
  channels.set(channel.id, next);
  return { ...next, signing_secret: signing };
}
