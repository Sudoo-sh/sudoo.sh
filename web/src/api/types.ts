// The documents of /api/v1, as docs/PANEL_API.md describes them. Times are ISO 8601 strings in UTC.

export type Role = "super_admin" | "admin" | "maintainer" | "developer" | "viewer";
export type Permission =
  | "instances.view"
  | "instances.logs"
  | "instances.update"
  | "instances.operate"
  | "instances.create"
  | "instances.restore"
  | "instances.delete"
  | "instances.credentials"
  | "backups.manage"
  | "github.manage"
  | "accounts.manage"
  | "tokens.manage"
  | "audit.view"
  | "plugins.view"
  | "settings.manage";

export interface Profile {
  first_name: string | null;
  last_name: string | null;
  email: string | null;
  phone: string | null;
}
/** "creating": its creation is queued or running (GET answers before its files exist, with the job as current_job) */
export type InstanceStatus = "creating" | "online" | "starting" | "busy" | "paused" | "switched_off" | "stopped" | "unknown";
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
  brand_color: string;
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

export interface JobRef {
  id: string;
  action: string;
  status: JobStatus;
  progress_percent: number | null;
}

/** An instance's job in progress: the reference with its steps so far */
export interface CurrentJob extends JobRef {
  steps: { title: string; status: "running" | "done" | "failed" }[];
}

export interface Job extends JobRef {
  instance: string;
  kind: string;
  requested_by: string;
  started_at: string | null;
  finished_at: string | null;
  steps: { title: string; status: "running" | "done" | "failed" }[];
  notes: { text: string; level: "info" | "warning" }[];
  error: string | null;
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

/** production: never paused or deleted automatically, backed up every day, updated safely; staging and development:
 *  the panel's own rules (instances/stages.py) */
export type Stage = "production" | "staging" | "development";

export interface Instance {
  name: string;
  status: InstanceStatus;
  stage: Stage;
  url: string | null;
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
  current_job: CurrentJob | null;
  created_by: string | null;
  // only on GET /instances/<name>
  disk?: { database_bytes: number | null; files_bytes: number | null };
  recent_commits?: { sha: string; subject: string; when: string; author: string }[];
  submodules?: { path: string; sha: string; url: string }[];
  recent_jobs?: JobRef[];
  backups?: InstanceBackup[];
  /** the backup "Restore the latest backup" puts back */
  restorable_backup?: string | null;
  /** while the state before the last restore is kept (ROLLBACK_HOURS): what Rollback puts back, and until when */
  rollback?: { backups: string[]; taken_at: string; until: string } | null;
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
  type: "job_failed" | "update_failed" | "backup_failed" | "instance_down" | "disk" | "memory" | "drift" | "public_link" | "test";
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
}

export type PlanDefaults = { delete_after_days: number; auto_update: boolean; auto_upgrade: boolean; auto_pause: boolean };

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
  scope: "read" | "instances" | "write";
  created: number;
  created_by: string;
  last_used: number | null;
}

export type BackupKind = "manual" | "daily" | "deletion" | "before-restore" | "before-rollback" | "before-repair" | "before-update";
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
}

/** A backup as an instance lists it. */
export interface InstanceBackup {
  file: string;
  kind: BackupKind;
  database: string;
  size_bytes: number;
  created_at: string;
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
  plugin: Omit<CatalogPlugin, "plans" | "latest_version" | "compatible_version" | "installed_version" | "license" | "action">;
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

export type LicencePhase = "active" | "perpetual" | "expiring" | "grace" | "ended" | "no_lease" | "withheld" | "unlicensed";

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
