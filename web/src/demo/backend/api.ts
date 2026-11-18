// The demo's /api/v1: a router over method and path that answers like the real backend (src/sudoo/web/v1): the
// same envelopes ({"data", "meta"}), 202 + the job for background work, 204 for deletions, RFC 9457 problem details
// for errors, and the same permission checks per role. It changes the in-memory state and starts simulated jobs.

import type { Account, Backup, CatalogPlugin, Instance, Job, Permission, Session, Stage, User } from "@/api/types";
import { publish } from "./bus";
import { docPage, DOCS } from "./docs";
import { announce, full, reference, startJob } from "./jobs";
import { capacity, currentMetrics, history, listed, newProfile } from "./metrics";
import * as mail from "./mail";
import { catalog, categories, marketplace, recommendations } from "./marketplace";
import {
  addBackup,
  alerts,
  ASLEEP,
  audit,
  backups,
  commitsOf,
  ageText,
  CREATE_STEPS,
  deployKeys,
  findRepo,
  github,
  instances,
  instanceUrl,
  jobs,
  logs,
  ME,
  OWNER,
  PERMISSIONS,
  plugins,
  profiles,
  record,
  REPOS,
  repoUrl,
  roleInfo,
  ROLES,
  session,
  setting,
  settings,
  tokens,
  UPDATE_STEPS,
  users,
} from "./state";
import { DAY, hash, hex, HOUR, iso, OPENED, Problem, slug } from "./util";

export interface Answer {
  status: number;
  body?: unknown;
  headers?: Record<string, string>;
}

interface Request {
  method: string;
  params: Record<string, string>;
  query: URLSearchParams;
  body: Record<string, unknown>;
}

type Handler = (request: Request) => Answer | Promise<Answer>;

const routes: { method: string; pattern: RegExp; names: string[]; handler: Handler }[] = [];

function route(method: string, path: string, handler: Handler) {
  const names: string[] = [];
  const pattern = new RegExp(
    "^" +
      path.replace(/\/:(\*?)(\w+)/g, (_, rest: string, name: string) => {
        names.push(name);
        return rest ? "/(.+)" : "/([^/]+)";
      }) +
      "$",
  );
  routes.push({ method, pattern, names, handler });
}

const ok = (data: unknown, meta?: Record<string, unknown>, status = 200): Answer => ({ status, body: { data, ...(meta ? { meta } : {}) } });
const created = (data: unknown) => ok(data, undefined, 201);
const none = (): Answer => ({ status: 204 });
const accepted = (job: Job): Answer => ({ status: 202, body: { data: job }, headers: { Location: `/api/v1/jobs/${job.id}` } });

const CONTACT = "Please contact the administrator, Operations team (ops@example.com).";
const can = (permission: Permission) => roleInfo(session.role).permissions.includes(permission);
function need(permission: Permission, path = "") {
  if (!can(permission)) {
    record(ME, `refused: ${path}`, { ok: false });
    throw new Problem(403, `Your role cannot do this (${PERMISSIONS[permission].toLowerCase()}). ${CONTACT}`);
  }
}

/** Things that would leave the demo or touch the real world are refused with a short explanation. */
const offline = (what: string) => new Problem(409, `${what} is not available in the demo: nothing here is real. Install Sudoo.sh on your own server to try it.`);

// ---------------------------------------------------------------- session and account

export function me(): Session {
  const user = users.find((u) => u.name === ME)!;
  const role = roleInfo(session.role);
  return {
    user: ME,
    display_name: user.display_name,
    first_name: user.first_name,
    last_name: user.last_name,
    email: user.email,
    phone: user.phone,
    role: role.name,
    role_label: role.label,
    permissions: [...role.permissions].sort(),
    csrf_token: session.csrf,
    restricted: null,
    must_set_up_second_factor: false,
    recovery_codes_needed: users.find((u) => u.name === ME)!.second_factors.includes("totp") && recovery.left === 0,
    brand_color: String(setting("brand_color") ?? "#714B67"),
  };
}

/** The banner's role switcher: the signed-in account takes another role, as an administrator would give it. */
export function switchRole(role: Session["role"]) {
  session.role = role;
  const user = users.find((u) => u.name === ME)!;
  user.role = role;
  user.role_label = roleInfo(role).label;
}

function signedIn() {
  if (!session.signedIn) throw new Problem(401, "Your session has ended. Please sign in again.");
}

/** The visitor's recovery codes (made 41 days ago with the app; three used since) and sign-in e-mail choice. */
const recovery = { left: 7, total: 10, created_at: iso(OPENED - 41 * DAY) };
let signInEmail: boolean | null = null;
const RECOVERY_ALPHABET = "23456789abcdefghjkmnpqrstuvwxyz";
const newRecoveryCodes = () => {
  const codes = Array.from({ length: 10 }, () => {
    const raw = Array.from({ length: 10 }, () => RECOVERY_ALPHABET[Math.floor(Math.random() * RECOVERY_ALPHABET.length)]).join("");
    return `${raw.slice(0, 5)}-${raw.slice(5)}`;
  });
  Object.assign(recovery, { left: codes.length, total: codes.length, created_at: iso(Date.now()) });
  return codes;
};

const factors = () => {
  const user = users.find((u) => u.name === ME)!;
  return [
    { name: "totp", label: "Authenticator app", enrolled: user.second_factors.includes("totp"), added_at: iso(OPENED - 41 * DAY) },
    { name: "email", label: "E-mail", enrolled: user.second_factors.includes("email"), added_at: user.second_factors.includes("email") ? iso(OPENED - 10 * DAY) : null },
  ];
};

function account(): Account {
  const user = users.find((u) => u.name === ME)!;
  return {
    name: user.name,
    display_name: user.display_name,
    first_name: user.first_name,
    last_name: user.last_name,
    email: user.email,
    phone: user.phone,
    role: user.role,
    role_label: user.role_label,
    password_managed_here: true,
    second_factors: factors(),
    second_factor_required: ["super_admin", "admin"].includes(user.role),
    require_second_factor: "admins",
    recovery_codes: user.second_factors.includes("totp") ? { ...recovery } : null,
    recovery_codes_needed: user.second_factors.includes("totp") && recovery.left === 0,
    sign_in_email: {
      enabled: signInEmail ?? Boolean(setting("sign_in_email") ?? true),
      own_choice: signInEmail,
      default: Boolean(setting("sign_in_email") ?? true),
      can_turn_off: Boolean(setting("sign_in_email_opt_out") ?? true),
      mail_ready: true,
    },
  };
}

