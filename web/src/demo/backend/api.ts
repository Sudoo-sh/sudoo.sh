// The demo's /api/v1: a router over method and path that answers like the real backend (src/sudoo/web/v1): the
// same envelopes ({"data", "meta"}), 202 + the job for background work, 204 for deletions, RFC 9457 problem details
// for errors, and the same permission checks per role. It changes the in-memory state and starts simulated jobs.

import type { Account, Backup, CatalogPlugin, Instance, Invitation, Job, Permission, ProjectOptions, Session, Stage, Upgrade, UpgradeMethod, UpgradeReport, User } from "@/api/types";
import { browserLocale, isLocale, storedLocale, type Locale } from "@/i18n";
import { publish } from "./bus";
import { docPage, DOCS } from "./docs";
import { addDomain, checkName, checkNow, domainsOf, findDomain, makePrimary, removeDomain } from "./domains";
import { announce, full, reference, startJob } from "./jobs";
import { capacity, currentMetrics, history, listed, newProfile } from "./metrics";
import { applied, cleanLimits, markPending, resourcesOf, setOwn } from "./resources";
import * as mail from "./mail";
import * as caught from "./caught-mail";
import * as offsite from "./offsite";
import { branchList, membersOf, productionInstance, projectResource, projects, stageOf, type DemoProject } from "./projects";
import { described, levelIn, LEVELS, mayUnlock, needIn, openAccess, permissionsIn, seesProject, setLocks, teamResource, teams, type DemoAccess } from "./access";
import { acceptLicence, LICENCE, licencePending, licenceStatus } from "./licence";
import { catalog, categories, marketplace, recommendations } from "./marketplace";
import { endShell, issueTicket, recordingOf, shellOptions, shellSessions } from "./shell";
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
  PROJECT_NAMES,
  projectReference,
  instances,
  instanceUrl,
  jobs,
  logs,
  MANAGED_BASE,
  MANAGED_COMPANY,
  managedName,
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
import { DAY, hash, hex, HOUR, iso, MINUTE, OPENED, Problem, slug } from "./util";

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

/** `permission` in an instance: the role, then the access of its project and branch (src/sudoo/projects/access.py). */
function needOn(name: string, permission: Permission, path = "", role = true) {
  if (role) need(permission, path);
  const instance = instances.get(name);
  const project = instance?.project ? projects.get(instance.project.id) : undefined;
  if (instance && project) {
    try {
      needIn(project.access, permission, instance.name, instance.git.branch);
    } catch (error) {
      record(ME, `refused: ${path || permission}`, { ok: false, instance: name });
      if (error instanceof Problem && error.status === 404) throw new Problem(404, "This instance does not exist.");
      throw error;
    }
  }
}

/** The instances the visitor may see (a restricted project or branch hides its own). */
function visible(instance: Instance): boolean {
  const project = instance.project ? projects.get(instance.project.id) : undefined;
  return !project || levelIn(project.access, instance.git.branch) !== "none";
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
    language: language(),
    licence_required: role.name === "super_admin" && licencePending(),
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
/** The visitor's own language, kept in this browser like the panel's own; none: the visitor's browser, then the
 *  demo's DEFAULT_LANGUAGE. */
let ownLanguage: Locale | null = storedLocale();
const defaultLanguage = (): Locale => (isLocale(setting("default_language")) ? (setting("default_language") as Locale) : "en");
const language = (): Locale => ownLanguage ?? browserLocale() ?? defaultLanguage();
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
    language: { value: language(), own_choice: ownLanguage, default: defaultLanguage() },
  };
}

