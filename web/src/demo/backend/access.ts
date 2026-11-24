// Teams, per-project and per-branch access, locked branches and clients' switches, as src/sudoo/projects/access.py
// and clients.py decide them: what someone may do in a project is their role ∩ their level there; a locked branch is
// closed to everyone its lock does not allow (super admins included, until they unlock it); a branch or project its
// client closed is closed to everyone the client does not let through (super admins included, who cannot lift it).
// The demo's visitor is the only one who acts; as a client, the visitor is CLIENT_ME.

import type {
  AccessLevel,
  AccessMode,
  AccessSubject,
  BranchLock,
  ClientAccess,
  Grant,
  Instance,
  Permission,
  Stage,
  SwitchState,
  Team,
} from "@/api/types";
import { instances, ME, roleInfo, session, users } from "./state";
import { iso, OPENED, DAY, Problem } from "./util";

export interface DemoAccess {
  mode: AccessMode;
  grants: Grant[];
  branches: Record<string, { restricted: boolean; grants: Grant[] }>;
  locks: Record<string, BranchLock>;
  /** "project_roles" (per stage, src/sudoo/projects/project_roles.py) or "legacy" (role ∩ level) */
  model: "project_roles" | "legacy";
  /** the project's changes to the default matrix */
  matrix: Partial<Record<AccessLevel, Partial<Record<Scope, Permission[]>>>>;
  /** the branches' stages (the project's own record, shared) */
  stages?: Record<string, Stage>;
  /** the project's client part (projects/clients.py), when it has clients */
  client?: DemoClient;
}

/** The client the demo's visitor is in the "Client" perspective. */
export const CLIENT_ME = "maya";

export interface DemoSwitch {
  state: SwitchState;
  until: number | null;
  by: string;
  at: number;
}
export interface DemoPass {
  id: string;
  kind: "request" | "emergency";
  users: string[];
  branch: string | null;
  until: number;
  by: string;
  at: number;
  reason: string;
}
export interface DemoClient {
  clients: { id: string; backups: boolean; shell: boolean }[];
  sw: DemoSwitch;
  branches: Record<string, DemoSwitch>;
  allow: string[];
  emergency: boolean;
  passes: DemoPass[];
  /** the client's locks: "*" the whole project, or a branch */
  locks?: Record<string, { by: string; at: number; reason: string }>;
}

/** The client's lock of a branch (the project's first), or null. */
export function clientLock(access: DemoAccess, branch?: string | null) {
  const locks = access.client?.locks ?? {};
  const found = locks["*"] ?? (branch ? locks[branch] : undefined);
  return found ? { ...found, scope: (locks["*"] ? "project" : "branch") as "project" | "branch" } : null;
}

/** A client lock lets through only the clients and a super admin's emergency access. */
export function lockLetsThrough(access: DemoAccess, branch?: string | null): boolean {
  const client = access.client;
  if (!client) return true;
  const me = visitor();
  if (client.clients.some((c) => c.id === me)) return true;
  return client.passes.some(
    (p) => p.kind === "emergency" && p.until > Date.now() && p.users.includes(me) && (!p.branch || p.branch === branch),
  );
}

/** The switch that applies to a branch (its own, else the project's) and where it comes from. */
export function switchOf(client: DemoClient, branch?: string | null): { sw: DemoSwitch; scope: "project" | "branch" } {
  const own = branch ? client.branches[branch] : undefined;
  return own ? { sw: own, scope: "branch" } : { sw: client.sw, scope: "project" };
}

export function closedIn(access: DemoAccess, branch?: string | null): boolean {
  if (!access.client) return false;
  const { sw } = switchOf(access.client, branch);
  return sw.state === "closed" || (sw.state === "timed" && (sw.until ?? 0) <= Date.now());
}

const visitor = () => (session.role === "client" ? CLIENT_ME : ME);

