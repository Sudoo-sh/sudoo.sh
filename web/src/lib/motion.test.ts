import { afterEach, describe, expect, it, vi } from "vitest";
import { delta, easeOutCubic, play, reducedMotion, shake, staggerDelay } from "./motion";

describe("staggerDelay", () => {
  it("spaces items 25 ms apart", () => {
    expect([0, 1, 2, 3].map((i) => staggerDelay(i))).toEqual([0, 25, 50, 75]);
  });

  it("caps the list at eight steps, so a long list never keeps the last items waiting", () => {
    expect(staggerDelay(7)).toBe(175);
    expect(staggerDelay(8)).toBe(175);
    expect(staggerDelay(500)).toBe(175);
  });

  it("never waits before the first item", () => {
    expect(staggerDelay(-3)).toBe(0);
  });
});

describe("delta", () => {
  const box = (left: number, top: number) => ({ left, top }) as DOMRect;

  it("is the move from where a thing was to where it is", () => {
    expect(delta(box(10, 100), box(10, 40))).toEqual({ x: 0, y: 60 });
  });

  it("is nothing for a thing that did not move, or was not there before", () => {
    expect(delta(box(10, 40.2), box(10, 40))).toBeNull();
    expect(delta(undefined, box(0, 0))).toBeNull();
  });
});

describe("easeOutCubic", () => {
  it("starts at 0, ends at 1 and is past half way early", () => {
    expect(easeOutCubic(0)).toBe(0);
    expect(easeOutCubic(1)).toBe(1);
    expect(easeOutCubic(0.25)).toBeGreaterThan(0.5);
    expect(easeOutCubic(2)).toBe(1);
  });
});

/** An element whose animations are recorded, as a browser would start them. */
function fakeElement() {
  const animations: { keyframes: Keyframe[]; options: KeyframeAnimationOptions; cancelled: boolean }[] = [];
  const element = {
    animate(keyframes: Keyframe[], options: KeyframeAnimationOptions) {
      const record = { keyframes, options, cancelled: false };
      animations.push(record);
      return {
        cancel: () => (record.cancelled = true),
        addEventListener: () => {},
      } as unknown as Animation;
    },
  } as unknown as Element;
  return { element, animations };
}

function preferReducedMotion(reduce: boolean) {
  vi.stubGlobal("window", { matchMedia: () => ({ matches: reduce }) });
}

describe("play", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("replaces the animation it started on an element instead of queueing after it", () => {
    preferReducedMotion(false);
    const { element, animations } = fakeElement();
    play(element, [{ opacity: 0 }, { opacity: 1 }]);
    play(element, [{ opacity: 1 }, { opacity: 0 }]);
    expect(animations).toHaveLength(2);
    expect(animations[0]!.cancelled).toBe(true);
    expect(animations[1]!.cancelled).toBe(false);
  });

  it("turns movement into a short fade with reduced motion", () => {
    preferReducedMotion(true);
    expect(reducedMotion()).toBe(true);
    const { element, animations } = fakeElement();
    play(element, [{ opacity: 0, transform: "translateY(6px)" }, { opacity: 1, transform: "none" }], { duration: 300 });
    expect(animations[0]!.keyframes).toEqual([{ opacity: 0 }, { opacity: 1 }]);
    expect(animations[0]!.options.duration).toBe(150);
  });

  it("does not shake with reduced motion", () => {
    preferReducedMotion(true);
    const { element, animations } = fakeElement();
    expect(shake(element)).toBeNull();
    expect(animations).toHaveLength(0);
  });

  it("does nothing without an element", () => {
    expect(play(null, [{ opacity: 0 }])).toBeNull();
  });
});
