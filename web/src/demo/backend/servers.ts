// The demo's servers (the premium "servers" package: GET /api/v1/servers and the rest): the main server in Frankfurt
// and two more, in Dubai and Virginia, with a few instances each. A server added here gets a join code; a moment
// later its (simulated) agent joins and prints a fingerprint, which approving asks for, as on real servers. Moving an
// instance is a job that ends with the instance on the other server. While the package does not run (the plan lacks
// it), every instance is on the main server and the instances carry no server, as on a panel without it.

import type { Instance, ServerChoice, ServerReference } from "@/api/types";
import { publish } from "./bus";
import { announce, startJob } from "./jobs";
import { capacity, currentMetrics } from "./metrics";
import { tell } from "./notices";
import { ENTITLEMENTS, currentPlan, onPlanChange, premiumActive, refusal } from "./premium";
import { instances, ME, record, session, setting, settings } from "./state";
import { clamp, DAY, hash, HOUR, iso, MINUTE, noise, OPENED, Problem, round, SECOND } from "./util";

const GIB = 1024 ** 3;
export const MAIN = "main";

type Status = "waiting" | "joined" | "active" | "revoked";

interface DemoServer {
  id: string;
  name: string;
  region: string;
  status: Status;
  max_instances: number | null;
  created_at: number;
  created_by: string;
  joined_at: number | null;
  approved_at: number | null;
  approved_by: string | null;
  revoked_at: number | null;
  revoked_by: string | null;
  fingerprint: string | null;
  code_expires_at: number | null;
  facts: { hostname: string; os: string; architecture: string; cpus: number; memory_bytes: number; disk_bytes: number; disk_free_bytes: number; version: string } | null;
  /** how busy it is: its figures move around these */
  load: { cpu: number; memory: number; disk: number };
}

export const servers = new Map<string, DemoServer>();
/** where each instance runs (instances not listed run on the main server) */
export const placement = new Map<string, string>();

const VERSION = "3.0.0";
const SEEDED_PLACES: Record<string, string> = {
  "zahra-clinic-main": "srv-dxb7k2m9qa",
  "zahra-clinic-appointments": "srv-dxb7k2m9qa",
  "acme-retail-loyalty-points": "srv-iad3h8w1ze",
  "nile-logistics-upgrade-18": "srv-iad3h8w1ze",
};

function fingerprintOf(seed: string) {
  const text = Array.from({ length: 4 }, (_, i) => hash(`${seed}${i}`).toString(16).toUpperCase().padStart(8, "0").slice(0, 4)).join("");
  return text.match(/.{4}/g)!.join("-");
}

export function seedServers() {
  servers.clear();
  placement.clear();
  const made = (id: string, name: string, region: string, ago: number, values: Partial<DemoServer>): DemoServer => ({
    id,
    name,
    region,
    status: "active",
    max_instances: 8,
    created_at: OPENED - ago,
    created_by: "omar",
    joined_at: OPENED - ago + 6 * MINUTE,
    approved_at: OPENED - ago + 9 * MINUTE,
    approved_by: "omar",
    revoked_at: null,
    revoked_by: null,
    fingerprint: fingerprintOf(id),
    code_expires_at: null,
    facts: null,
    load: { cpu: 18, memory: 46, disk: 38 },
    ...values,
  });
  servers.set(
    "srv-dxb7k2m9qa",
    made("srv-dxb7k2m9qa", "Dubai", "me-central", 46 * DAY, {
      max_instances: 6,
      facts: { hostname: "odoo-dxb-1", os: "Ubuntu 24.04.3 LTS", architecture: "x86_64", cpus: 8, memory_bytes: 32 * GIB, disk_bytes: 320 * GIB, disk_free_bytes: 201 * GIB, version: VERSION },
      load: { cpu: 21, memory: 52, disk: 37 },
    }),
  );
  servers.set(
    "srv-iad3h8w1ze",
    made("srv-iad3h8w1ze", "Virginia", "us-east", 19 * DAY, {
      max_instances: 10,
      facts: { hostname: "odoo-iad-1", os: "Debian GNU/Linux 13", architecture: "aarch64", cpus: 4, memory_bytes: 16 * GIB, disk_bytes: 160 * GIB, disk_free_bytes: 118 * GIB, version: VERSION },
      load: { cpu: 9, memory: 38, disk: 26 },
    }),
  );
  for (const [name, server] of Object.entries(SEEDED_PLACES)) if (instances.has(name)) placement.set(name, server);
  applyPlacement();
}

