// Fuzzy matching for the command palette: the letters typed must appear in order in the text, not necessarily next to
// each other ("acmprd" finds "acme-prod"). A match scores higher when the letters follow each other, start words, or
// start the text; the best matches come first. Several words typed ("logs acme") must each match.

const WORD_START = /[\s\-_./:]/;

/** How well `query` (one word, lower case) matches `text`; null when it does not. Higher is better. */
export function scoreWord(query: string, text: string): number | null {
  if (!query) return 0;
  const haystack = text.toLowerCase();
  const direct = haystack.indexOf(query);
  if (direct >= 0) {
    // the whole word appears as written: best at the start, then at a word's start
    const atWord = direct === 0 || WORD_START.test(haystack[direct - 1]!);
    return 1000 - direct + (direct === 0 ? 500 : atWord ? 250 : 0) + query.length * 10;
  }
  let score = 0;
  let from = 0;
  let previous = -2;
  for (const char of query) {
    const at = haystack.indexOf(char, from);
    if (at < 0) return null;
    score += 10;
    if (at === previous + 1) score += 15; // next to the previous letter
    if (at === 0 || WORD_START.test(haystack[at - 1]!)) score += 20; // the start of a word
    score -= Math.min(at - from, 10); // letters skipped
    previous = at;
    from = at + 1;
  }
  return score;
}

/** The score of `query` (any number of words) against an item's texts, or null when a word matches none of them. */
export function score(query: string, texts: string[]): number | null {
  const words = query.toLowerCase().trim().split(/\s+/).filter(Boolean);
  let total = 0;
  for (const word of words) {
    let best: number | null = null;
    texts.forEach((text, index) => {
      const found = scoreWord(word, text);
      // the first text (the item's own label) counts more than its keywords
      if (found != null) best = Math.max(best ?? -Infinity, found - index * 50);
    });
    if (best == null) return null;
    total += best;
  }
  return total;
}

/** The items that match, best first (ties keep their order). */
export function rank<T>(items: T[], query: string, texts: (item: T) => string[], limit = 50): T[] {
  if (!query.trim()) return items.slice(0, limit);
  return items
    .map((item, index) => ({ item, index, value: score(query, texts(item)) }))
    .filter((found): found is { item: T; index: number; value: number } => found.value != null)
    .sort((a, b) => b.value - a.value || a.index - b.index)
    .slice(0, limit)
    .map((found) => found.item);
}
