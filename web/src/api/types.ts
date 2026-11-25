// The documents of /api/v1, as docs/PANEL_API.md describes them. Times are ISO 8601 strings in UTC.

export type Role = "super_admin" | "admin" | "maintainer" | "developer" | "viewer" | "member" | "client";
export type Permission =
  | "instances.view"
  | "instances.logs"
  | "instances.update"
  | "instances.operate"
  | "instances.extend_unlimited"
  | "instances.create"
  | "projects.manage"
  | "projects.access"
  | "teams.manage"
  | "instances.restore"
  | "instances.upgrade"
  | "instances.delete"
  | "instances.credentials"
  | "instances.connect"
  | "instances.resources"
  | "projects.resources"
  | "caught_mail.view"
  | "caught_mail.manage"
  | "backups.manage"
  | "domains.manage"
  | "github.manage"
  | "accounts.manage"
  | "tokens.manage"
  | "audit.view"
  | "plugins.view"
  | "settings.manage"
  | "mail.manage"
  | "offsite.configure"
  | "offsite.restore"
  | "offsite.delete"
  | "instances.shell"
  | "instances.database_shell"
  | "instances.profile"
  | "shells.supervise"
  | "servers.manage"
  | "instances.move"
  | "support_access.manage"
  | "clients.manage"
  | "maintenance.manage";

export interface Profile {
  first_name: string | null;
  last_name: string | null;
  email: string | null;
  phone: string | null;
}
/** "creating": its creation is queued or running (GET answers before its files exist, with the job as current_job) */
/** "unreachable": an instance of another server whose agent is offline (several servers) */
export type InstanceStatus =
  "creating" | "online" | "starting" | "busy" | "paused" | "switched_off" | "stopped" | "unknown" | "unreachable";
export type JobStatus = "queued" | "running" | "success" | "failed";

export interface Envelope<T> {
  data: T;
  meta?: { total?: number; count?: number; limit?: number; offset?: number; [key: string]: unknown };
}

export interface Session extends Profile {
  user: string;
  display_name: string;
  role: Role;
  role_label: string;
  permissions: Permission[];
  csrf_token: string;
  restricted: "password" | "second_factor" | null;
  must_set_up_second_factor: boolean;
  /** the account uses the authenticator app but has no recovery codes: it is asked to make them */
  recovery_codes_needed: boolean;
  brand_color: string;
  /** the web app's language: the account's own choice, or the server's DEFAULT_LANGUAGE */
  language: "en" | "ar";
  /** a super admin must accept the Sudoo.sh Licence Agreement before anything else (/licence) */
  licence_required: boolean;
  /** the address this session belongs to: the panel's, or the client portal's (PORTAL_DOMAIN) */
  address?: "panel" | "portal";
}

export type LicenceUse = "personal" | "company" | "service_provider";

/** The licence agreement this release bundles (GET /licence/text). */
export interface LicenceText {
  version: string;
  effective: string;
  sha256: string;
  url: string;
  version_url: string;
  statements: { accept: string; company: string };
  summary_html: string;
  summary_ar_html: string;
  html: string;
}

/** What the web app sends to accept it (POST /licence/acceptances, or "licence" in POST /setup). */
export interface LicenceAcceptance {
  accepted: true;
  version: string;
  sha256: string;
  use: LicenceUse;
  on_behalf: boolean;
  language: "en" | "ar";
}

export interface LicenceRecord {
  id?: string;
  version: string;
  sha256: string;
  accepted_at: string;
  user: string;
  user_name?: string;
  user_email?: string;
  use?: LicenceUse;
  on_behalf?: boolean;
  surface: "panel_setup" | "panel" | "installer";
  ip?: string;
  user_agent?: string;
  host?: string;
  via?: string;
  installer_version?: string;
  synced_at?: string | null;
}

/** GET /licence: what is required and who accepted; `history` for those who manage settings. */
export interface LicenceStatus {
  bundled_version: string;
  required_version: string;
  pending: boolean;
  update_needed: boolean;
  accepted: LicenceRecord | null;
  history?: LicenceRecord[];
}

export interface RoleInfo {
  name: Role;
  label: string;
  description: string;
  permissions: Permission[];
}

export interface SecondFactorChallenge {
  methods: { name: string; label: string }[];
  sent_to?: string;
}

/** The ways to sign in: password, a code by e-mail (no password), GitHub (auth/methods.py) */
export type SignInMethod = "password" | "email_code" | "github";

/** GET /session/options: which ways the sign-in page offers */
export type SignInOptions = Record<SignInMethod, boolean>;

/** The GitHub account linked to a panel account (only its id, login and verified address are kept) */
export interface GitHubLink {
  id: number;
  login: string;
  email: string | null;
  linked_at: string | null;
}

/** How an account signs in: its methods (an administrator's choice, or the server's default) and which work now */
export interface SignInMethods {
  methods: SignInMethod[];
  own_choice: boolean;
  usable: SignInMethod[];
  offered: SignInOptions;
  github: GitHubLink | null;
  github_login: string | null;
}

export interface JobRef {
  id: string;
  action: string;
  status: JobStatus;
  progress_percent: number | null;
  /** how many steps the job announced it takes; missing from older panels and for jobs that do not know */
  expected_steps?: number | null;
}

/** One step of a job. While it runs, `progress` says how far it is in plain words ("Downloading Odoo 19.0: 2 of 5
 * parts"), with a percentage when known. A job's technical log is never sent: it stays in the server's log. */
export interface JobStep {
  title: string;
  status: "running" | "done" | "failed";
  /** missing from panels older than this web app */
  started_at?: string | null;
  progress?: string | null;
  progress_percent?: number | null;
}

/** Why a job or a build failed (core/failure.py): the step, the plain reason, and only the lines that explain it */
export interface Failure {
  step: string | null;
  reason: string;
  kind: "repository" | "git" | "requirements" | "tests" | "odoo" | "other";
  /** cleaned lines (no server paths, addresses or secrets), at most 60; empty when nothing explains it */
  details: string[];
  /** for kind "repository": the repository the panel could not read (owner/name) */
  repository: string | null;
}

/** An instance's job in progress: the reference with its steps so far */
export interface CurrentJob extends JobRef {
  steps: JobStep[];
}

export interface Job extends JobRef {
  instance: string;
  kind: string;
  requested_by: string;
  started_at: string | null;
  finished_at: string | null;
  steps: JobStep[];
  notes: { text: string; level: "info" | "warning" }[];
  error: string | null;
  /** when it failed (missing from panels older than this web app) */
  failure?: Failure | null;
}

export interface Usage {
  cpu_percent: number;
  memory_bytes: number;
  memory_limit_bytes?: number;
  memory_percent?: number | null;
  requests_per_minute?: number;
  error_rate_percent?: number | null;
  response_ms_avg?: number | null;
  response_ms_p95?: number | null;
  uptime_seconds?: number | null;
  disk_bytes?: number | null;
}

/** The limits an admin can set per stage and per instance (instances/resources.py); 0 is "not set". */
export const RESOURCE_KNOBS = [
  "odoo_workers",
  "odoo_cron_threads",
  "odoo_limit_memory_soft_mb",
  "odoo_limit_memory_hard_mb",
  "odoo_limit_time_cpu",
  "odoo_limit_time_real",
  "odoo_memory_limit",
  "odoo_cpu_limit",
  "db_memory_limit",
  "db_cpu_limit",
  "odoo_limit_time_real_cron",
  "db_max_connections",
] as const;
export type ResourceKnob = (typeof RESOURCE_KNOBS)[number];
export type ResourceLimits = Record<ResourceKnob, number | string>;

/** GET /instances/<name>/resources: priority under load, the limits that apply and where they come from. */
export interface InstanceResources {
  stage: Stage;
  priority: {
    rank: 1 | 2 | 3;
    cpu_weight: number;
    oom_score_adj: number;
    memory_reservation: string;
    db_memory_reservation: string;
  };
  limits: ResourceLimits;
  own: Partial<ResourceLimits>;
  stage_defaults: ResourceLimits;
  odoo_defaults: Partial<Record<ResourceKnob, number>>;
  unlimited: boolean;
  /** the containers run with older values until the instance restarts */
  pending: boolean;
  warnings: { code: string; text: string; values: Record<string, number> }[];
  server: ResourceServer;
  /** the values of the project (for the instance's stage) and of its branch; what applies without the instance's own */
  project?: Partial<ResourceLimits>;
  branch?: Partial<ResourceLimits>;
  inherited?: Partial<ResourceLimits>;
  sources?: Record<ResourceKnob, ResourceLayer>;
  /** container limits computed from the workers (none set) */
  computed?: ResourceKnob[];
  maxima?: ResourceLimits;
  storage?: StorageUse;
}

/** Where a resource value comes from: the stage's default, the project, the branch, the instance itself. */
export type ResourceLayer = "stage" | "project" | "branch" | "instance" | "own";

export interface ResourceServer {
  cores: number;
  memory_bytes: number | null;
  usable_bytes: number | null;
  reserved_bytes: number;
}

/** An instance's database and files (instances/disk_use.py) against the soft quota of its stage. */
export interface StorageUse {
  database_bytes: number | null;
  files_bytes: number | null;
  total_bytes: number | null;
  measured_at: number | null;
  quota_bytes: number | null;
  percent: number | null;
  level: "none" | "ok" | "warning" | "over";
}

