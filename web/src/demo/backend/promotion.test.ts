// The demo's promotion tools (promotion.ts) through its /api/v1: the pre-promotion checklist, comparing branches, the
// scheduled staging refresh and pull-request previews. Run with `npm test`.

import { beforeAll, beforeEach, describe, expect, test, vi } from "vitest";
import type { BranchCompare, Preview, PromotionCheck, StagingRefresh } from "@/api/types";
import { answer } from "./api";
import { seedTeams } from "./access";
import { seedProjects } from "./projects";
import { seedAll, session } from "./state";

vi.hoisted(() => {
  const g = globalThis as Record<string, unknown>;
  g.document ??= { cookie: "", documentElement: { lang: "en", dir: "ltr", dataset: {} } };
});

async function call(method: string, path: string, body?: unknown, query = "") {
  const found = await answer(method, path, new URLSearchParams(query), body);
  const json = (found.body ?? {}) as { data?: unknown; meta?: Record<string, unknown>; detail?: string };
  return { status: found.status, data: json.data, meta: json.meta, detail: json.detail };
}

beforeAll(() => {
  session.signedIn = true;
});

describe("promotion", () => {
  beforeEach(() => {
    seedAll();
    seedTeams();
    seedProjects();
  });

  test("the checklist of a staging branch compares it with production", async () => {
    const { status, data } = await call("GET", "/projects/acme-retail/branches/staging/promotion-check");
    expect(status).toBe(200);
    const check = data as PromotionCheck;
    expect(check.production.branch).toBe("main");
    expect(check.items.map((i) => i.key)).toEqual(["build", "backup", "modules", "migrations", "odoo", "lock", "capacity"]);
    expect(check.items.find((i) => i.key === "migrations")?.status).toBe("warning");
    expect(check.blocking).toBe(false);
  });

  test("comparing two branches lists commits, modules and migrations", async () => {
    const { status, data } = await call("GET", "/projects/acme-retail/compare", undefined, "base=main&head=staging");
    expect(status).toBe(200);
    const found = data as BranchCompare;
    expect(found.ahead).toBeGreaterThan(0);
    expect(found.modules.changed.some((m) => m.bump === "up")).toBe(true);
    expect(found.github_url).toContain("/compare/main...staging");
    expect((await call("GET", "/projects/acme-retail/compare", undefined, "base=main&head=main")).status).toBe(400);
  });

  test("the staging refresh is scheduled and run", async () => {
    const saved = await call("PUT", "/projects/acme-retail/staging-refresh", { frequency: "daily", time: "04:30", weekday: 0, branches: [] });
    expect(saved.status).toBe(200);
    expect((saved.data as StagingRefresh).next_run).not.toBeNull();
    expect((await call("PUT", "/projects/acme-retail/staging-refresh", { frequency: "daily", time: "25:00" })).status).toBe(400);
    const read = await call("GET", "/projects/acme-retail/staging-refresh");
    expect((read.data as StagingRefresh).time).toBe("04:30");
    expect((read.meta?.branches as { name: string }[]).map((b) => b.name)).toContain("staging");
    const run = await call("POST", "/projects/acme-retail/staging-refresh/run");
    expect((run.data as { started: string[] }).started).toContain("acme-retail-staging");
  });

  test("pull-request previews are listed with the project's options", async () => {
    const { data, meta } = await call("GET", "/projects/acme-retail/previews");
    expect((data as Preview[]).length).toBe(2);
    expect(meta?.enabled).toBe(true);
    expect(meta?.limit).toBe(3);
  });
});