export const serversActive = () => premiumActive("servers");
const mainName = () => String(setting("server_name") || "Main");
const mainRegion = () => String(setting("server_region") ?? "");
export const serverOf = (name: string) => placement.get(name) ?? MAIN;

export function reference(id: string): ServerReference {
  if (id === MAIN) return { id: MAIN, name: mainName(), region: mainRegion(), online: true };
  const found = servers.get(id);
  return { id, name: found?.name ?? id, region: found?.region ?? "", online: found?.status === "active" };
}

/** Each instance carries its server while the package runs, and none otherwise (the panel without it). */
export function applyPlacement() {
  const on = serversActive();
  for (const instance of instances.values()) {
    if (on) instance.server = reference(serverOf(instance.name));
    else delete instance.server;
  }
}

/** Where a new instance goes (POST /instances "server"): 402 for another server without the package. */
export function placeNew(instance: Instance, wanted: unknown) {
  const id = typeof wanted === "string" && wanted ? wanted : MAIN;
  if (!serversActive()) {
    if (id !== MAIN) throw new Problem(402, refusal("servers"));
    return;
  }
  if (id !== MAIN) {
    const found = servers.get(id);
    if (!found) throw new Problem(400, "There is no such server.");
    if (found.status !== "active") throw new Problem(409, `The server ${found.name} is not active: choose another one.`);
    if (roomOf(id).full) throw new Problem(409, `The server ${found.name} is full (${found.max_instances} instances). Choose another one, or give it more room.`);
    placement.set(instance.name, id);
  }
  instance.server = reference(id);
}

/** The servers a new instance may go to (GET /instance-plan "servers"): the main one first, then those with room. */
export function choices(): ServerChoice[] | undefined {
  if (!serversActive()) return undefined;
  const others = [...servers.values()].filter((s) => s.status === "active" && !roomOf(s.id).full);
  return [{ id: MAIN, name: mainName(), region: mainRegion() }, ...others.map((s) => ({ id: s.id, name: s.name, region: s.region }))];
}

export const forget = (name: string) => placement.delete(name);

const countOn = (id: string) => [...instances.keys()].filter((name) => serverOf(name) === id).length;

function roomOf(id: string) {
  const count = countOn(id);
  if (id === MAIN) {
    const estimate = capacity();
    return { instances: count, max_instances: null, comfortable: estimate.comfortable, running: estimate.running, headroom: estimate.headroom, full: false, warning: null };
  }
  const server = servers.get(id)!;
  const running = [...instances.values()].filter((i) => serverOf(i.name) === id && i.status === "online").length;
  const comfortable = server.facts ? Math.floor((server.facts.memory_bytes / GIB - 2) / 1.2) : null;
  const limit = server.max_instances;
  return {
    instances: count,
    max_instances: limit,
    comfortable,
    running,
    headroom: comfortable === null ? null : comfortable - running,
    full: limit !== null && count >= limit,
    warning: null,
  };
}

function figures(id: string) {
  if (id === MAIN) {
    const server = currentMetrics().server;
    return { cpu_percent: server.cpu_percent ?? null, memory_percent: server.memory_percent ?? null, disk_percent: server.disk_percent ?? null, load_1m: server.load_1m ?? null };
  }
  const server = servers.get(id)!;
  if (server.status !== "active") return { cpu_percent: null, memory_percent: null, disk_percent: null, load_1m: null };
  const seconds = Date.now() / 1000;
  const seed = hash(id);
  const cpu = clamp(server.load.cpu + noise(seed, seconds, 20) * 7, 1, 98);
  return {
    cpu_percent: round(cpu),
    memory_percent: round(clamp(server.load.memory + noise(seed + 1, seconds, 60) * 3, 5, 95)),
    disk_percent: round(server.load.disk),
    load_1m: round(((server.facts?.cpus ?? 4) * cpu) / 100, 2),
  };
}

