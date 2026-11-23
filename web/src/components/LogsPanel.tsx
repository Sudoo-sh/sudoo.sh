// An instance's Logs tab. odoo.log is followed live over Server-Sent Events (GET .../logs/stream, web/v1/log_stream.py)
// and read further back in pages (GET .../logs/entries?before=); install.log and update.log (Odoo's own output of
// module installs and updates, never the panel's commands) are read in pages. Levels, a logger, tracebacks, a time
// range and "only matching lines" filter on the server, over the whole log; the search highlights and steps through
// its matches in what is loaded. A link to one line (#line=<id>) opens the lines around it. Pausing (or scrolling up)
// holds new lines aside; the pill brings them in. Keyboard: / F E W Alt+R Esc ? (the list: "?").

import { useQuery } from "@tanstack/react-query";
import { ChevronDown, Clock, Download, Keyboard, Maximize2, Minimize2, Pause, Play, Regex, Search, Settings2, X, ChevronUp } from "lucide-react";
import { useCallback, useDeferredValue, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { api, errorText } from "@/api/client";
import type { Envelope } from "@/api/types";
import { useT } from "@/i18n";
import logWords from "@/i18n/logs";
import { number } from "@/lib/format";
import { activeFilters, appendLines, filterParams, fromServer, LEVELS, makeMatcher, NO_FILTERS, type Level, type LogEntry, type LogFilters, type TimeRange } from "@/lib/logs";
import { Button, Callout, Segmented, Switch } from "./app/basics";
import { Menu, MenuItem, MenuLabel, MenuSeparator, Modal, useToast } from "./app/overlay";
import { LogSurface, searchParts, Shortcuts, Timeline, typing, useKeys, type LogSurfaceHandle, type MatchAt, type TextSize } from "./LogView";

interface LogFile {
  name: string;
  started_at: string | null;
}
interface ServerEntry {
  id: string;
  text: string;
}

const KEEP_LINES = 100_000; // lines kept in the page; the oldest go first (the download has the rest)
const TAIL = 500; // lines sent first when the log is opened
const PAGE = 1000; // entries read at once further back
const VIEW_KEY = "sudoo.logs.view";
const ERRORS: Level[] = ["ERROR", "CRITICAL"];
const PROBLEMS: Level[] = ["WARNING", "ERROR", "CRITICAL"];

type State = "connecting" | "open" | "stopped" | "slow" | "failed";

function stored(): { wrap: boolean; size: TextSize } {
  try {
    const saved = JSON.parse(localStorage.getItem(VIEW_KEY) ?? "{}") as { wrap?: boolean; size?: TextSize };
    return { wrap: saved.wrap ?? true, size: saved.size ?? "m" };
  } catch {
    return { wrap: true, size: "m" };
  }
}

function useDebounced<T>(value: T, delay = 350): T {
  const [shown, setShown] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setShown(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return shown;
}

const lineCount = (entries: LogEntry[]) => entries.reduce((n, e) => n + 1 + e.more.length, 0);
/** The entries, without the oldest beyond KEEP_LINES lines. */
function kept(entries: LogEntry[]): LogEntry[] {
  let lines = lineCount(entries);
  if (lines <= KEEP_LINES) return entries;
  let drop = 0;
  while (lines > KEEP_LINES && drop < entries.length - 1) lines -= 1 + entries[drop++]!.more.length;
  return entries.slice(drop);
}

/** The line a shared link points to: "#log=odoo.log&line=<id>". */
function linked(): { log: string; line: string } | null {
  const params = new URLSearchParams(window.location.hash.slice(1));
  const line = params.get("line");
  return line ? { log: params.get("log") ?? "odoo.log", line } : null;
}

const localInput = (iso: string) => {
  const d = new Date(iso);
  return new Date(d.getTime() - d.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
};

export function LogsPanel({ name }: { name: string }) {
  const t = useT(logWords);
  const toast = useToast();
  const files = useQuery({
    queryKey: ["log-files", name],
    queryFn: () => api.get<LogFile[]>(`/instances/${name}/logs/files`),
    refetchInterval: 30_000,
  });
  const link = useMemo(linked, []);
  const [log, setLog] = useState(link?.log ?? "odoo.log");
  const [context, setContext] = useState<string | null>(link?.line ?? null);
  const [marked, setMarked] = useState<string | null>(null);
  const [filters, setFilters] = useState<LogFilters>(NO_FILTERS);
  const [typed, setTyped] = useState("");
  const query = useDeferredValue(typed);
  const sentQuery = useDebounced(typed);
  const serverFilters = useMemo(() => ({ ...filters, query: sentQuery }), [filters, sentQuery]);
  const matcher = useMemo(() => makeMatcher(query, filters.regex), [query, filters.regex]);
  const sendable = makeMatcher(sentQuery, filters.regex).ok;
  const serverKey = sendable ? JSON.stringify(filterParams(serverFilters, 0)) : "refused";

  const [view, setView] = useState(stored);
  const [entries, setEntries] = useState<LogEntry[]>([]);
  const [state, setState] = useState<State>("connecting");
  const [following, setFollowing] = useState(true);
  const [pendingCount, setPendingCount] = useState(0);
  const [hasBefore, setHasBefore] = useState(false);
  const [loadingEarlier, setLoadingEarlier] = useState(false);
  const [full, setFull] = useState(false);
  const [keys, setKeys] = useState(false);
  const [custom, setCustom] = useState<{ since: string; until: string } | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const pending = useRef<{ lines: string[]; ids: string[] }>({ lines: [], ids: [] });
  const followingNow = useRef(true);
  followingNow.current = following;
  const surface = useRef<LogSurfaceHandle>(null);
  const searchBox = useRef<HTMLInputElement>(null);
  const jobLog = log !== "odoo.log";

  useEffect(() => {
    try {
      localStorage.setItem(VIEW_KEY, JSON.stringify(view));
    } catch {
      // a private window: the choice lasts for this page only
    }
  }, [view]);

  const add = useCallback((lines: string[], ids: string[] | undefined) => {
    if (!lines.length) return;
    if (!followingNow.current) {
      pending.current.lines.push(...lines);
      pending.current.ids.push(...(ids ?? lines.map(() => "")));
      setPendingCount(pending.current.lines.length);
      return;
    }
    setEntries((before) => kept(appendLines(before, lines, ids)));
  }, []);

  // the log: followed (odoo.log), read in a page (install.log, update.log) or around a shared line
  useEffect(() => {
    if (serverKey === "refused") return;
    const params = JSON.parse(serverKey) as Record<string, string>;
    // a time range counts from now, when the log is opened
    Object.assign(params, filterParams({ ...NO_FILTERS, range: filters.range }));
    let ended = false;
    let source: EventSource | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    setEntries([]);
    pending.current = { lines: [], ids: [] };
    setPendingCount(0);
    setHasBefore(false);
    setState("connecting");
    // another log or other filters: a new reading, from its latest lines
    followingNow.current = !jobLog && !context;
    setFollowing(followingNow.current);
    if (jobLog || context) {
      const query = new URLSearchParams({ log, limit: String(jobLog ? 2000 : 300), ...params, ...(context ? { around: context } : {}) });
      api.page<ServerEntry[]>(`/instances/${name}/logs/entries?${query}`)
        .then((page: Envelope<ServerEntry[]>) => {
          if (ended) return;
          const got = fromServer(page.data);
          setEntries(got);
          setHasBefore(Boolean(page.meta?.has_before));
          setState("open");
          setFollowing(!context);
          if (context) {
            // the line itself, or the first after it when it is no longer in the log
            const exact = got.findIndex((e) => e.id === context);
            const at = exact >= 0 ? exact : got.findIndex((e) => e.id > context);
            if (exact < 0) toast("info", t.notFound);
            setMarked(got[at]?.id ?? null);
            if (at >= 0) setTimeout(() => surface.current?.show(at), 120);
          }
        })
        .catch((error: unknown) => {
          if (ended) return;
          setState("failed");
          toast("error", errorText(error));
        });
      return () => {
        ended = true;
      };
    }
    let lastId = "";
    const open = (after?: string) => {
      const query = new URLSearchParams({ log, tail: after ? "0" : String(TAIL), ...params });
      if (after) query.set("after", after);
      const current = new EventSource(`/api/v1/instances/${name}/logs/stream?${query}`);
      source = current;
      current.onopen = () => setState((s) => (s === "connecting" || s === "stopped" ? "open" : s));
      current.addEventListener("lines", (event) => {
        const message = event as MessageEvent<string>;
        if (message.lastEventId) lastId = message.lastEventId;
        const data = JSON.parse(message.data) as { lines: string[]; ids?: string[] };
        add(data.lines, data.ids);
        if (!after && data.lines.length) setHasBefore(true);
        setState("open");
      });
      current.addEventListener("end", (event) => {
        const message = event as MessageEvent<string>;
        if (message.lastEventId) lastId = message.lastEventId;
        const reason = (JSON.parse(message.data) as { reason: string }).reason;
        current.close();
        if (ended) return;
        if (reason === "max_duration") open(lastId || undefined);
        else if (reason === "stopped") {
          setState("stopped");
          timer = setTimeout(() => open(lastId || undefined), 5000);
        } else if (reason === "slow_pattern") setState("slow");
      });
      current.onerror = () => {
        // EventSource tries again by itself (with Last-Event-ID) unless the server refused it: then later
        if (current.readyState === EventSource.CLOSED && !ended) timer = setTimeout(() => open(lastId || undefined), 10_000);
      };
    };
    open();
    return () => {
      ended = true;
      source?.close();
      clearTimeout(timer);
    };
    // filters.range is in serverKey; it is read again here to count from now
  }, [name, log, serverKey, context, add]);

  const loadEarlier = useCallback(() => {
    const first = entries[0];
    if (!hasBefore || loadingEarlier || !first || first.id.startsWith("~") || state === "connecting") return;
    setLoadingEarlier(true);
    const params = new URLSearchParams({ log, limit: String(PAGE), before: first.id, ...filterParams(serverFilters) });
    api.page<ServerEntry[]>(`/instances/${name}/logs/entries?${params}`)
      .then((page) => {
        const earlier = fromServer(page.data);
        setEntries((now) => (now[0]?.id === first.id ? [...earlier, ...now] : now));
        setHasBefore(Boolean(page.meta?.has_before));
      })
      .catch((error: unknown) => toast("error", errorText(error)))
      .finally(() => setLoadingEarlier(false));
  }, [entries, hasBefore, loadingEarlier, log, name, serverFilters, state, toast]);

  const setFollow = useCallback((next: boolean) => {
    setFollowing(next);
    if (next && pending.current.lines.length) {
      const { lines, ids } = pending.current;
      pending.current = { lines: [], ids: [] };
      setPendingCount(0);
      setEntries((before) => kept(appendLines(before, lines, ids)));
    }
  }, []);
  const jump = () => {
    setFollow(true);
    surface.current?.toEnd();
  };
  const backToLive = () => {
    history.replaceState(null, "", window.location.pathname + window.location.search);
    setContext(null);
    setFollowing(true);
  };

  // ---------------------------------------------------------------- search
  const matches = useMemo(() => {
    const found: MatchAt[] = [];
    if (!matcher.ok || matcher.empty) return found;
    for (let i = 0; i < entries.length && found.length < 50_000; i++)
      for (const [part, text] of searchParts(entries[i]!)) for (const [start] of matcher.ranges(text)) found.push({ entry: i, part, start });
    return found;
  }, [entries, matcher]);
  const [cursor, setCursor] = useState({ of: "", n: 0 });
  const searchKey = `${query}\u0000${filters.regex}\u0000${log}`;
  const currentIndex = cursor.of === searchKey ? Math.min(cursor.n, matches.length - 1) : matches.length - 1;
  const current = currentIndex >= 0 ? matches[currentIndex]! : null;
  const step = (by: number) => {
    if (!matches.length) return;
    // the first step goes to the latest match (the log reads downwards, the newest last), then steps from there
    const n = cursor.of !== searchKey ? matches.length - 1 : (currentIndex + by + matches.length) % matches.length;
    setCursor({ of: searchKey, n });
    setFollow(false);
    surface.current?.show(matches[n]!.entry);
  };

  // ---------------------------------------------------------------- filters
  const update = (change: Partial<LogFilters>) => setFilters((f) => ({ ...f, ...change }));
  const levelsShown = filters.levels.length ? filters.levels : LEVELS;
  const toggleLevel = (level: Level) => {
    const next = levelsShown.includes(level) ? levelsShown.filter((l) => l !== level) : [...levelsShown, level];
    update({ levels: next.length === LEVELS.length || !next.length ? [] : LEVELS.filter((l) => next.includes(l)) });
  };
  const same = (a: Level[], b: Level[]) => a.length === b.length && a.every((l) => b.includes(l));
  const focus = filters.tracebacks ? "tracebacks" : same(filters.levels, ERRORS) ? "errors" : same(filters.levels, PROBLEMS) ? "problems" : filters.levels.length ? "custom" : "all";
  const setFocus = (value: string) => {
    if (value === "tracebacks") update({ tracebacks: true, levels: [] });
    else update({ tracebacks: false, levels: value === "errors" ? ERRORS : value === "problems" ? PROBLEMS : [] });
  };
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const e of entries) c[e.level ?? "INFO"] = (c[e.level ?? "INFO"] ?? 0) + 1;
    return c;
  }, [entries]);
  const clearAll = () => {
    setFilters((f) => ({ ...NO_FILTERS, regex: f.regex }));
  };

  // ---------------------------------------------------------------- copying and links
  const copy = useCallback((text: string, what: "line" | "traceback" | "link", id: string) => {
    const value = what === "link" ? `${window.location.origin}${window.location.pathname}${window.location.search}#${new URLSearchParams({ log, line: id })}` : text;
    navigator.clipboard?.writeText(value).catch(() => undefined);
    setCopied(`${what}:${id}`);
    setTimeout(() => setCopied((c) => (c === `${what}:${id}` ? null : c)), 1400);
    if (what === "link") toast("success", t.linkCopied);
  }, [log, t.linkCopied, toast]);

  // ---------------------------------------------------------------- keyboard
  useKeys((event) => {
    if (event.defaultPrevented || keys || custom) return;
    if (document.querySelector('[role="dialog"]:not(.logs-full)')) return;
    if (event.altKey && event.code === "KeyR") {
      event.preventDefault();
      update({ regex: !filters.regex });
      return;
    }
    if (event.key === "Escape") {
      if (typed) setTyped("");
      else if (activeFilters(filters)) clearAll();
      else if (full) setFull(false);
      else return;
      event.preventDefault();
      return;
    }
    if (typing(event.target) || event.metaKey || event.ctrlKey || event.altKey) return;
    const action: Record<string, () => void> = {
      "/": () => searchBox.current?.focus(),
      "?": () => setKeys(true),
      f: () => (jobLog || context ? undefined : following ? setFollow(false) : jump()),
      e: () => setFocus(focus === "errors" ? "all" : "errors"),
      w: () => setView((v) => ({ ...v, wrap: !v.wrap })),
    };
    const run = action[event.key.length === 1 ? event.key.toLowerCase() : event.key];
    if (!run) return;
    event.preventDefault();
    run();
  });

  useEffect(() => {
    if (!full) return;
    const overflow = document.documentElement.style.overflow;
    document.documentElement.style.overflow = "hidden";
    return () => {
      document.documentElement.style.overflow = overflow;
    };
  }, [full]);

  // ---------------------------------------------------------------- drawing
  const available = files.data ?? [{ name: "odoo.log", started_at: null }];
  const base = `/api/v1/instances/${name}/logs/download?`;
  const downloadAll = base + new URLSearchParams({ log });
  const downloadShown = base + new URLSearchParams({ log, ...filterParams(serverFilters) });
  const patternProblem = matcher.ok ? null : t.pattern[matcher.reason];
  const position = matches.length ? t.matchOf(number(currentIndex + 1), number(matches.length)) : query.trim() && matcher.ok ? t.noMatches : "";
  const clock = (iso: string) => new Date(iso).toLocaleString(undefined, { dateStyle: "short", timeStyle: "short" });
  const rangeLabel = (r: TimeRange) =>
    r.kind === "all" ? t.anyTime : r.kind === "last" ? t.last[String(r.minutes)] ?? t.anyTime
      : r.since && r.until ? t.between(clock(r.since), clock(r.until)) : r.since ? t.since(clock(r.since)) : t.until(clock(r.until));

  const chips: { key: string; label: ReactNode; remove: () => void }[] = [];
  if (filters.levels.length) chips.push({ key: "levels", label: filters.levels.map((l) => t.levelNames[l]).join(" · "), remove: () => update({ levels: [] }) });
  if (filters.tracebacks) chips.push({ key: "tb", label: t.tracebacks, remove: () => update({ tracebacks: false }) });
  if (filters.logger) chips.push({ key: "logger", label: t.loggerChip(filters.logger), remove: () => update({ logger: "" }) });
  if (filters.range.kind !== "all") chips.push({ key: "time", label: rangeLabel(filters.range), remove: () => update({ range: { kind: "all" } }) });
  if (filters.onlyMatches && typed.trim()) chips.push({ key: "q", label: t.matching(typed.trim()), remove: () => update({ onlyMatches: false }) });

  const top = jobLog || context || !entries.length ? null : (
    <div className="logs-top">
      {loadingEarlier ? t.loadingEarlier : hasBefore ? (
        <button type="button" onClick={loadEarlier}>{t.loadEarlier}</button>
      ) : t.startOfLog}
    </div>
  );
  const emptyText =
    state === "connecting" ? t.connecting : state === "slow" ? t.slowPattern : activeFilters(filters) ? t.noMatch : jobLog ? t.emptyJob : t.empty;

  const panel = (
    <section className={`logs${full ? " logs-full" : ""}`} role={full ? "dialog" : undefined} aria-modal={full || undefined} aria-label={t.title}>
      <header className="logs-head">
        <div className="logs-picker" aria-label={t.which} role="group">
          <Segmented
            value={log}
            onChange={(next) => {
              setLog(next);
              if (context) backToLive();
            }}
            options={available.map((file) => ({ value: file.name, label: t.names[file.name] ?? file.name }))}
          />
        </div>
        <span className="logs-about">{t.about[log]}</span>
        <span className="spacer" />
        {!jobLog && !context && (
          <Button size="sm" variant={following ? "default" : "soft"} className="logs-follow" icon={following ? <Pause /> : <Play />} aria-pressed={following}
            onClick={() => (following ? setFollow(false) : jump())} title={following ? t.followText : t.pausedText}>
            {following ? t.following : t.paused}
            {!following && pendingCount > 0 && <span className="count">{t.newLines(number(pendingCount))}</span>}
          </Button>
        )}
        <Menu trigger={(open) => <Button size="sm" variant="ghost" icon={<Download />} onClick={open} aria-label={t.download} title={t.download} />}>
          {() => (
            <>
              <MenuLabel>{t.download}</MenuLabel>
              <MenuItem asChild><a href={downloadShown} download>{t.downloadShown}</a></MenuItem>
              <MenuItem asChild><a href={downloadAll} download>{t.downloadAll}</a></MenuItem>
            </>
          )}
        </Menu>
        <Menu trigger={(open) => <Button size="sm" variant="ghost" icon={<Settings2 />} onClick={open} aria-label={t.view} title={t.view} />}>
          {() => (
            <>
              <MenuItem onSelect={(e) => { e.preventDefault(); setView((v) => ({ ...v, wrap: !v.wrap })); }}>
                <span className="check" data-on={view.wrap || undefined} aria-hidden="true" />{t.wrap}
              </MenuItem>
              <MenuSeparator />
              <MenuLabel>{t.textSize}</MenuLabel>
              {(["s", "m", "l"] as TextSize[]).map((size) => (
                <MenuItem key={size} onSelect={(e) => { e.preventDefault(); setView((v) => ({ ...v, size })); }}>
                  <span className="check" data-on={view.size === size || undefined} aria-hidden="true" />{t.sizes[size]}
                </MenuItem>
              ))}
              <MenuSeparator />
              <MenuItem onSelect={() => setKeys(true)}><Keyboard />{t.shortcuts}<kbd className="hint">?</kbd></MenuItem>
            </>
          )}
        </Menu>
        <Button size="sm" variant="ghost" icon={full ? <Minimize2 /> : <Maximize2 />} onClick={() => setFull(!full)} aria-label={full ? t.exitFullScreen : t.fullScreen} title={full ? t.exitFullScreen : t.fullScreen} />
      </header>

      <div className="logs-filters">
        <div className="logs-search" data-invalid={patternProblem ? true : undefined}>
          <Search aria-hidden="true" />
          <input
            ref={searchBox}
            type="search"
            dir="ltr"
            value={typed}
            placeholder={t.searchPlaceholder}
            aria-label={t.search}
            aria-invalid={patternProblem ? true : undefined}
            maxLength={200}
            onChange={(e) => setTyped(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                step(e.shiftKey ? -1 : 1);
              }
            }}
          />
          <span className="position" aria-live="polite">{position}</span>
          {typed && <button type="button" className="icon" onClick={() => setTyped("")} aria-label={t.clear} title={t.clear}><X /></button>}
          <button type="button" className="icon" disabled={!matches.length} onClick={() => step(-1)} aria-label={t.previous} title={t.previous}><ChevronUp /></button>
          <button type="button" className="icon" disabled={!matches.length} onClick={() => step(1)} aria-label={t.next} title={t.next}><ChevronDown /></button>
          <button type="button" className="icon regex" aria-pressed={filters.regex} onClick={() => update({ regex: !filters.regex })} aria-label={t.regex} title={t.regex}><Regex /></button>
        </div>
        <Switch checked={filters.onlyMatches} onChange={(onlyMatches) => update({ onlyMatches })} label={t.onlyMatches} />
        <span className="spacer" />
        <Menu trigger={(open) => (
          <Button size="sm" variant={filters.range.kind === "all" ? "ghost" : "soft"} icon={<Clock />} onClick={open} title={t.time}>
            {rangeLabel(filters.range)}
          </Button>
        )}>
          {() => (
            <>
              <MenuLabel>{t.time}</MenuLabel>
              <MenuItem onSelect={() => update({ range: { kind: "all" } })}>{t.anyTime}</MenuItem>
              {[15, 60, 1440].map((minutes) => (
                <MenuItem key={minutes} onSelect={() => update({ range: { kind: "last", minutes } })}>{t.last[String(minutes)]}</MenuItem>
              ))}
              <MenuSeparator />
              <MenuItem onSelect={() => {
                const now = new Date();
                setCustom(filters.range.kind === "custom"
                  ? { since: filters.range.since ? localInput(filters.range.since) : "", until: filters.range.until ? localInput(filters.range.until) : "" }
                  : { since: localInput(new Date(now.getTime() - 3600_000).toISOString()), until: localInput(now.toISOString()) });
              }}>{t.custom}</MenuItem>
            </>
          )}
        </Menu>
      </div>
      {patternProblem && <p className="logs-problem" role="alert">{patternProblem}</p>}

      <div className="logs-levels-row">
        <div className="logs-focus" role="group" aria-label={t.quick}>
          {([["problems", t.problems], ["errors", t.errorsOnly], ["tracebacks", t.tracebacks]] as const).map(([value, label]) => (
            <button key={value} type="button" aria-pressed={focus === value} data-focus={value} onClick={() => setFocus(focus === value ? "all" : value)}>{label}</button>
          ))}
        </div>
        <div className="logs-levels" role="group" aria-label={t.levels}>
          {LEVELS.map((level) => (
            <button key={level} type="button" data-level={level.toLowerCase()} aria-pressed={levelsShown.includes(level)} onClick={() => toggleLevel(level)}>
              <i aria-hidden="true" />
              {t.levelNames[level]}
              <span className="count">{number(counts[level] ?? 0)}</span>
            </button>
          ))}
        </div>
      </div>

      <Timeline entries={entries} onJump={(index) => { setFollow(false); surface.current?.show(index); }} />

      {(chips.length > 0 || context) && (
        <div className="logs-active">
          {context ? (
            <>
              <span>{t.context}</span>
              <Button size="sm" variant="soft" onClick={backToLive}>{t.backToLive}</Button>
            </>
          ) : (
            <>
              <span className="label">{t.filteredBy}</span>
              {chips.map((chip) => (
                <span key={chip.key} className="chip">
                  {chip.label}
                  <button type="button" onClick={chip.remove} aria-label={t.remove} title={t.remove}><X /></button>
                </span>
              ))}
              <button type="button" className="clear" onClick={clearAll}>{t.clearAll}</button>
            </>
          )}
        </div>
      )}

      {state === "stopped" && <Callout tone="info">{t.stopped}</Callout>}
      <LogSurface
        ref={surface}
        entries={entries}
        matcher={matcher}
        current={current}
        wrap={view.wrap}
        size={view.size}
        following={following && !context && !jobLog}
        onFollow={(next) => !jobLog && !context && setFollow(next)}
        pending={pendingCount}
        onJump={jump}
        onTop={jobLog || context ? undefined : loadEarlier}
        top={top}
        empty={emptyText}
        marked={context ? marked : null}
        onLogger={(logger) => update({ logger })}
        onLevel={(level) => update({ levels: [level], tracebacks: false })}
        onCopy={copy}
        copied={copied}
      />
      {keys && <Shortcuts onClose={() => setKeys(false)} />}
      {custom && (
        <Modal title={t.customTitle} description={t.customText} onClose={() => setCustom(null)}
          footer={
            <>
              <Button variant="ghost" onClick={() => setCustom(null)}>{t.cancel}</Button>
              <Button variant="primary" disabled={!custom.since && !custom.until} onClick={() => {
                update({ range: { kind: "custom", since: custom.since ? new Date(custom.since).toISOString() : "", until: custom.until ? new Date(custom.until).toISOString() : "" } });
                setCustom(null);
              }}>{t.apply}</Button>
            </>
          }>
          <div className="logs-range">
            <label>{t.from}<input type="datetime-local" dir="ltr" value={custom.since} onChange={(e) => setCustom({ ...custom, since: e.target.value })} /></label>
            <label>{t.to}<input type="datetime-local" dir="ltr" value={custom.until} onChange={(e) => setCustom({ ...custom, until: e.target.value })} /></label>
          </div>
        </Modal>
      )}
    </section>
  );
  return full ? <>{createPortal(panel, document.body)}<div className="logs-placeholder" /></> : panel;
}
