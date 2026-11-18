import { useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, KeyRound, LogOut } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Navigate, useNavigate } from "react-router";
import { api, errorText, setCsrfToken } from "@/api/client";
import { keys, useLicenceText, useSetup } from "@/api/queries";
import type { LicenceAcceptance as Acceptance, Session } from "@/api/types";
import { LicenceAcceptance } from "@/components/LicenceAcceptance";
import licenceWords from "@/i18n/licence";
import { useSignOut } from "@/components/Shell";
import { Brand } from "@/components/ui/Brand";
import { Button, Callout, Field, Spinner } from "@/components/ui/basics";
import { PasswordFields, passwordReady } from "@/components/PasswordFields";
import type { Person } from "@/lib/password";
import { useT } from "@/i18n";
import auth from "@/i18n/auth";
import common from "@/i18n/common";
import { LanguageSwitch } from "@/components/LanguageSwitch";
import { Preview } from "./Login";

/** First run: no account exists yet. Whoever has the setup code printed by the installer accepts the licence
 *  agreement (step 1), then creates the first account (step 2); both are sent together. */
export function SetupPage() {
  const { data: setup, isPending } = useSetup();
  const licenceText = useLicenceText();
  const [licence, setLicence] = useState<Acceptance | null>(null);
  const l = useT(licenceWords);
  const client = useQueryClient();
  const navigate = useNavigate();
  const [form, setForm] = useState({ setup_code: "", first_name: "", last_name: "", email: "", phone: "" });
  const [password, setPassword] = useState<[string, string]>(["", ""]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const t = useT(auth).setup;
  if (isPending) return <div style={{ display: "grid", placeItems: "center", height: "100vh" }}><Spinner /></div>;
  if (!setup?.needed) return <Navigate to="/login" replace />;
  const set = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [key]: e.target.value });
  const person: Person = { email: form.email, names: [form.first_name, form.last_name] };
  const ready = form.setup_code && form.first_name && form.email.includes("@") && passwordReady(password, person);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const me = await api.post<Session>("/setup", { ...form, email: form.email.trim(), password: password[0], licence });
      setCsrfToken(me.csrf_token);
      client.setQueryData(["setup"], { ...setup, needed: false });
      client.setQueryData(keys.session, me);
      navigate("/", { replace: true, viewTransition: true });
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  if (!licence)
    return (
      <div className="auth">
        <section className="auth-panel">
          <div className="auth-card wide">
            <div className="row"><Brand to="/setup" /><span className="spacer" /><LanguageSwitch /></div>
            <header>
              <span className="faint small">{l.step(1, 2)}</span>
              <h1>{l.title}</h1>
              <p>{l.setupText}</p>
            </header>
            {licenceText.data ? (
              <LicenceAcceptance text={licenceText.data} onAccept={(accepted) => { setLicence(accepted); window.scrollTo({ top: 0 }); }} submitLabel={l.submit} />
            ) : licenceText.isError ? (
              <Callout tone="danger">{l.loadFailed}</Callout>
            ) : (
              <Spinner />
            )}
          </div>
        </section>
        <Preview />
      </div>
    );

  return (
    <div className="auth">
      <section className="auth-panel">
        <form className="auth-card wide" onSubmit={submit}>
          <div className="row"><Brand to="/setup" /><span className="spacer" /><LanguageSwitch /></div>
          <header>
            <span className="faint small">{l.step(2, 2)}</span>
            <h1>{t.title}</h1>
            <p>{t.text}</p>
          </header>
          <Field label={t.code} hint={<>{t.codeHint(<code key="c">docker exec sudoo-panel sudoo.sh setup-code</code>)}</>}>
            <div className="input-group">
              <KeyRound />
              <input className="input mono" dir="ltr" autoFocus placeholder="XXXX-XXXX-XXXX" value={form.setup_code} onChange={set("setup_code")} autoComplete="off" />
            </div>
          </Field>
          <div className="form-grid">
            <Field label={t.firstName}><input className="input" autoComplete="given-name" value={form.first_name} onChange={set("first_name")} /></Field>
            <Field label={t.lastName}><input className="input" autoComplete="family-name" value={form.last_name} onChange={set("last_name")} /></Field>
            <Field label={t.email} hint={t.emailHint}><input className="input" type="email" autoComplete="username email" value={form.email} onChange={set("email")} /></Field>
            <Field label={t.phone} hint={t.optional}><input className="input" type="tel" dir="ltr" autoComplete="tel" value={form.phone} onChange={set("phone")} /></Field>
            <PasswordFields value={password} onChange={setPassword} person={person} />
          </div>
          {error && <Callout tone="danger">{error}</Callout>}
          <Button type="submit" variant="primary" size="lg" block loading={busy} disabled={!ready}>
            {t.submit}
          </Button>
          <Button variant="ghost" icon={<ArrowLeft className="flip-rtl" />} onClick={() => setLicence(null)} style={{ alignSelf: "flex-start" }}>{l.back}</Button>
        </form>
      </section>
      <Preview />
    </div>
  );
}

/** A password generated by someone else (a new account, a reset) must be replaced before anything else. */
export function ChangePasswordPage() {
  const me = useQueryClient().getQueryData<Session>(keys.session);
  const person: Person = { username: me?.user, email: me?.email ?? undefined, names: [me?.first_name ?? "", me?.last_name ?? ""] };
  const client = useQueryClient();
  const navigate = useNavigate();
  const signOut = useSignOut();
  const [current, setCurrent] = useState("");
  const [password, setPassword] = useState<[string, string]>(["", ""]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const t = useT(auth).changePassword;
  const signOutLabel = useT(common).nav.signOut;
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api.put("/session/password", { current, new: password[0] });
      const me = await api.get<Session>("/session");
      client.setQueryData(keys.session, me);
      navigate(me.restricted === "second_factor" ? "/setup-second-factor" : "/", { replace: true, viewTransition: true });
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="auth-panel" style={{ minHeight: "100vh" }}>
      <form className="auth-card" onSubmit={submit}>
        <Brand />
        <header>
          <h1>{t.title}</h1>
          <p>{t.text}</p>
        </header>
        <Field label={t.current}>
          <input className="input" type="password" autoComplete="current-password" autoFocus value={current} onChange={(e) => setCurrent(e.target.value)} />
        </Field>
        <PasswordFields value={password} onChange={setPassword} person={person} />
        {error && <Callout tone="danger">{error}</Callout>}
        <Button type="submit" variant="primary" size="lg" block loading={busy} disabled={!current || !passwordReady(password, person)}>
          {t.submit}
        </Button>
        <Button variant="ghost" icon={<LogOut />} onClick={signOut} style={{ alignSelf: "flex-start" }}>{signOutLabel}</Button>
      </form>
    </div>
  );
}
