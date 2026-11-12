// The live charts: Chart.js lines over the last hour, updated in place as each sample arrives on the event stream.
// A new sample glides in (the line moves continuously instead of jumping); colours come from the design tokens and
// follow the theme.

import {
  Chart,
  Filler,
  LinearScale,
  LineController,
  LineElement,
  PointElement,
  Tooltip,
  type ChartDataset,
  type ScriptableContext,
} from "chart.js";
import { useEffect, useRef } from "react";
import { alpha, token, useThemeVersion } from "./theme";

Chart.register(LineController, LineElement, PointElement, LinearScale, Filler, Tooltip);

export interface Series {
  label: string;
  values: (number | null)[];
  /** a custom property of the design tokens, like "--accent" */
  color: string;
  fill?: boolean;
}

const clock = (seconds: number, withSeconds = false) =>
  new Date(seconds * 1000).toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    ...(withSeconds ? { second: "2-digit" } : {}),
  });

/** Seconds on the axis while the chart covers only a few minutes, so the labels differ. */
const short = (t: number[]) => t.length > 1 && t[t.length - 1]! - t[0]! < 600;

export function LiveChart({
  t,
  series,
  format,
  max,
  height = 280,
}: {
  t: number[];
  series: Series[];
  format: (value: number) => string;
  max?: number;
  height?: number;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const chart = useRef<Chart<"line"> | null>(null);
  const theme = useThemeVersion();
  const latest = useRef({ t, series, format });
  latest.current = { t, series, format };

  const datasets = (): ChartDataset<"line">[] =>
    latest.current.series.map((s, index) => {
      const color = token(s.color) || "#714b67";
      return {
        label: s.label,
        data: latest.current.t.map((x, i) => ({ x, y: s.values[i] ?? null })) as never,
        borderColor: color,
        borderWidth: index === 0 ? 2 : 1.5,
        pointRadius: 0,
        pointHoverRadius: 4,
        pointHoverBackgroundColor: color,
        pointHoverBorderColor: token("--surface"),
        pointHoverBorderWidth: 2,
        tension: 0.35,
        spanGaps: true,
        fill: s.fill ? "origin" : false,
        backgroundColor: (context: ScriptableContext<"line">) => {
          const area = context.chart.chartArea;
          if (!area) return alpha(color, 0.12);
          const gradient = context.chart.ctx.createLinearGradient(0, area.top, 0, area.bottom);
          gradient.addColorStop(0, alpha(color, 0.18));
          gradient.addColorStop(1, alpha(color, 0));
          return gradient;
        },
      };
    });

  // create (again when the theme changes)
  useEffect(() => {
    if (!canvas.current) return;
    const muted = token("--text-3");
    const mono = token("--mono") || "monospace";
    chart.current = new Chart<"line">(canvas.current, {
      type: "line",
      data: { datasets: datasets() },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 300, easing: "easeOutQuart" },
        interaction: { mode: "index", intersect: false },
        scales: {
          x: {
            type: "linear",
            bounds: "data", // the line reaches both edges: the oldest and the newest sample
            grid: { display: false },
            border: { display: false },
            ticks: {
              color: muted,
              font: { family: mono, size: 11 },
              maxTicksLimit: 6,
              includeBounds: false, // no squeezed labels at the edges
              maxRotation: 0,
              callback: (v) => clock(Number(v), short(latest.current.t)),
            },
          },
          y: {
            beginAtZero: true,
            suggestedMax: max,
            grid: { color: token("--series-grid") },
            border: { display: false },
            ticks: {
              color: muted,
              font: { family: mono, size: 11 },
              maxTicksLimit: 5,
              callback: (v) => latest.current.format(Number(v)),
            },
          },
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: token("--text"),
            titleColor: token("--bg"),
            bodyColor: token("--bg"),
            titleFont: { family: mono, size: 11, weight: "normal" },
            bodyFont: { size: 12 },
            padding: 10,
            cornerRadius: 8,
            boxWidth: 8,
            boxHeight: 2,
            callbacks: {
              title: (items) => clock(Number(items[0]?.parsed.x ?? 0), true),
              label: (item) =>
                ` ${item.dataset.label}: ${item.parsed.y == null ? "—" : latest.current.format(item.parsed.y)}`,
            },
          },
        },
      },
    });
    return () => {
      chart.current?.destroy();
      chart.current = null;
    };
  }, [theme, max]); // eslint-disable-line react-hooks/exhaustive-deps

  // new samples: change the data in place; the existing points glide to their new places
  useEffect(() => {
    const current = chart.current;
    if (!current) return;
    const fresh = datasets();
    fresh.forEach((dataset, i) => {
      const existing = current.data.datasets[i];
      if (existing) existing.data = dataset.data;
      else current.data.datasets.push(dataset);
    });
    current.data.datasets.length = fresh.length;
    const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
    current.update(reduce ? "none" : undefined);
  }, [t, series]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div style={{ position: "relative", height }}>
      <canvas ref={canvas} role="img" aria-label={series.map((s) => s.label).join(", ")} />
      {t.length < 2 && <div className="chart-empty">Collecting the first figures</div>}
    </div>
  );
}