/** One stage of a project's resources, or a branch's: its own values, what it inherits and what applies. */
export interface ResourceLayerView {
  own: Partial<ResourceLimits>;
  inherited: Partial<ResourceLimits>;
  applies: ResourceLimits;
  computed: ResourceKnob[];
  sources: Record<ResourceKnob, ResourceLayer>;
}

export interface ResourceInstance {
  name: string;
  branch: string;
  stage: Stage;
  limits: ResourceLimits;
  pending: boolean;
  storage: StorageUse;
}

/** What a change would reserve on the server (PUT ... "preview": true). */
export interface ResourcePlan {
  before_bytes: number;
  after_bytes: number;
  usable_bytes: number | null;
  refusal: string | null;
}

/** GET /projects/<id>/resources */
export interface ProjectResources {
  project: string;
  stages: Record<Stage, ResourceLayerView & { quota_bytes: number | null }>;
  maxima: ResourceLimits;
  odoo_defaults: Partial<Record<ResourceKnob, number>>;
  instances: ResourceInstance[];
  storage: {
    instances: Record<string, StorageUse & { stage: Stage }>;
    by_stage: Record<Stage, number>;
    total_bytes: number;
  };
  server: ResourceServer;
  changed: { by: string; at: number } | null;
}

/** GET /projects/<id>/branches/<branch>/resources */
export interface BranchResources extends ResourceLayerView {
  project: string;
  branch: string;
  stage: Stage;
  maxima: ResourceLimits;
  odoo_defaults: Partial<Record<ResourceKnob, number>>;
  instances: ResourceInstance[];
  quota_bytes: number | null;
  server: ResourceServer;
  changed: { by: string; at: number } | null;
}

/** PUT answer: the resources, what was started, and the instances that apply them at their next start. */
export interface ResourcesChange {
  plan: ResourcePlan;
  jobs: Job[];
  waiting: string[];
}

/** production: never paused or deleted automatically, backed up every day, updated safely; staging and development:
 *  the panel's own rules (instances/stages.py) */
export type Stage = "production" | "staging" | "development";

/** The server an instance runs on: "main" (where the panel runs), or another server with several servers. */
export interface ServerReference {
  id: string;
  name: string;
  region: string;
  online: boolean;
}

/** A server a new instance may go to (GET /instance-plan "servers"; the main one first). */
export interface ServerChoice {
  id: string;
  name: string;
  region: string;
}

/** An instance's name under Sudoo.sh's domain (managed subdomains, every plan): its address once served. */
export interface ManagedName {
  hostname: string;
  /** waiting_dns, issuing, active, renewing, failed, expiring (as custom domains) */
  status: string;
  reason: string | null;
  serving: boolean;
  url: string | null;
}

/** GET /server/managed-subdomains */
export interface ManagedSubdomains {
  setting: boolean;
  linked: boolean;
  enabled: boolean;
  active: boolean;
  base_domain: string | null;
  company: string | null;
  limit: number | null;
  used: number | null;
  held: number;
  checked_at: number | null;
  error: string | null;
  pending_release: string[];
  addresses: { public_ipv4?: string; public_ipv6?: string };
  custom_domains: string;
}

/** Where an instance's Odoo and PostgreSQL run (src/sudoo/engines/) */
export type Engine = "docker" | "kubernetes";

/** A node of the Kubernetes engine's cluster (GET /kubernetes) */
export interface KubernetesNode {
  name: string;
  roles: string[];
  ready: boolean;
  schedulable: boolean;
  version: string;
  address: string;
  cpu: number;
  memory_bytes: number;
  cpu_used: number | null;
  memory_used_bytes: number | null;
  created: string | null;
}

/** GET /kubernetes: the Kubernetes engine (beta) and its cluster */
export interface KubernetesOverview {
  enabled: boolean;
  engines: Engine[];
  nodes: KubernetesNode[];
  reachable: boolean;
  problem: string | null;
  join: { url_set: boolean; minutes: number };
}

/** POST /kubernetes/join-command: shown once */
export interface KubernetesJoin {
  command: string;
  /** Unix time the token stops working */
  expires: number;
  version: string;
}

export interface Instance {
  name: string;
  status: InstanceStatus;
  /** the server it runs on (premium/servers.py) */
  server?: ServerReference;
  /** where it runs on its server: docker, or kubernetes (beta) */
  engine?: Engine;
  stage: Stage;
  /** the project (GitHub repository) it belongs to; null: standalone (plain Odoo) */
  project: ProjectReference | null;
  /** its branch's lock (projects/access.py), or null */
  lock?: BranchLock | null;
  /** its client's switch of its branch (projects/clients.py), or null: the project has no client */
  client_access?: ClientAccess | null;
  url: string | null;
  /** its managed name (every plan), or null */
  managed_name?: ManagedName | null;
  odoo: { version: string; edition: "community" | "enterprise"; database: string };
  /** "plain": the official Odoo only, created without a repository (no code to update, no branch, no submodules) */
  source: "repository" | "plain";
  git: {
    repository: string | null;
    branch: string | null;
    branch_deleted_on_github: boolean;
    commit: { sha: string; subject: string; author: string; age: string } | null;
  };
  settings: { auto_update: boolean; auto_upgrade: boolean; auto_pause: boolean; delete_after_days: number };
  usage: Usage;
  uptime: string | null;
  gateway_error: string | null;
  /** changes made outside the panel: what they are, and whether the panel puts its configuration back */
  drift: { detected_at: string | null; items: string[]; action: "restore" | "warn" } | null;
  /** the last code update failed (until one works): what happened, and whether the instance was put back */
  update_failure: { message: string; failed_at: string | null; rolled_back: boolean; job: string | null } | null;
  created_at: string | null;
  last_visit_at: string | null;
  pauses_at: string | null;
  expires_at: string | null;
  /** stopped at the end of its lifetime (not yet deleted): when, and when it will be deleted */
  expired_at?: string | null;
  /** how long its creation took, in seconds (null: not measured) */
  built_seconds?: number | null;
  deletes_at?: string | null;
  current_job: CurrentJob | null;
  created_by: string | null;
  // only on GET /instances/<name>
  disk?: { database_bytes: number | null; files_bytes: number | null } & Partial<StorageUse>;
  /** `when`: ISO 8601 in UTC; `full_sha`: the whole hash (missing from panels older than this web app) */
  recent_commits?: { sha: string; full_sha?: string; subject: string; when: string; author: string }[];
  submodules?: { path: string; sha: string; url: string }[];
  recent_jobs?: JobRef[];
  backups?: InstanceBackup[];
  /** the backup "Restore the latest backup" puts back */
  restorable_backup?: string | null;
  /** while the state before the last restore is kept (ROLLBACK_HOURS): what Rollback puts back, and until when */
  rollback?: { backups: string[]; taken_at: string; until: string } | null;
  /** a newer copy of its Odoo image is on this server: "Update Odoo and PostgreSQL images" moves it there */
  newer_odoo_image?: boolean;
  /** for callers who may open a shell or see the credentials: the names a command needs (never a password) */
  containers?: { odoo: string; db: string; db_user: string; host: string | null };
}

export interface ProjectReference {
  id: string;
  name: string;
  links: { self: string };
}

export interface ProjectOptions {
  auto_development: boolean;
  max_auto_development: number;
  development_demo_data: boolean;
  staging_on_push: "rebuild" | "keep_data";
  branch_deleted: "keep" | "archive" | "delete";
  upgrade_test_on_push: boolean;
  development_lifetime_days: number;
  staging_lifetime_days: number;
  /** build options: the defaults of every branch (BranchBuildOptions) */
  development_on_new_commit: OnNewCommit;
  module_install: ModuleInstall;
  /** comma-separated module names, for module_install "list" */
  modules: string;
  test_suite: TestSuite;
  /** Odoo's --test-tags, for test_suite "custom" */
  test_tags: string;
  /** staging builds run the tests too, on a throw-away copy of the database */
  staging_tests: boolean;
  /** builds are reported on GitHub as commit statuses (when the GitHub App may write them) */
  commit_statuses: boolean;
  /** a failed deployment of a push e-mails the person who pushed (projects/push_failed.py) */
  email_pusher: boolean;
  /** pull requests get a development instance of their branch (projects/previews.py) */
  pr_previews: boolean;
  /** previews at the same time; 0: no limit */
  pr_preview_limit: number;
  /** pull requests from forks get previews too (their code is untrusted) */
  pr_previews_forks: boolean;
}

/** What a new commit does: the same database updated, a new build from scratch, or only a line in the history */
export type OnNewCommit = "new_build" | "update" | "nothing";
/** Modules of a new development database: the repository's, every module (no tests), or a list */
export type ModuleInstall = "repository" | "all" | "list";
/** Tests of a build: none, the repository's installed modules, or custom --test-tags */
export type TestSuite = "off" | "selection" | "custom";

/** A branch's build options (GET/PUT /projects/<id>/branches/<branch>/options) */
export interface BuildOptionValues {
  on_new_commit: OnNewCommit;
  module_install: ModuleInstall;
  modules: string;
  test_suite: TestSuite;
  test_tags: string;
  demo_data: boolean;
}

