// Which way the visitor moves between pages, for the page transition (styles/_motion.scss): going back through the
// browser's history brings the new page in from the left, anything else from the right. The router numbers its
// history entries (history.state.idx), so a step back is a smaller number than the page being left.

import type { createBrowserRouter } from "react-router";

type Router = ReturnType<typeof createBrowserRouter>;

const historyIndex = (): number => {
  const idx = (window.history.state as { idx?: unknown } | null)?.idx;
  return typeof idx === "number" ? idx : 0;
};

/** Keeps `data-nav="forward" | "back"` on <html> up to date. Call once, before the router is rendered, so this runs
 *  before the router starts the page transition. */
export function followNavigationDirection(router: Router) {
  const root = document.documentElement;
  let location = router.state.location;
  let index = historyIndex();
  router.subscribe((state) => {
    if (state.location === location) return;
    location = state.location;
    const next = historyIndex();
    root.dataset.nav = state.historyAction === "POP" && next < index ? "back" : "forward";
    index = next;
  });
}
