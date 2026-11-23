// A project's repository guard (/projects/<id>/guard): the shapes the API answers (src/sudoo/web/v1/repo_guard.py).

export type Strictness = "relaxed" | "standard" | "strict";
export type Linter = "ruff" | "flake8";

export interface GuardChecks {
  enabled: boolean;
  lint: boolean;
  linter: Linter;
  pylint_odoo: boolean;
  manifest: boolean;
  xml_csv: boolean;
  tests: boolean;
  strictness: Strictness;
}

export interface GuardStageRules {
  approvals: number;
  require_checks: boolean;
  linear_history: boolean;
  signed_commits: boolean;
  enabled?: boolean;
}

export type PushersMode = "anyone" | "roles" | "named";
export type RulesMethod = "auto" | "rulesets" | "branch_protection";

export interface GuardRules {
  enabled: boolean;
  method: RulesMethod;
  production: GuardStageRules;
  staging: GuardStageRules & { enabled: boolean };
  pushers: { mode: PushersMode; teams: string[]; users: string[] };
}

export interface GuardPull {
  number: number;
  url: string;
  state: "open" | "closed" | "merged";
  title?: string;
  purpose?: "add" | "remove";
  base?: string;
  by?: string;
  at?: number;
  files?: string[];
}

export interface GuardPermission {
  key: string;
  level: "read" | "write";
  feature: "checks" | "results" | "rules";
  why: string;
  granted: string | null;
  ok: boolean;
}

export interface Guard {
  checks: GuardChecks;
  block_promotion: boolean;
  rules: GuardRules;
  /** the values the project chose itself; everything else follows the panel's settings (PROJECT_GUARD_*) */
  own: { checks?: Partial<GuardChecks>; rules?: Partial<GuardRules>; block_promotion?: boolean };
  defaults: { checks: GuardChecks; rules: GuardRules; block_promotion: boolean };
  pull: GuardPull | null;
  applied: { method: string; rulesets: Record<string, number>; protections: string[]; at: number; by: string } | null;
  changed_by: string | null;
  changed_at: number | null;
  repository: string;
  versions: { default: string[]; branches: Record<string, string[]> };
  required_check: string;
  files: string[];
  may_change: boolean;
  github: {
    app_connected: boolean;
    installed: boolean;
    permissions: GuardPermission[];
    missing: Record<"checks" | "results" | "rules", string[]>;
    app_permissions_url: string | null;
    installation_url: string | null;
    install_url: string | null;
  };
}

export type FileAction = "add" | "update" | "remove" | "unchanged" | "kept";

export interface GuardFile {
  path: string;
  action: FileAction;
  content: string | null;
  reason?: string;
}

export interface ChecksPreview {
  files: GuardFile[];
  base: string | null;
  note: string | null;
  changes: number;
  pull?: GuardPull | null;
  result?: "opened" | "updated" | "up_to_date" | "dry_run";
}

export type RuleAction = "create" | "update" | "delete" | "unchanged" | "blocked";

export interface RuleChange {
  field: string;
  label: string;
  before: string;
  after: string;
}

export interface RuleTarget {
  key: string;
  kind: "ruleset" | "protection";
  action: RuleAction;
  name: string;
  changes: RuleChange[];
  reason?: string;
}

export interface RulesPlan {
  repository: { full_name: string; private: boolean; visibility: string; default_branch: string; owner_type: string };
  method: "rulesets" | "branch_protection";
  available: boolean;
  limitations: { reason: string; message: string }[];
  targets: RuleTarget[];
  untouched: string[];
  branches: { production: string[]; staging: string[] };
  affects_production: boolean;
  changes: number;
  result?: "dry_run" | "applied" | "removed";
}

export type CiState = "success" | "failure" | "pending" | "none" | "unknown";

export interface CiResult {
  state: CiState;
  total?: number;
  failed?: number;
  pending?: number;
  runs?: { name: string; state: CiState; url: string }[];
  url?: string;
  error?: string;
}
