// The log itself, as the Logs tab draws it (components/LogsPanel.tsx keeps the data): one row per entry, its parts
// in columns (time, process, level, database, logger, message), werkzeug's requests taken apart (method, path,
// status by class, timings) and a traceback as one tinted block under its line, folded on request. Only the rows in
// view are drawn (a virtual list: 100,000 lines scroll as smoothly as 100). Following keeps the latest row in view;
// scrolling up pauses it (the parent holds new lines aside and the pill brings them in). Also the per-minute strip of
// errors and warnings and the list of keyboard shortcuts. The log reads left to right in Arabic too.

import { useVirtualizer } from "@tanstack/react-virtual";
import { ArrowDown, Check, ChevronDown, Copy, Layers, Link2 } from "lucide-react";
import { forwardRef, memo, useCallback, useEffect, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useT } from "@/i18n";
import logWords from "@/i18n/logs";
import { number } from "@/lib/format";
import { entryText, perMinute, statusClass, type Level, type LogEntry, type Matcher } from "@/lib/logs";
import { Modal } from "./app/overlay";

/** The current match: which entry, which part of it ("message", "logger", "db", "time", "more:<n>") and where. */
export interface MatchAt {
  entry: number;
  part: string;
  start: number;
}

export interface LogSurfaceHandle {
  /** brings entry `index` into view (centred) */
  show: (index: number) => void;
  toEnd: () => void;
}

export type TextSize = "s" | "m" | "l";

interface SurfaceProps {
  entries: LogEntry[];
  matcher: Matcher;
  current: MatchAt | null;
  wrap: boolean;
  size: TextSize;
  following: boolean;
  /** the reader scrolled away from the end (false), or back to it (true) */
  onFollow: (following: boolean) => void;
  /** lines held while paused */
  pending: number;
  onJump: () => void;
  /** the reader is at the top and earlier lines exist */
  onTop?: () => void;
  top: ReactNode;
  empty: ReactNode;
  /** the entry a shared link points to */
  marked?: string | null;
  onLogger: (logger: string) => void;
  onLevel: (level: Level) => void;
  onCopy: (text: string, what: "line" | "traceback" | "link", id: string) => void;
  copied: string | null;
}