/** A server as GET /servers lists it; `full` with the details for those who manage servers. */
export function serverResource(id: string, full = false): Record<string, unknown> {
  const main = id === MAIN;
  const server = main ? null : servers.get(id)!;
  const status: Status = main ? "active" : server!.status;
  const resource: Record<string, unknown> = {
    id,
    name: main ? mainName() : server!.name,
    region: main ? mainRegion() : server!.region,
    main,
    status,
    online: status === "active",
    last_seen_at: main ? null : status === "active" ? iso(Date.now() - 4 * SECOND) : iso(server!.joined_at),
    version: main ? VERSION : server!.facts?.version ?? null,
    instances: countOn(id),
    capacity: { max_instances: main ? null : server!.max_instances },
    room: roomOf(id),
    metrics: figures(id),
    created_at: iso(main ? OPENED - 120 * DAY : server!.created_at),
    links: { self: `/api/v1/servers/${id}` },
  };
  if (server?.status === "waiting") {
    resource.code_expires_at = iso(server.code_expires_at);
    resource.code_expired = (server.code_expires_at ?? 0) < Date.now();
  }
  if (full && session.role !== "developer" && session.role !== "viewer") {
    Object.assign(resource, {
      fingerprint: main ? null : server!.fingerprint,
      facts: main
        ? { hostname: "odoo-fra-1", os: "Ubuntu 24.04.3 LTS", architecture: "x86_64", cpus: 8, memory_bytes: 32 * GIB, disk_bytes: 480 * GIB, disk_free_bytes: 291 * GIB, version: VERSION }
        : (server!.facts ?? {}),
      created_by: main ? null : server!.created_by,
      joined_at: main ? null : iso(server!.joined_at),
      approved_at: main ? null : iso(server!.approved_at),
      approved_by: main ? null : server!.approved_by,
      revoked_at: main ? null : iso(server!.revoked_at),
      revoked_by: main ? null : server!.revoked_by,
      connected_since: main ? null : status === "active" ? iso(OPENED - 3 * HOUR) : null,
    });
  }
  return resource;
}

const counted = () => 1 + [...servers.values()].filter((s) => s.status === "joined" || s.status === "active").length;

function limitRefusal(adding = 1): string | null {
  const limit = ENTITLEMENTS[currentPlan()].max_servers;
  const have = counted();
  if (limit === null || have + adding <= limit) return null;
  const plan = currentPlan().charAt(0).toUpperCase() + currentPlan().slice(1);
  return `The ${plan} plan allows ${limit} ${limit === 1 ? "server" : "servers"}, and this panel has ${have} (the main server included). Remove a server, or get more servers. See the plans at https://sudoo.sh/pricing.`;
}

export function listServers() {
  const ids = [MAIN, ...[...servers.values()].sort((a, b) => a.created_at - b.created_at).map((s) => s.id)];
  return {
    data: ids.map((id) => serverResource(id)),
    meta: { limit: ENTITLEMENTS[currentPlan()].max_servers, counted: counted(), refusal: limitRefusal(), panel_url: "https://panel.example-integrator.com" },
  };
}

export function getServer(id: string) {
  if (id !== MAIN && !servers.has(id)) throw new Problem(404, "This server does not exist.");
  return serverResource(id, true);
}

const text = (value: unknown, field: string, max = 60) => {
  const found = typeof value === "string" ? value.split(/\s+/).join(" ").trim() : "";
  if (found.length > max) throw new Problem(400, `${field}: at most ${max} characters.`);
  return found;
};

function maxInstances(value: unknown): number | null {
  const given = (value as { max_instances?: unknown } | undefined)?.max_instances;
  if (given === null || given === undefined || given === "") return null;
  const number = Number(given);
  if (!Number.isInteger(number) || number < 1 || number > 500) throw new Problem(400, "capacity.max_instances: a whole number from 1 to 500, or nothing.");
  return number;
}

const newCode = (id: string) => `sdj1.${id}.${Array.from(crypto.getRandomValues(new Uint8Array(18)), (b) => b.toString(16).padStart(2, "0")).join("")}`;
const installCommand = (code: string) =>
  `curl -fsSL https://github.com/Sudoo-sh/sudoo.sh/releases/download/v${VERSION}/sudoo-install.sh | sudo bash -s -- agent --join '${code}' --version ${VERSION}`;

