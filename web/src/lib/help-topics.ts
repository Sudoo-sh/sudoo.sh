// Which help topic each page of the panel shows in the help panel, and where its documentation is. The texts are
// in i18n/help.ts (one per topic, English and Arabic); the documentation is the repository's own docs, shown in the
// panel under /help/<path> and on the documentation site. tests/test_mail_catcher.py checks every page and anchor
// here against docs/ (keep each entry as ["<path>", "<anchor>"] on one line).

export type Topic =
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
  | "backups"
  | "audit"
  | "github"
  | "account"
  | "help"
  | "other";

export const TOPIC_DOCS: Record<Topic, [string, string]> = {
  instances: ["docs/USER_MANUAL.md", "2-the-instances-page"],
  newInstance: ["docs/USER_MANUAL.md", "4-creating-an-instance"],
  overview: ["docs/USER_MANUAL.md", "3-an-instances-page"],
  activity: ["docs/USER_MANUAL.md", "tabs"],
  code: ["docs/USER_MANUAL.md", "5-keeping-the-code-up-to-date"],
  logs: ["docs/USER_MANUAL.md", "13-when-something-goes-wrong"],
  domains: ["docs/USER_MANUAL.md", "custom-domains"],
  instanceSettings: ["docs/USER_MANUAL.md", "stages-production-staging-and-development"],
  access: ["docs/USER_MANUAL.md", "tabs"],
  shell: ["docs/USER_MANUAL.md", "the-shell-tab"],
  mail: ["docs/USER_MANUAL.md", "the-mail-tab"],
  settings: ["docs/ADMIN_GUIDE.md", "5-settings"],
  emailTemplates: ["docs/ADMIN_GUIDE.md", "17-e-mail-templates"],
  plugins: ["docs/MARKETPLACE.md", "3-the-plugins-page"],
  accounts: ["docs/USER_MANUAL.md", "8-accounts-and-roles"],
  tokens: ["docs/USER_MANUAL.md", "api-tokens"],
  backups: ["docs/USER_MANUAL.md", "10-backups-and-audit-log"],
  audit: ["docs/USER_MANUAL.md", "audit-log"],
  github: ["docs/USER_MANUAL.md", "7-github-access-private-repositories"],
  account: ["docs/USER_MANUAL.md", "9-my-account"],
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
  return PAGES[parts[0] ?? ""] ?? "other";
}

/** The documentation site's address of a page of docs/ (README.md is its overview, docs/README.md its start). */
export function siteAddress(base: string, path: string, anchor: string): string {
  const slug =
    path === "README.md"
      ? "overview"
      : path === "docs/README.md"
        ? "start"
        : path.replace(/^docs\//, "").replace(/\.md$/, "").split("/").map((part, i, all) =>
            i === all.length - 1 ? part.toLowerCase().replace(/_/g, "-") : part,
          ).join("/");
  const top = anchor === slug.split("/").pop() ? "" : `#${anchor}`;
  return `${base.replace(/\/$/, "")}/${slug}${top}`;
}