// first-run setup can be tried by opening /setup: the demo then answers as a panel without accounts, once
let setUp = false;
route("GET", "/setup", () => ok({ needed: !setUp && location.pathname === "/setup", contact: CONTACT, default_language: defaultLanguage() }));
route("GET", "/contact", () => ok({ sentence: CONTACT, name: "Operations team", phone: "", email: "ops@example.com", default_language: defaultLanguage() }));
route("POST", "/setup", ({ body }) => {
  if (setUp) throw new Problem(409, "This panel is set up already.");
  if (!String(body.setup_code ?? "").trim()) throw new Problem(403, "The setup code is not correct. Show it again with: sudoo.sh setup-code");
  if (!String(body.first_name ?? "").trim() || !String(body.email ?? "").includes("@")) throw new Problem(400, "Enter at least your first name and your e-mail address.");
  switchRole("super_admin");
  acceptLicence(body.licence, { user: ME, display_name: String(body.first_name), email: String(body.email) }, "panel_setup");
  setUp = true;
  session.signedIn = true;
  remember();
  record(ME, "first-run setup: super admin account created");
  record(ME, "accepted the Sudoo.sh Licence Agreement", { detail: `version ${LICENCE.version}` });
  return created(me());
});
// Premium features (src/sudoo/premium): the demo has none installed, so every premium page shows its plan card.
const PREMIUM_PACKAGES = [
  ["teams", "Teams and access per project", ["teams", "per_project_access"], "Pro and Business"],
  ["domains", "Custom domains", ["custom_domains"], "Business"],
  ["offsite", "Off-site backups", ["offsite_backups"], "Pro and Business"],
  ["support", "Support access", ["support_access"], "Pro and Business"],
  ["servers", "Several servers", ["multi_server"], "Business"],
] as const;
route("GET", "/premium", () =>
  ok({
    packages: PREMIUM_PACKAGES.map(([id, name, features, plans]) => ({
      id, name, features: [...features], plans, included: false, status: "not_included", version: null, installed_at: null,
    })),
    auto_install: true,
    marketplace: true,
    last_checked_at: Math.floor(OPENED / 1000),
    last_error: null,
    job: null,
    build: { python_tag: "cp315", platform: "linux_x86_64" },
  }),
);
route("POST", "/premium/sync", () => {
  throw offline("Installing premium features");
});
route("GET", "/licence/text", () => ok(LICENCE));
route("GET", "/licence", () => {
  signedIn();
  return ok(licenceStatus(can("settings.manage")));
});
route("POST", "/licence/acceptances", ({ body }) => {
  signedIn();
  if (session.role !== "super_admin") throw new Problem(403, "Only a super admin can accept the Sudoo.sh Licence Agreement for this panel.");
  const self = me();
  acceptLicence(body, { user: self.user, display_name: self.display_name, email: self.email }, "panel");
  record(ME, "accepted the Sudoo.sh Licence Agreement", { detail: `version ${LICENCE.version}` });
  return created(licenceStatus(true));
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
  if ("language" in body) {
    const choice = body.language;
    if (choice !== null && !isLocale(choice)) throw new Problem(400, "language must be one of en, ar, or null.");
    ownLanguage = choice;
    record(ME, choice ? `language set to ${choice}` : "language back to the default");
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
  const list = [...instances.values()].filter(visible).sort((a, b) => a.name.localeCompare(b.name)).map(listed);
  const limit = Number(query.get("limit") ?? 100);
  return ok(list.slice(0, limit), { total: list.length, count: Math.min(limit, list.length), limit, offset: 0 });
});
route("GET", "/instances/:name", ({ params }) => {
  needOn(params.name!, "instances.view");
  const instance = instanceOf(params.name!);
  const project = instance.project ? projects.get(instance.project.id) : undefined;
  return ok(full(instance), { permissions: (project ? permissionsIn(project.access, instance.git.branch) : roleInfo(session.role).permissions).slice().sort() });
});
route("GET", "/instances/:name/credentials", ({ params }) => {
  needOn(params.name!, "instances.credentials", `/api/v1/instances/${params.name}/credentials`);
  const instance = instanceOf(params.name!);
  record(ME, "credentials viewed", { instance: instance.name });
  const n = hash(instance.name);
  return ok({ login: "admin", admin_password: `demo-${n.toString(36)}-not-real`, master_password: `demo-master-${(n >>> 7).toString(36)}`, ports: { gateway: 8069 + (n % 40), odoo: 28069 + (n % 40) } });
});
route("GET", "/instances/:name/resources", ({ params }) => {
  need("instances.view");
  return ok(resourcesOf(instanceOf(params.name!)));
});
route("PUT", "/instances/:name/resources", ({ params, body }) => {
  needOn(params.name!, "instances.resources", `/api/v1/instances/${params.name}/resources`);
  const instance = instanceOf(params.name!);
  if (instance.status === "creating") throw new Problem(409, "This instance is still being created. Try again when its creation has finished.");
  if (instance.current_job) throw new Problem(409, `${instance.name} is busy with "${instance.current_job.action}". Try again when it has finished.`);
  const limits = cleanLimits(body.limits);
  const production = instance.stage === "production";
  if (production && body.confirm_production !== true)
    throw new Problem(400, `${instance.name} is a production instance: applying resources restarts it. Confirm ("confirm_production": true).`);
  setOwn(instance, limits);
  record(ME, "instance resources changed", { instance: instance.name, detail: Object.entries(limits).map(([k, v]) => `${k}=${v}`).join(", ") || "stage defaults" });
  if (ASLEEP.includes(instance.status)) {
    applied(instance.name);
    return accepted(startJob({ instance: instance.name, action: "Apply resources", kind: "restart", steps: ["Writing the new resources"], seconds: 1.5 }));
  }
  return accepted(
    startJob({
      instance: instance.name,
      action: "Apply resources",
      kind: "restart",
      steps: [...(production ? ["Backing up the database and files before the change"] : []), "Applying the new resources", "Waiting for Odoo to be ready"],
      status: "starting",
      onStart: production ? () => addBackup(instance, "before-update", Date.now()) : undefined,
      onDone: () => wake(instance),
    }),
  );
});
route("GET", "/instances/:name/logs", ({ params, query }) => {
  needOn(params.name!, "instances.logs");
  const instance = instanceOf(params.name!);
  return ok({ text: odooLog(instance, Math.min(Number(query.get("lines") ?? 300), 3000)) });
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
    else if (n % 89 === 0)
      out.push(
        `${stamp} 1 ERROR ${db} odoo.http: Exception during request handling.`,
        "Traceback (most recent call last):",
        '  File "/usr/lib/python3/dist-packages/odoo/http.py", line 2203, in __call__',
        "    response = request._serve_db()",
        `odoo.exceptions.ValidationError: The payment of order POS/${n % 900} does not match its total.`,
      );
    else if (n % 23 === 0) out.push(`${stamp} 1 DEBUG ${db} odoo.sql_db: query took 0.0${n % 90}s: SELECT "res_partner"."id" FROM "res_partner"`);
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
  return createInstance(body);
});

/** A new instance as POST /instances makes it; `extra` for an instance of a project's branch (the project, the
 *  production instance a staging one copies). */
function createInstance(
  body: Record<string, unknown>,
  extra: { project?: string; fromProduction?: Instance | null; upgradeOf?: { source: Instance; target: string; method: UpgradeMethod; onDone: () => void } } = {},
): Answer {
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
  const projectId = extra.project ?? (known && PROJECT_NAMES[known.name] ? known.name : null);
  if (stage === "production" && projectId) {
    const found = projects.get(projectId);
    const limit = Number(setting("project_production_branches") ?? 1);
    const others = found ? branchList(found).filter((b) => b.stage === "production" && b.name !== branch) : [];
    if (found && limit && others.length >= limit)
      throw new Problem(409, `${found.name} may have ${limit} production ${limit === 1 ? "branch" : "branches"}, and ${others.map((b) => b.name).join(", ")} is in production already.`);
  }
  const instance: Instance = {
    name,
    status: "creating",
    stage,
    project: projectId ? projectReference(projectId) : null,
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
  if (extra.fromProduction) {
    const at = steps.findIndex((s) => s.startsWith("Creating the Odoo"));
    steps = [`Copying the data of ${extra.fromProduction.name} (production)`, ...steps.slice(0, at), `Loading the backup of ${extra.fromProduction.name} into database ${instance.odoo.database}`, "Neutralising the copied data", "Setting the admin password", ...steps.slice(at + 1)];
    addBackup(extra.fromProduction, "staging-copy", Date.now());
  }
  if (extra.upgradeOf) {
    const { source, target, method } = extra.upgradeOf;
    const at = steps.findIndex((s) => s.startsWith("Creating the Odoo"));
    steps = [`Backing up ${source.name}`, ...steps.slice(0, at), `Loading the backup of ${source.name} into database ${instance.odoo.database}`,
      `Upgrading the copy to Odoo ${target} (${method === "enterprise" ? "Odoo's upgrade service" : "OpenUpgrade"})`, "Neutralising the copied data", "Setting the admin password", ...steps.slice(at + 1)];
    addBackup(source, "staging-copy", Date.now());
  }
  if (fromBackup) {
    const at = steps.findIndex((s) => s.startsWith("Creating the Odoo"));
    steps = [...steps.slice(0, at), `Loading the backup of ${fromBackup.instance} into database ${instance.odoo.database}`, ...(fromBackup.instance !== name ? ["Neutralising the copied data"] : []), ...steps.slice(at + 1)];
  }
  const job = startJob({
    instance: name,
    action: "Create instance",
    kind: "create",
    steps,
    seconds: steps.map((s) => (s.startsWith("Upgrading the copy") ? 7 : s.startsWith("Creating the Odoo") || s.startsWith("Loading the backup") ? 4.5 : s.startsWith("Installing") ? 3.5 : 2)),
    status: "creating",
    notes: extra.upgradeOf
      ? [{ text: `${name} runs a neutralised copy of ${extra.upgradeOf.source.name} upgraded to Odoo ${extra.upgradeOf.target}. Test it; then upgrade production from ${extra.upgradeOf.source.name}'s Upgrade tab.`, level: "info" }]
      : extra.fromProduction
      ? [{ text: `The data comes from ${extra.fromProduction.name} (production) and was neutralised: it sends no e-mail and runs no scheduled actions; payment providers are off.`, level: "info" }]
      : fromBackup && fromBackup.instance !== name ? [{ text: `The backup of ${fromBackup.instance} was neutralised: outgoing mail, scheduled actions and integrations are off.`, level: "info" }] : [],
    onDone: () => {
      const commits = known ? commitsOf(known, known.branches.includes(branch) ? branch : known.defaultBranch) : [];
      const head = commits[0];
      instance.status = "online";
      instance.managed_name = managedName(name, repo ? branch : null, instance.stage);
      instance.url = instance.managed_name?.url ?? instanceUrl(name);
      instance.git.commit = head ? { sha: head.sha, subject: head.subject, author: head.author, age: ageText(Date.parse(head.when)) } : repo ? { sha: hex(7), subject: "Initial commit", author: "you", age: "1 minute ago" } : null;
      instance.recent_commits = commits;
      instance.last_visit_at = iso(Date.now());
      instance.pauses_at = instance.settings.auto_pause ? iso(Date.now() + Number(setting("idle_hours")) * HOUR) : null;
      instance.expires_at = deleteAfter ? iso(now + deleteAfter * DAY) : null;
      instance.disk = { database_bytes: 96 * 1024 ** 2, files_bytes: 12 * 1024 ** 2 };
      profiles.get(name)!.since = Date.now();
      extra.upgradeOf?.onDone();
    },
  });
  instance.current_job = { ...reference(job), steps: [] };
  announce(instance);
  return { status: 202, body: { data: job }, headers: { Location: `/api/v1/jobs/${job.id}`, "Content-Location": `/api/v1/instances/${name}` } };
}

route("PATCH", "/instances/:name", ({ params, body }) => {
  needOn(params.name!, "instances.operate");
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
    markPending(instance.name); // its containers keep the old weights until it restarts
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
  applied(instance.name);
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
  needOn(instance.name, permission!, `/api/v1/instances/${instance.name}/actions/${action}`);
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
      return startJob({ ...base, steps: ["Backing up the database and files"], seconds: 4, status: instance.status, onDone: () => offsite.backupTaken(addBackup(instance, "manual", Date.now())) });
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
  needOn(instance.name, "instances.delete", `/api/v1/instances/${instance.name}`);
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

// ---------------------------------------------------------------- custom domains

function confirmProduction(instance: Instance, body: Record<string, unknown>, what: string) {
  if (instance.stage === "production" && body.confirm_production !== true)
    throw new Problem(400, `${instance.name} is a production instance: confirm that you want to ${what} ("confirm_production": true). Its users reach it through its domains.`);
}
function ownDomain(instance: Instance, name: string) {
  const found = findDomain(instance.name, name);
  if (!found) throw new Problem(404, "This instance has no such domain.");
  return found;
}

// ---------------------------------------------------------------- web shell (simulated: shell.ts)

route("GET", "/instances/:name/shell", ({ params }) => {
  need("instances.view");
  return ok(shellOptions(instanceOf(params.name!)));
});
route("POST", "/instances/:name/shell/tickets", ({ params, body }) => {
  const instance = instanceOf(params.name!);
  needOn(instance.name, body.target === "psql" ? "instances.database_shell" : "instances.shell", `/api/v1/instances/${instance.name}/shell/tickets`, false);
  try {
    return created(issueTicket(instance, body));
  } catch (error) {
    record(ME, "shell refused", { instance: instance.name, ok: false, detail: error instanceof Error ? error.message : "" });
    throw error;
  }
});
route("GET", "/shells", ({ query }) => {
  need("shells.supervise", "/shells");
  const name = query.get("instance");
  return ok(shellSessions(name ? instanceOf(name) : null));
});
route("DELETE", "/shells/:id", ({ params }) => {
  need("shells.supervise", "/shells");
  endShell(params.id!);
  return none();
});
route("GET", "/shells/:id/recording", ({ params }) => {
  need("shells.supervise", "/shells");
  return ok(recordingOf(params.id!));
});

route("GET", "/instances/:name/domains", ({ params }) => {
  need("instances.view");
  return ok(domainsOf(instanceOf(params.name!)));
});
route("POST", "/instances/:name/domains", ({ params, body }) => {
  needOn(params.name!, "domains.manage", `/api/v1/instances/${params.name}/domains`);
  const instance = instanceOf(params.name!);
  const value = String(body.domain ?? "");
  if (!value.trim()) throw new Problem(400, "Enter the domain, such as erp.example.com.");
  confirmProduction(instance, body, "add a domain to it");
  const limit = domainsOf(instance).limit;
  if (domainsOf(instance).domains.length >= limit) throw new Problem(409, `An instance may have ${limit} custom domains at most (DOMAINS_PER_INSTANCE).`);
  const checked = checkName(value, instance.name);
  if (typeof checked !== "string") throw new Problem(typeof checked.error === "string" && checked.error.includes("already") ? 409 : 400, checked.error);
  const added = addDomain(instance, checked, ME);
  record(ME, "custom domain added", { instance: instance.name, detail: checked });
  return created(added);
});
route("PATCH", "/instances/:name/domains/:domain", ({ params, body }) => {
  needOn(params.name!, "domains.manage", `/api/v1/instances/${params.name}/domains/${params.domain}`);
  const instance = instanceOf(params.name!);
  const found = ownDomain(instance, params.domain!);
  if (body.primary !== true) throw new Problem(400, 'Send {"primary": true} to make this the primary domain.');
  if (!found.primary) {
    confirmProduction(instance, body, "change its primary domain");
    makePrimary(instance, found.domain);
    record(ME, "primary custom domain changed", { instance: instance.name, detail: found.domain });
  }
  return ok(found);
});
route("POST", "/instances/:name/domains/:domain/check", ({ params }) => {
  needOn(params.name!, "domains.manage", `/api/v1/instances/${params.name}/domains/${params.domain}/check`);
  const instance = instanceOf(params.name!);
  ownDomain(instance, params.domain!);
  return ok(checkNow(instance, params.domain!));
});
route("DELETE", "/instances/:name/domains/:domain", ({ params, body }) => {
  needOn(params.name!, "domains.manage", `/api/v1/instances/${params.name}/domains/${params.domain}`);
  const instance = instanceOf(params.name!);
  const found = ownDomain(instance, params.domain!);
  confirmProduction(instance, body, `remove ${found.domain}`);
  removeDomain(instance, found.domain);
  record(ME, "custom domain removed", { instance: instance.name, detail: found.domain });
  return none();
});

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

// ---------------------------------------------------------------- the help panel and the mail catcher

route("GET", "/help", () => {
  need("instances.view");
  const email = String(setting("admin_contact_email") ?? "");
  const own = String(setting("support_url") ?? "");
  const support = own
    ? { kind: own.startsWith("mailto:") ? "email" : "link", url: own }
    : email
      ? { kind: "email", url: `mailto:${email}` }
      : { kind: "none", url: null, name: String(setting("admin_contact_name") ?? "the administrator") };
  return ok({ version: "3.0.0", docs_url: String(setting("docs_url") ?? "https://docs.sudoo.sh"), support });
});

function catching(name: string) {
  const instance = instanceOf(name);
  if (instance.stage === "production") throw new Problem(409, "A production instance sends its own e-mail: the mail catcher keeps nothing for it.");
  return instance;
}

route("GET", "/instances/:name/mail", ({ params, query }) => {
  needOn(params.name!, "caught_mail.view", `/api/v1/instances/${params.name}/mail`);
  const instance = catching(params.name!);
  const limit = Math.min(200, Number(query.get("limit") ?? 50));
  const found = caught.list(instance.name, query.get("q") ?? "", limit);
  return ok(found.items, {
    total: found.total,
    count: found.items.length,
    limit,
    offset: 0,
    catcher: { running: true, exists: true },
    usage: found.usage,
    limits: {
      retention_days: Number(setting("mail_catcher_retention_days") ?? 7),
      max_messages: Number(setting("mail_catcher_max_messages") ?? 500),
      max_mb: Number(setting("mail_catcher_max_mb") ?? 200),
      max_message_mb: Number(setting("mail_catcher_max_message_mb") ?? 25),
    },
    release: setting("mail_catcher_release") !== false,
  });
});
route("GET", "/instances/:name/mail/:id", ({ params }) => {
  needOn(params.name!, "caught_mail.view", `/api/v1/instances/${params.name}/mail`);
  const found = caught.detail(catching(params.name!).name, params.id!);
  if (!found) throw new Problem(404, "This e-mail does not exist (it may have been deleted, or removed after the retention period).");
  return ok(found);
});
route("DELETE", "/instances/:name/mail/:id", ({ params }) => {
  needOn(params.name!, "caught_mail.manage", `/api/v1/instances/${params.name}/mail/${params.id}`);
  const instance = catching(params.name!);
  const subject = caught.detail(instance.name, params.id!)?.subject ?? "";
  if (!caught.remove(instance.name, params.id!)) throw new Problem(404, "This e-mail does not exist.");
  record(ME, "caught e-mail deleted", { instance: instance.name, detail: `${params.id}: ${subject}` });
  return none();
});
route("DELETE", "/instances/:name/mail", ({ params }) => {
  needOn(params.name!, "caught_mail.manage", `/api/v1/instances/${params.name}/mail`);
  const instance = catching(params.name!);
  record(ME, "caught e-mails deleted", { instance: instance.name, detail: `${caught.removeAll(instance.name)} e-mails` });
  return none();
});
route("POST", "/instances/:name/mail/:id/release", ({ params, body }) => {
  needOn(params.name!, "caught_mail.manage", `/api/v1/instances/${params.name}/mail/${params.id}/release`);
  const instance = catching(params.name!);
  const to = String(body.to ?? "").trim();
  if (!/^[^@\s]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,63}$/.test(to)) throw new Problem(400, "Type one e-mail address to send the copy to.");
  if (setting("mail_catcher_release") === false) throw new Problem(403, "Releasing caught e-mails is switched off on this panel (Settings, E-mail).");
  if (!caught.detail(instance.name, params.id!)) throw new Problem(404, "This e-mail does not exist.");
  throw offline("Sending a copy of a caught e-mail");
});

route("GET", "/server", () => {
  need("instances.view");
  return ok({ version: "1.0.0", panel_url: "https://panel.demo.sudoo.sh", plugins: { exposure: "cloudflare-quick", enterprise_source: "folder", update_triggers: ["polling", "github-app"] }, metrics: currentMetrics().server, live_clients: 1 });
});
route("GET", "/server/capacity", () => ok(capacity()));

function managedSubdomains() {
  const held = [...instances.values()].filter((i) => i.managed_name).length;
  return {
    setting: setting("managed_subdomains") !== false, linked: true, enabled: true, active: setting("managed_subdomains") !== false,
    base_domain: MANAGED_BASE, company: MANAGED_COMPANY, limit: 25, used: held, held, checked_at: Date.now() / 1000 - 4 * 60,
    error: null, pending_release: [], addresses: { public_ipv4: "203.0.113.24" }, custom_domains: String(setting("custom_domains") ?? "off"),
  };
}
route("GET", "/server/managed-subdomains", () => ok(managedSubdomains()));
route("POST", "/server/managed-subdomains/sync", () => {
  need("settings.manage");
  return ok(managedSubdomains());
});
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
  if (Object.keys(body).some((key) => key.startsWith("offsite_"))) need("offsite.configure");
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
  if (!passwordAllowed()) throw new Problem(400, "Invite the person instead: they receive an e-mail and choose their own password (INVITATION_PASSWORD_FALLBACK).");
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
  for (const team of teams.values()) team.members = team.members.filter((m) => m !== user.name);
  const keep = <T extends { type: string; id: string }>(entries: T[]) => entries.filter((e) => !(e.type === "user" && e.id === user.name));
  for (const project of projects.values()) {
    project.access.grants = keep(project.access.grants);
    for (const rule of Object.values(project.access.branches)) rule.grants = keep(rule.grants);
    for (const lock of Object.values(project.access.locks)) lock.allow = keep(lock.allow);
  }
  record(ME, "account deleted", { detail: user.name });
  return none();
});
// ---------------------------------------------------------------- invitations
// The demo sends no e-mail: each invitation carries its link (the real API never shows it), and the menu of an
// invited person opens it. /invite#sample is the seeded invitation's link, for anyone who wants to see the page.

type DemoInvitation = Invitation & { token: string };
const invitations: DemoInvitation[] = [];
const MAIL_READY = true; // the demo's e-mail settings are filled in
const passwordAllowed = () => {
  const rule = String(setting("invitation_password_fallback") ?? "no_mail");
  return rule === "always" || (rule === "no_mail" && !MAIL_READY);
};
const invitationHours = () => Number(setting("invitation_hours") ?? 72);
function makeInvitation(email: string, role: User["role"], first: string | null, last: string | null, token = hex(24), sentAgo = 0, by = ME): DemoInvitation {
  const sent = Date.now() - sentAgo;
  return {
    id: hex(12), token, email, first_name: first, last_name: last, role, role_label: roleInfo(role).label, invited_by: by, sent_count: 1,
    expired: sent + invitationHours() * HOUR < Date.now(), invited_at: iso(sent)!, sent_at: iso(sent)!, expires_at: iso(sent + invitationHours() * HOUR)!,
    link: `/invite#${token}`,
  };
}
invitations.push(
  makeInvitation("huda.ali@example.com", "developer", "Huda", "Ali", "sample", 20 * HOUR),
  makeInvitation("k.mansour@example.com", "viewer", null, null, hex(24), 4 * DAY, "omar"),
);
const shownInvitation = ({ token: _t, ...rest }: DemoInvitation): Invitation => ({ ...rest, expired: Date.parse(rest.expires_at) < Date.now() });
function invitationOf(id: string): DemoInvitation {
  const found = invitations.find((v) => v.id === id);
  if (!found) throw new Problem(404, "No such invitation.");
  if (found.role === "super_admin" && session.role !== "super_admin") throw new Problem(403, "Only a super admin can invite super admins.");
  return found;
}
function validInvitation(token: unknown): DemoInvitation {
  const found = invitations.find((v) => v.token === String(token ?? "").trim());
  if (!found || Date.parse(found.expires_at) < Date.now())
    throw new Problem(410, "This invitation link does not work: it was used, sent again, revoked or it expired. Ask for a new one.");
  return found;
}
route("GET", "/invitations", () => {
  need("accounts.manage", "/api/v1/invitations");
  return ok(invitations.map(shownInvitation), { mail_ready: MAIL_READY, password_allowed: passwordAllowed(), invitation_hours: invitationHours() });
});
route("POST", "/invitations", ({ body }) => {
  need("accounts.manage", "/api/v1/invitations");
  const email = String(body.email ?? "").trim();
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) throw new Problem(400, "Enter a valid e-mail address.");
  if (users.some((u) => u.email?.toLowerCase() === email.toLowerCase())) throw new Problem(400, "An account already uses this e-mail address.");
  if (invitations.some((v) => v.email.toLowerCase() === email.toLowerCase() && Date.parse(v.expires_at) > Date.now()))
    throw new Problem(400, "This address is already invited. Send the invitation again from the list instead.");
  const role = String(body.role ?? "viewer") as User["role"];
  if (role === "super_admin" && session.role !== "super_admin") throw new Problem(403, "Only a super admin can invite super admins.");
  const made = makeInvitation(email, role, String(body.first_name ?? "") || null, String(body.last_name ?? "") || null);
  invitations.unshift(made);
  record(ME, "invitation sent", { detail: `${email} (${roleInfo(role).label})` });
  return created(shownInvitation(made));
});
route("POST", "/invitations/:id/resend", ({ params }) => {
  need("accounts.manage");
  const found = invitationOf(params.id!);
  found.token = hex(24);
  found.link = `/invite#${found.token}`;
  found.sent_count += 1;
  found.sent_at = iso(Date.now())!;
  found.expires_at = iso(Date.now() + invitationHours() * HOUR)!;
  record(ME, "invitation sent again", { detail: found.email });
  return ok(shownInvitation(found));
});
route("DELETE", "/invitations/:id", ({ params }) => {
  need("accounts.manage");
  const found = invitationOf(params.id!);
  invitations.splice(invitations.indexOf(found), 1);
  record(ME, "invitation revoked", { detail: found.email });
  return none();
});
route("POST", "/invitation", ({ body }) => {
  const found = validInvitation(body.token);
  const by = users.find((u) => u.name === found.invited_by);
  return ok({
    email: found.email, first_name: found.first_name, last_name: found.last_name, role: found.role, role_label: found.role_label,
    invited_by: by?.display_name ?? found.invited_by, expires_at: found.expires_at, second_factor_required: false,
  });
});
route("POST", "/invitation/accept", ({ body }) => {
  const found = validInvitation(body.token);
  if (!String(body.first_name ?? "").trim()) throw new Problem(400, "Enter your first name.");
  if (String(body.password ?? "").length < 12) throw new Problem(400, "The password must be at least 12 characters long.");
  invitations.splice(invitations.indexOf(found), 1);
  // the demo has one person: accepting signs you in as them again (nothing here is real)
  session.signedIn = true;
  remember();
  record(ME, "invitation accepted: account created", { detail: found.email });
  return created(me());
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
route("GET", "/backups", ({ query }) => {
  need("backups.manage", "/api/v1/backups");
  // ?instance=<name>: those of one instance; ?project=<id>: those of the project's instances (as the server filters)
  const instance = query.get("instance");
  const project = query.get("project");
  return ok(
    backups.filter(
      (b: Backup) => (!instance || b.instance === instance) && (!project || instances.get(b.instance)?.project?.id === project),
    ),
  );
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
// ---------------------------------------------------------------- off-site copies
const copyOf = (id: string) => offsite.find(id) ?? (() => { throw new Problem(404, "No such off-site copy."); })();
const busyCopy = (found: { status: string }) => {
  if (found.status === "uploading" || found.status === "verifying") throw new Problem(409, "This copy is being uploaded or checked right now.");
};
const givenKey = (body: Record<string, unknown>) => {
  const text = String(body.public_key ?? "").trim();
  if (text && !/^-----BEGIN PUBLIC KEY-----[\s\S]+-----END PUBLIC KEY-----$/.test(text)) throw new Problem(400, "This is not a public key in PEM form (-----BEGIN PUBLIC KEY-----).");
  return Boolean(text);
};
route("GET", "/offsite", () => {
  need("backups.manage", "/api/v1/offsite");
  return ok(offsite.state());
});
route("GET", "/offsite/public-key", () => {
  throw offline("Downloading the public key");
});
route("POST", "/offsite/test", () => {
  need("offsite.configure");
  const state = offsite.state();
  if (!state.configured) throw new Problem(409, "Off-site storage is not set up yet: fill in its address, bucket and access key in Settings, Backups.");
  record(ME, "off-site storage tested", { detail: state.location ?? "" });
  return ok({ location: state.location, steps: ["wrote a test file", "read it back", "listed the folder", "deleted it"] });
});
route("POST", "/offsite/scan", () => {
  need("backups.manage");
  return ok({ found: 0, missing: 0 });
});
route("GET", "/offsite/copies", () => {
  need("backups.manage", "/api/v1/offsite/copies");
  return ok(offsite.all());
});
route("POST", "/offsite/copies/:id/verify", ({ params, body }) => {
  need("backups.manage");
  const found = copyOf(params.id!);
  busyCopy(found);
  return accepted(offsite.verify(found, givenKey(body)));
});
route("POST", "/offsite/copies/:id/retry", ({ params }) => {
  need("backups.manage");
  const found = copyOf(params.id!);
  busyCopy(found);
  if (!backups.some((b) => found.files.some((f) => f.name === b.file))) throw new Problem(409, "The backup files of this copy are no longer on this server, so it cannot be copied again.");
  return accepted(offsite.retry(found));
});
route("POST", "/offsite/copies/:id/restore", ({ params, body }) => {
  need("offsite.restore");
  need("instances.restore");
  const found = copyOf(params.id!);
  const name = String(body.instance ?? "") || found.instance;
  const instance = instances.get(name);
  if (!instance) throw new Problem(404, `There is no instance ${name}. Create one of the same Odoo version first, then restore into it.`);
  busyCopy(found);
  if (instance.stage === "production" && body.confirm_production !== true) throw new Problem(400, `${name} is a production instance: confirm that you want to restore into it ("confirm_production": true).`);
  const withKey = givenKey(body);
  const foreign = found.instance !== name;
  found.status = "verifying";
  publish("offsite", found.id, null);
  record(ME, "off-site copy restore started", { instance: name, detail: found.id });
  const taken = new Date((found.created ?? 0) * 1000).toUTCString().slice(5, 22);
  return accepted(startJob({
    instance: name, action: "Restore an off-site copy", kind: "restore", seconds: 2.2, status: "busy",
    steps: ["Checking the signature of the manifest", `Downloading and checking ${found.files[0]?.name}`, "Saving the current state first", "Stopping Odoo", `Restoring the backup of ${taken}` + (foreign ? ` from ${found.instance}` : ""), ...(foreign ? ["Neutralising the copied data"] : []), "Starting Odoo"],
    notes: [{ text: `The off-site copy ${found.id} passed its check${withKey ? " with the public key you gave" : ""} (signature and every checksum)`, level: "info" }],
    onDone: () => {
      found.status = "verified";
      found.verified_at = Date.now() / 1000;
      found.reason = null;
      publish("offsite", found.id, null);
      const before = addBackup(instance, "before-restore", Date.now());
      instance.rollback = { backups: [before.file], taken_at: iso(Date.now())!, until: iso(Date.now() + Number(setting("rollback_hours")) * HOUR)! };
      wake(instance);
    },
  }));
});
route("DELETE", "/offsite/copies/:id", ({ params }) => {
  need("offsite.delete");
  const found = copyOf(params.id!);
  busyCopy(found);
  if (instances.has(found.instance) && offsite.isProtected(found)) throw new Problem(409, "This is the newest good off-site copy of the instance, so it is kept. Delete it once a newer copy is stored (or after the instance is deleted).");
  offsite.remove(found);
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

// ---------------------------------------------------------------- projects (src/sudoo/web/v1/projects.py)

function projectOf(id: string): DemoProject {
  const found = projects.get(id);
  if (!found || !seesProject(found.access)) throw new Problem(404, "This project does not exist.");
  return found;
}

/** `permission` in a project (and branch): the role, then the project's access and the branch's lock. */
function needInProject(project: DemoProject, permission: Permission, branch?: string, path = "") {
  need(permission, path);
  try {
    needIn(project.access, permission, project.name, branch);
  } catch (error) {
    record(ME, `refused: ${path || permission}`, { ok: false, detail: `${project.id}${branch ? ` ${branch}` : ""}` });
    throw error;
  }
}

function projectMeta(project: DemoProject) {
  const c = capacity();
  return {
    default_branch: project.repo?.defaultBranch ?? null,
    branches_error: null,
    branches_read_at: iso(Date.now() - 12 * 1000),
    capacity: { comfortable: c.comfortable, running: c.running, headroom: c.headroom, warning: c.warning, message: c.message, overloaded: c.overloaded },
    production_limit: Number(setting("project_production_branches") ?? 1) || null,
    server_production_limit: Number(setting("max_production_instances") ?? 0) || null,
    permissions: permissionsIn(project.access).filter((p) => (PROJECT_SCOPED as string[]).includes(p)),
  };
}

const PROJECT_SCOPED: Permission[] = [
  "instances.view", "instances.logs", "instances.update", "instances.operate", "instances.create", "instances.restore",
  "instances.delete", "instances.credentials", "instances.resources", "instances.shell", "instances.database_shell",
  "caught_mail.view", "caught_mail.manage", "backups.manage", "offsite.restore", "offsite.delete", "domains.manage",
  "projects.manage", "projects.access",
];

const publishProject = (project: DemoProject) => publish("project", project.id, projectResource(project));

route("GET", "/projects", () => {
  need("instances.view");
  const list = [...projects.values()].filter((p) => seesProject(p.access)).sort((a, b) => a.name.localeCompare(b.name)).map(projectResource);
  const standalone = [...instances.values()].filter((i) => !i.project).map((i) => i.name).sort();
  return ok(list, { standalone, total: list.length });
});

route("GET", "/projects/repository-owners", () => {
  need("projects.manage");
  return ok(
    (github.app?.installations ?? []).map((i) => ({
      account: i.account,
      account_type: i.account_type,
      possible: true,
      reason: "ok",
      message: "",
      url: "",
      label: "",
    })),
    { connected: true },
  );
});

route("GET", "/projects/:id", ({ params }) => {
  need("instances.view");
  const project = projectOf(params.id!);
  return ok({ ...projectResource(project), branches_list: branchList(project) }, projectMeta(project));
});

route("POST", "/projects", ({ body }) => {
  need("projects.manage", "/api/v1/projects");
  const version = String(body.odoo_version ?? "");
  if (!["17.0", "18.0", "19.0"].includes(version)) throw new Problem(400, "Choose an Odoo version from the list.");
  const edition = body.edition === "enterprise" ? "enterprise" : "community";
  const create = body.create as Record<string, unknown> | undefined;
  let repository: string;
  let repo = null;
  if (create) {
    need("github.manage");
    const name = String(create.name ?? "").trim();
    if (!/^[A-Za-z0-9._-]{1,100}$/.test(name) || name === "." || name === "..") throw new Problem(400, "This is not a repository name GitHub accepts (letters, digits, '.', '-', '_').");
    if (REPOS.some((r) => r.name === name)) throw new Problem(409, `${OWNER} has a repository named ${name} already. Choose another name.`);
    repository = `${String(create.owner ?? OWNER)}/${name}`;
  } else {
    const typed = String(body.repository ?? "").trim().replace(/^https:\/\/github\.com\//, "").replace(/\.git$/, "").replace(/\/$/, "");
    repo = findRepo(`https://github.com/${typed}`);
    if (!repo) throw new Problem(400, `The server cannot read ${typed || "this repository"}. In the demo, pick one of ${REPOS.map((r) => r.name).join(", ")}.`);
    repository = `${OWNER}/${repo.name}`;
  }
  if ([...projects.values()].some((p) => p.repository.toLowerCase() === repository.toLowerCase())) throw new Problem(409, `${repository} has a project already.`);
  const shown = String(body.name ?? "").trim() || repository.split("/")[1]!;
  let id = slug(shown).slice(0, 36) || "project";
  for (let n = 2; projects.has(id); n++) id = `${slug(shown).slice(0, 33)}-${n}`;
  const project: DemoProject = { id, name: shown, repository, repo, version, edition, origin: create ? "created" : "linked", created_at: Date.now(), created_by: ME, own: {}, stages: {}, autoCreated: {}, access: openAccess() };
  projects.set(id, project);
  PROJECT_NAMES[id] = shown;
  if (create) record(ME, "repository created on GitHub", { detail: repository });
  record(ME, "project created", { detail: `${id}: ${project.origin} ${repository}` });
  publishProject(project);
  return created({ ...projectResource(project), default_branch: repo?.defaultBranch ?? null });
});

// ---------------------------------------------------------------- database upgrades (src/sudoo/upgrades/)

const DEMO_VERSIONS = ["17.0", "18.0", "19.0"];
const OPENUPGRADE_REPOSITORY = "https://github.com/OCA/OpenUpgrade";
const ODOO_TERMS = "https://www.odoo.com/documentation/18.0/legal/terms/enterprise.html#upgrade-services";
interface DemoUpgrade {
  state: Upgrade["state"];
  target: string | null;
  branch: string | null;
  report: UpgradeReport | null;
  staging: string | null;
  tested_at: string | null;
  promotion: Upgrade["promotion"];
  previous?: { version: string; branch: string | null };
  history: Upgrade["history"];
}
const upgrades = new Map<string, DemoUpgrade>();
const upgradeOf = (name: string): DemoUpgrade =>
  upgrades.get(name) ?? (upgrades.set(name, { state: null, target: null, branch: null, report: null, staging: null, tested_at: null, promotion: null, history: [] }), upgrades.get(name)!);
const upgradeMethod = (instance: Instance): UpgradeMethod => (instance.odoo.edition === "enterprise" ? "enterprise" : "openupgrade");
const upgradeTargets = (instance: Instance) => {
  const major = Number(instance.odoo.version.split(".")[0]);
  const later = DEMO_VERSIONS.filter((v) => Number(v.split(".")[0]) > major);
  return upgradeMethod(instance) === "enterprise" ? later : later.filter((v) => Number(v.split(".")[0]) === major + 1);
};
function rememberUpgrade(name: string, event: string, detail = "", values: Partial<DemoUpgrade> = {}) {
  const found = upgradeOf(name);
  Object.assign(found, values);
  found.history = [{ at: iso(Date.now()), user: ME, event, detail }, ...found.history].slice(0, 20);
  record(ME, `database upgrade: ${event}`, { instance: name, detail });
}
function upgradeResource(instance: Instance): Upgrade {
  const found = upgradeOf(instance.name);
  const targets = upgradeTargets(instance);
  const until = found.promotion?.rollback_until ? Date.parse(found.promotion.rollback_until) : 0;
  return {
    instance: instance.name,
    from_version: instance.odoo.version,
    edition: instance.odoo.edition,
    method: upgradeMethod(instance),
    allowed: true,
    targets,
    next: targets[0] ?? null,
    skips_versions: upgradeMethod(instance) === "enterprise",
    terms_url: upgradeMethod(instance) === "enterprise" ? ODOO_TERMS : null,
    openupgrade_source: upgradeMethod(instance) === "openupgrade" ? OPENUPGRADE_REPOSITORY : null,
    refusal: [...upgrades.values()].some((u) => u.staging === instance.name) ? `${instance.name} is the test instance of another upgrade.` : null,
    state: found.state,
    target: found.target,
    branch: found.branch,
    report: found.report,
    checked_at: found.report?.checked_at ?? null,
    staging: found.staging && instances.has(found.staging) ? found.staging : null,
    tested_at: found.tested_at,
    test_job: null,
    promotion: found.promotion,
    rollback_open: found.state === "promoted" && until > Date.now(),
    rollback_minutes: Number(setting("upgrade_rollback_minutes") ?? 60),
    history: found.history,
  };
}
function upgradeTarget(instance: Instance, body: Record<string, unknown>): string {
  const targets = upgradeTargets(instance);
  const target = String(body.target ?? targets[0] ?? "");
  if (!targets.includes(target)) throw new Problem(400, `Choose a version ${instance.name} can be upgraded to: ${targets.join(", ") || "none"}.`);
  return target;
}
function upgradeCode(instance: Instance, body: Record<string, unknown>) {
  if (upgradeMethod(instance) !== "enterprise") return;
  if (body.accept_odoo_terms !== true)
    throw new Problem(400, 'This uses Odoo S.A.\'s upgrade service under your Odoo Enterprise subscription and Odoo\'s terms: accept them first ("accept_odoo_terms": true).');
  if (typeof body.subscription_code !== "string" || !/^[A-Za-z0-9][A-Za-z0-9_.-]{3,99}$/.test(body.subscription_code.trim()))
    throw new Problem(400, "Enter the Odoo Enterprise subscription code of this database (letters, digits and dashes).");
  // the demo never sends it anywhere: Odoo's upgrade service is simulated
}
const DEMO_MODULES = (prefix: string, target: string): UpgradeReport["modules"] => [
  { name: `${prefix}_sale`, version: "18.0.1.2.0", target_version: `${target}.1.0.0`, status: "ready" },
  { name: `${prefix}_stock`, version: "18.0.1.0.3", target_version: `${target}.1.0.0`, status: "ready" },
  { name: `${prefix}_reports`, version: "18.0.2.1.0", target_version: "18.0.2.1.0", status: "version_mismatch" },
];

route("GET", "/instances/:name/upgrade", ({ params }) => {
  needOn(params.name!, "instances.view");
  return ok(upgradeResource(instanceOf(params.name!)));
});
route("POST", "/instances/:name/upgrade/check", ({ params, body }) => {
  needOn(params.name!, "instances.upgrade");
  const instance = instanceOf(params.name!);
  const target = upgradeTarget(instance, body);
  const repo = instance.source === "repository";
  const prefix = (instance.project?.id ?? instance.name).split("-")[0]!.replace(/[^a-z]/g, "") || "custom";
  const modules = repo ? DEMO_MODULES(prefix, target) : [];
  const report: UpgradeReport = {
    instance: instance.name,
    from_version: instance.odoo.version,
    target,
    edition: instance.odoo.edition,
    method: upgradeMethod(instance),
    repository: instance.git.repository,
    branch: repo ? target : null,
    branch_exists: repo ? true : null,
    installed: ["base", "web", "sale_management", "stock", "account", ...modules.map((m) => m.name)],
    installed_count: 42,
    modules,
    warnings: modules.some((m) => m.status !== "ready")
      ? [{ code: "modules_not_ready", text: `1 of the repository's modules is not ready for Odoo ${target}: ${prefix}_reports. A developer has to port it.` }]
      : [],
    blocking: [],
    checked_at: iso(Date.now()),
  };
  rememberUpgrade(instance.name, "pre-check", `to Odoo ${target}: ${modules.length} repository modules, ${modules.filter((m) => m.status !== "ready").length} not ready`,
    { report, target, branch: repo ? target : null, state: upgradeOf(instance.name).state ?? "checked" });
  return ok(upgradeResource(instance));
});
route("POST", "/instances/:name/upgrade/test", ({ params, body }) => {
  needOn(params.name!, "instances.upgrade");
  const instance = instanceOf(params.name!);
  const target = upgradeTarget(instance, body);
  const found = upgradeOf(instance.name);
  if (found.report?.target !== target) throw new Problem(409, `Run the pre-check for Odoo ${target} first, and read its report.`);
  if (found.state === "testing" || found.state === "promoting") throw new Problem(409, "A test or an upgrade of this instance is running already.");
  upgradeCode(instance, body);
  const method = upgradeMethod(instance);
  const staging = found.staging ? instances.get(found.staging) : undefined;
  if (staging && staging.odoo.version === target) {
    if (staging.current_job) throw new Problem(409, `${staging.name} is busy; try again when its job has finished.`);
    rememberUpgrade(instance.name, "test started", `again on ${staging.name}`, { state: "testing" });
    addBackup(instance, "staging-copy", Date.now());
    return accepted(startJob({
      instance: staging.name,
      action: `Run the Odoo ${target} upgrade test again`,
      kind: "upgrade-test",
      steps: [`Backing up ${instance.name}`, `Downloading the latest code of ${target}`, "Stopping Odoo", `Loading the copy into database ${staging.odoo.database}`,
        `Upgrading the copy to Odoo ${target} (${method === "enterprise" ? "Odoo's upgrade service" : "OpenUpgrade"})`, "Neutralising the copied data", "Setting the admin password", "Starting Odoo"],
      seconds: [2, 2, 1.5, 3.5, 7, 2, 1.5, 2.5],
      onDone: () => rememberUpgrade(instance.name, "tested", `again on ${staging.name}`, { state: "tested", tested_at: iso(Date.now()) }),
    }));
  }
  const repo = instance.source === "repository";
  const answer = createInstance(
    { repo: repo ? instance.git.repository : "", branch: target, version: target, edition: instance.odoo.edition, stage: "staging", name: `${instance.name.slice(0, 30)}-odoo${target.split(".")[0]}`, install_modules: false },
    {
      project: instance.project?.id,
      upgradeOf: {
        source: instance,
        target,
        method,
        onDone: () => rememberUpgrade(instance.name, "tested", `on ${stagingName}`, { state: "tested", staging: stagingName, tested_at: iso(Date.now()) }),
      },
    },
  );
  const stagingName = String((answer.headers?.["Content-Location"] ?? "").split("/").pop());
  rememberUpgrade(instance.name, "test started", `to Odoo ${target} on ${stagingName}`, { state: "testing", target, staging: stagingName });
  return answer;
});
route("POST", "/instances/:name/upgrade/promote", ({ params, body }) => {
  needOn(params.name!, "instances.upgrade");
  const instance = instanceOf(params.name!);
  const found = upgradeOf(instance.name);
  if (!found.tested_at || !["tested", "promotion_failed", "rolled_back"].includes(found.state ?? ""))
    throw new Problem(409, "Test the upgrade first: production is upgraded only after a test that succeeded.");
  if (instance.stage === "production" && body.confirm_production !== true)
    throw new Problem(400, `${instance.name} is a production instance: confirm that you want to upgrade it to Odoo ${found.target} ("confirm_production": true).`);
  upgradeCode(instance, body);
  if (instance.current_job) throw new Problem(409, `${instance.name} is busy; try again when its job has finished.`);
  const target = found.target!;
  const method = upgradeMethod(instance);
  const minutes = Number(setting("upgrade_rollback_minutes") ?? 60);
  rememberUpgrade(instance.name, "production upgrade started", `to Odoo ${target}`, { state: "promoting", previous: { version: instance.odoo.version, branch: instance.git.branch } });
  addBackup(instance, "before-upgrade", Date.now());
  return accepted(startJob({
    instance: instance.name,
    action: `Upgrade to Odoo ${target}`,
    kind: "db-upgrade",
    status: "stopped",
    steps: ["Putting Odoo into maintenance", "Backing up the database and files",
      ...(method === "enterprise"
        ? [`Upgrading the database to Odoo ${target} with Odoo's upgrade service`, `Switching to Odoo ${target}${found.branch ? ` and branch ${found.branch}` : ""}`]
        : [`Switching to Odoo ${target}${found.branch ? ` and branch ${found.branch}` : ""}`, `Upgrading the database to Odoo ${target} with OpenUpgrade`]),
      "Starting Odoo", "Waiting for Odoo to be ready"],
    seconds: [1.5, 3, 7, 3, 2, 2],
    notes: minutes ? [{ text: `${instance.name} runs Odoo ${target}. For ${minutes} minutes you can cancel the upgrade (back to the backup taken before it; what was changed meanwhile is lost), and it is put back by itself if Odoo stops answering.`, level: "info" }] : [],
    onDone: () => {
      instance.odoo.version = target;
      if (found.branch) instance.git.branch = found.branch;
      instance.status = "online";
      rememberUpgrade(instance.name, "production upgraded", `to Odoo ${target}`, {
        state: "promoted",
        promotion: { at: iso(Date.now()), by: ME, target, backup: [`${instance.name}__before-upgrade.zip`], finished_at: iso(Date.now()), rollback_until: minutes ? iso(Date.now() + minutes * MINUTE) : null },
      });
      announce(instance);
    },
  }));
});
route("POST", "/instances/:name/upgrade/cancel", ({ params, body }) => {
  needOn(params.name!, "instances.upgrade");
  const instance = instanceOf(params.name!);
  const found = upgradeOf(instance.name);
  if (!upgradeResource(instance).rollback_open) throw new Problem(409, "There is no upgrade of this instance that can still be cancelled.");
  if (instance.stage === "production" && body.confirm_production !== true)
    throw new Problem(400, `${instance.name} is a production instance: confirm that you want to cancel its upgrade ("confirm_production": true).`);
  addBackup(instance, "before-rollback", Date.now());
  return accepted(startJob({
    instance: instance.name,
    action: "Cancel the Odoo upgrade",
    kind: "db-upgrade",
    status: "stopped",
    steps: ["Saving the current state first", "Putting the instance back", "Starting Odoo", "Waiting for Odoo to be ready"],
    seconds: [2.5, 4, 2, 2],
    onDone: () => {
      if (found.previous) {
        instance.odoo.version = found.previous.version;
        instance.git.branch = found.previous.branch;
      }
      instance.status = "online";
      rememberUpgrade(instance.name, "production upgrade cancelled", "by hand", { state: "rolled_back", promotion: { ...found.promotion, rolled_back_at: iso(Date.now()), rolled_back_by: ME } });
      announce(instance);
    },
  }));
});
route("POST", "/instances/:name/upgrade/keep", ({ params }) => {
  needOn(params.name!, "instances.upgrade");
  const instance = instanceOf(params.name!);
  const found = upgradeOf(instance.name);
  if (!upgradeResource(instance).rollback_open) throw new Problem(409, "There is no upgrade of this instance that can still be cancelled.");
  found.promotion = { ...found.promotion, rollback_until: iso(Date.now() - 1000) };
  rememberUpgrade(instance.name, "upgrade kept", "the rollback window was closed");
  return ok(upgradeResource(instance));
});
route("DELETE", "/instances/:name/upgrade", ({ params }) => {
  needOn(params.name!, "instances.upgrade");
  const instance = instanceOf(params.name!);
  upgrades.delete(instance.name);
  return none();
});

const OPTION_CHOICES: Partial<Record<keyof ProjectOptions, string[]>> = { staging_on_push: ["rebuild", "keep_data"], branch_deleted: ["keep", "archive", "delete"] };

route("PATCH", "/projects/:id", ({ params, body }) => {
  const project = projectOf(params.id!);
  needInProject(project, "projects.manage");
  if ("name" in body) {
    const name = String(body.name ?? "").trim();
    if (!name) throw new Problem(400, "Give the project a name.");
    project.name = name.slice(0, 60);
    PROJECT_NAMES[project.id] = project.name;
    for (const instance of membersOf(project.id)) instance.project = projectReference(project.id);
  }
  if (typeof body.odoo_version === "string") project.version = body.odoo_version;
  if (body.edition === "community" || body.edition === "enterprise") project.edition = body.edition;
  const changes = (body.options ?? {}) as Record<string, unknown>;
  for (const [key, value] of Object.entries(changes)) {
    const option = key as keyof ProjectOptions;
    if (!(option in projectResource(project).options)) throw new Problem(400, `Unknown option ${key}.`);
    if (value === null) delete project.own[option];
    else {
      const choices = OPTION_CHOICES[option];
      if (choices && !choices.includes(String(value))) throw new Problem(400, `${key}: ${choices.join(" or ")}.`);
      if (typeof value === "number" && (!Number.isInteger(value) || value < 0)) throw new Problem(400, `${key}: a whole number from 0.`);
      (project.own as Record<string, unknown>)[option] = value;
    }
  }
  record(ME, "project changed", { detail: project.id });
  publishProject(project);
  return ok(projectResource(project));
});

route("DELETE", "/projects/:id", ({ params, body }) => {
  const project = projectOf(params.id!);
  needInProject(project, "projects.manage");
  if (body.confirm !== project.id) throw new Problem(400, "Type the project's id exactly to confirm.");
  const members = membersOf(project.id);
  if (members.length) throw new Problem(409, `${project.name} still has ${members.length} instances. Delete them first; the repository on GitHub is never touched.`);
  projects.delete(project.id);
  record(ME, "project deleted", { detail: project.id });
  publish("project", project.id, { id: project.id, deleted: true });
  return none();
});

route("PUT", "/projects/:id/branches/:*branch/stage", ({ params, body }) => {
  const project = projectOf(params.id!);
  const branch = params.branch!.replace(/\/stage$/, "");
  needInProject(project, "projects.manage", branch);
  const stage = body.stage as Stage;
  if (!["production", "staging", "development"].includes(stage)) throw new Problem(400, "Choose a stage: production, staging or development.");
  const before = stageOf(project, branch);
  if (before !== stage) {
    if ((stage === "production" || before === "production") && body.confirm_production !== true)
      throw new Problem(400, stage === "production" ? `Making ${branch} production changes how its instances run. Confirm ("confirm_production": true).` : `${branch} is a production branch. Confirm that it leaves production ("confirm_production": true).`);
    if (stage === "production") {
      const limit = Number(setting("project_production_branches") ?? 1);
      const others = branchList(project).filter((b) => b.stage === "production" && b.name !== branch);
      if (limit && others.length >= limit)
        throw new Problem(409, `${project.name} may have ${limit} production ${limit === 1 ? "branch" : "branches"}, and ${others.map((b) => b.name).join(", ")} ${others.length === 1 ? "is" : "are"} in production already. Move it to staging or development first, or raise Production branches per project (Settings, Projects).`);
    }
  }
  for (const instance of membersOf(project.id).filter((i) => i.git.branch === branch)) {
    if (instance.status === "creating") throw new Problem(409, `${instance.name} is still being created. Try again when its creation has finished.`);
    if (instance.stage !== stage) {
      instance.stage = stage;
      if (stage === "production") {
        instance.settings.delete_after_days = 0;
        instance.settings.auto_pause = false;
        instance.expires_at = null;
        instance.pauses_at = null;
      }
      announce(instance);
    }
  }
  project.stages[branch] = stage;
  record(ME, "branch stage changed", { detail: `${project.id} ${branch}: ${before} -> ${stage}` });
  publishProject(project);
  return ok(branchList(project).find((b) => b.name === branch));
});

route("POST", "/projects/:id/branches/:*branch/instances", ({ params, body }) => {
  const project = projectOf(params.id!);
  const branch = params.branch!.replace(/\/instances$/, "");
  needInProject(project, "instances.create", branch);
  if (!branchList(project).some((b) => b.name === branch && b.on_github !== false)) throw new Problem(404, `${project.repository} has no branch ${branch} on GitHub.`);
  const stage = stageOf(project, branch);
  let fromProduction: Instance | null = null;
  if (stage === "staging") {
    need("instances.restore");
    fromProduction = productionInstance(project);
    if (!fromProduction) throw new Problem(409, "This project has no production instance to copy. Create the instance of its production branch first (or give a branch the production stage), or make this branch a development branch.");
  }
  const lifetime = stage === "production" ? 0 : stage === "staging" ? Number(projectResource(project).options.staging_lifetime_days) : Number(projectResource(project).options.development_lifetime_days);
  return createInstance(
    {
      repo: `https://github.com/${project.repository}`,
      branch,
      version: project.version,
      edition: project.edition,
      stage,
      name: body.name,
      demo: stage === "development" ? body.demo ?? projectResource(project).options.development_demo_data : false,
      install_modules: body.install_modules,
      delete_after_days: lifetime,
    },
    { project: project.id, fromProduction },
  );
});

route("POST", "/projects/:id/branches/:*branch/rebuild", ({ params, body }) => {
  const project = projectOf(params.id!);
  const branch = params.branch!.replace(/\/rebuild$/, "");
  needInProject(project, "instances.restore", branch);
  const members = membersOf(project.id).filter((i) => i.git.branch === branch);
  const instance = members.find((i) => i.name === body.instance) ?? members[0];
  if (!instance) throw new Problem(404, `${branch} has no instance to rebuild.`);
  if (instance.stage !== "staging") throw new Problem(409, `${instance.name} is not a staging instance: only staging is rebuilt from production.`);
  const production = productionInstance(project);
  if (!production) throw new Problem(409, "This project has no production instance to copy.");
  if (instance.current_job) throw new Problem(409, `${instance.name} is busy with another job. Try again when it has finished.`);
  addBackup(production, "staging-copy", Date.now());
  const job = startJob({
    instance: instance.name,
    action: "Rebuild from production",
    kind: "restore",
    steps: [`Copying the data of ${production.name} (production)`, `Downloading the latest code of ${branch}`, "Stopping Odoo", `Loading the copy into database ${instance.odoo.database}`, "Neutralising the copied data", "Setting the admin password", "Upgrading the repository's modules (2)", "Starting Odoo", "Waiting for Odoo to be ready"],
    seconds: [3, 1.5, 1, 3.5, 2, 1, 3, 2, 1.5],
    status: "busy",
    notes: [{ text: `Rebuilt from a new copy of ${production.name}: neutralised (no e-mail goes out, scheduled actions and payment providers off).`, level: "info" }],
    onDone: () => {
      newCommit(instance);
      wake(instance);
    },
  });
  record(ME, "Rebuild from production", { instance: instance.name, detail: `${project.id} ${branch}` });
  return accepted(job);
});


// ---------------------------------------------------------------- teams, access and locks (projects/access.py)

const usedIn = (teamId: string) =>
  [...projects.values()]
    .filter((p) => {
      const lists = [p.access.grants, ...Object.values(p.access.branches).map((b) => b.grants), ...Object.values(p.access.locks).map((l) => l.allow)];
      return lists.some((entries) => entries.some((e) => e.type === "team" && e.id === teamId));
    })
    .map((p) => ({ id: p.id, name: p.name }));

function teamOf(id: string) {
  const team = teams.get(id);
  if (!team) throw new Problem(404, "This team does not exist.");
  return team;
}

function cleanMembers(raw: unknown): string[] {
  if (!Array.isArray(raw)) throw new Problem(400, 'members: a list of account names, for example ["ada", "omar"].');
  const found = [...new Set(raw.map(String))].sort();
  const unknown = found.find((m) => !users.some((u) => u.name === m));
  if (unknown) throw new Problem(400, `There is no account ${unknown}.`);
  return found;
}

function cleanTeamName(raw: unknown, other?: string): string {
  const name = String(raw ?? "").trim().replace(/\s+/g, " ");
  if (!name) throw new Problem(400, "Give the team a name.");
  if (name.length > 60) throw new Problem(400, "A team's name is at most 60 characters.");
  if ([...teams.values()].some((t) => t.id !== other && t.name.toLowerCase() === name.toLowerCase())) throw new Problem(409, `There is a team called ${name} already.`);
  return name;
}

route("GET", "/teams", () => {
  if (!can("teams.manage")) need("projects.access", "/api/v1/teams");
  return ok([...teams.values()].sort((a, b) => a.name.localeCompare(b.name)).map((t) => teamResource(t, usedIn(t.id))));
});
route("POST", "/teams", ({ body }) => {
  need("teams.manage", "/api/v1/teams");
  const name = cleanTeamName(body.name);
  let id = slug(name).slice(0, 36) || "team";
  for (let n = 2; teams.has(id); n++) id = `${slug(name).slice(0, 33)}-${n}`;
  const team = { id, name, description: String(body.description ?? "").trim().slice(0, 200), members: "members" in body ? cleanMembers(body.members) : [], created_at: Date.now(), created_by: ME, changed_at: Date.now(), changed_by: ME };
  teams.set(id, team);
  record(ME, "team created", { detail: `${id} (${name}): members ${team.members.join(", ") || "none"}` });
  return created(teamResource(team, []));
});
route("PATCH", "/teams/:id", ({ params, body }) => {
  need("teams.manage", `/api/v1/teams/${params.id}`);
  const team = teamOf(params.id!);
  const before = [...team.members];
  if ("name" in body) team.name = cleanTeamName(body.name, team.id);
  if ("description" in body) team.description = String(body.description ?? "").trim().slice(0, 200);
  if ("members" in body) team.members = cleanMembers(body.members);
  Object.assign(team, { changed_at: Date.now(), changed_by: ME });
  const added = team.members.filter((m) => !before.includes(m));
  const removed = before.filter((m) => !team.members.includes(m));
  record(ME, "team changed", { detail: `${team.id}: ${[added.length ? `added ${added.join(", ")}` : "", removed.length ? `removed ${removed.join(", ")}` : ""].filter(Boolean).join(", ") || "name or description changed"}` });
  for (const project of projects.values()) publishProject(project);
  return ok(teamResource(team, usedIn(team.id)));
});
route("DELETE", "/teams/:id", ({ params }) => {
  need("teams.manage", `/api/v1/teams/${params.id}`);
  const team = teamOf(params.id!);
  teams.delete(team.id);
  const keep = <T extends { type: string; id: string }>(entries: T[]) => entries.filter((e) => !(e.type === "team" && e.id === team.id));
  for (const project of projects.values()) {
    project.access.grants = keep(project.access.grants);
    for (const rule of Object.values(project.access.branches)) rule.grants = keep(rule.grants);
    for (const lock of Object.values(project.access.locks)) lock.allow = keep(lock.allow);
    setLocks(project.id, project.access, announce);
    publishProject(project);
  }
  record(ME, "team deleted", { detail: `${team.id} (${team.name})` });
  return none();
});

function accessResource(project: DemoProject) {
  const a = project.access;
  return {
    mode: a.mode,
    grants: described(a.grants),
    branches: Object.fromEntries(Object.entries(a.branches).map(([b, v]) => [b, { restricted: v.restricted, grants: described(v.grants) }])),
    locks: Object.fromEntries(Object.entries(a.locks).map(([b, v]) => [b, { ...v, allow: described(v.allow) }])),
    levels: LEVELS,
  };
}

function cleanSubjects<T extends boolean>(raw: unknown, what: string, withLevel: T): T extends true ? DemoAccess["grants"] : DemoAccess["locks"][string]["allow"] {
  if (raw == null) return [] as never;
  if (!Array.isArray(raw)) throw new Problem(400, `${what}: a list of entries.`);
  const found = new Map<string, Record<string, string>>();
  for (const entry of raw as Record<string, unknown>[]) {
    const type = entry.type === "team" || entry.type === "user" ? entry.type : null;
    if (!type) throw new Problem(400, `${what}: type is team or user.`);
    const id = String(entry.id ?? "");
    const known = type === "team" ? teams.has(id) : users.some((u) => u.name === id);
    if (!known) throw new Problem(400, `${what}: there is no ${type === "team" ? "team" : "account"} '${id}'.`);
    const item: Record<string, string> = { type, id };
    if (withLevel) {
      if (!LEVELS.includes(entry.level as never)) throw new Problem(400, `${what}: level is ${LEVELS.join(", ")}.`);
      item.level = String(entry.level);
    }
    found.set(`${type}:${id}`, item);
  }
  return [...found.values()] as never;
}

route("GET", "/projects/:id/access", ({ params }) => {
  const project = projectOf(params.id!);
  needInProject(project, "projects.access");
  return ok(accessResource(project), { permissions: permissionsIn(project.access) });
});
route("PUT", "/projects/:id/access", ({ params, body }) => {
  const project = projectOf(params.id!);
  needInProject(project, "projects.access");
  const mode = body.mode === "restricted" ? "restricted" : "panel_roles";
  const grants = cleanSubjects(body.grants, "grants", true);
  const branches: DemoAccess["branches"] = {};
  for (const [branch, rule] of Object.entries((body.branches ?? {}) as Record<string, Record<string, unknown>>)) {
    const own = cleanSubjects(rule.grants, `branches.${branch}.grants`, true);
    if (own.length || rule.restricted === true) branches[branch] = { restricted: rule.restricted === true, grants: own };
  }
  const after: DemoAccess = { ...project.access, mode, grants, branches };
  if (!permissionsIn(after).includes("projects.access"))
    throw new Problem(409, "After this change you could no longer manage this project's access. Grant yourself (or one of your teams) the admin level first.");
  project.access = after;
  record(ME, "project access changed", { detail: `${project.id}: mode ${mode}, ${grants.length} grants, branch rules ${Object.keys(branches).join(", ") || "none"}` });
  publishProject(project);
  return ok(accessResource(project));
});
route("PUT", "/projects/:id/branches/:*branch/lock", ({ params, body }) => {
  const project = projectOf(params.id!);
  const branch = params.branch!.replace(/\/lock$/, "");
  const before = project.access.locks[branch];
  if (before) {
    need("projects.access");
    if (!mayUnlock(project.access, branch))
      throw new Problem(403, `The branch ${branch} is locked by ${before.locked_by}: only the people and teams its lock allows (and super admins) may change or remove it.`);
  } else needInProject(project, "projects.access", branch, `lock ${project.id} ${branch}`);
  if (!branchList(project, true).some((b) => b.name === branch)) throw new Problem(404, "There is no such branch.");
  const allow = cleanSubjects(body.allow, "allow", false);
  if (!allow.some((e) => e.type === "user" && e.id === ME) && !allow.some((e) => e.type === "team" && teams.get(e.id)?.members.includes(ME))) allow.push({ type: "user", id: ME });
  const lock = { locked_by: before?.locked_by ?? ME, locked_at: before?.locked_at ?? Math.round(Date.now() / 1000), reason: String(body.reason ?? "").trim().slice(0, 200), allow };
  project.access.locks[branch] = lock;
  record(ME, before ? "branch lock changed" : "branch locked", { detail: `${project.id} ${branch}: allows ${allow.map((e) => `${e.type} ${e.id}`).join(", ")}` });
  setLocks(project.id, project.access, announce);
  publishProject(project);
  return ok({ ...lock, allow: described(allow) });
});
route("DELETE", "/projects/:id/branches/:*branch/lock", ({ params }) => {
  const project = projectOf(params.id!);
  const branch = params.branch!.replace(/\/lock$/, "");
  const lock = project.access.locks[branch];
  if (!lock) throw new Problem(404, `${branch} is not locked.`);
  need("projects.access", `unlock ${project.id} ${branch}`);
  if (!mayUnlock(project.access, branch)) {
    record(ME, `refused: unlock ${project.id} ${branch}`, { ok: false });
    throw new Problem(403, `The branch ${branch} is locked by ${lock.locked_by}: only the people and teams its lock allows (and super admins) may unlock it.`);
  }
  delete project.access.locks[branch];
  record(ME, "branch unlocked", { detail: `${project.id} ${branch} (locked by ${lock.locked_by})` });
  setLocks(project.id, project.access, announce);
  publishProject(project);
  return none();
});

// ---------------------------------------------------------------- support access (src/sudoo/support/): the demo has
// no connection to Sudoo.sh, so access cannot be granted here

route("GET", "/support-access/status", () => ok({ connected: false, sessions: [], active_grants: 0, can_end: false }));
route("GET", "/support-access", () => {
  need("support_access.manage");
  return ok({ enabled: true, unavailable: "not_connected", max_hours: 72, durations: [1, 24, 72], levels: ["view", "admin", "admin_shell"],
    shell_enabled: true, grants: [], sessions: [], channel: { last_ok: null, error: null } });
});

// ---------------------------------------------------------------- the panel's plan (src/sudoo/entitlements.py)

route("GET", "/plan", () => {
  need("instances.view");
  const names = new Set<string>();
  for (const project of projects.values())
    for (const branch of branchList(project, true)) if (branch.stage === "production") names.add(`${project.id}/${branch.name}`);
  for (const instance of instances.values()) if (!instance.project && instance.stage === "production") names.add(instance.name);
  const byStage = { production: 0, staging: 0, development: 0 };
  for (const instance of instances.values()) byStage[instance.stage] += 1;
  const limit = 5;
  const used = names.size;
  return ok({
    plan: "pro",
    source: "hub",
    linked: true,
    account_name: "Example Integrator",
    received_at: iso(Date.now() - 6 * 60 * 1000),
    valid_until: iso(Date.now() + 23 * HOUR),
    entitlements: {
      max_production_branches: limit,
      max_servers: 2,
      max_projects: null,
      teams: true,
      features: { per_project_access: true, custom_domains: true, offsite_backups: true },
      support_level: "email",
      addons: {},
      period: "year",
      renews_at: iso(OPENED + 210 * DAY),
      trial_plan: null,
    },
    production_withheld: null,
    banned: false,
    over_limit: [],
    usage: { servers: 1, projects: projects.size, production_branches: used, production_branch_names: [...names].sort(), instances_by_stage: byStage },
    production_refusal: used >= limit ? `The Pro plan allows ${limit} production branches, and this panel has ${used}. Move one out of production, or get more production branches. See the plans at https://sudoo.sh/pricing, or manage yours at https://app.sudoo.sh.` : null,
    production_keep: null,
    leaving_production: [],
    links: { pricing: "https://sudoo.sh/pricing", account: "https://app.sudoo.sh", keep: "https://app.sudoo.sh/plan" },
    checked_at: iso(Date.now()),
    // the demo shows the banner of a panel that is not linked yet (src/sudoo/link_deadline.py): to super admins and
    // administrators only, five of its seven days left
    link_deadline:
      ["super_admin", "admin"].includes(users.find((u) => u.name === ME)?.role ?? "")
        ? { linked: false, first_run: iso(OPENED - 2 * DAY), deadline_days: 7, deadline: iso(OPENED + 5 * DAY), days_left: 5, restricted: false }
        : null,
  });
});

// ---------------------------------------------------------------- dispatching

const PUBLIC = ["/setup", "/contact", "/session", "/session/second-factor", "/invitation"];

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