/** The new server's agent "joins" a while after the join code is made, as when someone ran the command. */
function joinLater(id: string) {
  setTimeout(() => {
    const server = servers.get(id);
    if (!server || server.status !== "waiting") return;
    server.status = "joined";
    server.joined_at = Date.now();
    server.code_expires_at = null;
    server.fingerprint = fingerprintOf(`${id}${Date.now()}`);
    server.facts = { hostname: `odoo-${server.name.toLowerCase().replace(/[^a-z0-9]+/g, "-").slice(0, 20)}`, os: "Ubuntu 24.04.3 LTS", architecture: "x86_64", cpus: 4, memory_bytes: 16 * GIB, disk_bytes: 160 * GIB, disk_free_bytes: 151 * GIB, version: VERSION };
    publish("reset", id, null);
    tell(`${server.name} joined`, `Its agent printed the fingerprint ${server.fingerprint}. Open the server and type it to approve it.`);
  }, 12 * SECOND);
}

export function addServer(body: Record<string, unknown>): Record<string, unknown> {
  const name = text(body.name, "name");
  if (!name) throw new Problem(400, "name: give the server a name.");
  if ([...servers.values()].some((s) => s.name.toLowerCase() === name.toLowerCase()) || name.toLowerCase() === mainName().toLowerCase())
    throw new Problem(409, `There is a server called ${name} already.`);
  const refused = limitRefusal();
  if (refused) throw new Problem(403, refused);
  const id = `srv-${Array.from(crypto.getRandomValues(new Uint8Array(10)), (b) => "abcdefghijklmnopqrstuvwxyz0123456789"[b % 36]).join("")}`;
  const server: DemoServer = {
    id,
    name,
    region: text(body.region, "region"),
    status: "waiting",
    max_instances: maxInstances(body.capacity),
    created_at: Date.now(),
    created_by: ME,
    joined_at: null,
    approved_at: null,
    approved_by: null,
    revoked_at: null,
    revoked_by: null,
    fingerprint: null,
    code_expires_at: Date.now() + DAY,
    facts: null,
    load: { cpu: 6, memory: 22, disk: 9 },
  };
  servers.set(id, server);
  record(ME, "server added", { detail: `${name} (${id})` });
  const code = newCode(id);
  joinLater(id);
  return { ...serverResource(id, true), join_code: code, install_command: installCommand(code) };
}

export function changeServer(id: string, body: Record<string, unknown>) {
  if (Object.keys(body).some((k) => !["name", "region", "capacity"].includes(k))) throw new Problem(400, "Send only name, region or capacity.");
  if (id === MAIN) {
    if ("capacity" in body) throw new Problem(400, "The main server's room is set by its capacity settings (Settings, Capacity).");
    for (const [field, key] of [["name", "server_name"], ["region", "server_region"]] as const) {
      if (!(field in body)) continue;
      const found = settings.find((s) => s.key === key);
      if (found) found.value = text(body[field], field, 40) || (field === "name" ? "Main" : "");
    }
    record(ME, "main server changed");
  } else {
    const server = servers.get(id);
    if (!server) throw new Problem(404, "This server does not exist.");
    if ("name" in body) server.name = text(body.name, "name") || server.name;
    if ("region" in body) server.region = text(body.region, "region");
    if ("capacity" in body) server.max_instances = maxInstances(body.capacity);
    record(ME, "server changed", { detail: `${server.name} (${id})` });
  }
  applyPlacement();
  return serverResource(id, true);
}

const other = (id: string) => {
  if (id === MAIN) throw new Problem(409, "The main server cannot be changed this way.");
  const server = servers.get(id);
  if (!server) throw new Problem(404, "This server does not exist.");
  return server;
};

export function newJoinCode(id: string) {
  const server = other(id);
  if (server.status !== "waiting") throw new Problem(409, "Only a server that has not joined yet gets a new join code.");
  server.code_expires_at = Date.now() + DAY;
  const code = newCode(id);
  joinLater(id);
  record(ME, "server join code created", { detail: `${server.name} (${id})` });
  return { ...serverResource(id, true), join_code: code, install_command: installCommand(code) };
}

