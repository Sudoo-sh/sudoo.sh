// Asking for an account (/request-access) and confirming the address (/request-access#<link>). The answer to the form
// is the same for every address: the page never says whether a domain is allowed or an account exists. The link is in
// the part of the address after "#", which the browser never sends to a server; it is used by pressing the button,
// not by opening the page, so a mail scanner that opens links does not use it up.

import { useState, type FormEvent } from "react";
import { Link } from "react-router";
import { api, errorText } from "@/api/client";
import { LanguageSwitch } from "@/components/LanguageSwitch";
import { Brand } from "@/components/app/Brand";
import { Button, Callout, Field } from "@/components/app/basics";
import { Input } from "@/components/ui/input";
import { useT } from "@/i18n";
import auth from "@/i18n/auth";

const tokenOf = (hash: string) => decodeURIComponent(hash.replace(/^#/, "")).trim();

export function RequestAccessPage() {
  const [token] = useState(() => tokenOf(window.location.hash));
  const [form, setForm] = useState({ email: "", first_name: "", last_name: "" });
  const [state, setState] = useState<"form" | "sent" | "confirmed">("form");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const t = useT(auth).requestAccess;
  const set = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm({ ...form, [key]: e.target.value });

  const run = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      if (token) {
        await api.post("/access-requests/confirm", { token });
        history.replaceState(null, "", window.location.pathname); // the used link leaves the address bar
        setState("confirmed");
      } else {
        await api.post("/access-requests", form);
        setState("sent");
      }
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  const toSignIn = (
    <Button asChild variant="primary" size="lg" block>
      <Link to="/login" viewTransition>
        {t.toSignIn}
      </Link>
    </Button>
  );
  return (
    <div className="auth-panel" style={{ minHeight: "100vh" }}>
      <div className="auth-card">
        <div className="row">
          <Brand to="/login" />
          <span className="spacer" />
          <LanguageSwitch />
        </div>
        {state === "sent" ? (
          <>
            <header className="stack-sm">
              <h1>{t.sentTitle}</h1>
              <p className="muted">{t.sentText}</p>
            </header>
            {toSignIn}
          </>
        ) : state === "confirmed" ? (
          <>
            <header className="stack-sm">
              <h1>{t.confirmedTitle}</h1>
              <p className="muted">{t.confirmedText}</p>
            </header>
            {toSignIn}
          </>
        ) : token ? (
          <form className="stack" style={{ gap: "var(--s6)" }} onSubmit={run}>
            <header className="stack-sm">
              <h1>{t.confirmTitle}</h1>
              <p className="muted">{t.confirmText}</p>
            </header>
            {error && <Callout tone="danger">{error}</Callout>}
            <Button type="submit" variant="primary" size="lg" block loading={busy}>
              {t.confirm}
            </Button>
          </form>
        ) : (
          <form className="stack" style={{ gap: "var(--s6)" }} onSubmit={run}>
            <header className="stack-sm">
              <h1>{t.title}</h1>
              <p className="muted">{t.text}</p>
            </header>
            <Field label={t.email}>
              <Input type="email" dir="ltr" autoComplete="email" autoFocus value={form.email} onChange={set("email")} />
            </Field>
            <div className="form-grid">
              <Field label={t.firstName}>
                <Input autoComplete="given-name" value={form.first_name} onChange={set("first_name")} />
              </Field>
              <Field label={t.lastName}>
                <Input autoComplete="family-name" value={form.last_name} onChange={set("last_name")} />
              </Field>
            </div>
            {error && <Callout tone="danger">{error}</Callout>}
            <Button type="submit" variant="primary" size="lg" block loading={busy} disabled={!form.email.includes("@")}>
              {t.submit}
            </Button>
            <Link to="/login" className="small muted">
              {t.toSignIn}
            </Link>
          </form>
        )}
      </div>
    </div>
  );
}