route("GET", "/setup", () => ok({ needed: false, contact: CONTACT }));
route("GET", "/contact", () => ok({ sentence: CONTACT, name: "Operations team", phone: "", email: "ops@example.com" }));
route("POST", "/setup", () => {
  throw new Problem(409, "This panel is set up already.");
});
route("GET", "/session", () => {
  signedIn();
  return ok(me());
});
route("POST", "/session", ({ body }) => {
  if (!String(body.username ?? "").trim() || !String(body.password ?? "")) throw new Problem(400, "Enter your e-mail address and password.");
  session.signedIn = true;
  remember();
  record(ME, "signed in");
  return created(me());
});
route("POST", "/session/second-factor", () => {
  session.signedIn = true;
  remember();
  return created(me());
});
route("POST", "/session/second-factor/:method", () => ok({ sent_to: "s***@example.com" }));
route("DELETE", "/session", () => {
  session.signedIn = false;
  remember();
  return none();
});
route("PUT", "/session/password", () => {
  signedIn();
  record(ME, "password changed");
  return none();
});
route("GET", "/account", () => {
  signedIn();
  return ok(account());
});
route("PATCH", "/account", ({ body }) => {
  signedIn();
  const user = users.find((u) => u.name === ME)!;
  if ("sign_in_email" in body) {
    const choice = body.sign_in_email;
    if (choice === false && !setting("sign_in_email_opt_out")) throw new Problem(403, `The sign-in e-mail cannot be turned off on this panel. ${CONTACT}`);
    signInEmail = typeof choice === "boolean" ? choice : null;
    record(ME, `sign-in e-mail ${choice ? "turned on" : choice === false ? "turned off" : "back to the default"}`);
  }
  for (const key of ["first_name", "last_name", "email", "phone"] as const) {
    if (key in body) user[key] = String(body[key] ?? "") || null;
  }
  user.display_name = [user.first_name, user.last_name].filter(Boolean).join(" ") || user.name;
  return ok(account());
});
route("POST", "/account/second-factors/:name", ({ params, body }) => {
  if (params.name === "totp") return ok({ secret: "DEMODEMODEMODEMO", otpauth_uri: "otpauth://totp/Sudoo.sh%20demo:sara%40example.com?secret=DEMODEMODEMODEMO&issuer=Sudoo.sh%20demo" });
  return ok({ sent_to: String(body.email ?? "sara@example.com") });
});
route("PUT", "/account/second-factors/:name", ({ params }) => {
  const user = users.find((u) => u.name === ME)!;
  const added = !user.second_factors.includes(params.name!);
  if (added) user.second_factors.push(params.name!);
  if (params.name === "totp" && added) return ok(factors(), { recovery_codes: newRecoveryCodes() });
  return ok(factors());
});
route("POST", "/account/recovery-codes", ({ body }) => {
  signedIn();
  if (!users.find((u) => u.name === ME)!.second_factors.includes("totp")) throw new Problem(400, "Recovery codes are for the authenticator app: set it up first.");
  if (!String(body.password ?? "")) throw new Problem(400, "The password is not correct.");
  const codes = newRecoveryCodes();
  record(ME, "recovery codes made", { detail: `${codes.length} codes, the earlier ones no longer work` });
  return created({ codes, ...recovery });
});
route("DELETE", "/account/sessions", () => {
  signedIn();
  record(ME, "signed out every other session");
  return none();
});
route("DELETE", "/account/second-factors/:name", ({ params }) => {
  const user = users.find((u) => u.name === ME)!;
  user.second_factors = user.second_factors.filter((f) => f !== params.name);
  if (params.name === "totp") recovery.left = 0;
  return none();
});

// ---------------------------------------------------------------- instances

function instanceOf(name: string): Instance {
  const instance = instances.get(name);
  if (!instance) throw new Problem(404, "This instance does not exist.");
  return instance;
}

route("GET", "/instances", ({ query }) => {
  need("instances.view", "/api/v1/instances");
  const list = [...instances.values()].sort((a, b) => a.name.localeCompare(b.name)).map(listed);
  const limit = Number(query.get("limit") ?? 100);
  return ok(list.slice(0, limit), { total: list.length, count: Math.min(limit, list.length), limit, offset: 0 });
});
route("GET", "/instances/:name", ({ params }) => {
  need("instances.view");
  return ok(full(instanceOf(params.name!)));
});
route("GET", "/instances/:name/credentials", ({ params }) => {
  need("instances.credentials", `/api/v1/instances/${params.name}/credentials`);
  const instance = instanceOf(params.name!);
  record(ME, "credentials viewed", { instance: instance.name });
  const n = hash(instance.name);
  return ok({ login: "admin", admin_password: `demo-${n.toString(36)}-not-real`, master_password: `demo-master-${(n >>> 7).toString(36)}`, ports: { gateway: 8069 + (n % 40), odoo: 28069 + (n % 40) } });
});
route("GET", "/instances/:name/logs", ({ params, query }) => {
  need("instances.logs");
  const instance = instanceOf(params.name!);
  return ok({ text: odooLog(instance, Math.min(Number(query.get("lines") ?? 300), 400)) });
});
route("GET", "/instances/:name/metrics", ({ params }) => {
  need("instances.view");
  instanceOf(params.name!);
  return ok({ latest: currentMetrics().instances[params.name!], history: history().instances[params.name!] });
});

function odooLog(instance: Instance, lines: number): string {
  if (ASLEEP.includes(instance.status)) return "";
  const db = instance.odoo.database;
  const paths = ["/web", "/web/dataset/call_kw/sale.order/web_search_read", "/web/action/load", "/pos/web", "/shop", "/web/dataset/call_kw/stock.picking/web_read", "/longpolling/poll", "/web/image/res.partner/7/avatar_128", "/my/orders", "/web/webclient/translations"];
  const out: string[] = [];
  let t = Date.now() - lines * 2500;
  for (let i = 0; i < lines; i++) {
    const n = hash(`${instance.name}${i}`);
    const stamp = new Date(t).toISOString().replace("T", " ").slice(0, 23).replace(".", ",");
    const path = paths[n % paths.length]!;
    if (n % 37 === 0) out.push(`${stamp} 1 INFO ${db} odoo.addons.base.models.ir_cron: Job 'Mail: Email Queue Manager' (${n % 20}) done in 0.0${n % 9}s`);
    else if (n % 53 === 0) out.push(`${stamp} 1 WARNING ${db} odoo.http: Generating nondeterministic report for invoice INV/2026/0${n % 900}`);
    else out.push(`${stamp} 1 INFO ${db} werkzeug: 198.51.100.${n % 200} - - "POST ${path} HTTP/1.1" 200 - ${n % 40} 0.0${n % 90} 0.${n % 300}`);
    t += 1000 + (n % 3000);
  }
  return out.join("\n") + "\n";
}

const DEFAULTS = (stage: Stage) => ({
  delete_after_days: stage === "production" ? 0 : stage === "staging" ? Number(setting("staging_delete_after_days")) : Number(setting("delete_after_days")),
  auto_update: stage === "production" ? Boolean(setting("production_auto_update")) : Boolean(setting("auto_update")),
  auto_upgrade: Boolean(setting("auto_upgrade")),
  auto_pause: stage === "production" ? false : Boolean(setting("auto_pause")),
});

function productionInfo() {
  const limit = Number(setting("max_production_instances") ?? 0);
  const names = [...instances.values()].filter((i) => i.stage === "production").map((i) => i.name);
  return {
    limit,
    instances: names,
    refusal: limit && names.length >= limit ? `At most ${limit} production instances: ${names.join(", ")} ${names.length === 1 ? "is" : "are"} in production already. Move one out of production first, or raise the limit in Settings.` : null,
  };
}

function planName(repo: string, branch: string, custom: string, version: string, edition: string) {
  let base: string;
  if (custom) base = slug(custom);
  else if (!repo && version) base = ["odoo", slug(version), slug(edition), hex(4)].join("-");
  else base = [slug(findRepo(repo)?.name ?? repo.split("/").pop()?.replace(/\.git$/, "") ?? ""), slug(branch)].filter(Boolean).join("-");
  base = base.slice(0, 28).replace(/-+$/, "") || "odoo";
  let name = base;
  for (let n = 2; instances.has(name); n++) name = `${base.slice(0, 26).replace(/-+$/, "")}-${n}`;
  return name;
}

