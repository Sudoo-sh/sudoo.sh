// Small helpers of the demo's backend: times as the API writes them, errors as problem details, ids, and a
// random source that gives the same "past" on every visit.

export const SECOND = 1000;
export const MINUTE = 60 * SECOND;
export const HOUR = 60 * MINUTE;
export const DAY = 24 * HOUR;

/** The moment the demo opened: every seeded time is relative to it, so the data always looks recent. */
export const OPENED = Date.now();

/** ISO 8601 in UTC without milliseconds, as the API answers (2026-11-12T08:30:00Z). */
export const iso = (ms: number | null | undefined): string | null =>
  ms == null ? null : new Date(ms).toISOString().replace(/\.\d{3}Z$/, "Z");

/** An error the API answers as RFC 9457 problem details. */
export class Problem extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
  ) {
    super(detail);
  }
}

export const hex = (length = 12) =>
  Array.from(crypto.getRandomValues(new Uint8Array(Math.ceil(length / 2))), (b) => b.toString(16).padStart(2, "0"))
    .join("")
    .slice(0, length);

/** A seeded generator (mulberry32): the same sequence for the same seed. */
export function seeded(seed: number) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function hash(text: string): number {
  let h = 2166136261;
  for (const char of text) h = Math.imul(h ^ char.charCodeAt(0), 16777619);
  return h >>> 0;
}

/** Smooth noise in [-1, 1] over time: values at fixed knots, eased between them, the same for the same seed. */
export function noise(seed: number, seconds: number, knot = 30): number {
  const at = (k: number) => seeded(seed ^ Math.imul(k, 2654435761))() * 2 - 1;
  const k = Math.floor(seconds / knot);
  const f = seconds / knot - k;
  const eased = f * f * (3 - 2 * f);
  return at(k) + (at(k + 1) - at(k)) * eased;
}

export const clamp = (value: number, low: number, high: number) => Math.min(high, Math.max(low, value));
export const round = (value: number, digits = 1) => Math.round(value * 10 ** digits) / 10 ** digits;
export const pause = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** The backup file name the panel writes: <instance>__<database>__<YYYYMMDD-HHMMSS>__<kind>.zip */
export function backupFile(instance: string, database: string, at: number, kind: string): string {
  const d = new Date(at);
  const two = (n: number) => String(n).padStart(2, "0");
  const stamp = `${d.getUTCFullYear()}${two(d.getUTCMonth() + 1)}${two(d.getUTCDate())}-${two(d.getUTCHours())}${two(d.getUTCMinutes())}${two(d.getUTCSeconds())}`;
  return `${instance}__${database}__${stamp}__${kind}.zip`;
}
export const stampOf = (file: string) => file.split("__")[2] ?? "";

/** The slug the panel makes names from (web/v1/instances.py _slug). */
export const slug = (text: string) =>
  text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
