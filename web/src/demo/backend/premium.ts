// The demo's Sudoo.sh plan and its premium features (src/sudoo/entitlements.py, src/sudoo/premium): which plan the
// panel has (Free, Pro or Business, chosen in the demo's banner and kept in the browser), what that plan allows
// (GET /plan), which premium packages run (GET /premium), and the 402 answers of the API paths of a package that does
// not run, with the sentence the panel writes. A package runs when the plan includes it and the demo was built with
// its compiled web files (vite.config.ts, demoPremium); without those files every package shows its plan card and
// the plan stays Free, so each page agrees with the plan.

import bundled from "virtual:sudoo-demo-premium";
import type { PlanStatus } from "@/api/types";
import type { PremiumId, PremiumOverview, PremiumPackage } from "@/premium/types";
import { DAY, OPENED, Problem } from "./util";

export type DemoPlan = "free" | "pro" | "business";
export const PLANS: DemoPlan[] = ["free", "pro", "business"];

/** The premium packages as src/sudoo/premium/__init__.py lists them, with the lowest plan that includes each. */
export const PACKAGES: { id: PremiumId; name: string; features: string[]; plans: string; from: DemoPlan }[] = [
  { id: "teams", name: "Teams and access per project", features: ["teams", "per_project_access"], plans: "Pro and Business", from: "pro" },
  { id: "domains", name: "Custom domains", features: ["custom_domains"], plans: "Business", from: "business" },
  { id: "offsite", name: "Off-site backups", features: ["offsite_backups"], plans: "Pro and Business", from: "pro" },
  { id: "support", name: "Support access", features: ["support_access"], plans: "Pro and Business", from: "pro" },
  { id: "servers", name: "Several servers", features: ["multi_server"], plans: "Business", from: "business" },
];

type Entitlements = PlanStatus["entitlements"];

/** What each plan allows (the hub's signed licence; Free is the licence's own, src/sudoo/entitlements.py FREE). */
export const ENTITLEMENTS: Record<DemoPlan, Entitlements> = {
  free: {
    max_production_branches: 1,
    max_servers: 1,
    max_projects: null,
    teams: false,
    features: { per_project_access: false, custom_domains: false, offsite_backups: false },
    support_level: "community",
    addons: {},
  },
  pro: {
    max_production_branches: 5,
    max_servers: 1,
    max_projects: null,
    teams: true,
    features: { per_project_access: true, custom_domains: false, offsite_backups: true },
    support_level: "email",
    addons: {},
    period: "year",
    trial_plan: null,
  },
  business: {
    max_production_branches: 20,
    max_servers: 5,
    max_projects: null,
    teams: true,
    features: { per_project_access: true, custom_domains: true, offsite_backups: true },
    support_level: "priority",
    addons: {},
    period: "year",
    trial_plan: null,
  },
};

const STORE = "sudoo.demo.plan";
let files: typeof bundled = bundled;
let plan: DemoPlan = "free";
const listeners = new Set<() => void>();

/** The packages whose compiled web files the demo was built with. */
export const bundledIds = (): PremiumId[] => PACKAGES.filter((p) => files[p.id]).map((p) => p.id);
export const hasPremiumFiles = () => bundledIds().length > 0;

/** The plan the demo shows: the visitor's last choice, else Business (Free when the build has no premium files). */
export function loadPlan(): DemoPlan {
  let stored: string | null = null;
  try {
    stored = localStorage.getItem(STORE);
  } catch {
    /* no storage (private window): the default plan */
  }
  plan = !hasPremiumFiles() ? "free" : PLANS.includes(stored as DemoPlan) ? (stored as DemoPlan) : "business";
  return plan;
}

export const currentPlan = () => plan;

/** Choose the plan (the demo's banner); every page asks again afterwards. */
export function setPlan(next: DemoPlan) {
  if (!PLANS.includes(next) || !hasPremiumFiles()) return;
  plan = next;
  try {
    localStorage.setItem(STORE, next);
  } catch {
    /* not kept across visits */
  }
  for (const listener of listeners) listener();
}

/** Called after every change of plan (the servers move their instances back to the main server and out again). */
export function onPlanChange(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** For the tests: the premium files the demo pretends to have been built with. */
export function setFilesForTests(next: typeof bundled) {
  files = next;
}

const rank = (p: DemoPlan) => PLANS.indexOf(p);
const packageOf = (id: PremiumId) => PACKAGES.find((p) => p.id === id)!;

/** Whether the plan includes a package (whatever the build has). */
export const included = (id: PremiumId, of: DemoPlan = plan) => rank(of) >= rank(packageOf(id).from);

/** Whether a package runs: the plan includes it and its web files are there. */
export const premiumActive = (id: PremiumId) => included(id) && !!files[id];

/** The sentence of src/sudoo/premium/__init__.py refusal() for a package that does not run. */
export function refusal(id: PremiumId): string {
  const pkg = packageOf(id);
  return `${pkg.name} is part of the ${pkg.plans} plans; it installs automatically from Sudoo.sh once your plan includes it. See the plans at https://sudoo.sh/pricing.`;
}

/** 402 when the package does not run, as the panel's stubs answer (src/sudoo/web/v1/premium.py). */
export function needPremium(id: PremiumId) {
  if (!premiumActive(id)) throw new Problem(402, refusal(id));
}

// the API paths each premium package answers (STUB_PATHS of src/sudoo/web/v1/premium.py, and the teams package's
// making and changing of teams and of project access): 402 while the package does not run
const GATED: { id: PremiumId; method: RegExp; path: RegExp }[] = [
  { id: "domains", method: /^(POST|PATCH)$/, path: /^\/instances\/[^/]+\/domains(\/[^/]+(\/check)?)?$/ },
  { id: "offsite", method: /./, path: /^\/offsite(\/.*)?$/ },
  { id: "support", method: /./, path: /^\/support-access(\/.*)?$/ },
  { id: "servers", method: /./, path: /^\/servers(\/.*)?$/ },
  { id: "servers", method: /^POST$/, path: /^\/instances\/[^/]+\/move$/ },
  { id: "teams", method: /^(POST|PATCH)$/, path: /^\/teams(\/[^/]+)?$/ },
  { id: "teams", method: /^PUT$/, path: /^\/projects\/[^/]+\/access$/ },
];

/** The package an API call belongs to, when it belongs to one. */
export function gatedBy(method: string, path: string): PremiumId | null {
  const found = GATED.find((g) => g.method.test(method) && g.path.test(path));
  return found?.id ?? null;
}

/** GET /premium. `details` for those who manage settings (problems, binding). */
export function premiumOverview(details: boolean): PremiumOverview {
  const installedAt = Math.floor((OPENED - 12 * DAY) / 1000);
  return {
    packages: PACKAGES.map(({ id, name, features, plans }): PremiumPackage => {
      const ui = files[id];
      const active = premiumActive(id);
      return {
        id,
        name,
        features,
        plans,
        included: included(id) && !!ui,
        status: active ? "active" : "not_included",
        version: active ? (ui!.version ?? "1.0.0") : null,
        installed_at: active ? installedAt : null,
        ...(details ? { problem: null, binding_until: active ? Math.floor((OPENED + 25 * DAY) / 1000) : null } : {}),
        ...(active ? { ui: { routes: ui!.routes, sections: ui!.sections, styles: ui!.styles, strings: ui!.strings, files: ui!.files } } : {}),
      };
    }),
    auto_install: true,
    marketplace: true,
    last_checked_at: Math.floor(OPENED / 1000),
    last_error: null,
    job: null,
    build: { python_tag: "cp315", platform: "linux_x86_64" },
  };
}

loadPlan();