route("GET", "/instance-plan", ({ query }) => {
  need("instances.create");
  const stage = (query.get("stage") as Stage) || "development";
  const name = planName(query.get("repo") ?? "", query.get("branch") ?? "", query.get("name") ?? "", query.get("version") ?? "", query.get("edition") ?? "");
  return ok({
    name,
    database: name.replace(/-/g, "_"),
    login: "admin",
    defaults: DEFAULTS(stage),
    stage_defaults: { development: DEFAULTS("development"), staging: DEFAULTS("staging"), production: DEFAULTS("production") },
    production: productionInfo(),
  });
});
route("GET", "/instance-quota", () => {
  need("instances.view");
  return ok({ instances: instances.size, max_instances: null, own_instances: [...instances.values()].filter((i) => i.created_by === ME).length, max_instances_per_user: null, cooldown_minutes: null, next_creation_at: null });
});
route("GET", "/versions", () => ok(["17.0", "18.0", "19.0"].map((version) => ({ version, enterprise: true }))));

route("POST", "/instances", ({ body }) => {
  need("instances.create", "/api/v1/instances");
  const repo = String(body.repo ?? "").trim();
  const branch = String(body.branch ?? "main") || "main";
  const version = String(body.version ?? "19.0");
  const edition = body.edition === "enterprise" ? "enterprise" : "community";
  const stage = (["production", "staging", "development"].includes(String(body.stage)) ? body.stage : "development") as Stage;
  const custom = String(body.name ?? "").trim();
  if (custom && !/^[a-z][a-z0-9-]{1,39}$/.test(custom)) throw new Problem(400, "A name has 2 to 40 lower-case letters, digits and dashes, and starts with a letter.");
  if (custom && instances.has(custom)) throw new Problem(409, `An instance named ${custom} exists already.`);
  if (stage === "production" && productionInfo().refusal) throw new Problem(409, productionInfo().refusal!);
  const fromBackup = body.from_backup ? backups.find((b) => b.file === body.from_backup) : null;
  if (body.from_backup && !fromBackup) throw new Problem(404, "This backup does not exist.");
  const name = custom || planName(repo, branch, "", version, edition);
  const known = findRepo(repo);
  const defaults = DEFAULTS(stage);
  const now = Date.now();
  const deleteAfter = stage === "production" ? 0 : Number(body.delete_after_days ?? defaults.delete_after_days);
  const instance: Instance = {
    name,
    status: "creating",
    stage,
    url: null,
    odoo: { version, edition, database: name.replace(/-/g, "_") },
    source: repo ? "repository" : "plain",
    git: { repository: repo ? (known ? repoUrl(known.name) : repo) : null, branch: repo ? branch : null, branch_deleted_on_github: false, commit: null },
    settings: {
      auto_update: !!repo && Boolean(body.auto_update ?? defaults.auto_update),
      auto_upgrade: !!repo && defaults.auto_upgrade,
      auto_pause: stage !== "production" && Boolean(body.auto_pause ?? defaults.auto_pause),
      delete_after_days: deleteAfter,
    },
    usage: { cpu_percent: 0, memory_bytes: 0 },
    uptime: null,
    gateway_error: null,
    drift: null,
    update_failure: null,
    created_at: iso(now),
    last_visit_at: null,
    pauses_at: null,
    expires_at: null,
    current_job: null,
    created_by: ME,
    disk: { database_bytes: null, files_bytes: null },
    recent_commits: [],
    submodules: [],
    rollback: null,
  };
  instances.set(name, instance);
  profiles.set(name, newProfile(name, stage === "production"));
  let steps = CREATE_STEPS(instance, body.install_modules !== false);
  if (fromBackup) {
    const at = steps.findIndex((s) => s.startsWith("Creating the Odoo"));
    steps = [...steps.slice(0, at), `Loading the backup of ${fromBackup.instance} into database ${instance.odoo.database}`, ...(fromBackup.instance !== name ? ["Neutralising the copied data"] : []), ...steps.slice(at + 1)];
  }
  const job = startJob({
    instance: name,
    action: "Create instance",
    kind: "create",
    steps,
    seconds: steps.map((s) => (s.startsWith("Creating the Odoo") || s.startsWith("Loading the backup") ? 4.5 : s.startsWith("Installing") ? 3.5 : 2)),
    status: "creating",
    notes: fromBackup && fromBackup.instance !== name ? [{ text: `The backup of ${fromBackup.instance} was neutralised: outgoing mail, scheduled actions and integrations are off.`, level: "info" }] : [],
    onDone: () => {
      const commits = known ? commitsOf(known, known.branches.includes(branch) ? branch : known.defaultBranch) : [];
      const head = commits[0];
      instance.status = "online";
      instance.url = instanceUrl(name);
      instance.git.commit = head ? { sha: head.sha, subject: head.subject, author: head.author, age: ageText(Date.parse(head.when)) } : repo ? { sha: hex(7), subject: "Initial commit", author: "you", age: "1 minute ago" } : null;
      instance.recent_commits = commits;
      instance.last_visit_at = iso(Date.now());
      instance.pauses_at = instance.settings.auto_pause ? iso(Date.now() + Number(setting("idle_hours")) * HOUR) : null;
      instance.expires_at = deleteAfter ? iso(now + deleteAfter * DAY) : null;
      instance.disk = { database_bytes: 96 * 1024 ** 2, files_bytes: 12 * 1024 ** 2 };
      profiles.get(name)!.since = Date.now();
    },
  });
  instance.current_job = { ...reference(job), steps: [] };
  announce(instance);
  return { status: 202, body: { data: job }, headers: { Location: `/api/v1/jobs/${job.id}`, "Content-Location": `/api/v1/instances/${name}` } };
});

route("PATCH", "/instances/:name", ({ params, body }) => {
  need("instances.operate");
  const instance = instanceOf(params.name!);
  if (instance.status === "creating") throw new Problem(409, "This instance is still being created. Try again when its creation has finished.");
  const s = instance.settings;
  if (body.stage && body.stage !== instance.stage) {
    const stage = body.stage as Stage;
    if (instance.stage === "production" && !body.confirm_production) throw new Problem(400, "Moving an instance out of production needs \"confirm_production\": true.");
    if (stage === "production") {
      const refusal = productionInfo().refusal;
      if (refusal) throw new Problem(409, refusal);
      s.delete_after_days = 0;
      s.auto_pause = false;
      instance.expires_at = null;
      instance.pauses_at = null;
    }
    record(ME, "instance stage changed", { instance: instance.name, detail: `${instance.stage} -> ${stage}` });
    instance.stage = stage;
  }
  for (const key of ["auto_update", "auto_upgrade", "auto_pause"] as const) {
    if (typeof body[key] === "boolean") {
      if (key === "auto_pause" && body[key] && instance.stage === "production") throw new Problem(409, "A production instance is never paused automatically.");
      if (key !== "auto_pause" && body[key] && instance.source === "plain") throw new Problem(409, `${instance.name} runs plain Odoo without a GitHub repository: it has no code to update. Connect a repository to it first.`);
      s[key] = body[key] as boolean;
    }
  }
  if (typeof body.delete_after_days === "number") {
    if (body.delete_after_days && instance.stage === "production") throw new Problem(409, "A production instance is never deleted automatically.");
    s.delete_after_days = body.delete_after_days;
    instance.expires_at = s.delete_after_days ? iso(Date.parse(instance.created_at!) + s.delete_after_days * DAY) : null;
  }
  instance.pauses_at = s.auto_pause && instance.status === "online" ? iso(Date.now() + Number(setting("idle_hours")) * HOUR) : null;
  record(ME, "instance settings changed", { instance: instance.name, detail: Object.keys(body).filter((k) => k !== "confirm_production").map((k) => `${k}: ${String(body[k])}`).join(", ") });
  announce(instance);
  return ok(full(instance));
});

