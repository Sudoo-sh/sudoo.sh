// The demo's repository guard (repo-guard.ts) through its /api/v1: the seeded pull request and rulesets, the checks'
// pull request, the rules' preview and confirmation, and the Checks results. Run with `npm test`.

import { beforeAll, beforeEach, describe, expect, test, vi } from "vitest";
import type { ChecksPreview, Guard, RulesPlan } from "@/api/guard";
import { answer } from "./api";
import { seedTeams } from "./access";
import { seedProjects } from "./projects";
import { resetGuards } from "./repo-guard";
import { seedAll, session } from "./state";

vi.hoisted(() => {
  const g = globalThis as Record<string, unknown>;
  g.document ??= { cookie: "", documentElement: { lang: "en", dir: "ltr", dataset: {} } };
});

async function call(method: string, path: string, body?: unknown, query = "") {
  const found = await answer(method, path, new URLSearchParams(query), body);
  const json = (found.body ?? {}) as { data?: unknown; detail?: string };
  return { status: found.status, data: json.data, detail: json.detail };
}

beforeAll(() => {
  session.signedIn = true;
});

describe("repository guard", () => {
  beforeEach(() => {
    seedAll();
    seedTeams();
    seedProjects();
    resetGuards();
  });

  test("Acme Retail shows a sample pull request and applied rulesets", async () => {
    const { status, data } = await call("GET", "/projects/acme-retail/guard");
    expect(status).toBe(200);
    const guard = data as Guard;
    expect(guard.checks.enabled).toBe(true);
    expect(guard.pull?.url).toMatch(/\/pull\/42$/);
    expect(guard.applied?.method).toBe("rulesets");
    const plan = (await call("POST", "/projects/acme-retail/guard/rules/preview", { rules: {} })).data as RulesPlan;
    expect(plan.untouched).toEqual(["Team rules"]);
    expect(plan.targets.every((t) => t.action === "unchanged")).toBe(true);
  });

  test("the checks open a pull request, and the rules need the repository's name", async () => {
    const preview = (await call("POST", "/projects/nile-logistics/guard/checks/preview", { checks: { enabled: true } }))
      .data as ChecksPreview;
    expect(preview.files.map((f) => f.action)).toEqual(["add", "add", "add", "add"]);
    const opened = (
      await call("PUT", "/projects/nile-logistics/guard/checks", { checks: { enabled: true, strictness: "strict" } })
    ).data as ChecksPreview;
    expect(opened.result).toBe("opened");
    expect(
      (await call("PUT", "/projects/nile-logistics/guard/checks", { checks: { strictness: "nope" } })).status,
    ).toBe(400);

    const plan = (
      await call("POST", "/projects/nile-logistics/guard/rules/preview", { rules: { production: { approvals: 2 } } })
    ).data as RulesPlan;
    const production = plan.targets.find((t) => t.key === "production")!;
    expect(production.action).toBe("create");
    expect(production.changes.find((c) => c.field === "approvals")).toMatchObject({ before: "", after: "2" });
    const refused = await call("PUT", "/projects/nile-logistics/guard/rules", {
      rules: { production: { approvals: 2 } },
      confirm: "wrong",
    });
    expect(refused.status).toBe(400);
    const guard = (await call("GET", "/projects/nile-logistics/guard")).data as Guard;
    const applied = await call("PUT", "/projects/nile-logistics/guard/rules", {
      rules: { production: { approvals: 2 } },
      confirm: guard.repository,
    });
    expect((applied.data as RulesPlan).result).toBe("applied");
    const removed = await call("DELETE", "/projects/nile-logistics/guard/rules", { confirm: guard.repository });
    expect((removed.data as RulesPlan).result).toBe("removed");
  });

  test("CI results only when the checks are on", async () => {
    const sha = "a".repeat(40);
    expect((await call("GET", "/projects/nile-logistics/guard/results", undefined, `sha=${sha}`)).data).toEqual({});
    const found = (await call("GET", "/projects/acme-retail/guard/results", undefined, `sha=${sha}`)).data as Record<
      string,
      { state: string }
    >;
    expect(["success", "failure", "pending"]).toContain(found[sha]!.state);
    expect((await call("GET", "/projects/acme-retail/guard/results", undefined, "sha=nope")).status).toBe(400);
  });
});
