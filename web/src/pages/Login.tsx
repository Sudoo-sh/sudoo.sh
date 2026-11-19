import { useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, LifeBuoy, Lock, Mail, Smartphone } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { Navigate, useNavigate, useSearchParams } from "react-router";
import { api, ApiError, errorText, setCsrfToken } from "@/api/client";
import { keys, useSession, useSetup } from "@/api/queries";
import type { SecondFactorChallenge, Session } from "@/api/types";
import { Brand } from "@/components/app/Brand";
import { Button, Callout, Field } from "@/components/app/basics";
import { Input } from "@/components/ui/input";
import { OtpInput } from "@/components/app/OtpInput";
import { LanguageSwitch } from "@/components/LanguageSwitch";
import { useT } from "@/i18n";
import auth from "@/i18n/auth";

const PREVIEW = [
  { name: "sales-main", branch: "main", version: "19.0", status: "var(--positive)", when: "now" },
  { name: "crm-lead-scoring", branch: "feature/lead-scoring", version: "19.0", status: "var(--working)", when: "updating" },
  { name: "pos-restaurant", branch: "17.0-restaurant", version: "17.0", status: "var(--positive)", when: "minutesAgo" },
  { name: "invoicing-demo", branch: "demo/customer-a", version: "18.0", status: "var(--caution)", when: "paused" },
] as const;

/** The sign-in page's one rich moment: the product itself, branches running as Odoo instances. */
export function Preview() {
  const t = useT(auth).preview;
  return (
    <section className="auth-preview" aria-hidden="true">
      <div className="pitch">
        <h1>{t.title}</h1>
        <p>{t.text}</p>
      </div>
      <div className="window">
        <div className="bar"><span dir="ltr">sudoo.sh</span> · {t.instances}</div>
        {PREVIEW.map((item, i) => (
          <div className="item" key={item.name} style={{ animationDelay: `${150 + i * 60}ms` }}>
            <span className="dot" style={{ background: item.status }} />
            <span className="truncate">
              <bdi className="name">{item.name}</bdi> <bdi className="branch">{item.branch}</bdi>
            </span>
            <span className="ver">{item.version}</span>
            <span className="when">{t[item.when]}</span>
          </div>
        ))}
      </div>
      <div className="foot">Sudoo.sh</div>
    </section>
  );
}

const FACTOR_ICON: Record<string, React.ReactNode> = { totp: <Smartphone size={16} />, email: <Mail size={16} /> };

export function LoginPage() {
  const { data: session } = useSession();
  const { data: setup } = useSetup();
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
  const [resendIn, setResendIn] = useState(0);
  const t = useT(auth).login;

  useEffect(() => {
    if (resendIn <= 0) return;
    const timer = setTimeout(() => setResendIn(resendIn - 1), 1000);
    return () => clearTimeout(timer);
  }, [resendIn]);

  if (setup?.needed) return <Navigate to="/setup" replace />;
  if (session) return <Navigate to={next} replace />;

  const signedIn = (me: Session) => {
    setCsrfToken(me.csrf_token);
    client.setQueryData(keys.session, me);
    navigate(next, { replace: true, viewTransition: true });
  };
  const fail = (err: unknown) => {
    setError(errorText(err));
    setShake(true);
    setTimeout(() => setShake(false), 350);
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

  const recovering = method === "recovery";
  const codeReady = (value: string) => (recovering ? value.replace(/[\s-]/g, "").length === 10 : value.length === 6);
  const submitCode = async (value = code) => {
    if (!codeReady(value)) return;
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
      <section className="auth-panel">
        <div className="auth-card" key={challenge ? "code" : "password"}>
          <div className="row"><Brand to="/login" /><span className="spacer" /><LanguageSwitch /></div>
          {!challenge ? (
            <>
              <header>
                <h1>{t.title}</h1>
                <p>{t.text}</p>
              </header>
              <form className="stack" onSubmit={submitPassword}>
                <Field label={t.email} htmlFor="username">
                  <div className="input-group">
                    <Mail />
                    <Input id="username" type="text" inputMode="email" dir="ltr" autoCapitalize="none" spellCheck={false} placeholder="you@example.com" autoComplete="username" autoFocus value={username} onChange={(e) => setUsername(e.target.value)} required />
                  </div>
                </Field>
                <Field label={t.password} htmlFor="password">
                  <div className="input-group">
                    <Lock />
                    <Input id="password" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
                  </div>
                </Field>
                {error && <Callout tone="danger">{error}</Callout>}
                <Button type="submit" variant="primary" size="lg" block loading={busy} className={shake ? "shake" : ""}>
                  {t.submit}
                </Button>
              </form>
              {setup?.contact && <p className="faint small">{t.forgot} {setup.contact}</p>}
            </>
          ) : (
            <>
              <header>
                <h1>{t.codeTitle}</h1>
                <p>
                  {recovering
                    ? t.recoveryText
                    : method === "email"
                    ? challenge.sent_to
                      ? t.emailSent(challenge.sent_to)
                      : t.emailCanSend
                    : t.appText}
                </p>
              </header>
              {!recovering && challenge.methods.filter((m) => m.name !== "recovery").length > 1 && (
                <div className="segmented" style={{ alignSelf: "flex-start" }}>
                  {challenge.methods.filter((m) => m.name !== "recovery").map((m) => (
                    <button key={m.name} type="button" aria-pressed={m.name === method} onClick={() => { setMethod(m.name); setCode(""); setError(""); }}>
                      {FACTOR_ICON[m.name]} {t.factors[m.name] ?? m.label}
                    </button>
                  ))}
                </div>
              )}
              {recovering ? (
                <form onSubmit={(e) => { e.preventDefault(); submitCode(); }}>
                  <Field label={t.recoveryCode} htmlFor="recovery-code">
                    <div className="input-group">
                      <LifeBuoy />
                      <Input id="recovery-code" dir="ltr" className={`mono ${shake ? "shake" : ""}`} autoComplete="one-time-code" autoCapitalize="none" spellCheck={false}
                        placeholder="xxxxx-xxxxx" maxLength={24} autoFocus value={code} onChange={(e) => setCode(e.target.value)} />
                    </div>
                  </Field>
                </form>
              ) : (
                <OtpInput value={code} onChange={setCode} onComplete={submitCode} shake={shake} />
              )}
              {error && <Callout tone="danger">{error}</Callout>}
              <Button variant="primary" size="lg" block loading={busy} disabled={!codeReady(code)} onClick={() => submitCode()}>
                {t.verify}
              </Button>
              <div className="row small">
                <Button variant="ghost" size="sm" icon={<ArrowLeft className="flip-rtl" />} onClick={() => { setChallenge(null); setCode(""); setError(""); }}>
                  {t.back}
                </Button>
                <span className="spacer" />
                {challenge.methods.some((m) => m.name === "recovery") && (method === "totp" || recovering) && (
                  <Button variant="ghost" size="sm" onClick={() => { setMethod(recovering ? "totp" : "recovery"); setCode(""); setError(""); }}>
                    {recovering ? t.useApp : t.lostPhone}
                  </Button>
                )}
                {method === "email" && (
                  <Button variant="ghost" size="sm" disabled={resendIn > 0} onClick={sendEmail}>
                    {resendIn > 0 ? t.sendAgainIn(resendIn) : challenge.sent_to ? t.sendNew : t.send}
                  </Button>
                )}
              </div>
            </>
          )}
        </div>
      </section>
      <Preview />
    </div>
  );
}
