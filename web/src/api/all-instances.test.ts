import { afterEach, describe, expect, it, vi } from "vitest";
import { allInstances, INSTANCES_PER_PAGE } from "./queries";

// the language is chosen when the modules load (i18n/locale.ts reads the cookie): no browser here
vi.hoisted(() => {
  (globalThis as { document?: unknown }).document ??= { cookie: "", documentElement: { lang: "", dir: "" } };
});

// A server with `total` instances answering GET /api/v1/instances?limit=&offset= as the backend does.
function serve(total: number) {
  const names = Array.from({ length: total }, (_, i) => `odoo-${String(i).padStart(4, "0")}`);
  const asked: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      asked.push(url);
      const query = new URL(url, "http://panel").searchParams;
      const limit = Number(query.get("limit") ?? 100);
      const offset = Number(query.get("offset") ?? 0);
      const data = names.slice(offset, offset + limit).map((name) => ({ name }));
      return new Response(JSON.stringify({ data, meta: { total, count: data.length, limit, offset } }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
  return asked;
}

afterEach(() => vi.unstubAllGlobals());

describe("allInstances", () => {
  it("asks once while the server has at most one page", async () => {
    const asked = serve(INSTANCES_PER_PAGE);
    expect(await allInstances()).toHaveLength(INSTANCES_PER_PAGE);
    expect(asked).toEqual([`/api/v1/instances?limit=${INSTANCES_PER_PAGE}`]);
  });

  it("reads every page past 500 instances, each instance once and in order", async () => {
    const asked = serve(1234);
    const found = await allInstances();
    expect(found).toHaveLength(1234);
    expect(new Set(found.map((i) => i.name)).size).toBe(1234);
    expect(found[0]?.name).toBe("odoo-0000");
    expect(found[1233]?.name).toBe("odoo-1233");
    expect(asked).toEqual([
      "/api/v1/instances?limit=500",
      "/api/v1/instances?limit=500&offset=500",
      "/api/v1/instances?limit=500&offset=1000",
    ]);
  });

  it("is empty for a server without instances", async () => {
    serve(0);
    expect(await allInstances()).toEqual([]);
  });
});
