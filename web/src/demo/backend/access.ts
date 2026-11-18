// Teams, per-project and per-branch access and locked branches, as src/sudoo/projects/access.py decides them: what
// someone may do in a project is their role ∩ their level there; a locked branch is closed to everyone its lock does
// not allow (super admins included, until they unlock it). The demo's visitor is the only one who acts.

import type { AccessLevel, AccessMode, AccessSubject, BranchLock, Grant, Instance, Permission, Team } from "@/api/types";
import { instances, ME, roleInfo, session, users } from "./state";
import { iso, OPENED, DAY, Problem } from "./util";

export interface DemoAccess {
  mode: AccessMode;
  grants: Grant[];
  branches: Record<string, { restricted: boolean; grants: Grant[] }>;
  locks: Record<string, BranchLock>;
}

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

export const LEVELS: AccessLevel[] = ["none", "view", "developer", "maintainer", "admin"];
export const PROJECT_PERMISSIONS: Permission[] = [
  "instances.view", "instances.logs", "instances.update", "instances.operate", "instances.create", "instances.restore",
  "instances.upgrade", "instances.delete", "instances.credentials", "instances.resources", "instances.shell", "instances.database_shell",
  "caught_mail.view", "caught_mail.manage", "backups.manage", "offsite.restore", "offsite.delete", "domains.manage",
  "projects.manage", "projects.access",
];
const VIEW: Permission[] = ["instances.view", "instances.logs"];
const DEVELOP: Permission[] = [...VIEW, "instances.update", "caught_mail.view", "instances.shell"];
const MAINTAIN: Permission[] = [...DEVELOP, "instances.operate", "instances.restore", "instances.create", "instances.credentials", "projects.manage"];
const LEVEL_PERMISSIONS: Record<AccessLevel, Permission[]> = { none: [], view: VIEW, developer: DEVELOP, maintainer: MAINTAIN, admin: PROJECT_PERMISSIONS };

export const teams = new Map<string, DemoTeam>();

export function seedTeams() {
  teams.clear();
  const made = OPENED - 30 * DAY;
  for (const team of [
    { id: "leads", name: "Leads", description: "Release managers: production changes go through them.", members: ["omar", "lena"] },
    { id: "nile-squad", name: "Nile squad", description: "Developers on the Nile Logistics project.", members: ["karim", "ines"] },
  ])
    teams.set(team.id, { ...team, created_at: made, created_by: "omar", changed_at: made, changed_by: "omar" });
}

/** The access a project starts with: everyone's role applies (as every project made before teams existed). */
export const openAccess = (): DemoAccess => ({ mode: "panel_roles", grants: [], branches: {}, locks: {} });

/** The seeded access of Nile Logistics: the squad develops, its production branch is locked to the leads. */
export function seededAccess(id: string): DemoAccess {
  if (id !== "nile-logistics") return openAccess();
  return {
    mode: "panel_roles",
    grants: [{ type: "team", id: "nile-squad", level: "developer" }],
    branches: {},
    locks: { main: { locked_by: "omar", locked_at: Math.round((OPENED - 2 * DAY) / 1000), reason: "Peak season: production changes only by the leads.", allow: [{ type: "team", id: "leads" }] } },
  };
}

const myTeams = () => new Set([...teams.values()].filter((t) => t.members.includes(ME)).map((t) => t.id));
const isSuper = () => session.role === "super_admin";

function pick(grants: Grant[]): AccessLevel | null {
  const own = grants.find((g) => g.type === "user" && g.id === ME);
  if (own) return own.level;
  const mine = myTeams();
  let best: AccessLevel | null = null;
  for (const grant of grants)
    if (grant.type === "team" && mine.has(grant.id) && (best === null || LEVELS.indexOf(grant.level) > LEVELS.indexOf(best))) best = grant.level;
  return best;
}

export function levelIn(access: DemoAccess, branch?: string | null): AccessLevel {
  if (isSuper()) return "admin";
  const rule = branch ? access.branches[branch] : undefined;
  let found = rule ? pick(rule.grants) : null;
  if (found === null && rule?.restricted) return "none";
  found ??= pick(access.grants);
  return found ?? (access.mode === "restricted" ? "none" : "admin");
}

export const lockAllows = (lock: BranchLock) => {
  const mine = myTeams();
  return lock.allow.some((e) => (e.type === "user" && e.id === ME) || (e.type === "team" && mine.has(e.id)));
};

export function permissionsIn(access: DemoAccess, branch?: string | null): Permission[] {
  const role = roleInfo(session.role).permissions;
  const level = LEVEL_PERMISSIONS[levelIn(access, branch)];
  const lock = branch ? access.locks[branch] : undefined;
  return role.filter((p) => {
    if (!PROJECT_PERMISSIONS.includes(p)) return true;
    if (!level.includes(p)) return false;
    return !lock || p === "instances.view" || lockAllows(lock);
  });
}

/** 404 when hidden, 403 with the reason otherwise; nothing when allowed. */
export function needIn(access: DemoAccess, permission: Permission, name: string, branch?: string | null) {
  if (permissionsIn(access, branch).includes(permission)) return;
  if (!roleInfo(session.role).permissions.includes(permission)) return; // the role's own refusal comes from need()
  const level = levelIn(access, branch);
  if (level === "none") throw new Problem(404, branch ? "There is no such branch." : "This project does not exist.");
  const lock = branch ? access.locks[branch] : undefined;
  if (lock && LEVEL_PERMISSIONS[level].includes(permission))
    throw new Problem(403, `The branch ${branch} is locked by ${lock.locked_by}: only the people and teams the lock allows may do this.`);
  throw new Problem(403, `Your access to ${name} does not include this (${level}).`);
}

export function mayUnlock(access: DemoAccess, branch: string): boolean {
  const level = LEVEL_PERMISSIONS[levelIn(access, branch)];
  if (!roleInfo(session.role).permissions.includes("projects.access") || !level.includes("projects.access")) return false;
  const lock = access.locks[branch];
  return !lock || isSuper() || lockAllows(lock);
}

export const seesProject = (access: DemoAccess) =>
  levelIn(access) !== "none" || Object.keys(access.branches).some((b) => levelIn(access, b) !== "none");

export const subjectName = (subject: AccessSubject): string | null =>
  subject.type === "team" ? (teams.get(subject.id)?.name ?? null) : (users.find((u) => u.name === subject.id)?.display_name ?? null);

export const described = <T extends AccessSubject>(entries: T[]): T[] => entries.map((e) => ({ ...e, name: subjectName(e) }));

export function teamResource(team: DemoTeam, usedIn: { id: string; name: string }[]): Team {
  return {
    id: team.id,
    name: team.name,
    description: team.description,
    members: team.members.map((name) => users.find((u) => u.name === name)).filter((u) => !!u).map((u) => ({ name: u!.name, display_name: u!.display_name, role: u!.role })),
    projects: usedIn,
    created_at: iso(team.created_at),
    created_by: team.created_by,
    changed_at: iso(team.changed_at),
    changed_by: team.changed_by,
  };
}

/** Each instance carries its branch's lock, as the panel's instance resource does. */
export function setLocks(projectId: string, access: DemoAccess, announce: (i: Instance) => void) {
  for (const instance of instances.values()) {
    if (instance.project?.id !== projectId) continue;
    const lock = instance.git.branch ? (access.locks[instance.git.branch] ?? null) : null;
    if (JSON.stringify(instance.lock ?? null) !== JSON.stringify(lock)) {
      instance.lock = lock ? { ...lock, allow: described(lock.allow) } : null;
      announce(instance);
    }
  }
}
