import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useLocale, useT } from "@/i18n";
import tourWords from "@/i18n/tour";
import {
  PANEL_TOUR,
  PANEL_TOUR_ID,
  TOUR_RESTART_EVENT,
  mayAutoStart,
  place,
  settle,
  shouldStart,
  visibleSteps,
} from "@/lib/tour";
import { api } from "@/api/client";
import { keys } from "@/api/queries";
import type { Session } from "@/api/types";
import { useQueryClient } from "@tanstack/react-query";
import { useToast } from "./app/overlay";
import { useMe } from "./Session";
import { useSaveTours } from "./useTours";
import { TourCard } from "./TourCard";

const targetOf = (name: string) => document.querySelector<HTMLElement>(`[data-tour="${name}"]`);
const isVisible = (name: string) => {
  const el = targetOf(name);
  return !!el && el.getClientRects().length > 0;
};
/** A dialog, drawer or menu of the page is open (not the tour's own): the tour waits. */
const overlayOpen = () =>
  [...document.querySelectorAll('[role="dialog"], [role="alertdialog"], [role="menu"]')].some(
    (el) => !el.hasAttribute("data-tour-popover"),
  );
const clearHighlight = () =>
  document.querySelectorAll("[data-tour-current]").forEach((el) => el.removeAttribute("data-tour-current"));

/**
 * The first-visit tour of the panel's frame: one popover at a time, no dimming, Skip and Esc always work. It looks at
 * the screen again when the window or the language changes or a dialog opens: a step whose item is gone moves on, a
 * dialog hides the tour until it closes, and focus returns to where it was when the tour ends.
 */
export function Tour() {
  const me = useMe();
  const t = useT(tourWords);
  const toast = useToast();
  const saveTours = useSaveTours();
  const client = useQueryClient();
  const tours = useRef(me.tours);
  tours.current = me.tours;
  const locale = useLocale();
  const [active, setActive] = useState(false);
  const [stepId, setStepId] = useState("");
  const [tick, setTick] = useState(0);
  const [paused, setPaused] = useState(false);
  const [at, setAt] = useState<{ left: number; top: number } | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const opener = useRef<HTMLElement | null>(null);

  /** `asked`: the person asked for it (Help), so tips and "seen" do not count, and a screen too narrow says why. */
  const begin = useCallback(
    async (asked: boolean) => {
      // ask the server again first: another browser, or the administrator's switch, may have changed things
      try {
        const fresh = await api.get<Session>("/session");
        client.setQueryData(keys.session, fresh);
        tours.current = fresh.tours;
      } catch {
        // cannot confirm the current switch: do not start from a possibly stale copy
        if (asked) toast("error", tourWords[locale].checkFailed, tourWords[locale].checkFailedText);
        return;
      }
      if (!tours.current.enabled) {
        if (asked) toast("info", tourWords[locale].off);
        return;
      }
      const shown = visibleSteps(PANEL_TOUR, isVisible);
      if (asked && !shouldStart(shown, false, false)) {
        toast("info", tourWords[locale].narrowTitle, tourWords[locale].narrowText);
        return;
      }
      if (!shouldStart(shown, asked ? false : !mayAutoStart(tours.current), overlayOpen())) return;
      opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      setStepId(shown[0]?.id ?? "");
      setAt(null);
      setActive(true);
    },
    [locale, toast, client],
  );

  const stop = useCallback(
    (seen: boolean) => {
      if (seen)
        void saveTours({ mark: [PANEL_TOUR_ID] }).then((saved) => {
          if (!saved) toast("error", tourWords[locale].notSaved, tourWords[locale].notSavedText);
        });
      clearHighlight();
      setActive(false);
      setPaused(false);
      const back = opener.current;
      opener.current = null;
      if (back?.isConnected) back.focus();
    },
    [saveTours, toast, locale],
  );

  // the first visit: wait for the frame to draw, then decide
  useEffect(() => {
    const timer = window.setTimeout(() => void begin(false), 1200);
    return () => window.clearTimeout(timer);
    // once per person: a later language change must not start it
  }, [me.user]);

  // "Show the tour again" on the Help page
  useEffect(() => {
    const again = () => {
      void begin(true);
    };
    window.addEventListener(TOUR_RESTART_EVENT, again);
    return () => window.removeEventListener(TOUR_RESTART_EVENT, again);
  }, [begin]);

  // look again when the window changes, anything scrolls, or the page's own elements change
  useEffect(() => {
    if (!active) return;
    let frame = 0;
    const later = () => {
      if (frame) return;
      frame = window.requestAnimationFrame(() => {
        frame = 0;
        setTick((n) => n + 1);
      });
    };
    const watch = new MutationObserver((records) => {
      if (records.every((r) => box.current?.contains(r.target))) return;
      later();
    });
    // also attributes: a sidebar that goes from hidden to shown changes a class or attribute, not the tree
    watch.observe(document.body, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["class", "style", "hidden", "aria-hidden", "data-state", "open"],
    });
    window.addEventListener("resize", later);
    window.addEventListener("scroll", later, true);
    return () => {
      watch.disconnect();
      window.removeEventListener("resize", later);
      window.removeEventListener("scroll", later, true);
      if (frame) window.cancelAnimationFrame(frame);
    };
  }, [active]);

  useLayoutEffect(() => {
    if (!active) return;
    if (overlayOpen()) {
      clearHighlight();
      setPaused(true);
      return;
    }
    setPaused(false);
    const now = settle(PANEL_TOUR, stepId, isVisible);
    if (!now) {
      stop(false);
      return;
    }
    if (now.id !== stepId) {
      setStepId(now.id);
      return;
    }
    clearHighlight();
    const step = now.steps.find((s) => s.id === stepId);
    const target = step?.target ? targetOf(step.target) : null;
    target?.setAttribute("data-tour-current", "");
    const size = box.current?.getBoundingClientRect() ?? { width: 320, height: 200 };
    const screen = { width: window.innerWidth, height: window.innerHeight };
    const next = target
      ? place(target.getBoundingClientRect(), size, screen)
      : {
          left: Math.max(8, (screen.width - size.width) / 2),
          top: Math.max(8, Math.min((screen.height - size.height) / 3, screen.height - size.height - 8)),
        };
    setAt((old) => (old && old.left === next.left && old.top === next.top ? old : next));
  }, [active, stepId, tick, locale, stop]);

  const shownSteps = active ? visibleSteps(PANEL_TOUR, isVisible) : [];
  const step = shownSteps.find((s) => s.id === stepId);
  const index = step ? shownSteps.indexOf(step) : -1;

  useEffect(() => {
    if (!active || paused || !step) return;
    box.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") stop(true);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [active, paused, step, stop]);

  if (!active || paused || !step) return null;
  return (
    <TourCard
      ref={box}
      step={step}
      index={index}
      total={shownSteps.length}
      words={t}
      at={at}
      onBack={() => setStepId(shownSteps[index - 1]?.id ?? stepId)}
      onNext={() => (index === shownSteps.length - 1 ? stop(true) : setStepId(shownSteps[index + 1]?.id ?? ""))}
      onSkip={() => stop(true)}
      onLearn={() => stop(true)}
    />
  );
}