const ACTIONS: Record<string, [title: string, kind: string, permission: Permission]> = {
  update: ["Get latest code", "update", "instances.update"],
  "update-submodules": ["Update submodules", "update", "instances.update"],
  restart: ["Restart", "restart", "instances.update"],
  start: ["Start", "wake", "instances.update"],
  "add-submodule": ["Add submodule", "submodule", "instances.update"],
  pause: ["Pause", "pause", "instances.operate"],
  "switch-off": ["Switch off", "stop", "instances.operate"],
  upgrade: ["Upgrade modules", "upgrade", "instances.operate"],
  "remove-submodule": ["Remove submodule", "submodule", "instances.operate"],
  "switch-branch": ["Switch branch", "update", "instances.operate"],
  "connect-repository": ["Connect a repository", "update", "instances.operate"],
  backup: ["Back up", "backup", "instances.operate"],
  "update-images": ["Update Odoo and PostgreSQL images", "update", "instances.operate"],
  repair: ["Restore the panel's configuration", "repair", "instances.operate"],
  restore: ["Restore a backup", "restore", "instances.restore"],
  rollback: ["Roll back the restore", "restore", "instances.restore"],
  delete: ["Delete instance", "delete", "instances.delete"],
};
const REPOSITORY_ACTIONS = ["update", "update-submodules", "switch-branch", "add-submodule", "remove-submodule"];
const PRODUCTION_CONFIRMED = ["pause", "switch-off", "restore", "rollback"];

function newCommit(instance: Instance) {
  const repo = findRepo(instance.git.repository);
  const pool = repo?.commits ?? [["Small fixes", "Karim"]];
  const [subject, author] = pool[Math.floor(Math.random() * pool.length)]!;
  const commit = { sha: hex(7), subject: subject.startsWith("Merge") ? subject : `${subject} (follow-up)`, author, when: iso(Date.now())! };
  instance.recent_commits = [commit, ...(instance.recent_commits ?? [])].slice(0, 10);
  instance.git.commit = { sha: commit.sha, subject: commit.subject, author, age: "just now" };
}

function wake(instance: Instance) {
  instance.status = "online";
  instance.last_visit_at = iso(Date.now());
  instance.pauses_at = instance.settings.auto_pause ? iso(Date.now() + Number(setting("idle_hours")) * HOUR) : null;
  const profile = profiles.get(instance.name)!;
  if (!Number.isFinite(profile.since) || profile.since > Date.now()) profile.since = Date.now();
}

function sleep(instance: Instance, status: "paused" | "switched_off") {
  instance.status = status;
  instance.pauses_at = null;
  profiles.get(instance.name)!.since = Number.POSITIVE_INFINITY;
}