/** Where a branch's option comes from: its own value, the project's, or why it is forced */
export type BuildOptionSource = "branch" | "project" | "production" | "module_install" | "empty_list" | "empty_tags";

export interface BranchBuildOptions {
  project: string;
  branch: string;
  stage: Stage;
  /** what applies to the branch now */
  options: BuildOptionValues;
  sources: Record<keyof BuildOptionValues, BuildOptionSource>;
  /** the values the branch sets itself */
  own: Partial<BuildOptionValues>;
  /** what the branch gets without its own values */
  project_defaults: BuildOptionValues;
  changed_by: string | null;
  changed_at: string | null;
  commit_statuses: { enabled: boolean; available: boolean; context: string };
  links: { self: string };
}

/** PUT /projects/<id>/branches/<branch>/options: a value sets it, null follows the project again */
export type BuildOptionsChange = { [K in keyof BuildOptionValues]?: BuildOptionValues[K] | null };

/** A project: one GitHub repository (GET /projects) */
export interface Project {
  id: string;
  name: string;
  repository: { full_name: string; url: string };
  odoo: { version: string; edition: "community" | "enterprise" };
  /** linked: an existing repository; created: a new, empty one made from the panel; adopted: from existing instances */
  origin: "linked" | "created" | "adopted";
  created_at: string | null;
  created_by: string | null;
  branches: { total: number } & Record<Stage, number>;
  instances: { total: number; by_status: Partial<Record<InstanceStatus, number>>; names: string[] };
  /** instances with a failed update, changes made by hand or a branch deleted on GitHub */
  attention: string[];
  production_branches: string[];
  options: ProjectOptions;
  own_options: Partial<ProjectOptions>;
  /** where its new instances go when nobody chooses ("main", or another server's id) */
  default_server?: string;
  /** panel_roles: everyone's role applies; restricted: only the teams and people granted see it */
  access: { mode: AccessMode; grants: number; restricted_branches: string[]; locked_branches: string[] };
  /** the client's switch of the project as a whole (projects/clients.py), or null: no client */
  client_access?: ClientAccess | null;
  links: { self: string };
}

/** A client's switch of a project or a branch (projects/clients.py). "timed": open until `until`. */
export type SwitchState = "open" | "closed" | "timed";
export interface ClientAccess {
  state: SwitchState;
  until: string | null;
  /** "branch": the branch's own switch; "project": it follows the project's */
  scope: "project" | "branch";
  changed_by: string | null;
  changed_at: string | null;
  emergency_allowed: boolean;
  clients: number;
  /** a closure lets the caller through (a client, someone it named, an approved request, an emergency) */
  lets_you_through?: boolean;
  /** the client's lock of the branch or of the whole project, or null */
  locked?: { by: string | null; at: string | null; reason: string; scope: "project" | "branch" } | null;
}
/** A client's major change waiting for its confirmation by e-mail (projects/client_confirm.py). */
export interface ClientConfirmation {
  id: string;
  kind: "switch" | "lock" | "emergency" | "approve" | "allow" | "maintenance_window" | "maintenance_approve";
  summary: string;
  asked_at: string;
  expires_at: string;
  status: "pending" | "confirmed" | "expired" | "cancelled";
  /** only in the demo, where no e-mail is sent: the code */
  demo_code?: string;
}
/** What a client's change answers: done at once, or waiting for the e-mail confirmation (202). */
export interface ClientActionAnswer {
  done: boolean;
  confirmation?: ClientConfirmation;
}
export interface PersonRef {
  name: string;
  display_name: string;
  role?: string;
}
export interface AccessRequest {
  id: string;
  project: string;
  branch: string | null;
  by: PersonRef;
  reason: string;
  hours: number;
  status: "pending" | "approved" | "refused" | "lapsed";
  asked_at: string;
  decided_by: string | null;
  decided_at: string | null;
  people: PersonRef[];
  until: string | null;
  note: string | null;
}
/** Who a closure lets through until when: an approved request or an emergency. */
export interface AccessPass {
  id: string;
  kind: "request" | "emergency";
  branch: string | null;
  people: PersonRef[];
  by: string;
  at: string;
  until: string;
  reason: string | null;
}
/** GET /projects/<id>/clients: what staff see of a project's clients. */
export interface ProjectClients {
  clients: (PersonRef & {
    email: string | null;
    backups: boolean;
    shell: boolean;
    added_by: string | null;
    added_at: string | null;
  })[];
  invited: { id: string; email: string | null; expires_at: string | null }[];
  project: ClientAccess | null;
  branches: Record<string, ClientAccess | null>;
  allow: PersonRef[];
  emergency_allowed: boolean;
  passes: AccessPass[];
  requests: AccessRequest[];
}
export interface ProjectClientsMeta {
  may_manage: boolean;
  may_force: boolean;
  emergency_minutes: number;
  request_hours: number;
  request_max_hours: number;
  portal_url: string | null;
}
/** GET /portal: what the sign-in page needs to look like the client portal. */
export interface PortalInfo {
  address: "panel" | "portal";
  portal: boolean;
  portal_configured: boolean;
  name: string | null;
  brand_color: string;
  default_language: "en" | "ar";
}
export interface ClientProjectSummary {
  id: string;
  name: string;
  switch: ClientAccess | null;
  closed_branches: string[];
  pending_requests: number;
  emergency_active: boolean;
  instances: number;
}
export interface ClientInstance {
  name: string;
  status: InstanceStatus;
  stage: Stage | null;
  url: string | null;
  odoo: string | null;
  branch: string | null;
  current_job?: string | null;
}
export interface ClientBranch {
  name: string;
  stage: Stage;
  switch: ClientAccess | null;
  instances: ClientInstance[];
}
export interface ClientProject extends ClientProjectSummary {
  branches: ClientBranch[];
  allow: PersonRef[];
  people: PersonRef[];
  emergency_allowed: boolean;
  passes: AccessPass[];
  requests: AccessRequest[];
  notices: AccessPass[];
  given: { backups: boolean; shell: boolean };
  /** the client's lock of the whole project, or null */
  locked: ClientAccess["locked"];
  confirmations: ClientConfirmation[];
}
export interface ClientProjectMeta {
  open_hours: number[];
  open_max_hours: number;
  request_max_hours: number;
  emergency_minutes: number;
  confirmations: boolean;
  confirm_minutes: number;
}
export interface ClientBackup {
  file: string;
  instance: string;
  kind: BackupKind;
  size_bytes: number;
  created_at: string;
}
export interface ActivityEntry {
  at: string;
  user: string;
  event: string;
  allowed: boolean;
  branch: string | null;
  instance: string | null;
  detail: string | null;
  address: string | null;
  emergency: boolean;
}

export type AccessMode = "panel_roles" | "restricted";
export type AccessLevel = "none" | "view" | "tester" | "developer" | "maintainer" | "admin";
export interface AccessSubject {
  /** github_team: a team of a GitHub organisation, "org/slug" (its members are read from GitHub) */
  type: "team" | "user" | "github_team";
  id: string;
  /** the team's or account's name; null when it no longer exists */
  name?: string | null;
}
export interface Grant extends AccessSubject {
  level: AccessLevel;
}
/** A locked branch: nobody but whoever `allow` names may open, change, deploy, back up, restore or delete it */
export interface BranchLock {
  locked_by: string | null;
  locked_at: number | null;
  reason: string;
  allow: AccessSubject[];
}
/** GET /projects/<id>/access */
export interface ProjectAccess {
  mode: AccessMode;
  grants: Grant[];
  branches: Record<string, { restricted: boolean; grants: Grant[] }>;
  locks: Record<string, BranchLock>;
  levels: AccessLevel[];
  /** project access from GitHub repository permissions (auth/github_access.py) */
  github: {
    enabled: boolean;
    repository: string;
    people: {
      user: string;
      name: string;
      github_login: string;
      applies: boolean;
      permission: string | null;
      level: AccessLevel | null;
      covered: boolean | null;
      checked: number | null;
      /** the repository's team that gives the permission, when one does */
      via?: string | null;
    }[];
  };
  /** what GitHub's read permission gives in this project: viewer, or tester (projects made before the setting) */
  github_read_role: "viewer" | "tester";
  /** project_roles: per-stage roles; legacy: the panel role limited by the level (projects made before them) */
  model: "project_roles" | "legacy";
  roles: ProjectRoles;
  requests_enabled: boolean;
  request_max_hours: number;
}

export type RoleScope = "production" | "staging" | "development" | "project";
/** What each project role may do in each stage (projects/project_roles.py); `changed`: rows this project changed */
export interface ProjectRoles {
  levels: AccessLevel[];
  editable: AccessLevel[];
  stages: RoleScope[];
  rights: { stage: Permission[]; project: Permission[] };
  labels: Record<string, string>;
  matrix: Record<string, Record<RoleScope, Permission[]>>;
  changed: Record<string, RoleScope[]>;
}

/** GET /projects/<id>/access/people: who can do what */
export interface AccessPerson {
  user: string;
  name: string;
  panel_role: Role;
  level: AccessLevel;
  source: "admin" | "grant" | "team" | "github" | "role" | "none";
  source_label: string;
  stages: Record<"production" | "staging" | "development", Permission[]>;
  project: Permission[];
  temporary: { id: string; stage: Stage; level: AccessLevel; expires: number | null }[];
}

