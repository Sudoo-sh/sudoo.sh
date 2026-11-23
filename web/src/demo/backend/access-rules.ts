// The demo's production access rules (beta), as src/sudoo/instances/access_rules.py and access_gate.py answer:
// sample rules on the production instances, a preview computed the same way (which paths are back office, who gets
// in), and a confirmed change (the instance's name and a code; in the demo the code is shown where it was "sent").

import type { AccessPreview, AccessRules, AccessRulesState, CodeMethod, Network, Outcome } from "@/api/access-rules";
import { iso, MINUTE, OPENED, Problem } from "./util";

const PRESETS: Record<string, string[]> = {
  payments: ["/payment/"],
  webhooks: ["/web/hook/"],
  portal: ["/web/login", "/web/signup", "/web/reset_password", "/my"],
  livechat: ["/websocket", "/longpolling/", "/bus/", "/im_livechat/"],
  mail_links: ["/mail/", "/mailing/"],
};
const BACK_OFFICE = ["/web", "/odoo", "/json", "/xmlrpc", "/jsonrpc", "/websocket", "/longpolling", "/bus", "/report", "/doc", "/__sudoo/connect"];
const PUBLIC_UNDER_WEB = [
  "/web/assets/", "/web/bundle/", "/web/static/", "/web/image", "/web/content", "/web/binary/company_logo",
  "/web/webclient/translations", "/web/webclient/locale", "/web/health",
];
const SAMPLES = [
  "/odoo", "/web/login", "/web/database/manager", "/json/2/res.partner/search_read", "/xmlrpc/2/object", "/websocket", "/",
  "/shop", "/my", "/payment/stripe/webhook", "/web/hook/2f6c1d0e", "/web/image/product.template/1/image_512",
];
export const YOU = "203.0.113.24";
const DEMO_CODE = "246810";

/** The demo's country table: a few documentation ranges stand for countries. */
function countryOf(address: string): string | null {
  if (address.startsWith("203.0.113.")) return "PS";
  if (address.startsWith("198.51.100.")) return "JO";
  if (address.startsWith("2001:db8:")) return "DE";
  if (address.startsWith("192.0.2.")) return null;
  return /^\d+\.\d+\.\d+\.\d+$/.test(address) ? "US" : null;
}

function inNetwork(address: string, network: string): boolean {
  const [base, bits = address.includes(":") ? "128" : "32"] = network.split("/");
  if (address.includes(":") || base!.includes(":")) return address.toLowerCase().startsWith(base!.toLowerCase().replace(/::$/, ":"));
  const toInt = (ip: string) => ip.split(".").reduce((n, part) => n * 256 + Number(part), 0);
  const mask = Number(bits) === 0 ? 0 : 2 ** 32 - 2 ** (32 - Number(bits));
  return (toInt(address) & mask) >>> 0 === (toInt(base!) & mask) >>> 0;
}

const matches = (path: string, entry: string) => (entry.endsWith("/") ? path.startsWith(entry) : path === entry || path.startsWith(`${entry}/`));
const backOffice = (path: string) =>
  !(path.startsWith("/web") && PUBLIC_UNDER_WEB.some((e) => matches(path, e))) && BACK_OFFICE.some((e) => matches(path, e));

function decide(rules: AccessRules, address: string | null, path: string): { allowed: boolean; why: string } {
  if (rules.scope === "back_office" && !backOffice(path)) return { allowed: true, why: "public" };
  const paths = [...rules.exceptions.presets.flatMap((p) => PRESETS[p] ?? []), ...rules.exceptions.paths];
  if (paths.some((e) => matches(path, e))) return { allowed: true, why: "exception" };
  if (address && rules.exceptions.networks.some((n) => inNetwork(address, n.value))) return { allowed: true, why: "exception" };
  if (rules.mode !== "countries" && address && rules.networks.some((n) => inNetwork(address, n.value))) return { allowed: true, why: "address" };
  const country = address ? countryOf(address) : null;
  if (rules.mode !== "ips" && country && rules.countries.includes(country)) return { allowed: true, why: "country" };
  return { allowed: false, why: "blocked" };
}

const rulesOf = new Map<string, AccessRules>();
const sent = new Map<string, number>();

const net = (value: string, label: string): Network => ({ value, label });

export function seedAccessRules() {
  rulesOf.clear();
  rulesOf.set("acme-retail-main", {
    enabled: true, mode: "either", scope: "back_office", countries: ["JO", "PS"],
    networks: [net("203.0.113.0/24", "Ramallah office"), net("2001:db8:4f::/48", "Company VPN")],
    exceptions: { presets: ["payments", "webhooks"], paths: [], networks: [net("198.51.100.77", "Uptime monitor")] },
    version: 3, changed_by: "omar", changed_at: iso(OPENED - 26 * 60 * MINUTE),
  });
  rulesOf.set("zahra-clinic-main", {
    enabled: true, mode: "ips", scope: "site", countries: [],
    networks: [net("198.51.100.0/24", "Clinic network")],
    exceptions: { presets: ["payments"], paths: ["/appointment/confirm/"], networks: [] },
    version: 1, changed_by: "sara", changed_at: iso(OPENED - 4 * 24 * 60 * MINUTE),
  });
}
seedAccessRules();

const empty = (): AccessRules => ({
  enabled: false, mode: "countries", scope: "back_office", countries: [], networks: [],
  exceptions: { presets: ["payments", "webhooks"], paths: [], networks: [] }, version: 0, changed_by: null, changed_at: null,
});

