import { describe, expect, it } from "vitest";
import { frameFor, redirectTarget, signInLookFor } from "./frame";

describe("the client portal's frame", () => {
  it("gives clients the portal and everyone else the panel", () => {
    expect(frameFor("client")).toBe("portal");
    for (const role of ["super_admin", "admin", "maintainer", "developer", "viewer"])
      expect(frameFor(role)).toBe("panel");
  });

  it("shows the portal's sign-in page on the portal's own address only", () => {
    expect(signInLookFor({ portal: true })).toBe("portal");
    expect(signInLookFor({ portal: false })).toBe("panel");
    expect(signInLookFor(undefined)).toBe("panel");
  });

  it("sends a sign-in at the wrong address to the other address's sign-in page, and nowhere else", () => {
    expect(redirectTarget({ address: "panel", url: "https://panel.example.com" })).toBe(
      "https://panel.example.com/login",
    );
    expect(redirectTarget({ address: "portal", url: "https://portal.example.com" })).toBe(
      "https://portal.example.com/login",
    );
    expect(redirectTarget({ address: "panel", url: "" })).toBeNull();
    expect(redirectTarget({ address: "panel", url: "javascript:alert(1)" })).toBeNull();
    expect(redirectTarget({ address: "panel", url: "https://evil.example.com/path" })).toBeNull();
    expect(redirectTarget(null)).toBeNull();
  });
});
