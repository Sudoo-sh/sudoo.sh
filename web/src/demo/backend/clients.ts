// The client portal in the demo (src/sudoo/projects/clients.py, web/v1/clients.py): Maya Rahman of Acme Retail is the
// client of Acme Retail (production closed, emergency access allowed, a request waiting) and of Zahra Clinic (closed,
// emergency access off). The "Client" perspective shows her portal; the others show the staff side: the closed
// branch, Request access, emergency access for super admins, and the project's Clients tab.

import type { AccessPass, AccessRequest, ActivityEntry, ClientBackup, ClientInstance, ClientProject, ClientProjectSummary, PersonRef, SwitchState } from "@/api/types";
import { CLIENT_ME, closedIn, describeSwitch, setLocks, type DemoAccess, type DemoClient, type DemoPass } from "./access";
import { created, none, ok, route } from "./api";
import { announce } from "./jobs";
import { branchNames, membersOf, projects, stageOf, type DemoProject } from "./projects";
import { audit, backups, ME, record, roleInfo, session, setting, users } from "./state";
import { DAY, hex, HOUR, iso, MINUTE, OPENED, Problem } from "./util";

interface DemoRequest {
  id: string;
  project: string;
  branch: string | null;
  by: string;
  reason: string;
  hours: number;
  status: AccessRequest["status"];
  at: number;
  decided_by?: string;
  decided_at?: number;
  users?: string[];
  until?: number;
  note?: string;
}

const requests: DemoRequest[] = [];
const OPEN_HOURS = [1, 4, 24];
const EMERGENCY_MINUTES = 60;

const person = (name: string): PersonRef => {
  const user = users.find((u) => u.name === name);
  return { name, display_name: user?.display_name ?? name };
};
const visitor = () => (session.role === "client" ? CLIENT_ME : ME);

function log(event: string, project: string, branch: string | null, detail: string, at = Date.now(), user = visitor(), ok = true) {
  record(user, event, { detail: `${branch ? `${project} ${branch}` : project}: ${detail}`, project, ...(branch ? { branch } : {}), ok }, at);
}

export function seedClients() {
  requests.length = 0;
  const acme = projects.get("acme-retail");
  const zahra = projects.get("zahra-clinic");
  if (acme) {
    acme.access.client = {
      clients: [{ id: CLIENT_ME, backups: true, shell: false }],
      sw: { state: "open", until: null, by: CLIENT_ME, at: OPENED - 40 * DAY },
      branches: { main: { state: "closed", until: null, by: CLIENT_ME, at: OPENED - 2 * DAY } },
      allow: ["lena"],
      emergency: true,
      passes: [
        { id: hex(8), kind: "emergency", users: ["omar"], branch: "main", until: OPENED - 2 * HOUR, by: "omar", at: OPENED - 3 * HOUR,
          reason: "Every till fails at payment since the payment provider renewed its certificate." },
      ],
    };
    requests.push(
      { id: hex(10), project: "acme-retail", branch: "main", by: "karim", reason: "The Z-report totals differ from the tills: I need production's log for yesterday.", hours: 4, status: "pending", at: OPENED - 40 * MINUTE },
      { id: hex(10), project: "acme-retail", branch: "main", by: "lena", reason: "Install the new price lists before Monday's opening.", hours: 2, status: "approved", at: OPENED - 6 * DAY, decided_by: CLIENT_ME, decided_at: OPENED - 6 * DAY + 20 * MINUTE, users: ["lena"], until: OPENED - 6 * DAY + 2 * HOUR },
    );
    log("client access closed", "acme-retail", "main", "closed", OPENED - 2 * DAY, CLIENT_ME);
    log("client's named people changed", "acme-retail", null, "Lena Brandt", OPENED - 2 * DAY + MINUTE, CLIENT_ME);
    log("refused: instances.credentials in project acme-retail", "acme-retail", "main", "closed by its client", OPENED - 26 * HOUR, "sara", false);
    log("emergency access used", "acme-retail", "main", "until the hour after; every till fails at payment since the payment provider renewed its certificate", OPENED - 3 * HOUR, "omar");
    log("instance credentials viewed", "acme-retail", "main", "acme-retail-main", OPENED - 3 * HOUR + 4 * MINUTE, "omar");
    log("shell opened", "acme-retail", "main", "odoo container, recorded", OPENED - 3 * HOUR + 9 * MINUTE, "omar");
    log("access ended (time over)", "acme-retail", "main", "Omar Haddad (emergency)", OPENED - 2 * HOUR, "system");
    log("access requested", "acme-retail", "main", "4 h; The Z-report totals differ from the tills", OPENED - 40 * MINUTE, "karim");
    setLocks("acme-retail", acme.access, () => undefined);
  }
  if (zahra) {
    zahra.access.client = {
      clients: [{ id: CLIENT_ME, backups: false, shell: false }],
      sw: { state: "closed", until: null, by: CLIENT_ME, at: OPENED - 5 * DAY },
      branches: {},
      allow: [],
      emergency: false,
      passes: [],
    };
    requests.push({ id: hex(10), project: "zahra-clinic", branch: null, by: "ines", reason: "Test the appointment reminders on a copy.", hours: 8, status: "refused", at: OPENED - 4 * DAY, decided_by: CLIENT_ME, decided_at: OPENED - 4 * DAY + HOUR, note: "Next week, please." });
    log("client access closed", "zahra-clinic", null, "closed", OPENED - 5 * DAY, CLIENT_ME);
    log("emergency access turned off", "zahra-clinic", null, "zahra-clinic", OPENED - 5 * DAY + MINUTE, CLIENT_ME);
    setLocks("zahra-clinic", zahra.access, () => undefined);
  }
}

