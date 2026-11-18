// A small round button that brings a long page back to its top. It appears once the page is scrolled down by about
// a screen, sits at the bottom of the inline-end side (the left in Arabic), and keeps out of the way of toasts, the
// Settings save bar, drawers and full-screen views (styles/_layout.scss). Smooth scroll, instant under reduced motion.

import { ArrowUp } from "lucide-react";
import { useEffect, useState } from "react";
import { useT } from "@/i18n";
import common from "@/i18n/common";

export function BackToTop() {
  const [shown, setShown] = useState(false);
  const label = useT(common).shell.backToTop;
  useEffect(() => {
    let frame = 0;
    const check = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => setShown(window.scrollY > window.innerHeight * 0.9));
    };
    check();
    window.addEventListener("scroll", check, { passive: true });
    window.addEventListener("resize", check);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("scroll", check);
      window.removeEventListener("resize", check);
    };
  }, []);
  const toTop = () => {
    const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
    window.scrollTo({ top: 0, behavior: reduce ? "auto" : "smooth" });
    // keyboard users continue from the top of the page, not from the button that disappears
    document.querySelector<HTMLElement>("main.page")?.focus({ preventScroll: true });
  };
  return (
    <button type="button" className="back-to-top" data-shown={shown || undefined} aria-hidden={!shown} tabIndex={shown ? 0 : -1}
      aria-label={label} title={label} onClick={toTop}>
      <ArrowUp aria-hidden="true" />
    </button>
  );
}
