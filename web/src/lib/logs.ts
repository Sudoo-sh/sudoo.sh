// Reading Odoo's log for the Logs tab (components/LogsPanel.tsx, LogView.tsx) and the demo's backend: lines become
// entries (a line in Odoo's format, "2026-11-22 08:00:00,123 7 INFO shop odoo.http: text", and the lines after it
// that are not: a traceback, a long message), each taken apart into time, pid, level, database, logger and message;
// werkzeug's request lines are taken apart too (method, path, status, timings). The same filters as the server's
// (instances/logs.py): levels, a logger and those under it, tracebacks, a time range and a text, plain or a regular
// expression. A regular expression that could take very long (nested repetitions) is refused before it is used,
// here as on the server, and only the first characters of an entry are searched. The text itself is never changed.

export type Level = "DEBUG" | "INFO" | "WARNING" | "ERROR" | "CRITICAL";
export const LEVELS: Level[] = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"];

/** A request line of werkzeug, taken apart; each part with where it is in the message (for the highlight). */
export interface Request {
  ip: string;
  method: string;
  path: string;
  status: number;
  /** seconds: the time Odoo spent in SQL and in all, when the line says (Odoo 12 and later) */
  queries: number | null;
  sql: number | null;
  total: number | null;
  /** [start, end] of method, path and status in the message */
  at: { method: [number, number]; path: [number, number]; status: [number, number] };
}

export interface LogEntry {
  /** the id the server gave the entry's first line (Docker's time stamp), or one made here ("~n") */
  id: string;
  /** Odoo's own time stamp, as written ("2026-11-22 08:00:00,123"), and as milliseconds (UTC) */
  time: string | null;
  at: number | null;
  pid: string | null;
  level: Level | null;
  db: string | null;
  logger: string | null;
  /** the first line's text after "logger: " (all of it when the line is not in Odoo's format) */
  message: string;
  /** the lines after the first (a traceback, the rest of a long message) */
  more: string[];
  traceback: boolean;
  request: Request | null;
  /** the first line as written */
  head: string;
}

const HEAD = /^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(?:,\d+)?) +(\d+) +(DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL|TEST) +(\S+) +([\w.-]+): ?(.*)$/;
const LEVEL_OF: Record<string, Level> = { DEBUG: "DEBUG", INFO: "INFO", TEST: "INFO", WARNING: "WARNING", WARN: "WARNING", ERROR: "ERROR", CRITICAL: "CRITICAL" };
// 10.0.0.9 - - [22/Nov/2026 08:00:02] "POST /web/dataset/call_kw HTTP/1.1" 200 - 12 0.010 0.045
const REQUEST = /^(\S+) - - (?:\[[^\]]*\] )?"([A-Z]+) (\S+) [^"]*" (\d{3}) \S+(?: (\d+) ([\d.]+) ([\d.]+))?/;
/** Only the first characters of an entry are searched, as on the server (instances/logs.MATCH_CHARS). */
export const MATCH_CHARS = 4096;
export const MAX_QUERY = 200;

let made = 0;

/** The parts of one line in Odoo's format, or null. */
export function parseHead(line: string) {
  const m = HEAD.exec(line);
  if (!m) return null;
  return { time: m[1]!, pid: m[2]!, level: LEVEL_OF[m[3]!]!, db: m[4]!, logger: m[5]!, message: m[6]! };
}

/** Odoo's "2026-11-22 08:00:00,123" (UTC in its container) as milliseconds. */
export function odooTime(time: string): number | null {
  const [day, rest] = time.split(" ");
  const [clock, fraction = "0"] = (rest ?? "").split(",");
  const at = Date.parse(`${day}T${clock}.${fraction.padEnd(3, "0").slice(0, 3)}Z`);
  return Number.isNaN(at) ? null : at;
}

/** A werkzeug request line's message taken apart, or null. */
export function parseRequest(message: string): Request | null {
  const m = REQUEST.exec(message);
  if (!m) return null;
  const method = message.indexOf(`"${m[2]} `) + 1;
  const path = method + m[2]!.length + 1;
  const status = message.indexOf(`" ${m[4]} `, path + m[3]!.length) + 2;
  return {
    ip: m[1]!,
    method: m[2]!,
    path: m[3]!,
    status: Number(m[4]),
    queries: m[5] ? Number(m[5]) : null,
    sql: m[6] ? Number(m[6]) : null,
    total: m[7] ? Number(m[7]) : null,
    at: { method: [method, method + m[2]!.length], path: [path, path + m[3]!.length], status: [status, status + 3] },
  };
}