export const LogSurface = forwardRef<LogSurfaceHandle, SurfaceProps>(function LogSurface(props, ref) {
  const { entries, matcher, current, wrap, size, following, onFollow, pending, onJump, onTop, top, empty, marked, onLogger, onLevel, onCopy, copied } = props;
  const t = useT(logWords);
  const box = useRef<HTMLDivElement>(null);
  const [folded, setFolded] = useState<Set<string>>(() => new Set());
  const [chosen, setChosen] = useState<string | null>(null);
  const pointerDown = useRef(false);
  const rowHeight = size === "s" ? 19 : size === "l" ? 24 : 21;

  const virtual = useVirtualizer({
    count: entries.length,
    getScrollElement: () => box.current,
    estimateSize: (index) => {
      const entry = entries[index];
      return rowHeight * (1 + (entry && !folded.has(entry.id) ? entry.more.length : entry?.more.length ? 1 : 0)) + 4;
    },
    overscan: 12,
    getItemKey: (index) => entries[index]?.id ?? index,
  });

  useImperativeHandle(ref, () => ({
    show: (index) => virtual.scrollToIndex(index, { align: "center" }),
    toEnd: () => entries.length && virtual.scrollToIndex(entries.length - 1, { align: "end" }),
  }), [virtual, entries.length]);

  // following: the latest entry stays in view
  useLayoutEffect(() => {
    if (following && entries.length) virtual.scrollToIndex(entries.length - 1, { align: "end" });
  }, [following, entries, virtual]);

  // earlier entries were put before the first: the rows in view stay where they were
  const firstId = useRef<string | null>(null);
  useLayoutEffect(() => {
    const before = firstId.current;
    firstId.current = entries[0]?.id ?? null;
    if (!before || following || entries[0]?.id === before) return;
    const moved = entries.findIndex((entry) => entry.id === before);
    if (moved > 0) virtual.scrollToIndex(moved, { align: "start" });
  }, [entries, following, virtual]);

  // the current match comes to the middle of the log (its entry may be taller than the view: a long traceback)
  const currentKey = current ? `${current.entry}:${current.part}:${current.start}` : "";
  useEffect(() => {
    if (!currentKey) return;
    // once the virtual list has brought the entry in and measured it
    const centre = () => {
      const el = box.current;
      const mark = el?.querySelector<HTMLElement>("mark[data-current]");
      if (!el || !mark) return;
      const offset = mark.getBoundingClientRect().top - el.getBoundingClientRect().top;
      if (offset < 40 || offset > el.clientHeight - 40) el.scrollTop += offset - el.clientHeight / 2;
    };
    // twice: the list may still be measuring the rows it just brought in
    const timers = [setTimeout(centre, 180), setTimeout(centre, 450)];
    return () => timers.forEach(clearTimeout);
  }, [currentKey]);

  const atEnd = () => {
    const el = box.current;
    return !el || el.scrollHeight - el.scrollTop - el.clientHeight < 32;
  };
  const leave = () => {
    if (following && !atEnd()) onFollow(false);
  };
  const onScroll = () => {
    const el = box.current;
    if (!el) return;
    if (pointerDown.current) leave(); // the scroll bar is dragged
    if (!following && atEnd() && !pointerDown.current && el.scrollTop > 0) onFollow(true);
    if (el.scrollTop < 40 && onTop) onTop();
  };

  const toggleFold = useCallback((id: string) => setFolded((was) => {
    const next = new Set(was);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    return next;
  }), []);

  const items = virtual.getVirtualItems();
  return (
    <div className="logs-surface-wrap">
      <div
        ref={box}
        className="logs-surface"
        data-wrap={wrap || undefined}
        data-size={size}
        dir="ltr"
        tabIndex={0}
        role="log"
        aria-label={t.title}
        onScroll={onScroll}
        onWheel={(e) => e.deltaY < 0 && setTimeout(leave)}
        onTouchMove={() => setTimeout(leave)}
        onPointerDown={(e) => { pointerDown.current = e.target === e.currentTarget; }}
        onPointerUp={() => { pointerDown.current = false; }}
        onKeyDown={(e) => ["ArrowUp", "PageUp", "Home"].includes(e.key) && setTimeout(leave)}
      >
        {top}
        {!entries.length ? (
          <div className="logs-empty">{empty}</div>
        ) : (
          <div className="logs-rows" style={{ height: virtual.getTotalSize() }}>
            <div className="logs-window" style={{ transform: `translateY(${items[0]?.start ?? 0}px)` }}>
              {items.map((item) => {
                const entry = entries[item.index]!;
                return (
                  <div key={item.key} data-index={item.index} ref={virtual.measureElement}>
                    <Row
                      entry={entry}
                      matcher={matcher}
                      current={current && current.entry === item.index ? current : null}
                      folded={folded.has(entry.id)}
                      onFold={toggleFold}
                      chosen={chosen === entry.id}
                      onChoose={setChosen}
                      marked={marked === entry.id}
                      onLogger={onLogger}
                      onLevel={onLevel}
                      onCopy={onCopy}
                      copied={copied}
                    />
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>
      {!following && (
        <button type="button" className="logs-jump" onClick={onJump}>
          <ArrowDown aria-hidden="true" />
          {t.jumpToLatest}
          {pending > 0 && <span className="count">{t.newLines(number(pending))}</span>}
        </button>
      )}
    </div>
  );
});

// ---------------------------------------------------------------- one entry

/** `text` with the parts the search found marked; `offset`: where `text` starts in the part searched. */
function Marked({ text, ranges, offset = 0, current }: { text: string; ranges: [number, number][]; offset?: number; current: number | null }) {
  if (!ranges.length) return <>{text}</>;
  const out: ReactNode[] = [];
  let from = 0;
  for (const [s, e] of ranges) {
    const start = Math.max(s - offset, 0);
    const end = Math.min(e - offset, text.length);
    if (end <= start || start < from) continue;
    if (start > from) out.push(text.slice(from, start));
    out.push(<mark key={start} data-current={s === current || undefined}>{text.slice(start, end)}</mark>);
    from = end;
  }
  if (from < text.length) out.push(text.slice(from));
  return <>{out}</>;
}

interface RowProps {
  entry: LogEntry;
  matcher: Matcher;
  current: MatchAt | null;
  folded: boolean;
  onFold: (id: string) => void;
  chosen: boolean;
  onChoose: (id: string | null) => void;
  marked: boolean;
  onLogger: (logger: string) => void;
  onLevel: (level: Level) => void;
  onCopy: (text: string, what: "line" | "traceback" | "link", id: string) => void;
  copied: string | null;
}

const Row = memo(function Row({ entry, matcher, current, folded, onFold, chosen, onChoose, marked, onLogger, onLevel, onCopy, copied }: RowProps) {
  const t = useT(logWords);
  const ranges = (text: string) => (matcher.ok && !matcher.empty ? matcher.ranges(text) : []);
  const at = (part: string) => (current && current.part === part ? current.start : null);
  const level = entry.level;
  const message = entry.message;
  const found = ranges(message);
  const request = entry.request;
  const slice = (from: number, to: number) => <Marked text={message.slice(from, to)} ranges={found} offset={from} current={at("message")} />;
  const copyKey = (what: string) => `${what}:${entry.id}`;

  return (
    <div
      className="logs-row"
      data-level={level?.toLowerCase()}
      data-traceback={entry.traceback || undefined}
      data-chosen={chosen || undefined}
      data-marked={marked || undefined}
      onClick={(e) => {
        if ((e.target as HTMLElement).closest("button, a")) return;
        if (window.getSelection()?.toString()) return; // the reader is selecting text
        onChoose(chosen ? null : entry.id);
      }}
    >
      <div className="logs-line">
        {entry.time ? (
          <>
            <span className="c-time" title={entry.time}>
              <Marked text={entry.time.slice(11).replace(",", ".")} ranges={ranges(entry.time)} offset={11} current={at("time")} />
            </span>
            <span className="c-pid" title={t.columns.pid}>{entry.pid}</span>
            <button type="button" className="c-level" data-level={level?.toLowerCase()} onClick={() => level && onLevel(level)} title={level ? t.onlyLevel(t.levelNames[level] ?? level) : undefined}>
              {level === "WARNING" ? "WARN" : level === "CRITICAL" ? "CRIT" : level}
            </button>
            <span className="c-db" title={`${t.columns.db}: ${entry.db}`}>
              <Marked text={entry.db ?? ""} ranges={ranges(entry.db ?? "")} current={at("db")} />
            </span>
            <button type="button" className="c-logger" onClick={() => entry.logger && onLogger(entry.logger)} title={entry.logger ? t.onlyLogger(entry.logger) : undefined}>
              <Marked text={entry.logger ?? ""} ranges={ranges(entry.logger ?? "")} current={at("logger")} />
            </button>
            <span className="c-message">
              {request ? (
                <>
                  <span className="r-ip">{slice(0, request.at.method[0] - 1)}</span>
                  {message[request.at.method[0] - 1]}
                  <span className="r-method" data-method={request.method}>{slice(...request.at.method)}</span>
                  {" "}
                  <span className="r-path">{slice(...request.at.path)}</span>
                  {slice(request.at.path[1], request.at.status[0])}
                  <span className="r-status" data-class={statusClass(request.status)}>{slice(...request.at.status)}</span>
                  <span className="r-rest">{slice(request.at.status[1], message.length)}</span>
                  {request.total != null && (
                    <span className="r-time" data-slow={request.total >= 1 || undefined}>
                      {request.total >= 1 ? `${request.total.toFixed(2)} s` : `${Math.round(request.total * 1000)} ms`}
                    </span>
                  )}
                </>
              ) : (
                <Marked text={message} ranges={found} current={at("message")} />
              )}
            </span>
          </>
        ) : (
          <span className="c-message c-plain"><Marked text={message} ranges={found} current={at("message")} /></span>
        )}
        <span className="logs-actions">
          <button type="button" onClick={() => onCopy(entryText(entry), "line", entry.id)} title={t.copyLine} aria-label={t.copyLine}>
            {copied === copyKey("line") ? <Check /> : <Copy />}
          </button>
          {entry.traceback && (
            <button type="button" onClick={() => onCopy(entry.more.join("\n"), "traceback", entry.id)} title={t.copyTraceback} aria-label={t.copyTraceback}>
              {copied === copyKey("traceback") ? <Check /> : <Layers />}
            </button>
          )}
          {!entry.id.startsWith("~") && (
            <button type="button" onClick={() => onCopy("", "link", entry.id)} title={t.copyLink} aria-label={t.copyLink}>
              {copied === copyKey("link") ? <Check /> : <Link2 />}
            </button>
          )}
        </span>
      </div>
      {entry.more.length > 0 && (
        <div className="logs-more" data-folded={folded || undefined}>
          <button type="button" className="logs-fold" aria-expanded={!folded} onClick={() => onFold(entry.id)}>
            <ChevronDown aria-hidden="true" />
            {folded ? t.showTraceback(number(entry.more.length)) : entry.traceback ? t.hideTraceback : t.showTraceback(number(entry.more.length))}
          </button>
          {!folded &&
            entry.more.map((line, i) => (
              <div key={i} className="logs-more-line" data-exception={entry.traceback && i === entry.more.length - 1 ? true : undefined}>
                <Marked text={line || " "} ranges={ranges(line)} current={at(`more:${i}`)} />
              </div>
            ))}
        </div>
      )}
    </div>
  );
});

/** The parts of an entry the search looks in, in the order they are drawn (for stepping through the matches). */
export function searchParts(entry: LogEntry): [string, string][] {
  const parts: [string, string][] = [];
  if (entry.time) parts.push(["time", entry.time], ["db", entry.db ?? ""], ["logger", entry.logger ?? ""]);
  parts.push(["message", entry.message]);
  entry.more.forEach((line, i) => parts.push([`more:${i}`, line]));
  return parts;
}

// ---------------------------------------------------------------- errors and warnings per minute

export function Timeline({ entries, onJump }: { entries: LogEntry[]; onJump: (index: number) => void }) {
  const t = useT(logWords);
  const minutes = useMemo(() => perMinute(entries), [entries]);
  const peak = Math.max(1, ...minutes.map((m) => m.errors + m.warnings));
  const total = minutes.reduce((sum, m) => sum + m.errors + m.warnings, 0);
  if (!minutes.length) return null;
  const clock = (at: number) => new Date(at).toISOString().slice(11, 16);
  return (
    <div className="logs-timeline" role="group" aria-label={t.timeline}>
      <div className="logs-timeline-head">
        <span>{t.timeline}</span>
        <span className="legend"><i data-kind="error" />{t.errorsLegend}</span>
        <span className="legend"><i data-kind="warning" />{t.warningsLegend}</span>
        <span className="hint">{total ? t.timelineHint : t.quiet}</span>
      </div>
      <div className="logs-bars" dir="ltr">
        {minutes.map((m) => {
          const label = t.minute(`${clock(m.at)} UTC`, number(m.errors), number(m.warnings));
          const busy = m.errors + m.warnings > 0;
          return (
            <button key={m.at} type="button" className="bar" tabIndex={busy ? 0 : -1} title={label} aria-label={label} onClick={() => onJump(m.first)}>
              <span className="fill" style={{ height: `${Math.max(busy ? 12 : 4, ((m.errors + m.warnings) / peak) * 100)}%` }}>
                {m.warnings > 0 && <span className="w" style={{ flexGrow: m.warnings }} />}
                {m.errors > 0 && <span className="e" style={{ flexGrow: m.errors }} />}
              </span>
            </button>
          );
        })}
      </div>
      <div className="logs-timeline-axis" dir="ltr">
        <span>{clock(minutes[0]!.at)}</span>
        <span>{clock(minutes[minutes.length - 1]!.at)} UTC</span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- shortcuts

export function Shortcuts({ onClose }: { onClose: () => void }) {
  const t = useT(logWords).keys;
  const rows: [string[], string][] = [
    [["/"], t.search],
    [["Enter", "Shift+Enter"], t.nextPrev],
    [["Alt+R"], t.regex],
    [["F"], t.follow],
    [["E"], t.errors],
    [["W"], t.wrap],
    [["Esc"], t.clear],
    [["?"], t.help],
  ];
  return (
    <Modal title={t.title} onClose={onClose}>
      <dl className="logs-keys">
        {rows.map(([keys, what]) => (
          <div key={what}>
            <dt>{keys.map((k) => <kbd key={k} dir="ltr">{k}</kbd>)}</dt>
            <dd>{what}</dd>
          </div>
        ))}
      </dl>
    </Modal>
  );
}

/** Whether a key press belongs to a field being typed in (the shortcuts leave it alone). */
export const typing = (target: EventTarget | null) =>
  target instanceof HTMLElement && (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName));

/** Runs `fn` on key presses on the page while mounted. */
export function useKeys(fn: (event: KeyboardEvent) => void) {
  const latest = useRef(fn);
  latest.current = fn;
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => latest.current(event);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
}