/** Temporary access asked for in a project (a higher project role in one stage, projects/elevation.py) */
export interface RoleRequest {
  id: string;
  user: string;
  name: string;
  stage: Stage;
  level: AccessLevel;
  hours: number;
  reason: string;
  status: "pending" | "approved" | "refused" | "withdrawn" | "expired";
  asked_at: string | null;
  decided_by: string | null;
  decided_at: string | null;
  expires_at: string | null;
  note: string;
}

/** A team: a named group of accounts that project access is given to (GET /teams) */
export interface Team {
  id: string;
  name: string;
  description: string;
  members: { name: string; display_name: string; role: Role }[];
  projects: { id: string; name: string }[];
  created_at: string | null;
  created_by: string | null;
  changed_at: string | null;
  changed_by: string | null;
}

export interface ProjectBranch {
  name: string;
  stage: Stage;
  default: boolean;
  /** null: GitHub could not be read */
  on_github: boolean | null;
  commit: { sha: string; full_sha: string; subject: string; when: string; author: string } | null;
  instances: string[];
  auto_created: string | null;
  /** the branch's own Odoo version, which its new instances get; null: the project's */
  odoo_version: string | null;
  lock: BranchLock | null;
  /** the client's switch of this branch (projects/clients.py), or null: the project has no client */
  client_access?: ClientAccess | null;
  /** seen only by the teams and people its own grants name */
  restricted: boolean;
  /** what the caller may do in this branch (role ∩ access, the lock) */
  permissions?: Permission[];
}

export type BuildStatus = "building" | "success" | "warning" | "failed" | "dropped";
/** what started a build: the instance's creation, a push, someone (Get latest code), a rebuild from production */
export type BuildTrigger = "create" | "push" | "manual" | "rebuild";

interface HistoryEntryBase {
  id: string;
  project: string;
  branch: string;
  /** when the entry was recorded (ISO 8601) */
  at: string | null;
  /** account name, or "system" */
  by: string | null;
  links: { self: string; job: string | null; instance?: string | null };
}

/** A build of a branch: one commit an instance was built from (projects/builds.py) */
export interface Build extends HistoryEntryBase {
  type: "build";
  instance: string;
  trigger: BuildTrigger;
  status: BuildStatus;
  /** null until the job has the code */
  commit: { sha: string; full_sha: string; subject: string; author: string; when: string } | null;
  action: string | null;
  /** a sentence: "Instance created", "1 warning in Odoo's log while loading the modules", the failure */
  result: string;
  /** Odoo's warning and error lines (the first ten) */
  messages: string[];
  /** the job (its steps) while the panel keeps it (the last 300 jobs) */
  job: string | null;
  /** why it failed; null unless it did */
  failure?: Failure | null;
  /** a build of a push that failed: whether the person who pushed was e-mailed (projects/push_failed.py) */
  notified?: {
    emailed: boolean;
    reason?: "option_off" | "no_address" | "already" | "mail_failed" | "no_project";
    at?: number;
  } | null;
  queued_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  duration_seconds: number | null;
  dropped_at: string | null;
  /** the build's tests (projects/testing.py); null when it ran none */
  tests?: BuildTests | null;
}

/** What a build's tests found: the counts of Odoo's summary and the modules with a failing test */
export interface BuildTests {
  status: "success" | "warning" | "failed";
  tests: number;
  failed: number;
  errors: number;
  warnings: number;
  modules: string[];
}

/** A change of a branch's stage */
export interface StageChange extends HistoryEntryBase {
  type: "stage";
  from: Stage;
  to: Stage;
  /** what the new stage does */
  explanation: string;
  /** null for the branch's own stage change; else what made it (a database upgrade, an instance's stage) */
  reason: string | null;
}

/** A backup restored into an instance of the branch (or a restore rolled back) */
export interface RestoreEntry extends HistoryEntryBase {
  /** "refresh": a scheduled refresh of staging from production (projects/refresh.py) */
  type: "restore" | "refresh";
  instance: string;
  status: "success" | "failed";
  action: string | null;
  result: string;
  job: string | null;
  failure?: Failure | null;
}

/** A commit pushed to a branch that builds nothing on new commits (on_new_commit "nothing"); the history lists
 * these only with ?commits=1, which the branch's timeline asks for */
export interface CommitEntry extends HistoryEntryBase {
  type: "commit";
  instance: string | null;
  status: "skipped";
  commit: { sha: string; full_sha: string; subject: string; author: string; when: string } | null;
  result: string;
}

export type HistoryEntry = Build | StageChange | RestoreEntry | CommitEntry;

/** GET /projects/<id>/builds: the last builds of each branch, newest first */
export interface BranchBuilds {
  branch: string;
  stage: Stage;
  builds: Build[];
}

/** The "build" live event: an entry, or an update that found nothing new (no build) */
export type BuildEvent = HistoryEntry | { id: string; project: string; branch: string; removed: true };

/** GET /projects/<id>: the project with its branches */
export interface ProjectDetail extends Project {
  branches_list: ProjectBranch[];
}

export interface ProjectMeta {
  default_branch: string | null;
  branches_error: string | null;
  branches_read_at: string | null;
  capacity: {
    comfortable: number;
    running: number;
    headroom: number;
    warning: boolean;
    message: string | null;
    overloaded: boolean;
  } | null;
  production_limit: number | null;
  server_production_limit: number | null;
  /** whether the GitHub App may write commit statuses in the repository */
  commit_statuses?: { available: boolean };
  /** what the caller may do in the project as a whole */
  permissions?: Permission[];
}

/** GET /projects/repository-owners: where a new, empty repository can be created */
export interface RepositoryOwner {
  account: string;
  account_type: string;
  possible: boolean;
  reason: "ok" | "no_app" | "not_installed" | "suspended" | "personal_account" | "permission_missing";
  message: string;
  url: string;
  label: string;
  accept_url?: string;
}

export interface Credentials {
  login: string;
  admin_password: string;
  master_password: string;
  ports: Record<string, number>;
}

export interface ServerFigures {
  cpu_percent: number;
  cores: number;
  load_1m: number;
  memory_used_bytes: number;
  memory_total_bytes: number;
  memory_percent: number;
  disk_used_bytes: number;
  disk_total_bytes: number;
  disk_percent: number;
  uptime_seconds: number;
  instances_running: number;
  instances_total: number;
  /** the panel's own use: backend, web service and Cloudflare connectors; now and at most over the history */
  panel_cpu_percent: number;
  panel_memory_bytes: number;
  panel_cpu_peak_percent: number;
  panel_memory_peak_bytes: number;
}

/** GET /server/capacity, and `capacity` of the metrics: how many instances the server runs comfortably. */
export interface Capacity {
  comfortable: number;
  running: number;
  headroom: number;
  limited_by: "memory" | "processor";
  warning: boolean;
  message: string | null;
  overloaded: boolean;
  alert: string | null;
  memory_percent: number | null;
  warning_percent: number;
  hard_limit: number | null;
  blocked: boolean;
  memory: {
    total_bytes: number;
    reserve_bytes: number;
    panel_bytes: number;
    usable_bytes: number;
    per_instance_bytes: number;
    per_instance_source: "measured" | "default";
    instance_average_bytes: number | null;
    instance_peak_bytes: number | null;
    instance_largest_bytes: number | null;
    measured_instances: number;
    instances: number;
  };
  processor: { cores: number; panel_percent: number; per_instance_percent: number | null; instances: number | null };
  disk: { free_bytes: number; instance_average_bytes: number | null; more_instances: number | null };
  by_version: { version: string; instances: number; average_bytes: number; peak_bytes: number }[];
  /** memory reserved under load by all instances (their stage's mem_reservation), by stage */
  reservations?: { total_bytes: number; by_stage: Record<Stage, number> };
  panel: {
    cpu_percent: number | null;
    cpu_peak_percent: number | null;
    memory_bytes: number | null;
    memory_peak_bytes: number | null;
    parts: Record<string, { cpu_percent: number; memory_bytes: number; containers: number }>;
  };
}

export interface Metrics {
  at: number | null;
  interval_seconds: number;
  server: Partial<ServerFigures>;
  instances: Record<string, Usage & { running: boolean }>;
  capacity: Capacity | null;
}

export type Columns = { t: number[] } & Record<string, (number | null)[]>;

export interface MetricsHistory {
  server: Columns;
  instances: Record<string, Columns>;
}

export interface Server {
  version: string;
  panel_url: string | null;
  plugins: { exposure: string; enterprise_source: string; update_triggers: string[] };
  /** how people reach this server (EXPOSURE) and the panel's own address */
  public_address?: {
    mode: string;
    url: string | null;
    domain: string | null;
    /** where the panel's name comes from */
    source?: "panel_domain" | "public_host" | "managed" | null;
    status: "waiting" | "active" | "failed" | null;
    reason: string | null;
    server_addresses: string[];
    /** the panel's own managed name from Sudoo.sh (panel.<company>.<base>) */
    managed_name?: {
      setting: boolean;
      wanted: boolean;
      offered: boolean;
      hostname: string | null;
      held: string | null;
      counts: boolean;
      checked_at: number | null;
      error: string | null;
    };
  };
  metrics: Partial<ServerFigures>;
  live_clients: number;
}

