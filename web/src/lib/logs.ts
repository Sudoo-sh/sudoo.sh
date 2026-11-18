// Reading log text line by line for the log views (components/LogView.tsx): each line gets a level from the standard
// Odoo log format (2026-11-18 06:34:19,123 1 WARNING db odoo.addons...: text) or from the markers the panel's jobs
// write (core/jobs.py: "== step", "$ command", "[warning] note", "ERROR: ..."). A line without a level of its own,
// such as the lines of a traceback, keeps the level of the line it continues. The text itself is never changed.

export type Level = "error" | "warning" | "success" | "info" | "debug";
/** How a line is drawn: its level, and for a job's log whether it is a step title or a command. */
export type Kind = "step" | "cmd" | null;
export interface LogLine {
  text: string;
  level: Level;
  kind: Kind;
}
/** A job's step and how it went, to colour its "== title" line (done: green, failed: red). */
export interface StepState {
  title: string;
  status: string;
}

const ODOO = /^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(?:,\d+)? +\d+ +(DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL|TEST)\b/;
const ODOO_LEVEL: Record<string, Level> = { DEBUG: "debug", INFO: "info", TEST: "info", WARNING: "warning", WARN: "warning", ERROR: "error", CRITICAL: "error" };
const NOTE = /^\[(info|warning|error|success|debug)\]/i;
const ERROR = /^(ERROR|FATAL|CRITICAL)\b|^Traceback \(most recent call last\)|^[\w.]*(Error|Exception): |\b(failed|refused|denied)\b/;
const WARNING = /^(WARNING|WARN)\b|^\w*Warning: /;
const SUCCESS = /\b(done|succeeded|successfully|completed|verified|up to date|healthy)\b|^(Backup written|Restored|Status: Downloaded)|\b(Started|Created)$|\bGET \S+ 200\b/i;
// a line that continues the one before (a traceback, an indented detail): it keeps that line's level
const CONTINUES = /^(\s+\S|File "|Traceback|[\w.]*(Error|Exception)(:|$))/;

/** The lines of `text` with their levels; `steps` (a job's) colours its step titles by how each step went. */
export function parseLog(text: string, steps?: StepState[]): LogLine[] {
  const status = new Map(steps?.map((s) => [s.title, s.status]));
  const lines = text.replace(/^\n+/, "").replace(/\s+$/, "").split("\n");
  const out: LogLine[] = new Array(lines.length);
  let carried: Level | null = null; // the level of the last Odoo line, for the lines that continue it
  for (let i = 0; i < lines.length; i++) {
    const raw = lines[i]!.replace(/\r$/, "");
    let level: Level = "info";
    let kind: Kind = null;
    const odoo = ODOO.exec(raw);
    const note = NOTE.exec(raw);
    if (odoo) {
      level = ODOO_LEVEL[odoo[1]!] ?? "info";
      carried = level;
    } else if (raw.startsWith("== ")) {
      kind = "step";
      const state = status.get(raw.slice(3).trim());
      level = state === "done" ? "success" : state === "failed" ? "error" : "info";
      carried = null;
    } else if (raw.startsWith("$ ")) {
      kind = "cmd";
      carried = null;
    } else if (note) {
      level = note[1]!.toLowerCase() as Level;
      carried = null;
    } else if (carried && carried !== "info" && CONTINUES.test(raw)) {
      level = carried;
    } else if (ERROR.test(raw)) {
      level = "error";
      carried = /^Traceback/.test(raw) ? "error" : null;
    } else if (WARNING.test(raw)) {
      level = "warning";
    } else if (SUCCESS.test(raw)) {
      level = "success";
    } else if (!CONTINUES.test(raw)) {
      carried = null;
    }
    out[i] = { text: raw, level, kind };
  }
  return out;
}

export type Filter = "all" | "error" | "warning" | "info" | "debug";

/** Whether a line shows under a filter: Info also shows the lines that say something went well. */
export const shows = (filter: Filter, line: LogLine) =>
  filter === "all" || line.level === filter || (filter === "info" && line.level === "success");
