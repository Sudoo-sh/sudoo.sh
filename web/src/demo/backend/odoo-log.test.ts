// The demo's Logs tab (odoo-log.ts) through its /api/v1: the logs listed, pages backwards and around a line, the
// filters, and the refusal of patterns that could take very long. Run with `npm test`.

import { beforeAll, beforeEach, describe, expect, test, vi } from "vitest";
import { answer } from "./api";
import { seedTeams } from "./access";
import { seedProjects } from "./projects";
import { seedAll, session } from "./state";

vi.hoisted(() => {
  const g = globalThis as Record<string, unknown>;
  g.document ??= { cookie: "", documentElement: { lang: "en", dir: "ltr", dataset: {} } };
});

type Entry = { id: string; level: string | null; logger: string | null; traceback: boolean; text: string };

async function call(path: string, query = "") {
  const found = await answer("GET", path, new URLSearchParams(query), undefined);
  const json = (found.body ?? {}) as { data?: unknown; meta?: Record<string, unknown>; detail?: string };
  return { status: found.status, data: json.data as Entry[], meta: json.meta, detail: json.detail };
}

beforeAll(() => {
  session.signedIn = true;
});

describe("the demo's logs", () => {
  beforeEach(() => {
    seedAll();
    seedTeams();
    seedProjects();
  });

  test("lists Odoo's own logs only", async () => {
    const files = (await call("/instances/acme-retail-main/logs/files")).data as unknown as { name: string }[];
    expect(files.map((f) => f.name)).toEqual(["odoo.log", "install.log", "update.log"]);
  });

  test("pages backwards and around a line, with the ids in order", async () => {
    const last = await call("/instances/acme-retail-main/logs/entries", "limit=50");
    expect(last.status).toBe(200);
    expect(last.data).toHaveLength(50);
    expect(last.meta?.has_before).toBe(true);
    const ids = last.data.map((e) => e.id);
    expect([...ids].sort()).toEqual(ids);
    const earlier = await call("/instances/acme-retail-main/logs/entries", `limit=50&before=${ids[0]}`);
    expect(earlier.data[earlier.data.length - 1]!.id < ids[0]!).toBe(true);
    const around = await call("/instances/acme-retail-main/logs/entries", `limit=10&around=${ids[20]}`);
    expect(around.data.map((e) => e.id)).toContain(ids[20]);
  });

  test("filters by level, with tracebacks kept with their line", async () => {
    const errors = await call("/instances/acme-retail-main/logs/entries", "limit=200&level=ERROR,CRITICAL");
    expect(errors.data.length).toBeGreaterThan(0);
    expect(errors.data.every((e) => e.level === "ERROR" || e.level === "CRITICAL")).toBe(true);
    const tracebacks = await call("/instances/acme-retail-main/logs/entries", "limit=200&tracebacks=1");
    expect(tracebacks.data.every((e) => e.traceback && e.text.includes("Traceback (most recent call last):"))).toBe(
      true,
    );
    const update = await call("/instances/acme-retail-main/logs/entries", "log=update.log&level=ERROR");
    expect(update.data[0]!.text).toContain("ParseError");
  });

  test("refuses patterns that could take very long", async () => {
    const refused = await call("/instances/acme-retail-main/logs/entries", `q=${encodeURIComponent("(a+)+$")}&regex=1`);
    expect(refused.status).toBe(400);
    expect((await call("/instances/acme-retail-main/logs/entries", "log=secrets.txt")).status).toBe(400);
  });
});
