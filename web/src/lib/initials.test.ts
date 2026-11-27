import { describe, expect, it } from "vitest";
import { initials } from "./initials";

describe("initials", () => {
  it("takes the first letter of each name", () => {
    expect(initials("Ada", "Lovelace")).toBe("AL");
    expect(initials("ada", null)).toBe("a");
  });
  it("keeps a character outside the Basic Multilingual Plane whole", () => {
    expect(initials("مستشار اختبار", "😀")).toBe("م😀");
    expect(initials("😀 Smile")).toBe("😀");
    expect(initials("x", "😀").includes("�")).toBe(false);
  });
  it("gives an empty string for missing names", () => {
    expect(initials("", undefined)).toBe("");
    expect(initials("  ", "  ")).toBe("");
  });
});
