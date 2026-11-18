// The Help pages of the demo: a short guide to what can be tried here. The real panel serves its full manual from
// the repository's docs/; the demo links to it on sudoo.sh instead of copying it.

import type { DocPage, DocsContents } from "@/api/types";
import { PERMISSIONS, ROLES } from "./state";

export const DOCS: DocsContents = {
  index: "demo/README.md",
  sections: [
    {
      title: "This demo",
      pages: [
        { path: "demo/README.md", title: "Start here" },
        { path: "demo/ROLES.md", title: "Roles and permissions" },
        { path: "demo/STAGES.md", title: "Stages" },
      ],
    },
  ],
};

const h = (level: number, id: string, text: string) => `<h${level} id="${id}">${text}</h${level}>`;

const PAGES: Record<string, () => DocPage> = {
  "demo/README.md": () => ({
    path: "demo/README.md",
    title: "Start here",
    headings: [
      { level: 2, id: "what-you-are-looking-at", text: "What you are looking at" },
      { level: 2, id: "things-to-try", text: "Things to try" },
      { level: 2, id: "what-the-demo-cannot-do", text: "What the demo cannot do" },
    ],
    html: [
      h(1, "start-here", "Start here"),
      "<p>This is the Sudoo.sh panel with sample data: an Odoo integrator who runs three client projects on one server. Every figure, instance and person here is fictional, and nothing you do leaves this browser tab.</p>",
      h(2, "what-you-are-looking-at", "What you are looking at"),
      "<p>Each client project has a GitHub repository. Every branch you want to see running becomes an <b>instance</b>: its own Odoo, database and public link. The <code>main</code> branches run in <b>production</b>; staging and feature branches come and go.</p>",
      h(2, "things-to-try", "Things to try"),
      "<ul><li>Open an instance from the Instances page and watch its charts move.</li><li>Create an instance under <b>New instance</b>: pick <code>example-integrator/cedar-hotels</code> and follow its creation step by step.</li><li>Restart, pause, back up or restore an instance; each runs as a job with its own log.</li><li>Switch the role in the banner at the top to see what a developer or a viewer can and cannot do.</li></ul>",
      h(2, "what-the-demo-cannot-do", "What the demo cannot do"),
      "<p>There is no Odoo behind the instances, so their links do not open. Downloads, GitHub, payments and e-mails are switched off. Reload the page to start over.</p>",
      "<p>Ready to run it for real? <a href=\"https://sudoo.sh\">Install Sudoo.sh on your own server</a>.</p>",
    ].join("\n"),
  }),
  "demo/ROLES.md": () => ({
    path: "demo/ROLES.md",
    title: "Roles and permissions",
    headings: ROLES.map((r) => ({ level: 2, id: r.name.replace("_", "-"), text: r.label })),
    html: [
      h(1, "roles-and-permissions", "Roles and permissions"),
      "<p>Every account has one role, and a role is a set of permissions. The panel hides what a role cannot do, and the server checks every request again.</p>",
      ...ROLES.map((r) => `${h(2, r.name.replace("_", "-"), r.label)}<p>${r.description}</p><ul>${r.permissions.map((p) => `<li>${PERMISSIONS[p]}</li>`).join("")}</ul>`),
    ].join("\n"),
  }),
  "demo/STAGES.md": () => ({
    path: "demo/STAGES.md",
    title: "Stages",
    headings: [
      { level: 2, id: "development", text: "Development" },
      { level: 2, id: "staging", text: "Staging" },
      { level: 2, id: "production", text: "Production" },
    ],
    html: [
      h(1, "stages", "Stages"),
      "<p>Every instance has a stage, which decides how the panel treats it.</p>",
      h(2, "development", "Development"),
      "<p>Paused when nobody uses it, and deleted after its lifetime (30 days unless changed). New code arrives by itself.</p>",
      h(2, "staging", "Staging"),
      "<p>Like development, with a longer lifetime, for the client to test before a release.</p>",
      h(2, "production", "Production"),
      "<p>Never paused or deleted automatically, backed up every day, and every update backs it up first and is undone if it fails. Pausing, restoring and deleting ask for a confirmation.</p>",
    ].join("\n"),
  }),
};

export const docPage = (path: string): DocPage | null => PAGES[path]?.() ?? null;
