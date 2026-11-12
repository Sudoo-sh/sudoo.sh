// The documents of /api/v1, as docs/PANEL_API.md describes them. Times are ISO 8601 strings in UTC.

export type Role = "admin" | "operator";
export type InstanceStatus = "online" | "starting" | "busy" | "paused" | "switched_off" | "stopped" | "unknown";
export type JobStatus = "queued" | "running" | "success" | "failed";

export interface Envelope<T> {
  data: T;
  meta?: { total?: number; count?: number; limit?: number; offset?: number; [key: string]: unknown };
}

export interface Session {
  user: string;
  role: Role;
  role_label: string;
  csrf_token: string;
  must_set_up_second_factor: boolean;
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

export interface Instance {
  name: string;
  status: InstanceStatus;
  url: string | null;
  odoo: { version: string; edition: "community" | "enterprise"; database: string };
  git: {
    repository: string | null;
    branch: string;
    branch_deleted_on_github: boolean;
    commit: { sha: string; subject: string; author: string; age: string } | null;
  };
  settings: { auto_update: boolean; auto_upgrade: boolean; auto_pause: boolean; delete_after_days: number };
  usage: Usage;
  uptime: string | null;
  gateway_error: string | null;
  created_at: string | null;
  last_visit_at: string | null;
  pauses_at: string | null;
  expires_at: string | null;
  current_job: JobRef | null;
  // only on GET /instances/<name>
  disk?: { database_bytes: number | null; files_bytes: number | null };
  recent_commits?: { sha: string; subject: string; when: string; author: string }[];
  submodules?: { path: string; sha: string; url: string }[];
  recent_jobs?: JobRef[];
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
}

export interface Metrics {
  at: number | null;
  interval_seconds: number;
  server: Partial<ServerFigures>;
  instances: Record<string, Usage & { running: boolean }>;
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
  secret: boolean;
  type: "boolean" | "integer" | "number" | "string";
  minimum: number | null;
  description: string | null;
}

export interface Version {
  version: string;
  enterprise: boolean;
}

export interface Plan {
  name: string;
  database: string;
  login: string;
  defaults: { delete_after_days: number; auto_update: boolean; auto_upgrade: boolean; auto_pause: boolean };
}

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

export interface Account {
  name: string;
  role: Role;
  role_label: string;
  email: string | null;
  password_managed_here: boolean;
  second_factors: Factor[];
  second_factor_required: boolean;
  require_second_factor: "none" | "admins" | "everyone";
}

export interface User {
  name: string;
  role: Role;
  role_label: string;
  last_sign_in_at: string | null;
  password_changed_at: string | null;
  email: string | null;
  second_factors: string[];
}

export interface Token {
  id: string;
  name: string;
  scope: "read" | "write";
  created: number;
  created_by: string;
  last_used: number | null;
}

export interface Backup {
  file: string;
  instance: string;
  db: string;
  size: number;
  created: number;
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

export interface RepoAccess {
  repositories: { url: string; role: "main" | "submodule"; ok: boolean; problem: string | null }[];
  all_ok: boolean;
}