export function accessState(name: string, production: boolean, canChange: boolean): AccessRulesState {
  const rules = rulesOf.get(name) ?? empty();
  return {
    available: true,
    production,
    active: production && rules.enabled,
    rules,
    can_change: canChange,
    channels: canChange ? ["email", "app"] : [],
    geo: {
      ready: true, month: "2026-11", built_at: Math.floor((OPENED - 3 * 24 * 60 * MINUTE) / 1000), ranges: 612_480,
      problem: null, last_attempt: null, last_error: null, attribution: "IP geolocation by DB-IP (https://db-ip.com), licensed under CC BY 4.0",
    },
    geo_missing: "open",
    you: { address: YOU, country: countryOf(YOU) },
    presets: PRESETS as AccessRulesState["presets"],
    back_office: BACK_OFFICE,
    public_under_web: PUBLIC_UNDER_WEB,
  };
}

/** The same checks as access_rules.clean(), in short. */
export function cleanRules(raw: unknown): AccessRules {
  const rules = raw as AccessRules;
  if (!rules || typeof rules !== "object") throw new Problem(400, "Send the rules as an object.");
  const networks = [...(rules.networks ?? []), ...(rules.exceptions?.networks ?? [])];
  for (const n of networks) {
    const value = n.value.trim();
    if (!/^[0-9a-fA-F:.]+(\/\d{1,3})?$/.test(value))
      throw new Problem(400, `"${value}" is not an address or a range. Use an address such as 203.0.113.7 or 2001:db8::7, or a range such as 203.0.113.0/24.`);
    if (/\/0$/.test(value)) throw new Problem(400, `"${value}" is every address: it would allow everyone. Remove it.`);
  }
  for (const p of rules.exceptions?.paths ?? []) {
    if (!p.startsWith("/")) throw new Problem(400, `"${p}" is not a path. A path starts with "/", for example /payment/ or /web/hook/.`);
    if (p.trim() === "/") throw new Problem(400, 'The path "/" is the whole site: it would let everyone in. Remove it.');
  }
  if (rules.enabled && rules.mode !== "ips" && !rules.countries.length && !(rules.mode === "either" && rules.networks.length))
    throw new Problem(400, "Choose at least one country, or switch the rules off.");
  if (rules.enabled && rules.mode === "ips" && !rules.networks.length) throw new Problem(400, "Add at least one address or range, or switch the rules off.");
  return {
    enabled: !!rules.enabled, mode: rules.mode, scope: rules.scope, countries: [...new Set(rules.countries.map((c) => c.toUpperCase()))].sort(),
    networks: rules.networks.map((n) => ({ value: n.value.trim(), label: n.label.trim() })),
    exceptions: {
      presets: rules.exceptions.presets, paths: rules.exceptions.paths.map((p) => p.trim().toLowerCase()).filter(Boolean),
      networks: rules.exceptions.networks.map((n) => ({ value: n.value.trim(), label: n.label.trim() })),
    },
  };
}

export function preview(raw: unknown, testAddress?: string): AccessPreview {
  const rules = cleanRules(raw);
  const outcome = (address: string | undefined): Outcome | null => {
    if (!address) return null;
    if (!/^[0-9a-fA-F:.]+$/.test(address)) throw new Problem(400, `"${address}" is not an address. Use an address such as 203.0.113.7 or 2001:db8::7.`);
    const decision = decide(rules, address, "/odoo");
    return { address, country: countryOf(address), ...decision };
  };
  const you = outcome(YOU);
  const warnings: AccessPreview["warnings"] = [];
  if (rules.enabled && you && !you.allowed) warnings.push("you_are_outside");
  if (rules.enabled && rules.mode !== "ips") warnings.push("vpn");
  if (!rules.enabled) warnings.push("disabled");
  return {
    you,
    test: outcome(testAddress),
    samples: SAMPLES.map((path) => {
      const d = decide(rules, "192.0.2.1", path);
      return { path, open_to_everyone: d.allowed, why: d.why };
    }),
    warnings,
    geo: { ready: true, missing: "open" },
  };
}

export function sendCode(name: string, method: CodeMethod): { method: string; sent_to?: string } {
  sent.set(name, Date.now());
  return method === "email" ? { method, sent_to: `o***@acme.example (demo code ${DEMO_CODE})` } : { method };
}

export function change(name: string, body: Record<string, unknown>, by: string): AccessRules {
  const rules = cleanRules(body.rules);
  if (body.confirm_name !== name) throw new Problem(400, `Type the instance's name, "${name}", to confirm.`);
  const code = String(body.code ?? "");
  const right = body.method === "app" ? /^\d{6}$/.test(code) : sent.has(name) && code === DEMO_CODE;
  if (!right) throw new Problem(400, "The code is not right, or it has expired. Ask for a new one.");
  sent.delete(name);
  const previous = rulesOf.get(name);
  const saved = { ...rules, version: (previous?.version ?? 0) + 1, changed_by: by, changed_at: iso(Date.now()) };
  rulesOf.set(name, saved);
  return saved;
}

export function summary(rules: AccessRules): string {
  if (!rules.enabled) return "Access rules: off (everyone can open it).";
  const who = rules.mode === "ips" ? `${rules.networks.length} address(es)` : rules.countries.join(", ");
  return `Only visitors from ${who} may open ${rules.scope === "back_office" ? "the back office" : "the whole site"}.`;
}

export const hasRules = (name: string) => !!rulesOf.get(name)?.enabled;
