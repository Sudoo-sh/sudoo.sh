import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { Field } from "./basics";
import { SelectBox } from "./select";
import { Input } from "@/components/ui/input";

// the words read the chosen language from the page when they load: no page here, and Field has no words
vi.mock("@/i18n", () => ({ useT: () => ({}) }));

const labelFor = (html: string) => html.match(/<label[^>]*\sfor="([^"]+)"/)?.[1];
const idOf = (html: string, tag: string) => html.match(new RegExp(`<${tag}[^>]*\\sid="([^"]+)"`))?.[1];

describe("Field", () => {
  it("links its label to the one input inside", () => {
    const html = renderToStaticMarkup(<Field label="Instance"><Input /></Field>);
    expect(labelFor(html)).toBeTruthy();
    expect(labelFor(html)).toBe(idOf(html, "input"));
  });

  it("links its label to a list to choose from", () => {
    const html = renderToStaticMarkup(
      <Field label="User">
        <SelectBox value="" onValueChange={() => {}}>
          <option value="">Any</option>
        </SelectBox>
      </Field>,
    );
    expect(labelFor(html)).toBeTruthy();
    expect(labelFor(html)).toBe(idOf(html, "button"));
  });

  it("keeps an id and htmlFor given by the page", () => {
    expect(labelFor(renderToStaticMarkup(<Field label="Name"><Input id="name" /></Field>))).toBe("name");
    const html = renderToStaticMarkup(<Field label="Search" htmlFor="q"><div><Input id="q" /></div></Field>);
    expect(labelFor(html)).toBe("q");
  });

  it("leaves other content alone", () => {
    const html = renderToStaticMarkup(<Field label="Result"><div className="group" /></Field>);
    expect(labelFor(html)).toBeUndefined();
    expect(html).toContain('<div class="group"></div>');
  });
});
