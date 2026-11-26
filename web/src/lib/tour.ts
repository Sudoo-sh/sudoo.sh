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
  let left = rect.right + gap;
  if (left + size.width > screen.width - 8) left = rect.left - gap - size.width;
  if (left < 8) left = rect.left;
  const top = rect.top + (rect.bottom - rect.top) / 2 - size.height / 2;
  return { left: room(left, screen.width - size.width + 8), top: room(top, screen.height - size.height + 8) };
}

/** The Help page's "Show the tour again" asks the open tour component to start (it listens for this event). */
export const TOUR_RESTART_EVENT = "sudoo:tour-restart";
