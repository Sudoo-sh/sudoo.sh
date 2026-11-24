// Where the value of a horizontal bar is written: inside the bar, at its end, when the text fits there; otherwise just
// past the end, with a gap. Works for bars that grow to the right (left-to-right pages) and to the left (right-to-left
// pages, where the chart is mirrored). Pure, so it is tested on its own (bar-labels.test.ts).

/** Space between the text and the end of the bar, inside or outside it. */
export const LABEL_GAP = 6;

export interface LabelPlace {
  /** where the text is anchored, in canvas pixels */
  x: number;
  /** canvas textAlign: always "left" or "right" (never start/end, which follow the page's direction) */
  align: "left" | "right";
  inside: boolean;
}

/**
 * @param base  where the bar starts (the zero of the scale)
 * @param end   where the bar ends (its value)
 * @param width the width of the text
 * @param mirrored the chart grows leftwards (right-to-left pages); decides only for a bar of zero length
 */
export function placeLabel(base: number, end: number, width: number, mirrored = false, gap = LABEL_GAP): LabelPlace {
  const length = Math.abs(end - base);
  const rightwards = end === base ? !mirrored : end > base;
  const inside = length >= width + gap * 2;
  if (inside) return { x: rightwards ? end - gap : end + gap, align: rightwards ? "right" : "left", inside };
  return { x: rightwards ? end + gap : end - gap, align: rightwards ? "left" : "right", inside };
}

/** The room the widest label needs past the end of the longest bar, so it is never cut off. */
export function trailingRoom(width: number, gap = LABEL_GAP): number {
  return Math.ceil(width + gap * 2);
}

/** WCAG relative luminance of an sRGB colour given as [r, g, b] in 0-255. */
function luminance([r, g, b]: [number, number, number]): number {
  const channel = (value: number) => {
    const c = value / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

export function contrast(a: [number, number, number], b: [number, number, number]): number {
  const [light, dark] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (light + 0.05) / (dark + 0.05);
}

/** Of the candidate text colours, the one that reads best on the bar's colour. */
export function readableOn<T extends { rgb: [number, number, number] }>(
  fill: [number, number, number],
  candidates: T[],
): T {
  return candidates.reduce((best, next) => (contrast(fill, next.rgb) > contrast(fill, best.rgb) ? next : best));
}

/**
 * `text`, shortened with an ellipsis at its end until `measure` says it fits in `maxWidth` (the names beside the bars,
 * which Chart.js would otherwise cut off at the canvas edge).
 */
export function fitText(text: string, maxWidth: number, measure: (value: string) => number): string {
  if (measure(text) <= maxWidth) return text;
  let low = 0;
  let high = text.length;
  // the longest prefix that fits with the ellipsis
  while (low < high) {
    const middle = Math.ceil((low + high) / 2);
    if (measure(`${text.slice(0, middle)}…`) <= maxWidth) low = middle;
    else high = middle - 1;
  }
  return low > 0 ? `${text.slice(0, low)}…` : "…";
}
