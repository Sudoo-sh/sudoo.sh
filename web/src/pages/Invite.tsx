// Accepting an invitation (/invite#<link>): who invited you and as what, then your name and password; the account is
// created and signed in, and a second factor is offered (or required first, when the panel asks for one). The link
// is in the part of the address after "#", which the browser never sends to a server.

import { useQueryClient } from "@tanstack/react-query";
import { ShieldCheck } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router";
import { api, ApiError, errorText, setCsrfToken } from "@/api/client";
import { keys } from "@/api/queries";
import type { InvitationPreview, Session } from "@/api/types";
import { LanguageSwitch } from "@/components/LanguageSwitch";
import { PasswordFields, passwordReady } from "@/components/PasswordFields";
import { Brand } from "@/components/app/Brand";
import { Button, Callout, Field, Spinner } from "@/components/app/basics";
import { Input } from "@/components/ui/input";
import { useT } from "@/i18n";
import auth from "@/i18n/auth";
import common from "@/i18n/common";
import { dateTime } from "@/lib/format";
import type { Person } from "@/lib/password";
import { Factors } from "./Account";

const tokenOf = (hash: string) => decodeURIComponent(hash.replace(/^#/, "")).trim();

export function InvitePage() {
  const [token] = useState(() => tokenOf(window.location.hash));
  const [invitation, setInvitation] = useState<InvitationPreview | null>(null);
  const [broken, setBroken] = useState<string | null>(token ? null : "missing");
  const [form, setForm] = useState({ first_name: "", last_name: "", phone: "" });
  const [password, setPassword] = useState<[string, string]>(["", ""]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [joined, setJoined] = useState<Session | null>(null);
  const client = useQueryClient();
  const navigate = useNavigate();
  const t = useT(auth).invite;
  const roles = useT(common).roles;

  useEffect(() => {
    if (!token) return;
    api
      .post<InvitationPreview>("/invitation", { token })
      .then((found) => {
        setInvitation(found);
        setForm((f) => ({ ...f, first_name: found.first_name ?? "", last_name: found.last_name ?? "" }));
      })
      .catch((e) => setBroken(e instanceof ApiError && e.status === 410 ? "broken" : errorText(e)));
  }, [token]);

  const person: Person = { email: invitation?.email ?? undefined, names: [form.first_name, form.last_name] };
  const ready = form.first_name.trim() && passwordReady(password, person);
  const set = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm({ ...form, [key]: e.target.value });

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const me = await api.post<Session>("/invitation/accept", { token, ...form, password: password[0] });
      setCsrfToken(me.csrf_token);
      client.setQueryData(keys.session, me);
      history.replaceState(null, "", window.location.pathname); // the used link leaves the address bar
      if (me.restricted)
        navigate(me.restricted === "second_factor" ? "/setup-second-factor" : "/", {
          replace: true,
          viewTransition: true,
        });
      else setJoined(me);
    } catch (err) {
      if (err instanceof ApiError && err.status === 410) setBroken("broken");
      else setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  const frame = (body: React.ReactNode, wide = false) => (
    <div className="auth-panel" style={{ minHeight: "100vh" }}>
      <div className={`auth-card ${wide ? "wide" : ""}`}>
        <div className="row">
          <Brand to="/login" />
          <span className="spacer" />
          <LanguageSwitch />
        </div>
        {body}
      </div>
    </div>
  );

  if (broken)
    return frame(
      <>
        <header>
          <h1>{t.brokenTitle}</h1>
          <p>{broken === "missing" ? t.noLink : broken === "broken" ? t.brokenText : broken}</p>
        </header>
        <Button asChild variant="primary" size="lg" block>
          <Link to="/login" viewTransition>
            {t.toSignIn}
          </Link>
        </Button>
      </>,
    );
  if (joined)
    return frame(
      <>
        <header>
          <h1 className="row">
            <ShieldCheck size={22} /> {t.factorTitle}
          </h1>
          <p>{t.factorText}</p>
        </header>
        <Factors />
        <div className="row-wrap">
          <Button variant="ghost" onClick={() => navigate("/", { replace: true, viewTransition: true })}>
            {t.later}
          </Button>
          <span className="spacer" />
          <Button variant="primary" onClick={() => navigate("/", { replace: true, viewTransition: true })}>
            {t.done}
          </Button>
        </div>
      </>,
      true,
    );
  if (!invitation)
    return (
      <div style={{ display: "grid", placeItems: "center", height: "100vh" }} aria-label={t.checking}>
        <Spinner />
      </div>
    );
  return frame(
    <form className="stack" style={{ gap: "var(--s6)" }} onSubmit={submit}>
      <header className="stack-sm">
        <h1>{t.title}</h1>
        <p className="muted">{t.text(invitation.invited_by, roles[invitation.role] ?? invitation.role_label)}</p>
      </header>
      <Field label={t.email} hint={t.expires(dateTime(invitation.expires_at))}>
        <Input type="email" dir="ltr" autoComplete="username email" value={invitation.email} readOnly />
      </Field>
      <div className="form-grid">
        <Field label={t.firstName}>
          <Input autoComplete="given-name" autoFocus value={form.first_name} onChange={set("first_name")} />
        </Field>
        <Field label={t.lastName}>
          <Input autoComplete="family-name" value={form.last_name} onChange={set("last_name")} />
        </Field>
        <Field label={t.phone} hint={t.optional} className="full">
          <Input type="tel" dir="ltr" autoComplete="tel" value={form.phone} onChange={set("phone")} />
        </Field>
        <PasswordFields value={password} onChange={setPassword} person={person} />
      </div>
      {invitation.second_factor_required && <Callout tone="info">{t.factorRequired}</Callout>}
      {error && <Callout tone="danger">{error}</Callout>}
      <Button type="submit" variant="primary" size="lg" block loading={busy} disabled={!ready}>
        {t.submit}
      </Button>
    </form>,
    true,
  );
}