export function letsThrough(access: DemoAccess, branch?: string | null): boolean {
  const client = access.client;
  if (!client) return true;
  const me = visitor();
  if (client.clients.some((c) => c.id === me) || client.allow.includes(me)) return true;
  return client.passes.some((p) => p.until > Date.now() && p.users.includes(me) && (!p.branch || p.branch === branch));
}

/** A project's or branch's switch as the API shows it (clients.describe), or null without a client part. */
export function describeSwitch(access: DemoAccess, branch?: string | null, withYou = true): ClientAccess | null {
  const client = access.client;
  if (!client) return null;
  const { sw, scope } = switchOf(client, branch);
  const closed = closedIn(access, branch);
  const lock = clientLock(access, branch);
  return {
    locked: lock ? { by: lock.by, at: iso(lock.at), reason: lock.reason, scope: lock.scope } : null,
    state: closed ? "closed" : sw.state,
    until: sw.state === "timed" && !closed ? iso(sw.until) : null,
    scope,
    changed_by: sw.by,
    changed_at: iso(sw.at),
    emergency_allowed: client.emergency,
    clients: client.clients.length,
    ...(withYou
      ? { lets_you_through: (!closed || letsThrough(access, branch)) && (!lock || lockLetsThrough(access, branch)) }
      : {}),
  };
}

export type Scope = Stage | "project";
export const STAGES: Stage[] = ["production", "staging", "development"];
export const STAGE_RIGHTS: Permission[] = [
  "instances.view",
  "instances.connect",
  "instances.logs",
  "instances.update",
  "instances.operate",
  "instances.create",
  "instances.delete",
  "instances.credentials",
  "instances.shell",
  "instances.database_shell",
  "backups.manage",
  "instances.restore",
  "instances.upgrade",
  "caught_mail.view",
  "caught_mail.manage",
  "instances.profile",
  "instances.resources",
  "instances.move",
  "domains.manage",
  "offsite.restore",
  "offsite.delete",
];
export const PROJECT_RIGHTS: Permission[] = ["projects.manage", "projects.access", "projects.resources"];
const SEE: Permission[] = ["instances.view"];
const TEST: Permission[] = [...SEE, "instances.connect"];
const DEVELOP_DEV = STAGE_RIGHTS.filter(
  (r) =>
    ![
      "instances.upgrade",
      "instances.move",
      "instances.resources",
      "domains.manage",
      "offsite.restore",
      "offsite.delete",
    ].includes(r),
);
const MAINTAIN_PROD: Permission[] = [...TEST, "instances.logs", "backups.manage", "instances.update"];
export const ROLE_DEFAULTS: Record<AccessLevel, Record<Scope, Permission[]>> = {
  none: { production: [], staging: [], development: [], project: [] },
  view: { production: SEE, staging: SEE, development: SEE, project: [] },
  tester: { production: [], staging: TEST, development: TEST, project: [] },
  developer: { production: SEE, staging: [...TEST, "instances.logs"], development: DEVELOP_DEV, project: [] },
  maintainer: { production: MAINTAIN_PROD, staging: DEVELOP_DEV, development: DEVELOP_DEV, project: [] },
  admin: { production: STAGE_RIGHTS, staging: STAGE_RIGHTS, development: STAGE_RIGHTS, project: PROJECT_RIGHTS },
};
export const EDITABLE_LEVELS: AccessLevel[] = ["view", "tester", "developer", "maintainer"];

export function matrixOf(access: DemoAccess): Record<AccessLevel, Record<Scope, Permission[]>> {
  const found = Object.fromEntries(Object.entries(ROLE_DEFAULTS).map(([l, scopes]) => [l, { ...scopes }])) as Record<
    AccessLevel,
    Record<Scope, Permission[]>
  >;
  for (const [level, scopes] of Object.entries(access.matrix ?? {}))
    for (const [scope, rights] of Object.entries(scopes ?? {}))
      found[level as AccessLevel][scope as Scope] = rights ?? [];
  return found;
}

