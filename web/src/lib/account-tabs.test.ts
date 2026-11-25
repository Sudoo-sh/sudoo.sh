import { describe, expect, it } from "vitest";
import { accountTab, firstTab, tabCounts } from "./account-tabs";

describe("account tabs", () => {
  it("puts a disabled account under Disabled and every other under Active", () => {
    expect(accountTab({ disabled: true })).toBe("disabled");
    expect(accountTab({ disabled: false })).toBe("active");
    expect(accountTab({})).toBe("active");
  });

  it("counts each tab, and only pending requests count as waiting", () => {
    const counts = tabCounts(
      [{}, { disabled: true }, { disabled: false }],
      [1, 2],
      [{ status: "pending" }, { status: "rejected" }, { status: "approved" }],
    );
    expect(counts).toEqual({ pending: 1, active: 2, invited: 2, disabled: 1 });
  });

  it("opens Pending approval only when someone waits", () => {
    expect(firstTab({ pending: 2, active: 5, invited: 0, disabled: 0 })).toBe("pending");
    expect(firstTab({ pending: 0, active: 5, invited: 1, disabled: 0 })).toBe("active");
  });
});
