// The demo's plans and premium features (premium.ts, servers.ts, support.ts), through the demo's own /api/v1 as the
// web app calls it: what each plan reports, the 402 answers of a feature the plan lacks, and the servers and support
// access of the Business plan. Run with `npm test`.

import { afterEach, beforeAll, beforeEach, describe, expect, test, vi } from "vitest";
import { answer } from "./api";
import { seedTeams } from "./access";
import { seedDomains } from "./domains";
import { currentPlan, loadPlan, setFilesForTests, setPlan, type DemoPlan } from "./premium";
import { seedProjects } from "./projects";
import { seedServers } from "./servers";
import { seedAll, session } from "./state";
import { seedSupport } from "./support";

// the little of a browser the demo's backend reads while it loads (the language of a signed-out visitor)
vi.hoisted(() => {
  const g = globalThis as Record<string, unknown>;
  g.document ??= { cookie: "", documentElement: { lang: "en", dir: "ltr", dataset: {} } };
});

const FILES = Object.fromEntries(
  ["teams", "domains", "offsite", "support", "servers"].map((id) => [
    id,
    {
      version: "1.0.0",
      routes: [],
      sections: [],
      styles: [],
      strings: "ui/strings.json",
      files: { [`ui/${id}.js`]: "a".repeat(64), "ui/strings.json": "b".repeat(64) },
    },
  ]),
);

type Body = { data?: unknown; meta?: Record<string, unknown>; detail?: string; status?: number };

async function call(method: string, path: string, body?: unknown) {
  const [route, search] = path.split("?");
  const found = await answer(method, route!, new URLSearchParams(search ?? ""), body);
  return { status: found.status, body: (found.body ?? {}) as Body };
}

const data = async <T>(path: string) => (await call("GET", path)).body.data as T;

function seed(plan: DemoPlan) {
  seedAll();
  seedTeams();
  seedProjects();
  seedDomains();
  setPlan(plan);
  seedServers();
  seedSupport();
}

beforeAll(() => {
  session.signedIn = true;
});

describe("without premium files (the public build)", () => {
  beforeEach(() => {
    setFilesForTests({});
    loadPlan();
    seed("free");
  });

  test("the plan is Free and cannot be changed", () => {
    expect(currentPlan()).toBe("free");
    setPlan("business");
    expect(currentPlan()).toBe("free");
  });

  test("every premium package shows its plan card", async () => {
    const overview = await data<{ packages: { id: string; status: string; ui?: unknown }[] }>("/premium");
    expect(overview.packages.map((p) => p.status)).toEqual(Array(5).fill("not_included"));
    expect(overview.packages.some((p) => p.ui)).toBe(false);
  });

  test("the plan is the Free plan of a panel not linked yet", async () => {
    const plan = await data<{
      plan: string;
      linked: boolean;
      entitlements: { max_production_branches: number; teams: boolean };
    }>("/plan");
    expect(plan).toMatchObject({
      plan: "free",
      linked: false,
      entitlements: { max_production_branches: 1, teams: false },
    });
  });
});