/** The demo's temporary access: running grants of ME (projects/elevation.py). */
export const elevations: {
  id: string;
  project: string;
  user: string;
  stage: Stage;
  level: AccessLevel;
  hours: number;
  reason: string;
  status: string;
  asked: number;
  expires?: number;
  decided_by?: string;
}[] = [];
const DEFAULT_LEVEL: Record<string, AccessLevel> = {
  maintainer: "maintainer",
  developer: "developer",
  viewer: "view",
  member: "tester",
};

interface DemoTeam {
  id: string;
  name: string;
  description: string;
  members: string[];
  created_at: number;
  created_by: string;
  changed_at: number;
  changed_by: string;
}

export const LEVELS: AccessLevel[] = ["none", "view", "tester", "developer", "maintainer", "admin"];
export const PROJECT_PERMISSIONS: Permission[] = [
  "instances.view",
  "instances.logs",
  "instances.update",
  "instances.operate",
  "instances.create",
  "instances.restore",
  "instances.upgrade",
  "instances.delete",
  "instances.credentials",
  "instances.connect",
  "instances.resources",
  "instances.shell",
  "instances.database_shell",
  "instances.profile",
  "caught_mail.view",
  "caught_mail.manage",
  "backups.manage",
  "offsite.restore",
  "offsite.delete",
  "domains.manage",
  "projects.manage",
  "projects.access",
  "projects.resources",
];
const VIEW: Permission[] = ["instances.view", "instances.logs"];
const DEVELOP: Permission[] = [...VIEW, "instances.update", "caught_mail.view", "instances.shell", "instances.profile"];
const MAINTAIN: Permission[] = [
  ...DEVELOP,
  "instances.operate",
  "instances.restore",
  "instances.create",
  "instances.credentials",
  "instances.connect",
  "projects.manage",
];
const LEVEL_PERMISSIONS: Record<AccessLevel, Permission[]> = {
  none: [],
  view: VIEW,
  tester: VIEW,
  developer: DEVELOP,
  maintainer: MAINTAIN,
  admin: PROJECT_PERMISSIONS,
};

export const teams = new Map<string, DemoTeam>();

export function seedTeams() {
  teams.clear();
  const made = OPENED - 30 * DAY;
  for (const team of [
    {
      id: "leads",
      name: "Leads",
      description: "Release managers: production changes go through them.",
      members: ["omar", "lena"],
    },
    {
      id: "nile-squad",
      name: "Nile squad",
      description: "Developers on the Nile Logistics project.",
      members: ["karim", "ines"],
    },
  ])
    teams.set(team.id, { ...team, created_at: made, created_by: "omar", changed_at: made, changed_by: "omar" });
}

/** The access a project starts with: everyone's role applies (as every project made before teams existed). */
export const openAccess = (): DemoAccess => ({
  mode: "panel_roles",
  grants: [],
  branches: {},
  locks: {},
  model: "project_roles",
  matrix: {},
});

/** The seeded access of Nile Logistics: the squad develops, its production branch is locked to the leads. */
export function seededAccess(id: string): DemoAccess {
  if (id !== "nile-logistics") return openAccess();
  return {
    mode: "panel_roles",
    model: "project_roles",
    matrix: {},
    grants: [
      { type: "team", id: "nile-squad", level: "developer" },
      { type: "github_team", id: "example-integrator/qa", level: "tester" },
    ],
    branches: {},
    locks: {
      main: {
        locked_by: "omar",
        locked_at: Math.round((OPENED - 2 * DAY) / 1000),
        reason: "Peak season: production changes only by the leads.",
        allow: [{ type: "team", id: "leads" }],
      },
    },
  };
}

const myTeams = () => new Set([...teams.values()].filter((t) => t.members.includes(ME)).map((t) => t.id));
const isSuper = () => session.role === "super_admin";
const isAdmin = () => session.role === "super_admin" || session.role === "admin";

function pick(grants: Grant[]): AccessLevel | null {
  const own = grants.find((g) => g.type === "user" && g.id === ME);
  if (own) return own.level;
  const mine = myTeams();
  let best: AccessLevel | null = null;
  for (const grant of grants)
    if (
      grant.type === "team" &&
      mine.has(grant.id) &&
      (best === null || LEVELS.indexOf(grant.level) > LEVELS.indexOf(best))
    )
      best = grant.level;
  return best;
}

