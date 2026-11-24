import { readFileSync, writeFileSync } from "node:fs";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import titles from "@/i18n/job-titles.json";
import { jobTitle, stepTitle } from "@/i18n/jobs";

// job-titles.json is every job and step title the server writes (tests/test_job_titles.py keeps it equal to the
// server's source); a value inside a title is {} and a count is {#}. Each must have Arabic in the panel.
const ARABIC = /[؀-ۿ]/;
const VALUE = "my-value_1.2";
const fill = (title: string) => title.replaceAll("{#}", "7").replaceAll("{}", VALUE);

const original = Object.getOwnPropertyDescriptor(globalThis, "document");
let lang = "ar";
beforeAll(() => {
  Object.defineProperty(globalThis, "document", {
    value: {
      documentElement: {
        get lang() {
          return lang;
        },
      },
    },
    configurable: true,
  });
});
afterAll(() => {
  if (original) Object.defineProperty(globalThis, "document", original);
  else delete (globalThis as { document?: unknown }).document;
});

describe.each([
  ["job", titles.jobs, jobTitle],
  ["step", titles.steps, stepTitle],
] as const)("Arabic %s titles", (_kind, list, show) => {
  it.each(list.map((title) => [title]))("%s", (title) => {
    const english = fill(title);
    lang = "ar";
    const arabic = show(english);
    expect(arabic, `no Arabic for "${title}"`).not.toBe(english);
    expect(arabic).toMatch(ARABIC);
    // every value of the title is kept, isolated so it stays left to right
    if (title.includes("{}")) expect(arabic).toContain(VALUE);
    // no English word of the title is left over (names and values aside)
    const left = arabic.replaceAll(VALUE, "").match(/(?<![\w.@/:`-])[A-Za-z]{3,}(?![\w.@/:`-])/g) ?? [];
    expect(left.filter((word) => !KEPT.has(word))).toEqual([]);
  });

  it("is left as the server wrote it in English", () => {
    lang = "en";
    for (const title of list) expect(show(fill(title))).toBe(fill(title));
  });
});

/** Names of products that stay in English inside the Arabic title. */
const KEPT = new Set(["Odoo", "GitHub", "PostgreSQL", "OpenUpgrade", "Enterprise", "Community", "Sudoo"]);

describe("Arabic titles of a value that is not a name", () => {
  it("keeps a branch with a slash and a version left to right", () => {
    lang = "ar";
    expect(stepTitle("Switching to Odoo 19.0 and branch feature/a-b")).toContain("⁦feature/a-b⁩");
  });
});

// tests/job_titles_ar.json: the Arabic of a sample of every title; the gateway's pages (instances/job_titles_ar.py)
// are tested against the same file in Python. UPDATE_JOB_TITLES=1 writes it again from this module.
describe("the sample the gateway's pages are tested against", () => {
  const FILE = new URL("../../../tests/job_titles_ar.json", import.meta.url);
  const known: Record<string, string> = JSON.parse(readFileSync(FILE, "utf8"));
  it("holds the Arabic this module gives", () => {
    lang = "ar";
    if (!process.env.UPDATE_JOB_TITLES)
      for (const title of [...titles.jobs, ...titles.steps]) expect(known).toHaveProperty([fill(title)]);
    const keys = [...new Set([...Object.keys(known), ...titles.jobs, ...titles.steps].map(fill))].sort();
    const now = Object.fromEntries(keys.map((english) => [english, stepTitle(english)]));
    if (process.env.UPDATE_JOB_TITLES) writeFileSync(FILE, `${JSON.stringify(now, null, 2)}\n`);
    else expect(now).toEqual(known);
  });
});
