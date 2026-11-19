import { useSyncExternalStore } from "react";

/** Whether a media query matches, kept up to date (a window resized or a phone turned). */
export function useMedia(query: string): boolean {
  return useSyncExternalStore(
    (change) => {
      const list = matchMedia(query);
      list.addEventListener("change", change);
      return () => list.removeEventListener("change", change);
    },
    () => matchMedia(query).matches,
  );
}

/** A phone-sized screen (below 640px): dialogs come up from the bottom edge there (a drawer). */
export const PHONE = "(max-width: 639px)";
/** Up to 960px the sidebar is a sheet opened from the top bar (styles/_layout.scss has the same breakpoint). */
export const NARROW = "(max-width: 960px)";
