// Which help topic each page of the panel shows in the help panel, and where its documentation is. The texts are
// in i18n/help.ts (one per topic, English and Arabic); the documentation is the repository's own docs, shown in the
// panel under /help/<path> and on the documentation site. tests/test_mail_catcher.py checks every page and anchor
// here against docs/ (keep each entry as ["<path>", "<anchor>"] on one line).

export type Topic =
  | "projects"
  | "project"
  | "newProject"
  | "instances"
  | "newInstance"
  | "overview"
  | "activity"
  | "code"
  | "logs"
  | "domains"
  | "instanceSettings"
  | "access"
  | "shell"
  | "mail"
  | "settings"
  | "emailTemplates"
  | "plugins"
  | "accounts"
  | "tokens"
  | "teams"
  | "projectAccess"
  | "backups"
  | "audit"
  | "github"
  | "account"
  | "help"
  | "other";

export const TOPIC_DOCS: Record<Topic, [string, string]> = {
  projects: ["docs/USER_MANUAL.md", "the-projects-page"],
  project: ["docs/USER_MANUAL.md", "a-projects-page"],
  newProject: ["docs/USER_MANUAL.md", "creating-a-project"],
  instances: ["docs/USER_MANUAL.md", "3-the-instances-page"],
  newInstance: ["docs/USER_MANUAL.md", "5-creating-an-instance"],
  overview: ["docs/USER_MANUAL.md", "4-an-instances-page"],
  activity: ["docs/USER_MANUAL.md", "tabs"],
  code: ["docs/USER_MANUAL.md", "6-keeping-the-code-up-to-date"],
  logs: ["docs/USER_MANUAL.md", "14-when-something-goes-wrong"],
  domains: ["docs/USER_MANUAL.md", "custom-domains"],
  instanceSettings: ["docs/USER_MANUAL.md", "stages-production-staging-and-development"],
  access: ["docs/USER_MANUAL.md", "tabs"],
  shell: ["docs/USER_MANUAL.md", "the-shell-tab"],
  mail: ["docs/USER_MANUAL.md", "the-mail-tab"],
  settings: ["docs/ADMIN_GUIDE.md", "5-settings"],
  emailTemplates: ["docs/ADMIN_GUIDE.md", "17-e-mail-templates"],
  plugins: ["docs/MARKETPLACE.md", "3-the-plugins-page"],
  accounts: ["docs/USER_MANUAL.md", "9-accounts-and-roles"],
  tokens: ["docs/USER_MANUAL.md", "api-tokens"],
  teams: ["docs/USER_MANUAL.md", "teams"],
  projectAccess: ["docs/USER_MANUAL.md", "project-access-and-locked-branches"],
  backups: ["docs/USER_MANUAL.md", "11-backups-and-audit-log"],
  audit: ["docs/USER_MANUAL.md", "audit-log"],
  github: ["docs/USER_MANUAL.md", "8-github-access-private-repositories"],
  account: ["docs/USER_MANUAL.md", "10-my-account"],
  help: ["docs/USER_MANUAL.md", "the-help-button"],
  other: ["docs/USER_MANUAL.md", "user-manual"],
};

const PAGES: Record<string, Topic> = {
  "": "instances",
  new: "newInstance",
  settings: "settings",
  "email-templates": "emailTemplates",
  plugins: "plugins",
  accounts: "accounts",
  tokens: "tokens",
  teams: "teams",
  backups: "backups",
  activity: "audit",
  github: "github",
  account: "account",
  help: "help",
};

const INSTANCE_TABS: Record<string, Topic> = {
  "": "overview",
  activity: "activity",
  code: "code",
  logs: "logs",
  domains: "domains",
  settings: "instanceSettings",
  access: "access",
  shell: "shell",
  mail: "mail",
};

/** The topic of a page, from its path: /i/<name>/<tab> by the tab, the others by their first part. */
export function topicOf(pathname: string): Topic {
  const parts = pathname.split("/").filter(Boolean);
  if (parts[0] === "i") return INSTANCE_TABS[parts[2] ?? ""] ?? "overview";
  if (parts[0] === "projects")
    return parts[1] === "new"
      ? "newProject"
      : parts[2] === "access"
        ? "projectAccess"
        : parts[1]
          ? "project"
          : "projects";
  return PAGES[parts[0] ?? ""] ?? "other";
}

/** The documentation site's address of a page of docs/ (README.md is its overview, docs/README.md its start). */
export function siteAddress(base: string, path: string, anchor: string): string {
  const slug =
    path === "README.md"
      ? "overview"
      : path === "docs/README.md"
        ? "start"
        : path
            .replace(/^docs\//, "")
            .replace(/\.md$/, "")
            .split("/")
            .map((part, i, all) => (i === all.length - 1 ? part.toLowerCase().replace(/_/g, "-") : part))
            .join("/");
  const top = anchor === slug.split("/").pop() ? "" : `#${anchor}`;
  return `${base.replace(/\/$/, "")}/${slug}${top}`;
}