function entryOf(line: string, id: string | undefined): LogEntry {
  const head = parseHead(line);
  if (!head)
    return { id: id || `~${++made}`, time: null, at: null, pid: null, level: null, db: null, logger: null, message: line, more: [], traceback: false, request: null, head: line };
  return {
    id: id || `~${++made}`,
    time: head.time,
    at: odooTime(head.time),
    pid: head.pid,
    level: head.level,
    db: head.db,
    logger: head.logger,
    message: head.message,
    more: [],
    traceback: false,
    request: head.logger === "werkzeug" ? parseRequest(head.message) : null,
    head: line,
  };
}

/** `lines` (with the server's `ids`, one per line, when it gives them) added to `entries` as entries: a line in
 *  Odoo's format starts one; any other line joins the entry before it, also across calls (the first lines of a new
 *  batch may finish the last entry). Returns a new list; the entries before the last are the same objects. */
export function appendLines(entries: LogEntry[], lines: string[], ids?: string[]): LogEntry[] {
  const out = entries.slice();
  let last: LogEntry | undefined = out[out.length - 1];
  let lastCopied = false;
  lines.forEach((raw, i) => {
    const line = raw.replace(/\r$/, "");
    if (last && last.level && !HEAD.test(line)) {
      if (!lastCopied) {
        last = { ...last, more: last.more.slice() };
        out[out.length - 1] = last;
        lastCopied = true;
      }
      last.more.push(line);
      if (line.startsWith("Traceback (most recent call last)")) last.traceback = true;
      return;
    }
    last = entryOf(line, ids?.[i]);
    lastCopied = true;
    out.push(last);
  });
  return out;
}

/** Entries from text (a download, a test). */
export const parseLog = (text: string): LogEntry[] => appendLines([], text.replace(/\s+$/, "").split("\n").filter((l, i, all) => l || i < all.length - 1));

/** The server's entries (GET .../logs/entries) as the view reads them. */
export function fromServer(items: { id: string; text: string }[]): LogEntry[] {
  const out: LogEntry[] = [];
  for (const item of items) {
    const [first = "", ...rest] = item.text.split("\n");
    const entry = entryOf(first, item.id);
    entry.more = rest;
    entry.traceback = rest.some((l) => l.startsWith("Traceback (most recent call last)"));
    out.push(entry);
  }
  return out;
}

/** The whole text of an entry, as written. */
export const entryText = (entry: LogEntry) => (entry.more.length ? `${entry.head}\n${entry.more.join("\n")}` : entry.head);

// ---------------------------------------------------------------- filters

export type TimeRange = { kind: "all" } | { kind: "last"; minutes: number } | { kind: "custom"; since: string; until: string };

export interface LogFilters {
  /** the levels shown; empty: all */
  levels: Level[];
  /** a logger and those under it ("odoo.addons" keeps odoo.addons.sale...) */
  logger: string;
  query: string;
  regex: boolean;
  /** the search hides the entries without a match (else it only highlights) */
  onlyMatches: boolean;
  tracebacks: boolean;
  range: TimeRange;
}

export const NO_FILTERS: LogFilters = { levels: [], logger: "", query: "", regex: false, onlyMatches: false, tracebacks: false, range: { kind: "all" } };

/** Whether a regular expression could take very long: a repeated group that holds a repetition or a choice
 *  ((a+)+, (a|aa)*, (\w+\s?)*), or a back reference. Refused before it is used, as the server refuses them. */
export function riskyPattern(pattern: string): boolean {
  const group = String.raw`\((?:[^()\\]|\\.)*`;
  const nested = new RegExp(`${group}(?:[+*]|\\{\\d*,?\\d*\\})(?:[^()\\\\]|\\\\.)*\\)(?:[+*]|\\{\\d*,\\d*\\})`);
  const choice = new RegExp(`${group}\\|(?:[^()\\\\]|\\\\.)*\\)(?:[+*]|\\{\\d*,\\d*\\})`);
  return nested.test(pattern) || choice.test(pattern) || /\\[1-9]|\\k</.test(pattern);
}

export type Matcher =
  | { ok: true; empty: boolean; ranges: (text: string) => [number, number][] }
  | { ok: false; reason: "invalid" | "slow" | "long" };

/** Where `query` matches in a text (any case): plain text, or a regular expression when `regex`. */
export function makeMatcher(query: string, regex: boolean): Matcher {
  const wanted = query.trim();
  if (!wanted) return { ok: true, empty: true, ranges: () => [] };
  if (wanted.length > MAX_QUERY) return { ok: false, reason: "long" };
  if (!regex) {
    const lower = wanted.toLowerCase();
    return {
      ok: true,
      empty: false,
      ranges: (text) => {
        const found: [number, number][] = [];
        const hay = text.slice(0, MATCH_CHARS).toLowerCase();
        for (let at = hay.indexOf(lower); at !== -1; at = hay.indexOf(lower, at + lower.length)) found.push([at, at + lower.length]);
        return found;
      },
    };
  }
  if (riskyPattern(wanted)) return { ok: false, reason: "slow" };
  let pattern: RegExp;
  try {
    pattern = new RegExp(wanted, "gi");
  } catch {
    return { ok: false, reason: "invalid" };
  }
  return {
    ok: true,
    empty: false,
    ranges: (text) => {
      const found: [number, number][] = [];
      pattern.lastIndex = 0;
      const hay = text.slice(0, MATCH_CHARS);
      for (let m = pattern.exec(hay); m && found.length < 500; m = pattern.exec(hay)) {
        if (m[0] === "") {
          pattern.lastIndex++;
          continue;
        }
        found.push([m.index, m.index + m[0].length]);
      }
      return found;
    },
  };
}

