import { useEffect, useState, type RefObject } from "react";

/**
 * True once the element has come within `margin` of the viewport, and from then on. The charts wait for it before
 * they create their Chart.js instance: one below the fold costs nothing while the page loads (each instance is a
 * long task on a slow phone), and is drawn just before it is scrolled to. Without IntersectionObserver, at once.
 */
export function useSeen(ref: RefObject<Element | null>, margin = "200px"): boolean {
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    const element = ref.current;
    if (seen || !element) return;
    if (typeof IntersectionObserver === "undefined") {
      setSeen(true);
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (!entries.some((entry) => entry.isIntersecting)) return;
        setSeen(true);
        observer.disconnect();
      },
      { rootMargin: margin },
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref, seen, margin]);
  return seen;
}
