// Disabling and enabling an account in the demo both ask for a reason, as the real API does. Run with `npm test`.

import { beforeAll, beforeEach, describe, expect, test, vi } from "vitest";
import { answer } from "./api";
import { seedAll, session } from "./state";

vi.hoisted(() => {
  const g = globalThis as Record<string, unknown>;
  g.document ??= { cookie: "", documentElement: { lang: "en", dir: "ltr", dataset: {} } };
});

async function call(method: string, path: string, body?: unknown) {
  const found = await answer(method, path, new URLSearchParams(), body);
  const json = (found.body ?? {}) as { data?: Record<string, unknown>; meta?: Record<string, unknown> };
  return { status: found.status, data: json.data, meta: json.meta };
}

beforeAll(() => {
  session.signedIn = true;
});

describe("disabled accounts need a reason", () => {
  beforeEach(() => {
    seedAll();
  });

  test("disable and enable are refused without a reason", async () => {
    const users = await call("GET", "/users");
    const target = (users.data as unknown as { name: string; role: string; disabled?: boolean }[]).find(
      (u) => !u.disabled && u.name !== "sara" && u.role !== "super_admin",
    );
    expect(target).toBeTruthy();
    expect((await call("POST", `/users/${target!.name}/disable`, {})).status).toBe(400);
    expect((await call("POST", `/users/${target!.name}/disable`, { reason: "   " })).status).toBe(400);
    const done = await call("POST", `/users/${target!.name}/disable`, { reason: "Left the team" });
    expect(done.status).toBe(200);
    expect(done.data?.disabled_reason).toBe("Left the team");
    expect((await call("POST", `/users/${target!.name}/enable`, {})).status).toBe(400);
    const back = await call("POST", `/users/${target!.name}/enable`, { reason: "Back from leave" });
    expect(back.status).toBe(200);
    expect(back.data?.disabled_reason).toBeNull();
  });

  test("the request list reports how many wait for the e-mail confirmation", async () => {
    const list = await call("GET", "/access-requests");
    expect(typeof list.meta?.awaiting_confirmation).toBe("number");
  });
});
