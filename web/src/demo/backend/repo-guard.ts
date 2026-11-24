// The demo's repository guard (src/sudoo/repo_guard): Acme Retail starts with its checks on, an open pull request and
// rulesets applied; every other project starts with everything off. Nothing reaches GitHub: the pull request and the
// rulesets live in memory, and a commit's CI results are drawn from its hash (the same on every visit).

import type {
  ChecksPreview,
  CiResult,
  Guard,
  GuardChecks,
  GuardFile,
  GuardRules,
  RulesPlan,
  RuleTarget,
} from "@/api/guard";
import { branchList, type DemoProject } from "./projects";
import { ME, setting } from "./state";
import { hash, Problem } from "./util";

const REQUIRED = "sudoo-checks";
const PREFIX = "Sudoo.sh guard: ";
const PATHS = [
  ".github/workflows/sudoo-checks.yml",
  ".github/sudoo/check_addons.py",
  ".pre-commit-config.yaml",
  "docs/CHECKS.md",
];
const DEFAULT_CHECKS: GuardChecks = {
  enabled: false,
  lint: true,
  linter: "ruff",
  pylint_odoo: true,
  manifest: true,
  xml_csv: true,
  tests: false,
  strictness: "standard",
};
const DEFAULT_RULES: GuardRules = {
  enabled: false,
  method: "auto",
  production: { approvals: 1, require_checks: true, linear_history: false, signed_commits: false },
  staging: { enabled: true, approvals: 0, require_checks: true, linear_history: false, signed_commits: false },
  pushers: { mode: "roles", teams: [], users: [] },
};

interface DemoGuard {
  checks: GuardChecks;
  block_promotion: boolean;
  rules: GuardRules;
  pull: Guard["pull"];
  applied: Guard["applied"];
  /** the rules on "GitHub": the panel's (by kind) and one the panel did not make */
  github: Record<string, Record<string, string>>;
  merged: GuardChecks | null;
}

const guards = new Map<string, DemoGuard>();
const now = () => Math.floor(Date.now() / 1000);
const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T;

/** What a project starts with: the panel's settings (PROJECT_GUARD_*), as on the server. */
function defaults(): { checks: GuardChecks; rules: GuardRules; block: boolean } {
  const rules = clone(DEFAULT_RULES);
  rules.production.approvals = Number(setting("project_guard_production_approvals") ?? 1);
  rules.production.linear_history = setting("project_guard_linear_history") === true;
  rules.production.signed_commits = setting("project_guard_signed_commits") === true;
  rules.staging.approvals = Number(setting("project_guard_staging_approvals") ?? 0);
  rules.pushers.mode = setting("project_guard_pushers") === "anyone" ? "anyone" : "roles";
  const strictness = String(setting("project_guard_strictness") ?? "standard") as GuardChecks["strictness"];
  return {
    checks: { ...DEFAULT_CHECKS, enabled: setting("project_guard_checks") === true, strictness },
    rules,
    block: setting("project_guard_blocks_promotion") === true,
  };
}

function guardOf(project: DemoProject): DemoGuard {
  let found = guards.get(project.id);
  if (!found) {
    const seeded = project.id === "acme-retail";
    const start = defaults();
    const checks = { ...start.checks, enabled: seeded || start.checks.enabled };
    const rules = { ...start.rules, enabled: seeded };
    found = {
      checks,
      block_promotion: start.block,
      rules,
      pull: seeded
        ? {
            number: 42,
            url: `https://github.com/${project.repository}/pull/42`,
            state: "open",
            purpose: "add",
            base: "main",
            by: ME,
            at: now() - 3 * 3600,
            files: PATHS,
          }
        : null,
      applied: null,
      github: { "Team rules": { branches: "main", deletion: "blocked" } },
      merged: null,
    };
    guards.set(project.id, found);
    if (seeded) applyRules(project, found, rules, false);
  }
  return found;
}

export function resetGuards() {
  guards.clear();
}

const series = (version: string) => /^(\d+\.\d)/.exec(version)?.[1] ?? version;

