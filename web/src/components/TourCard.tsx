import { forwardRef } from "react";
import { Link } from "react-router";
import type tourWords from "@/i18n/tour";
import { manualPath, type TourStepDef } from "@/lib/tour";
import { Button } from "./ui/button";

type Words = (typeof tourWords)["en"];

/** One step's popover. Drawn by the tour; also rendered alone in the tests. */
export const TourCard = forwardRef<
  HTMLDivElement,
  {
    step: TourStepDef;
    index: number;
    total: number;
    words: Words;
    at: { left: number; top: number } | null;
    onBack: () => void;
    onNext: () => void;
    onSkip: () => void;
    onLearn: () => void;
  }
>(function TourCard({ step, index, total, words, at, onBack, onNext, onSkip, onLearn }, ref) {
  const text = words.steps[step.id as keyof Words["steps"]];
  const last = index === total - 1;
  return (
    <div
      ref={ref}
      role="dialog"
      aria-label={words.label}
      data-tour-popover
      tabIndex={-1}
      style={{
        position: "fixed",
        zIndex: 60,
        width: 320,
        maxWidth: "calc(100vw - 16px)",
        left: at?.left ?? -9999,
        top: at?.top ?? 0,
        background: "var(--card, #fff)",
        color: "var(--foreground, #111)",
        border: "1px solid var(--border, #ddd)",
        borderRadius: 12,
        padding: 16,
        boxShadow: "0 8px 30px rgba(0,0,0,.18)",
      }}
    >
      <div style={{ fontSize: 12, opacity: 0.7 }}>{words.stepOf(index + 1, total)}</div>
      <h2 style={{ fontSize: 16, fontWeight: 600, margin: "4px 0" }}>{text.title}</h2>
      <p style={{ fontSize: 14, margin: "0 0 8px" }}>{text.body}</p>
      <Link to={manualPath(step.doc)} onClick={onLearn} style={{ fontSize: 14, textDecoration: "underline" }}>
        {words.learnMore}
      </Link>
      <div style={{ display: "flex", justifyContent: "space-between", marginTop: 12 }}>
        <Button type="button" variant="ghost" onClick={onSkip}>
          {words.skip}
        </Button>
        <span style={{ display: "flex", gap: 8 }}>
          {index > 0 && (
            <Button type="button" variant="outline" onClick={onBack}>
              {words.back}
            </Button>
          )}
          <Button type="button" onClick={onNext}>
            {last ? words.done : words.next}
          </Button>
        </span>
      </div>
    </div>
  );
});