export interface Setting {
  key: string;
  env: string;
  value: string | number | boolean;
  env_value: string | number | boolean;
  changed_in_panel: boolean;
  editable: boolean;
  applies: "now" | "restart" | "env";
  pending_restart: boolean;
  secret: boolean;
  type: "boolean" | "integer" | "number" | "string";
  minimum: number | null;
  description: string | null;
}

/** An alert (GET /api/v1/alerts, live event "alert"): something that needed attention, or the notice it is over. */
export interface Alert {
  id: string;
  key: string;
  type:
    | "job_failed"
    | "update_failed"
    | "backup_failed"
    | "instance_down"
    | "disk"
    | "memory"
    | "drift"
    | "public_link"
    | "certificate"
    | "test"
    | "default_admin_password"
    | "country_database";
  severity: "problem" | "recovered" | "info";
  title: string;
  text: string;
  hint: string | null;
  details: string[];
  instance: string | null;
  /** a path of the web app, e.g. /i/shop/activity?job=abc */
  link: string | null;
  at: number;
  last_at: number;
  count: number;
  resolved_at: number | null;
  delivery: string[];
}

export interface Version {
  version: string;
  enterprise: boolean;
}

export interface Plan {
  name: string;
  database: string;
  login: string;
  defaults: PlanDefaults;
  /** what a new instance of each stage starts with */
  stage_defaults: Record<Stage, PlanDefaults>;
  /** how many production instances there may be (0: no limit) and are, and why one more is refused */
  production: { limit: number; instances: string[]; refusal: string | null };
  /** where a new instance may go: the main server, and with several servers every online one with room */
  servers?: ServerChoice[];
  /** the engines a new instance may run on: "docker", and "kubernetes" while its beta is on */
  engines?: Engine[];
}

export type PlanDefaults = {
  delete_after_days: number;
  auto_update: boolean;
  auto_upgrade: boolean;
  auto_pause: boolean;
};

export interface Quota {
  instances: number;
  max_instances: number | null;
  own_instances: number;
  max_instances_per_user: number | null;
  cooldown_minutes: number | null;
  next_creation_at: number | null;
}

export interface Factor {
  name: string;
  label: string;
  enrolled: boolean;
  added_at: string | null;
}

export interface Account extends Profile {
  name: string;
  display_name: string;
  role: Role;
  role_label: string;
  email: string | null;
  password_managed_here: boolean;
  second_factors: Factor[];
  second_factor_required: boolean;
  require_second_factor: "none" | "admins" | "everyone";
  recovery_codes: RecoveryCodes | null;
  recovery_codes_needed: boolean;
  sign_in_email: SignInEmail;
  /** The person's own choice for the e-mails about instances that expire; null: the default (on). */
  expiry_email: boolean | null;
  sign_in_methods: SignInMethods;
  /** the web app's language: in use, the person's own choice (null: the server's default) and that default */
  language: { value: "en" | "ar"; own_choice: "en" | "ar" | null; default: "en" | "ar" };
}

/** The account's recovery codes: how many are left of how many made, and when */
export interface RecoveryCodes {
  left: number;
  total: number;
  created_at: string | null;
}

/** POST /account/recovery-codes: the new codes, shown this once */
export interface NewRecoveryCodes extends RecoveryCodes {
  codes: string[];
}

/** Whether the account gets the "new sign-in" e-mail, and what the person may change */
export interface SignInEmail {
  enabled: boolean;
  own_choice: boolean | null;
  default: boolean;
  can_turn_off: boolean;
  mail_ready: boolean;
}

export type PlaceholderKind = "text" | "link" | "block";

/** One e-mail the panel sends, on the E-mail templates page (GET /mail-templates) */
export interface MailTemplate {
  key: string;
  name: string;
  description: string;
  recipients: string;
  subject: string;
  title: string;
  body: string;
  default: { subject: string; title: string; body: string };
  customized: boolean;
  changed_at: string | null;
  changed_by: string | null;
  placeholders: { name: string; description: string; kind: PlaceholderKind }[];
  required: string[];
}

export interface MailPreview {
  subject: string | null;
  html: string | null;
  text: string | null;
  problems: string[];
}

/** A pending (or expired) invitation, as Accounts lists it: GET /invitations. */
export interface Invitation {
  id: string;
  email: string;
  first_name: string | null;
  last_name: string | null;
  role: Role;
  role_label: string;
  invited_by: string;
  sent_count: number;
  expired: boolean;
  invited_at: string;
  sent_at: string;
  expires_at: string;
  github_login?: string | null;
  /** only in the demo, where no e-mail is sent: the invitation's link */
  link?: string;
}
export interface InvitationPolicy {
  mail_ready: boolean;
  /** whether a password may be set instead of inviting (INVITATION_PASSWORD_FALLBACK) */
  password_allowed: boolean;
  invitation_hours: number;
}
/** What an invitee sees before accepting: POST /invitation. */
export interface InvitationPreview {
  email: string;
  first_name: string | null;
  last_name: string | null;
  role: Role;
  role_label: string;
  invited_by: string;
  expires_at: string;
  second_factor_required: boolean;
}

export interface User extends Profile {
  name: string;
  display_name: string;
  role: Role;
  role_label: string;
  last_sign_in_at: string | null;
  password_changed_at: string | null;
  must_change_password: boolean;
  second_factors: string[];
  sign_in_methods: SignInMethod[];
  /** false: the server's default (SIGN_IN_METHODS) applies */
  sign_in_methods_chosen: boolean;
  github: GitHubLink | null;
  /** the GitHub user name an administrator expects, linked at the first sign-in with GitHub */
  github_login: string | null;
  has_password: boolean;
}

export interface DocsContents {
  index: string;
  sections: { title: string; pages: { path: string; title: string }[] }[];
}

export interface DocPage {
  path: string;
  title: string;
  html: string;
  headings: { level: number; id: string; text: string }[];
}

export interface Token {
  id: string;
  name: string;
  scope: "read" | "instances" | "developer" | "write";
  created: number;
  created_by: string;
  last_used: number | null;
}

export type BackupKind =
  | "manual"
  | "daily"
  | "deletion"
  | "before-restore"
  | "before-rollback"
  | "before-repair"
  | "before-update"
  | "staging-copy"
  | "before-upgrade";
/** GET /offsite: where off-site copies go, and the panel's signing key (never the storage's secret) */
export interface OffsiteState {
  enabled: boolean;
  /** address, bucket, access key ID and secret are set */
  configured: boolean;
  /** enabled and configured: backups are being copied */
  active: boolean;
  /** bucket/folder */
  location: string | null;
  endpoint: string | null;
  other_stages: boolean;
  kinds: string[];
  /** PEM; null before off-site copies were first switched on */
  public_key: string | null;
  /** SHA256:<hex of the DER public key> */
  fingerprint: string | null;
  last_scan: { at: number; found: number; missing: number } | null;
}

export type OffsiteStatus = "uploading" | "stored" | "verifying" | "verified" | "failed";

export interface OffsiteCopy {
  /** <instance>__<stamp>__<kind> */
  id: string;
  instance: string;
  stage: string | null;
  kind: BackupKind;
  stamp: string;
  /** when the backup was taken (seconds) */
  created: number | null;
  status: OffsiteStatus;
  /** why it failed, or why its last check could not finish */
  reason: string | null;
  size: number;
  files: { name: string; db: string | null; size: number | null }[];
  location: string;
  uploaded_at: number | null;
  verified_at: number | null;
  /** the fingerprint of the key it was last verified with */
  verified_with: string | null;
  /** the job that last copied or checked it */
  job: string | null;
  /** found by reading the bucket's list (after a rebuild) rather than copied by this panel */
  found_in_bucket: boolean;
}

export interface Backup {
  file: string;
  instance: string;
  db: string;
  stamp: string;
  kind: BackupKind;
  size: number;
  created: number;
  /** from the backup's manifest.json; null when it does not say */
  version: string | null;
  /** the Odoo series the database belongs to ("17.0"): it only goes into an instance of the same series */
  series: string | null;
  edition: "community" | "enterprise" | null;
  /** the commit the instance's code was at, from the manifest */
  revision?: string | null;
  /** given with a manual backup */
  comment?: string | null;
}

/** A backup as an instance lists it. */
export interface InstanceBackup {
  file: string;
  kind: BackupKind;
  database: string;
  size_bytes: number;
  created_at: string;
  /** the commit the instance's code was at (recorded since backups have it) */
  revision?: string | null;
  /** given with a manual backup */
  comment?: string | null;
}

export interface DeployKey {
  repo: string;
  spelling: string;
  public_key: string;
  created: number;
  github_url: string;
}

export interface AuditEntry {
  ts: number;
  user: string;
  ip: string | null;
  event: string;
  ok: boolean;
  instance?: string;
  detail?: string;
  /** the project and branch the entry is about, when there is one */
  project?: string;
  branch?: string;
  /** done by Sudoo.sh support through support access */
  support?: boolean;
}

