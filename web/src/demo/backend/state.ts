// The demo's data: an Odoo integrator's server with three client projects, their branches in development, staging
// and production, backups, team accounts, the audit log and alerts. Everything is fictional: example.com
// addresses, *.demo.sudoo.sh links, documentation IP ranges (RFC 5737). It lives in this tab's memory only.

import type {
  Alert,
  AuditEntry,
  Backup,
  BackupKind,
  DeployKey,
  GitHubAppState,
  InstalledPlugin,
  Instance,
  Job,
  Permission,
  Role,
  RoleInfo,
  Setting,
  Stage,
  Token,
  User,
} from "@/api/types";
import { logText, type LogContext } from "./logs";
import seedSettings from "./settings.json";
import { backupFile, DAY, hash, hex, HOUR, iso, MINUTE, OPENED, seeded, SECOND } from "./util";

// ---------------------------------------------------------------- roles (as src/sudoo/roles.py)

const VIEW: Permission[] = ["instances.view", "instances.logs"];
const DEVELOP: Permission[] = [...VIEW, "instances.update", "caught_mail.view"];
const MAINTAIN: Permission[] = [...DEVELOP, "instances.operate", "instances.restore", "instances.create", "instances.credentials", "github.manage", "projects.manage"];
const ALL: Permission[] = [
  "instances.view", "instances.logs", "instances.update", "instances.operate", "instances.create", "projects.manage", "projects.access", "teams.manage", "instances.restore", "instances.upgrade",
  "instances.delete", "instances.credentials", "instances.resources", "caught_mail.view", "caught_mail.manage", "backups.manage", "domains.manage", "github.manage", "accounts.manage", "tokens.manage",
  "audit.view", "plugins.view", "settings.manage", "mail.manage", "offsite.configure", "offsite.restore", "offsite.delete",
  "instances.shell", "instances.database_shell", "shells.supervise", "servers.manage", "instances.move", "support_access.manage",
];
const SHELL: Permission[] = ["instances.shell", "instances.database_shell"];

export const PERMISSIONS: Record<Permission, string> = {
  "instances.view": "See instances, their figures and activity",
  "instances.logs": "Read Odoo's log and jobs' technical logs",
  "instances.update": "Get new code, restart, start, add submodules",
  "instances.operate": "Pause, switch off, upgrade, switch branch, back up, change automation",
  "instances.create": "Create instances",
  "projects.manage": "Create and link projects (one GitHub repository each), change their settings and the stages of their branches",
  "projects.access": "Choose who may see and work in a project and its branches (grants, restricted projects and branches) and lock or unlock branches",
  "teams.manage": "Create, change and delete teams (named groups of accounts that project access is given to)",
  "instances.restore": "Restore an instance from a backup (replaces its database and files)",
  "instances.upgrade": "Upgrade an instance's database to a newer Odoo version: test it on a new staging instance, then upgrade production (with its backup and rollback)",
  "instances.delete": "Delete instances",
  "instances.credentials": "See Odoo passwords and ports",
  "instances.resources": "Set an instance's resources: Odoo workers, time and memory limits, container limits",
  "caught_mail.view": "Read the e-mails caught from staging and development instances (the Mail tab)",
  "caught_mail.manage": "Delete caught e-mails and release them (send a copy to an address)",
  "backups.manage": "Download and delete backups; see, check and copy again off-site copies",
  "domains.manage": "Add and remove instances' custom domains (seeing them needs instances.view)",
  "github.manage": "Manage deploy keys; see the GitHub App's state",
  "accounts.manage": "Create, change and delete accounts",
  "tokens.manage": "Create and revoke API tokens",
  "audit.view": "Read the audit log",
  "plugins.view": "See installed plugins, the marketplace and purchases",
  "settings.manage": "Change the panel's settings, install and buy plugins, and restart it",
  "mail.manage": "Change the texts of the e-mails the panel sends",
  "offsite.configure": "Set up where off-site copies go, and test it",
  "offsite.restore": "Restore an instance from an off-site copy (it is downloaded and verified first)",
  "offsite.delete": "Delete off-site copies",
  "instances.shell": "Open a terminal in instances' Odoo containers (odoo shell too)",
  "instances.database_shell": "Open psql in instances' database containers",
  "shells.supervise": "See open and past shells, end them and play their recordings",
  "servers.manage": "Add, approve, revoke and remove servers; change their name, region and room",
  "instances.move": "Move instances to another server",
  "support_access.manage": "Grant Sudoo.sh support temporary access to the panel, see it and withdraw it",
};

