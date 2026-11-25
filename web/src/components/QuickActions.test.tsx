import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Instance } from "@/api/types";

vi.mock("@/i18n", async () => {
  const dashboard = (await import("@/i18n/dashboard")).default;
  const common = (await import("@/i18n/common")).default;
  const expiry = (await import("@/i18n/expiry")).default;
  const pick = (d: unknown) => (d === dashboard ? dashboard.en : d === common ? common.en : expiry.en);
  return { useT: pick, words: pick, intlTag: () => "en", getLocale: () => "en" };
});

const can = {
  allowed: undefined as string[] | undefined,
  role: new Set(["instances.operate", "instances.delete", "instances.logs"]),
};
vi.mock("./Session", () => ({ useCan: () => (p: string) => can.role.has(p) }));
vi.mock("@/api/queries", () => ({
  keys: { instances: ["instances"], instance: (n: string) => ["instance", n] },
  useSettings: () => ({ data: [] }),
  useCall: () => ({ mutate: () => {}, isPending: false }),
  useInstancePermissions: () => ({ data: can.allowed }),
}));
vi.mock("@/pages/Instance", () => ({ DeleteDialog: () => null }));

import { QuickActions } from "./QuickActions";

const instance = (over: Partial<Instance> = {}) =>
  ({
    name: "shop",
    stage: "development",
    status: "online",
    lock: null,
    client_access: null,
    update_failure: null,
    expires_at: null,
    expired_at: null,
    ...over,
  }) as unknown as Instance;

const html = (i: Instance) =>
  renderToStaticMarkup(
    <MemoryRouter>
      <QuickActions instance={i} />
    </MemoryRouter>,
  );

describe("QuickActions", () => {
  beforeEach(() => {
    can.allowed = undefined;
    can.role = new Set(["instances.operate", "instances.delete", "instances.logs"]);
  });

  it("offers Delete to a role that may delete, and nothing else on a healthy instance with no end date", () => {
    const out = html(instance());
    expect(out).toContain("Delete shop");
    expect(out).not.toContain("Latest errors");
    expect(out).not.toContain("Extend");
  });

  it("offers the latest errors only when the instance failed", () => {
    expect(html(instance({ status: "unreachable" }))).toContain('href="/i/shop/logs"');
  });

  it("offers Extend only to an instance that ends and has not been stopped", () => {
    expect(html(instance({ expires_at: "2026-12-01T00:00:00Z" }))).toContain("Extend");
    expect(html(instance({ expires_at: "2026-12-01T00:00:00Z", expired_at: "2026-12-01T00:00:00Z" }))).not.toContain(
      "Extend",
    );
  });

  it("leaves out what the role or the branch lock does not allow", () => {
    can.role = new Set(["instances.logs"]);
    expect(html(instance({ status: "unreachable", expires_at: "2026-12-01T00:00:00Z" }))).not.toContain("Delete");
    can.role = new Set(["instances.operate", "instances.delete"]);
    can.allowed = ["instances.operate"];
    const out = html(instance({ expires_at: "2026-12-01T00:00:00Z" }));
    expect(out).toContain("Extend");
    expect(out).not.toContain("Delete shop");
  });
});
