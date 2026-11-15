// A small line inside a figure card: how the figure moved over the last hour. No axes, no labels; the card's figure
// says the value. Drawn with Chart.js like the large charts, updated in place as samples arrive.

import {
  Chart,
  Decimation,
  Filler,
  LinearScale,
  LineController,
  LineElement,
  PointElement,
  type ScriptableContext,
} from "chart.js";
import { useEffect, useRef } from "react";
import { alpha, token, useThemeVersion } from "./theme";

Chart.register(LineController, LineElement, PointElement, LinearScale, Filler, Decimation);

export function Sparkline({ t, values, color = "--accent", max }: { t: number[]; values: (number | null)[]; color?: string; max?: number }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const chart = useRef<Chart<"line"> | null>(null);
  const theme = useThemeVersion();
  const drawn = useRef<unknown[]>([]); // the data the chart was created with: not drawn a second time
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
            // fixed point colours: otherwise each of the hour's points asks the gradient function below for its own
            pointBackgroundColor: stroke,
            pointBorderColor: stroke,
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
        parsing: false, // the points are given as {x, y} already, in time order
        normalized: true,
        animation: false, // drawn at once; new samples glide in (below)
        events: [],
        layout: { padding: { top: 2, bottom: 1 } },
        scales: {
          x: { type: "linear", display: false, bounds: "data" },
          y: { display: false, beginAtZero: true, suggestedMax: max },
        },
        // an hour of samples on a few hundred pixels: drawn from at most 120 points that keep its shape (LTTB)
        plugins: {
          legend: { display: false },
          tooltip: { enabled: false },
          decimation: { enabled: true, algorithm: "lttb", samples: 120, threshold: 120 },
        },
      },
    });
    chart.current.options.animation = { duration: 300, easing: "easeOutQuart" };
    drawn.current = [t, values];
    return () => {
      chart.current?.destroy();
      chart.current = null;
    };
  }, [theme, color, max]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const current = chart.current;
    if (!current || (drawn.current[0] === t && drawn.current[1] === values)) return;
    // the first figures (the hour's history arriving) appear at once; later samples glide in
    const first = !current.data.datasets[0]!.data.length;
    current.data.datasets[0]!.data = points() as never;
    current.update(first || matchMedia("(prefers-reduced-motion: reduce)").matches ? "none" : undefined);
  }, [t, values]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="sparkline" aria-hidden="true">
      <canvas ref={canvas} />
    </div>
  );
}