function runAction(instance: Instance, action: string, body: Record<string, unknown>): Job {
  const [title, kind, permission] = ACTIONS[action] ?? [];
  if (!title) throw new Problem(404, `Unknown action ${action}.`);
  need(permission!, `/api/v1/instances/${instance.name}/actions/${action}`);
  if (instance.status === "creating") throw new Problem(409, "This instance is still being created. Try again when its creation has finished.");
  if (instance.current_job) throw new Problem(409, `${instance.name} is busy with "${instance.current_job.action}". Try again when it has finished.`);
  if (instance.source === "plain" && REPOSITORY_ACTIONS.includes(action))
    throw new Problem(409, `${instance.name} runs plain Odoo without a GitHub repository: it has no code to update, no branch and no submodules. Connect a repository to it first.`);
  if (instance.stage === "production" && PRODUCTION_CONFIRMED.includes(action) && !body.confirm_production)
    throw new Problem(400, `${instance.name} is a production instance: confirm with "confirm_production": true.`);
  const was = ASLEEP.includes(instance.status) ? instance.status : "online";
  const base = { instance: instance.name, action: title, kind: kind! };
  switch (action) {
    case "restart":
      return startJob({ ...base, steps: ["Restarting Odoo", "Waiting for Odoo to be ready"], seconds: [2.5, 2], status: "starting", onDone: () => wake(instance) });
    case "start": {
      if (capacity().blocked) throw new Problem(403, "This server runs as many instances as it may. Pause one first.");
      return startJob({ ...base, steps: ["Starting the database", "Starting Odoo", "Waiting for Odoo to be ready"], status: "starting", onDone: () => wake(instance) });
    }
    case "pause":
      return startJob({ ...base, steps: ["Pausing to free server resources"], seconds: 2.5, status: "busy", onDone: () => sleep(instance, "paused") });
    case "switch-off":
      return startJob({ ...base, steps: ["Switching off"], seconds: 2.5, status: "busy", onDone: () => sleep(instance, "switched_off") });
    case "update":
    case "update-submodules":
      return startJob({
        ...base,
        steps: action === "update" ? UPDATE_STEPS : ["Backing up the database and files before the update", "Moving the submodules to their latest version", "Upgrading modules: changed modules", "Restarting Odoo"],
        status: "busy",
        onStart: () => addBackup(instance, "before-update", Date.now()),
        onDone: () => {
          newCommit(instance);
          instance.update_failure = null;
          wake(instance);
          if (was !== "online") sleep(instance, was as "paused");
        },
      });
    case "upgrade": {
      const modules = String(body.modules ?? "all") || "all";
      return startJob({ ...base, steps: [`Upgrading modules: ${modules}`, "Restarting Odoo"], seconds: [4, 2], status: "busy", onDone: () => wake(instance) });
    }
    case "switch-branch": {
      const branch = String(body.branch ?? "").trim();
      if (!branch) throw new Problem(400, "Choose a branch.");
      return startJob({
        ...base,
        steps: [`Switching the code to branch ${branch}`, "Upgrading modules: changed modules", "Restarting Odoo"],
        status: "busy",
        context: { ...contextFor(instance), branch },
        onDone: () => {
          instance.git.branch = branch;
          instance.git.branch_deleted_on_github = false;
          const repo = findRepo(instance.git.repository);
          if (repo) instance.recent_commits = commitsOf(repo, branch);
          newCommit(instance);
          wake(instance);
        },
      });
    }
    case "connect-repository": {
      const repo = String(body.repo ?? "").trim();
      if (!repo) throw new Problem(400, "Enter the repository's address.");
      const branch = String(body.branch ?? "main") || "main";
      return startJob({
        ...base,
        steps: [`Downloading the code of ${repo.replace(/^https:\/\/github\.com\//, "")} (${branch}) from GitHub`, "Putting the code into the instance", ...(body.install_modules ? ["Installing the repository's modules (2)"] : []), "Restarting Odoo"],
        status: "busy",
        onDone: () => {
          instance.source = "repository";
          instance.git.repository = repo;
          instance.git.branch = branch;
          const known = findRepo(repo);
          instance.recent_commits = known ? commitsOf(known, branch) : [];
          newCommit(instance);
          instance.settings.auto_update = true;
          instance.settings.auto_upgrade = true;
          wake(instance);
        },
      });
    }
    case "add-submodule": {
      const url = String(body.url ?? "").trim();
      if (!/^https:\/\/github\.com\/[\w.-]+\/[\w.-]+/.test(url)) throw new Problem(400, "Enter a GitHub address, for example https://github.com/OCA/web.");
      const path = String(body.path ?? "").trim() || `oca/${url.split("/").pop()!.replace(/\.git$/, "").toLowerCase()}`;
      return startJob({
        ...base,
        steps: [`Adding submodule ${path}`, "Saving the change to GitHub", "Restarting Odoo"],
        status: "busy",
        onDone: () => {
          instance.submodules = [...(instance.submodules ?? []).filter((s) => s.path !== path), { path, url, sha: hex(7) }];
          wake(instance);
        },
      });
    }
    case "remove-submodule": {
      const path = String(body.path ?? "");
      return startJob({
        ...base,
        steps: [`Removing submodule ${path}`, "Saving the change to GitHub", "Restarting Odoo"],
        status: "busy",
        onDone: () => {
          instance.submodules = (instance.submodules ?? []).filter((s) => s.path !== path);
          wake(instance);
        },
      });
    }
    case "backup":
      return startJob({ ...base, steps: ["Backing up the database and files"], seconds: 4, status: instance.status, onDone: () => addBackup(instance, "manual", Date.now()) });
    case "update-images":
      return startJob({ ...base, steps: ["Downloading the images", "Starting Odoo with the new images"], seconds: [4, 2.5], status: "busy", onDone: () => wake(instance) });
    case "repair":
      return startJob({ ...base, steps: ["Comparing the instance with the panel's configuration", "Putting the panel's configuration back"], status: "busy", onDone: () => { instance.drift = null; wake(instance); } });
    case "restore": {
      const file = body.file ? String(body.file) : full(instance).restorable_backup;
      const backup = backups.find((b) => b.file === file);
      if (!backup) throw new Problem(body.file ? 404 : 409, body.file ? "This backup does not exist." : "There is no backup of this instance to restore.");
      if (backup.instance !== instance.name) need("backups.manage");
      if (backup.series !== instance.odoo.version) throw new Problem(400, `This backup is of Odoo ${backup.series}; ${instance.name} runs Odoo ${instance.odoo.version}.`);
      if (backup.edition === "enterprise" && instance.odoo.edition !== "enterprise") throw new Problem(400, "An Enterprise backup only goes into an Enterprise instance.");
      const foreign = backup.instance !== instance.name;
      const taken = new Date(backup.created * 1000).toUTCString().slice(5, 22);
      return startJob({
        ...base,
        steps: ["Saving the current state first", "Stopping Odoo", `Restoring the backup of ${taken}` + (foreign ? ` from ${backup.instance}` : ""), ...(foreign ? ["Neutralising the copied data", "Setting the admin password"] : []), "Starting Odoo"],
        seconds: 2.4,
        status: "busy",
        notes: foreign ? [{ text: `The backup of ${backup.instance} was neutralised: outgoing mail, scheduled actions and integrations are off.`, level: "info" }] : [],
        onDone: () => {
          const now = Date.now();
          const before = addBackup(instance, "before-restore", now);
          for (const old of backups.filter((b) => b.instance === instance.name && b.kind === "before-restore" && b.file !== before.file)) backups.splice(backups.indexOf(old), 1);
          instance.rollback = { backups: [before.file], taken_at: iso(now)!, until: iso(now + Number(setting("rollback_hours")) * HOUR)! };
          wake(instance);
        },
      });
    }
    case "rollback": {
      if (!instance.rollback) throw new Problem(409, "There is nothing to roll back to: no restore in the last hours.");
      return startJob({
        ...base,
        steps: ["Saving the current state first", "Stopping Odoo", "Rolling back to the state before the restore", "Starting Odoo"],
        status: "busy",
        onDone: () => {
          addBackup(instance, "before-rollback", Date.now());
          instance.rollback = null;
          wake(instance);
        },
      });
    }
    case "delete":
      return deleteInstance(instance, body);
  }
  throw new Problem(404, `Unknown action ${action}.`);
}

const contextFor = (instance: Instance) => ({ name: instance.name, database: instance.odoo.database, version: instance.odoo.version, edition: instance.odoo.edition, repository: instance.git.repository, branch: instance.git.branch });

function deleteInstance(instance: Instance, body: Record<string, unknown>): Job {
  need("instances.delete", `/api/v1/instances/${instance.name}`);
  if (body.without_backup) need("backups.manage");
  if (body.confirm !== instance.name) throw new Problem(400, `Type the instance's name (${instance.name}) to confirm.`);
  if (instance.stage === "production" && body.confirm_stage !== "production") throw new Problem(400, 'This is a production instance: confirm with "confirm_stage": "production" as well.');
  if (instance.current_job) throw new Problem(409, `${instance.name} is busy with "${instance.current_job.action}". Try again when it has finished.`);
  return startJob({
    instance: instance.name,
    action: "Delete instance",
    kind: "delete",
    steps: ["Backing up the database and files", "Removing Odoo, its database and its files", "Removing the instance folder"],
    seconds: [3, 2, 1],
    status: "busy",
    onDone: () => {
      addBackup(instance, "deletion", Date.now());
      for (const old of backups.filter((b) => b.instance === instance.name && b.kind !== "manual" && b.kind !== "deletion")) backups.splice(backups.indexOf(old), 1);
      instances.delete(instance.name);
      profiles.delete(instance.name);
      publish("instance.deleted", instance.name, { name: instance.name });
    },
  });
}

route("POST", "/instances/:name/actions/:action", ({ params, body }) => accepted(runAction(instanceOf(params.name!), params.action!, body)));
route("DELETE", "/instances/:name", ({ params, body }) => accepted(deleteInstance(instanceOf(params.name!), body)));

// ---------------------------------------------------------------- jobs

route("GET", "/jobs", ({ query }) => {
  need("instances.view");
  const name = query.get("instance");
  const limit = Number(query.get("limit") ?? 30);
  const list = jobs.filter((j) => !name || j.instance === name);
  return ok(list.slice(0, limit), { total: list.length, count: Math.min(limit, list.length), limit, offset: 0 });
});
route("GET", "/jobs/:id", ({ params }) => {
  need("instances.view");
  const job = jobs.find((j) => j.id === params.id);
  if (!job) throw new Problem(404, "This job does not exist.");
  return ok(job);
});
route("GET", "/jobs/:id/log", ({ params, query }) => {
  need("instances.logs");
  const text = logs.get(params.id!) ?? "";
  const lines = text.split("\n");
  const offset = Number(query.get("offset") ?? 0);
  const total = lines.length - 1;
  return ok({ text: lines.slice(offset, total).map((l) => `${l}\n`).join("") }, { next_offset: total });
});

// ---------------------------------------------------------------- server, metrics, settings, alerts

