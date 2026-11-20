// Where a bar's value is written (bar-labels.ts): inside the bar at its end when it fits, otherwise past the end,
// for bars growing to the right and to the left (mirrored, right-to-left pages). Run with `npm test`.

import { describe, expect, test } from "vitest";
import { LABEL_GAP, contrast, fitText, placeLabel, readableOn, trailingRoom } from "./bar-labels";

describe("placeLabel", () => {
  test("inside a long bar growing to the right, right-aligned before its end", () => {
    expect(placeLabel(100, 400, 40)).toEqual({ x: 400 - LABEL_GAP, align: "right", inside: true });
  });

  test("past the end of a short bar growing to the right, left-aligned after a gap", () => {
    expect(placeLabel(100, 130, 40)).toEqual({ x: 130 + LABEL_GAP, align: "left", inside: false });
  });

  test("inside a long bar growing to the left (mirrored), left-aligned after its end", () => {
    expect(placeLabel(500, 200, 40, true)).toEqual({ x: 200 + LABEL_GAP, align: "left", inside: true });
  });

  test("past the end of a short bar growing to the left, right-aligned before it", () => {
    expect(placeLabel(500, 480, 40, true)).toEqual({ x: 480 - LABEL_GAP, align: "right", inside: false });
  });

  test("inside only with a gap on both sides of the text", () => {
    expect(placeLabel(0, 40 + LABEL_GAP * 2, 40).inside).toBe(true);
    expect(placeLabel(0, 40 + LABEL_GAP * 2 - 1, 40).inside).toBe(false);
  });

  test("a bar of zero length puts its value on the side the chart grows to", () => {
    expect(placeLabel(100, 100, 40)).toEqual({ x: 100 + LABEL_GAP, align: "left", inside: false });
    expect(placeLabel(500, 500, 40, true)).toEqual({ x: 500 - LABEL_GAP, align: "right", inside: false });
  });

  test("never uses start/end alignment, which would follow the page's direction", () => {
    for (const [base, end, mirrored] of [[0, 300, false], [0, 10, false], [300, 0, true], [300, 290, true]] as const) {
      expect(["left", "right"]).toContain(placeLabel(base, end, 40, mirrored).align);
    }
  });
});

describe("trailingRoom", () => {
  test("leaves the widest value and a gap on both sides past the longest bar", () => {
    expect(trailingRoom(50.2)).toBe(Math.ceil(50.2 + LABEL_GAP * 2));
  });
});

describe("readableOn", () => {
  const white = { name: "white", rgb: [255, 255, 255] as [number, number, number] };
  const ink = { name: "ink", rgb: [23, 23, 23] as [number, number, number] };

  test("white on the plum brand, dark ink on the light plum of the dark theme", () => {
    expect(readableOn([0x71, 0x4b, 0x67], [white, ink]).name).toBe("white");
    expect(readableOn([0xc7, 0x9b, 0xbd], [white, ink]).name).toBe("ink");
  });

  test("the chosen colour reads at least 4.5:1 on the quiet bars of both themes", () => {
    for (const grey of [[0x73, 0x73, 0x73], [0x85, 0x85, 0x8b]] as [number, number, number][]) {
      expect(contrast(grey, readableOn(grey, [white, ink]).rgb)).toBeGreaterThanOrEqual(4.5);
    }
  });
});

describe("fitText", () => {
  const measure = (value: string) => value.length * 7;

  test("keeps a name that fits", () => {
    expect(fitText("acme-main", 100, measure)).toBe("acme-main");
  });

  test("shortens a long name with an ellipsis at its end, within the width", () => {
    const fitted = fitText("nile-logistics-fleet-gps", 100, measure);
    expect(fitted).toBe("nile-logistic…");
    expect(measure(fitted)).toBeLessThanOrEqual(100);
  });

  test("an ellipsis alone when nothing else fits", () => {
    expect(fitText("acme", 7, measure)).toBe("…");
  });
});