export const ROLES: RoleInfo[] = [
  { name: "super_admin", label: "Super admin", description: "Everything, including the panel's settings.", permissions: ALL },
  { name: "admin", label: "Administrator", description: "Everything except the panel's settings and e-mail texts.", permissions: ALL.filter((p) => p !== "settings.manage" && p !== "mail.manage" && p !== "offsite.configure" && p !== "shells.supervise") },
  { name: "maintainer", label: "Maintainer", description: "Creates instances and runs every action on them except deleting.", permissions: MAINTAIN },
  { name: "developer", label: "Developer", description: "Gets new code, restarts and starts instances, adds submodules.", permissions: DEVELOP },
  { name: "viewer", label: "Viewer", description: "Sees instances, their activity and logs.", permissions: VIEW },
];
/** A role with its permissions as the settings make them: SHELL_ENABLED and SHELL_ROLES (as roles.permissions). */
export const roleInfo = (role: Role): RoleInfo => {
  const found = ROLES.find((r) => r.name === role) ?? ROLES[4]!;
  if (setting("shell_enabled") === false) return { ...found, permissions: found.permissions.filter((p) => !SHELL.includes(p)) };
  const extra = { admins: [], maintainers: ["maintainer"], developers: ["maintainer", "developer"] }[String(setting("shell_roles")) as "admins"] ?? [];
  return (extra as string[]).includes(found.name) ? { ...found, permissions: [...found.permissions, "instances.shell"] } : found;
};

// ---------------------------------------------------------------- repositories on GitHub

export const OWNER = "example-integrator";
export const repoUrl = (repo: string) => `https://github.com/${OWNER}/${repo}`;

export interface DemoRepo {
  name: string;
  description: string;
  defaultBranch: string;
  branches: string[];
  modules: string[];
  pushedAgo: number;
  commits: [subject: string, author: string][];
}

export const REPOS: DemoRepo[] = [
  {
    name: "acme-retail",
    description: "Acme Retail: point of sale, loyalty and e-commerce customisations",
    defaultBranch: "main",
    branches: ["main", "staging", "pos-refunds", "loyalty-points", "ecommerce-bundles"],
    modules: ["acme_pos_refunds", "acme_loyalty", "acme_website_bundles", "acme_reports"],
    pushedAgo: 38 * MINUTE,
    commits: [
      ["Refund to the original payment method from the POS", "Karim"],
      ["Loyalty points expire after twelve months", "Ines"],
      ["Bundle prices on the shop page", "Karim"],
      ["Z-report: totals per payment method", "Lena"],
      ["Fix rounding of discounted lines", "Ines"],
      ["Barcode nomenclature for weighed items", "Karim"],
      ["Gift cards in the loyalty programme", "Lena"],
    ],
  },
  {
    name: "nile-logistics",
    description: "Nile Logistics: fleet, routes and warehouse automation",
    defaultBranch: "main",
    branches: ["main", "fleet-gps", "upgrade-18", "customs-docs"],
    modules: ["nile_fleet_gps", "nile_routes", "nile_customs", "nile_wms"],
    pushedAgo: 2 * HOUR,
    commits: [
      ["Live vehicle positions on the delivery map", "Omar"],
      ["Route planner: respect loading-dock hours", "Lena"],
      ["Customs documents per shipment", "Omar"],
      ["Port the warehouse rules to Odoo 18", "Karim"],
      ["Batch pickings by delivery zone", "Lena"],
      ["Driver app: proof of delivery photo", "Omar"],
    ],
  },
  {
    name: "zahra-clinic",
    description: "Zahra Clinic: appointments, patient records and lab results",
    defaultBranch: "main",
    branches: ["main", "appointments", "lab-results"],
    modules: ["zahra_appointments", "zahra_patients", "zahra_lab", "zahra_invoicing"],
    pushedAgo: 5 * HOUR,
    commits: [
      ["Online booking with doctor availability", "Sara"],
      ["SMS reminder the day before an appointment", "Ines"],
      ["Lab results as a PDF on the patient portal", "Sara"],
      ["Invoice insurers and patients separately", "Lena"],
      ["Waiting list when a day is full", "Ines"],
    ],
  },
  {
    name: "odoo-common",
    description: "Shared add-ons used in every client project",
    defaultBranch: "18.0",
    branches: ["17.0", "18.0", "19.0"],
    modules: ["integrator_base", "integrator_mail_layout"],
    pushedAgo: 3 * DAY,
    commits: [["Mail layout with the client's colours", "Lena"]],
  },
  {
    name: "cedar-hotels",
    description: "Cedar Hotels: reservations and housekeeping (proposal)",
    defaultBranch: "main",
    branches: ["main", "housekeeping"],
    modules: ["cedar_reservations", "cedar_housekeeping"],
    pushedAgo: 6 * DAY,
    commits: [["Room types and seasonal rates", "Omar"]],
  },
];
/** The demo's projects, one per client repository (projects.ts); their names as people read them. */
export const PROJECT_NAMES: Record<string, string> = {
  "acme-retail": "Acme Retail",
  "nile-logistics": "Nile Logistics",
  "zahra-clinic": "Zahra Clinic",
  "cedar-hotels": "Cedar Hotels",
};
export const projectReference = (id: string) => ({ id, name: PROJECT_NAMES[id] ?? id, links: { self: `/api/v1/projects/${id}` } });

export const findRepo = (url: string | null | undefined) => {
  const name = /github\.com[/:][\w.-]+\/([\w.-]+?)(?:\.git)?\/?$/.exec(url ?? "")?.[1];
  return REPOS.find((r) => r.name === name) ?? null;
};