describe("with premium files", () => {
  beforeEach(() => {
    session.role = "admin";
    setFilesForTests(FILES);
    seed("business");
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  test("the demo starts on Business when nothing was chosen", () => {
    expect(loadPlan()).toBe("business");
  });

  test("Business runs every package, with its files and their hashes", async () => {
    const overview = await data<{ packages: { id: string; status: string; ui?: { files: Record<string, string> } }[] }>(
      "/premium",
    );
    expect(overview.packages.every((p) => p.status === "active")).toBe(true);
    expect(overview.packages.find((p) => p.id === "servers")!.ui!.files["ui/servers.js"]).toHaveLength(64);
    const plan = await data<{
      plan: string;
      linked: boolean;
      usage: { servers: number };
      entitlements: { features: Record<string, boolean> };
    }>("/plan");
    expect(plan).toMatchObject({ plan: "business", linked: true, usage: { servers: 3 } });
    expect(Object.values(plan.entitlements.features).every(Boolean)).toBe(true);
  });

  test("Pro runs teams, off-site backups and support, not domains or servers", async () => {
    setPlan("pro");
    const overview = await data<{ packages: { id: string; status: string }[] }>("/premium");
    expect(Object.fromEntries(overview.packages.map((p) => [p.id, p.status]))).toEqual({
      teams: "active",
      domains: "not_included",
      offsite: "active",
      support: "active",
      servers: "not_included",
    });
    expect((await call("GET", "/support-access")).status).toBe(200);
    expect((await call("POST", "/instances/acme-retail-main/domains", { domain: "new.example.com" })).status).toBe(402);
  });

  test("Free answers 402 with the plans that include each feature", async () => {
    setPlan("free");
    const servers = await call("GET", "/servers");
    expect(servers.status).toBe(402);
    expect(servers.body.detail).toContain("Several servers is part of the Business plans");
    const team = await call("POST", "/teams", { name: "Night shift" });
    expect(team.status).toBe(402);
    expect(team.body.detail).toContain("Pro and Business");
    expect((await call("GET", "/offsite")).status).toBe(402);
    expect((await call("POST", "/instances/zahra-clinic-main/move", { server: "main" })).status).toBe(402);
    // what the core keeps without the packages still answers
    expect((await call("GET", "/teams")).status).toBe(200);
    expect((await call("GET", "/instances/acme-retail-main/domains")).status).toBe(200);
  });

  test("the instances carry their server only while several servers run", async () => {
    const zahra = await data<{ server?: { name: string; region: string } }>("/instances/zahra-clinic-main");
    expect(zahra.server).toMatchObject({ name: "Dubai", region: "me-central" });
    setPlan("pro");
    expect((await data<{ server?: unknown }>("/instances/zahra-clinic-main")).server).toBeUndefined();
    setPlan("business");
    expect((await data<{ server?: { name: string } }>("/instances/zahra-clinic-main")).server?.name).toBe("Dubai");
  });

  test("three servers in three regions, and where a new instance may go", async () => {
    const list = await call("GET", "/servers");
    const servers = list.body.data as { name: string; region: string; main: boolean; status: string }[];
    expect(servers.map((s) => [s.name, s.region, s.main])).toEqual([
      ["Frankfurt", "eu-central", true],
      ["Dubai", "me-central", false],
      ["Virginia", "us-east", false],
    ]);
    expect(list.body.meta).toMatchObject({ limit: 5, counted: 3, refusal: null });
    const plan = await data<{ servers: { name: string }[] }>("/instance-plan?stage=development");
    expect(plan.servers.map((s) => s.name)).toEqual(["Frankfurt", "Dubai", "Virginia"]);
  });

  test("a new instance goes to the chosen server", async () => {
    vi.useFakeTimers();
    const made = await call("POST", "/instances", {
      version: "19.0",
      edition: "community",
      name: "dubai-test",
      server: "srv-dxb7k2m9qa",
    });
    expect(made.status).toBe(202);
    expect((await data<{ server: { name: string } }>("/instances/dubai-test")).server.name).toBe("Dubai");
  });

  test("moving an instance is a job that ends on the other server", async () => {
    vi.useFakeTimers();
    const refused = await call("POST", "/instances/acme-retail-main/move", { server: "srv-iad3h8w1ze" });
    expect(refused.status).toBe(400); // production asks for a confirmation
    const moved = await call("POST", "/instances/acme-retail-main/move", {
      server: "srv-iad3h8w1ze",
      confirm_production: true,
    });
    expect(moved.status).toBe(202);
    await vi.advanceTimersByTimeAsync(60_000);
    expect((await data<{ server: { name: string } }>("/instances/acme-retail-main")).server.name).toBe("Virginia");
  });

  test("an added server joins, and is approved with the fingerprint it printed", async () => {
    vi.useFakeTimers();
    const added = await call("POST", "/servers", { name: "Singapore", region: "ap-southeast" });
    expect(added.status).toBe(201);
    const server = added.body.data as { id: string; status: string; join_code: string; install_command: string };
    expect(server.status).toBe("waiting");
    expect(server.install_command).toContain(server.join_code);
    await vi.advanceTimersByTimeAsync(15_000);
    const joined = await data<{ status: string; fingerprint: string }>(`/servers/${server.id}`);
    expect(joined.status).toBe("joined");
    expect((await call("POST", `/servers/${server.id}/approve`, { fingerprint: "0000-0000-0000-0000" })).status).toBe(
      400,
    );
    const approved = await call("POST", `/servers/${server.id}/approve`, {
      fingerprint: joined.fingerprint.toLowerCase(),
    });
    expect(approved.body.data).toMatchObject({ status: "active", online: true });
  });

  test("support access: the seeded grant, a new one, withdrawing it and ending all", async () => {
    const overview = await data<{ grants: { state: string; ticket: string | null }[] }>("/support-access");
    expect(overview.grants.filter((g) => g.state === "active").map((g) => g.ticket)).toEqual(["SUP-2481"]);
    const granted = await call("POST", "/support-access/grants", {
      level: "view",
      hours: 1,
      ticket: "SUP-9",
      scope: { all: false, projects: ["nile-logistics"] },
    });
    expect(granted.status).toBe(201);
    const id = (granted.body.data as { id: string }).id;
    expect((await data<{ active_grants: number }>("/support-access/status")).active_grants).toBe(2);
    expect((await call("DELETE", `/support-access/grants/${id}`)).status).toBe(204);
    expect((await call("POST", "/support-access/end")).status).toBe(204);
    expect((await data<{ active_grants: number }>("/support-access/status")).active_grants).toBe(0);
  });

  test("the choice of plan is kept in the browser", () => {
    const store = new Map<string, string>();
    vi.stubGlobal("localStorage", {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => store.set(k, v),
    });
    setPlan("pro");
    expect(store.get("sudoo.demo.plan")).toBe("pro");
    expect(loadPlan()).toBe("pro");
    vi.unstubAllGlobals();
  });
});
