// A small line inside a figure card: how the figure moved over the last hour. No axes, no labels; the card's figure
// says the value. Drawn with Chart.js like the large charts, updated in place as samples arrive.

import { Chart, Filler, LinearScale, LineController, LineElement, PointElement, type ScriptableContext } from "chart.js";
import { useEffect, useRef } from "react";
import { alpha, token, useThemeVersion } from "./theme";

Chart.register(LineController, LineElement, PointElement, LinearScale, Filler);

export function Sparkline({ t, values, color = "--accent", max }: { t: number[]; values: (number | null)[]; color?: string; max?: number }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const chart = useRef<Chart<"line"> | null>(null);
  const theme = useThemeVersion();
  const points = () => t.map((x, i) => ({ x, y: values[i] ?? null }));

  useEffect(() => {
    if (!canvas.current) return;
    const stroke = token(color) || "#714b67";
    chart.current = new Chart<"line">(canvas.current, {
      type: "line",
      data: {
        datasets: [
          {
            data: points() as never,
            borderColor: stroke,
            borderWidth: 1.5,
            pointRadius: 0,
            tension: 0.35,
            spanGaps: true,
            fill: "origin",
            backgroundColor: (context: ScriptableContext<"line">) => {
              const area = context.chart.chartArea;
              if (!area) return alpha(stroke, 0.1);
              const gradient = context.chart.ctx.createLinearGradient(0, area.top, 0, area.bottom);
              gradient.addColorStop(0, alpha(stroke, 0.16));
              gradient.addColorStop(1, alpha(stroke, 0));
              return gradient;
            },
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 300, easing: "easeOutQuart" },
        events: [],
        layout: { padding: { top: 2, bottom: 1 } },
        scales: {
          x: { type: "linear", display: false, bounds: "data" },
          y: { display: false, beginAtZero: true, suggestedMax: max },
        },
        plugins: { legend: { display: false }, tooltip: { enabled: false } },
      },
    });
    return () => {
      chart.current?.destroy();
      chart.current = null;
    };
  }, [theme, color, max]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const current = chart.current;
    if (!current) return;
    current.data.datasets[0]!.data = points() as never;
    current.update(matchMedia("(prefers-reduced-motion: reduce)").matches ? "none" : undefined);
  }, [t, values]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="sparkline" aria-hidden="true">
      <canvas ref={canvas} />
    </div>
  );
}