// ---------------------------------------------------------------- the people

/** The account the visitor is signed in as; its role is what the banner's switcher changes. */
export const ME = "sara";

export const users: User[] = [
  { name: "sara", display_name: "Sara Nassar", first_name: "Sara", last_name: "Nassar", email: "sara@example.com", phone: null, role: "admin", role_label: "Administrator", last_sign_in_at: iso(OPENED - 2 * MINUTE), password_changed_at: iso(OPENED - 41 * DAY), must_change_password: false, second_factors: ["totp"] },
  { name: "omar", display_name: "Omar Haddad", first_name: "Omar", last_name: "Haddad", email: "omar@example.com", phone: "+20 100 000 0000", role: "super_admin", role_label: "Super admin", last_sign_in_at: iso(OPENED - 3 * HOUR), password_changed_at: iso(OPENED - 80 * DAY), must_change_password: false, second_factors: ["totp", "email"] },
  { name: "lena", display_name: "Lena Brandt", first_name: "Lena", last_name: "Brandt", email: "lena@example.com", phone: null, role: "maintainer", role_label: "Maintainer", last_sign_in_at: iso(OPENED - 26 * MINUTE), password_changed_at: iso(OPENED - 12 * DAY), must_change_password: false, second_factors: ["totp"] },
  { name: "karim", display_name: "Karim Saleh", first_name: "Karim", last_name: "Saleh", email: "karim@example.com", phone: null, role: "developer", role_label: "Developer", last_sign_in_at: iso(OPENED - 50 * MINUTE), password_changed_at: iso(OPENED - 30 * DAY), must_change_password: false, second_factors: ["email"] },
  { name: "ines", display_name: "Ines Moreau", first_name: "Ines", last_name: "Moreau", email: "ines@example.com", phone: null, role: "developer", role_label: "Developer", last_sign_in_at: iso(OPENED - 1 * DAY), password_changed_at: iso(OPENED - 64 * DAY), must_change_password: false, second_factors: ["totp"] },
  { name: "acme.reviewer", display_name: "Acme reviewer", first_name: "Acme", last_name: "reviewer", email: "it@acme-retail.example.com", phone: null, role: "viewer", role_label: "Viewer", last_sign_in_at: iso(OPENED - 3 * DAY), password_changed_at: null, must_change_password: true, second_factors: [] },
];

// ---------------------------------------------------------------- settings

export const settings: Setting[] = (seedSettings as Setting[]).map((s) => ({ ...s }));
export const setting = (key: string) => settings.find((s) => s.key === key)?.value;

// ---------------------------------------------------------------- instances

/** How busy an instance is: what its figures move around. */
export interface Profile {
  seed: number;
  cpu: number;
  memory: number;
  rpm: number;
  ms: number;
  errors: number;
  /** when it was last started (ms): its figures begin there */
  since: number;
  /** a job makes it busier from then until then */
  boostFrom: number;
  boostUntil: number;
}

export interface Seeded {
  instance: Instance;
  profile: Profile;
}

export const instances = new Map<string, Instance>();
export const profiles = new Map<string, Profile>();
export const jobs: Job[] = []; // newest first
export const logs = new Map<string, string>();
export const backups: Backup[] = []; // newest first

const GIB = 1024 ** 3;
const MIB = 1024 ** 2;
export const MEMORY_LIMIT = 32 * GIB; // no container limit (the default): Docker reports the server's memory

const sha = (seed: string) => hash(seed).toString(16).padStart(8, "0").slice(0, 7);

export function commitsOf(repo: DemoRepo, branch: string, offset = 0) {
  const random = seeded(hash(repo.name + branch));
  let when = OPENED - repo.pushedAgo - offset * HOUR;
  return repo.commits.map(([subject, author], i) => {
    const commit = { sha: sha(`${repo.name}${branch}${i + offset}`), subject, author, when: iso(when)! };
    when -= (4 + random() * 30) * HOUR;
    return commit;
  });
}