// ---------------------------------------------------------------- reading

const passResource = (p: DemoPass): AccessPass => ({
  id: p.id, kind: p.kind, branch: p.branch, people: p.users.map(person), by: p.by, at: iso(p.at)!, until: iso(p.until)!, reason: p.reason,
});

const requestResource = (r: DemoRequest): AccessRequest => ({
  id: r.id, project: r.project, branch: r.branch, by: person(r.by), reason: r.reason, hours: r.hours, status: r.status,
  asked_at: iso(r.at)!, decided_by: r.decided_by ?? null, decided_at: iso(r.decided_at ?? null), people: (r.users ?? []).map(person),
  until: iso(r.until ?? null), note: r.note ?? null,
});

function myProject(id: string): DemoProject & { access: DemoAccess & { client: DemoClient } } {
  const found = projects.get(id);
  if (session.role !== "client" || !found?.access.client?.clients.some((c) => c.id === CLIENT_ME)) throw new Problem(404, "This project does not exist.");
  return found as DemoProject & { access: DemoAccess & { client: DemoClient } };
}

const branchesOf = (project: DemoProject) =>
  [...new Set([...branchNames(project).filter((b) => membersOf(project.id).some((i) => i.git.branch === b)), ...Object.keys(project.access.client?.branches ?? {})])];

function summary(project: DemoProject): ClientProjectSummary {
  const active = (project.access.client?.passes ?? []).filter((p) => p.until > Date.now());
  return {
    id: project.id,
    name: project.name,
    switch: describeSwitch(project.access, null, false),
    closed_branches: branchesOf(project).filter((b) => closedIn(project.access, b)),
    pending_requests: requests.filter((r) => r.project === project.id && r.status === "pending").length,
    emergency_active: active.some((p) => p.kind === "emergency"),
    instances: membersOf(project.id).length,
  };
}

const staffPeople = (): PersonRef[] => users.filter((u) => u.role !== "client").map((u) => ({ ...person(u.name), role: u.role_label }));

function detail(project: DemoProject & { access: DemoAccess & { client: DemoClient } }): ClientProject {
  const client = project.access.client;
  const me = client.clients.find((c) => c.id === CLIENT_ME)!;
  const order = { production: 0, staging: 1, development: 2 } as const;
  const branches = branchesOf(project).map((name) => ({
    name,
    stage: stageOf(project, name),
    switch: describeSwitch(project.access, name, false),
    instances: membersOf(project.id).filter((i) => i.git.branch === name).map((i): ClientInstance => ({
      name: i.name, status: i.status, stage: i.stage, url: i.url, odoo: i.odoo.version, branch: i.git.branch, current_job: i.current_job?.action ?? null,
    })),
  })).sort((a, b) => order[a.stage] - order[b.stage] || a.name.localeCompare(b.name));
  return {
    ...summary(project),
    branches,
    allow: client.allow.map(person),
    people: staffPeople(),
    emergency_allowed: client.emergency,
    passes: client.passes.filter((p) => p.until > Date.now()).map(passResource),
    requests: requests.filter((r) => r.project === project.id).sort((a, b) => b.at - a.at).map(requestResource),
    notices: client.passes.filter((p) => p.kind === "emergency" && p.at > Date.now() - 7 * DAY).map(passResource),
    given: { backups: me.backups, shell: me.shell },
  };
}

