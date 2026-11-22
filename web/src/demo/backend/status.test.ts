import { describe, expect, it } from "vitest";
import { dayTone } from "@/lib/uptime";
import { floorPercent } from "./status";

describe("status in the demo", () => {
  it("never rounds uptime up to 100 % while a minute was down", () => {
    expect(floorPercent(1439, 1440)).toBe(99.93);
    expect(floorPercent(999_999, 1_000_000)).toBe(99.999);
    expect(floorPercent(1440, 1440)).toBe(100);
    expect(floorPercent(0, 0)).toBeNull();
  });

  it("colours a day by its uptime", () => {
    expect(dayTone(100)).toBe("up");
    expect(dayTone(99.95)).toBe("up");
    expect(dayTone(99.5)).toBe("degraded");
    expect(dayTone(97)).toBe("down");
    expect(dayTone(null)).toBe("none");
  });
});
