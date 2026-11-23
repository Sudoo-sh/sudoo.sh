// The demo's module picker (modules.ts) through its /api/v1: a branch's modules, the Odoo apps of an edition, and
// "modules" when creating an instance. Run with `npm test`.

import { beforeAll, beforeEach, describe, expect, test, vi } from "vitest";
import type { Job, OdooApp, RepositoryModules } from "@/api/types";
import { answer } from "./api";
import { seedTeams } from "./access";
import { seedProjects } from "./projects";
import { repoUrl, seedAll, session } from "./state";

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

describe("module picker", () => {
  beforeEach(() => {
    seedAll();
    seedTeams();
    seedProjects();
  });

  test("a branch's modules, its submodules' too, with its applications selected first", async () => {
    const url = encodeURIComponent(repoUrl("acme-retail"));
    const { status, data } = await call("GET", "/repositories/modules", undefined, `url=${url}&branch=main`);
    expect(status).toBe(200);
    const found = data as RepositoryModules;
    expect(found.modules.some((m) => m.submodule === "oca/pos")).toBe(true);
    expect(found.default.modules).toEqual(["acme_pos_refunds"]);
    expect((await call("GET", "/repositories/modules", undefined, `url=${url}&branch=nope`)).status).toBe(404);
  });

  test("Enterprise apps only for Enterprise", async () => {
    const community = (await call("GET", "/odoo-apps", undefined, "version=18.0&edition=community")).data as OdooApp[];
    const enterprise = (await call("GET", "/odoo-apps", undefined, "version=18.0&edition=enterprise")).data as OdooApp[];
    expect(community.some((a) => a.name === "helpdesk")).toBe(false);
    expect(enterprise.some((a) => a.name === "helpdesk")).toBe(true);
  });

  test("creating with modules installs exactly those; wrong names are refused", async () => {
    vi.useFakeTimers();
    const body = { version: "18.0", edition: "community", name: "picked-demo", modules: ["crm", "sale_management"] };
    const { status, data } = await call("POST", "/instances", body);
    expect(status).toBe(202);
    await vi.advanceTimersByTimeAsync(120_000);
    const job = (await call("GET", `/jobs/${(data as Job).id}`)).data as Job;
    vi.useRealTimers();
    expect(job.steps.map((s) => s.title)).toContain("Installing the chosen modules (2)");
    expect((await call("POST", "/instances", { ...body, name: "picked-two", modules: ["-u all"] })).status).toBe(400);
  });
});
