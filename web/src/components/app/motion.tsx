// The motion system's components (docs/DESIGN.md, Motion): small wrappers a page puts around what it already draws.
// The motion itself is CSS (styles/_motion.scss) wherever it can be; these only add the classes, the state and the
// measurements CSS cannot know. Everything here works without animation and with reduced motion.

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type ElementType,
  type ReactNode,
} from "react";
import { cn } from "@/lib/utils";
import { DURATION, easeOutCubic, reducedMotion, staggerDelay } from "@/lib/motion";

// ---------------------------------------------------------------- arriving

/** Its children come in (fade and a 6 px rise, the spring) when it is first drawn; `index` delays it as the n-th of a
 *  list (25 ms apart, at most eight steps). */
export function Reveal({
  as: Tag = "div",
  index = 0,
  className,
  style,
  children,
  ...props
}: {
  as?: ElementType;
  index?: number;
  className?: string;
  style?: CSSProperties;
  children?: ReactNode;
} & Record<string, unknown>) {
  return (
    <Tag
      className={cn("reveal", className)}
      style={{ ...style, "--reveal-delay": `${staggerDelay(index)}ms` } as CSSProperties}
      {...props}
    >
      {children}
    </Tag>
  );
}

/** A list whose items come in one after the other on first load (25 ms apart, capped at eight steps). Items added
 *  later come in the same way, on their own; items that stay are not animated again. */
export function Stagger({
  as: Tag = "div",
  className,
  children,
  ...props
}: { as?: ElementType; className?: string; children?: ReactNode } & Record<string, unknown>) {
  return (
    <Tag className={cn("stagger-in", className)} {...props}>
      {children}
    </Tag>
  );
}

// ---------------------------------------------------------------- arriving and leaving

/** Keeps its children on screen while they leave: `show` false plays the exit (quicker than the entry) and then
 *  removes them. `collapse` also opens and closes the room they take (height), so what is around them moves smoothly
 *  instead of jumping. Showing again while leaving turns the motion round (nothing queues). */
export function Presence({
  show,
  collapse,
  className,
  children,
  as: Tag = "div",
}: {
  show: boolean;
  collapse?: boolean;
  className?: string;
  children: ReactNode;
  as?: ElementType;
}) {
  const [mounted, setMounted] = useState(show);
  // while leaving, the children that were last shown stay (the caller may already have dropped them)
  const last = useRef<ReactNode>(children);
  if (show) last.current = children;
  if (show && !mounted) setMounted(true);
  useEffect(() => {
    if (show) return;
    const timer = window.setTimeout(() => setMounted(false), reducedMotion() ? 0 : DURATION.exit + 40);
    return () => window.clearTimeout(timer);
  }, [show]);
  if (!mounted) return null;
  const state = show ? "open" : "closed";
  const content = show ? children : last.current;
  if (collapse)
    return (
      <Tag className={cn("presence collapse", className)} data-state={state} aria-hidden={show ? undefined : true}>
        <div className="collapse-inner">{content}</div>
      </Tag>
    );
  return (
    <Tag className={cn("presence", className)} data-state={state} aria-hidden={show ? undefined : true}>
      {content}
    </Tag>
  );
}

// ---------------------------------------------------------------- numbers

/** A number that glides to its new value (ease-out, 600 ms) in tabular figures, so the digits do not jitter. `from`
 *  is where it starts the first time it is drawn (a dashboard's figure counts up from 0); without it the first value
 *  shows at once. A new value while gliding continues from where the number is (nothing queues). Reduced motion:
 *  the new value at once. */
export function AnimatedNumber({
  value,
  format = (n) => n.toFixed(0),
  from,
  duration = DURATION.number,
}: {
  value: number;
  format?: (n: number) => string;
  from?: number;
  duration?: number;
}) {
  const node = useRef<HTMLSpanElement>(null);
  const shown = useRef(from ?? value);
  const formatter = useRef(format);
  formatter.current = format;
  useLayoutEffect(() => {
    const element = node.current;
    if (!element) return;
    const begin = shown.current;
    const write = (n: number) => {
      shown.current = n;
      element.textContent = formatter.current(n);
    };
    if (begin === value || reducedMotion() || !Number.isFinite(begin) || !Number.isFinite(value)) {
      write(value);
      return;
    }
    const start = performance.now();
    let frame = requestAnimationFrame(function step(now) {
      const t = (now - start) / duration;
      write(t >= 1 ? value : begin + (value - begin) * easeOutCubic(t));
      if (t < 1) frame = requestAnimationFrame(step);
    });
    return () => cancelAnimationFrame(frame);
  }, [value, duration]);
  // the text is written by the effect above, frame by frame, without rendering the component again
  return (
    <span ref={node} className="num">
      {format(shown.current)}
    </span>
  );
}

