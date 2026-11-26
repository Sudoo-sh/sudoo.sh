import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import tourWords from "@/i18n/tour";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { TourCard } from "@/components/TourCard";
import {
  BACKUPS_TOUR,
  PAGE_TOURS,
  PANEL_TOUR,
  PANEL_TOUR_ID,
  PROJECTS_TOUR,
  manualPath,
  mayAutoStart,
  mayAutoStartTour,
  place,
  settle,
  shouldStart,
  visibleSteps,
  type TourDef,
} from "./tour";

describe("the panel tour", () => {
  it("has unique step ids, each with words in English and Arabic", () => {
    const ids = PANEL_TOUR.map((s) => s.id);
    expect(new Set(ids).size).toBe(ids.length);
    for (const id of ids) {
      for (const lang of ["en", "ar"] as const) {
        const w = tourWords[lang].steps[id as keyof typeof tourWords.en.steps];
        expect(w.title.trim()).not.toBe("");
        expect(w.body.trim()).not.toBe("");
      }
    }
  });

  it("links every step to a page that exists, and to a real heading of the User Manual", () => {
    const docs = join(__dirname, "../../../docs");
    for (const step of [...PANEL_TOUR, ...PAGE_TOURS.flatMap((tour) => tour.steps)]) {
      const [file, heading] = step.doc.split("#");
      expect(existsSync(join(docs, file!)), step.doc).toBe(true);
      expect(manualPath(step.doc)).toBe(`/help/docs/${step.doc}`);
      if (file!.startsWith("manual/") && !file!.endsWith("README.md"))
        expect(existsSync(join(docs, file!.replace(".md", ".ar.md")))).toBe(true);
      if (heading) {
        const slugs = readFileSync(join(docs, file!), "utf8")
          .split("\n")
          .filter((line) => line.startsWith("## "))
          .map((line) =>
            line
              .slice(3)
              .toLowerCase()
              .replace(/[^a-z0-9 -]/g, "")
              .trim()
              .replace(/ +/g, "-"),
          );
        expect(slugs).toContain(heading);
      }
    }
  });

  it("has page tours with their own saved names, unique step ids and words in both languages", () => {
    const names = ["panel-welcome", ...PAGE_TOURS.map((t) => t.id)];
    expect(new Set(names).size).toBe(names.length);
    for (const name of names) expect(name).toMatch(/^[a-z0-9][a-z0-9-]{0,39}$/); // what the server accepts
    const all = [...PANEL_TOUR, ...PAGE_TOURS.flatMap((t) => t.steps)].map((s) => s.id);
    expect(new Set(all).size).toBe(all.length);
    for (const tour of PAGE_TOURS)
      for (const step of tour.steps) {
        expect(step.target, step.id).toBeDefined();
        for (const lang of ["en", "ar"] as const) {
          const w = tourWords[lang].steps[step.id as keyof typeof tourWords.en.steps];
          expect(w.title.trim()).not.toBe("");
          expect(w.body.trim()).not.toBe("");
        }
      }
    for (const lang of ["en", "ar"] as const)
      for (const key of ["takeTour", "nothingTitle", "nothingText"] as const)
        expect(tourWords[lang][key].trim()).not.toBe("");
  });

  it("starts a page tour by itself only after the welcome tour was seen, and respects the switches", () => {
    const base = { seen: [] as string[], tips: true, enabled: true };
    expect(mayAutoStartTour(base, "panel-welcome")).toBe(true);
    expect(mayAutoStartTour(base, PROJECTS_TOUR.id)).toBe(false); // welcome first
    const welcomed = { ...base, seen: ["panel-welcome"] };
    expect(mayAutoStartTour(welcomed, PROJECTS_TOUR.id)).toBe(true);
    expect(mayAutoStartTour({ ...welcomed, seen: ["panel-welcome", PROJECTS_TOUR.id] }, PROJECTS_TOUR.id)).toBe(false);
    expect(mayAutoStartTour({ ...welcomed, tips: false }, BACKUPS_TOUR.id)).toBe(false);
    expect(mayAutoStartTour({ ...welcomed, enabled: false }, BACKUPS_TOUR.id)).toBe(false);
  });

  it("skips page steps whose target is not on the page (empty states) and does not start with none", () => {
    const empty: TourDef = PROJECTS_TOUR;
    expect(visibleSteps(empty.steps, (t) => t === "projects-new").map((s) => s.id)).toEqual(["projects-new"]);
    expect(
      shouldStart(
        visibleSteps(empty.steps, () => false),
        false,
        false,
      ),
    ).toBe(false);
    expect(settle(BACKUPS_TOUR.steps, "backups-actions", (t) => t === "backups-list")).toBeNull();
  });

  it("keeps only the steps whose target is on the screen", () => {
    const shown = visibleSteps(PANEL_TOUR, (t) => t === "nav-projects" || t === "nav-help");
    expect(shown.map((s) => s.id)).toEqual(["intro", "projects", "help"]);
  });

  it("starts only when unseen, with no dialog open, and with something to point at (asked of the visible steps)", () => {
    const wide = visibleSteps(PANEL_TOUR, () => true);
    const narrow = visibleSteps(PANEL_TOUR, () => false); // sidebar closed on a phone: only the introduction
    expect(shouldStart(wide, false, false)).toBe(true);
    expect(shouldStart(wide, true, false)).toBe(false);
    expect(shouldStart(wide, false, true)).toBe(false);
    expect(narrow.map((s) => s.id)).toEqual(["intro"]);
    expect(shouldStart(narrow, false, false)).toBe(false);
  });

  it("moves on or ends when the screen changes", () => {
    const all = () => true;
    expect(settle(PANEL_TOUR, "projects", all)?.id).toBe("projects");
    // the current item went away: the next one that can be shown
    expect(settle(PANEL_TOUR, "new-instance", (t) => t !== "nav-new-instance")?.id).toBe("backups");
    // nothing after the current one can be shown
    expect(settle(PANEL_TOUR, "help", (t) => t !== "nav-help")).toBeNull();
    // the sidebar closed: only the introduction is left, the tour ends
    expect(settle(PANEL_TOUR, "projects", () => false)).toBeNull();
  });

  it("draws each step in English and Arabic, with its Learn more link inside Help", () => {
    for (const lang of ["en", "ar"] as const) {
      const words = tourWords[lang];
      PANEL_TOUR.forEach((step, index) => {
        const html = renderToStaticMarkup(
          <MemoryRouter>
            <TourCard
              step={step}
              index={index}
              total={PANEL_TOUR.length}
              words={words}
              at={{ left: 10, top: 20 }}
              onBack={() => {}}
              onNext={() => {}}
              onSkip={() => {}}
              onLearn={() => {}}
            />
          </MemoryRouter>,
        );
        const text = words.steps[step.id as keyof typeof words.steps];
        expect(html).toContain('role="dialog"');
        expect(html).toContain("data-tour-popover");
        expect(html).toContain(text.title);
        expect(html).toContain(`href="/help/docs/${step.doc}"`);
        expect(html).toContain(words.learnMore);
        expect(html).toContain(words.skip);
        expect(html.includes(words.back)).toBe(index > 0);
        expect(html).toContain(index === PANEL_TOUR.length - 1 ? words.done : words.next);
      });
    }
  });

  it("starts by itself only when tours are on, tips are on and it was not seen", () => {
    const state = { seen: [] as string[], tips: true, enabled: true };
    expect(mayAutoStart(state)).toBe(true);
    expect(mayAutoStart({ ...state, enabled: false })).toBe(false); // the administrator's switch
    expect(mayAutoStart({ ...state, tips: false })).toBe(false); // the person's own choice
    expect(mayAutoStart({ ...state, seen: [PANEL_TOUR_ID] })).toBe(false);
    expect(mayAutoStart({ ...state, seen: ["other-tour"] })).toBe(true);
  });

  it("has words for the narrow-screen explanation, the administrator's switch and the failure messages, in both languages", () => {
    for (const lang of ["en", "ar"] as const) {
      for (const key of [
        "narrowTitle",
        "narrowText",
        "off",
        "notSaved",
        "notSavedText",
        "checkFailed",
        "checkFailedText",
      ] as const)
        expect(tourWords[lang][key].trim()).not.toBe("");
    }
  });

  it("places the popover beside the target and inside the screen", () => {
    const screen = { width: 1000, height: 700 };
    const size = { width: 320, height: 180 };
    const right = place({ left: 0, right: 240, top: 100, bottom: 130 }, size, screen);
    expect(right.left).toBe(252);
    const flipped = place({ left: 900, right: 990, top: 100, bottom: 130 }, size, screen); // RTL sidebar
    expect(flipped.left).toBe(900 - 12 - 320);
    const low = place({ left: 0, right: 240, top: 690, bottom: 700 }, size, screen);
    expect(low.top + size.height).toBeLessThanOrEqual(screen.height);
    const high = place({ left: 0, right: 240, top: 0, bottom: 10 }, size, screen);
    expect(high.top).toBeGreaterThanOrEqual(8);
  });

  it("never covers the item it points at when there is room on any side", () => {
    const screen = { width: 1000, height: 700 };
    const size = { width: 320, height: 180 };
    const overlaps = (
      r: { left: number; right: number; top: number; bottom: number },
      p: { left: number; top: number },
    ) => p.left < r.right && p.left + size.width > r.left && p.top < r.bottom && p.top + size.height > r.top;
    // a wide bar across the top: below it; a wide bar across the bottom: above it
    for (const r of [
      { left: 20, right: 980, top: 20, bottom: 80 },
      { left: 20, right: 980, top: 600, bottom: 660 },
      { left: 300, right: 700, top: 300, bottom: 400 },
    ]) {
      expect(overlaps(r, place(r, size, screen))).toBe(false);
    }
  });
});