route("GET", "/server", () => {
  need("instances.view");
  return ok({ version: "1.0.0", panel_url: "https://panel.demo.sudoo.sh", plugins: { exposure: "cloudflare-quick", enterprise_source: "folder", update_triggers: ["polling", "github-app"] }, metrics: currentMetrics().server, live_clients: 1 });
});
route("GET", "/server/capacity", () => ok(capacity()));
route("POST", "/server/restart", () => {
  need("settings.manage", "/api/v1/server/restart");
  if (jobs.some((j) => j.status === "running" || j.status === "queued")) throw new Problem(409, "Jobs are running. Restart the panel when they have finished.");
  for (const s of settings) s.pending_restart = false;
  record(ME, "panel restarted");
  return { status: 202, body: { data: { restarting: true } } };
});
route("GET", "/metrics", () => {
  need("instances.view");
  return ok(currentMetrics());
});
route("GET", "/metrics/history", ({ query }) => {
  need("instances.view");
  return ok(history(Number(query.get("minutes") ?? 60)), { minutes: 60, interval_seconds: 3 });
});
route("GET", "/settings", () => {
  need("settings.manage", "/api/v1/settings");
  return ok(settings);
});
route("PATCH", "/settings", ({ body }) => {
  need("settings.manage", "/api/v1/settings");
  for (const [key, value] of Object.entries(body)) {
    const s = settings.find((x) => x.key === key);
    if (!s) throw new Problem(400, `${key.toUpperCase()}: unknown setting.`);
    if (!s.editable) throw new Problem(400, `${key.toUpperCase()} can only be set in .env.`);
    if (s.minimum != null && typeof value === "number" && value < s.minimum) throw new Problem(400, `${key.toUpperCase()}: Input should be greater than or equal to ${s.minimum}.`);
    if (value === null) {
      s.value = s.env_value;
      s.changed_in_panel = false;
    } else {
      s.value = s.secret ? value !== "" : (value as typeof s.value);
      s.changed_in_panel = true;
    }
    if (s.applies === "restart") s.pending_restart = s.value !== s.env_value;
  }
  record(ME, "settings changed", { detail: Object.entries(body).map(([k, v]) => `${k.toUpperCase()}=${settings.find((s) => s.key === k)?.secret ? "(set)" : v === null ? "(env)" : String(v)}`).join(", ") });
  publish("settings", "", { changed: Object.keys(body) });
  return ok(settings);
});
route("DELETE", "/settings/:key", ({ params }) => {
  need("settings.manage");
  const s = settings.find((x) => x.key === params.key);
  if (s) {
    s.value = s.env_value;
    s.changed_in_panel = false;
    s.pending_restart = false;
  }
  publish("settings", "", { changed: [params.key] });
  return none();
});
route("POST", "/settings/test-email", ({ body }) => {
  need("settings.manage");
  return ok({ sent_to: String(body.to ?? "") });
});
route("GET", "/mail-templates", () => {
  need("mail.manage", "/api/v1/mail-templates");
  return ok(mail.all(), { mail_ready: true });
});
const mailOf = (key: string) => {
  need("mail.manage", `/api/v1/mail-templates/${key}`);
  const found = mail.find(key);
  if (!found) throw new Problem(404, "No e-mail with this name.");
  return found;
};
const mailText = (key: string, body: Record<string, unknown>) => {
  const current = mail.resource(mailOf(key));
  const part = (name: "subject" | "title" | "body") => (typeof body[name] === "string" ? String(body[name]).trim() : current[name]);
  return { subject: part("subject"), title: part("title"), body: part("body") };
};
route("GET", "/mail-templates/:key", ({ params }) => ok(mail.resource(mailOf(params.key!))));
route("PUT", "/mail-templates/:key", ({ params, body }) => {
  const message = mailOf(params.key!);
  const text = mailText(params.key!, body);
  const problems = mail.problems(message, text);
  if (problems.length) throw new Problem(400, problems.join(" "));
  mail.save(message, text, ME);
  record(ME, "e-mail template changed", { detail: message.name });
  return ok(mail.resource(message));
});
route("DELETE", "/mail-templates/:key", ({ params }) => {
  const message = mailOf(params.key!);
  if (mail.reset(message.key)) record(ME, "e-mail template reset to default", { detail: message.name });
  return none();
});
route("POST", "/mail-templates/:key/preview", ({ params, body }) =>
  ok(mail.preview(mailOf(params.key!), mailText(params.key!, body), String(setting("brand_color") ?? "#714B67"))),
);
route("POST", "/mail-templates/:key/test", ({ params, body }) => {
  const message = mailOf(params.key!);
  const problems = mail.problems(message, mailText(params.key!, body));
  if (problems.length) throw new Problem(400, problems.join(" "));
  throw offline("Sending e-mail");
});
route("GET", "/alerts", ({ query }) => {
  need("audit.view", "/api/v1/alerts");
  return ok([...alerts].sort((a, b) => b.last_at - a.last_at).slice(0, Number(query.get("limit") ?? 50)));
});
route("POST", "/alerts/test", ({ body }) => {
  need("settings.manage");
  const alert = { id: hex(12), key: "test", type: "test" as const, severity: "info" as const, title: "Test alert", text: "This is a test alert from the Sudoo.sh demo.", hint: null, details: [], instance: null, link: "/settings?tab=alerts", at: Date.now() / 1000, last_at: Date.now() / 1000, count: 1, resolved_at: null, delivery: [] as string[] };
  alerts.unshift(alert);
  publish("alert", alert.key, alert);
  return body.channel === "webhook" ? ok({ alert, webhook: body.webhook, status: 200 }) : ok({ alert, sent_to: ["ops@example.com", "omar@example.com"] });
});

// ---------------------------------------------------------------- accounts, roles, audit

