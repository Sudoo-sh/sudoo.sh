import type { Instance } from "@/api/types";

const HOUR = 3_600_000;
const DAY = 24 * HOUR;

/** The instances that expire within `days`, the nearest first, at most `rows` of them (an expired date counts as the
 *  nearest of all). A production instance has none; one that is already stopped at the end of its lifetime still
 *  shows, since an administrator can extend it. */
export function expiringSoon(instances: Instance[] | undefined, days: number, rows: number, now = Date.now()) {
  const within = (instances ?? [])
    .filter((i): i is Instance & { expires_at: string } => !!i.expires_at)
    .map((i) => ({ instance: i, at: new Date(i.expires_at).getTime() }))
    .filter((x) => !Number.isNaN(x.at) && x.at <= now + days * DAY)
    .sort((a, b) => a.at - b.at || a.instance.name.localeCompare(b.instance.name));
  return { total: within.length, rows: within.slice(0, Math.max(0, rows)) };
}

/** How near an expiry is: "over" (already passed), "soon" (under 24 hours) or "near". */
export function urgency(at: number, now = Date.now()): "over" | "soon" | "near" {
  return at <= now ? "over" : at - now < DAY ? "soon" : "near";
}

/** The extensions the Extend menu offers: the whole numbers of days in the setting ("1,7,30"), once each, smallest
 *  first. Without the right to extend without limits, nothing above `maxDays` (0: no limit) is offered. */
export function extendChoices(presets: string, maxDays: number, unlimited: boolean): number[] {
  const days = presets
    .split(",")
    .map((x) => Number(x.trim()))
    .filter((n) => Number.isInteger(n) && n > 0);
  return [...new Set(days)].filter((n) => unlimited || maxDays === 0 || n <= maxDays).sort((a, b) => a - b);
}

/** The lifetime (`delete_after_days`) that ends `add` days later than now does: the expiry is the creation plus the
 *  lifetime, so the extension is added to the lifetime it has. */
export const extendedLifetime = (lifetimeDays: number, add: number) => lifetimeDays + add;

/** Why an extended instance stays stopped (the API's `meta.restart`), or null when nothing is said. */
export function stayedStopped(answer: { meta?: Record<string, unknown> } | null | undefined): string | null {
  const why = answer?.meta?.restart;
  return typeof why === "string" && why ? why : null;
}
