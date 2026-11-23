// What kind of move the page transition (styles/_motion.scss) is: another tab of the same page (an instance's tabs,
// the Plugins tabs), where only the tab's content fades; another branch of the same project, the same plus a short
// cross-fade of the branch's part of the project's header; or another page, where the whole view fades out and the
// new one fades in.

import type { createBrowserRouter } from "react-router";

type Router = ReturnType<typeof createBrowserRouter>;

/** The page a path belongs to, without its tab: /i/<name>/activity -> /i/<name>, /projects/<id>/settings
 *  -> /projects/<id>, /plugins/store -> /plugins. */
export function pageOf(pathname: string): string {
  const parts = pathname.split("/").filter(Boolean);
  if (parts[0] === "i" && parts[1]) return `/i/${parts[1]}`;
  if (parts[0] === "projects" && parts[1] && parts[1] !== "new") return `/projects/${parts[1]}`;
  if (parts[0] === "plugins") return "/plugins";
  return pathname;
}

/** The branch a project's path names (/projects/<id>/branches/<branch>/<view>), or null when it names none. */
export function branchOf(pathname: string): string | null {
  const parts = pathname.split("/").filter(Boolean);
  if (parts[0] !== "projects" || parts[2] !== "branches" || !parts[3]) return null;
  try {
    return decodeURIComponent(parts[3]);
  } catch {
    return parts[3];
  }
}

/** What kind of move going from one path to another is: "page", "branch" (another branch of the same project) or
 *  "tab" (anything else within the same page). */
export function navigationKind(from: string, to: string): "page" | "branch" | "tab" {
  if (pageOf(from) !== pageOf(to)) return "page";
  return branchOf(from) !== branchOf(to) ? "branch" : "tab";
}

/** Keeps `data-nav="tab" | "branch" | "page"` on <html> up to date. Call once, before the router is rendered, so
 *  this runs before the router starts the page transition (the old view is captured with the new value). */
export function followNavigationKind(router: Router) {
  const root = document.documentElement;
  let location = router.state.location;
  router.subscribe((state) => {
    if (state.location === location) return;
    root.dataset.nav = navigationKind(location.pathname, state.location.pathname);
    location = state.location;
  });
}
