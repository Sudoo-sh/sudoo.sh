import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { CommitHash } from "./CommitHash";

// the words read the chosen language from the page when they load: no page here
vi.mock("@/i18n", () => ({
  useT: () => ({
    history: { copySha: "Copy the commit's hash", copied: "Copied", onGitHub: "Open the commit on GitHub" },
  }),
}));

const SHA = "f25e4f6a9b0c1d2e3f405162738495a6b7c8d9e0";
const links = (html: string) => [...html.matchAll(/<a\s[^>]*href="([^"]+)"[^>]*>/g)];

describe("CommitHash", () => {
  it("shows the seven-character short hash", () => {
    const html = renderToStaticMarkup(<CommitHash sha={SHA} repository="https://github.com/acme/erp" />);
    expect(html).toContain(">f25e4f6</code>");
    expect(html).not.toContain(`>${SHA}<`); // the whole hash is only in the link and in what a press copies
  });

  it("links to the commit on GitHub, in a new tab", () => {
    const html = renderToStaticMarkup(<CommitHash sha={SHA} repository="https://github.com/acme/erp" />);
    const found = links(html);
    expect(found).toHaveLength(1);
    expect(found[0]![1]).toBe(`https://github.com/acme/erp/commit/${SHA}`);
    expect(found[0]![0]).toContain('target="_blank"');
    expect(found[0]![0]).toContain('rel="noreferrer"');
  });

  it("has no link for a repository that is not on GitHub", () => {
    for (const repository of ["https://gitlab.com/acme/erp", "/srv/git/erp", null]) {
      const html = renderToStaticMarkup(<CommitHash sha={SHA} repository={repository} />);
      expect(links(html)).toHaveLength(0);
      expect(html).toContain(">f25e4f6</code>"); // still shown, and still copied on a press
    }
  });
});
