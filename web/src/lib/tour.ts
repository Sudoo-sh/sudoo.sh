/**
 * The panel's first-visit tour: which steps there are, which of them can be shown on this screen, and what this
 * browser remembers. Pure functions, so tests can run them without a page.
 *
 * What is remembered is kept on the person's account on the server (GET /session and PATCH /account, "tours"): the
 * tours seen, and whether they want tips. It follows them to every browser. An administrator can turn the tours off for
 * everyone (the TOURS_ENABLED setting). "Show the tour again" on Help and "Restart all tours" on the Account page
 * clear what was seen.
 */

export type TourStepDef = {
  /** Lower case letters, digits and hyphens, unique in the tour. */
  id: string;
  /** The value of the element's data-tour attribute. Without one the step is centred. */
  target?: string;
  /** The page that explains the step, as a path under /help/docs/ (a manual page or a heading of the User Manual). */
  doc: string;
};

export const PANEL_TOUR_ID = "panel-welcome";

export const PANEL_TOUR: readonly TourStepDef[] = [
  { id: "intro", doc: "manual/README.md" },
  { id: "projects", target: "nav-projects", doc: "manual/02-connect-github-first-project.md" },
  { id: "new-instance", target: "nav-new-instance", doc: "USER_MANUAL.md#5-creating-an-instance" },
  { id: "backups", target: "nav-backups", doc: "manual/04-backups-and-restores.md" },
  { id: "help", target: "nav-help", doc: "manual/README.md" },
];

/** One tour: its name (what is stored as seen) and its steps. */
export type TourDef = { id: string; steps: readonly TourStepDef[] };

export const PANEL_TOUR_DEF: TourDef = { id: PANEL_TOUR_ID, steps: PANEL_TOUR };

/** Page tours: each starts by itself on its page once the welcome tour is seen, and has a "?" in the page header. */
export const PROJECTS_TOUR: TourDef = {
  id: "projects-page",
  steps: [
    { id: "projects-new", target: "projects-new", doc: "manual/02-connect-github-first-project.md#steps" },
    { id: "projects-grid", target: "projects-grid", doc: "manual/03-branches-stages-promotion.md" },
  ],
};

export const BACKUPS_TOUR: TourDef = {
  id: "backups-page",
  steps: [
    { id: "backups-list", target: "backups-list", doc: "manual/04-backups-and-restores.md" },
    { id: "backups-actions", target: "backups-actions", doc: "manual/04-backups-and-restores.md#steps" },
  ],
};

export const AUDIT_TOUR: TourDef = {
  id: "audit-page",
  steps: [
    { id: "audit-filters", target: "audit-filters", doc: "USER_MANUAL.md#11-backups-and-audit-log" },
    { id: "audit-export", target: "audit-export", doc: "USER_MANUAL.md#11-backups-and-audit-log" },
    { id: "audit-rows", target: "audit-rows", doc: "USER_MANUAL.md#11-backups-and-audit-log" },
  ],
};

export const INSTANCES_TOUR: TourDef = {
  id: "instances-page",
  steps: [
    { id: "instances-new", target: "instances-new", doc: "USER_MANUAL.md#5-creating-an-instance" },
    { id: "instances-card", target: "instances-card", doc: "USER_MANUAL.md#3-the-instances-page" },
  ],
};

export const SETTINGS_TOUR: TourDef = {
  id: "settings-page",
  steps: [
    { id: "settings-tabs", target: "settings-tabs", doc: "USER_MANUAL.md#12-settings" },
    { id: "settings-section", target: "settings-section", doc: "USER_MANUAL.md#12-settings" },
  ],
};

export const PLUGINS_TOUR: TourDef = {
  id: "plugins-page",
  steps: [
    { id: "plugins-tabs", target: "plugins-tabs", doc: "manual/12-plugins-and-marketplace.md" },
    { id: "plugins-card", target: "plugins-card", doc: "manual/12-plugins-and-marketplace.md" },
  ],
};

export const ACCOUNTS_TOUR: TourDef = {
  id: "accounts-page",
  steps: [
    { id: "accounts-new", target: "accounts-new", doc: "manual/07-team-members-and-roles.md" },
    { id: "accounts-rows", target: "accounts-rows", doc: "manual/07-team-members-and-roles.md" },
  ],
};

export const PAGE_TOURS: readonly TourDef[] = [
  PROJECTS_TOUR,
  BACKUPS_TOUR,
  AUDIT_TOUR,
  INSTANCES_TOUR,
  SETTINGS_TOUR,
  PLUGINS_TOUR,
  ACCOUNTS_TOUR,
];