export function levelIn(access: DemoAccess, branch?: string | null): AccessLevel {
  if (session.role === "client") return access.client?.clients.some((c) => c.id === CLIENT_ME) ? "view" : "none";
  if (isSuper() || (access.model === "project_roles" && isAdmin())) return "admin";
  const rule = branch ? access.branches[branch] : undefined;
  let found = rule ? pick(rule.grants) : null;
  if (found === null && rule?.restricted) return "none";
  found ??= pick(access.grants);
  if (found !== null) return found;
  if (access.mode === "restricted") return "none";
  return access.model === "project_roles" ? (DEFAULT_LEVEL[session.role] ?? "none") : "admin";
}

export const lockAllows = (lock: BranchLock) => {
  const mine = myTeams();
  return lock.allow.some((e) => (e.type === "user" && e.id === ME) || (e.type === "team" && mine.has(e.id)));
};

/** Project roles: the rights of the level in the branch's stage (any stage without a branch), with running temporary
 *  access; the panel role adds only its panel-wide permissions. */
function rolesPermissions(access: DemoAccess, branch?: string | null): Permission[] {
  const role = roleInfo(session.role).permissions.filter((p) => !PROJECT_PERMISSIONS.includes(p));
  const stage = branch ? (access.stages?.[branch] ?? "development") : null;
  const matrix = matrixOf(access);
  const level = levelIn(access, branch);
  const scopes: Scope[] = stage ? [stage, "project"] : [...STAGES, "project"];
  const granted = new Set<Permission>(scopes.flatMap((s) => matrix[level][s]));
  for (const e of elevations)
    if (e.status === "approved" && (e.expires ?? 0) > Date.now() && (!stage || e.stage === stage))
      matrix[e.level][e.stage].forEach((r) => granted.add(r));
  const lock = branch ? access.locks[branch] : undefined;
  return [...role, ...[...granted].filter((p) => !lock || p === "instances.view" || lockAllows(lock))];
}

export function permissionsIn(access: DemoAccess, branch?: string | null): Permission[] {
  if (access.model === "project_roles") return rolesPermissions(access, branch);
  const role = roleInfo(session.role).permissions;
  const level = LEVEL_PERMISSIONS[levelIn(access, branch)];
  const lock = branch ? access.locks[branch] : undefined;
  const given: Permission[] =
    session.role === "client" && access.client?.clients.find((c) => c.id === CLIENT_ME)?.backups
      ? ["backups.manage"]
      : [];
  const closed =
    (closedIn(access, branch) && !letsThrough(access, branch)) ||
    (!!clientLock(access, branch) && !lockLetsThrough(access, branch));
  return role.filter((p) => {
    if (!PROJECT_PERMISSIONS.includes(p)) return session.role !== "client";
    if (!level.includes(p) && !given.includes(p)) return false;
    if (closed && p !== "instances.view") return false;
    return !lock || p === "instances.view" || lockAllows(lock);
  });
}