// ---------------------------------------------------------------- icons

/** Icons that turn into one another in place (scale, fade and a little blur): a copy icon into a check, the theme's
 *  sun into a moon. `active` names the one shown; every one keeps the same box, so nothing around it moves. */
export function MorphIcon({
  icons,
  active,
  className,
}: {
  icons: Record<string, ReactNode>;
  active: string;
  className?: string;
}) {
  return (
    <span className={cn("morph-icon", className)} aria-hidden="true">
      {Object.entries(icons).map(([key, icon]) => (
        <span key={key} className="morph-icon-item" data-active={key === active || undefined}>
          {icon}
        </span>
      ))}
    </span>
  );
}

/** A check in a circle whose stroke draws itself in when it appears (success); still with reduced motion. */
export function SuccessCheck({ className }: { className?: string }) {
  return (
    <svg
      className={cn("success-check", className)}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="10" pathLength={1} />
      <path d="m9 12 2 2 4-4" pathLength={1} />
    </svg>
  );
}

// ---------------------------------------------------------------- tabs

const ACTIVE_TAB = ':scope > :is(.active, [aria-selected="true"], [aria-current="page"])';

/** The underline of a row of tabs (`.tabs`) that slides to the chosen tab instead of jumping (only transform). Put it
 *  inside the `.tabs` element, after the tabs. */
export function TabInk() {
  const ink = useRef<HTMLSpanElement>(null);
  const place = useCallback(() => {
    const line = ink.current;
    const row = line?.parentElement;
    const tab = row?.querySelector<HTMLElement>(ACTIVE_TAB);
    if (!line || !row) return;
    if (!tab) {
      line.removeAttribute("data-shown");
      return;
    }
    // the same place as the tab's own underline was: inside its padding, along its bottom edge
    const inset = parseFloat(getComputedStyle(tab).paddingInlineStart) || 0;
    const width = Math.max(0, tab.offsetWidth - inset * 2);
    line.style.transform = `translate(${tab.offsetLeft + inset}px, ${tab.offsetTop + tab.offsetHeight - 1}px) scaleX(${width})`;
    if (!line.hasAttribute("data-shown")) {
      line.setAttribute("data-shown", "");
      // the first place is taken at once; from then on it slides
      requestAnimationFrame(() => line.setAttribute("data-ready", ""));
    }
  }, []);
  // after every render of the row (a tab chosen, tabs added or removed)
  useLayoutEffect(place);
  useEffect(() => {
    const row = ink.current?.parentElement;
    if (!row || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(place);
    observer.observe(row);
    const mutations = new MutationObserver(place);
    mutations.observe(row, {
      subtree: true,
      attributes: true,
      attributeFilter: ["class", "aria-selected", "aria-current"],
      childList: true,
    });
    return () => {
      observer.disconnect();
      mutations.disconnect();
    };
  }, [place]);
  return <span ref={ink} className="tab-ink" aria-hidden="true" />;
}

// ---------------------------------------------------------------- one thing from one page to the next

let hero: string | null = null;
const holders = new Set<HTMLElement>();

/** Shared-element continuity between two pages: the element given `useHero(key)` on one page (a card's title) and
 *  the one with the same key on the next (the page's title) are one thing that moves and grows into place during the
 *  page transition. Call `startHero` when the first one is pressed. Only one element at a time carries the name, so
 *  a view never has two (the browser would then skip the transition). */
export function useHero<T extends HTMLElement>(key: string) {
  const node = useRef<T>(null);
  useLayoutEffect(() => {
    const element = node.current;
    if (
      !element ||
      hero !== key ||
      reducedMotion() ||
      [...holders].some((other) => other !== element && other.isConnected)
    )
      return;
    element.style.viewTransitionName = "hero";
    holders.add(element);
    return () => {
      element.style.viewTransitionName = "";
      holders.delete(element);
    };
  });
  return node;
}

/** Marks `element` as the one that moves into the next page (see useHero). */
export function startHero(key: string, element: HTMLElement | null) {
  for (const other of holders) other.style.viewTransitionName = "";
  holders.clear();
  hero = key;
  if (element && !reducedMotion()) {
    element.style.viewTransitionName = "hero";
    holders.add(element);
  }
}
