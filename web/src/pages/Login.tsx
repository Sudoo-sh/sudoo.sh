import { useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight, KeyRound, Lock, Mail, Smartphone, User } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { Navigate, useNavigate, useSearchParams } from "react-router";
import { api, ApiError, errorText, setCsrfToken } from "@/api/client";
import { keys, useSession } from "@/api/queries";
import type { SecondFactorChallenge, Session } from "@/api/types";
import { Brand, Mark } from "@/components/ui/Brand";
import { Button, Callout, Field } from "@/components/ui/basics";
import { OtpInput } from "@/components/ui/OtpInput";

const CONSOLE = [
  ["p", "$ sudoo.sh create --branch feature/invoices"],
  ["dim", "  cloning, preparing Odoo 19.0 Enterprise…"],
  ["ok", "  ✓ sales-feature-invoices is online"],
  ["p", "$ git push origin main"],
  ["ok", "  ✓ updated in 9 s, modules upgraded"],
];

function Art() {
  return (
    <section className="auth-art">
      <div className="grid" />
      <Brand to="/login" />
      <div className="pitch">
        <h1>Every branch, a running Odoo.</h1>
        <p>Create, update and share Odoo instances on your own server, the way Odoo.sh does it in the cloud.</p>
        <div className="console">
          {CONSOLE.map(([tone, text], i) => (
            <div key={i} className={`line ${tone}`} style={{ animationDelay: `${300 + i * 420}ms` }}>
              {text}
            </div>
          ))}
        </div>
      </div>
      <div className="foot">Sudoo.sh · Created by Yousef M. Y. Al Sabbah</div>
    </section>
  );
}

const FACTOR_ICON: Record<string, React.ReactNode> = { totp: <Smartphone size={16} />, email: <Mail size={16} /> };

export function LoginPage() {
  const { data: session } = useSession();
  const client = useQueryClient();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const next = params.get("next")?.startsWith("/") ? params.get("next")! : "/";
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [challenge, setChallenge] = useState<SecondFactorChallenge | null>(null);
  const [method, setMethod] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [shake, setShake] = useState(false);
  const [contact, setContact] = useState("");
  const [resendIn, setResendIn] = useState(0);

  useEffect(() => {
    api.get<{ sentence: string }>("/contact").then((c) => setContact(c.sentence)).catch(() => undefined);
  }, []);
  useEffect(() => {
    if (resendIn <= 0) return;
    const timer = setTimeout(() => setResendIn(resendIn - 1), 1000);
    return () => clearTimeout(timer);
  }, [resendIn]);

  if (session) return <Navigate to={next} replace />;

  const signedIn = (me: Session) => {
    setCsrfToken(me.csrf_token);
    client.setQueryData(keys.session, me);
    navigate(me.must_set_up_second_factor ? "/setup-second-factor" : next, { replace: true, viewTransition: true });
  };
  const fail = (err: unknown) => {
    setError(errorText(err));
    setShake(true);
    setTimeout(() => setShake(false), 450);
  };

  const submitPassword = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const answer = await api.post<Session | { second_factor: SecondFactorChallenge }>("/session", { username, password });
      if ("second_factor" in answer) {
        setChallenge(answer.second_factor);
        setMethod(answer.second_factor.methods[0]!.name);
        if (answer.second_factor.sent_to) setResendIn(60);
      } else signedIn(answer);
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  };

  const submitCode = async (value = code) => {
    if (value.length !== 6) return;
    setBusy(true);
    setError("");
    try {
      signedIn(await api.post<Session>("/session/second-factor", { method, code: value }));
    } catch (err) {
      setCode("");
      fail(err);
      if (err instanceof ApiError && err.status === 401 && /too long/.test(err.detail)) setChallenge(null);
    } finally {
      setBusy(false);
    }
  };

  const sendEmail = async () => {
    setError("");
    try {
      const sent = await api.post<{ sent_to: string }>("/session/second-factor/email");
      setChallenge((c) => c && { ...c, sent_to: sent.sent_to });
      setResendIn(60);
    } catch (err) {
      fail(err);
    }
  };

  return (
    <div className="auth">
      <Art />
      <section className="auth-panel">
        <div className="auth-card" key={challenge ? "code" : "password"}>
          <div className="mobile-brand">
            <Brand to="/login" />
          </div>
          {!challenge ? (
            <>
              <header>
                <h1>Welcome back</h1>
                <p>Sign in to manage your Odoo instances.</p>
              </header>
              <form className="stack" onSubmit={submitPassword}>
                <Field label="User name" htmlFor="username">
                  <div className="input-group">
                    <User />
                    <input id="username" className="input" autoComplete="username" autoFocus value={username} onChange={(e) => setUsername(e.target.value)} required />
                  </div>
                </Field>
                <Field label="Password" htmlFor="password">
                  <div className="input-group">
                    <Lock />
                    <input id="password" className="input" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
                  </div>
                </Field>
                {error && <Callout tone="danger">{error}</Callout>}
                <Button type="submit" variant="primary" size="lg" block loading={busy} className={shake ? "shake" : ""}>
                  Sign in <ArrowRight />
                </Button>
              </form>
              {contact && <p className="faint small">Forgot your password? {contact}</p>}
            </>
          ) : (
            <>
              <header>
                <span className="badge primary" style={{ alignSelf: "flex-start" }}>
                  <KeyRound /> Second step
                </span>
                <h1>Enter your code</h1>
                <p>
                  {method === "email"
                    ? challenge.sent_to
                      ? `We sent a 6-digit code to ${challenge.sent_to}.`
                      : "We can send a 6-digit code to your e-mail address."
                    : "Open your authenticator app and type the 6-digit code for Sudoo.sh."}
                </p>
              </header>
              {challenge.methods.length > 1 && (
                <div className="segmented" style={{ alignSelf: "flex-start" }}>
                  {challenge.methods.map((m) => (
                    <button key={m.name} type="button" aria-pressed={m.name === method} onClick={() => { setMethod(m.name); setCode(""); setError(""); }}>
                      {FACTOR_ICON[m.name]} {m.label}
                    </button>
                  ))}
                </div>
              )}
              <OtpInput value={code} onChange={setCode} onComplete={submitCode} shake={shake} />
              {error && <Callout tone="danger">{error}</Callout>}
              <Button variant="primary" size="lg" block loading={busy} disabled={code.length !== 6} onClick={() => submitCode()}>
                Verify and sign in
              </Button>
              <div className="row small">
                <Button variant="ghost" size="sm" icon={<ArrowLeft />} onClick={() => { setChallenge(null); setCode(""); setError(""); }}>
                  Back
                </Button>
                <span className="spacer" />
                {method === "email" && (
                  <Button variant="ghost" size="sm" disabled={resendIn > 0} onClick={sendEmail}>
                    {resendIn > 0 ? `Send again in ${resendIn}s` : challenge.sent_to ? "Send a new code" : "Send the code"}
                  </Button>
                )}
              </div>
            </>
          )}
          <p className="faint xsmall row" style={{ justifyContent: "center" }}>
            <Mark size={14} /> Sudoo.sh
          </p>
        </div>
      </section>
    </div>
  );
}