/** 404 when hidden, 403 with the reason otherwise; nothing when allowed. */
export function needIn(access: DemoAccess, permission: Permission, name: string, branch?: string | null) {
  if (permissionsIn(access, branch).includes(permission)) return;
  if (access.model === "project_roles") {
    const level = levelIn(access, branch);
    if (!permissionsIn(access, branch).includes("instances.view"))
      throw new Problem(404, branch ? "There is no such branch." : "This project does not exist.");
    const stage = branch ? ` in ${access.stages?.[branch] ?? "development"}` : "";
    const who = {
      none: "People without a role",
      view: "Viewers",
      tester: "Testers",
      developer: "Developers",
      maintainer: "Maintainers",
      admin: "Admins",
    }[level];
    throw new Problem(
      403,
      `${who} of ${name} cannot do this${stage} (${permission}). Ask an admin of the project for temporary access (the project's Access tab, Request access).`,
    );
  }
  if (!roleInfo(session.role).permissions.includes(permission)) return; // the role's own refusal comes from need()
  const level = levelIn(access, branch);
  if (level === "none") throw new Problem(404, branch ? "There is no such branch." : "This project does not exist.");
  const lock = branch ? access.locks[branch] : undefined;
  if (lock && LEVEL_PERMISSIONS[level].includes(permission))
    throw new Problem(
      403,
      `The branch ${branch} is locked by ${lock.locked_by}: only the people and teams the lock allows may do this.`,
    );
  if (clientLock(access, branch) && !lockLetsThrough(access, branch) && LEVEL_PERMISSIONS[level].includes(permission))
    throw new Problem(
      403,
      `${branch ? `The branch ${branch}` : "This project"} is locked by its client: only the client may do this, or unlock it.`,
    );
  if (closedIn(access, branch) && !letsThrough(access, branch) && LEVEL_PERMISSIONS[level].includes(permission))
    throw new Problem(
      403,
      `${branch ? `The branch ${branch}` : "This project"} is closed by its client: only the client and the people it lets in may do this. Ask the client for access (Request access).`,
    );
  throw new Problem(403, `Your access to ${name} does not include this (${level}).`);
}

export function mayUnlock(access: DemoAccess, branch: string): boolean {
  if (access.model === "project_roles") {
    if (!permissionsIn({ ...access, locks: {} }, branch).includes("projects.access")) return false;
    const lock = access.locks[branch];
    return !lock || isSuper() || lockAllows(lock);
  }
  const level = LEVEL_PERMISSIONS[levelIn(access, branch)];
  if (!roleInfo(session.role).permissions.includes("projects.access") || !level.includes("projects.access"))
    return false;
  if (closedIn(access, branch) && !letsThrough(access, branch)) return false;
  const lock = access.locks[branch];
  return !lock || isSuper() || lockAllows(lock);
}

export const seesProject = (access: DemoAccess) =>
  access.model === "project_roles"
    ? permissionsIn(access).includes("instances.view")
    : levelIn(access) !== "none" || Object.keys(access.branches).some((b) => levelIn(access, b) !== "none");

export const subjectName = (subject: AccessSubject): string | null =>
  subject.type === "github_team"
    ? `GitHub team ${subject.id}`
    : subject.type === "team"
      ? (teams.get(subject.id)?.name ?? null)
      : (users.find((u) => u.name === subject.id)?.display_name ?? null);

export const described = <T extends AccessSubject>(entries: T[]): T[] =>
  entries.map((e) => ({ ...e, name: subjectName(e) }));

export function teamResource(team: DemoTeam, usedIn: { id: string; name: string }[]): Team {
  return {
    id: team.id,
    name: team.name,
    description: team.description,
    members: team.members
      .map((name) => users.find((u) => u.name === name))
      .filter((u) => !!u)
      .map((u) => ({ name: u!.name, display_name: u!.display_name, role: u!.role })),
    projects: usedIn,
    created_at: iso(team.created_at),
    created_by: team.created_by,
    changed_at: iso(team.changed_at),
    changed_by: team.changed_by,
  };
}

/** Each instance carries its branch's lock and its client's switch, as the panel's instance resource does. */
export function setLocks(projectId: string, access: DemoAccess, announce: (i: Instance) => void) {
  for (const instance of instances.values()) {
    if (instance.project?.id !== projectId) continue;
    const lock = instance.git.branch ? (access.locks[instance.git.branch] ?? null) : null;
    const closure = describeSwitch(access, instance.git.branch, false);
    if (
      JSON.stringify(instance.lock ?? null) !== JSON.stringify(lock) ||
      JSON.stringify(instance.client_access ?? null) !== JSON.stringify(closure)
    ) {
      instance.lock = lock ? { ...lock, allow: described(lock.allow) } : null;
      instance.client_access = closure;
      announce(instance);
    }
  }
}
