// Small charts drawn as SVG: no chart library, a few hundred bytes each, smooth curves and a gradient fill.

import { useId, useMemo, useRef, useState } from "react";

type Point = [number, number];

/** A smooth path through the points (monotone cubic, so the curve never overshoots the values). */
function smooth(points: Point[]): string {
  if (points.length < 2) return points.length ? `M${points[0]![0]},${points[0]![1]}` : "";
  const d: string[] = [`M${points[0]![0]},${points[0]![1]}`];
  for (let i = 0; i < points.length - 1; i++) {
    const [x0, y0] = points[i - 1] ?? points[i]!;
    const [x1, y1] = points[i]!;
    const [x2, y2] = points[i + 1]!;
    const [x3, y3] = points[i + 2] ?? points[i + 1]!;
    const c1x = x1 + (x2 - x0) / 6;
    const c1y = y1 + (y2 - y0) / 6;
    const c2x = x2 - (x3 - x1) / 6;
    const c2y = y2 - (y3 - y1) / 6;
    const clamp = (v: number, a: number, b: number) => Math.min(Math.max(v, Math.min(a, b)), Math.max(a, b));
    d.push(`C${c1x},${clamp(c1y, y1, y2)} ${c2x},${clamp(c2y, y1, y2)} ${x2},${y2}`);
  }
  return d.join(" ");
}

function scale(values: (number | null)[], width: number, height: number, max?: number, pad = 2): Point[] {
  const numbers = values.map((v) => v ?? 0);
  const top = Math.max(max ?? 0, ...numbers, 1e-9);
  const step = values.length > 1 ? width / (values.length - 1) : width;
  return numbers.map((v, i) => [i * step, pad + (height - 2 * pad) * (1 - v / top)]);
}

export function Sparkline({ values, max, color = "var(--primary)", height = 54 }: { values: (number | null)[]; max?: number; color?: string; height?: number }) {
  const id = useId();
  const width = 300;
  const points = useMemo(() => scale(values.length ? values : [0, 0], width, height, max, 4), [values, max, height]);
  const line = smooth(points);
  return (
    <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" style={{ width: "100%", height }} aria-hidden="true">
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.28" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={`${line} L${width},${height} L0,${height} Z`} fill={`url(#${id})`} />
      <path d={line} fill="none" stroke={color} strokeWidth="1.8" vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
    </svg>
  );
}

/** A labelled chart with a time axis, value grid and a tooltip following the pointer. */
export function AreaChart({
  t,
  values,
  max,
  color = "var(--primary)",
  format,
  height = 180,
}: {
  t: number[];
  values: (number | null)[];
  max?: number;
  color?: string;
  format: (value: number) => string;
  height?: number;
}) {
  const id = useId();
  const ref = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<number | null>(null);
  const width = 600;
  const top = Math.max(max ?? 0, ...values.map((v) => v ?? 0)) * (max ? 1 : 1.15) || 1;
  const points = scale(values, width, height, top, 6);
  const line = smooth(points);
  const ticks = [0, 0.5, 1].map((f) => ({ y: 6 + (height - 12) * (1 - f), label: format(top * f) }));
  const time = (seconds: number) => new Date(seconds * 1000).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  const onMove = (event: React.PointerEvent) => {
    const box = ref.current?.getBoundingClientRect();
    if (!box || values.length < 2) return;
    const index = Math.round(((event.clientX - box.left) / box.width) * (values.length - 1));
    setHover(Math.max(0, Math.min(values.length - 1, index)));
  };
  if (values.length < 2) {
    return (
      <div className="chart" style={{ height, display: "grid", placeItems: "center" }}>
        <span className="faint small">Collecting data: a point every 15 seconds.</span>
      </div>
    );
  }
  const hovered = hover != null ? points[hover] : undefined;
  return (
    <div className="chart" ref={ref} onPointerMove={onMove} onPointerLeave={() => setHover(null)}>
      <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" style={{ height }}>
        <defs>
          <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={color} stopOpacity="0.32" />
            <stop offset="100%" stopColor={color} stopOpacity="0.02" />
          </linearGradient>
        </defs>
        {ticks.map((tick) => (
          <line key={tick.y} className="grid-line" x1="0" x2={width} y1={tick.y} y2={tick.y} vectorEffect="non-scaling-stroke" />
        ))}
        <path className="area" d={`${line} L${width},${height} L0,${height} Z`} fill={`url(#${id})`} />
        <path className="line" d={line} stroke={color} vectorEffect="non-scaling-stroke" />
        {hovered && <line className="cursor" x1={hovered[0]} x2={hovered[0]} y1="0" y2={height} vectorEffect="non-scaling-stroke" />}
      </svg>
      <div className="row xsmall faint" style={{ justifyContent: "space-between", marginTop: 6 }}>
        <span>{time(t[0]!)}</span>
        <span>{ticks[2]!.label} max</span>
        <span>{time(t[t.length - 1]!)}</span>
      </div>
      {hovered && hover != null && (
        <>
          <span
            style={{
              position: "absolute",
              left: `${(hovered[0] / width) * 100}%`,
              top: (hovered[1] / height) * height,
              width: 10,
              height: 10,
              marginLeft: -5,
              marginTop: -5,
              borderRadius: "50%",
              background: "var(--surface)",
              border: `2px solid ${color}`,
              pointerEvents: "none",
            }}
          />
          <div className="tip" style={{ left: `${(hovered[0] / width) * 100}%`, top: (hovered[1] / height) * height }}>
            <b>{values[hover] == null ? "—" : format(values[hover]!)}</b> · {time(t[hover]!)}
          </div>
        </>
      )}
    </div>
  );
}

export function Ring({ value, size = 64, tone, label }: { value: number | null | undefined; size?: number; tone?: string; label?: string }) {
  const radius = 26;
  const circumference = 2 * Math.PI * radius;
  const v = Math.max(0, Math.min(100, value ?? 0));
  const color = tone ?? (v > 85 ? "var(--danger)" : v > 65 ? "var(--warning)" : "var(--primary)");
  return (
    <div className="ring" style={{ ["--size" as string]: `${size}px`, ["--tone" as string]: color }}>
      <svg viewBox="0 0 64 64">
        <circle className="track" cx="32" cy="32" r={radius} />
        <circle className="value" cx="32" cy="32" r={radius} strokeDasharray={circumference} strokeDashoffset={circumference * (1 - v / 100)} />
      </svg>
      <span className="label">{label ?? `${Math.round(v)}%`}</span>
    </div>
  );
}