/** GET /audit/facets: what the audit log's filters can offer. */
export interface AuditFacets {
  users: string[];
  events: string[];
  projects: { id: string; name: string }[];
  /** AUDIT_EXPORT_MAX_ROWS */
  export_max_rows: number;
}

export interface GitHubInstallation {
  id: number;
  account: string;
  account_type: "User" | "Organization" | string;
  repository_selection: "all" | "selected" | string;
  settings_url: string;
  suspended: boolean;
  added: number;
}

export interface GitHubApp {
  id: number;
  slug: string;
  name: string;
  client_id: string;
  html_url: string;
  owner: { login: string; type: string };
  created: number;
  hook_url: string | null;
  settings_url: string;
  install_url: string;
  installations: GitHubInstallation[];
}

export interface GitHubAppState {
  connected: boolean;
  app: GitHubApp | null;
  trigger_running: boolean;
  expected_hook_url: string | null;
  deploy_keys: number;
}

export interface GitHubRepository {
  full_name: string;
  url: string;
  private: boolean;
  default_branch: string;
  description: string;
  pushed_at: string | null;
  account: string;
}

export interface RepoAccess {
  repositories: {
    url: string;
    role: "main" | "submodule";
    ok: boolean;
    problem: string | null;
    via?: "deploy-key" | "github-app" | "public";
    /** owner/name, for creating a deploy key */
    repo?: string | null;
    /** what keeps the GitHub App from this repository and where to fix it; url is a panel path when it starts with "/" */
    fix?: { reason: string; url: string; label: string } | null;
  }[];
  all_ok: boolean;
}

// ---------------------------------------------------------------- plugins and the marketplace (docs/MARKETPLACE.md)

export interface Publisher {
  name: string;
  slug: string;
  verified: boolean;
  official: boolean;
}

export interface PricePlan {
  id: string;
  kind: "monthly" | "yearly" | "fixed_term" | "one_time";
  price_cents: number;
  currency: string;
  period_days: number | null;
  interval: "month" | "year" | "days" | null;
  perpetual: boolean;
  monthly_equivalent_cents: number | null;
  savings_percent: number | null;
}

export interface PluginVersionInfo {
  version: string;
  min_sudoo_version?: string;
  max_sudoo_version?: string | null;
  published_at?: string | null;
}

export type CatalogAction = "install" | "update" | "installed" | "buy" | "renew" | "incompatible";

/** One plugin of GET /marketplace/catalog: the hub's catalogue for this panel. */
export interface CatalogPlugin {
  id: string;
  name: string;
  description: string;
  type: string;
  tier: "free" | "paid";
  tags: string[];
  icon_url?: string | null;
  avg_rating: number;
  rating_count?: number;
  install_count: number;
  publisher: Publisher | null;
  countries?: string[];
  languages?: string[];
  odoo_versions?: string[];
  local?: boolean | null;
  requires: { id: string; version: string | null }[];
  plans: PricePlan[];
  latest_version: PluginVersionInfo | null;
  compatible_version: PluginVersionInfo | null;
  installed_version: string | null;
  license: { state: string; expires_at: string | null; grace_until: string | null; perpetual?: boolean } | null;
  action: CatalogAction;
}

/** GET /marketplace/recommendations: a plugin the hub suggests, and why. */
export interface Recommendation {
  plugin: Omit<
    CatalogPlugin,
    "plans" | "latest_version" | "compatible_version" | "installed_version" | "license" | "action"
  >;
  score: number;
  reasons: string[];
}

/** GET /marketplace/catalog/<id>: the plugin's public page. */
export interface CatalogPluginDetail {
  id: string;
  homepage?: string | null;
  permissions: string[];
  plans?: PricePlan[];
}

export interface Billing {
  payments_enabled: boolean;
  linked: boolean;
  account_name: string | null;
  can_buy: boolean;
  reason: "payments_disabled" | "link_required" | "fingerprint_mismatch" | "installation_suspended" | null;
  currency: string;
  manage_url: string | null;
}

export type LicencePhase =
  "active" | "perpetual" | "expiring" | "grace" | "ended" | "no_lease" | "withheld" | "unlicensed";

export interface InstalledPlugin {
  id: string;
  name: string;
  version: string;
  type: string | null;
  tier: "free" | "paid";
  description: string;
  publisher: Publisher | null;
  permissions: string[];
  enabled: boolean;
  status: "active" | "disabled" | "modified" | "blocked" | "withheld" | "switched_off" | "unlicensed";
  loaded: boolean;
  restart_pending: boolean;
  licence: {
    state: string | null;
    standing: string | null;
    expires_at: string | null;
    grace_until: string | null;
    run_until: string | null;
    perpetual: boolean;
    phase: LicencePhase | null;
  } | null;
  latest_version: string | null;
  update_available: boolean;
  installed_at: string | null;
  installed_by: string | null;
}

export interface PluginNotice {
  plugin: string | null;
  kind: string;
  level: "info" | "warning" | "danger";
  text: string;
  date: string | null;
}

export interface MarketplaceStatus {
  enabled: boolean;
  hub_url: string;
  installation_id: string | null;
  registered: boolean;
  reachable: boolean | null;
  error: string | null;
  error_code: string | null;
  hub_status: string | null;
  hub_keys: string[];
  flags: string[];
  linked: { account_name: string | null; linked_at?: string | null } | null;
  key_revoked_at: string | null;
  removing_paid_plugins: boolean;
  last_heartbeat_at: string | null;
  next_heartbeat_at: string | null;
  lease_expires_at: string | null;
  lease_valid: boolean;
  lease_ending_soon: boolean;
}

/** A checkout session: the account owner pays it on app.sudoo.sh (`url`); the panel never charges anything. */
export interface CheckoutSession {
  id: string;
  plugin_id: string | null;
  plugin_name: string | null;
  plan_id: string | null;
  plan_kind: string | null;
  plan: PricePlan | null;
  amount_cents: number | null;
  currency: string | null;
  status: "open" | "processing" | "checkout_required" | "succeeded" | "expired" | "cancelled" | "failed" | null;
  method: "saved_card" | "checkout" | null;
  reason: string | null;
  url: string | null;
  expires_at: string | null;
  receipt_number: string | null;
  requested_by: { name: string | null; email: string | null };
  created_at: string | null;
  completed_at: string | null;
}

export interface Receipt {
  id: string;
  kind: "receipt" | "credit_note";
  number: string;
  issued_at: string | null;
  plugin_id: string | null;
  plugin_name: string | null;
  plan_kind: string | null;
  payment_kind: "purchase" | "renewal" | null;
  total_cents: number;
  currency: string;
  requested_by_name: string | null;
}

// ---------------------------------------------------------------- custom domains (GET /instances/<name>/domains)

export type DomainStatus = "waiting_dns" | "issuing" | "active" | "renewing" | "expiring" | "failed";

export interface DnsRecord {
  type: "CNAME" | "A" | "AAAA";
  name: string;
  value: string;
}

export interface CustomDomain {
  domain: string;
  primary: boolean;
  status: DomainStatus;
  reason: string | null;
  tls: "acme" | "external";
  dns_records: DnsRecord[];
  addresses: string[];
  added_at: string | null;
  added_by: string | null;
  checked_at: string | null;
  next_check_at: string | null;
  renews_at: string | null;
  certificate: { issued_at: string | null; expires_at: string | null; issuer: string | null; staging: boolean } | null;
  url: string | null;
  /** The server the domain moved here from with its instance (several servers), or null. */
  moved_from?: string | null;
  /** Its DNS does not point at this server yet: `reason` says where to point it, `dns_records` are this server's. */
  dns_pending?: boolean;
}

export interface Domains {
  mode: "off" | "acme" | "external";
  limit: number;
  default_url: string | null;
  target: { host: string; record_type: "CNAME" | "A" | "AAAA" } | null;
  exposure: string;
  listener_error: string | null;
  authority: "production" | "staging" | "custom";
  domains: CustomDomain[];
  managed?: boolean;
  premium?: string | null;
}

// ---------------------------------------------------------------- web shell (GET /api/v1/instances/<name>/shell)

export type ShellTarget = "shell" | "odoo-shell" | "psql";
export type ShellChoice =
  { allowed: true; user: string; recorded: boolean } | { allowed: false; code: string; reason: string };
export interface ShellOptions {
  enabled: boolean;
  production: boolean;
  running: boolean;
  status: string;
  targets: Record<ShellTarget, ShellChoice>;
  root: { allowed: true; recorded: boolean } | { allowed: false; code: string; reason: string };
  limits: { idle_minutes: number; max_minutes: number; per_user: number; per_instance: number };
  open: number;
  open_mine: number;
}
export interface ShellTicket {
  ticket: string;
  expires_in: number;
  socket: string;
  protocol: string;
  target: ShellTarget;
  container: string;
  container_user: string;
  root: boolean;
  production: boolean;
  recorded: boolean;
}
/** An open or past shell (GET /api/v1/shells, super admins) */
export interface ShellSession {
  id: string;
  instance: string;
  target: ShellTarget;
  container_user: string;
  root: boolean;
  production: boolean;
  user: string;
  ip: string | null;
  started_at: string;
  ended_at: string | null;
  duration_seconds: number | null;
  reason: string | null;
  recorded: boolean;
  /** a recording can be played */
  recording: boolean;
  open: boolean;
}
/** asciicast v2: seconds since the start, "o" output, "i" input, "r" new size, "m" a note */
export type ShellEvent = [number, "o" | "i" | "r" | "m", string];
export interface ShellRecording {
  session: ShellSession | null;
  header: { version: number; width: number; height: number; timestamp: number; title?: string };
  events: ShellEvent[];
}

