import { useEffect, useRef } from "react";

/** Six boxes for a one-time code: typing moves on, backspace moves back, pasting fills them all. */
export function OtpInput({ value, onChange, onComplete, shake }: { value: string; onChange: (value: string) => void; onComplete?: (value: string) => void; shake?: boolean }) {
  const refs = useRef<(HTMLInputElement | null)[]>([]);
  useEffect(() => refs.current[0]?.focus(), []);
  const set = (next: string) => {
    const clean = next.replace(/\D/g, "").slice(0, 6);
    onChange(clean);
    refs.current[Math.min(clean.length, 5)]?.focus();
    if (clean.length === 6) onComplete?.(clean);
  };
  return (
    <div className={`otp ${shake ? "shake" : ""}`}>
      {Array.from({ length: 6 }, (_, i) => (
        <span key={i} style={{ display: "contents" }}>
          {i === 3 && <span className="gap" />}
          <input
            ref={(el) => {
              refs.current[i] = el;
            }}
            inputMode="numeric"
            autoComplete={i === 0 ? "one-time-code" : "off"}
            aria-label={`Digit ${i + 1}`}
            placeholder=" "
            maxLength={1}
            value={value[i] ?? ""}
            onChange={(e) => {
              const digit = e.target.value.replace(/\D/g, "").slice(-1);
              if (!digit) return;
              set((value.slice(0, i) + digit + value.slice(i + 1)).slice(0, 6));
            }}
            onKeyDown={(e) => {
              if (e.key === "Backspace") {
                e.preventDefault();
                const next = value[i] ? value.slice(0, i) + value.slice(i + 1) : value.slice(0, Math.max(0, i - 1));
                onChange(next);
                refs.current[Math.max(0, value[i] ? i : i - 1)]?.focus();
              } else if (e.key === "ArrowLeft") refs.current[i - 1]?.focus();
              else if (e.key === "ArrowRight") refs.current[i + 1]?.focus();
            }}
            onPaste={(e) => {
              e.preventDefault();
              set(e.clipboardData.getData("text"));
            }}
          />
        </span>
      ))}
    </div>
  );
}
