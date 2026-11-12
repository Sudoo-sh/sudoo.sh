// Horizontal bars, one per instance, largest first: which instances use the most of something right now.

import { BarController, BarElement, CategoryScale, Chart, LinearScale, Tooltip } from "chart.js";
import { useEffect, useRef } from "react";
import { token, useThemeVersion } from "./theme";

Chart.register(BarController, BarElement, CategoryScale, LinearScale, Tooltip);

export interface Bar {
  label: string;
  value: number;
}

export function Bars({ bars, format, max, onPick }: { bars: Bar[]; format: (value: number) => string; max?: number; onPick?: (label: string) => void }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const chart = useRef<Chart<"bar"> | null>(null);
  const theme = useThemeVersion();
  const latest = useRef({ bars, format, onPick });
  latest.current = { bars, format, onPick };

  useEffect(() => {
    if (!canvas.current) return;
    const accent = token("--accent");
    const quiet = token("--text-3");
    chart.current = new Chart<"bar">(canvas.current, {
      type: "bar",
      data: { labels: [], datasets: [{ data: [], borderRadius: 4, borderSkipped: false, barThickness: 10 }] },
      options: {
        indexAxis: "y",
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 300, easing: "easeOutQuart" },
        onClick: (_, elements) => {
          const index = elements[0]?.index;
          if (index != null) latest.current.onPick?.(latest.current.bars[index]!.label);
        },
        onHover: (event, elements) => {
          (event.native?.target as HTMLElement | undefined)?.style.setProperty("cursor", elements.length && latest.current.onPick ? "pointer" : "default");
        },
        scales: {
          x: { display: false, beginAtZero: true, suggestedMax: max },
          y: {
            grid: { display: false },
            border: { display: false },
            ticks: {
              color: token("--text-2"),
              font: { size: 12 },
              callback: (_, index) => {
                const label = latest.current.bars[index]?.label ?? "";
                return label.length > 22 ? `${label.slice(0, 21)}…` : label;
              },
            },
          },
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: token("--text"),
            titleColor: token("--bg"),
            bodyColor: token("--bg"),
            padding: 10,
            cornerRadius: 8,
            displayColors: false,
            callbacks: { label: (item) => latest.current.format(Number(item.parsed.x)) },
          },
        },
      },
      plugins: [
        {
          // the value at the end of each bar
          id: "values",
          afterDatasetsDraw(current) {
            const { ctx } = current;
            ctx.save();
            ctx.font = `11px ${token("--mono") || "monospace"}`;
            ctx.fillStyle = token("--text-2");
            ctx.textBaseline = "middle";
            current.getDatasetMeta(0).data.forEach((bar, i) => {
              const value = latest.current.bars[i]?.value;
              if (value != null) ctx.fillText(latest.current.format(value), bar.x + 6, bar.y);
            });
            ctx.restore();
          },
        },
      ],
    });
    chart.current.data.datasets[0]!.backgroundColor = (context) => (context.dataIndex === 0 ? accent : quiet);
    return () => {
      chart.current?.destroy();
      chart.current = null;
    };
  }, [theme, max]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const current = chart.current;
    if (!current) return;
    current.data.labels = bars.map((b) => b.label);
    current.data.datasets[0]!.data = bars.map((b) => b.value);
    current.options.layout = { padding: { right: 56 } };
    current.update(matchMedia("(prefers-reduced-motion: reduce)").matches ? "none" : undefined);
  }, [bars, theme]);

  return (
    <div style={{ position: "relative", height: Math.max(80, bars.length * 30 + 8) }}>
      <canvas ref={canvas} role="img" aria-label={bars.map((b) => `${b.label} ${format(b.value)}`).join(", ")} />
    </div>
  );
}
