// The module picker in the demo: the standard Odoo apps (GET /odoo-apps, the same list as
// src/sudoo/instances/odoo_apps.py), the panel's check of a "modules" list sent when creating an instance, and a
// branch's modules for API users (GET /repositories/modules) made from the demo repositories.

import type { OdooApp, RepositoryModule, RepositoryModules } from "@/api/types";
import { findRepo, setting } from "./state";
import { hash, Problem } from "./util";

type App = OdooApp & { since?: number; until?: number };

const APPS: App[] = [
  ["sale_management", "Sales"], ["account", "Invoicing"], ["crm", "CRM"], ["stock", "Inventory"], ["purchase", "Purchase"],
  ["mrp", "Manufacturing"], ["point_of_sale", "Point of Sale"], ["website", "Website"], ["website_sale", "eCommerce"],
  ["website_blog", "Blog"], ["website_forum", "Forum"], ["website_slides", "eLearning"], ["project", "Project"],
  ["hr_timesheet", "Timesheets"], ["hr", "Employees"], ["hr_holidays", "Time Off"], ["hr_attendance", "Attendances"],
  ["hr_expense", "Expenses"], ["hr_recruitment", "Recruitment"], ["hr_skills", "Skills Management"], ["mail", "Discuss"],
  ["calendar", "Calendar"], ["contacts", "Contacts"], ["im_livechat", "Live Chat"], ["mass_mailing", "Email Marketing"],
  ["event", "Events"], ["survey", "Surveys"], ["fleet", "Fleet"], ["maintenance", "Maintenance"], ["repair", "Repairs"],
  ["lunch", "Lunch"],
].map(([name, title]) => ({ name: name!, title: title!, enterprise: false }) as App).concat([
  { name: "note", title: "Notes", enterprise: false, until: 16 },
  { name: "project_todo", title: "To-do", enterprise: false, since: 17 },
  { name: "spreadsheet_dashboard", title: "Dashboards", enterprise: false, since: 16 },
  ...[
    ["accountant", "Accounting"], ["helpdesk", "Helpdesk"], ["planning", "Planning"], ["timesheet_grid", "Timesheets (Enterprise)"],
    ["industry_fsm", "Field Service"], ["sale_subscription", "Subscriptions"], ["sale_renting", "Rental"], ["documents", "Documents"],
    ["sign", "Sign"],
  ].map(([name, title]) => ({ name: name!, title: title!, enterprise: true })),
  { name: "knowledge", title: "Knowledge", enterprise: true, since: 16 },
  { name: "approvals", title: "Approvals", enterprise: true },
  { name: "appointment", title: "Appointments", enterprise: true, since: 16 },
  { name: "website_calendar", title: "Appointments", enterprise: true, until: 15 },
  ...[
    ["quality_control", "Quality"], ["mrp_plm", "PLM"], ["stock_barcode", "Barcode"], ["hr_payroll", "Payroll"],
    ["hr_appraisal", "Appraisals"], ["hr_referral", "Referrals"], ["marketing_automation", "Marketing Automation"],
    ["social", "Social Marketing"], ["voip", "VoIP"],
  ].map(([name, title]) => ({ name: name!, title: title!, enterprise: true })),
  { name: "frontdesk", title: "Frontdesk", enterprise: true, since: 17 },
  { name: "web_studio", title: "Studio", enterprise: true },
]);

export const maxModules = () => Number(setting("max_initial_modules") ?? 200);

export function odooApps(version: string, edition: string): OdooApp[] {
  const major = Number(/(\d+)\.\d+/.exec(version)?.[1] ?? 0);
  return APPS.filter((a) => (edition === "enterprise" || !a.enterprise) && (!major || ((!a.since || major >= a.since) && (!a.until || major <= a.until))))
    .map(({ name, title, enterprise }) => ({ name, title, enterprise }));
}

const titled = (name: string) => name.split("_").map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");

// the add-ons the demo repositories take from other repositories (their submodules)
const SUBMODULES: Record<string, Record<string, string[]>> = {
  "acme-retail": { "oca/pos": ["pos_order_mgmt", "pos_payment_method_cashdro", "pos_lot_selection"], "oca/web": ["web_responsive", "web_environment_ribbon"] },
  "nile-logistics": { "oca/fleet": ["fleet_vehicle_inspection", "fleet_vehicle_service_kanban"] },
};

export function repositoryModules(url: string, branch: string): RepositoryModules {
  if (!/github\.com[/:][\w.-]+\/[\w.-]+/.test(url)) throw new Problem(400, "This is not a GitHub repository address. Example: https://github.com/company/repository");
  const repo = findRepo(url);
  if (repo && !repo.branches.includes(branch)) throw new Problem(404, `The repository has no branch ${branch} on GitHub.`);
  const names = repo?.modules ?? ["custom_sale", "custom_stock", "custom_reports"];
  const own: RepositoryModule[] = names.map((name, i) => ({
    name,
    title: titled(name),
    summary: "",
    application: i === 0,
    path: `addons/${name}`,
    submodule: null,
  }));
  const subs: RepositoryModule[] = Object.entries(SUBMODULES[repo?.name ?? ""] ?? {}).flatMap(([path, modules]) =>
    modules.map((name) => ({ name, title: titled(name), summary: "", application: false, path: `${path}/${name}`, submodule: path })),
  );
  return {
    repository: url.replace(/^(https:\/\/github\.com\/|git@github\.com:)/, "").replace(/\.git$/, ""),
    branch,
    commit: hash(`${url}@${branch}`).toString(16).padStart(8, "0").repeat(5).slice(0, 40),
    modules: [...own, ...subs],
    problems: [],
  };
}

/** The panel's check of "modules" (web/validation.py initial_modules): null when it is not sent. */
export function checkModules(value: unknown): string[] | null {
  if (value === undefined || value === null) return null;
  if (!Array.isArray(value) || !value.every((v) => typeof v === "string"))
    throw new Problem(400, "The field 'modules' must be a list of technical module names, for example [\"sale_management\"].");
  const names = [...new Set(value.map((v: string) => v.trim()).filter(Boolean))];
  const wrong = names.find((n) => !/^[a-z0-9_]{1,64}$/.test(n));
  if (wrong) throw new Problem(400, `'${wrong.slice(0, 64)}' is not a technical module name: lower-case letters, digits and _, for example sale_custom.`);
  if (names.length > maxModules()) throw new Problem(400, `Choose at most ${maxModules()} modules (Odoo installs their dependencies itself).`);
  return names;
}
