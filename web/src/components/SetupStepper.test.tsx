import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { SetupStepper } from "./SetupStepper";

// the words read the chosen language from the page when they load: no page here
vi.mock("@/i18n", async () => {
  const words = (await import("@/i18n/setup")).default;
  return { useT: () => words.en };
});

const states = (html: string) => [...html.matchAll(/<li data-state="(\w+)"/g)].map((m) => m[1]);
const enabled = (html: string) =>
  [...html.matchAll(/<li[^>]*><button type="button"( disabled="")?/g)].map((m) => !m[1]);

describe("SetupStepper", () => {
  it("shows the eight steps: done ones checked, the current one marked, the rest to do", () => {
    const html = renderToStaticMarkup(<SetupStepper current="address" reached="address" onOpen={() => {}} />);
    expect(states(html)).toEqual(["done", "done", "current", "todo", "todo", "todo", "todo", "todo"]);
    expect(html).toContain('aria-current="step"');
    expect(html).toContain("Step 3 of 8");
    expect(html.match(/<svg/g)).toHaveLength(2); // a check mark on each step done
    expect(html).toContain("Super admin");
  });

  it("opens again only the steps already reached, never the current one or done", () => {
    const html = renderToStaticMarkup(<SetupStepper current="server" reached="email" onOpen={() => {}} />);
    expect(enabled(html)).toEqual([true, false, true, true, false, false, false, false]);
  });

  it("marks the optional steps for screen readers", () => {
    const html = renderToStaticMarkup(<SetupStepper current="welcome" reached="welcome" onOpen={() => {}} />);
    expect(html.match(/\(Optional\)/g)).toHaveLength(3);
  });

  it("after the final confirm every step is done and none opens", () => {
    const html = renderToStaticMarkup(<SetupStepper current="done" reached="done" onOpen={() => {}} />);
    expect(states(html).every((s) => s === "done")).toBe(true);
    expect(enabled(html).every((e) => !e)).toBe(true);
  });
});