export function ageText(ms: number) {
  const minutes = Math.round((Date.now() - ms) / MINUTE);
  if (minutes < 60) return `${Math.max(1, minutes)} minutes ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  const days = Math.round(hours / 24);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}

export function contextOf(instance: Instance): LogContext {
  const repo = findRepo(instance.git.repository);
  return {
    name: instance.name,
    database: instance.odoo.database,
    version: instance.odoo.version,
    edition: instance.odoo.edition,
    repository: instance.git.repository,
    branch: instance.git.branch,
    modules: repo?.modules,
  };
}

interface Seed {
  repo?: string;
  branch?: string;
  name?: string;
  stage: Stage;
  version: string;
  edition: "community" | "enterprise";
  status: Instance["status"];
  createdAgo: number;
  createdBy: string;
  visitedAgo: number | null;
  profile: Omit<Profile, "seed" | "since" | "boostFrom" | "boostUntil">;
  upFor: number;
  autoUpdate?: boolean;
  autoPause?: boolean;
  deleteAfter?: number;
  submodules?: { path: string; url: string }[];
}

const SEEDS: Seed[] = [
  { repo: "acme-retail", branch: "main", stage: "production", version: "18.0", edition: "enterprise", status: "online", createdAgo: 74 * DAY, createdBy: "omar", visitedAgo: 0.3 * MINUTE, profile: { cpu: 7.5, memory: 1.72 * GIB, rpm: 140, ms: 62, errors: 0.08 }, upFor: 11 * DAY, autoUpdate: false, autoPause: false, deleteAfter: 0, submodules: [{ path: "oca/pos", url: "https://github.com/OCA/pos" }, { path: "oca/web", url: "https://github.com/OCA/web" }] },
  { repo: "acme-retail", branch: "staging", stage: "staging", version: "18.0", edition: "enterprise", status: "online", createdAgo: 60 * DAY, createdBy: "omar", visitedAgo: 9 * MINUTE, profile: { cpu: 1.6, memory: 1.08 * GIB, rpm: 14, ms: 71, errors: 0 }, upFor: 2 * DAY, deleteAfter: 90, submodules: [{ path: "oca/pos", url: "https://github.com/OCA/pos" }, { path: "oca/web", url: "https://github.com/OCA/web" }] },
  { repo: "acme-retail", branch: "pos-refunds", stage: "development", version: "18.0", edition: "enterprise", status: "online", createdAgo: 6 * DAY, createdBy: "karim", visitedAgo: 4 * MINUTE, profile: { cpu: 0.9, memory: 0.82 * GIB, rpm: 6, ms: 84, errors: 0 }, upFor: 5 * HOUR, deleteAfter: 30 },
  { repo: "acme-retail", branch: "loyalty-points", stage: "development", version: "18.0", edition: "enterprise", status: "paused", createdAgo: 12 * DAY, createdBy: "ines", visitedAgo: 9 * HOUR, profile: { cpu: 0.7, memory: 0.74 * GIB, rpm: 3, ms: 90, errors: 0 }, upFor: 0, deleteAfter: 30 },
  { repo: "nile-logistics", branch: "main", stage: "production", version: "17.0", edition: "community", status: "online", createdAgo: 210 * DAY, createdBy: "omar", visitedAgo: 0.5 * MINUTE, profile: { cpu: 4.2, memory: 1.41 * GIB, rpm: 64, ms: 48, errors: 0.05 }, upFor: 23 * DAY, autoUpdate: false, autoPause: false, deleteAfter: 0, submodules: [{ path: "oca/fleet", url: "https://github.com/OCA/fleet" }] },
  { repo: "nile-logistics", branch: "fleet-gps", stage: "development", version: "17.0", edition: "community", status: "online", createdAgo: 3 * DAY, createdBy: "lena", visitedAgo: 2 * MINUTE, profile: { cpu: 1.1, memory: 0.69 * GIB, rpm: 9, ms: 55, errors: 0 }, upFor: 3 * HOUR, deleteAfter: 14, submodules: [{ path: "oca/fleet", url: "https://github.com/OCA/fleet" }] },
  { repo: "nile-logistics", branch: "upgrade-18", stage: "development", version: "18.0", edition: "community", status: "online", createdAgo: 9 * DAY, createdBy: "karim", visitedAgo: 47 * MINUTE, profile: { cpu: 0.8, memory: 0.88 * GIB, rpm: 4, ms: 132, errors: 1.6 }, upFor: 3 * HOUR, deleteAfter: 30 },
  { repo: "zahra-clinic", branch: "main", stage: "production", version: "19.0", edition: "enterprise", status: "online", createdAgo: 31 * DAY, createdBy: "omar", visitedAgo: 1 * MINUTE, profile: { cpu: 3.4, memory: 1.33 * GIB, rpm: 38, ms: 57, errors: 0.02 }, upFor: 6 * DAY, autoUpdate: false, autoPause: false, deleteAfter: 0 },
  { repo: "zahra-clinic", branch: "appointments", stage: "staging", version: "19.0", edition: "enterprise", status: "online", createdAgo: 15 * DAY, createdBy: "lena", visitedAgo: 18 * MINUTE, profile: { cpu: 1.2, memory: 0.97 * GIB, rpm: 11, ms: 66, errors: 0 }, upFor: 2 * HOUR, deleteAfter: 90 },
  { name: "odoo-19-0-community-7c2e", stage: "development", version: "19.0", edition: "community", status: "switched_off", createdAgo: 2 * DAY, createdBy: "sara", visitedAgo: 1 * DAY, profile: { cpu: 0.4, memory: 0.6 * GIB, rpm: 1, ms: 70, errors: 0 }, upFor: 0, deleteAfter: 7, autoUpdate: false },
];

export const instanceUrl = (name: string) => `https://${name}.demo.sudoo.sh`;

/** The demo's managed subdomains (every plan): <branch>.<stage>.<company>.<base>, as Sudoo.sh hands them out. */
export const MANAGED_BASE = "sudoo.cloud";
export const MANAGED_COMPANY = "acme";
const managedLabel = (text: string) => text.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 63);
export function managedName(name: string, branch: string | null | undefined, stage: string, serving = true): Instance["managed_name"] {
  const hostname = `${managedLabel(branch || name)}.${stage}.${MANAGED_COMPANY}.${MANAGED_BASE}`;
  return { hostname, status: serving ? "active" : "issuing", reason: null, serving, url: serving ? `https://${hostname}` : null };
}
export const ASLEEP = ["paused", "switched_off", "stopped"];