route("GET", "/roles", () => ok({ roles: ROLES, permissions: PERMISSIONS }));
route("GET", "/users", () => {
  need("accounts.manage", "/api/v1/users");
  return ok(users);
});
const superAdmins = () => users.filter((u) => u.role === "super_admin");
function userOf(name: string): User {
  const user = users.find((u) => u.name === name);
  if (!user) throw new Problem(404, "This account does not exist.");
  if (user.role === "super_admin" && session.role !== "super_admin") throw new Problem(403, "Only a super admin changes another super admin.");
  return user;
}
const generated = () => `demo ${["green", "quiet", "early", "silver"][Math.floor(Math.random() * 4)]} ${["harbour", "lantern", "meadow", "comet"][Math.floor(Math.random() * 4)]} ${Math.floor(Math.random() * 90 + 10)}`;
route("POST", "/users", ({ body }) => {
  need("accounts.manage", "/api/v1/users");
  const email = String(body.email ?? "").trim().toLowerCase();
  if (!email) throw new Problem(400, "Enter the person's e-mail address: they sign in with it.");
  if (users.some((u) => u.email?.toLowerCase() === email)) throw new Problem(400, "Another account already uses this e-mail address.");
  const role = String(body.role ?? "viewer") as User["role"];
  if (role === "super_admin" && session.role !== "super_admin") throw new Problem(403, "Only a super admin creates a super admin.");
  let name = String(body.name ?? "") || slug(email.split("@")[0]!).replace(/-/g, ".") || "user";
  for (let n = 2; users.some((u) => u.name === name); n++) name = `${name.replace(/-\d+$/, "")}-${n}`;
  const first = String(body.first_name ?? "") || null;
  const last = String(body.last_name ?? "") || null;
  const user: User = { name, display_name: [first, last].filter(Boolean).join(" ") || name, first_name: first, last_name: last, email, phone: String(body.phone ?? "") || null, role, role_label: roleInfo(role).label, last_sign_in_at: null, password_changed_at: iso(Date.now()), must_change_password: body.password ? body.must_change_password !== false : true, second_factors: [] };
  users.push(user);
  record(ME, "account created", { detail: `${name} (${role})` });
  return created({ ...user, password: String(body.password ?? "") || generated() });
});
route("PATCH", "/users/:name", ({ params, body }) => {
  need("accounts.manage");
  const user = userOf(params.name!);
  if (body.role && body.role !== user.role) {
    if (user.role === "super_admin" && superAdmins().length === 1) throw new Problem(400, "The last super admin must stay a super admin.");
    if (body.role === "super_admin" && session.role !== "super_admin") throw new Problem(403, "Only a super admin makes someone a super admin.");
    user.role = body.role as User["role"];
    user.role_label = roleInfo(user.role).label;
    if (user.name === ME) session.role = user.role;
  }
  for (const key of ["first_name", "last_name", "email", "phone"] as const) if (key in body) user[key] = String(body[key] ?? "") || null;
  user.display_name = [user.first_name, user.last_name].filter(Boolean).join(" ") || user.name;
  record(ME, "account changed", { detail: user.name });
  return ok(user);
});
route("POST", "/users/:name/password", ({ params, body }) => {
  need("accounts.manage");
  const user = userOf(params.name!);
  if (user.name === ME) throw new Problem(400, "Change your own password on My account.");
  user.must_change_password = body.password ? body.must_change_password !== false : true;
  user.password_changed_at = iso(Date.now());
  return ok({ password: String(body.password ?? "") || generated() });
});
route("DELETE", "/users/:name/second-factors", ({ params }) => {
  need("accounts.manage");
  userOf(params.name!).second_factors = [];
  return none();
});
route("DELETE", "/users/:name", ({ params }) => {
  need("accounts.manage");
  const user = userOf(params.name!);
  if (user.name === ME) throw new Problem(400, "You cannot delete your own account.");
  if (user.role === "super_admin" && superAdmins().length === 1) throw new Problem(400, "The last super admin cannot be deleted.");
  users.splice(users.indexOf(user), 1);
  record(ME, "account deleted", { detail: user.name });
  return none();
});
route("GET", "/audit", ({ query }) => {
  need("audit.view", "/api/v1/audit");
  return ok(audit.slice(0, Number(query.get("limit") ?? 200)));
});

// ---------------------------------------------------------------- tokens, backups, GitHub

