// A ring of parts of a whole, with the total in the middle and the parts listed beside it.

import { ArcElement, Chart, DoughnutController, Tooltip } from "chart.js";
import { useEffect, useRef } from "react";
import { token, useThemeVersion } from "./theme";

Chart.register(DoughnutController, ArcElement, Tooltip);

export interface Part {
  label: string;
  value: number;
  /** a custom property of the design tokens, like "--positive" */
  color: string;
}

export function Ring({ parts, total, caption }: { parts: Part[]; total: number; caption: string }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const chart = useRef<Chart<"doughnut"> | null>(null);
  const theme = useThemeVersion();
  const shown = parts.some((p) => p.value > 0) ? parts : [{ label: "None", value: 1, color: "--hairline" }];

  useEffect(() => {
    if (!canvas.current) return;
    chart.current = new Chart<"doughnut">(canvas.current, {
      type: "doughnut",
      data: { labels: [], datasets: [{ data: [], borderWidth: 0, spacing: 2, borderRadius: 3 }] },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: "74%",
        animation: { duration: 400, easing: "easeOutQuart" },
        plugins: {
          legend: { display: false },
          tooltip: { backgroundColor: token("--text"), titleColor: token("--bg"), bodyColor: token("--bg"), padding: 10, cornerRadius: 8, displayColors: false },
        },
      },
    });
    return () => {
      chart.current?.destroy();
      chart.current = null;
    };
  }, [theme]);

  useEffect(() => {
    const current = chart.current;
    if (!current) return;
    current.data.labels = shown.map((p) => p.label);
    current.data.datasets[0]!.data = shown.map((p) => p.value);
    current.data.datasets[0]!.backgroundColor = shown.map((p) => token(p.color));
    current.update(matchMedia("(prefers-reduced-motion: reduce)").matches ? "none" : undefined);
  }, [parts, theme]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="ring">
      <div className="ring-chart">
        <canvas ref={canvas} role="img" aria-label={parts.map((p) => `${p.label} ${p.value}`).join(", ")} />
        <div className="ring-total">
          <b>{total}</b>
          <span>{caption}</span>
        </div>
      </div>
      <ul className="ring-legend">
        {parts.map((p) => (
          <li key={p.label}>
            <i style={{ background: `var(${p.color})` }} />
            <span className="spacer">{p.label}</span>
            <b>{p.value}</b>
          </li>
        ))}
      </ul>
    </div>
  );
}
