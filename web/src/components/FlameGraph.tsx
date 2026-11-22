// The profiler's flame graph (an icicle, the root on top, like speedscope's "left heavy" view): drawn on a canvas,
// since a recording has thousands of calls. Each bar is a function; its width is the share of the samples spent in
// it and in what it called. Click a bar to zoom into it (its callers stay above, full width), click a caller to
// zoom out; hover for details; the search highlights matching functions and says how much time they take. Code
// reads left to right in every language, so the graph itself stays left to right in Arabic too. The table of the
// functions that took the most time (in the Tools tab) is the accessible way to the same answer.

import { RotateCcw, Search } from "lucide-react";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { ProfileNode, ProfileTree } from "@/api/types";
import { Button } from "@/components/app/basics";
import { SelectBox } from "@/components/app/select";
import { Input } from "@/components/ui/input";
import { useT } from "@/i18n";
import words from "@/i18n/profiler";

const ROW = 20;
const KINDS = ["odoo", "addons", "sql", "python", "other"] as const;
type Kind = (typeof KINDS)[number];

interface Node {
  name: string;
  where: string;
  value: number;
  depth: number;
  parent: Node | null;
  children: Node[];
  kind: Kind;
}

/** What kind of code a frame is, for its colour: the database layer, Odoo's core, add-ons, Python itself, other. */
export function kindOf(name: string, where: string): Kind {
  if (/sql_db\.py|psycopg/.test(where) || /^[Cc]ursor\.execute/.test(name)) return "sql";
  if (/(^|\/)addons\/|odoo\/addons\//.test(where) && !/^odoo\/(models|fields|api|http|service|tools|modules|osv)\b/.test(where)) return "addons";
  if (/^odoo\//.test(where)) return "odoo";
  if (/^[a-z_]+(\/|\.py)/.test(where) || where.startsWith("<")) return "python";
  return "other";
}

function build(source: ProfileNode, depth = 0, parent: Node | null = null): Node {
  const node: Node = { name: source.n, where: source.w ?? "", value: source.v, depth, parent, children: [], kind: kindOf(source.n, source.w ?? "") };
  node.children = (source.c ?? []).map((child) => build(child, depth + 1, node));
  return node;
}

function maxDepth(node: Node): number {
  return node.children.reduce((deepest, child) => Math.max(deepest, maxDepth(child)), node.depth);
}

interface Placed {
  node: Node;
  x: number; // 0..1 of the width
  w: number;
  row: number;
}

/** Bars to draw for `focus` inside `root`: its callers full width, then its calls scaled to the full width. */
function place(focus: Node): Placed[] {
  const placed: Placed[] = [];
  const chain: Node[] = [];
  for (let n: Node | null = focus.parent; n; n = n.parent) chain.unshift(n);
  chain.forEach((node, row) => placed.push({ node, x: 0, w: 1, row }));
  const top = chain.length;
  const walk = (node: Node, x: number, w: number, row: number) => {
    placed.push({ node, x, w, row });
    let offset = x;
    for (const child of node.children) {
      const share = (child.value / node.value) * w;
      walk(child, offset, share, row + 1);
      offset += share;
    }
  };
  walk(focus, 0, 1, top);
  return placed;
}

/** Whether a function's name or file contains the search (already in lower case). */
const matches = (n: Node, needle: string) => !!needle && (n.name.toLowerCase().includes(needle) || n.where.toLowerCase().includes(needle));

const percent = (part: number, whole: number) => `${whole ? ((100 * part) / whole).toFixed(part / whole < 0.1 ? 1 : 0) : 0}%`;

export function FlameGraph({ tree, label }: { tree: ProfileTree; label: string }) {
  const t = useT(words);
  const [thread, setThread] = useState("");
  const [query, setQuery] = useState("");
  const root = useMemo(() => build(tree.root), [tree]);
  const start = useMemo(() => (thread ? (root.children.find((c) => c.name === thread) ?? root) : root), [root, thread]);
  const [focus, setFocus] = useState<Node>(start);
  useEffect(() => setFocus(start), [start]);
  const placed = useMemo(() => place(focus), [focus]);
  const rows = useMemo(() => Math.max(...placed.map((p) => p.row)) + 1, [placed]);
  const deepest = useMemo(() => maxDepth(root), [root]);
  const view = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [width, setWidth] = useState(0);
  const [tip, setTip] = useState<{ x: number; y: number; at: Placed } | null>(null);
  const needle = query.trim().toLowerCase();

  // samples in matching functions, each sample counted once (the outermost match)
  const matched = useMemo(() => {
    if (!needle) return 0;
    const sum = (n: Node): number => (matches(n, needle) ? n.value : n.children.reduce((s, c) => s + sum(c), 0));
    return sum(focus);
  }, [needle, focus]);

  useLayoutEffect(() => {
    const element = view.current;
    if (!element) return;
    const observer = new ResizeObserver(() => setWidth(element.clientWidth));
    observer.observe(element);
    setWidth(element.clientWidth);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const element = canvas.current;
    if (!element || !width) return;
    const ratio = window.devicePixelRatio || 1;
    const height = rows * ROW;
    element.width = Math.round(width * ratio);
    element.height = Math.round(height * ratio);
    element.style.width = `${width}px`;
    element.style.height = `${height}px`;
    const context = element.getContext("2d");
    if (!context) return;
    const style = getComputedStyle(element);
    const color = (kind: Kind) => style.getPropertyValue(`--flame-${kind}`).trim() || "#ddd";
    const text = style.getPropertyValue("--flame-text").trim() || "#111";
    const mark = style.getPropertyValue("--flame-match").trim() || "#f3c969";
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, width, height);
    context.font = `12px ${style.getPropertyValue("--mono") || "monospace"}`;
    context.textBaseline = "middle";
    for (const p of placed) {
      const x = p.x * width;
      const w = p.w * width;
      if (w < 0.6) continue;
      const y = p.row * ROW;
      const hit = matches(p.node, needle);
      context.globalAlpha = needle && !hit ? 0.35 : 1;
      context.fillStyle = hit ? mark : color(p.node.kind);
      context.fillRect(x, y, Math.max(w - 1, 0.6), ROW - 1);
      if (w > 28) {
        context.fillStyle = text;
        const room = w - 8;
        let shown = p.node.name;
        if (context.measureText(shown).width > room) {
          while (shown.length > 1 && context.measureText(`${shown}…`).width > room) shown = shown.slice(0, -1);
          shown = shown.length > 1 ? `${shown}…` : "";
        }
        if (shown) context.fillText(shown, x + 4, y + ROW / 2);
      }
    }
    context.globalAlpha = 1;
  }, [placed, rows, width, needle]);

  const at = (clientX: number, clientY: number): Placed | null => {
    const element = canvas.current;
    if (!element || !width) return null;
    const box = element.getBoundingClientRect();
    const fraction = (clientX - box.left) / width;
    const row = Math.floor((clientY - box.top) / ROW);
    return placed.find((p) => p.row === row && fraction >= p.x && fraction < p.x + p.w) ?? null;
  };

  const shown = focus.value;
  return (
    <div className="flame">
      <div className="flame-tools">
        {tree.threads.length > 1 && (
          <SelectBox size="sm" value={thread} onValueChange={setThread} aria-label={t.allThreads} dir="ltr">
            <option value="">{t.allThreads}</option>
            {tree.threads.map((th) => (
              <option key={th.name} value={th.name}>{`${th.name} (${percent(th.samples, tree.total)})`}</option>
            ))}
          </SelectBox>
        )}
        <div className="input-group">
          <Search />
          <Input dir="ltr" placeholder={t.search} value={query} onChange={(e) => setQuery(e.target.value)} aria-label={t.search} />
        </div>
        {focus !== start && (
          <Button size="sm" icon={<RotateCcw />} onClick={() => setFocus(start)}>{t.reset}</Button>
        )}
        {needle && <span className="small muted">{t.matches(percent(matched, shown))}</span>}
      </div>
      <div className="flame-legend" aria-hidden="true">
        {KINDS.map((kind) => (
          <span key={kind}><i style={{ background: `var(--flame-${kind})` }} />{t.legend[kind]}</span>
        ))}
        <span className="faint">{t.zoomHint}</span>
      </div>
      <div className="flame-view" ref={view} dir="ltr" onMouseLeave={() => setTip(null)}>
        <canvas
          ref={canvas}
          role="img"
          aria-label={`${label}. ${t.graphLabel(tree.total)}`}
          data-depth={deepest}
          onMouseMove={(e) => {
            const found = at(e.clientX, e.clientY);
            setTip(found ? { x: e.clientX, y: e.clientY, at: found } : null);
          }}
          onClick={(e) => {
            const found = at(e.clientX, e.clientY);
            if (found) setFocus(found.node === focus && focus.parent ? focus.parent : found.node);
          }}
        />
      </div>
      {tip && (
        <div className="flame-tip" style={{ left: Math.min(tip.x + 14, window.innerWidth - 300), top: tip.y + 16 }} dir="ltr">
          <b>{tip.at.node.name}</b>
          {tip.at.node.where && <div className="mono faint">{tip.at.node.where}</div>}
          <div>{t.sampleCount(tip.at.node.value)}</div>
          <div>{t.ofTotal(percent(tip.at.node.value, tree.total))}</div>
          {focus !== root && <div>{t.ofShown(percent(Math.min(tip.at.node.value, shown), shown))}</div>}
        </div>
      )}
    </div>
  );
}
