import { useQueryClient } from "@tanstack/react-query";
import { KeyRound, LogOut } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Navigate, useNavigate } from "react-router";
import { api, errorText, setCsrfToken } from "@/api/client";
import { keys, useSetup } from "@/api/queries";
import type { Session } from "@/api/types";
import { useSignOut } from "@/components/Shell";
import { Brand } from "@/components/ui/Brand";
import { Button, Callout, Field, Spinner } from "@/components/ui/basics";
import { Preview } from "./Login";

/** Twelve characters or more, as the server requires; a few words make a strong one. */
export function PasswordFields({ value, onChange }: { value: [string, string]; onChange: (value: [string, string]) => void }) {
  const [first, again] = value;
  const mismatch = again.length > 0 && first !== again;
  return (
    <>
      <Field label="Password" hint="At least 12 characters. A few unrelated words make a strong one">
        <input className="input" type="password" autoComplete="new-password" value={first} onChange={(e) => onChange([e.target.value, again])} />
      </Field>
      <Field label="Password again" error={mismatch && "The two passwords are not the same"}>
        <input className="input" type="password" autoComplete="new-password" value={again} aria-invalid={mismatch} onChange={(e) => onChange([first, e.target.value])} />
      </Field>
    </>
  );
}

/** First run: no account exists yet. Whoever has the setup code printed by the installer creates the first one. */
export function SetupPage() {
  const { data: setup, isPending } = useSetup();
  const client = useQueryClient();
  const navigate = useNavigate();
  const [form, setForm] = useState({ setup_code: "", first_name: "", last_name: "", username: "", email: "", phone: "" });
  const [password, setPassword] = useState<[string, string]>(["", ""]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (isPending) return <div style={{ display: "grid", placeItems: "center", height: "100vh" }}><Spinner /></div>;
  if (!setup?.needed) return <Navigate to="/login" replace />;
  const set = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [key]: e.target.value });
  const ready = form.setup_code && form.first_name && form.username && form.email && password[0].length >= 12 && password[0] === password[1];

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const me = await api.post<Session>("/setup", { ...form, username: form.username.toLowerCase(), password: password[0] });
      setCsrfToken(me.csrf_token);
      client.setQueryData(["setup"], { needed: false, contact: setup.contact });
      client.setQueryData(keys.session, me);
      navigate("/", { replace: true, viewTransition: true });
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth">
      <section className="auth-panel">
        <form className="auth-card wide" onSubmit={submit}>
          <Brand to="/setup" />
          <header>
            <h1>Set up Sudoo.sh</h1>
            <p>Create the first account. It can do everything, including adding the other accounts.</p>
          </header>
          <Field label="Setup code" hint={<>Printed by the installer. Show it again on the server with <code>docker exec sudoo-panel sudoo.sh setup-code</code></>}>
            <div className="input-group">
              <KeyRound />
              <input className="input mono" autoFocus placeholder="XXXX-XXXX-XXXX" value={form.setup_code} onChange={set("setup_code")} autoComplete="off" />
            </div>
          </Field>
          <div className="form-grid">
            <Field label="First name"><input className="input" autoComplete="given-name" value={form.first_name} onChange={set("first_name")} /></Field>
            <Field label="Last name"><input className="input" autoComplete="family-name" value={form.last_name} onChange={set("last_name")} /></Field>
            <Field label="E-mail" className="full"><input className="input" type="email" autoComplete="email" value={form.email} onChange={set("email")} /></Field>
            <Field label="Phone" hint="Optional"><input className="input" type="tel" autoComplete="tel" value={form.phone} onChange={set("phone")} /></Field>
            <Field label="User name" hint="For signing in, lower case"><input className="input" autoComplete="username" value={form.username} onChange={set("username")} /></Field>
            <PasswordFields value={password} onChange={setPassword} />
          </div>
          {error && <Callout tone="danger">{error}</Callout>}
          <Button type="submit" variant="primary" size="lg" block loading={busy} disabled={!ready}>
            Create the account
          </Button>
        </form>
      </section>
      <Preview />
    </div>
  );
}

/** A password generated by someone else (a new account, a reset) must be replaced before anything else. */
export function ChangePasswordPage() {
  const client = useQueryClient();
  const navigate = useNavigate();
  const signOut = useSignOut();
  const [current, setCurrent] = useState("");
  const [password, setPassword] = useState<[string, string]>(["", ""]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
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
          <h1>Choose your password</h1>
          <p>The password you signed in with was made for you. Replace it with one only you know.</p>
        </header>
        <Field label="Current password">
          <input className="input" type="password" autoComplete="current-password" autoFocus value={current} onChange={(e) => setCurrent(e.target.value)} />
        </Field>
        <PasswordFields value={password} onChange={setPassword} />
        {error && <Callout tone="danger">{error}</Callout>}
        <Button type="submit" variant="primary" size="lg" block loading={busy} disabled={!current || password[0].length < 12 || password[0] !== password[1]}>
          Save and continue
        </Button>
        <Button variant="ghost" icon={<LogOut />} onClick={signOut} style={{ alignSelf: "flex-start" }}>Sign out</Button>
      </form>
    </div>
  );
}
