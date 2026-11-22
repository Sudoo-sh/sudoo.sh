import { describe, expect, it } from "vitest";
import { rank, score, scoreWord } from "./fuzzy";

describe("fuzzy matching", () => {
  it("finds letters in order, not necessarily together", () => {
    expect(scoreWord("acmprd", "acme-prod")).not.toBeNull();
    expect(scoreWord("dorp", "acme-prod")).toBeNull();
  });

  it("prefers the start of the text, then the start of a word, then anywhere", () => {
    const start = scoreWord("acme", "acme-prod")!;
    const word = scoreWord("prod", "acme-prod")!;
    const inside = scoreWord("rod", "acme-prod")!;
    expect(start).toBeGreaterThan(word);
    expect(word).toBeGreaterThan(inside);
  });

  it("needs every word typed, in any of the texts", () => {
    expect(score("logs acme", ["Logs of acme-prod", "instance"])).not.toBeNull();
    expect(score("logs zebra", ["Logs of acme-prod"])).toBeNull();
  });

  it("ranks the best match first and keeps the order of ties", () => {
    const items = ["Settings", "Acme ERP", "acme-staging", "Audit log"];
    expect(rank(items, "acme", (item) => [item])).toEqual(["Acme ERP", "acme-staging"]);
    expect(rank(items, "", (item) => [item], 2)).toEqual(["Settings", "Acme ERP"]);
    expect(rank(items, "st", (item) => [item])[0]).toBe("acme-staging");
  });

  it("is not fooled by case", () => {
    expect(scoreWord("erp", "Acme ERP")).not.toBeNull();
  });
});
