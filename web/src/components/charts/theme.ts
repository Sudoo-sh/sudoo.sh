// Colours for the charts, read from the design tokens, and a counter that changes with the theme so charts redraw.

import { useEffect, useState } from "react";

export const token = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

export function alpha(color: string, amount: number): string {
  const hex = color.replace("#", "");
  if (hex.length !== 6) return color;
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(hex.slice(i, i + 2), 16));
  return `rgb(${r} ${g} ${b} / ${amount})`;
}

/** Changes when the theme or the brand colour changes, so charts pick up the new colours. */
export function useThemeVersion(): number {
  const [version, setVersion] = useState(0);
  useEffect(() => {
    const bump = () => setVersion((v) => v + 1);
    const observer = new MutationObserver(bump);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme", "style"] });
    const media = matchMedia("(prefers-color-scheme: dark)");
    media.addEventListener("change", bump);
    return () => {
      observer.disconnect();
      media.removeEventListener("change", bump);
    };
  }, []);
  return version;
}
