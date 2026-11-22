// A log as people read it: every line coloured by its level (lib/logs.ts), a filter by level, a search that highlights
// its matches and steps through them, following new lines unless the reader scrolled up, copy, and a full-screen view
// (Esc leaves it). Logs stay left to right in Arabic. Only the last lines are drawn at first (more on request), so a
// long log stays quick; the search and the filter work on all of it.

import { ArrowDown, Check, ChevronDown, ChevronUp, Copy, Maximize2, Minimize2, Pause, Play, Search, X } from "lucide-react";
import { useCallback, useDeferredValue, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useT } from "@/i18n";
import words from "@/i18n/instance";
import toolWords from "@/i18n/tools";
import { number } from "@/lib/format";
import { parseLog, shows, type Filter, type LogLine, type StepState } from "@/lib/logs";
import { Button } from "./app/basics";

const PAGE = 2000; // lines drawn at once; "Show earlier lines" draws this many more

type Match = { line: number; start: number };

function Highlighted({ text, query, current }: { text: string; query: string; current: number | null }) {
  if (!query) return <>{text || " "}</>;
  const lower = text.toLowerCase();
  const parts: ReactNode[] = [];
  let from = 0;
  let at = lower.indexOf(query);
  while (at !== -1) {
    if (at > from) parts.push(text.slice(from, at));
    parts.push(
      <mark key={at} className={at === current ? "current" : undefined} data-current={at === current || undefined}>
        {text.slice(at, at + query.length)}
      </mark>,
    );
    from = at + query.length;
    at = lower.indexOf(query, from);
  }
  if (from < text.length) parts.push(text.slice(from));
  return <>{parts.length ? parts : " "}</>;
}

/** A followed log's pause (components/LogsPanel.tsx): while paused, new lines wait in the parent, counted here. */
export interface LogPause {
  paused: boolean;
  /** lines that arrived while paused */
  pending: number;
  onChange: (paused: boolean) => void;
}