/** since and until (ISO 8601, UTC) of a time range, at `now`. */
export function rangeBounds(range: TimeRange, now = Date.now()): { since?: string; until?: string } {
  if (range.kind === "last") return { since: new Date(now - range.minutes * 60_000).toISOString() };
  if (range.kind === "custom") {
    const out: { since?: string; until?: string } = {};
    if (range.since) out.since = new Date(range.since).toISOString();
    if (range.until) out.until = new Date(range.until).toISOString();
    return out;
  }
  return {};
}

/** The query of GET .../logs/entries, .../logs/stream and .../logs/download for these filters. */
export function filterParams(filters: LogFilters, now = Date.now()): Record<string, string> {
  const params: Record<string, string> = {};
  if (filters.levels.length && filters.levels.length < LEVELS.length) params.level = filters.levels.join(",");
  if (filters.logger) params.logger = filters.logger;
  if (filters.tracebacks) params.tracebacks = "1";
  if (filters.onlyMatches && filters.query.trim()) {
    params.q = filters.query.trim();
    if (filters.regex) params.regex = "1";
  }
  const { since, until } = rangeBounds(filters.range, now);
  if (since) params.since = since;
  if (until) params.until = until;
  return params;
}

/** Whether an entry passes the filters (the demo's backend filters with this; the real server does the same). */
export function passes(entry: LogEntry, filters: LogFilters, matcher: Matcher = makeMatcher(filters.onlyMatches ? filters.query : "", filters.regex), now = Date.now()): boolean {
  if (filters.levels.length && !filters.levels.includes(entry.level ?? "INFO")) return false;
  if (filters.logger) {
    const logger = entry.logger ?? "";
    if (logger !== filters.logger && !logger.startsWith(`${filters.logger}.`)) return false;
  }
  if (filters.tracebacks && !entry.traceback) return false;
  const { since, until } = rangeBounds(filters.range, now);
  if (since || until) {
    if (entry.at == null) return false;
    if (since && entry.at < Date.parse(since)) return false;
    if (until && entry.at >= Date.parse(until)) return false;
  }
  if (!matcher.ok) return false;
  return matcher.empty || matcher.ranges(entryText(entry)).length > 0;
}

/** How many filters are on (for the summary row). */
export const activeFilters = (f: LogFilters) =>
  (f.levels.length ? 1 : 0) + (f.logger ? 1 : 0) + (f.tracebacks ? 1 : 0) + (f.range.kind !== "all" ? 1 : 0) + (f.onlyMatches && f.query.trim() ? 1 : 0);

// ---------------------------------------------------------------- the error timeline

export interface Minute {
  /** the minute's start (ms, UTC) */
  at: number;
  errors: number;
  warnings: number;
  /** index of the first entry of the minute */
  first: number;
}

/** Errors (ERROR, CRITICAL) and warnings per minute over the entries, oldest first: every minute from the first entry
 *  to the last (at most `max`, the latest), so a quiet minute shows as a gap. */
export function perMinute(entries: LogEntry[], max = 180): Minute[] {
  const byMinute = new Map<number, Minute>();
  let lo = Infinity;
  let hi = -Infinity;
  entries.forEach((entry, index) => {
    if (entry.at == null) return;
    const at = Math.floor(entry.at / 60_000) * 60_000;
    let minute = byMinute.get(at);
    if (!minute) byMinute.set(at, (minute = { at, errors: 0, warnings: 0, first: index }));
    if (entry.level === "ERROR" || entry.level === "CRITICAL") minute.errors++;
    else if (entry.level === "WARNING") minute.warnings++;
    lo = Math.min(lo, at);
    hi = Math.max(hi, at);
  });
  if (!byMinute.size) return [];
  lo = Math.max(lo, hi - (max - 1) * 60_000);
  const out: Minute[] = [];
  let lastFirst = 0;
  for (let at = lo; at <= hi; at += 60_000) {
    const minute = byMinute.get(at);
    if (minute) lastFirst = minute.first;
    out.push(minute ?? { at, errors: 0, warnings: 0, first: lastFirst });
  }
  return out;
}

/** The HTTP status of a request, as a class: 2xx, 3xx, 4xx or 5xx. */
export const statusClass = (status: number) => `${Math.min(5, Math.max(1, Math.floor(status / 100)))}xx`;
