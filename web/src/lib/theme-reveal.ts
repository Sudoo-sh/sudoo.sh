/**
 * A theme change revealed by a circle that grows from the control that asked for it until it covers the page
 * (the View Transitions API). Without the API, or when the visitor prefers reduced motion, `update` simply runs at
 * once. `update` is told which (true: revealed) and must change the DOM synchronously (in React, wrap state changes in
 * flushSync).
 */
export const REVEAL_MS = 450;

export function revealTheme(origin: Element | null | undefined, update: (revealed: boolean) => void): Promise<void> {
  const doc = typeof document === "undefined" ? undefined : document;
  const reduce =
    typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true;
  if (!origin || !doc || typeof doc.startViewTransition !== "function" || reduce) {
    update(false);
    return Promise.resolve();
  }

  // the centre of the control: the same for a click, a tap and a key press
  const box = origin.getBoundingClientRect();
  const x = box.left + box.width / 2;
  const y = box.top + box.height / 2;
  const radius = Math.hypot(Math.max(x, window.innerWidth - x), Math.max(y, window.innerHeight - y));

  const root = doc.documentElement;
  // scopes the reveal's CSS (styles/_motion.scss) to this transition only
  root.classList.add("theme-reveal");
  const transition = doc.startViewTransition(() => update(true));
  const done = () => root.classList.remove("theme-reveal");
  transition.finished.then(done, done);
  return transition.ready.then(
    () => {
      root.animate(
        { clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${radius}px at ${x}px ${y}px)`] },
        { duration: REVEAL_MS, easing: "ease-in-out", pseudoElement: "::view-transition-new(root)" },
      );
    },
    () => {
      /* the transition was skipped: the theme has still changed */
    },
  );
}