function build(seed: Seed): Instance {
  const repo = seed.repo ? REPOS.find((r) => r.name === seed.repo)! : null;
  const name = seed.name ?? `${seed.repo}-${seed.branch}`;
  const created = OPENED - seed.createdAgo;
  const commits = repo ? commitsOf(repo, seed.branch!) : [];
  const head = commits[0];
  const autoPause = seed.autoPause ?? seed.stage !== "production";
  const visit = seed.visitedAgo == null ? null : OPENED - seed.visitedAgo;
  const idle = Number(setting("idle_hours") ?? 6) * HOUR;
  const deleteAfter = seed.deleteAfter ?? 30;
  return {
    name,
    status: seed.status,
    stage: seed.stage,
    project: repo ? projectReference(repo.name) : null,
    url: managedName(name, seed.branch, seed.stage)?.url ?? instanceUrl(name),
    managed_name: managedName(name, seed.branch, seed.stage),
    odoo: { version: seed.version, edition: seed.edition, database: name.replace(/-/g, "_") },
    source: repo ? "repository" : "plain",
    git: {
      repository: repo ? repoUrl(repo.name) : null,
      branch: seed.branch ?? null,
      branch_deleted_on_github: false,
      commit: head ? { sha: head.sha, subject: head.subject, author: head.author, age: ageText(Date.parse(head.when)) } : null,
    },
    settings: { auto_update: seed.autoUpdate ?? !!repo, auto_upgrade: !!repo, auto_pause: autoPause, delete_after_days: deleteAfter },
    usage: { cpu_percent: 0, memory_bytes: 0 },
    uptime: null,
    gateway_error: null,
    drift: null,
    update_failure: null,
    created_at: iso(created),
    last_visit_at: iso(visit),
    pauses_at: autoPause && seed.status === "online" && visit ? iso(visit + idle) : null,
    expires_at: deleteAfter ? iso(created + deleteAfter * DAY) : null,
    current_job: null,
    created_by: seed.createdBy,
    disk: { database_bytes: Math.round((seed.stage === "production" ? 2.4 : 0.6) * GIB * (0.7 + (hash(name) % 60) / 100)), files_bytes: Math.round((seed.stage === "production" ? 3.1 : 0.4) * GIB * (0.6 + (hash(name) % 50) / 100)) },
    recent_commits: commits,
    submodules: (seed.submodules ?? []).map((s) => ({ ...s, sha: sha(s.path + name) })),
    rollback: null,
  };
}

// ---------------------------------------------------------------- jobs

export function addJob(job: Job, log: string) {
  jobs.unshift(job);
  logs.set(job.id, log);
}

function pastJob(
  instance: Instance,
  action: string,
  kind: string,
  steps: string[],
  startedAgo: number,
  seconds: number,
  by: string,
  extra: Partial<Job> = {},
) {
  const started = OPENED - startedAgo;
  const failedAt = extra.status === "failed" ? steps.length - 1 : -1;
  const job: Job = {
    id: hex(12),
    instance: instance.name,
    action,
    kind,
    status: "success",
    progress_percent: 100,
    requested_by: by,
    started_at: iso(started),
    finished_at: iso(started + seconds * SECOND),
    steps: steps.map((title, i) => ({ title, status: i === failedAt ? "failed" : "done" })),
    notes: [],
    error: null,
    ...extra,
  };
  if (job.status === "failed") job.progress_percent = Math.round((100 * failedAt) / steps.length);
  let text = logText(steps, contextOf(instance));
  if (job.error) text += `ERROR ${job.error}\n`;
  addJob(job, text);
  return job;
}

export const CREATE_STEPS = (i: Pick<Instance, "odoo" | "source" | "git">, withModules = true) => [
  `Downloading Odoo ${i.odoo.version} image`,
  "Preparing the instance",
  ...(i.source === "repository" ? ["Downloading the code from GitHub"] : []),
  ...(i.odoo.edition === "enterprise" ? [`Preparing the Odoo ${i.odoo.version} Enterprise add-ons`] : []),
  "Starting the database",
  `Creating the Odoo ${i.odoo.edition === "enterprise" ? "Enterprise" : "Community"} database (about 1-2 minutes)`,
  ...(i.source === "repository" && withModules ? [`Installing the repository's modules (${findRepo(i.git.repository)?.modules.length ?? 1})`] : []),
  "Setting the admin password",
  "Creating the public link",
  "Starting Odoo",
];
export const UPDATE_STEPS = ["Backing up the database and files before the update", "Checking GitHub for new code", "Downloading the latest code from GitHub", "Upgrading modules: changed modules", "Restarting Odoo"];