export function LogView({ text, live, steps, empty, tools, wrap = true, onQuery, pause }: {
  text: string;
  /** new lines are still coming (a running job): a cursor blinks at the end */
  live?: boolean;
  /** a job's steps, to colour each "== step" line by how it went */
  steps?: StepState[];
  /** shown when there is no text */
  empty?: string;
  /** more controls for the toolbar (the Odoo log's number of lines, Refresh) */
  tools?: ReactNode;
  /** long lines wrap (default), or the log scrolls sideways */
  wrap?: boolean;
  /** told what is typed in the search box (a followed log filters on the server too) */
  onQuery?: (typed: string) => void;
  /** the follow button pauses and resumes the parent's stream instead of only the scrolling */
  pause?: LogPause;
}) {
  const t = useT(words).logView;
  const tl = useT(toolWords).logs;
  const lines = useMemo(() => parseLog(text, steps), [text, steps]);
  const [filter, setFilter] = useState<Filter>("all");
  const [typed, setTyped] = useState("");
  const query = useDeferredValue(typed.trim().toLowerCase());
  const [follow, setFollow] = useState(true);
  const [full, setFull] = useState(false);
  const [copied, setCopied] = useState(false);
  const [limit, setLimit] = useState(PAGE);
  // which match is current, for one search and filter (another search starts at its first match)
  const [cursor, setCursor] = useState({ of: "", n: 0 });
  const box = useRef<HTMLDivElement>(null);
  const told = useRef(onQuery);
  told.current = onQuery;
  useEffect(() => told.current?.(typed), [typed]);
  // paused by the parent: the scrolling stops too; following again goes to the end
  const paused = pause?.paused ?? false;
  useEffect(() => setFollow(!paused), [paused]);

  const counts = useMemo(() => {
    const c = { error: 0, warning: 0, info: 0, debug: 0 };
    for (const line of lines) c[line.level === "success" ? "info" : line.level]++;
    return c;
  }, [lines]);
  const visible = useMemo(() => lines.map((line, index) => ({ line, index })).filter(({ line }) => shows(filter, line)), [lines, filter]);
  const matches = useMemo(() => {
    const found: Match[] = [];
    if (!query) return found;
    visible.forEach(({ line }, i) => {
      const lower = line.text.toLowerCase();
      for (let at = lower.indexOf(query); at !== -1; at = lower.indexOf(query, at + query.length)) found.push({ line: i, start: at });
    });
    return found;
  }, [visible, query]);
  const searchKey = `${filter}\u0000${query}`;
  const current = cursor.of === searchKey ? cursor.n : 0;

  // draw only the last `limit` lines, unless the current match is earlier
  const match = matches[Math.min(current, matches.length - 1)];
  const first = Math.max(0, Math.min(visible.length - limit, match ? match.line : Infinity));
  const drawn = visible.slice(first);

  // follow the end while following (and not searching); the reader scrolling up stops following
  useLayoutEffect(() => {
    const el = box.current;
    if (!el || !follow || query) return;
    el.scrollTop = el.scrollHeight;
  }, [text, follow, query, filter, full, drawn.length]);
  // the current match comes into the middle of the log (the log scrolls, never the page)
  useEffect(() => {
    const el = box.current;
    const mark = el?.querySelector<HTMLElement>("mark[data-current]");
    if (!el || !mark) return;
    const top = el.scrollTop + mark.getBoundingClientRect().top - el.getBoundingClientRect().top - el.clientHeight / 2;
    el.scrollTo({ top: Math.max(0, top), behavior: "auto" });
  }, [current, matches, full]);
  const onScroll = () => {
    const el = box.current;
    if (!el) return;
    // at the end: follow new lines; anywhere else (the reader scrolled up, or a match was shown): pause
    const atEnd = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
    if (atEnd !== follow) setFollow(atEnd);
  };

  const step = useCallback(
    (by: number) => matches.length && setCursor({ of: searchKey, n: (current + by + matches.length) % matches.length }),
    [matches.length, searchKey, current],
  );
  const copy = async () => {
    const shown = filter === "all" ? text : visible.map(({ line }) => line.text).join("\n");
    await navigator.clipboard.writeText(shown).catch(() => undefined);
    setCopied(true);
    setTimeout(() => setCopied(false), 1400);
  };

  // full screen: Esc leaves it (and only it: a drawer around the log stays open); the page behind does not scroll
  useEffect(() => {
    if (!full) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      setFull(false);
    };
    window.addEventListener("keydown", onKey, true);
    const overflow = document.documentElement.style.overflow;
    document.documentElement.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey, true);
      document.documentElement.style.overflow = overflow;
    };
  }, [full]);

  const filters: { value: Filter; label: string; count?: number }[] = [
    { value: "all", label: t.all },
    { value: "error", label: t.errors, count: counts.error },
    { value: "warning", label: t.warnings, count: counts.warning },
    { value: "info", label: t.info, count: counts.info },
    { value: "debug", label: t.debug, count: counts.debug },
  ];
  const position = matches.length ? t.matchOf(Math.min(current, matches.length - 1) + 1, matches.length) : query ? t.noMatches : "";
  const currentLine = match?.line;

  const view = (
    <div className={`log-view ${full ? "log-fullscreen" : ""}`} role={full ? "dialog" : undefined} aria-modal={full || undefined} aria-label={full ? t.fullScreen : undefined}>
      <div className="log-toolbar">
        <div className="log-levels" role="group" aria-label={t.levels}>
          {filters.map((f) => (
            <button key={f.value} type="button" aria-pressed={filter === f.value} data-level={f.value} onClick={() => setFilter(f.value)}>
              {f.label}
              {f.count !== undefined && <span className="count">{number(f.count)}</span>}
            </button>
          ))}
        </div>
        <div className="log-search">
          <Search aria-hidden="true" />
          <input
            type="search"
            dir="ltr"
            value={typed}
            placeholder={t.search}
            aria-label={t.search}
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
        </div>
        <span className="spacer" />
        {tools}
        <div className="log-actions">
          {pause ? (
            <Button size="sm" variant={paused ? "ghost" : "default"} icon={paused ? <Play /> : <Pause />} aria-pressed={!paused}
              onClick={() => pause.onChange(!paused)} title={paused ? tl.pauseText : tl.followText}>
              {paused ? tl.follow : tl.pause}
            </Button>
          ) : (
            <Button size="sm" variant={follow ? "default" : "ghost"} icon={<ArrowDown />} aria-pressed={follow} onClick={() => setFollow(!follow)} title={follow ? t.followingText : t.pausedText}>
              {follow ? t.following : t.paused}
            </Button>
          )}
          <Button size="sm" variant="ghost" icon={copied ? <Check /> : <Copy />} onClick={copy} aria-label={copied ? t.copied : t.copy} title={copied ? t.copied : t.copy} />
          <Button size="sm" variant="ghost" icon={full ? <Minimize2 /> : <Maximize2 />} onClick={() => setFull(!full)} aria-label={full ? t.exitFullScreen : t.fullScreen} title={full ? t.exitFullScreen : t.fullScreen} />
        </div>
      </div>
      <div className={`terminal log-lines${wrap ? "" : " nowrap"}`} dir="ltr" ref={box} onScroll={onScroll} tabIndex={0} aria-label={t.log}>
        {first > 0 && (
          <button type="button" className="log-more" onClick={() => setLimit((l) => l + PAGE)}>
            {t.earlier(number(first))}
          </button>
        )}
        {!text.trim() ? (
          <div className="ln faint">{empty ?? t.empty}</div>
        ) : !drawn.length ? (
          <div className="ln faint">{t.noneAtLevel}</div>
        ) : (
          drawn.map(({ line, index }, i) => <Line key={index} line={line} query={query} current={currentLine === first + i ? match!.start : null} />)
        )}
        {live && <span className="cursor" />}
      </div>
      {paused && pause && pause.pending > 0 ? (
        <button type="button" className="log-jump" onClick={() => pause.onChange(false)}>
          <ArrowDown aria-hidden="true" /> {tl.newLines(number(pause.pending))}
        </button>
      ) : !follow && live && !query && (
        <button type="button" className="log-jump" onClick={() => setFollow(true)}>
          <ArrowDown aria-hidden="true" /> {t.jumpToEnd}
        </button>
      )}
    </div>
  );
  return full ? <>{createPortal(view, document.body)}<div className="log-placeholder" /></> : view;
}

function Line({ line, query, current }: { line: LogLine; query: string; current: number | null }) {
  return (
    <div className={`ln lvl-${line.level}${line.kind ? ` ${line.kind}` : ""}`}>
      <Highlighted text={line.text} query={query} current={current} />
    </div>
  );
}