/** Whether a tour may start by itself on its own: a page tour waits until the welcome tour has been seen. */
export const mayAutoStartTour = (state: TourState, id: string) =>
  mayAutoStart(state, id) && (id === PANEL_TOUR_ID || state.seen.includes(PANEL_TOUR_ID));

/** Where a step's "Learn more" goes: the page inside this panel's own Help. */
export const manualPath = (doc: string) => `/help/docs/${doc}`;

/** The steps that can be shown now: those without a target, and those whose target is visible on this screen. */
export function visibleSteps(steps: readonly TourStepDef[], isVisible: (target: string) => boolean): TourStepDef[] {
  return steps.filter((step) => step.target === undefined || isVisible(step.target));
}

/**
 * Whether to start, asked of the steps that can be shown now (visibleSteps), never of the whole list: only when there
 * is more than the centred introduction to show (on a narrow screen the sidebar is closed, so no step has a target
 * and the tour waits for a wider screen), it was not seen, and no dialog is open.
 */
export function shouldStart(shown: readonly TourStepDef[], seen: boolean, overlayOpen: boolean): boolean {
  return !seen && !overlayOpen && shown.some((step) => step.target !== undefined);
}

/**
 * The tour after the screen changed (resize, language, a menu opening): the steps that can be shown now, and the one
 * to show. The current step stays when it can still be shown, otherwise the next one that can; none left, or only the
 * introduction, ends the tour without marking it seen (it was not really shown).
 */
export function settle(
  all: readonly TourStepDef[],
  currentId: string,
  isVisible: (target: string) => boolean,
): { steps: TourStepDef[]; id: string } | null {
  const steps = visibleSteps(all, isVisible);
  if (!steps.some((step) => step.target !== undefined)) return null;
  if (steps.some((step) => step.id === currentId)) return { steps, id: currentId };
  const from = all.findIndex((step) => step.id === currentId);
  const next = steps.find((step) => all.indexOf(step) > from);
  return next ? { steps, id: next.id } : null;
}

/** What the server keeps for the person, and the panel's switch (the "tours" of the session and of the account). */
export type TourState = { seen: readonly string[]; tips: boolean; enabled: boolean };

/** Whether the tour may start by itself: the panel has tours on, the person wants tips, and this tour is not seen. */
export const mayAutoStart = (state: TourState, id = PANEL_TOUR_ID) =>
  state.enabled && state.tips && !state.seen.includes(id);

/** Left and top of the popover for a target rectangle: beside it, kept inside the screen. */
export function place(
  rect: { left: number; right: number; top: number; bottom: number },
  size: { width: number; height: number },
  screen: { width: number; height: number },
  gap = 12,
): { left: number; top: number } {
  const room = (v: number, max: number) => Math.max(8, Math.min(v, max - 8));
  const midTop = rect.top + (rect.bottom - rect.top) / 2 - size.height / 2;
  const midLeft = rect.left + (rect.right - rect.left) / 2 - size.width / 2;
  const fitsX = (l: number) => l >= 8 && l + size.width <= screen.width - 8;
  const fitsY = (t: number) => t >= 8 && t + size.height <= screen.height - 8;
  // beside the item first (the sidebar case), then below, then above: never over the item itself
  const sides: { left: number; top: number; ok: boolean }[] = [
    { left: rect.right + gap, top: midTop, ok: fitsX(rect.right + gap) },
    { left: rect.left - gap - size.width, top: midTop, ok: fitsX(rect.left - gap - size.width) },
    { left: midLeft, top: rect.bottom + gap, ok: fitsY(rect.bottom + gap) },
    { left: midLeft, top: rect.top - gap - size.height, ok: fitsY(rect.top - gap - size.height) },
  ];
  const side = sides.find((s) => s.ok);
  if (side)
    return { left: room(side.left, screen.width - size.width), top: room(side.top, screen.height - size.height) };
  // no room on any side (a very large item): the corner of the screen farthest from the item's middle
  return { left: room(midLeft, screen.width - size.width), top: room(midTop, screen.height - size.height) };
}

/** The Help page's "Show the tour again" asks the open tour component to start (it listens for this event). */
export const TOUR_RESTART_EVENT = "sudoo:tour-restart";

/** A page's "?" button asks one tour to start: the event's detail is the tour's id (none: the welcome tour). */
export const askTour = (id: string) => window.dispatchEvent(new CustomEvent(TOUR_RESTART_EVENT, { detail: id }));
