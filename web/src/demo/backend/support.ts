// The demo's support access (the premium "support" package: GET /api/v1/support-access and the rest): one active
// grant for a Nile Logistics ticket (nobody from support connected now), and two earlier ones (expired, withdrawn) with the sessions support opened. New
// grants, withdrawing one and "End now" change it as on a real panel; nobody from support ever connects here.

import type { SupportGrant, SupportLevel, SupportOverview, SupportStatus } from "@/api/types";
import { publish } from "./bus";
import { projects } from "./projects";
import { instances, ME, record } from "./state";
import { DAY, hex, HOUR, iso, MINUTE, OPENED, Problem } from "./util";

const LEVELS: SupportLevel[] = ["view", "admin", "admin_shell"];
const DURATIONS = [1, 24, 72];

export const grants: SupportGrant[] = [];

export function seedSupport() {
  grants.length = 0;
  const grant = (values: Partial<SupportGrant> & Pick<SupportGrant, "id" | "level" | "hours">, at: number): SupportGrant => ({
    scope: { all: true, projects: [], instances: [] },
    ticket: null,
    created_by: "omar",
    created_at: iso(at),
    expires_at: iso(at + values.hours! * HOUR),
    revoked_at: null,
    revoked_by: null,
    state: "active",
    sessions: [],
    ...values,
  });
  const now = OPENED - 3 * HOUR;
  grants.push(
    grant(
      {
        id: "sg-7f3a91c2",
        level: "admin",
        hours: 24,
        ticket: "SUP-2481",
        scope: { all: false, projects: [{ id: "nile-logistics", name: "Nile Logistics" }], instances: [] },
      },
      now,
    ),
    grant(
      {
        id: "sg-2b8e0d47",
        level: "view",
        hours: 72,
        ticket: "SUP-2317",
        state: "expired",
        sessions: [{ id: "ss-19ac", grant_id: "sg-2b8e0d47", staff_name: "Malik (Sudoo.sh support)", opened_at: iso(OPENED - 11 * DAY), expires_at: iso(OPENED - 11 * DAY + HOUR) }],
      },
      OPENED - 12 * DAY,
    ),
    grant(
      { id: "sg-c05e6a13", level: "admin_shell", hours: 1, ticket: null, created_by: "sara", state: "withdrawn", revoked_at: iso(OPENED - 26 * DAY + 20 * MINUTE), revoked_by: "sara" },
      OPENED - 26 * DAY,
    ),
  );
}

function expire() {
  for (const g of grants) if (g.state === "active" && g.expires_at && Date.parse(g.expires_at) < Date.now()) g.state = "expired";
}

export function supportOverview(): SupportOverview {
  expire();
  return {
    enabled: true,
    unavailable: null,
    max_hours: 72,
    durations: DURATIONS,
    levels: LEVELS,
    shell_enabled: true,
    grants: [...grants].sort((a, b) => Date.parse(b.created_at ?? "") - Date.parse(a.created_at ?? "")),
    sessions: [], // nobody from support is connected now
    channel: { last_ok: iso(Date.now() - 40 * 1000), error: null },
  };
}

export function supportStatus(canEnd: boolean): SupportStatus {
  expire();
  return { connected: false, sessions: [], active_grants: grants.filter((g) => g.state === "active").length, can_end: canEnd };
}

export function grantAccess(body: Record<string, unknown>): SupportGrant {
  const level = body.level as SupportLevel;
  if (!LEVELS.includes(level)) throw new Problem(400, `level: one of ${LEVELS.join(", ")}.`);
  const hours = Number(body.hours);
  if (!DURATIONS.includes(hours)) throw new Problem(400, `hours: one of ${DURATIONS.join(", ")}.`);
  const ticket = typeof body.ticket === "string" && body.ticket.trim() ? body.ticket.trim().slice(0, 40) : null;
  const scopeIn = (body.scope ?? {}) as { all?: unknown; projects?: unknown; instances?: unknown };
  let scope: SupportGrant["scope"] = { all: true, projects: [], instances: [] };
  if (scopeIn.all !== true) {
    const chosen = Array.isArray(scopeIn.projects) ? scopeIn.projects.map(String) : [];
    const names = Array.isArray(scopeIn.instances) ? scopeIn.instances.map(String) : [];
    for (const id of chosen) if (!projects.has(id)) throw new Problem(400, `scope.projects: there is no project ${id}.`);
    for (const name of names) if (!instances.has(name)) throw new Problem(400, `scope.instances: there is no instance ${name}.`);
    if (!chosen.length && !names.length) throw new Problem(400, "Choose at least one project or instance.");
    scope = { all: false, projects: chosen.map((id) => ({ id, name: projects.get(id)!.name })), instances: names };
  }
  const now = Date.now();
  const grant: SupportGrant = {
    id: `sg-${hex(8)}`,
    level,
    scope,
    ticket,
    hours,
    created_by: ME,
    created_at: iso(now),
    expires_at: iso(now + hours * HOUR),
    revoked_at: null,
    revoked_by: null,
    state: "active",
    sessions: [],
  };
  grants.push(grant);
  record(ME, "support access granted", { detail: `${level} for ${hours} h${ticket ? `, ticket ${ticket}` : ""}` });
  publish("support", grant.id, null);
  return grant;
}

export function withdraw(id: string) {
  const grant = grants.find((g) => g.id === id);
  if (!grant) throw new Problem(404, "There is no such grant.");
  if (grant.state === "active") {
    grant.state = "withdrawn";
    grant.revoked_at = iso(Date.now());
    grant.revoked_by = ME;
    record(ME, "support access withdrawn", { detail: grant.id });
    publish("support", grant.id, null);
  }
}

export function endAll() {
  for (const grant of grants) if (grant.state === "active") withdraw(grant.id);
}