function seedJobs() {
  for (const instance of instances.values()) {
    const created = OPENED - Date.parse(instance.created_at!);
    pastJob(instance, "Create instance", "create", CREATE_STEPS(instance), created, 214, instance.created_by ?? "omar");
    if (instance.source === "plain") continue;
    const updates = Math.min(5, Math.floor(created / DAY));
    for (let i = updates; i >= 1; i--) {
      pastJob(instance, "Get latest code", "update", UPDATE_STEPS, (i * created) / (updates + 1), 48 + i * 7, i % 2 ? "GitHub push" : "karim");
    }
    if (instance.stage !== "development") pastJob(instance, "Back up", "backup", ["Backing up the database and files"], 26 * HOUR, 31, "lena");
    pastJob(instance, "Restart", "restart", ["Restarting Odoo", "Waiting for Odoo to be ready"], 7 * HOUR + (hash(instance.name) % 300) * MINUTE, 12, "karim");
  }
  jobs.sort((a, b) => Date.parse(b.started_at!) - Date.parse(a.started_at!));
}

// ---------------------------------------------------------------- backups

export function addBackup(instance: Pick<Instance, "name" | "odoo">, kind: BackupKind, at: number, size?: number, comment?: string): Backup {
  const series = instance.odoo.version;
  const head = instances.get(instance.name)?.git.commit?.sha;
  const backup: Backup = {
    file: backupFile(instance.name, instance.odoo.database, at, kind),
    instance: instance.name,
    db: instance.odoo.database,
    stamp: backupFile(instance.name, instance.odoo.database, at, kind).split("__")[2]!,
    kind,
    size: size ?? Math.round((140 + (hash(instance.name + at) % 260)) * MIB),
    created: at / 1000,
    version: series,
    series,
    edition: instance.odoo.edition,
    revision: head ? `${head}${hash(instance.name + head).toString(16)}`.padEnd(40, "0").slice(0, 40) : null,
    comment: comment || null,
  };
  backups.push(backup);
  backups.sort((a, b) => b.created - a.created);
  return backup;
}

function seedBackups() {
  const midnight = new Date(OPENED);
  midnight.setUTCHours(0, 0, 12, 0);
  const lastNight = midnight.getTime() > OPENED ? midnight.getTime() - DAY : midnight.getTime();
  for (const instance of instances.values()) {
    if (instance.status === "online" || instance.stage === "production") addBackup(instance, "daily", lastNight + (hash(instance.name) % 400) * SECOND);
  }
  const by = (name: string) => instances.get(name)!;
  addBackup(by("acme-retail-main"), "manual", OPENED - 3 * DAY - 2 * HOUR, 612 * MIB, "Before importing the 2027 price lists");
  addBackup(by("acme-retail-main"), "manual", OPENED - 17 * DAY, 571 * MIB, "Before the go-live");
  addBackup(by("nile-logistics-main"), "manual", OPENED - 8 * DAY, 438 * MIB);
  addBackup(by("zahra-clinic-main"), "manual", OPENED - 5 * HOUR, 296 * MIB);
  addBackup(by("acme-retail-loyalty-points"), "daily", OPENED - 1 * DAY - 22 * HOUR);
  addBackup(by("nile-logistics-upgrade-18"), "before-update", OPENED - 3 * HOUR - 6 * MINUTE);
  const appointments = by("zahra-clinic-appointments");
  const restored = OPENED - 2 * HOUR - 14 * MINUTE;
  const before = addBackup(appointments, "before-restore", restored);
  appointments.rollback = { backups: [before.file], taken_at: iso(restored)!, until: iso(restored + 24 * HOUR)! };
  pastJob(appointments, "Restore a backup", "restore", ["Saving the current state first", "Stopping Odoo", `Restoring the backup of ${new Date(OPENED - 5 * HOUR).toUTCString().slice(5, 22)} from zahra-clinic-main`, "Neutralising the copied data", "Setting the admin password", "Starting Odoo"], OPENED - restored, 95, "lena", {
    notes: [{ text: "The backup of zahra-clinic-main was neutralised: outgoing mail, scheduled actions and integrations are off.", level: "info" }],
  });
  // an instance deleted last week: its last backup is kept
  addBackup({ name: "acme-retail-black-friday", odoo: { version: "18.0", edition: "enterprise", database: "acme_retail_black_friday" } }, "deletion", OPENED - 9 * DAY - 3 * HOUR, 388 * MIB);
}

// ---------------------------------------------------------------- the update that failed and was put back

function seedFailure() {
  const instance = instances.get("nile-logistics-upgrade-18")!;
  const failedAt = OPENED - 3 * HOUR;
  const job = pastJob(
    instance,
    "Get latest code",
    "update",
    ["Backing up the database and files before the update", "Checking GitHub for new code", "Downloading the latest code from GitHub", "Upgrading modules: nile_wms", `Putting the instance back to ${instance.git.commit!.sha}`],
    3 * HOUR + 6 * MINUTE,
    184,
    "GitHub push",
    {
      status: "failed",
      error: `The update to 9b1e0c4 failed at upgrading modules; the instance was put back to ${instance.git.commit!.sha}. The module nile_wms has an error in its XML files: the field "batch_zone_id" in view stock.picking.form does not exist on Odoo 18.`,
    },
  );
  instance.update_failure = {
    message: job.error!,
    failed_at: iso(failedAt),
    rolled_back: true,
    job: `/api/v1/jobs/${job.id}`,
  };
}

