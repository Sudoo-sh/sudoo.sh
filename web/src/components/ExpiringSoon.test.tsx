import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Instance } from "@/api/types";

// the words read the chosen language from the page when they load: no page here
vi.mock("@/i18n", async () => {
  const dict = (await import("@/i18n/expiry")).default;
  const common = (await import("@/i18n/common")).default;
  return {
    useT: (d: unknown) => (d === common ? common.en : dict.en),
    words: (d: unknown) => (d === common ? common.en : dict.en),
  };
});

vi.mock("@/lib/format", () => ({ ago: () => "in 5 hours", fullDateTime: () => "" }));

const permissions = { operate: true, unlimited: false, allowed: undefined as string[] | undefined };
vi.mock("./Session", () => ({
  useCan: () => (p: string) => (p === "instances.extend_unlimited" ? permissions.unlimited : permissions.operate),
}));
vi.mock("@/api/queries", () => ({
  keys: { instances: ["instances"], instance: (n: string) => ["instance", n] },
  useSettings: () => ({
    data: [
      { key: "expiry_card_days", value: 7 },
      { key: "expiry_card_rows", value: 5 },
      { key: "expiry_extend_presets", value: "1,7,30" },
      { key: "expiry_extend_max_days", value: 30 },
    ],
  }),
  useCall: () => ({ mutate: () => {}, isPending: false }),
  useInstancePermissions: () => ({ data: permissions.allowed }),
}));

import { ExpiringSoon } from "./ExpiringSoon";

const soon = (name: string, hours: number) =>
  ({
    name,
    stage: "development",
    project: { id: "p", name: "Acme" },
    git: { branch: "feat" },
    settings: { delete_after_days: 30 },
    expires_at: new Date(Date.now() + hours * 3_600_000).toISOString(),
  }) as unknown as Instance;

const html = (instances: Instance[]) =>
  renderToStaticMarkup(
    <MemoryRouter>
      <ExpiringSoon instances={instances} />
    </MemoryRouter>,
  );

describe("ExpiringSoon", () => {
  beforeEach(() => Object.assign(permissions, { operate: true, unlimited: false, allowed: undefined }));

  it("lists the nearest first, each with an Extend button", () => {
    const out = html([soon("later", 72), soon("sooner", 5)]);
    expect(out.indexOf("sooner")).toBeLessThan(out.indexOf("later"));
    expect(out.match(/Extend [a-z]+/g)).toEqual(["Extend sooner", "Extend later"]);
  });

  it("draws nothing when nothing expires in the window", () => {
    expect(html([soon("far", 24 * 30)])).toBe("");
    expect(html([])).toBe("");
  });

  it("hides the button from people who may not change the instance, and from a locked branch", () => {
    permissions.operate = false;
    expect(html([soon("a", 5)])).not.toContain("Extend a");
    permissions.operate = true;
    permissions.allowed = ["instances.view"];
    expect(html([soon("a", 5)])).not.toContain("Extend a");
    permissions.allowed = ["instances.operate"];
    expect(html([soon("a", 5)])).toContain("Extend a");
  });
});