export function approveServer(id: string, body: Record<string, unknown>) {
  const server = other(id);
  if (server.status !== "joined") throw new Problem(409, "Only a server that has joined and waits for approval can be approved.");
  const typed = String(body.fingerprint ?? "").replace(/[\s-]/g, "").toUpperCase();
  if (typed !== (server.fingerprint ?? "").replace(/-/g, ""))
    throw new Problem(400, "This is not the fingerprint of the key that joined. Type the fingerprint the server printed (sudoo-install agent status). If they differ, someone else may have used the join code: revoke this server and add it again.");
  server.status = "active";
  server.approved_at = Date.now();
  server.approved_by = ME;
  record(ME, "server approved", { detail: `${server.name} (${id}), key ${server.fingerprint}` });
  applyPlacement();
  return serverResource(id, true);
}

export function revokeServer(id: string, body: Record<string, unknown>) {
  if (id === MAIN) throw new Problem(409, "The main server cannot be revoked.");
  const server = other(id);
  if (String(body.confirm ?? "") !== server.name) throw new Problem(400, "Type the server's name exactly to confirm.");
  if (server.status !== "revoked") {
    server.status = "revoked";
    server.revoked_at = Date.now();
    server.revoked_by = ME;
    record(ME, "server revoked", { detail: `${server.name} (${id})` });
  }
  applyPlacement();
  for (const instance of instances.values()) if (serverOf(instance.name) === id) announce(instance);
  return serverResource(id, true);
}

export function removeServer(id: string, body: Record<string, unknown>) {
  if (id === MAIN) throw new Problem(409, "The main server cannot be removed.");
  const server = other(id);
  if (server.status !== "revoked" && server.status !== "waiting") throw new Problem(409, "Revoke the server first; then it can be removed from the list.");
  const left = [...instances.keys()].filter((name) => serverOf(name) === id);
  if (left.length && body.forget_instances !== true)
    throw new Problem(409, `${server.name} still reports ${left.join(", ")}. Move them first, or remove the server and forget them ("forget_instances": true).`);
  for (const name of left) {
    instances.delete(name);
    placement.delete(name);
    publish("instance.deleted", name, null);
  }
  servers.delete(id);
  record(ME, "server removed", { detail: `${server.name} (${id})${left.length ? ", instances forgotten" : ""}` });
}

/** POST /instances/<name>/move: a job; the instance runs on the other server when it ends. */
export function moveInstance(instance: Instance, body: Record<string, unknown>) {
  const target = typeof body.server === "string" ? body.server : "";
  const source = serverOf(instance.name);
  if (!target || target === source) throw new Problem(400, "Choose another server than the one the instance runs on.");
  if (target !== MAIN) {
    const found = servers.get(target);
    if (!found) throw new Problem(400, "There is no such server.");
    if (found.status !== "active") throw new Problem(409, `The server ${found.name} is not active: choose another one.`);
    if (roomOf(target).full) throw new Problem(409, `The server ${found.name} is full (${found.max_instances} instances). Choose another one, or give it more room.`);
  }
  if (instance.stage === "production" && body.confirm_production !== true)
    throw new Problem(400, `${instance.name} is a production instance: it is offline while it moves. Confirm ("confirm_production": true).`);
  if (instance.current_job) throw new Problem(409, `${instance.name} is busy with another job. Try again when it has finished.`);
  const from = reference(source).name;
  const to = reference(target).name;
  const before = instance.status;
  record(ME, "instance move started", { instance: instance.name, detail: `${from} -> ${to}` });
  return startJob({
    instance: instance.name,
    action: `Move to ${to}`,
    kind: "move",
    steps: [`Back up ${instance.name} on ${from}`, `Copy the backup to ${to}`, `Restore it on ${to}`, `Start it on ${to}`, `Check that it answers`, `Remove it from ${from}`],
    seconds: [2.4, 3.2, 2.8, 2, 1.4, 1.2],
    status: "busy",
    onDone: () => {
      if (target === MAIN) placement.delete(instance.name);
      else placement.set(instance.name, target);
      instance.status = before === "creating" ? "online" : before;
      applyPlacement();
      announce(instance);
    },
  });
}

// a plan without the package puts every instance back on the main server in the panel's eyes, and back again
onPlanChange(applyPlacement);