// ---------------------------------------------------------------- audit log and alerts

export const audit: AuditEntry[] = []; // newest first
export const ADDRESSES = ["198.51.100.24", "203.0.113.57", "192.0.2.140", "198.51.100.8"];
export const MY_ADDRESS = "203.0.113.10";

export function record(user: string, event: string, extra: Partial<AuditEntry> = {}, at = Date.now()) {
  audit.unshift({ ts: at / 1000, user, ip: MY_ADDRESS, event, ok: true, ...extra });
}

function seedAudit() {
  const random = seeded(7);
  const people = ["omar", "lena", "karim", "ines", "sara"];
  const names = [...instances.keys()];
  const events: [string, Partial<AuditEntry>][] = [
    ["signed in", {}],
    ["instance action: Get latest code", { instance: "" }],
    ["instance action: Restart", { instance: "" }],
    ["instance action: Back up", { instance: "" }],
    ["credentials viewed", { instance: "" }],
    ["signed in", {}],
    ["instance settings changed", { instance: "", detail: "auto_pause: true" }],
  ];
  let at = OPENED - 4 * MINUTE;
  for (let i = 0; i < 70; i++) {
    const [event, extra] = events[Math.floor(random() * events.length)]!;
    const user = people[Math.floor(random() * people.length)]!;
    const entry: AuditEntry = { ts: at / 1000, user, ip: ADDRESSES[Math.floor(random() * ADDRESSES.length)]!, event, ok: true, ...extra };
    if (extra.instance === "") entry.instance = names[Math.floor(random() * names.length)]!;
    audit.push(entry);
    at -= (8 + random() * 70) * MINUTE;
  }
  const special: AuditEntry[] = [
    { ts: (OPENED - 37 * MINUTE) / 1000, user: "acme.reviewer", ip: "192.0.2.77", event: "refused: /api/v1/instances/acme-retail-main/actions/restart", ok: false, instance: "acme-retail-main" },
    { ts: (OPENED - 2 * HOUR - 14 * MINUTE) / 1000, user: "lena", ip: "198.51.100.24", event: "instance action: Restore a backup", ok: true, instance: "zahra-clinic-appointments", detail: "zahra-clinic-main backup, neutralised" },
    { ts: (OPENED - 5 * HOUR) / 1000, user: "omar", ip: "203.0.113.57", event: "settings changed", ok: true, detail: "MAX_PRODUCTION_INSTANCES=3, ALERT_WEBHOOK_1_URL=(set)" },
    { ts: (OPENED - 6 * HOUR) / 1000, user: "unknown@example.net", ip: "192.0.2.201", event: "sign-in failed", ok: false, detail: "wrong e-mail address or password" },
    { ts: (OPENED - 26 * HOUR) / 1000, user: "omar", ip: "203.0.113.57", event: "account created", ok: true, detail: "acme.reviewer (viewer)" },
    { ts: (OPENED - 30 * HOUR) / 1000, user: "omar", ip: "203.0.113.57", event: "API token created", ok: true, detail: "CI pipeline (instances)" },
  ];
  audit.push(...special);
  audit.sort((a, b) => b.ts - a.ts);
}

export const alerts: Alert[] = [];

function seedAlerts() {
  const at = (ago: number) => (OPENED - ago) / 1000;
  alerts.push(
    {
      id: hex(12), key: "update_failed:nile-logistics-upgrade-18", type: "update_failed", severity: "problem",
      title: "The update of nile-logistics-upgrade-18 failed and was undone",
      text: "The new code broke a module upgrade (nile_wms), so the instance was put back to the commit before. It keeps running the previous code.",
      hint: "Fix the view in nile_wms, push, and get the latest code again.", details: [], instance: "nile-logistics-upgrade-18",
      link: "/i/nile-logistics-upgrade-18/activity", at: at(3 * HOUR), last_at: at(3 * HOUR), count: 1, resolved_at: null,
      delivery: ["e-mailed to 2 of 2 addresses", "webhook 1: delivered (HTTP 200)"],
    },
    {
      id: hex(12), key: "certificate:portal.nile-logistics.com", type: "certificate", severity: "problem",
      title: "Certificate of portal.nile-logistics.com could not be issued",
      text: "The ACME authority could not reach portal.nile-logistics.com over http: Timeout during connect (likely firewall problem)",
      hint: null, details: [], instance: "nile-logistics-main",
      link: "/i/nile-logistics-main/domains", at: at(5 * HOUR), last_at: at(20 * MINUTE), count: 4, resolved_at: null,
      delivery: ["e-mailed to 2 of 2 addresses"],
    },
    {
      id: hex(12), key: "instance_down:acme-retail-staging", type: "instance_down", severity: "recovered",
      title: "acme-retail-staging is back", text: "Instance acme-retail-staging answers again after 7 minutes.", hint: null, details: [],
      instance: "acme-retail-staging", link: "/i/acme-retail-staging", at: at(27 * HOUR), last_at: at(26 * HOUR + 50 * MINUTE), count: 1,
      resolved_at: at(26 * HOUR + 50 * MINUTE), delivery: ["e-mailed to 2 of 2 addresses"],
    },
    {
      id: hex(12), key: "memory", type: "memory", severity: "problem", title: "The server's memory was above 85%",
      text: "Memory use stayed above 85% for 10 minutes while a large import ran on acme-retail-main.", hint: null, details: [],
      instance: null, link: "/", at: at(2 * DAY), last_at: at(2 * DAY - 25 * MINUTE), count: 3, resolved_at: at(2 * DAY - 40 * MINUTE),
      delivery: ["e-mailed to 2 of 2 addresses"],
    },
  );
}