/** GET /help: what the help panel shows besides its own texts. */
export interface HelpInfo {
  version: string;
  docs_url: string;
  support: { kind: "hub" | "link" | "email" | "none"; url: string | null; name?: string };
}

/** One e-mail caught from a staging or development instance (GET /instances/<name>/mail). */
export interface CaughtMail {
  id: string;
  /** seconds since 1970 */
  received_at: number;
  subject: string;
  from: string;
  to: string[];
  cc: string[];
  mail_from: string;
  rcpt_to: string[];
  date: string;
  size: number;
  has_html: boolean;
  attachments: number;
  snippet: string;
}

export interface CaughtMailAttachment {
  index: number;
  filename: string;
  content_type: string;
  size: number;
}

export interface CaughtMailDetail extends CaughtMail {
  headers: [string, string][];
  text: string;
  attachment_list: CaughtMailAttachment[];
  /** the demo only: the HTML itself (the real panel serves it on its own address, for a sandboxed frame) */
  html?: string;
}

export interface CaughtMailMeta {
  total: number;
  count: number;
  limit: number;
  offset: number;
  catcher: { running: boolean; exists: boolean };
  usage: { messages: number; bytes: number };
  limits: { retention_days: number; max_messages: number; max_mb: number; max_message_mb: number };
  release: boolean;
}

/** GET /plan: the panel's Sudoo.sh plan from the hub's signed licence (src/sudoo/entitlements.py) */
export interface PlanStatus {
  plan: string;
  /** hub: heard recently; grace: the hub cannot be reached, the last licence still applies; expired: no new
   *  production until the hub is heard again; unlinked / default: the Free plan of the licence */
  source: "hub" | "grace" | "expired" | "unlinked" | "default";
  linked: boolean;
  account_name: string | null;
  received_at: string | null;
  valid_until: string | null;
  entitlements: {
    max_production_branches: number | null;
    max_servers: number | null;
    max_projects: number | null;
    teams: boolean;
    features: { per_project_access: boolean; custom_domains: boolean; offsite_backups: boolean };
    support_level: string;
    addons: Record<string, number>;
    period?: string;
    renews_at?: string | number;
    trial_plan?: string | null;
  };
  production_withheld: string | null;
  banned: boolean;
  over_limit: string[];
  usage: {
    servers: number;
    projects: number;
    production_branches: number;
    production_branch_names: string[];
    instances_by_stage: Record<Stage, number>;
  };
  production_refusal: string | null;
  /** after a downgrade: the production branches the account keeps (null: no choice to apply) */
  production_keep: string[] | null;
  /** the production branches that are not kept and become development branches */
  leaving_production: string[];
  links: { pricing: string; account: string; keep: string };
  checked_at: string | null;
  /** linking the panel to a Sudoo.sh account within LINK_DEADLINE_DAYS of its first run (src/sudoo/link_deadline.py);
   *  null for everyone but super admins and administrators */
  link_deadline: LinkDeadline | null;
}

export interface LinkDeadline {
  linked: boolean;
  first_run: string;
  deadline_days: number;
  deadline: string;
  /** started days left (1 on the last day); 0 once linked or past the deadline */
  days_left: number;
  /** the deadline passed while unlinked: no new instances, no moves to production, no updates */
  restricted: boolean;
}

export type UpgradeMethod = "enterprise" | "openupgrade";
export type UpgradeState =
  "checked" | "testing" | "tested" | "test_failed" | "promoting" | "promoted" | "promotion_failed" | "rolled_back";
export type UpgradeModuleStatus = "ready" | "version_mismatch" | "unknown_version" | "not_installable" | "missing";
export interface UpgradeFinding {
  code: string;
  text: string;
}
/** The pre-check of an upgrade (POST /instances/<name>/upgrade/check) */
export interface UpgradeReport {
  instance: string;
  from_version: string;
  target: string;
  edition: "community" | "enterprise";
  method: UpgradeMethod;
  repository: string | null;
  branch: string | null;
  branch_exists: boolean | null;
  installed: string[];
  installed_count: number;
  modules: { name: string; version: string; target_version: string | null; status: UpgradeModuleStatus }[];
  warnings: UpgradeFinding[];
  blocking: UpgradeFinding[];
  checked_at: string | null;
}
/** GET /instances/<name>/upgrade: the database upgrade of an instance to a newer Odoo version */
export interface Upgrade {
  instance: string;
  from_version: string;
  edition: "community" | "enterprise";
  method: UpgradeMethod;
  allowed: boolean;
  targets: string[];
  next: string | null;
  skips_versions: boolean;
  terms_url: string | null;
  openupgrade_source: string | null;
  refusal: string | null;
  state: UpgradeState | null;
  target: string | null;
  branch: string | null;
  report: UpgradeReport | null;
  checked_at: string | null;
  staging: string | null;
  tested_at: string | null;
  test_job: string | null;
  promotion: {
    job?: string;
    at?: string | null;
    by?: string;
    target?: string;
    backup?: string[];
    rollback_until?: string | null;
    finished_at?: string | null;
    rolled_back_at?: string | null;
    rolled_back_by?: string;
    reason?: string | null;
  } | null;
  rollback_open: boolean;
  rollback_minutes: number;
  history: { at: string | null; user: string; event: string; detail: string }[];
}

/** Support access (GET /support-access, src/sudoo/web/v1/support_access.py). */
export type SupportLevel = "view" | "admin" | "admin_shell";
export interface SupportSession {
  id: string;
  grant_id: string;
  staff_name: string;
  opened_at: string | null;
  expires_at: string | null;
}
export interface SupportGrant {
  id: string;
  level: SupportLevel;
  scope: { all: boolean; projects: { id: string; name: string }[]; instances: string[] };
  ticket: string | null;
  hours: number | null;
  created_by: string | null;
  created_at: string | null;
  expires_at: string | null;
  revoked_at: string | null;
  revoked_by: string | null;
  state: "active" | "withdrawn" | "expired";
  sessions: SupportSession[];
}
export interface SupportOverview {
  enabled: boolean;
  unavailable: "off" | "not_connected" | null;
  max_hours: number;
  durations: number[];
  levels: SupportLevel[];
  shell_enabled: boolean;
  grants: SupportGrant[];
  sessions: SupportSession[];
  channel: { last_ok: string | null; error: string | null };
}
export interface SupportStatus {
  connected: boolean;
  sessions: SupportSession[];
  active_grants: number;
  can_end: boolean;
}

// ---------------------------------------------------------------- promotion (web/v1/promotion.py)

export type CheckStatus = "ok" | "warning" | "blocked";
export type CheckKey = "build" | "ci" | "backup" | "modules" | "migrations" | "odoo" | "lock" | "capacity";

export interface ModuleVersion {
  name: string;
  version: string;
}
export interface ModuleChange {
  name: string;
  from: string;
  to: string;
  /** how the version changed; "unknown" when GitHub answered without the manifests */
  bump: "up" | "down" | "same" | "unknown";
}
export interface ModuleChanges {
  added: ModuleVersion[];
  removed: ModuleVersion[];
  changed: ModuleChange[];
  /** false: found from the changed paths only, without versions */
  known: boolean;
}
export interface PendingMigration {
  module: string;
  version: string;
  scripts: string[];
}

/** One item of the pre-promotion checklist, with what it found */
export interface CheckItem {
  key: CheckKey;
  status: CheckStatus;
  text: string;
  build?: {
    id: string;
    status: string;
    at: number | string | null;
    commit: { sha: string; subject?: string } | null;
    tests: Record<string, unknown> | null;
  };
  backup?: { file: string; created: number } | null;
  instance?: string;
  max_hours?: number;
  busy?: boolean;
  modules?: ModuleChanges;
  ahead?: number | null;
  behind?: number | null;
  migrations?: PendingMigration[];
  branch_series?: string[];
  production_series?: string[];
  branches?: string[];
  /** the "ci" item: the CI results of the latest build's commit (repository guard) */
  ci?: { state: string; total?: number; failed?: number; pending?: number; url?: string } | null;
}

/** GET /projects/<id>/branches/<branch>/promotion-check */
export interface PromotionCheck {
  branch: string;
  /** "move": the branch becomes production; "merge": it is merged into production's branch (no capacity item) */
  mode: "move" | "merge";
  production: { branch: string | null; instance: string | null };
  items: CheckItem[];
  blocked: CheckKey[];
  /** red items stop the move into production (PROJECT_PROMOTION_BLOCKS) */
  blocking: boolean;
  compare: {
    ahead: number;
    behind: number;
    files_changed: number;
    github_url: string;
    source: "git" | "github";
  } | null;
  checked_at: string | null;
}

export interface CompareCommit {
  sha: string;
  full_sha: string;
  subject: string;
  author: string;
  when: string;
}