export function guardView(project: DemoProject, mayChange: boolean): Guard {
  const g = guardOf(project);
  const own = Object.fromEntries(
    Object.entries(project.versions ?? {})
      .map(([b, v]) => [b, [series(v)]])
      .filter(([, v]) => v?.[0] !== series(project.version)),
  );
  const permissions = [
    {
      key: "contents",
      level: "write",
      feature: "checks",
      why: "Commit the check files to the panel's own branch (sudoo/repository-guard).",
    },
    {
      key: "pull_requests",
      level: "write",
      feature: "checks",
      why: "Open, update and close the pull request that adds or removes the check files.",
    },
    {
      key: "workflows",
      level: "write",
      feature: "checks",
      why: "GitHub refuses any change under .github/workflows without it.",
    },
    {
      key: "checks",
      level: "read",
      feature: "results",
      why: "Read the results of the checks, to show them on builds and before a promotion.",
    },
    {
      key: "administration",
      level: "write",
      feature: "rules",
      why: "Create, change and remove the panel's rulesets (or classic branch protection).",
    },
  ] as const;
  return {
    checks: clone(g.checks),
    block_promotion: g.block_promotion,
    rules: clone(g.rules),
    pull: g.pull,
    applied: g.applied,
    own: {},
    defaults: (({ checks, rules, block }) => ({ checks, rules, block_promotion: block }))(defaults()),
    changed_by: ME,
    changed_at: now() - 3 * 3600,
    repository: project.repository,
    versions: { default: [series(project.version)], branches: own },
    required_check: REQUIRED,
    files: PATHS,
    may_change: mayChange,
    github: {
      app_connected: true,
      installed: true,
      permissions: permissions.map((p) => ({ ...p, granted: p.level, ok: true })),
      missing: { checks: [], results: [], rules: [] },
      app_permissions_url: "https://github.com/organizations/acme/settings/apps/sudoo-demo/permissions",
      installation_url: "https://github.com/organizations/acme/settings/installations/1",
      install_url: "https://github.com/apps/sudoo-demo/installations/new",
    },
  };
}

// ---------------------------------------------------------------- checks

const FLAGS = ["enabled", "lint", "pylint_odoo", "manifest", "xml_csv", "tests"] as const;

export function cleanChecks(raw: unknown, current: GuardChecks): GuardChecks {
  const body = (raw ?? {}) as Record<string, unknown>;
  const found = { ...current };
  for (const [key, value] of Object.entries(body)) {
    if ((FLAGS as readonly string[]).includes(key)) {
      if (typeof value !== "boolean") throw new Problem(400, `${key}: true or false.`);
      (found as Record<string, unknown>)[key] = value;
    } else if (key === "linter") {
      if (value !== "ruff" && value !== "flake8") throw new Problem(400, "linter: ruff or flake8.");
      found.linter = value;
    } else if (key === "strictness") {
      if (!["relaxed", "standard", "strict"].includes(String(value)))
        throw new Problem(400, "strictness: relaxed, standard, strict.");
      found.strictness = value as GuardChecks["strictness"];
    } else throw new Problem(400, `Unknown check option '${key.slice(0, 40)}'.`);
  }
  return found;
}

function fileText(path: string, checks: GuardChecks): string {
  const head = "# Managed by Sudoo.sh (repository guard).";
  if (path.endsWith(".yml"))
    return `${head}\nname: Sudoo.sh checks\n\non:\n  pull_request:\n  push:\n    branches: ["**"]\n\njobs:\n  lint:\n    name: Lint (${checks.linter})\n    steps:\n      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1\n  ${REQUIRED}:\n    name: ${REQUIRED}\n    if: always()\n`;
  if (path.endsWith(".yaml"))
    return `${head}\nrepos:\n  - repo: https://github.com/OCA/pylint-odoo\n    rev: 30224d0a2ae20645b197776358a734d81197aafc  # frozen: v10.0.12\n    hooks:\n      - id: pylint_odoo\n`;
  if (path.endsWith(".md")) return `<!-- ${head.slice(2)} -->\n# Checks\n\nStrictness: ${checks.strictness}.\n`;
  return `${head}\n"""Checks of the Odoo modules of a repository: their manifests, XML files and CSV files."""\n`;
}

