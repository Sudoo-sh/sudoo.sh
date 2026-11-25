import { describe, expect, it, vi } from "vitest";

// the words read the chosen language from the page when they load: no page here
vi.mock("@/i18n", async () => {
  const common = (await import("@/i18n/common")).default;
  return { t: () => common.en, useT: () => common.en, words: () => common.en };
});

import { buildTime } from "./format";

describe("buildTime", () => {
  it("shows minutes and seconds below an hour", () => {
    expect(buildTime(291)).toBe("4m 51s");
    expect(buildTime(45)).toBe("45s");
    expect(buildTime(null)).toBe("");
  });
});