function changed(project: DemoProject) {
  setLocks(project.id, project.access, announce);
}

// ---------------------------------------------------------------- the client

route("GET", "/portal", () =>
  // the "Client" perspective signs in at the portal's own address: its sign-in page looks like it
  ok({ address: session.role === "client" ? "portal" : "panel", portal: session.role === "client", portal_configured: true, name: "Example Integrators", brand_color: String(setting("brand_color") ?? "#714B67"), default_language: "en" }),
);

route("GET", "/client/projects", () => {
  if (session.role !== "client") throw new Problem(404, "Only clients have a client portal.");
  const mine = [...projects.values()].filter((p) => p.access.client?.clients.some((c) => c.id === CLIENT_ME));
  return ok(mine.map(summary), { portal_name: "Example Integrators" });
});

route("GET", "/client/projects/:id", ({ params }) =>
  ok(detail(myProject(params.id!)), { open_hours: OPEN_HOURS, open_max_hours: 720, request_max_hours: 168, emergency_minutes: EMERGENCY_MINUTES }),
);

route("PUT", "/client/projects/:id/switch", ({ params, body }) => {
  const project = myProject(params.id!);
  const client = project.access.client;
  const branch = (body.branch as string | null | undefined) ?? null;
  const state = body.state as SwitchState;
  if (!["open", "closed", "timed"].includes(state)) throw new Problem(400, "state: open, closed or timed.");
  const hours = Number(body.hours ?? 0);
  if (state === "timed" && !(hours > 0 && hours <= 720)) throw new Problem(400, "Choose how long to open it: at most 720 hours.");
  const sw = { state, until: state === "timed" ? Date.now() + hours * HOUR : null, by: CLIENT_ME, at: Date.now() };
  if (branch && body.inherit) delete client.branches[branch];
  else if (branch) client.branches[branch] = sw;
  else client.sw = sw;
  if (state === "closed") client.passes = client.passes.filter((p) => p.until > Date.now() && branch !== null && p.branch !== null && p.branch !== branch);
  log(body.inherit ? "client access follows the project again" : state === "timed" ? "client access opened for a time" : `client access ${state === "closed" ? "closed" : "opened"}`,
    project.id, branch, state === "timed" ? `open for ${hours} h` : state);
  changed(project);
  return ok({ project: describeSwitch(project.access, null), branches: {} });
});

route("PUT", "/client/projects/:id/allow", ({ params, body }) => {
  const project = myProject(params.id!);
  const people = (body.people as string[] | undefined) ?? [];
  if (people.some((p) => !users.some((u) => u.name === p && u.role !== "client"))) throw new Problem(400, "Name people of the team who see this project.");
  project.access.client.allow = [...new Set(people)];
  log("client's named people changed", project.id, null, people.map((p) => person(p).display_name).join(", ") || "nobody");
  changed(project);
  return ok(project.access.client.allow.map(person));
});

route("PUT", "/client/projects/:id/emergency", ({ params, body }) => {
  const project = myProject(params.id!);
  project.access.client.emergency = Boolean(body.allowed);
  log(body.allowed ? "emergency access allowed" : "emergency access turned off", project.id, null, project.id);
  changed(project);
  return ok({ emergency_allowed: project.access.client.emergency });
});

route("DELETE", "/client/projects/:id/passes/:pass", ({ params }) => {
  const project = myProject(params.id!);
  const found = project.access.client.passes.find((p) => p.id === params.pass && p.until > Date.now());
  if (!found) throw new Problem(404, "This access has ended already.");
  project.access.client.passes = project.access.client.passes.filter((p) => p !== found);
  log("access ended by the client", project.id, found.branch, `${found.users.join(", ")} (${found.kind})`);
  changed(project);
  return none();
});

function myRequest(id: string) {
  const found = requests.find((r) => r.id === id);
  if (!found) throw new Problem(404, "There is no such request.");
  const project = myProject(found.project);
  if (found.status !== "pending") throw new Problem(409, `This request was ${found.status} already.`);
  return { found, project };
}

