import { OTPInputContext, REGEXP_ONLY_DIGITS } from "input-otp";
import { useContext } from "react";
import { InputOTP, InputOTPGroup, InputOTPSeparator, InputOTPSlot } from "@/components/ui/input-otp";
import { useT } from "@/i18n";
import auth from "@/i18n/auth";

/**
 * A 6-digit code in six boxes, grouped 3 + 3 (shadcn's input-otp). Underneath it is one field with one label,
 * autocomplete="one-time-code" and a numeric keyboard, so code suggestions, password managers and pasting the whole
 * code work; digits run left to right in Arabic too. A typed digit pops in; `shake` shakes the boxes once (no motion
 * when less is asked for; styles/_look.scss).
 */
export function OtpInput({ value, onChange, onComplete, shake, label }: { value: string; onChange: (value: string) => void; onComplete?: (value: string) => void; shake?: boolean; label?: string }) {
  const fallback = useT(auth).login.code;
  return (
    <div className="otp" dir="ltr" data-shake={shake || undefined}>
      <InputOTP
        maxLength={6}
        pattern={REGEXP_ONLY_DIGITS}
        pasteTransformer={(text) => text.replace(/\D/g, "")}
        inputMode="numeric"
        autoComplete="one-time-code"
        autoFocus
        aria-label={label ?? fallback}
        value={value}
        onChange={onChange}
        onComplete={onComplete}
        noScriptCSSFallback={null}
      >
        <Boxes from={0} to={3} />
        <InputOTPSeparator />
        <Boxes from={3} to={6} />
      </InputOTP>
    </div>
  );
}

/** A group of boxes; a box is drawn again when its digit changes, so the new digit pops in. */
function Boxes({ from, to }: { from: number; to: number }) {
  const slots = useContext(OTPInputContext)?.slots ?? [];
  return (
    <InputOTPGroup>
      {Array.from({ length: to - from }, (_, n) => {
        const index = from + n;
        const char = slots[index]?.char ?? "";
        return <InputOTPSlot key={`${index}:${char}`} index={index} data-filled={char ? "" : undefined} />;
      })}
    </InputOTPGroup>
  );
}
