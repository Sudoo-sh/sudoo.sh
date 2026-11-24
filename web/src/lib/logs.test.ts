import { describe, expect, it } from "vitest";
import {
  activeFilters,
  appendLines,
  entryText,
  filterParams,
  fromServer,
  makeMatcher,
  NO_FILTERS,
  parseHead,
  parseLog,
  parseRequest,
  passes,
  perMinute,
  riskyPattern,
  statusClass,
  type LogFilters,
} from "./logs";

const LOG = [
  "2026-11-22 08:00:00,100 7 INFO shop odoo.modules.loading: Modules loaded.",
  "2026-11-22 08:00:01,200 7 INFO shop odoo.addons.base.models.ir_cron: Job 'Mail: Email Queue Manager' (3) done in 0.04s",
  '2026-11-22 08:00:02,300 7 INFO shop werkzeug: 10.0.0.9 - - [22/Nov/2026 08:00:02] "POST /web/dataset/call_kw/sale.order/web_search_read HTTP/1.1" 200 - 12 0.010 0.045',
  "2026-11-22 08:00:03,400 7 WARNING shop odoo.addons.account.models.move: Generating nondeterministic report",
  "2026-11-22 08:01:00,500 7 ERROR shop odoo.http: Exception during request handling.",
  "Traceback (most recent call last):",
  '  File "/usr/lib/python3/dist-packages/odoo/http.py", line 2203, in __call__',
  "    response = request._serve_db()",
  "odoo.exceptions.ValidationError: The payment does not match its total.",
  "2026-11-22 08:02:00,600 7 CRITICAL shop odoo.service.server: Worker crashed",
  "2026-11-22 08:03:00,700 7 DEBUG shop odoo.sql_db: query took 0.2s",
  '2026-11-22 08:04:00,800 7 INFO shop werkzeug: 10.0.0.9 - - [22/Nov/2026 08:04:00] "GET /web/login HTTP/1.1" 500 - 4 0.002 0.120',
].join("\n");

const filters = (more: Partial<LogFilters>): LogFilters => ({ ...NO_FILTERS, ...more });

describe("parsing Odoo's log", () => {
  it("takes a line in Odoo's format apart", () => {
    expect(parseHead("2026-11-22 08:01:00,500 7 ERROR shop odoo.http: Exception during request handling.")).toEqual({
      time: "2026-11-22 08:01:00,500",
      pid: "7",
      level: "ERROR",
      db: "shop",
      logger: "odoo.http",
      message: "Exception during request handling.",
    });
    expect(parseHead("2026-11-22 08:01:00,500 7 WARN ? odoo.x: y")?.level).toBe("WARNING");
    expect(parseHead("Traceback (most recent call last):")).toBeNull();
  });

  it("takes werkzeug's request line apart, with where each part is", () => {
    const message = '10.0.0.9 - - [22/Nov/2026 08:04:00] "GET /web/login HTTP/1.1" 500 - 4 0.002 0.120';
    const request = parseRequest(message)!;
    expect(request).toMatchObject({
      ip: "10.0.0.9",
      method: "GET",
      path: "/web/login",
      status: 500,
      queries: 4,
      sql: 0.002,
      total: 0.12,
    });
    expect(message.slice(...request.at.method)).toBe("GET");
    expect(message.slice(...request.at.path)).toBe("/web/login");
    expect(message.slice(...request.at.status)).toBe("500");
    expect(parseRequest('127.0.0.1 - - "GET / HTTP/1.1" 303 -')).toMatchObject({ status: 303, total: null });
    expect(statusClass(204)).toBe("2xx");
    expect(statusClass(404)).toBe("4xx");
  });

  it("groups a traceback with the line it belongs to", () => {
    const entries = parseLog(LOG);
    expect(entries).toHaveLength(8);
    const error = entries[4]!;
    expect(error.level).toBe("ERROR");
    expect(error.traceback).toBe(true);
    expect(error.more).toHaveLength(4);
    expect(entryText(error).split("\n")).toHaveLength(5);
    expect(entries[2]!.request?.method).toBe("POST");
    expect(entries[0]!.at).toBe(Date.UTC(2026, 10, 22, 8, 0, 0, 100));
  });

  it("finishes the last entry with the next batch's first lines, without changing the earlier list", () => {
    const lines = LOG.split("\n");
    const first = appendLines(
      [],
      lines.slice(0, 6),
      lines.slice(0, 6).map((_, i) => `id${i}`),
    );
    const before = first[first.length - 1]!;
    const second = appendLines(
      first,
      lines.slice(6),
      lines.slice(6).map((_, i) => `id${i + 6}`),
    );
    expect(second).toHaveLength(8);
    expect(second[4]!.more).toHaveLength(4);
    expect(before.more).toHaveLength(1); // the first list is unchanged
    expect(second[4]!.id).toBe("id4");
    expect(second[5]!.id).toBe("id9");
  });

  it("reads the server's entries", () => {
    const [entry] = fromServer([
      { id: "2026-11-22T08:01:00.000000001Z", text: LOG.split("\n").slice(4, 9).join("\n") },
    ]);
    expect(entry!.traceback).toBe(true);
    expect(entry!.id).toBe("2026-11-22T08:01:00.000000001Z");
  });
});

