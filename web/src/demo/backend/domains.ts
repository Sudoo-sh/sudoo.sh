// The demo's custom domains (GET /api/v1/instances/<name>/domains): a few sample domains in different states on
// the production instances, and an added domain that goes through DNS, the certificate and active on its own
// (live event "domains"), as on a real server with the record already in place.

import type { CustomDomain, Domains, Instance } from "@/api/types";
import { publish } from "./bus";
import { instances, instanceUrl, setting } from "./state";
import { DAY, HOUR, iso, MINUTE, OPENED, SECOND } from "./util";

export const TARGET = "edge.integrator-demo.net";
const ISSUER = "Let's Encrypt R11";

const byInstance = new Map<string, CustomDomain[]>();

function domain(name: string, values: Partial<CustomDomain>): CustomDomain {
  const status = values.status ?? "waiting_dns";
  return {
    domain: name,
    primary: false,
    status,
    reason: null,
    tls: "acme",
    dns_records: [{ type: "CNAME", name, value: TARGET }],
    addresses: [],
    added_at: iso(OPENED - 3 * DAY),
    added_by: "omar",
    checked_at: iso(OPENED - 4 * MINUTE),
    next_check_at: null,
    renews_at: null,
    certificate: null,
    url: null,
    ...values,
  };
}

function certified(issuedAgo: number, lifetime = 90 * DAY): Partial<CustomDomain> {
  const issued = OPENED - issuedAgo;
  return {
    certificate: { issued_at: iso(issued), expires_at: iso(issued + lifetime), issuer: ISSUER, staging: false },
    renews_at: iso(issued + lifetime - 30 * DAY),
    addresses: ["203.0.113.40"],
  };
}

export function seedDomains() {
  byInstance.clear();
  byInstance.set("acme-retail-main", [
    domain("erp.acme-retail.com", { primary: true, status: "active", added_at: iso(OPENED - 70 * DAY), ...certified(41 * DAY), url: "https://erp.acme-retail.com" }),
    domain("shop.acme-retail.com", {
      status: "waiting_dns", added_at: iso(OPENED - 25 * MINUTE), added_by: "sara", next_check_at: iso(OPENED + 4 * MINUTE),
      reason: "shop.acme-retail.com has no DNS record yet. Create the record below; it can take a while to appear.",
    }),
  ]);
  byInstance.set("nile-logistics-main", [
    domain("ops.nile-logistics.com", {
      primary: true, status: "expiring", added_at: iso(OPENED - 200 * DAY), ...certified(81 * DAY), url: "https://ops.nile-logistics.com",
      next_check_at: iso(OPENED + 2 * HOUR),
      reason: "ops.nile-logistics.com points at 198.51.100.77, which is not this server. Change its DNS record as shown below (behind a proxy such as Cloudflare, switch the proxy off for this record: DNS only).",
    }),
    domain("portal.nile-logistics.com", {
      status: "failed", added_at: iso(OPENED - 2 * DAY), next_check_at: iso(OPENED + 40 * MINUTE), addresses: ["203.0.113.40"],
      reason: "The ACME authority could not reach portal.nile-logistics.com over http: Timeout during connect (likely firewall problem)",
    }),
  ]);
  byInstance.set("zahra-clinic-main", [
    domain("app.zahra-clinic.com", { primary: true, status: "active", added_at: iso(OPENED - 30 * DAY), ...certified(29 * DAY), url: "https://app.zahra-clinic.com" }),
  ]);
  for (const name of byInstance.keys()) syncUrl(name);
}

/** The instance's address: its active primary domain, else its own link (as exposure.address). */
function syncUrl(name: string) {
  const instance = instances.get(name);
  if (!instance) return;
  const primary = (byInstance.get(name) ?? []).find((d) => d.primary && d.url);
  instance.url = primary?.url ?? instanceUrl(name);
}

export function domainsOf(instance: Instance): Domains {
  return {
    mode: "acme",
    limit: Number(setting("domains_per_instance") ?? 5),
    default_url: instanceUrl(instance.name),
    target: { host: TARGET, record_type: "CNAME" },
    exposure: "host-ports",
    listener_error: null,
    authority: "production",
    domains: (byInstance.get(instance.name) ?? []).map((d) => ({ ...d, dns_records: d.status === "active" ? [] : d.dns_records })),
  };
}

export const findDomain = (instance: string, name: string) => (byInstance.get(instance) ?? []).find((d) => d.domain === name);
const taken = (name: string) => [...byInstance.entries()].find(([, list]) => list.some((d) => d.domain === name))?.[0];

function announce(name: string) {
  syncUrl(name);
  const instance = instances.get(name);
  if (instance) publish("domains", name, domainsOf(instance));
}

/** Checks a name like domains/names.py, roughly; returns the stored form or an error message. */
export function checkName(value: string, instance: string): string | { error: string } {
  const name = value.trim().toLowerCase().replace(/\.$/, "");
  if (/[/:@]/.test(name)) return { error: "Enter only the domain, such as erp.example.com: no https://, path or port." };
  if (name.startsWith("*")) return { error: "Wildcard domains are not supported: add each domain on its own (erp.example.com)." };
  if (/^[\d.]+$/.test(name)) return { error: "Enter a domain name, not an IP address." };
  if (!/^(?!-)[a-z0-9-]{1,63}(?<!-)(\.(?!-)[a-z0-9-]{1,63}(?<!-))+$/.test(name)) return { error: "This is not a valid domain name: use letters, digits, hyphens and dots (erp.example.com)." };
  if (/(^|\.)(sudoo\.sh|example\.(com|net|org)|localhost|local|test|invalid)$/.test(name)) return { error: "Domains under this name cannot be used here." };
  if (name === TARGET) return { error: "This name belongs to this server or its panel, not to an instance." };
  const owner = taken(name);
  if (owner) return { error: `${name} is already a custom domain of ${owner === instance ? "this instance" : "another instance"}.` };
  return name;
}

export function addDomain(instance: Instance, name: string, by: string): CustomDomain {
  const list = byInstance.get(instance.name) ?? [];
  const added = domain(name, { primary: list.length === 0, added_at: iso(Date.now()), added_by: by, checked_at: null, next_check_at: iso(Date.now() + MINUTE) });
  list.push(added);
  byInstance.set(instance.name, list);
  // the demo's record "appears" in DNS after a few seconds: issuing, then active with a certificate
  setTimeout(() => {
    Object.assign(added, { status: "issuing", reason: null, checked_at: iso(Date.now()), next_check_at: null, addresses: ["203.0.113.40"] });
    announce(instance.name);
  }, 6 * SECOND);
  setTimeout(() => {
    if (!findDomain(instance.name, name)) return;
    Object.assign(added, { status: "active", ...certified(0), url: `https://${name}` });
    announce(instance.name);
  }, 11 * SECOND);
  announce(instance.name);
  return added;
}

export function makePrimary(instance: Instance, name: string) {
  for (const d of byInstance.get(instance.name) ?? []) d.primary = d.domain === name;
  announce(instance.name);
}

export function removeDomain(instance: Instance, name: string) {
  const list = (byInstance.get(instance.name) ?? []).filter((d) => d.domain !== name);
  if (list.length && !list.some((d) => d.primary)) list[0]!.primary = true;
  byInstance.set(instance.name, list);
  announce(instance.name);
}

export function checkNow(instance: Instance, name: string): CustomDomain | undefined {
  const found = findDomain(instance.name, name);
  if (found) {
    found.checked_at = iso(Date.now());
    found.next_check_at = iso(Date.now() + MINUTE);
    announce(instance.name);
  }
  return found;
}