route("POST", "/client/requests/:id/approve", ({ params, body }) => {
  const { found, project } = myRequest(params.id!);
  const hours = Number(body.hours ?? found.hours);
  const people = (body.people as string[] | undefined) ?? [found.by];
  if (!(hours > 0 && hours <= 168)) throw new Problem(400, "Give at most 168 hours.");
  Object.assign(found, { status: "approved", decided_by: CLIENT_ME, decided_at: Date.now(), users: people, until: Date.now() + hours * HOUR, hours, note: String(body.note ?? "") || undefined });
  project.access.client.passes.push({ id: hex(8), kind: "request", users: people, branch: found.branch, until: found.until!, by: CLIENT_ME, at: Date.now(), reason: found.reason });
  log("access request approved", project.id, found.branch, `${people.map((p) => person(p).display_name).join(", ")} for ${hours} h`);
  changed(project);
  return ok(requestResource(found));
});

route("POST", "/client/requests/:id/refuse", ({ params, body }) => {
  const { found, project } = myRequest(params.id!);
  Object.assign(found, { status: "refused", decided_by: CLIENT_ME, decided_at: Date.now(), note: String(body.note ?? "") || undefined });
  log("access request refused", project.id, found.branch, `asked by ${found.by}`);
  return ok(requestResource(found));
});

route("GET", "/client/projects/:id/backups", ({ params }) => {
  const project = myProject(params.id!);
  const me = project.access.client.clients.find((c) => c.id === CLIENT_ME)!;
  if (!me.backups) return ok([], { given: false });
  const names = new Set(membersOf(project.id).map((i) => i.name));
  const shown: ClientBackup[] = backups.filter((b) => names.has(b.instance)).map((b) => ({ file: b.file, instance: b.instance, kind: b.kind, size_bytes: b.size, created_at: iso(b.created * 1000)! }));
  return ok(shown, { given: true });
});

const ACCESS_WORDS = ["client", "access", "emergency", "credentials", "backup", "shell", "restor", "refused", "sign"];

route("GET", "/client/projects/:id/activity", ({ params, query }) => {
  const project = myProject(params.id!);
  const names = new Set(membersOf(project.id).map((i) => i.name));
  const kind = query.get("kind") === "access" ? "access" : "all";
  const limit = Math.min(500, Math.max(1, Number(query.get("limit") ?? 100)));
  const offset = Math.max(0, Number(query.get("offset") ?? 0));
  const found = audit
    .filter((e) => e.project === project.id || (e.instance && names.has(e.instance)))
    .filter((e) => kind === "all" || ACCESS_WORDS.some((w) => e.event.includes(w)));
  const shown: ActivityEntry[] = found.slice(offset, offset + limit).map((e) => ({
    at: iso(e.ts * 1000)!, user: e.user, event: e.event, allowed: e.ok, branch: e.branch ?? null, instance: e.instance ?? null, detail: e.detail ?? null,
    address: e.user === CLIENT_ME ? e.ip : null, emergency: e.event.includes("emergency"),
  }));
  return ok(shown, { total: found.length, limit, offset });
});

// ---------------------------------------------------------------- the panel's people

function staffProject(id: string) {
  const found = projects.get(id);
  if (!found || session.role === "client") throw new Problem(404, "This project does not exist.");
  return found;
}

route("GET", "/projects/:id/clients", ({ params }) => {
  const project = staffProject(params.id!);
  const client = project.access.client;
  const manages = roleInfo(session.role).permissions.includes("clients.manage");
  return ok(
    {
      clients: (client?.clients ?? []).map((c) => ({ ...person(c.id), email: manages ? (users.find((u) => u.name === c.id)?.email ?? null) : null, backups: c.backups, shell: c.shell, added_by: "omar", added_at: iso(OPENED - 40 * DAY) })),
      invited: [],
      project: describeSwitch(project.access, null),
      branches: Object.fromEntries(branchesOf(project).map((b) => [b, describeSwitch(project.access, b)])),
      allow: (client?.allow ?? []).map(person),
      emergency_allowed: client?.emergency ?? true,
      passes: (client?.passes ?? []).filter((p) => p.until > Date.now()).map(passResource),
      requests: requests.filter((r) => r.project === project.id).sort((a, b) => b.at - a.at).map(requestResource),
    },
    { may_manage: manages && !closedIn(project.access, null), may_force: session.role === "super_admin", emergency_minutes: EMERGENCY_MINUTES, request_hours: 4, request_max_hours: 168, portal_url: "https://portal.example-integrators.com" },
  );
});

