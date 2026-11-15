// What kind of move the page transition (styles/_motion.scss) is: another tab of the same page (an instance's tabs,
// the Plugins tabs), where only the tab's content fades, or another page, where the whole view fades out and the new
// one fades in.

import type { createBrowserRouter } from "react-router";

type Router = ReturnType<typeof createBrowserRouter>;

/** The page a path belongs to, without its tab: /i/<name>/activity -> /i/<name>, /plugins/store -> /plugins. */
export function pageOf(pathname: string): string {
  const parts = pathname.split("/").filter(Boolean);
  if (parts[0] === "i" && parts[1]) return `/i/${parts[1]}`;
  if (parts[0] === "plugins") return "/plugins";
  return pathname;
}

/** Keeps `data-nav="tab" | "page"` on <html> up to date. Call once, before the router is rendered, so this runs
 *  before the router starts the page transition (the old view is captured with the new value). */
export function followNavigationKind(router: Router) {
  const root = document.documentElement;
  let location = router.state.location;
  router.subscribe((state) => {
    if (state.location === location) return;
    root.dataset.nav = pageOf(state.location.pathname) === pageOf(location.pathname) ? "tab" : "page";
    location = state.location;
  });
}
