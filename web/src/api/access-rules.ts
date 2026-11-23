// Production access rules (beta) of an instance: GET/PUT /api/v1/instances/<name>/access-rules, its preview and
// one-time code, and the link that opens an instance through the panel's pass (web/v1/access_rules.py).

import { useQuery } from "@tanstack/react-query";
import { api } from "./client";

export type AccessMode = "countries" | "ips" | "either";
export type AccessScope = "back_office" | "site";
export type AccessPreset = "payments" | "webhooks" | "portal" | "livechat" | "mail_links";
export type CodeMethod = "email" | "app";

export interface Network {
  value: string;
  label: string;
}

export interface AccessRules {
  enabled: boolean;
  mode: AccessMode;
  scope: AccessScope;
  countries: string[];
  networks: Network[];
  exceptions: { presets: AccessPreset[]; paths: string[]; networks: Network[] };
  version?: number;
  changed_by?: string | null;
  changed_at?: string | null;
}

export interface GeoStatus {
  ready: boolean;
  month: string | null;
  built_at: number | null;
  ranges: number;
  problem: string | null;
  last_attempt: number | null;
  last_error: string | null;
  attribution: string;
}

export interface AccessRulesState {
  available: boolean;
  production?: boolean;
  active?: boolean;
  rules?: AccessRules;
  can_change?: boolean;
  channels?: CodeMethod[];
  geo?: GeoStatus;
  geo_missing?: "open" | "closed";
  you?: { address: string; country: string | null };
  presets?: Record<AccessPreset, string[]>;
  back_office?: string[];
  public_under_web?: string[];
}

export interface Outcome {
  address: string;
  country: string | null;
  allowed: boolean;
  why: string;
}

export type PreviewWarning = "geo_missing_open" | "geo_missing_closed" | "you_are_outside" | "vpn" | "disabled";

export interface AccessPreview {
  you: Outcome | null;
  test: Outcome | null;
  samples: { path: string; open_to_everyone: boolean; why: string }[];
  warnings: PreviewWarning[];
  geo: { ready: boolean; missing: "open" | "closed" };
}

export const accessKey = (name: string) => ["instance", name, "access-rules"] as const;

export function useAccessRules(name: string, enabled = true) {
  return useQuery({
    queryKey: accessKey(name),
    queryFn: () => api.get<AccessRulesState>(`/instances/${name}/access-rules`),
    enabled: enabled && !!name,
    staleTime: 30_000,
  });
}

/** Opens a production instance in a new tab through the panel's pass when its access rules apply (the pass works from
 *  anywhere); the tab is opened at once, so the browser does not take it for a pop-up, and gets its address after. */
export async function openThroughPass(name: string, fallback: string, target = "/odoo") {
  const tab = window.open("", "_blank");
  try {
    const { url } = await api.post<{ url: string; pass: boolean }>(`/instances/${name}/open-link`, { target });
    if (tab) {
      tab.opener = null;
      tab.location.href = url;
    } else window.location.href = url;
  } catch {
    if (tab) tab.location.href = fallback;
  }
}

/** ISO 3166-1 alpha-2 codes of the countries and territories the country database knows. */
export const COUNTRIES = (
  "AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ CA CC " +
  "CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE " +
  "GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM " +
  "KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ " +
  "NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH " +
  "SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG " +
  "VI VN VU WF WS XK YE YT ZA ZM ZW"
).split(" ");

/** The emoji flag of a country code (two regional indicator letters; drawn by the system, nothing is downloaded). */
export const flag = (code: string) =>
  /^[A-Za-z]{2}$/.test(code) ? String.fromCodePoint(...[...code.toUpperCase()].map((c) => 0x1f1e6 + c.charCodeAt(0) - 65)) : "";

const names = new Map<string, Intl.DisplayNames | null>();

/** The country's name in the language shown (the browser knows them); the code when it does not. */
export function countryName(code: string, locale: string) {
  if (!names.has(locale)) {
    try {
      names.set(locale, new Intl.DisplayNames([locale], { type: "region" }));
    } catch {
      names.set(locale, null);
    }
  }
  try {
    return names.get(locale)?.of(code) ?? code;
  } catch {
    return code;
  }
}

const IPV4 = /^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}(\/([0-9]|[12]\d|3[0-2]))?$/;
const IPV6 = /^[0-9a-fA-F:.]{2,45}(\/([0-9]|[1-9]\d|1[01]\d|12[0-8]))?$/;

/** A quick check while typing (the panel checks addresses for real, ranges' start included). */
export const looksLikeNetwork = (value: string) => {
  const text = value.trim();
  return IPV4.test(text) || (text.includes(":") && IPV6.test(text));
};
