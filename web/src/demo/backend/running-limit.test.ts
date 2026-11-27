import { afterEach, beforeAll, describe, expect, it } from "vitest";
import type { Capacity, Quota, User } from "@/api/types";

/** The demo's running-instance limits: off by default, shown as "n of m" when set, the number on an account is
 *  checked, and production is counted in the panel total but named apart. */

let answer: typeof import("./api").answer;
let session: typeof import("./state").session;
let seedAll: typeof import("./state").seedAll;

beforeAll(async () => {
  (globalThis as { document?: unknown }).document ??= { cookie: "", documentElement: { lang: "", dir: "" } };
  ({ answer } = await import("./api"));
  ({ session, seedAll } = await import("./state"));
  seedAll();
  session.role = "super_admin";
});

const call = (method: string, path: string, body?: Record<string, unknown>) =>
  answer(method, path, new URLSearchParams(), body);
const data = async <T>(method: string, path: string, body?: Record<string, unknown>) =>
  ((await call(method, path, body)).body as { data: T }).data;

afterEach(async () => {
  await call("PATCH", "/settings", { max_running_instances: 0, max_running_non_production_per_user: 0 });
});

describe("demo running limits", () => {
  it("shows no limit while both settings are 0", async () => {
    const quota = await data<Quota>("GET", "/instance-quota");
    expect(quota.max_running).toBeNull();
    expect(quota.max_running_own).toBeNull();
  });

  it("shows the limits once set, with production counted apart", async () => {
    await call("PATCH", "/settings", { max_running_instances: 8, max_running_non_production_per_user: 3 });
    const quota = await data<Quota>("GET", "/instance-quota");
    expect([quota.max_running, quota.max_running_own]).toEqual([8, 3]);
    expect(quota.running).toBeGreaterThanOrEqual(quota.running_production ?? 0);
    expect(quota.running_production).toBeGreaterThan(0);
    const metrics = await data<{ capacity: Capacity }>("GET", "/metrics");
    expect(metrics.capacity.hard_limit).toBe(8);
    expect(metrics.capacity.running_production).toBe(quota.running_production);
  });

  it("keeps the number on an account, 0 meaning no limit, null the setting, and refuses bad ones", async () => {
    await call("PATCH", "/settings", { max_running_non_production_per_user: 2 });
    const users = await data<User[]>("GET", "/users");
    const name = users[0]!.name;
    expect(users[0]!.running_limit_default).toBe(2);
    expect((await data<User>("PATCH", `/users/${name}`, { running_limit: 0 })).running_limit).toBe(0);
    expect((await data<User>("PATCH", `/users/${name}`, { running_limit: null })).running_limit).toBeNull();
    for (const bad of [-1, 1001, "3", 1.5])
      expect((await call("PATCH", `/users/${name}`, { running_limit: bad })).status).toBe(400);
  });
});

describe("demo refusal of a creation", () => {
  it("answers 403 for a non-production instance at the person's limit, never for production", async () => {
    await call("PATCH", "/settings", { max_running_non_production_per_user: 1 });
    const refused = await call("POST", "/instances", { stage: "development", engine: "docker", version: "19.0" });
    expect(refused.status).toBe(403);
    expect(JSON.stringify(refused.body)).toContain("Pause one of yours first");
    const production = await call("POST", "/instances", { stage: "production", engine: "docker", version: "19.0" });
    expect(production.status).not.toBe(403);
  });
});
