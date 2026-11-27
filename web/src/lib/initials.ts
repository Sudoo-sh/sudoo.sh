// The letters on an account's avatar (pages/Accounts.tsx, components/Shell.tsx).

/** The letters on an avatar: the first character of each name, whole (an emoji or an accented letter is never cut in
 *  half, as indexing a string by [0] would do with a character outside the Basic Multilingual Plane). */
export function initials(first: string | null | undefined, last?: string | null): string {
  const lead = (value: string | null | undefined) => Array.from((value ?? "").trim())[0] ?? "";
  return lead(first) + lead(last);
}
