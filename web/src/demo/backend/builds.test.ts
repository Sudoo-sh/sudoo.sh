// The demo's build history (builds.ts) through its /api/v1, as the web app reads it: the Builds tab, a branch's
// timeline with its live build, an entry by id, and a stage change joining the timeline. Run with `npm test`.

import { beforeAll, beforeEach, describe, expect, test, vi } from "vitest";
import type { BranchBuildOptions, Build, BranchBuilds, HistoryEntry } from "@/api/types";
import { answer } from "./api";
import { seedTeams } from "./access";
import { seedProjects } from "./projects";
import { seedAll, session } from "./state";

vi.hoisted(() => {
  const g = globalThis as Record<string, unknown>;
  g.document ??= { cookie: "", documentElement: { lang: "en", dir: "ltr", dataset: {} } };
});

type Body = { data?: unknown; meta?: Record<string, unknown> };

async function call(method: string, path: string, body?: unknown) {
  const [route, search] = path.split("?");
  const found = await answer(method, route!, new URLSearchParams(search ?? ""), body);
  return { status: found.status, body: (found.body ?? {}) as Body };
}

beforeAll(() => {
  session.signedIn = true;
});

describe("build history", () => {
  beforeEach(() => {
    seedAll();
    seedTeams();
    seedProjects();
  });

  test("the Builds tab lists each branch with its last builds, production first", async () => {
    const { status, body } = await call("GET", "/projects/acme-retail/builds?per_branch=4");
    expect(status).toBe(200);
    const branches = body.data as BranchBuilds[];
    expect(branches[0]!.stage).toBe("production");
    expect(branches.every((b) => b.builds.length <= 4)).toBe(true);
    const main = branches.find((b) => b.branch === "main")!;
    expect(main.builds.length).toBeGreaterThan(0);
    expect(body.meta?.kept_per_branch).toBe(200);
  });

  test("a branch's timeline has one live build per instance and pages", async () => {
    const all = (await call("GET", "/projects/acme-retail/branches/staging/history?limit=200")).body;
    const entries = all.data as HistoryEntry[];
    const builds = entries.filter((e): e is Build => e.type === "build");
    expect(builds.filter((b) => b.status === "success" || b.status === "warning")).toHaveLength(1);
    expect(entries.some((e) => e.type === "stage")).toBe(true);
    const page = (await call("GET", "/projects/acme-retail/branches/staging/history?limit=2&offset=1")).body;
    expect((page.data as HistoryEntry[]).map((e) => e.id)).toEqual(entries.slice(1, 3).map((e) => e.id));
    expect(page.meta?.total).toBe(entries.length);
  });

  test("an entry is found by id, and an unknown one is 404", async () => {
    const entries = (await call("GET", "/projects/acme-retail/branches/main/history")).body.data as HistoryEntry[];
    const found = await call("GET", `/projects/acme-retail/builds/${entries[0]!.id}`);
    expect((found.body.data as HistoryEntry).id).toBe(entries[0]!.id);
    expect((await call("GET", "/projects/acme-retail/builds/nothing")).status).toBe(404);
  });

  test("a stage change joins the branch's timeline", async () => {
    await call("PUT", "/projects/acme-retail/branches/loyalty-points/stage", { stage: "staging" });
    const entries = (await call("GET", "/projects/acme-retail/branches/loyalty-points/history")).body
      .data as HistoryEntry[];
    const change = entries.find((e) => e.type === "stage");
    expect(change).toMatchObject({ from: "development", to: "staging" });
  });
});

describe("build options", () => {
  beforeEach(() => {
    seedAll();
    seedTeams();
    seedProjects();
  });

  test("a branch's options say where each value comes from, and a change is checked", async () => {
    const read = (await call("GET", "/projects/acme-retail/branches/pos-refunds/options")).body
      .data as BranchBuildOptions;
    expect(read.options.test_suite).toBe("selection");
    expect(read.sources.test_suite).toBe("project");
    expect(
      (await call("PUT", "/projects/acme-retail/branches/pos-refunds/options", { module_install: "list" })).status,
    ).toBe(400);
    const changed = (
      await call("PUT", "/projects/acme-retail/branches/pos-refunds/options", {
        module_install: "list",
        modules: "sale, stock",
      })
    ).body.data as BranchBuildOptions;
    expect(changed.options.modules).toBe("sale,stock");
    expect(changed.sources.module_install).toBe("branch");
    const production = (await call("GET", "/projects/acme-retail/branches/main/options")).body
      .data as BranchBuildOptions;
    expect(production.options.on_new_commit).toBe("update");
    expect(production.sources.test_suite).toBe("production");
  });

  test("tested builds carry their tests, and a branch that does nothing lists the commits it skipped", async () => {
    const dev = (await call("GET", "/projects/acme-retail/branches/pos-refunds/history?limit=200")).body
      .data as HistoryEntry[];
    const tested = dev.filter((e): e is Build => e.type === "build" && !!e.tests);
    expect(tested.length).toBeGreaterThan(0);
    expect(tested.some((b) => b.status === "failed" && b.tests!.failed === 1 && b.tests!.modules.length === 1)).toBe(
      true,
    );
    const plain = (await call("GET", "/projects/acme-retail/branches/loyalty-points/history")).body
      .data as HistoryEntry[];
    expect(plain.some((e) => e.type === "commit")).toBe(false);
    const all = (await call("GET", "/projects/acme-retail/branches/loyalty-points/history?commits=1")).body
      .data as HistoryEntry[];
    expect(all.filter((e) => e.type === "commit")).toHaveLength(2);
  });
});
