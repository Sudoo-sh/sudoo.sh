// A choice on a list (a filter, a stage) kept in this browser, so the list is as the person left it. Only a value
// the page still offers is used again; anything else, or a browser that refuses storage, gives the starting value.
import { useCallback, useState } from "react";

const PREFIX = "sudoo.list.";

export function readRemembered<T extends string>(key: string, allowed: readonly T[], start: T): T {
  try {
    const value = localStorage.getItem(PREFIX + key);
    return allowed.includes(value as T) ? (value as T) : start;
  } catch {
    return start;
  }
}

export function writeRemembered(key: string, value: string, start: string) {
  try {
    if (value === start) localStorage.removeItem(PREFIX + key);
    else localStorage.setItem(PREFIX + key, value);
  } catch {
    /* the choice just is not kept */
  }
}

/** Like useState, starting from the remembered value (or `first` when one is given, as from a link) and keeping each change. */
export function useRemembered<T extends string>(key: string, allowed: readonly T[], start: T, first?: T) {
  const [value, set] = useState<T>(() => first ?? readRemembered(key, allowed, start));
  const change = useCallback(
    (next: T) => {
      set(next);
      writeRemembered(key, next, start);
    },
    [key, start],
  );
  return [value, change] as const;
}