function wanted(checks: GuardChecks): Record<string, string> {
  if (!checks.enabled) return {};
  const found: Record<string, string> = {};
  for (const path of PATHS) {
    if (path.endsWith(".py") && !(checks.manifest || checks.xml_csv || checks.tests)) continue;
    found[path] = fileText(path, checks);
  }
  return found;
}

function plannedFiles(g: DemoGuard, checks: GuardChecks): GuardFile[] {
  const next = wanted(checks);
  const current = g.merged ? wanted(g.merged) : {};
  return PATHS.flatMap((path): GuardFile[] => {
    const content = next[path] ?? null;
    const before = current[path];
    if (content === null) return before ? [{ path, action: "remove", content: null }] : [];
    return [{ path, action: before === undefined ? "add" : before === content ? "unchanged" : "update", content }];
  });
}

export function previewChecks(project: DemoProject, checks: GuardChecks): ChecksPreview {
  const files = plannedFiles(guardOf(project), checks);
  return {
    files,
    base: "main",
    note: null,
    changes: files.filter((f) => ["add", "update", "remove"].includes(f.action)).length,
  };
}

export function syncChecks(project: DemoProject, checks: GuardChecks): ChecksPreview {
  const g = guardOf(project);
  const preview = previewChecks(project, checks);
  g.checks = checks;
  if (!preview.changes) {
    if (g.pull?.state === "open") g.pull = { ...g.pull, state: "closed" };
    return { ...preview, pull: g.pull, result: "up_to_date" };
  }
  const open = g.pull?.state === "open";
  const number = open ? g.pull!.number : (g.pull?.number ?? 41) + 1;
  g.pull = {
    number,
    url: `https://github.com/${project.repository}/pull/${number}`,
    state: "open",
    purpose: checks.enabled ? "add" : "remove",
    base: "main",
    by: ME,
    at: now(),
    files: preview.files.filter((f) => f.action !== "unchanged").map((f) => f.path),
  };
  return { ...preview, pull: g.pull, result: open ? "updated" : "opened" };
}

export function refreshPull(project: DemoProject) {
  const g = guardOf(project);
  return g.pull;
}

export function setBlock(project: DemoProject, value: unknown, mayChange: boolean): Guard {
  if (typeof value !== "boolean") throw new Problem(400, "The field 'block_promotion' must be true or false.");
  guardOf(project).block_promotion = value;
  return guardView(project, mayChange);
}

// ---------------------------------------------------------------- results

/** A commit's CI results, drawn from its hash: mostly passed, sometimes failed or still running. */
export function resultOf(project: DemoProject, sha: string): CiResult {
  const roll = hash(project.id + sha) % 10;
  const url = `https://github.com/${project.repository}/commit/${sha}/checks`;
  if (roll === 0)
    return {
      state: "failure",
      total: 5,
      failed: 1,
      pending: 0,
      url,
      runs: [{ name: "pylint-odoo (18.0)", state: "failure", url }],
    };
  if (roll === 1) return { state: "pending", total: 5, failed: 0, pending: 2, url, runs: [] };
  return { state: "success", total: 5, failed: 0, pending: 0, url, runs: [{ name: REQUIRED, state: "success", url }] };
}

export function results(project: DemoProject, shas: string[]): Record<string, CiResult> {
  const g = guardOf(project);
  if (!g.checks.enabled || !g.pull) return {};
  return Object.fromEntries(shas.map((sha) => [sha, resultOf(project, sha)]));
}

