/** The colour of a day's bar on a status page: green from 99.9 %, amber from 99 %, red below, grey without checks. */
export function dayTone(value: number | null | undefined): "up" | "degraded" | "down" | "none" {
  if (value == null) return "none";
  if (value >= 99.9) return "up";
  if (value >= 99) return "degraded";
  return "down";
}
