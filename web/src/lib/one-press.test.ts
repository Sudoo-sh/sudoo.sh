import { describe, expect, it, vi } from "vitest";
import { DOUBLE_CLICK_MS, onePress } from "./one-press";

describe("onePress", () => {
  it("ignores a second press while the first one's work runs, then accepts presses again", async () => {
    let calls = 0;
    let finish: () => void = () => undefined;
    const running = { current: false };
    const press = onePress(running, () => {
      calls++;
      return new Promise<void>((resolve) => (finish = resolve));
    })!;
    press("click");
    press("click"); // a double click
    expect(calls).toBe(1);
    finish();
    await Promise.resolve();
    await Promise.resolve();
    press("click");
    expect(calls).toBe(2);
  });

  it("frees the button when the work fails", async () => {
    const running = { current: false };
    const press = onePress(running, () => Promise.reject(new Error("refused")))!;
    press("click");
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(running.current).toBe(false);
  });

  it("lets every press through when the handler does no lasting work", () => {
    let calls = 0;
    const press = onePress({ current: false }, () => {
      calls++;
    })!;
    press("click");
    press("click");
    expect(calls).toBe(2);
  });

  it("has nothing to wrap without a handler", () => {
    expect(onePress({ current: false }, undefined)).toBeUndefined();
  });
});

describe("onePress on a button that shows a busy state", () => {
  it("ignores a double click on a handler that starts work without returning it", () => {
    vi.useFakeTimers();
    let calls = 0;
    const press = onePress(
      { current: false },
      () => {
        calls++;
      },
      true,
    )!;
    press("click");
    press("click");
    expect(calls).toBe(1);
    vi.advanceTimersByTime(DOUBLE_CLICK_MS);
    press("click");
    expect(calls).toBe(2);
    vi.useRealTimers();
  });
});