// ---------------------------------------------------------------- tokens, GitHub, plugins

export const tokens: Token[] = [
  { id: "t1c9e2a4", name: "CI pipeline", scope: "instances", created: (OPENED - 30 * HOUR) / 1000, created_by: "omar", last_used: (OPENED - 38 * MINUTE) / 1000 },
  { id: "t7b3f0d1", name: "Status dashboard", scope: "read", created: (OPENED - 40 * DAY) / 1000, created_by: "omar", last_used: (OPENED - 20 * SECOND) / 1000 },
];

export const deployKeys: DeployKey[] = [
  {
    repo: `${OWNER}/legacy-addons`,
    spelling: `${OWNER}/legacy-addons`,
    public_key: "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDemoKeyOnlyNotARealKeyDemoKeyOnly sudoo-demo",
    created: (OPENED - 95 * DAY) / 1000,
    github_url: `https://github.com/${OWNER}/legacy-addons/settings/keys/new`,
  },
];

export const github: GitHubAppState = {
  connected: true,
  app: {
    id: 100001,
    slug: "sudoo-sh-panel-demo",
    name: "Sudoo.sh panel-demo",
    client_id: "Iv23liDEMO0000000000",
    html_url: "https://github.com/apps/sudoo-sh-panel-demo",
    owner: { login: OWNER, type: "Organization" },
    created: (OPENED - 120 * DAY) / 1000,
    hook_url: "https://panel.demo.sudoo.sh/hooks/github-app",
    settings_url: `https://github.com/organizations/${OWNER}/settings/apps/sudoo-sh-panel-demo`,
    install_url: "https://github.com/apps/sudoo-sh-panel-demo/installations/new",
    installations: [
      {
        id: 4242,
        account: OWNER,
        account_type: "Organization",
        repository_selection: "selected",
        settings_url: `https://github.com/organizations/${OWNER}/settings/installations/4242`,
        suspended: false,
        added: (OPENED - 120 * DAY) / 1000,
      },
    ],
  },
  trigger_running: true,
  expected_hook_url: "https://panel.demo.sudoo.sh/hooks/github-app",
  deploy_keys: 1,
};

const OFFICIAL = { name: "Sudoo.sh", slug: "sudoo", verified: true, official: true };
export const plugins: InstalledPlugin[] = [
  {
    id: "com.sudoo.slack-alerts", name: "Chat alerts", version: "1.3.0", type: "notification", tier: "free",
    description: "Alerts as rich messages in Slack, Discord or Teams, with buttons to the job.", publisher: OFFICIAL,
    permissions: ["alerts.read"], enabled: true, status: "active", loaded: true, restart_pending: false, licence: null,
    latest_version: "1.4.0", update_available: true, installed_at: iso(OPENED - 33 * DAY), installed_by: "omar",
  },
  {
    id: "com.sudoo.offsite-backups", name: "Off-site backups", version: "2.1.2", type: "backup", tier: "paid",
    description: "Copies every backup to S3-compatible storage, encrypted, with its own retention.", publisher: OFFICIAL,
    permissions: ["backups.read", "network.outbound"], enabled: true, status: "active", loaded: true, restart_pending: false,
    licence: { state: "active", standing: "good", expires_at: iso(OPENED + 220 * DAY), grace_until: null, run_until: iso(OPENED + 227 * DAY), perpetual: false, phase: "active" },
    latest_version: "2.1.2", update_available: false, installed_at: iso(OPENED - 70 * DAY), installed_by: "omar",
  },
];

// ---------------------------------------------------------------- the visitor's session

export const session = {
  signedIn: true,
  role: "admin" as Role,
  user: ME,
  csrf: hex(32),
};

export function seedAll() {
  for (const seed of SEEDS) {
    const instance = build(seed);
    instances.set(instance.name, instance);
    profiles.set(instance.name, {
      ...seed.profile,
      seed: hash(instance.name),
      since: ASLEEP.includes(seed.status) ? Number.POSITIVE_INFINITY : OPENED - seed.upFor,
      boostFrom: 0,
      boostUntil: 0,
    });
  }
  seedJobs();
  seedBackups();
  seedFailure();
  seedAudit();
  seedAlerts();
  jobs.sort((a, b) => Date.parse(b.started_at!) - Date.parse(a.started_at!));
}
