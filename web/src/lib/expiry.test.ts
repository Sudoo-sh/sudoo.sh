import { describe, expect, it } from "vitest";
import type { Instance } from "@/api/types";
import { extendChoices, extendedLifetime, expiringSoon, stayedStopped, urgency } from "./expiry";

const NOW = Date.parse("2026-11-25T10:00:00Z");
const make = (name: string, hours: number | null) =>
  ({ name, expires_at: hours == null ? null : new Date(NOW + hours * 3_600_000).toISOString() }) as Instance;

describe("expiringSoon", () => {
  const list = [make("c", 100), make("a", 5), make("none", null), make("far", 24 * 20), make("late", -3), make("b", 5)];
  it("lists the nearest first, an expired one before the rest, ties by name", () => {
    expect(expiringSoon(list, 7, 10, NOW).rows.map((r) => r.instance.name)).toEqual(["late", "a", "b", "c"]);
  });
  it("keeps to the window and the number of rows, and counts all of them", () => {
    const cut = expiringSoon(list, 7, 2, NOW);
    expect(cut.rows.map((r) => r.instance.name)).toEqual(["late", "a"]);
    expect(cut.total).toBe(4);
    expect(expiringSoon(list, 1, 10, NOW).total).toBe(3);
  });
  it("is empty without instances or expiries", () => {
    expect(expiringSoon(undefined, 7, 5, NOW).rows).toEqual([]);
    expect(expiringSoon([make("p", null)], 7, 5, NOW).total).toBe(0);
  });
});

describe("urgency", () => {
  it("tells passed, under a day and later", () => {
    expect(urgency(NOW - 1, NOW)).toBe("over");
    expect(urgency(NOW + 23 * 3_600_000, NOW)).toBe("soon");
    expect(urgency(NOW + 25 * 3_600_000, NOW)).toBe("near");
  });
});

describe("extendChoices", () => {
  it("offers the presets up to the limit", () => {
    expect(extendChoices("30, 1,7,7", 30, false)).toEqual([1, 7, 30]);
    expect(extendChoices("1,7,30", 14, false)).toEqual([1, 7]);
  });
  it("offers all of them with the right to extend without limits or without a limit", () => {
    expect(extendChoices("1,7,30", 14, true)).toEqual([1, 7, 30]);
    expect(extendChoices("1,7,30", 0, false)).toEqual([1, 7, 30]);
  });
  it("ignores what is not a whole number of days", () => {
    expect(extendChoices("x,0,-2,1.5,3", 0, false)).toEqual([3]);
  });
});

describe("extendedLifetime", () => {
  it("adds the days to the lifetime", () => expect(extendedLifetime(30, 7)).toBe(37));
});

describe("stayedStopped", () => {
  it("returns the reason the panel gives, and nothing otherwise", () => {
    expect(stayedStopped({ meta: { restart: "The lifetime was extended, but shop stays stopped." } })).toBe(
      "The lifetime was extended, but shop stays stopped.",
    );
    expect(stayedStopped({ meta: {} })).toBeNull();
    expect(stayedStopped({ meta: { restart: "" } })).toBeNull();
    expect(stayedStopped(undefined)).toBeNull();
  });
});
