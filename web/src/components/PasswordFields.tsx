import { passwordProblem, strength, STRENGTH_LABELS, type Person } from "@/lib/password";
import { Field } from "./ui/basics";

/** A new password, typed twice, checked against the same rules as the server's while it is typed. */
export function PasswordFields({ value, onChange, person, label = "Password", autoFocus }: { value: [string, string]; onChange: (value: [string, string]) => void; person?: Person; label?: string; autoFocus?: boolean }) {
  const [first, again] = value;
  const mismatch = again.length > 0 && first !== again;
  const problem = first ? passwordProblem(first, person) : null;
  const level = first ? strength(first, person) : 0;
  return (
    <>
      <Field label={label} className="full" error={problem}
        hint={!problem && (first ? STRENGTH_LABELS[level] : "At least 12 characters. A few unrelated words make a strong one")}>
        <input className="input" type="password" autoComplete="new-password" autoFocus={autoFocus} value={first} aria-invalid={!!problem} onChange={(e) => onChange([e.target.value, again])} />
        <span className="strength" data-level={level} aria-hidden="true"><i /><i /><i /><i /></span>
      </Field>
      <Field label={`${label} again`} className="full" error={mismatch && "The two passwords are not the same"}>
        <input className="input" type="password" autoComplete="new-password" value={again} aria-invalid={mismatch} onChange={(e) => onChange([first, e.target.value])} />
      </Field>
    </>
  );
}

/** Both fields filled, the same, and accepted by the rules. */
export const passwordReady = ([first, again]: [string, string], person?: Person) => !!first && first === again && !passwordProblem(first, person);
