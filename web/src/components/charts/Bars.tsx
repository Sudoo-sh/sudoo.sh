// Horizontal bars, one per instance, largest first: which instances use the most of something right now. Each value
// is written inside its bar when it fits there, otherwise just past the bar's end (bar-labels.ts). On right-to-left
// pages the chart is mirrored: names on the right, bars growing to the left.

import { BarController, BarElement, CategoryScale, Chart, LinearScale, Tooltip } from "chart.js";
import { useEffect, useRef } from "react";
import { getLocale, useLocale } from "@/i18n";
import { fitText, placeLabel, readableOn, trailingRoom } from "./bar-labels";
import { token, useThemeVersion } from "./theme";

Chart.register(BarController, BarElement, CategoryScale, LinearScale, Tooltip);

export interface Bar {
  label: string;
  value: number;
}

const BAR_THICKNESS = 18;
const VALUE_FONT_PX = 11;
const NAME_FONT_PX = 12;
/**
 * The names beside the bars take at most this share of the chart's width. Chart.js gives the axis at most half of the
 * width left after the padding and cuts longer names off at the canvas edge, so they are shortened to fit first.
 */
const NAME_SHARE = 0.4;
const NAME_SLACK = 10;

/** A CSS colour as [r, g, b] (0-255), resolved by the browser (hex, rgb(), named, color-mix()...). */
function rgbOf(color: string): [number, number, number] {
  const probe = document.createElement("canvas").getContext("2d", { willReadFrequently: true });
  if (!probe) return [0, 0, 0];
  probe.canvas.width = probe.canvas.height = 1;
  probe.fillStyle = "#000";
  probe.fillStyle = color;
  probe.fillRect(0, 0, 1, 1);
  const [r, g, b] = probe.getImageData(0, 0, 1, 1).data;
  return [r ?? 0, g ?? 0, b ?? 0];
}

export function Bars({
  bars,
  format,
  max,
  onPick,
}: {
  bars: Bar[];
  format: (value: number) => string;
  max?: number;
  onPick?: (label: string) => void;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const chart = useRef<Chart<"bar"> | null>(null);
  const theme = useThemeVersion();
  const locale = useLocale();
  const latest = useRef({ bars, format, onPick });
  /** the padding past the longest bar (for its value), which the names' room depends on */
  const room = useRef(0);
  latest.current = { bars, format, onPick };

  useEffect(() => {
    if (!canvas.current) return;
    const accent = token("--brand");
    const quiet = token("--text-3");
    const mirrored = getLocale() === "ar";
    const font = `${VALUE_FONT_PX}px ${token("--mono") || "monospace"}`;
    const nameFamily = token("--font") || "sans-serif";
    // the value's colour inside a bar: whichever of the page's text colours reads best on that bar; outside, the
    // quiet text colour of the card
    const inks = [token("--on-brand"), token("--text"), token("--bg"), "#ffffff", "#000000"]
      .filter(Boolean)
      .map((color) => ({ color, rgb: rgbOf(color) }));
    const insideInk = { accent: readableOn(rgbOf(accent), inks).color, quiet: readableOn(rgbOf(quiet), inks).color };
    chart.current = new Chart<"bar">(canvas.current, {
      type: "bar",
      data: {
        labels: [],
        datasets: [{ data: [], borderRadius: 5, borderSkipped: false, barThickness: BAR_THICKNESS }],
      },
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
          (event.native?.target as HTMLElement | undefined)?.style.setProperty(
            "cursor",
            elements.length && latest.current.onPick ? "pointer" : "default",
          );
        },
        scales: {
          x: { display: false, beginAtZero: true, suggestedMax: max, reverse: mirrored },
          y: {
            position: mirrored ? "right" : "left",
            grid: { display: false },
            border: { display: false },
            ticks: {
              color: token("--text-2"),
              font: { size: NAME_FONT_PX, family: nameFamily },
              callback(_, index) {
                const label = latest.current.bars[index]?.label ?? "";
                const { ctx } = this;
                ctx.save();
                ctx.font = `${NAME_FONT_PX}px ${nameFamily}`;
                const width = Math.min(
                  this.chart.width * NAME_SHARE,
                  (this.chart.width - room.current) / 2 - NAME_SLACK,
                );
                const fitted = fitText(label, width, (value) => ctx.measureText(value).width);
                ctx.restore();
                return fitted;
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
            rtl: getLocale() === "ar",
            textDirection: getLocale() === "ar" ? "rtl" : "ltr",
            callbacks: { label: (item) => latest.current.format(Number(item.parsed.x)) },
          },
        },
      },
      plugins: [
        {
          // the value of each bar: inside it at its end when it fits, otherwise past its end
          id: "values",
          afterDatasetsDraw(current) {
            const { ctx } = current;
            ctx.save();
            ctx.font = font;
            ctx.textBaseline = "middle";
            current.getDatasetMeta(0).data.forEach((element, i) => {
              const value = latest.current.bars[i]?.value;
              if (value == null) return;
              const text = latest.current.format(value);
              const { base } = element.getProps(["base"]) as { base: number };
              const place = placeLabel(base, element.x, ctx.measureText(text).width, mirrored);
              ctx.fillStyle = place.inside ? (i === 0 ? insideInk.accent : insideInk.quiet) : token("--text-2");
              ctx.textAlign = place.align;
              // "1.7 GB" stays in that order on Arabic pages; a value with Arabic words in it reads right to left
              ctx.direction = /[\u0600-\u06ff]/.test(text) ? "rtl" : "ltr";
              ctx.fillText(text, place.x, element.y);
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
  }, [theme, max, locale]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const current = chart.current;
    if (!current) return;
    current.data.labels = bars.map((b) => b.label);
    current.data.datasets[0]!.data = bars.map((b) => b.value);
    // room past the longest bar for the widest value, in case it does not fit inside (a narrow card)
    const { ctx } = current;
    ctx.save();
    ctx.font = `${VALUE_FONT_PX}px ${token("--mono") || "monospace"}`;
    const widest = Math.max(0, ...bars.map((b) => ctx.measureText(format(b.value)).width));
    ctx.restore();
    room.current = trailingRoom(widest);
    current.options.layout = { padding: getLocale() === "ar" ? { left: room.current } : { right: room.current } };
    current.update(matchMedia("(prefers-reduced-motion: reduce)").matches ? "none" : undefined);
  }, [bars, theme, locale]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div style={{ position: "relative", height: Math.max(80, bars.length * 30 + 8) }}>
      {/* instance names are Latin branch names: drawn left to right on Arabic pages too (the ellipsis at their end) */}
      <canvas
        ref={canvas}
        dir="ltr"
        role="img"
        aria-label={bars.map((b) => `${b.label} ${format(b.value)}`).join(", ")}
      />
    </div>
  );
}
