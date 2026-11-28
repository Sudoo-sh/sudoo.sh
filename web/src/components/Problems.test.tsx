import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import problems from "@/i18n/problems";
import { AppErrorBoundary, ErrorScreen } from "./Problems";

// the words in the language the test chooses: no page here to read it from
const language = vi.hoisted(() => ({ current: "en" as "en" | "ar" }));
vi.mock("@/i18n", () => ({
  useT: (dict: Record<string, unknown>) => dict[language.current],
  words: (dict: Record<string, unknown>) => dict[language.current],
  useLocale: () => language.current,
  getLocale: () => language.current,
}));

describe("ErrorScreen", () => {
  it("shows a way out instead of a blank page, with the error's message tucked away", () => {
    language.current = "en";
    const html = renderToStaticMarkup(<ErrorScreen error={new Error("Cannot read properties of undefined")} />);
    expect(html).toContain('role="alert"');
    expect(html).toContain(problems.en.screen.title);
    expect(html).toContain(problems.en.screen.reload);
    expect(html).toContain(problems.en.screen.home);
    expect(html).toContain("<details>");
    expect(html).toContain("Cannot read properties of undefined");
  });

  it("speaks Arabic on the Arabic page", () => {
    language.current = "ar";
    const html = renderToStaticMarkup(<ErrorScreen error="boom" />);
    expect(html).toContain(problems.ar.screen.title);
    expect(html).toContain(problems.ar.screen.reload);
    language.current = "en";
  });

  it("has no details when there is no message", () => {
    expect(renderToStaticMarkup(<ErrorScreen />)).not.toContain("<details>");
  });
});

describe("AppErrorBoundary", () => {
  it("draws the error screen once a child failed to draw", () => {
    const boundary = new AppErrorBoundary({ children: <p>dashboard-content</p> });
    expect(renderToStaticMarkup(<>{boundary.render()}</>)).toContain("dashboard-content");
    boundary.state = AppErrorBoundary.getDerivedStateFromError(new Error("render failed"));
    const html = renderToStaticMarkup(<>{boundary.render()}</>);
    expect(html).not.toContain("dashboard-content");
    expect(html).toContain(problems.en.screen.title);
    expect(html).toContain("render failed");
  });

  it("treats a thrown value that is not an Error as an error too", () => {
    expect(AppErrorBoundary.getDerivedStateFromError(undefined).error).toBeTruthy();
  });
});
