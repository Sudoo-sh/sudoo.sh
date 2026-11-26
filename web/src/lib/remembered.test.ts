import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { readRemembered, writeRemembered } from "./remembered";

const ALLOWED = ["all", "online", "paused"] as const;

function fakeStorage(refuse = false) {
  const items = new Map<string, string>();
  const guard = () => {
    if (refuse) throw new Error("blocked");
  };
  return {
    items,
    getItem: (k: string) => (guard(), items.get(k) ?? null),
    setItem: (k: string, v: string) => (guard(), void items.set(k, v)),
    removeItem: (k: string) => (guard(), void items.delete(k)),
  };
}

describe("remembered list choices", () => {
  let store = fakeStorage();
  beforeEach(() => {
    store = fakeStorage();
    vi.stubGlobal("localStorage", store);
  });
  afterEach(() => vi.unstubAllGlobals());

  it("starts from the starting value when nothing was kept", () => {
    expect(readRemembered("t", ALLOWED, "all")).toBe("all");
  });

  it("gives back what was kept", () => {
    writeRemembered("t", "paused", "all");
    expect(readRemembered("t", ALLOWED, "all")).toBe("paused");
  });

  it("forgets the choice when it goes back to the starting value", () => {
    writeRemembered("t", "paused", "all");
    writeRemembered("t", "all", "all");
    expect(store.items.has("sudoo.list.t")).toBe(false);
  });

  it("ignores a kept value the page no longer offers", () => {
    store.items.set("sudoo.list.t", "gone");
    expect(readRemembered("t", ALLOWED, "all")).toBe("all");
  });

  it("falls back to the starting value when the browser refuses storage", () => {
    vi.stubGlobal("localStorage", fakeStorage(true));
    expect(readRemembered("t", ALLOWED, "online")).toBe("online");
    expect(() => writeRemembered("t", "paused", "all")).not.toThrow();
  });
});