/** The pre-promotion checklist's "ci" item, or null when the project's checks are off. */
export function promotionItem(project: DemoProject, sha: string | undefined) {
  const g = guardOf(project);
  if (!g.checks.enabled || !g.pull) return null;
  if (!sha)
    return {
      key: "ci" as const,
      status: "warning" as const,
      text: "The branch has no built commit whose checks could be read.",
      ci: null,
    };
  const found = resultOf(project, sha);
  const ci = { state: found.state, total: found.total, failed: found.failed, pending: found.pending, url: found.url };
  if (found.state === "success")
    return { key: "ci" as const, status: "ok" as const, text: `The CI checks of ${sha.slice(0, 7)} passed.`, ci };
  if (found.state === "failure")
    return {
      key: "ci" as const,
      status: g.block_promotion ? ("blocked" as const) : ("warning" as const),
      text: `1 CI check failed on ${sha.slice(0, 7)}.`,
      ci,
    };
  return {
    key: "ci" as const,
    status: "warning" as const,
    text: `The CI checks of ${sha.slice(0, 7)} are still running.`,
    ci,
  };
}

// ---------------------------------------------------------------- branch rules

export function cleanRules(raw: unknown, current: GuardRules): GuardRules {
  const body = (raw ?? {}) as Partial<GuardRules>;
  const found = clone(current);
  if (body.enabled !== undefined) found.enabled = Boolean(body.enabled);
  if (body.method !== undefined) {
    if (!["auto", "rulesets", "branch_protection"].includes(body.method))
      throw new Problem(400, "method: auto, rulesets, branch_protection.");
    found.method = body.method;
  }
  for (const stage of ["production", "staging"] as const) {
    const given = body[stage];
    if (!given) continue;
    const approvals = given.approvals ?? found[stage].approvals;
    if (!Number.isInteger(approvals) || approvals < 0 || approvals > 10)
      throw new Problem(400, `${stage}.approvals: a whole number from 0 to 10.`);
    Object.assign(found[stage], given, { approvals });
  }
  if (body.pushers) {
    const mode = body.pushers.mode ?? found.pushers.mode;
    if (!["anyone", "roles", "named"].includes(mode)) throw new Problem(400, "pushers.mode: anyone, roles, named.");
    const names = (list: unknown) =>
      Array.isArray(list) ? list.map((s) => String(s).replace(/^@/, "").trim()).filter(Boolean) : [];
    found.pushers = {
      mode,
      teams: names(body.pushers.teams ?? found.pushers.teams),
      users: names(body.pushers.users ?? found.pushers.users),
    };
    if (mode === "named" && !found.pushers.teams.length && !found.pushers.users.length)
      throw new Problem(400, "pushers: name at least one team or person, or choose the roles.");
  }
  return found;
}

const LABELS: Record<string, string> = {
  branches: "Branches",
  pull_request: "Pull request required",
  approvals: "Approvals required",
  checks: "Checks that must pass",
  force_push: "Force-pushes",
  deletion: "Deleting the branch",
  linear_history: "Linear history",
  signed_commits: "Signed commits",
  pushers: "Who may push or merge",
};

function desired(project: DemoProject, rules: GuardRules): Record<string, Record<string, string>> {
  const branches = branchList(project, true);
  const production = branches.filter((b) => b.stage === "production").map((b) => b.name);
  const staging = branches.filter((b) => b.stage === "staging").map((b) => b.name);
  const level = (key: "production" | "staging", names: string[]) => ({
    branches: names.join(", ") || "main",
    pull_request: "yes",
    approvals: String(rules[key].approvals),
    checks: rules[key].require_checks ? REQUIRED : "none",
    force_push: "blocked",
    deletion: "blocked",
    linear_history: rules[key].linear_history ? "yes" : "no",
    signed_commits: rules[key].signed_commits ? "yes" : "no",
  });
  const found: Record<string, Record<string, string>> = { production: level("production", production) };
  if (rules.pushers.mode !== "anyone")
    found.production_merges = {
      branches: production.join(", ") || "main",
      pushers:
        rules.pushers.mode === "roles"
          ? "Admin and Maintain roles"
          : rules.pushers.teams.map((t) => `team ${t}`).join(", ") || "nobody",
    };
  if (rules.staging.enabled && staging.length) found.staging = level("staging", staging);
  return found;
}

