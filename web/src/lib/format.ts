// How numbers, sizes and times are shown, in the language of the panel: Intl with Western digits in Arabic too
// ("ar-u-nu-latn"), as on the other Sudoo.sh sites. Pages are drawn again when the language changes, so these read it
// when called.

import { getLocale, intlTag, words } from "@/i18n";
import common from "@/i18n/common";

const tag = () => intlTag(getLocale());
const t = () => words(common);

export function bytes(value: number | null | undefined, digits = 1): string {
  if (value == null) return "—";
  const units = t().bytes;
  let size = value;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit++;
  }
  const shown = unit === 0 ? 0 : size < 10 ? digits : 0;
  return `${new Intl.NumberFormat(tag(), { minimumFractionDigits: shown, maximumFractionDigits: shown }).format(size)} ${units[unit]}`;
}

export const percent = (value: number | null | undefined, digits = 0) =>
  value == null
    ? "—"
    : `${new Intl.NumberFormat(tag(), { minimumFractionDigits: digits, maximumFractionDigits: digits, useGrouping: false }).format(value)}%`;

export const number = (value: number | null | undefined, digits = 0) =>
  value == null ? "—" : new Intl.NumberFormat(tag(), { maximumFractionDigits: digits }).format(value);

export const ms = (value: number | null | undefined) =>
  value == null ? "—" : value >= 1000 ? t().time.s(number(value / 1000, 1)) : t().time.ms(number(Math.round(value)));

const toDate = (value: string | number | null | undefined) =>
  value == null ? null : typeof value === "number" ? new Date(value * 1000) : new Date(value);

export function ago(value: string | number | null | undefined): string {
  const date = toDate(value);
  if (!date) return t().time.never;
  const seconds = Math.round((date.getTime() - Date.now()) / 1000);
  const abs = Math.abs(seconds);
  if (abs < 45) return seconds <= 0 ? t().time.justNow : t().time.soon;
  const [amount, unit]: [number, Intl.RelativeTimeFormatUnit] =
    abs < 3600
      ? [seconds / 60, "minute"]
      : abs < 86400
        ? [seconds / 3600, "hour"]
        : abs < 2592000
          ? [seconds / 86400, "day"]
          : abs < 31536000
            ? [seconds / 2592000, "month"]
            : [seconds / 31536000, "year"];
  return new Intl.RelativeTimeFormat(tag(), { numeric: "auto" }).format(Math.round(amount), unit);
}

/** The date and time in full, for a tooltip next to a relative time: "Wednesday 18 November 2026 at 06:34". */
export function fullDateTime(value: string | number | null | undefined): string {
  const date = toDate(value);
  return date && !Number.isNaN(date.getTime())
    ? date.toLocaleString(tag(), { dateStyle: "full", timeStyle: "short" })
    : "";
}

export function dateTime(value: string | number | null | undefined): string {
  const date = toDate(value);
  return date ? date.toLocaleString(tag(), { dateStyle: "medium", timeStyle: "short" }) : "—";
}

/** A clock time ("14:05"), for chart axes and lists of today's events. */
export function time(value: string | number | Date, withSeconds = false): string {
  const date = value instanceof Date ? value : toDate(value)!;
  return date.toLocaleTimeString(tag(), {
    hour: "2-digit",
    minute: "2-digit",
    ...(withSeconds ? { second: "2-digit" } : {}),
  });
}

export function duration(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const words = t().time;
  if (d) return words.days(d, h);
  if (h) return words.hours(h, m);
  if (m) return words.minutes(m);
  return words.seconds(Math.max(0, Math.floor(seconds)));
}

export function elapsed(from: string | null, to?: string | null): string {
  if (!from) return "";
  const end = to ? new Date(to).getTime() : Date.now();
  return duration((end - new Date(from).getTime()) / 1000);
}

/** A count with its word: count(3, "instance") -> "3 instances", "3 بيئات". */
export function count(n: number, unit: keyof ReturnType<typeof t>["units"]): string {
  const forms = t().units[unit];
  const category = new Intl.PluralRules(getLocale()).select(n) as keyof typeof forms;
  return (forms[category] ?? forms.other).replace("#", new Intl.NumberFormat("en").format(n));
}

/** Two colours for an instance's monogram, always the same for the same name. */
export function monogram(name: string): { from: string; to: string; letters: string } {
  let hash = 0;
  for (const char of name) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  const hue = hash % 360;
  const letters =
    name
      .split(/[-_.]/)
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part[0])
      .join("") ||
    name[0] ||
    "?";
  return { from: `hsl(${hue} 52% 58%)`, to: `hsl(${(hue + 40) % 360} 58% 42%)`, letters };
}

/** The word for an instance's or a job's state; one this app does not know is shown as sent. */
export const statusLabel = (status: string) => t().status[status] ?? status;

/** An amount in cents as money: 500, "usd" -> "$5.00". */
export function money(cents: number | null | undefined, currency: string | null | undefined = "usd"): string {
  if (cents == null) return "—";
  try {
    const ar = getLocale() === "ar";
    return new Intl.NumberFormat(tag(), {
      style: "currency",
      currency: (currency || "usd").toUpperCase(),
      currencyDisplay: ar ? "code" : "symbol",
    }).format(cents / 100);
  } catch {
    return `${(cents / 100).toFixed(2)} ${(currency || "").toUpperCase()}`;
  }
}

export function day(value: string | number | null | undefined): string {
  const parsed = toDate(value);
  return parsed ? parsed.toLocaleDateString(tag(), { dateStyle: "medium" }) : "—";
}

/** How long a build took, to the second below an hour: "4m 51s" */
export function buildTime(seconds: number | null | undefined): string {
  if (seconds == null) return "";
  const whole = Math.max(0, Math.round(seconds));
  if (whole >= 3600) return duration(whole);
  const words = t().time;
  const m = Math.floor(whole / 60);
  return m ? `${words.minutes(m)} ${words.seconds(whole % 60)}` : words.seconds(whole);
}