/** GET /projects/<id>/compare?base=&head= */
export interface BranchCompare {
  ahead: number;
  behind: number;
  commits: CompareCommit[];
  files_changed: number;
  modules: ModuleChanges;
  migrations: PendingMigration[];
  source: "git" | "github";
  base: { branch: string; sha: string };
  head: { branch: string; sha: string };
  github_url: string;
  odoo_series: string;
}

export type RefreshFrequency = "off" | "daily" | "weekly";

/** GET/PUT /projects/<id>/staging-refresh */
export interface StagingRefresh {
  frequency: RefreshFrequency;
  /** "HH:MM" in the panel's time zone */
  time: string;
  /** 0 Monday to 6 Sunday */
  weekday: number;
  /** empty: every staging branch */
  branches: string[];
  last_run: string | null;
  next_run: string | null;
  changed_by?: string | null;
}
export interface StagingRefreshMeta {
  branches: { name: string; instances: string[] }[];
  timezone?: string;
}
export interface StagingRefreshRun {
  started: string[];
  skipped: { instance: string; reason: string }[];
}

export type PreviewStatus = "creating" | "building" | "ready" | "failed" | "deleted" | "refused";

/** GET /projects/<id>/previews: one pull request's preview */
export interface Preview {
  number: number;
  title: string;
  url: string;
  branch: string;
  repository: string;
  fork: boolean;
  head_sha: string | null;
  instance: string | null;
  instance_exists: boolean;
  /** the branch's instance existed before the pull request: it is not deleted with it */
  existing: boolean;
  status: PreviewStatus;
  reason: string | null;
  commented: boolean;
  opened_at: string | null;
}
export interface PreviewsMeta {
  enabled: boolean;
  limit: number;
  forks: boolean;
  app_connected: boolean;
  can_comment: boolean;
  created: number;
}

// ---------------------------------------------------------------- code profiler (web/v1/profiler.py)

/** A recording being made: preparing (image, checks), recording, saving. */
export interface ProfilerRun {
  id: string;
  phase: "preparing" | "recording" | "saving";
  minutes: number;
  user: string;
  started_at: string;
  recording_since: string | null;
  ends_at: string;
  stopping: boolean;
  production: boolean;
}
export interface ProfileRecording {
  id: string;
  user: string;
  minutes: number;
  seconds: number;
  samples: number;
  threads: number;
  rate: number;
  size: number;
  started_at: string;
  ended_at: string;
  stopped_early: boolean;
  production: boolean;
  links: { self: string; download: string };
}
export interface ProfilerState {
  enabled: boolean;
  production: boolean;
  production_allowed: boolean;
  running: boolean;
  status: string;
  max_minutes: number;
  keep: number;
  rate: number;
  current: ProfilerRun | null;
  last_error: { message: string; code: string; at: number } | null;
  recordings: ProfileRecording[];
}
/** A node of the flame graph: name, where (file:line), samples, children. */
export interface ProfileNode {
  n: string;
  w?: string;
  v: number;
  c?: ProfileNode[];
}
export interface ProfileTree {
  total: number;
  seconds: number;
  threads: { name: string; samples: number }[];
  root: ProfileNode;
  top: { name: string; where: string; self: number; total: number }[];
}
export type ProfileDetail = ProfileRecording & { tree: ProfileTree };

// ---------------------------------------------------------------- GitHub link checks (repos/github_check.py)

export type GitHubCheckStatus = "ok" | "warning" | "error" | "skipped";
export interface GitHubCheck {
  id: "repository" | "installation" | "permissions" | "webhook" | "submodules";
  status: GitHubCheckStatus;
  message: string;
  fix: { url: string; label: string } | null;
}
export interface GitHubCheckResult {
  status: "ok" | "warning" | "error";
  checks: GitHubCheck[];
  checked_at: string;
}

/** A module of a repository's branch (GET /repositories/modules): its folder holds a __manifest__.py. */
export interface RepositoryModule {
  name: string;
  title: string;
  summary: string;
  application: boolean;
  path: string;
  /** the submodule it comes from, or null for the repository's own */
  submodule: string | null;
}

export interface RepositoryModules {
  repository: string;
  branch: string;
  commit: string;
  modules: RepositoryModule[];
  problems: string[];
}

/** A standard Odoo app offered by the module picker (GET /odoo-apps). */
export interface OdooApp {
  name: string;
  title: string;
  enterprise: boolean;
}

// ---------------------------------------------------------------- first-run setup wizard (web/v1/setup_wizard.py)
// (Engine, "docker" | "kubernetes", is declared with the instances above)

/** The three questions that choose the engine: more servers later, stronger separation, a team runs it. */
export interface SetupNeeds {
  grow: boolean | null;
  separation: boolean | null;
  team: boolean | null;
}
export type SetupStep = "welcome" | "server" | "address" | "email" | "github" | "account" | "admin" | "done";

/** GET /setup/server-check: what this server has and the engine recommended for it (read only). */
export interface ServerCheck {
  memory_mb: number;
  processors: number;
  disk_free_gb: number;
  system: { name: string; id: string; version: string };
  virtualisation: { name: string; kind: "none" | "vm" | "container" | "unknown"; from_installer: boolean };
  ports: Record<"80" | "443", { free: boolean; sudoo: boolean }>;
  needs: SetupNeeds;
  recommendation: {
    fits: boolean;
    recommended: Engine;
    default: Engine;
    reason: string;
    reason_code: "minimum" | "grow" | "separation" | "unanswered" | "team" | "one";
    reasons: string[];
    beta: boolean;
  };
  /** the minimum for Kubernetes (k3s's own plus room for Odoo); "measured" false while they are placeholders */
  figures: {
    min_cpus: number;
    min_memory_mb: number;
    k3s_min_memory_mb: number;
    odoo_room_memory_mb: number;
    measured: boolean;
    source: string;
  };
  engine: { current: Engine; wanted: Engine | null; available: Engine[]; switch_command: string };
  checked_at: number;
}

/** GET /setup/draft: the wizard's choices so far; nothing of it is applied before the final confirm. */
export interface SetupDraft {
  step: SetupStep;
  language: "en" | "ar";
  engine: Engine;
  needs: SetupNeeds;
  exposure: "direct" | "proxy" | "tunnel";
  name: { kind: "server" | "domain" | "managed"; domain: string };
  mail: {
    enabled: boolean;
    host: string;
    port: number;
    security: "starttls" | "ssl" | "none";
    username: string;
    sender: string;
    password_set: boolean;
  };
  github: { connect: boolean };
  account: { link: boolean; link_code: string; company_label: string; from_install_code?: boolean };
  admin: Profile;
}

/** The "setup" part of POST /setup's answer: what the final confirm applied. */
export interface SetupResult {
  settings: string[];
  restart_needed: boolean;
  linked: { account_name: string | null } | null;
  link_error: string | null;
  github: boolean;
  engine: { chosen: Engine; current: Engine; command: string | null };
}

// ---------------------------------------------------------------- maintenance windows (src/sudoo/maintenance.py)

export type MaintenanceStatus = "proposed" | "scheduled" | "running" | "done" | "failed" | "cancelled";
export type MaintenancePreset = "tonight" | "weekend" | "default";
export interface MaintenanceItem {
  id: string;
  kind: "panel_update" | "odoo_images" | "restart" | "custom";
  scope: "panel" | "server" | "instance" | "project";
  targets: string[];
  version: string | null;
  title: { en: string; ar: string };
  reason: { en: string; ar: string };
  estimated_minutes: number;
  earliest_start: string;
  deadline: string;
  required: boolean;
  status: MaintenanceStatus;
  window: {
    start: string;
    end: string;
    source: "panel" | "client" | "account" | "default";
    chosen_by: string | null;
    chosen_at: string | null;
  } | null;
  choose_from: string | null;
  choose_until: string | null;
  presets: { id: MaintenancePreset; start: string; end: string }[];
  auto_at: string | null;
  default_start: string | null;
  instances: { name: string; stage: string; project: string | null; held: string | null }[];
  approvals: string[];
  steps: {
    name: string;
    instance?: string | null;
    stage?: string | null;
    status: string;
    job?: string | null;
    detail?: string | null;
  }[];
  detail: string | null;
  received_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  /** the client portal only: the client's projects it works on, and those closed or locked */
  projects?: { id: string; name: string }[];
  held?: string[];
}
export interface MaintenanceMeta {
  timezone: string;
  lead_minutes: number;
  default_window: string;
  default_days: string;
  tonight_time: string;
  weekend_day: string;
  reminder_hours: number[];
  client_choice: boolean;
  default_minutes: number;
  default_start: string;
  may_choose: boolean;
  confirmations?: boolean;
}
export interface PublicMaintenance {
  kind: MaintenanceItem["kind"];
  title: { en: string; ar: string };
  status: "scheduled" | "running";
  window_start: string | null;
  window_end: string | null;
}

/** An instance that was deleted (GET /deleted-instances): who deleted it, or that its lifetime ended */
export interface DeletedInstance {
  name: string;
  project: { id: string; name: string } | null;
  branch: string | null;
  stage: Stage;
  created_at: string | null;
  deleted_at: string;
  /** null when its lifetime ended */
  deleted_by: string | null;
  expired: boolean;
}