const NAMES: Record<string, string> = {
  production: `${PREFIX}production`,
  production_merges: `${PREFIX}production merges`,
  staging: `${PREFIX}staging`,
};

export function planRules(project: DemoProject, rules: GuardRules, turnOff: boolean): RulesPlan {
  const g = guardOf(project);
  const next = turnOff ? {} : desired(project, rules);
  const targets: RuleTarget[] = [];
  for (const key of Object.keys(NAMES)) {
    const before = g.github[NAMES[key]!];
    const after = next[key];
    if (!before && !after) continue;
    const fields = Object.keys(LABELS).filter((f) => (before && f in before) || (after && f in after));
    const changes = fields
      .map((f) => ({ field: f, label: LABELS[f]!, before: before?.[f] ?? "", after: after?.[f] ?? "" }))
      .filter((c) => c.before !== c.after);
    const action = after && !before ? "create" : before && !after ? "delete" : changes.length ? "update" : "unchanged";
    targets.push({ key, kind: "ruleset", action, name: NAMES[key]!, changes });
  }
  const limitations =
    !turnOff && rules.pushers.mode === "named" && rules.pushers.users.length
      ? [
          {
            reason: "people_in_rulesets",
            message:
              "Rulesets cannot name people, only roles and teams: the people listed are left out. Choose classic branch protection to name them (organisation repositories only).",
          },
        ]
      : [];
  return {
    repository: {
      full_name: project.repository,
      private: true,
      visibility: "private",
      default_branch: project.repo?.defaultBranch ?? "main",
      owner_type: "Organization",
    },
    method: "rulesets",
    available: true,
    limitations,
    targets,
    untouched: Object.keys(g.github).filter((name) => !name.startsWith(PREFIX)),
    branches: {
      production: branchList(project, true)
        .filter((b) => b.stage === "production")
        .map((b) => b.name),
      staging: branchList(project, true)
        .filter((b) => b.stage === "staging")
        .map((b) => b.name),
    },
    affects_production: targets.some(
      (t) => t.key.startsWith("production") && ["create", "update", "delete"].includes(t.action),
    ),
    changes: targets.filter((t) => ["create", "update", "delete"].includes(t.action)).length,
  };
}

function applyRules(project: DemoProject, g: DemoGuard, rules: GuardRules, turnOff: boolean): RulesPlan {
  const plan = planRules(project, rules, turnOff);
  const next = turnOff ? {} : desired(project, rules);
  for (const key of Object.keys(NAMES)) {
    if (next[key]) g.github[NAMES[key]!] = next[key]!;
    else delete g.github[NAMES[key]!];
  }
  g.rules = { ...rules, enabled: !turnOff };
  const owned = Object.fromEntries(Object.keys(next).map((k, i) => [k, 9000 + i]));
  g.applied = turnOff
    ? null
    : {
        method: "rulesets",
        rulesets: owned,
        protections: [],
        at: now() - (project.id === "acme-retail" && !g.applied ? 2 * 86400 : 0),
        by: ME,
      };
  return { ...plan, result: turnOff ? "removed" : "applied" };
}

export function changeRules(
  project: DemoProject,
  rules: GuardRules,
  confirm: unknown,
  turnOff: boolean,
  dryRun: boolean,
): RulesPlan {
  const g = guardOf(project);
  const plan = planRules(project, rules, turnOff);
  if (dryRun) return { ...plan, result: "dry_run" };
  if (
    plan.affects_production &&
    String(confirm ?? "")
      .trim()
      .toLowerCase() !== project.repository.toLowerCase()
  )
    throw new Problem(
      400,
      `These rules change production: type the repository's name (${project.repository}) to confirm.`,
    );
  return applyRules(project, g, rules, turnOff);
}

export function currentRules(project: DemoProject): GuardRules {
  return guardOf(project).rules;
}

export function currentChecks(project: DemoProject): GuardChecks {
  return guardOf(project).checks;
}
