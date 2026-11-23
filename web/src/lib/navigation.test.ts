import { describe, expect, it } from "vitest";
import { branchOf, navigationKind } from "./navigation";

describe("branchOf", () => {
  it("reads the branch a project's path names, whatever '/' it holds", () => {
    expect(branchOf("/projects/acme/branches/main")).toBe("main");
    expect(branchOf("/projects/acme/branches/feature%2Fcart/logs")).toBe("feature/cart");
    expect(branchOf("/projects/acme")).toBeNull();
    expect(branchOf("/projects/acme/builds")).toBeNull();
    expect(branchOf("/i/acme-main")).toBeNull();
  });
});

describe("navigationKind", () => {
  it("tells another page, another branch and another tab apart", () => {
    expect(navigationKind("/projects/acme/branches/main", "/projects/acme/branches/dev")).toBe("branch");
    expect(navigationKind("/projects/acme/branches/main", "/projects/acme/branches/main/logs")).toBe("tab");
    expect(navigationKind("/projects/acme/builds", "/projects/acme/settings")).toBe("tab");
    expect(navigationKind("/projects/acme/branches/main", "/projects/other/branches/main")).toBe("page");
    expect(navigationKind("/i/a", "/i/a/logs")).toBe("tab");
  });
});
