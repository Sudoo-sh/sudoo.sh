import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Failure } from "@/api/types";

const lang = { current: "en" as "en" | "ar" };
vi.mock("@/i18n", async () => {
  const words = (await import("@/i18n/failure")).default;
  return { useT: () => words[lang.current], words: () => words[lang.current], getLocale: () => lang.current };
});
vi.mock("@/i18n/jobs", () => ({ stepTitle: (s: string) => s }));
vi.mock("./Session", () => ({ useCan: () => () => false }));
vi.mock("@/api/queries", () => ({ useCall: () => ({ mutate: () => {}, isPending: false }), useInstance: () => ({}) }));
vi.mock("@/api/client", () => ({ api: {}, errorText: (e: unknown) => String(e) }));

import words from "@/i18n/failure";
import { WhyItFailed } from "./WhyItFailed";

const base: Failure = {
  step: "Upgrading modules",
  reason: "The update failed.",
  kind: "odoo",
  details: ["ERROR odoo: something"],
  repository: null,
};
const withRule: Failure = {
  ...base,
  rule: {
    id: "odoo_xml_parse_error",
    title: "server english title",
    explanation: "server english explanation",
    fix: "server english fix",
    captured: { file: "shop/views/a.xml", line: "42" },
    excerpt_line: 1,
  },
};

beforeEach(() => {
  lang.current = "en";
});

describe("WhyItFailed cause", () => {
  it("shows the recognised cause with what the log named", () => {
    const html = renderToStaticMarkup(<WhyItFailed failure={withRule} />);
    expect(html).toContain(words.en.rules.odoo_xml_parse_error.title);
    expect(html).toContain("shop/views/a.xml");
    expect(html).toContain("42");
    expect(html).toContain(words.en.rules.odoo_xml_parse_error.fix);
  });
  it("marks the matched line of the shown details and says it is a line of the details, not of the file", () => {
    const html = renderToStaticMarkup(<WhyItFailed failure={withRule} />);
    expect(html).toContain('id="why-line-1" class="why-mark"');
    expect(html).toContain(words.en.ruleLabels.excerptLine);
    expect(html).toContain(words.en.ruleLabels.line);
    expect(words.en.ruleLabels.excerptLine).not.toBe(words.en.ruleLabels.line);
  });
  it("does not say the cause twice: the reason that is the rule's own text is left to the card", () => {
    const own = { ...withRule, reason: "server english title. server english explanation" };
    expect(renderToStaticMarkup(<WhyItFailed failure={own} />)).not.toContain("server english title. server");
    const other = { ...withRule, reason: "The update failed and was put back." };
    expect(renderToStaticMarkup(<WhyItFailed failure={other} />)).toContain("The update failed and was put back.");
  });
  it("marks nothing when the server gave no excerpt line", () => {
    const html = renderToStaticMarkup(
      <WhyItFailed failure={{ ...withRule, rule: { ...withRule.rule!, excerpt_line: undefined } }} />,
    );
    expect(html).not.toContain("why-mark");
    expect(html).not.toContain(words.en.ruleLabels.excerptLine);
  });
  it("shows the Arabic text of the same rule", () => {
    lang.current = "ar";
    const html = renderToStaticMarkup(<WhyItFailed failure={withRule} />);
    expect(html).toContain(words.ar.rules.odoo_xml_parse_error.title);
    expect(html).not.toContain("server english title");
  });
  it("falls back to the server's text for a rule this web app does not know", () => {
    const html = renderToStaticMarkup(
      <WhyItFailed failure={{ ...withRule, rule: { ...withRule.rule!, id: "future_rule" } }} />,
    );
    expect(html).toContain("server english title");
  });
  it("renders an older entry without a rule exactly as before", () => {
    const html = renderToStaticMarkup(<WhyItFailed failure={base} />);
    expect(html).not.toContain("why-cause");
    expect(html).toContain("The update failed.");
    expect(html).toContain("ERROR odoo: something");
  });
  it("has Arabic for every rule id, as many as English", () => {
    expect(Object.keys(words.ar.rules).sort()).toEqual(Object.keys(words.en.rules).sort());
  });
});
