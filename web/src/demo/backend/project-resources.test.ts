// The demo's resources of projects and branches (project-resources.ts) through its /api/v1, as the Resources section
// of a project's Settings tab and a branch's Branch settings tab read them. Run with `npm test`.

import { beforeAll, beforeEach, describe, expect, test, vi } from "vitest";
import type { BranchResources, ProjectResources, ResourcePlan } from "@/api/types";
import { answer } from "./api";
import { seedTeams } from "./access";
import { seedProjects } from "./projects";
import { seedAll, session } from "./state";

vi.hoisted(() => {
  const g = globalThis as Record<string, unknown>;
  g.document ??= { cookie: "", documentElement: { lang: "en", dir: "ltr", dataset: {} } };
});

async function call(method: string, path: string, body?: unknown) {
  const found = await answer(method, path, new URLSearchParams(""), body);
  return {
    status: found.status,
    data: ((found.body ?? {}) as { data?: unknown; detail?: string }).data,
    detail: (found.body as { detail?: string } | undefined)?.detail,
  };
}

beforeAll(() => {
  session.signedIn = true;
});

describe("project resources", () => {
  beforeEach(() => {
    seedAll();
    seedTeams();
    seedProjects();
  });

  test("each stage shows its values, the computed limits and the storage of the instances", async () => {
    const { status, data } = await call("GET", "/projects/acme-retail/resources");
    expect(status).toBe(200);
    const found = data as ProjectResources;
    expect(found.stages.production.own.odoo_workers).toBe(2);
    expect(found.stages.production.computed).toContain("odoo_memory_limit");
    expect(found.stages.development.applies.odoo_workers).toBe(0);
    expect(found.maxima.odoo_workers).toBe(8);
    expect(found.instances.length).toBeGreaterThan(0);
  });

  test("a preview changes nothing, values above the maxima are refused, production asks to confirm", async () => {
    const preview = await call("PUT", "/projects/acme-retail/resources", {
      stage: "production",
      limits: { odoo_workers: 3 },
      preview: true,
    });
    expect(preview.status).toBe(200);
    expect((preview.data as { plan: ResourcePlan }).plan.after_bytes).toBeGreaterThan(0);
    expect(
      ((await call("GET", "/projects/acme-retail/resources")).data as ProjectResources).stages.production.own
        .odoo_workers,
    ).toBe(2);
    expect(
      (await call("PUT", "/projects/acme-retail/resources", { stage: "production", limits: { odoo_workers: 20 } }))
        .status,
    ).toBe(400);
    expect(
      (await call("PUT", "/projects/acme-retail/resources", { stage: "production", limits: { odoo_workers: 3 } }))
        .status,
    ).toBe(400);
    const done = await call("PUT", "/projects/acme-retail/resources", {
      stage: "production",
      limits: { odoo_workers: 3 },
      confirm_production: true,
    });
    expect(done.status).toBe(200);
    expect((done.data as ProjectResources).stages.production.own.odoo_workers).toBe(3);
  });

  test("a branch overrides the project and follows it again", async () => {
    const branches = (
      (await call("GET", "/projects/acme-retail")).data as { branches_list: { name: string; stage: string }[] }
    ).branches_list;
    const branch = branches.find((b) => b.stage === "development")!.name;
    const path = `/projects/acme-retail/branches/${branch}/resources`;
    const changed = await call("PUT", path, { limits: { odoo_workers: 1 } });
    expect(changed.status).toBe(200);
    expect((changed.data as BranchResources).sources.odoo_workers).toBe("own");
    const back = await call("PUT", path, { limits: {} });
    expect((back.data as BranchResources).own).toEqual({});
  });
});
