// The acceptance of the Sudoo.sh Licence Agreement: its summary, the full text (the copy this release bundles), the
// declared use and the boxes to tick. Used by first-run setup and by the step shown to super admins (pages/Licence).

import { ArrowUpRight, Briefcase, Building2, User } from "lucide-react";
import { useState, type FormEvent, type ReactNode } from "react";
import type { LicenceAcceptance as Acceptance, LicenceText, LicenceUse } from "@/api/types";
import { useLocale, useT } from "@/i18n";
import words from "@/i18n/licence";
import { Button, Callout } from "./app/basics";
import { Checkbox } from "@/components/ui/checkbox";

const USES: { value: LicenceUse; icon: ReactNode }[] = [
  { value: "personal", icon: <User /> },
  { value: "company", icon: <Building2 /> },
  { value: "service_provider", icon: <Briefcase /> },
];

/** The agreement's facts, its summary in the page's language and the full text (English) in a scrolling box. */
export function LicenceDocument({ text, open: startOpen = false }: { text: LicenceText; open?: boolean }) {
  const t = useT(words);
  const locale = useLocale();
  const [open, setOpen] = useState(startOpen);
  return (
    <div className="licence">
      <div className="row-wrap licence-facts">
        <span className="muted small">{t.version(text.version, text.effective)}</span>
        <span className="spacer" />
        <Button asChild variant="ghost" size="sm">
          <a href={text.version_url} target="_blank" rel="noreferrer">
            {t.openPublished} <ArrowUpRight aria-hidden="true" className="flip-rtl" />
          </a>
        </Button>
      </div>
      <section className="licence-summary" aria-label={t.summary}>
        <div
          className="prose"
          dangerouslySetInnerHTML={{ __html: locale === "ar" ? text.summary_ar_html : text.summary_html }}
        />
      </section>
      <div>
        <Button size="sm" variant="ghost" aria-expanded={open} onClick={() => setOpen(!open)}>
          {open ? t.hideFullText : t.showFullText}
        </Button>
      </div>
      {open && (
        <section className="licence-full" aria-label={t.fullText}>
          {locale !== "en" && <p className="faint small">{t.englishOnly}</p>}
          <div className="licence-scroll" tabIndex={0} role="document" lang="en" dir="ltr">
            <div className="prose" dangerouslySetInnerHTML={{ __html: text.html }} />
          </div>
        </section>
      )}
    </div>
  );
}

/** The declared use, the boxes and the button. `onAccept` gets what the backend expects. */
export function LicenceAcceptance({
  text,
  busy,
  error,
  submitLabel,
  secondary,
  onAccept,
}: {
  text: LicenceText;
  busy?: boolean;
  error?: string;
  submitLabel?: string;
  secondary?: ReactNode;
  onAccept: (acceptance: Acceptance) => void;
}) {
  const t = useT(words);
  const locale = useLocale();
  const [use, setUse] = useState<LicenceUse | null>(null);
  const [accepted, setAccepted] = useState(false);
  const [onBehalf, setOnBehalf] = useState(false);
  const forCompany = use === "company" || use === "service_provider";
  const ready = use !== null && accepted && (!forCompany || onBehalf);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!ready || !use) return;
    onAccept({
      accepted: true,
      version: text.version,
      sha256: text.sha256,
      use,
      on_behalf: forCompany && onBehalf,
      language: locale === "ar" ? "ar" : "en",
    });
  };

  return (
    <form className="stack" onSubmit={submit}>
      <LicenceDocument text={text} />
      <fieldset className="licence-uses">
        <legend>{t.use}</legend>
        <div className="stack-sm" role="radiogroup" aria-label={t.use}>
          {USES.map((item) => (
            <button
              key={item.value}
              type="button"
              className="choice"
              role="radio"
              aria-checked={use === item.value}
              aria-pressed={use === item.value}
              onClick={() => setUse(item.value)}
            >
              {item.icon}
              <span>
                <span className="t">{t.uses[item.value].title}</span>
                <br />
                <span className="d">{t.uses[item.value].text}</span>
              </span>
            </button>
          ))}
        </div>
      </fieldset>
      <label className="licence-check">
        <Checkbox checked={accepted} onCheckedChange={(value) => setAccepted(value === true)} />
        <span>{t.accept(text.version)}</span>
      </label>
      {forCompany && (
        <label className="licence-check">
          <Checkbox checked={onBehalf} onCheckedChange={(value) => setOnBehalf(value === true)} />
          <span>{t.company}</span>
        </label>
      )}
      {error && <Callout tone="danger">{error}</Callout>}
      <div className="row-wrap">
        {secondary}
        <span className="spacer" />
        <Button type="submit" variant="primary" size="lg" loading={busy} disabled={!ready}>
          {submitLabel ?? t.submit}
        </Button>
      </div>
    </form>
  );
}