describe("filtering", () => {
  const entries = parseLog(LOG);
  const kept = (f: LogFilters) =>
    entries.filter((e) => passes(e, f, undefined, Date.UTC(2026, 10, 22, 8, 5))).map((e) => `${e.level}:${e.logger}`);

  it("by level, logger and tracebacks", () => {
    expect(kept(filters({ levels: ["ERROR", "CRITICAL"] }))).toEqual([
      "ERROR:odoo.http",
      "CRITICAL:odoo.service.server",
    ]);
    expect(kept(filters({ logger: "odoo.addons" }))).toEqual([
      "INFO:odoo.addons.base.models.ir_cron",
      "WARNING:odoo.addons.account.models.move",
    ]);
    expect(kept(filters({ logger: "odoo.add" }))).toEqual([]);
    expect(kept(filters({ tracebacks: true }))).toEqual(["ERROR:odoo.http"]);
  });

  it("by time", () => {
    expect(kept(filters({ range: { kind: "last", minutes: 3 } }))).toEqual([
      "CRITICAL:odoo.service.server",
      "DEBUG:odoo.sql_db",
      "INFO:werkzeug",
    ]);
    expect(
      kept(filters({ range: { kind: "custom", since: "2026-11-22T08:01:00Z", until: "2026-11-22T08:02:00Z" } })),
    ).toEqual(["ERROR:odoo.http"]);
  });

  it("by text, only when showing only the matches", () => {
    expect(kept(filters({ query: "validationerror" }))).toHaveLength(8); // the search alone only highlights
    expect(kept(filters({ query: "validationerror", onlyMatches: true }))).toEqual(["ERROR:odoo.http"]);
    expect(kept(filters({ query: String.raw`HTTP/1\.1" 5\d\d`, regex: true, onlyMatches: true }))).toEqual([
      "INFO:werkzeug",
    ]);
  });

  it("gives the server the same filters", () => {
    expect(
      filterParams(
        filters({
          levels: ["ERROR"],
          logger: "odoo.http",
          tracebacks: true,
          query: "x",
          regex: true,
          onlyMatches: true,
        }),
      ),
    ).toEqual({
      level: "ERROR",
      logger: "odoo.http",
      tracebacks: "1",
      q: "x",
      regex: "1",
    });
    expect(filterParams(filters({ query: "x" }))).toEqual({});
    expect(filterParams(filters({ range: { kind: "last", minutes: 15 } }), Date.UTC(2026, 10, 22, 8, 15))).toEqual({
      since: "2026-11-22T08:00:00.000Z",
    });
    expect(activeFilters(filters({ levels: ["ERROR"], range: { kind: "last", minutes: 60 } }))).toBe(2);
  });
});

describe("search", () => {
  it("finds plain text in any case", () => {
    const m = makeMatcher("ERROR", false);
    expect(m.ok && m.ranges("an error, an Error")).toEqual([
      [3, 8],
      [13, 18],
    ]);
  });

  it("refuses regular expressions that could take very long, and invalid ones", () => {
    for (const pattern of ["(a+)+$", "(x+x+)+y", "(a|aa)+$", String.raw`^(\w+\s?)*$`, String.raw`(a)\1`]) {
      expect(riskyPattern(pattern)).toBe(true);
      expect(makeMatcher(pattern, true)).toEqual({ ok: false, reason: "slow" });
    }
    expect(makeMatcher("(", true)).toEqual({ ok: false, reason: "invalid" });
    expect(makeMatcher("x".repeat(201), false)).toEqual({ ok: false, reason: "long" });
    for (const pattern of [String.raw`HTTP/1\.1" 5\d\d`, "odoo\\.(http|sql_db)", "a+b*", "(ab)+"])
      expect(riskyPattern(pattern)).toBe(false);
  });

  it("searches only the start of a huge entry", () => {
    const m = makeMatcher("needle", false);
    expect(m.ok && m.ranges(`${"x".repeat(10_000)}needle`)).toEqual([]);
  });
});

describe("the error timeline", () => {
  it("counts errors and warnings per minute, quiet minutes included", () => {
    const minutes = perMinute(parseLog(LOG));
    expect(minutes.map((m) => [m.errors, m.warnings])).toEqual([
      [0, 1],
      [1, 0],
      [1, 0],
      [0, 0],
      [0, 0],
    ]);
    expect(minutes[1]!.first).toBe(4);
  });

  it("handles 100,000 lines quickly", () => {
    const many = Array.from(
      { length: 100_000 },
      (_, i) =>
        `2026-11-22 08:${String(Math.floor(i / 2000) % 60).padStart(2, "0")}:00,${i % 1000} 7 ${i % 97 ? "INFO" : "ERROR"} shop odoo.http: request ${i}`,
    );
    const started = performance.now();
    const entries = appendLines([], many);
    const minutes = perMinute(entries);
    expect(entries).toHaveLength(100_000);
    expect(minutes.length).toBeGreaterThan(0);
    expect(performance.now() - started).toBeLessThan(3000);
  });
});
