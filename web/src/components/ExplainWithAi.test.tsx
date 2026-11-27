import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

const state = { data: undefined as unknown, error: null as unknown, can: [] as string[] };
vi.mock("@tanstack/react-query", () => ({ useQuery: () => ({ data: state.data, error: state.error }) }));
vi.mock("@/i18n", async () => {
  const words = (await import("@/i18n/explain")).default;
  return { useT: () => words.en };
});
vi.mock("./Session", () => ({ useCan: () => (p: string) => state.can.includes(p) }));
vi.mock("@/api/queries", () => ({ useCall: () => ({ mutate: () => {}, isPending: false, reset: () => {} }) }));
vi.mock("@/api/client", () => ({ api: {}, errorText: (e: unknown) => String(e) }));
vi.mock("./app/overlay", () => ({ Modal: () => null }));

import words from "@/i18n/explain";
import { ExplainSettingsFields, ExplainWithAi } from "./ExplainWithAi";

const draw = () => renderToStaticMarkup(<ExplainWithAi project="p" build="b1" lang="en" />);

beforeEach(() => {
  state.data = undefined;
  state.error = null;
  state.can = [];
});

describe("Explain with AI", () => {
  it("offers nothing while off, not set up, or unknown to the server", () => {
    expect(draw()).toBe("");
    state.data = { available: false, reason: "off", enabled_for_project: false };
    expect(draw()).toBe("");
    state.data = { available: false, reason: "project_off", enabled_for_project: false };
    expect(draw()).toBe("");
    state.data = { available: true, reason: null, enabled_for_project: true };
    state.error = new Error("404");
    expect(draw()).toBe("");
  });
  it("offers the project's switch only to who manages the project", () => {
    state.data = { available: false, reason: "project_off", enabled_for_project: false };
    state.can = ["projects.manage"];
    expect(draw()).toContain(words.en.turnOn);
  });
  it("shows the button when available, and nothing is sent by showing it", () => {
    state.data = { available: true, reason: null, enabled_for_project: true };
    expect(draw()).toContain(words.en.button);
  });
  it("never renders a saved key in the settings", () => {
    state.data = {
      enabled: true,
      endpoint: "https://a.example/v1",
      model: "m",
      key_set: true,
      projects: [],
      destination_problem: null,
      limits: { per_user_hour: 10, per_project_hour: 30 },
    };
    const html = renderToStaticMarkup(<ExplainSettingsFields />);
    expect(html).toContain(words.en.settings.keySet);
    expect(html).toContain('type="password"');
  });
  it("says in both languages that scrubbing is no guarantee and the answer can be wrong", () => {
    expect(words.en.privacy).toMatch(/cannot guarantee/);
    expect(words.en.generated("m")).toMatch(/can be wrong/);
    expect(words.ar.privacy).toContain("لا يضمن");
    expect(words.en.settings.limits(1, 2)).toMatch(/not a cap on cost/);
  });
});