route("POST", "/projects/:id/clients", ({ params, body }) => {
  const project = staffProject(params.id!);
  if (!roleInfo(session.role).permissions.includes("clients.manage")) throw new Problem(403, "Your role cannot do this (invite clients to projects and take them out).");
  if (closedIn(project.access, null) || branchesOf(project).some((b) => closedIn(project.access, b)))
    throw new Problem(409, "The project or one of its branches is closed by its client: a new client can be added once the client opens access again.");
  const email = String(body.email ?? "").trim();
  if (!email.includes("@")) throw new Problem(400, "Enter the client's e-mail address.");
  const existing = users.find((u) => u.email === email);
  if (existing && existing.role !== "client") throw new Problem(409, "This address belongs to someone of your team, not to a client account.");
  project.access.client ??= { clients: [], sw: { state: "open", until: null, by: ME, at: Date.now() }, branches: {}, allow: [], emergency: true, passes: [] };
  if (existing) project.access.client.clients.push({ id: existing.name, backups: Boolean(body.backups), shell: Boolean(body.shell) });
  record(ME, existing ? "client added to the project" : "client invited", { detail: `${project.id}: ${email}` });
  changed(project);
  return created({ invited: !existing });
});

route("DELETE", "/projects/:id/clients/:name", ({ params }) => {
  const project = staffProject(params.id!);
  const client = project.access.client;
  if (!client?.clients.some((c) => c.id === params.name)) throw new Problem(404, `${params.name} is not a client of this project.`);
  if (client.clients.length === 1 && (closedIn(project.access, null) || branchesOf(project).some((b) => closedIn(project.access, b))))
    throw new Problem(409, "The project or one of its branches is closed by this client, its only client: the client opens access first.");
  client.clients = client.clients.filter((c) => c.id !== params.name);
  record(ME, "client removed from the project", { detail: `${project.id}: ${params.name}` });
  changed(project);
  return none();
});

route("POST", "/projects/:id/access-requests", ({ params, body }) => {
  const project = staffProject(params.id!);
  const branch = (body.branch as string | null | undefined) ?? null;
  const reason = String(body.reason ?? "").trim();
  if (!closedIn(project.access, branch)) throw new Problem(409, "This is open: there is nothing to ask for.");
  if (reason.length < 10) throw new Problem(400, "Say why you need access (at least 10 characters).");
  if (requests.some((r) => r.project === project.id && r.branch === branch && r.by === ME && r.status === "pending")) throw new Problem(409, "You asked already: the client has not answered yet.");
  const made: DemoRequest = { id: hex(10), project: project.id, branch, by: ME, reason, hours: Number(body.hours ?? 4), status: "pending", at: Date.now() };
  requests.push(made);
  log("access requested", project.id, branch, `${made.hours} h; ${reason}`, Date.now(), ME);
  return created(requestResource(made));
});

route("POST", "/projects/:id/emergency-access", ({ params, body }) => {
  const project = staffProject(params.id!);
  const branch = (body.branch as string | null | undefined) ?? null;
  const client = project.access.client;
  if (session.role !== "super_admin") throw new Problem(403, "Only a super admin, signed in, may use emergency access.");
  if (!client || !closedIn(project.access, branch)) throw new Problem(409, "This is open: no emergency access is needed.");
  if (!client.emergency) throw new Problem(403, "The client turned emergency access off for this project: nobody in the panel can force access. Ask the client.");
  const reason = String(body.reason ?? "").trim();
  if (reason.length < 10) throw new Problem(400, "Say why (at least 10 characters): the client reads it.");
  if (String(body.confirm ?? "").trim() !== (branch ?? project.id)) throw new Problem(400, `Type ${branch ?? project.id} to confirm.`);
  const made: DemoPass = { id: hex(8), kind: "emergency", users: [ME], branch, until: Date.now() + EMERGENCY_MINUTES * MINUTE, by: ME, at: Date.now(), reason };
  client.passes.push(made);
  log("emergency access used", project.id, branch, reason, Date.now(), ME);
  changed(project);
  return created(passResource(made));
});
