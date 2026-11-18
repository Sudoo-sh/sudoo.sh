// The marketplace as the demo shows it: a small catalogue of fictional plugins. Free ones install with a simulated
// job; buying is refused (it would open app.sudoo.sh to pay).

import type { CatalogPlugin, MarketplaceStatus, PricePlan, Recommendation } from "@/api/types";
import { plugins } from "./state";
import { DAY, iso, OPENED } from "./util";

const OFFICIAL = { name: "Sudoo.sh", slug: "sudoo", verified: true, official: true };
const VERIFIED = { name: "Example Labs", slug: "example-labs", verified: true, official: false };
const COMMUNITY = { name: "Sample Works", slug: "sample-works", verified: false, official: false };

const monthly = (id: string, cents: number): PricePlan => ({ id, kind: "monthly", price_cents: cents, currency: "usd", period_days: null, interval: "month", perpetual: false, monthly_equivalent_cents: cents, savings_percent: null });
const yearly = (id: string, cents: number, month: number): PricePlan => ({ id, kind: "yearly", price_cents: cents, currency: "usd", period_days: null, interval: "year", perpetual: false, monthly_equivalent_cents: Math.round(cents / 12), savings_percent: Math.round(100 - (100 * cents) / (month * 12)) });

type Entry = Omit<CatalogPlugin, "installed_version" | "license" | "action" | "compatible_version">;
const version = (v: string, ago: number) => ({ version: v, min_sudoo_version: "1.0.0", max_sudoo_version: null, published_at: iso(OPENED - ago * DAY) });

const ENTRIES: Entry[] = [
  { id: "com.sudoo.slack-alerts", name: "Chat alerts", description: "Alerts as rich messages in Slack, Discord or Teams, with buttons to the job.", type: "notification", tier: "free", tags: ["alerts", "slack", "teams"], avg_rating: 4.8, rating_count: 212, install_count: 3410, publisher: OFFICIAL, requires: [], plans: [], latest_version: version("1.4.0", 6) },
  { id: "com.sudoo.offsite-backups", name: "Off-site backups", description: "Copies every backup to S3-compatible storage, encrypted, with its own retention.", type: "backup", tier: "paid", tags: ["backups", "s3"], avg_rating: 4.9, rating_count: 96, install_count: 1288, publisher: OFFICIAL, requires: [], plans: [monthly("p-ob-m", 900), yearly("p-ob-y", 9000, 900)], latest_version: version("2.1.2", 21) },
  { id: "com.sudoo.oidc", name: "Single sign-on (OpenID Connect)", description: "Sign in to the panel with your company's identity provider.", type: "auth", tier: "paid", tags: ["sso", "oidc", "security"], avg_rating: 4.7, rating_count: 54, install_count: 612, publisher: OFFICIAL, requires: [], plans: [monthly("p-sso-m", 1500), yearly("p-sso-y", 15000, 1500)], latest_version: version("1.1.0", 34) },
  { id: "com.examplelabs.uptime-board", name: "Uptime board", description: "A public status page of your production instances, with response times.", type: "dashboard_widget", tier: "free", tags: ["status", "uptime"], avg_rating: 4.5, rating_count: 41, install_count: 905, publisher: VERIFIED, requires: [], plans: [], latest_version: version("0.9.3", 12) },
  { id: "com.examplelabs.gitlab-trigger", name: "GitLab updates", description: "New code from GitLab pushes and merge requests, like the GitHub App does for GitHub.", type: "update_trigger", tier: "free", tags: ["gitlab", "ci"], avg_rating: 4.3, rating_count: 27, install_count: 377, publisher: VERIFIED, requires: [], plans: [], latest_version: version("1.0.4", 45) },
  { id: "com.sampleworks.postmark", name: "Postmark mailer", description: "Sends the panel's e-mails through Postmark instead of SMTP.", type: "mailer", tier: "free", tags: ["email"], avg_rating: 4.1, rating_count: 12, install_count: 140, publisher: COMMUNITY, requires: [], plans: [], latest_version: version("0.4.1", 80) },
];

export function catalog(): CatalogPlugin[] {
  return ENTRIES.map((entry) => {
    const installed = plugins.find((p) => p.id === entry.id);
    const action = installed ? (installed.update_available ? "update" : "installed") : entry.tier === "paid" ? "buy" : "install";
    return {
      ...entry,
      compatible_version: entry.latest_version,
      installed_version: installed?.version ?? null,
      license: installed?.licence ? { state: "active", expires_at: installed.licence.expires_at, grace_until: null } : null,
      action,
    };
  });
}

export const categories = () => [...new Set(ENTRIES.map((e) => e.type))].map((type) => ({ type, count: ENTRIES.filter((e) => e.type === type).length }));

export function recommendations(): Recommendation[] {
  return catalog()
    .filter((p) => p.action === "install" || p.action === "buy")
    .slice(0, 3)
    .map((p, i) => {
      const { plans: _p, latest_version: _l, compatible_version: _c, installed_version: _i, license: _li, action: _a, ...plugin } = p;
      void [_p, _l, _c, _i, _li, _a];
      return { plugin, score: 0.9 - i * 0.1, reasons: [i === 0 ? "Because you run three production instances" : "Popular with Odoo integrators"] };
    });
}

export const marketplace: MarketplaceStatus = {
  enabled: true,
  hub_url: "https://hub.sudoo.sh",
  installation_id: "inst_demo",
  registered: true,
  reachable: true,
  error: null,
  error_code: null,
  hub_status: "active",
  hub_keys: ["demo-key"],
  flags: [],
  linked: { account_name: "Example Integrator", linked_at: iso(OPENED - 70 * DAY) },
  key_revoked_at: null,
  removing_paid_plugins: false,
  last_heartbeat_at: iso(OPENED - 4 * 60_000),
  next_heartbeat_at: iso(OPENED + 56 * 60_000),
  lease_expires_at: iso(OPENED + 6 * DAY),
  lease_valid: true,
  lease_ending_soon: false,
};
