import { afterEach, beforeAll, describe, expect, it } from "vitest";
import type { WaitingRequest } from "@/api/types";

/** The demo's capacity queue: off by default (the refusal is the 403 it was), a start over the limit waits when
 *  CAPACITY_QUEUE is on, a person cancels their own request and an administrator moves one to the front. */

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
  await call("PATCH", "/settings", { max_running_instances: 0, capacity_queue: false });
});

describe("demo capacity queue", () => {
  it("is empty while the queue is off", async () => {
    expect(await data<WaitingRequest[]>("GET", "/capacity-queue")).toEqual([]);
  });

  it("lists the line once on, lets an administrator move one up and anyone cancel", async () => {
    await call("PATCH", "/settings", { capacity_queue: true });
    const line = await data<WaitingRequest[]>("GET", "/capacity-queue");
    expect(line.map((item) => item.position)).toEqual(line.map((_, index) => index + 1));
    expect(line.length).toBeGreaterThan(1);
    const last = line[line.length - 1]!;
    const moved = await data<WaitingRequest>("POST", `/capacity-queue/${last.id}/front`);
    expect(moved.position).toBe(1);
    expect((await call("DELETE", `/capacity-queue/${last.id}`)).status).toBe(204);
    expect((await call("DELETE", `/capacity-queue/${last.id}`)).status).toBe(404);
  });

  it("answers a creation over the limit with a waiting request, not 403, when on", async () => {
    await call("PATCH", "/settings", { max_running_instances: 1 });
    expect(
      (await call("POST", "/instances", { version: "19.0", edition: "community", name: "later-one" })).status,
    ).toBe(403);
    await call("PATCH", "/settings", { capacity_queue: true });
    const waiting = await call("POST", "/instances", { version: "19.0", edition: "community", name: "later-two" });
    expect(waiting.status).toBe(202);
    expect((waiting.body as { data: WaitingRequest }).data.kind).toBe("create");
  });
});
