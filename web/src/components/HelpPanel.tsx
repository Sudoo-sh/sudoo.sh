// The "?" button of the top bar and the help panel it opens at the side: a short text for the page shown, links to
// its documentation (in the panel and on the documentation site), the keyboard shortcuts, Contact support, the
// panel's version and Copy diagnostics. "?" opens it from anywhere but a text field; Esc closes it.

import { BookOpen, CircleHelp, ClipboardCopy, ExternalLink, LifeBuoy, Mail } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router";
import { useHelpInfo } from "@/api/queries";
import type { HelpInfo } from "@/api/types";
import { useLocale, useT } from "@/i18n";
import words from "@/i18n/help";
import { diagnostics } from "@/lib/diagnostics";
import { siteAddress, TOPIC_DOCS, topicOf } from "@/lib/help-topics";
import { Button, Callout } from "./app/basics";
import { Drawer, useToast } from "./app/overlay";

/** True while the person is typing somewhere: "?" is then a character, not the shortcut. */
function typing(target: EventTarget | null): boolean {
  const element = target as HTMLElement | null;
  if (!element || !(element instanceof HTMLElement)) return false;
  return element.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(element.tagName) || !!element.closest(".xterm");
}

export function HelpButton() {
  const [open, setOpen] = useState(false);
  const t = useT(words);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "?" || event.ctrlKey || event.metaKey || event.altKey || typing(event.target)) return;
      if (document.querySelector(".overlay")) return; // another dialog is open: it has the keyboard
      event.preventDefault();
      setOpen(true);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  return (
    <>
      <Button
        variant="ghost"
        size="sm"
        icon={<CircleHelp />}
        title={t.buttonTitle}
        aria-label={t.button}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-keyshortcuts="?"
        onClick={() => setOpen(true)}
      />
      {open && <HelpPanel onClose={() => setOpen(false)} />}
    </>
  );
}

const SHORTCUTS: { keys: string[]; what: keyof (typeof words)["en"]["keys"] }[] = [
  { keys: ["?"], what: "help" },
  { keys: ["Esc"], what: "escape" },
  { keys: ["Esc"], what: "fullLog" },
  { keys: ["Shift", "Esc"], what: "fullShell" },
  { keys: ["Ctrl", "Shift", "C"], what: "copyShell" },
  { keys: ["←", "→"], what: "tabs" },
  { keys: ["Home", "End"], what: "firstLast" },
];

function HelpPanel({ onClose }: { onClose: () => void }) {
  const { pathname } = useLocation();
  const { data: info } = useHelpInfo();
  const locale = useLocale();
  const t = useT(words);
  const topic = topicOf(pathname);
  const [path, anchor] = TOPIC_DOCS[topic];
  const docsBase = info?.docs_url ?? "https://docs.sudoo.sh";
  return (
    <Drawer className="help-panel" label={t.title} onClose={onClose} title={<h2 id="help-panel-title">{t.title}</h2>}>
      <div className="stack help-sections">
        <section aria-labelledby="help-topic">
          <div className="help-eyebrow">{t.onThisPage}</div>
          <h3 id="help-topic">{t.topics[topic].title}</h3>
          <p className="muted">{t.topics[topic].text}</p>
          <div className="help-links">
            <Link to={`/help/${path}#${anchor}`} viewTransition onClick={onClose}>
              <BookOpen aria-hidden="true" /> {t.readInPanel}
            </Link>
            <a href={siteAddress(docsBase, path, anchor)} target="_blank" rel="noreferrer noopener">
              <ExternalLink aria-hidden="true" className="flip-rtl" /> {t.readOnSite}
            </a>
            <Link to="/help" viewTransition onClick={onClose}>
              <BookOpen aria-hidden="true" /> {t.allDocs}
            </Link>
          </div>
        </section>

        <section aria-labelledby="help-shortcuts">
          <h3 id="help-shortcuts">{t.shortcuts}</h3>
          <dl className="help-keys">
            {SHORTCUTS.map((shortcut) => (
              <div key={shortcut.what}>
                <dt dir="ltr">
                  {shortcut.keys.map((key) => (
                    <kbd key={key} className="kbd">{key}</kbd>
                  ))}
                </dt>
                <dd>{t.keys[shortcut.what]}</dd>
              </div>
            ))}
          </dl>
        </section>

        <section aria-labelledby="help-support">
          <h3 id="help-support">{t.support}</h3>
          <Support info={info} />
        </section>

        <section aria-labelledby="help-version" className="help-version">
          <div className="row">
            <h3 id="help-version">{t.version}</h3>
            <span className="spacer" />
            <span className="mono small" dir="ltr">{info?.version ?? "…"}</span>
          </div>
          <Diagnostics version={info?.version ?? ""} pathname={pathname} language={locale} />
        </section>
      </div>
    </Drawer>
  );
}

function Support({ info }: { info: HelpInfo | undefined }) {
  const t = useT(words);
  if (!info) return null;
  const { kind, url, name } = info.support;
  if (kind === "none" || !url) return <p className="muted small">{t.supportNone(name ?? "")}</p>;
  const explanation = kind === "hub" ? t.supportHub : kind === "link" ? t.supportLink : t.supportEmail;
  return (
    <div className="stack-sm">
      <Button asChild variant="primary" className="help-support"><a href={url} target={kind === "email" ? undefined : "_blank"} rel="noreferrer noopener">
        {kind === "email" ? <Mail aria-hidden="true" /> : <LifeBuoy aria-hidden="true" />} {t.contact}
      </a></Button>
      <p className="muted small">{explanation}</p>
    </div>
  );
}

function Diagnostics({ version, pathname, language }: { version: string; pathname: string; language: string }) {
  const t = useT(words);
  const toast = useToast();
  const [shown, setShown] = useState<string | null>(null);
  const copy = async () => {
    const text = diagnostics(version, pathname, language);
    try {
      await navigator.clipboard.writeText(text);
      toast("success", t.copied);
      setShown(null);
    } catch {
      setShown(text); // no clipboard (an old browser, a page without permission): the text to select by hand
    }
  };
  return (
    <div className="stack-sm">
      <Button icon={<ClipboardCopy />} onClick={copy}>
        {t.diagnostics}
      </Button>
      <p className="muted xsmall">{t.diagnosticsText}</p>
      {shown && (
        <>
          <Callout tone="warning">{t.copyFailed}</Callout>
          <pre className="help-diagnostics" dir="ltr" tabIndex={0}>{shown}</pre>
        </>
      )}
    </div>
  );
}
