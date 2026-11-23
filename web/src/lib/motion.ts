// The motion system's script side (styles/_motion.scss is the CSS side, components/app/motion.tsx the components):
// the same tokens as _tokens.scss for the Web Animations API, reduced motion, and helpers that keep motion
// interruptible (a new animation on an element replaces the one running there instead of queueing after it).

import { useSyncExternalStore } from "react";

/** The durations of _tokens.scss, in milliseconds. */
export const DURATION = { micro: 120, ui: 180, enter: 220, surface: 300, exit: 140, number: 600 } as const;
/** Lists come in 25 ms apart, at most eight steps. */
export const STAGGER = { step: 25, max: 7 } as const;

const SPRING_POINTS =
  "0, 0.074, 0.234, 0.415, 0.584, 0.723, 0.83, 0.906, 0.957, 0.988, 1.005, 1.013, 1.015, 1.014, 1.012, 1.009, 1.007, 1.004, 1.003, 1.002, 1";
const BOUNCE_POINTS =
  "0, 0.122, 0.375, 0.638, 0.849, 0.988, 1.06, 1.083, 1.076, 1.056, 1.034, 1.015, 1.003, 0.996, 0.993, 0.993, 0.995, 0.997, 0.998, 1, 1";
const EASE = "cubic-bezier(0.23, 1, 0.32, 1)";

const supportsLinear = () =>
  typeof CSS !== "undefined" && typeof CSS.supports === "function" && CSS.supports("animation-timing-function", "linear(0, 1)");

/** The spring curves for the Web Animations API (the cubic-bezier ease-out where linear() is not supported). */
export const easing = {
  get spring() {
    return supportsLinear() ? `linear(${SPRING_POINTS})` : EASE;
  },
  get bounce() {
    return supportsLinear() ? `linear(${BOUNCE_POINTS})` : EASE;
  },
  ease: EASE,
  out: "ease-out",
};

const REDUCE = "(prefers-reduced-motion: reduce)";

/** Whether the visitor prefers reduced motion (false where it cannot be known). */
export function reducedMotion(): boolean {
  return typeof window !== "undefined" && window.matchMedia?.(REDUCE).matches === true;
}

/** Reduced motion, kept up to date. */
export function useReducedMotion(): boolean {
  return useSyncExternalStore(
    (change) => {
      if (typeof window === "undefined" || !window.matchMedia) return () => {};
      const list = window.matchMedia(REDUCE);
      list.addEventListener("change", change);
      return () => list.removeEventListener("change", change);
    },
    reducedMotion,
    () => false,
  );
}

/** The delay of the n-th item of a list (from 0): 25 ms apart, the ninth and later together with the eighth. */
export function staggerDelay(index: number, step: number = STAGGER.step, max: number = STAGGER.max): number {
  return Math.max(0, Math.min(Math.floor(index), max)) * step;
}

const running = new WeakMap<Element, Animation>();

/** Plays an animation on an element, first cancelling the one this helper started there, so quick repeated actions
 *  never queue up. With reduced motion, keyframes that move are replaced by a short fade (or nothing, `fade: false`). */
export function play(
  element: Element | null | undefined,
  keyframes: Keyframe[],
  options: KeyframeAnimationOptions & { fade?: boolean } = {},
): Animation | null {
  if (!element || typeof element.animate !== "function") return null;
  running.get(element)?.cancel();
  const { fade = true, ...timing } = options;
  let frames = keyframes;
  if (reducedMotion()) {
    if (!fade) return null;
    const opacities = keyframes.map((frame) => frame.opacity).filter((value) => value != null);
    if (opacities.length < 2) return null;
    frames = [{ opacity: opacities[0] }, { opacity: opacities[opacities.length - 1] }];
    timing.duration = Math.min(Number(timing.duration ?? DURATION.ui), 150);
    timing.easing = "ease";
  }
  const animation = element.animate(frames, { duration: DURATION.ui, easing: easing.spring, ...timing });
  running.set(element, animation);
  const clear = () => {
    if (running.get(element) === animation) running.delete(element);
  };
  animation.addEventListener("finish", clear);
  animation.addEventListener("cancel", clear);
  return animation;
}

/** A tiny shake that says "no" (a wrong value), 4 px each way; nothing with reduced motion. */
export function shake(element: Element | null | undefined) {
  return play(
    element,
    [
      { transform: "translateX(0)" },
      { transform: "translateX(-4px)" },
      { transform: "translateX(4px)" },
      { transform: "translateX(-2px)" },
      { transform: "translateX(0)" },
    ],
    { duration: 280, easing: "ease-out", fade: false },
  );
}

/** Where each child is, by key: the first step of a FLIP (first, last, invert, play). */
export function measure(children: Iterable<HTMLElement>, key: (element: HTMLElement) => string | undefined) {
  const boxes = new Map<string, DOMRect>();
  for (const child of children) {
    const id = key(child);
    if (id) boxes.set(id, child.getBoundingClientRect());
  }
  return boxes;
}

/** The move from where a thing was to where it is now, or null when it has not moved (less than half a pixel). */
export function delta(before: DOMRect | undefined, after: DOMRect): { x: number; y: number } | null {
  if (!before) return null;
  const x = before.left - after.left;
  const y = before.top - after.top;
  return Math.abs(x) < 0.5 && Math.abs(y) < 0.5 ? null : { x, y };
}

/** FLIP: the children that moved since `before` glide from where they were to where they are. Only transform. */
export function glide(
  children: Iterable<HTMLElement>,
  before: Map<string, DOMRect>,
  key: (element: HTMLElement) => string | undefined,
  duration: number = DURATION.enter,
) {
  if (reducedMotion()) return;
  for (const child of children) {
    const id = key(child);
    // `before` includes a glide still under way, so the new one starts from where the thing is on screen right now
    running.get(child)?.cancel();
    const moved = id ? delta(before.get(id), child.getBoundingClientRect()) : null;
    if (moved)
      play(child, [{ transform: `translate(${moved.x}px, ${moved.y}px)` }, { transform: "none" }], {
        duration,
        easing: easing.spring,
        fade: false,
      });
  }
}

/** The ease-out curve numbers glide on (cubic): fast at first, settling softly. */
export const easeOutCubic = (t: number) => 1 - Math.pow(1 - Math.min(1, Math.max(0, t)), 3);