route("GET", "/tokens", () => {
  need("tokens.manage", "/api/v1/tokens");
  return ok(tokens);
});
route("POST", "/tokens", ({ body }) => {
  need("tokens.manage");
  const name = String(body.name ?? "").trim();
  if (!name) throw new Problem(400, "Give the token a name that says what uses it.");
  const token = { id: `t${hex(7)}`, name, scope: (body.scope as "read") ?? "read", created: Date.now() / 1000, created_by: ME, last_used: null };
  tokens.push(token);
  record(ME, "API token created", { detail: `${name} (${token.scope})` });
  return created({ ...token, token: `sudoo_demo_${hex(24)}`, signing_secret: `demo_${hex(32)}` });
});
route("DELETE", "/tokens/:id", ({ params }) => {
  need("tokens.manage");
  const index = tokens.findIndex((t) => t.id === params.id);
  if (index >= 0) tokens.splice(index, 1);
  return none();
});
route("GET", "/backups", () => {
  need("backups.manage", "/api/v1/backups");
  return ok(backups);
});
route("GET", "/backups/:file", () => {
  throw offline("Downloading a backup");
});
route("DELETE", "/backups/:file", ({ params }) => {
  need("backups.manage");
  const index = backups.findIndex((b: Backup) => b.file === params.file);
  if (index < 0) throw new Problem(404, "This backup does not exist.");
  backups.splice(index, 1);
  record(ME, "backup deleted", { detail: params.file });
  return none();
});
route("GET", "/deploy-keys", () => {
  need("github.manage", "/api/v1/deploy-keys");
  return ok(deployKeys);
});
route("POST", "/deploy-keys", ({ body }) => {
  need("github.manage");
  const repo = String(body.repo ?? "").replace(/^https:\/\/github\.com\//, "").replace(/\.git$/, "").trim();
  if (!/^[\w.-]+\/[\w.-]+$/.test(repo)) throw new Problem(400, "Enter the repository as owner/name or its GitHub address.");
  const key = { repo, spelling: repo, public_key: `ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI${hex(32)} sudoo-demo`, created: Date.now() / 1000, github_url: `https://github.com/${repo}/settings/keys/new` };
  deployKeys.push(key);
  github.deploy_keys = deployKeys.length;
  return created(key);
});
route("DELETE", "/deploy-keys/:*repo", ({ params }) => {
  need("github.manage");
  const index = deployKeys.findIndex((k) => k.repo === params.repo);
  if (index >= 0) deployKeys.splice(index, 1);
  github.deploy_keys = deployKeys.length;
  return none();
});
route("GET", "/github/app", () => {
  need("github.manage", "/api/v1/github/app");
  return ok(github, { can_manage: can("settings.manage") });
});
route("POST", "/github/app/manifest", () => {
  need("settings.manage");
  throw offline("Connecting a GitHub App (it opens GitHub)");
});
route("POST", "/github/app", () => {
  throw offline("Connecting a GitHub App");
});
route("DELETE", "/github/app", () => {
  need("settings.manage");
  const app = github.app;
  github.app = null;
  github.connected = false;
  return ok({ settings_url: app?.settings_url ?? "", installations: (app?.installations ?? []).map((i) => ({ account: i.account, settings_url: i.settings_url })) });
});
route("POST", "/github/app/installations", () => {
  throw offline("Installing the GitHub App");
});
route("POST", "/github/app/installations/sync", () => {
  need("settings.manage");
  return ok(github.app?.installations ?? []);
});
route("DELETE", "/github/app/installations/:id", ({ params }) => {
  need("settings.manage");
  if (github.app) github.app.installations = github.app.installations.filter((i) => String(i.id) !== params.id);
  return none();
});
route("GET", "/github/repositories", ({ query }) => {
  need("instances.create");
  const words = (query.get("q") ?? "").toLowerCase().split(/\s+/).filter(Boolean);
  const list = REPOS.filter((r) => words.every((w) => `${OWNER}/${r.name} ${r.description}`.toLowerCase().includes(w))).map((r) => ({
    full_name: `${OWNER}/${r.name}`,
    url: repoUrl(r.name),
    private: true,
    default_branch: r.defaultBranch,
    description: r.description,
    pushed_at: iso(OPENED - r.pushedAgo),
    account: OWNER,
  }));
  return ok(github.connected ? list : [], { connected: github.connected, installations: (github.app?.installations ?? []).map((i) => ({ account: i.account, settings_url: i.settings_url })) });
});
route("GET", "/repositories/branches", ({ query }) => {
  need("instances.view");
  const url = query.get("url") ?? "";
  const repo = findRepo(url);
  if (repo) return ok({ branches: repo.branches, default: repo.defaultBranch });
  if (/github\.com[/:][\w.-]+\/[\w.-]+/.test(url)) return ok({ branches: ["main", "develop"], default: "main" });
  throw new Problem(400, "Enter a GitHub repository address, for example https://github.com/your-company/odoo-addons.");
});
route("GET", "/repositories/access", ({ query }) => {
  need("instances.view");
  const url = query.get("url") ?? "";
  const submodules = findRepo(url) ? [{ url: "https://github.com/OCA/web", role: "submodule" as const, ok: true, problem: null, via: "public" as const, fix: null }] : [];
  return ok({ repositories: [{ url, role: "main", ok: true, problem: null, via: findRepo(url) ? "github-app" : "public", fix: null }, ...submodules], all_ok: true }, { can_manage_keys: can("github.manage"), github_app: true });
});

// ---------------------------------------------------------------- documentation

route("GET", "/docs", () => ok(DOCS));
route("GET", "/docs/:*path", ({ params }) => {
  const page = docPage(params.path!);
  if (!page) throw new Problem(404, "This page does not exist.");
  return ok(page);
});

// ---------------------------------------------------------------- plugins and the marketplace

function pluginJob(id: string, title: string, steps: string[], onDone: () => void) {
  need("settings.manage");
  if (jobs.some((j) => j.instance === "panel:plugins" && (j.status === "running" || j.status === "queued"))) throw new Problem(409, "Another plugin is being installed. Try again when it has finished.");
  const job = startJob({ instance: "panel:plugins", action: title, kind: "plugin-install", steps, seconds: 1.6, panel: true, context: { name: id, database: "", version: "", edition: "community" }, onDone: () => { onDone(); publish("plugins", "", {}); } });
  return accepted(job);
}

route("GET", "/plugins", () => {
  need("plugins.view", "/api/v1/plugins");
  return ok(plugins, { restart_pending: false });
});
route("GET", "/plugins/notices", () => ok([]));
route("POST", "/plugins", ({ body }) => {
  const entry = catalog().find((p) => p.id === body.id);
  if (!entry) throw new Problem(404, "This plugin is not in the marketplace.");
  if (entry.tier === "paid" && entry.action === "buy") throw offline("Buying a plugin");
  return pluginJob(entry.id, `Install ${entry.name}`, ["Downloading the package", "Checking its signature", "Backing up the panel's data", "Installing", "Restarting the backend"], () => {
    const existing = plugins.findIndex((p) => p.id === entry.id);
    const plugin = {
      id: entry.id, name: entry.name, version: entry.latest_version?.version ?? "1.0.0", type: entry.type, tier: entry.tier,
      description: entry.description, publisher: entry.publisher, permissions: ["alerts.read"], enabled: true, status: "active" as const,
      loaded: true, restart_pending: false, licence: null, latest_version: entry.latest_version?.version ?? null, update_available: false,
      installed_at: iso(Date.now()), installed_by: ME,
    };
    if (existing >= 0) plugins[existing] = plugin;
    else plugins.push(plugin);
    record(ME, "plugin installed", { detail: `${entry.id} ${plugin.version}` });
  });
});
route("POST", "/plugins/:id/update", ({ params }) => {
  const plugin = plugins.find((p) => p.id === params.id);
  if (!plugin) throw new Problem(404, "This plugin is not installed.");
  return pluginJob(plugin.id, `Update ${plugin.name}`, ["Downloading the package", "Checking its signature", "Installing", "Restarting the backend"], () => {
    plugin.version = plugin.latest_version ?? plugin.version;
    plugin.update_available = false;
  });
});
route("POST", "/plugins/:id/:verb", ({ params }) => {
  need("settings.manage");
  const plugin = plugins.find((p) => p.id === params.id);
  if (!plugin) throw new Problem(404, "This plugin is not installed.");
  plugin.enabled = params.verb === "enable";
  plugin.status = plugin.enabled ? "active" : "disabled";
  publish("plugins", "", {});
  return ok(plugin);
});
route("DELETE", "/plugins/:id", ({ params }) => {
  const plugin = plugins.find((p) => p.id === params.id);
  if (!plugin) throw new Problem(404, "This plugin is not installed.");
  return pluginJob(plugin.id, `Uninstall ${plugin.name}`, ["Backing up the panel's data", "Removing the plugin", "Restarting the backend"], () => {
    plugins.splice(plugins.indexOf(plugin), 1);
  });
});
route("GET", "/marketplace", () => ok(marketplace));
route("POST", "/marketplace/heartbeat", () => {
  marketplace.last_heartbeat_at = iso(Date.now());
  return ok(marketplace);
});
route("POST", "/marketplace/link", () => {
  throw offline("Linking this panel to an app.sudoo.sh account");
});
route("POST", "/marketplace/unlink", () => {
  throw offline("Logging this panel out of its app.sudoo.sh account");
});
route("GET", "/marketplace/catalog", ({ query }) => {
  need("plugins.view");
  const q = (query.get("q") ?? "").toLowerCase();
  const type = query.get("type");
  const tier = query.get("tier");
  const list: CatalogPlugin[] = catalog().filter((p) => (!q || `${p.name} ${p.description} ${p.tags.join(" ")}`.toLowerCase().includes(q)) && (!type || p.type === type) && (!tier || p.tier === tier));
  return ok(list, { total: list.length });
});
route("GET", "/marketplace/catalog/:id", ({ params }) => {
  const entry = catalog().find((p) => p.id === params.id);
  if (!entry) throw new Problem(404, "This plugin is not in the marketplace.");
  return ok({ id: entry.id, homepage: null, permissions: ["alerts.read"], plans: entry.plans });
});
route("GET", "/marketplace/recommendations", () => ok(recommendations()));
route("GET", "/marketplace/categories", () => ok(categories()));
route("GET", "/marketplace/icons/:id", () => {
  throw new Problem(404, "No icon.");
});
route("GET", "/marketplace/billing", () => ok({ payments_enabled: false, linked: true, account_name: "Example Integrator", can_buy: false, reason: "payments_disabled", currency: "usd", manage_url: null }));
route("POST", "/marketplace/checkout-sessions", () => {
  throw offline("Buying a plugin");
});
route("GET", "/marketplace/checkout-sessions", () => ok([]));
route("GET", "/marketplace/checkout-sessions/:id", () => {
  throw new Problem(404, "This purchase does not exist.");
});
route("GET", "/marketplace/receipts", () => ok([]));

// ---------------------------------------------------------------- dispatching

const PUBLIC = ["/setup", "/contact", "/session", "/session/second-factor"];

export function remember() {
  try {
    sessionStorage.setItem("sudoo.demo", JSON.stringify({ role: session.role, signedIn: session.signedIn }));
  } catch {
    /* private window: the demo starts over on a reload */
  }
}

export async function answer(method: string, path: string, query: URLSearchParams, body: unknown): Promise<Answer> {
  for (const r of routes) {
    if (r.method !== method) continue;
    const match = r.pattern.exec(path);
    if (!match) continue;
    const params = Object.fromEntries(r.names.map((name, i) => [name, decodeURIComponent(match[i + 1]!)]));
    try {
      if (!PUBLIC.some((p) => path === p || path.startsWith(`${p}/`))) signedIn();
      return await r.handler({ method, params, query, body: (body ?? {}) as Record<string, unknown> });
    } catch (error) {
      if (error instanceof Problem) return problem(error.status, error.detail, path);
      console.error(error);
      return problem(500, "Something went wrong in the demo. Reload the page to start again.", path);
    }
  }
  return problem(404, "Not found.", path);
}

const TITLES: Record<number, string> = { 400: "Bad Request", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found", 409: "Conflict", 500: "Internal Server Error" };
function problem(status: number, detail: string, path: string): Answer {
  return { status, body: { type: "about:blank", title: TITLES[status] ?? "Error", status, detail, instance: `/api/v1${path}`, request_id: hex(16) } };
}
