import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AnimatedNumber, MorphIcon, Presence, Reveal, Stagger, SuccessCheck, TabInk } from "./motion";

describe("AnimatedNumber", () => {
  it("is drawn first at its starting value, in tabular figures", () => {
    expect(renderToStaticMarkup(<AnimatedNumber value={42} from={0} />)).toBe('<span class="num">0</span>');
  });

  it("shows the value at once without a starting value", () => {
    expect(renderToStaticMarkup(<AnimatedNumber value={7.25} format={(n) => n.toFixed(1)} />)).toBe('<span class="num">7.3</span>');
  });
});

describe("MorphIcon", () => {
  it("keeps every icon in one box and marks the one shown", () => {
    const html = renderToStaticMarkup(<MorphIcon icons={{ copy: <i>c</i>, done: <i>d</i> }} active="done" />);
    expect(html).toContain('aria-hidden="true"');
    expect(html.match(/morph-icon-item/g)).toHaveLength(2);
    expect(html).toMatch(/data-active="true"><i>d<\/i>/);
    expect(html).not.toMatch(/data-active="true"><i>c<\/i>/);
  });
});

describe("Reveal and Stagger", () => {
  it("delays the n-th item of a list, capped at eight steps", () => {
    expect(renderToStaticMarkup(<Reveal index={2}>x</Reveal>)).toContain("--reveal-delay:50ms");
    expect(renderToStaticMarkup(<Reveal index={40}>x</Reveal>)).toContain("--reveal-delay:175ms");
  });

  it("marks a list whose items come in one after the other", () => {
    expect(renderToStaticMarkup(<Stagger as="ul" className="list"><li>a</li></Stagger>)).toBe('<ul class="stagger-in list"><li>a</li></ul>');
  });
});

describe("Presence", () => {
  it("draws its children while shown, with the room they take when it collapses", () => {
    expect(renderToStaticMarkup(<Presence show>hi</Presence>)).toBe('<div class="presence" data-state="open">hi</div>');
    expect(renderToStaticMarkup(<Presence show collapse>hi</Presence>)).toContain('<div class="collapse-inner">hi</div>');
  });

  it("draws nothing when it starts hidden", () => {
    expect(renderToStaticMarkup(<Presence show={false}>hi</Presence>)).toBe("");
  });
});

describe("SuccessCheck and TabInk", () => {
  it("are decoration only", () => {
    expect(renderToStaticMarkup(<SuccessCheck />)).toContain('aria-hidden="true"');
    expect(renderToStaticMarkup(<TabInk />)).toBe('<span class="tab-ink" aria-hidden="true"></span>');
  });
});
