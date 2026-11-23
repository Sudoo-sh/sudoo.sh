import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, KeyRound, LifeBuoy, Lock, Mail, Smartphone } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate, useSearchParams } from "react-router";
import { api, ApiError, errorText, setCsrfToken } from "@/api/client";
import { usePortal } from "@/api/clients";
import { keys, useSession, useSetup } from "@/api/queries";
import type { SecondFactorChallenge, Session, SignInOptions } from "@/api/types";
import { Brand } from "@/components/app/Brand";
import { Button, Callout, Field, Spinner } from "@/components/app/basics";
import { GitHubMark } from "@/components/app/GitHubMark";
import { useToast } from "@/components/app/overlay";
import { Input } from "@/components/ui/input";
import { OtpInput } from "@/components/app/OtpInput";
import { LanguageSwitch } from "@/components/LanguageSwitch";
import { useT } from "@/i18n";
import auth from "@/i18n/auth";
import clientWords from "@/i18n/clients";
import { redirectTarget, signInLookFor } from "@/lib/frame";

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

const useSignInOptions = () =>
  useQuery({ queryKey: ["sign-in-options"], queryFn: () => api.get<SignInOptions>("/session/options"), retry: false, staleTime: 60_000 });

type FirstStep = Session | { second_factor: SecondFactorChallenge } | { redirect: { address: string; url: string } };

/**
 * Signing in: Continue with GitHub (when the panel offers it), then the e-mail address with the password, or a code
 * sent to that address instead (no password). Each may lead to the second factor's code. The server decides which
 * ways an account may use; this page only offers what the panel has turned on.
 */
export function LoginPage() {
  const { data: session } = useSession();
  const { data: setup } = useSetup();
  const { data: options } = useSignInOptions();
  const client = useQueryClient();
  const navigate = useNavigate();
  const location = useLocation();
  const [params] = useSearchParams();
  const next = params.get("next")?.startsWith("/") ? params.get("next")! : "/";
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [challenge, setChallenge] = useState<SecondFactorChallenge | null>(
    (location.state as { challenge?: SecondFactorChallenge } | null)?.challenge ?? null,
  );
  const [method, setMethod] = useState(challenge?.methods[0]?.name ?? "");
  const [inbox, setInbox] = useState<string | null>(null); // the address a sign-in code was sent to
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState<"" | "password" | "code" | "github" | "verify">("");
  const [error, setError] = useState("");
  const [shake, setShake] = useState(false);
  const [resendIn, setResendIn] = useState(challenge?.sent_to ? 60 : 0);
  const t = useT(auth).login;
  const p = useT(clientWords).portal;
  const { data: portalInfo } = usePortal();
  const look = signInLookFor(portalInfo);
  const [elsewhere, setElsewhere] = useState<string | null>(null);

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
  const firstStep = (answer: FirstStep) => {
    setCode("");
    if ("redirect" in answer) {
      // the account signs in at the other address (staff at the panel's, clients at the portal's): nothing started
      setElsewhere(answer.redirect.address === "panel" ? p.staffHere : p.clientHere);
      const target = redirectTarget(answer.redirect);
      if (target) setTimeout(() => window.location.assign(target), 1500);
    } else if ("second_factor" in answer) {
      setInbox(null);
      setChallenge(answer.second_factor);
      setMethod(answer.second_factor.methods[0]!.name);
      if (answer.second_factor.sent_to) setResendIn(60);
    } else signedIn(answer);
  };
  const run = async (what: typeof busy, call: () => Promise<void>) => {
    setBusy(what);
    setError("");
    try {
      await call();
    } catch (err) {
      fail(err);
    } finally {
      setBusy("");
    }
  };

  const submitPassword = (event: FormEvent) => {
    event.preventDefault();
    void run("password", async () => firstStep(await api.post<FirstStep>("/session", { username, password })));
  };
  const askCode = () => {
    if (!username.includes("@")) {
      fail(new Error(t.emailFirst));
      return;
    }
    void run("code", async () => {
      await api.post<{ sent_to: string }>("/session/email-code", { email: username.trim() });
      setInbox(username.trim());
      setCode("");
      setResendIn(60);
    });
  };
  const github = () =>
    run("github", async () => {
      const { url } = await api.post<{ url: string }>("/session/github", {});
      window.location.assign(url);
    });

  const recovering = method === "recovery";
  const codeReady = (value: string) => (recovering ? value.replace(/[\s-]/g, "").length === 10 : value.length === 6);
  const submitCode = (value = code) => {
    if (!codeReady(value)) return;
    void run("verify", async () => {
      try {
        if (inbox) firstStep(await api.post<FirstStep>("/session/email-code/verify", { email: inbox, code: value }));
        else signedIn(await api.post<Session>("/session/second-factor", { method, code: value }));
      } catch (err) {
        setCode("");
        if (err instanceof ApiError && err.status === 401 && /too long/.test(err.detail)) setChallenge(null);
        throw err;
      }
    });
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
  const back = () => {
    setChallenge(null);
    setInbox(null);
    setCode("");
    setError("");
  };

  const step = challenge ? "factor" : inbox ? "inbox" : "start";
  return (
    <div className={look === "portal" ? "auth portal-auth" : "auth"}>
      <section className="auth-panel">
        <div className="auth-card" key={step}>
          <div className="row"><Brand to="/login" /><span className="spacer" /><LanguageSwitch /></div>
          {step === "start" && (
            <>
              {look === "portal" ? (
                <header>
                  {portalInfo?.name && <p className="small muted portal-auth-name">{p.of(portalInfo.name)}</p>}
                  <h1>{p.signInTitle}</h1>
                  <p>{p.signInText}</p>
                </header>
              ) : (
                <header>
                  <h1>{t.title}</h1>
                  <p>{t.text}</p>
                </header>
              )}
              {elsewhere && <Callout tone="info">{elsewhere}</Callout>}
              {options?.github && (
                <>
                  <Button size="lg" block className="github-button" icon={<GitHubMark size={18} />} loading={busy === "github"} onClick={github}>
                    {t.github}
                  </Button>
                  <div className="auth-or" role="separator"><span>{t.or}</span></div>
                </>
              )}
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
                <Button type="submit" variant="primary" size="lg" block loading={busy === "password"} className={shake ? "shake" : ""}>
                  {t.submit}
                </Button>
              </form>
              {options?.email_code && (
                <div className="auth-alt">
                  <Button variant="ghost" block icon={<KeyRound />} loading={busy === "code"} onClick={askCode}>{t.sendCode}</Button>
                  <p className="xsmall faint">{t.sendCodeHint}</p>
                </div>
              )}
              {setup?.contact && <p className="faint small">{t.forgot} {setup.contact}</p>}
            </>
          )}
          {step === "inbox" && (
            <>
              <header>
                <h1>{t.inboxTitle}</h1>
                <p>{t.inboxText(inbox!)}</p>
              </header>
              <OtpInput value={code} onChange={setCode} onComplete={submitCode} shake={shake} />
              {error && <Callout tone="danger">{error}</Callout>}
              <Button variant="primary" size="lg" block loading={busy === "verify"} disabled={!codeReady(code)} onClick={() => submitCode()}>
                {t.verify}
              </Button>
              <div className="row small">
                <Button variant="ghost" size="sm" icon={<ArrowLeft className="flip-rtl" />} onClick={back}>{t.useAnother}</Button>
                <span className="spacer" />
                <Button variant="ghost" size="sm" disabled={resendIn > 0} onClick={askCode}>
                  {resendIn > 0 ? t.sendAgainIn(resendIn) : t.sendNew}
                </Button>
              </div>
            </>
          )}
          {step === "factor" && challenge && (
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
              <Button variant="primary" size="lg" block loading={busy === "verify"} disabled={!codeReady(code)} onClick={() => submitCode()}>
                {t.verify}
              </Button>
              <div className="row small">
                <Button variant="ghost" size="sm" icon={<ArrowLeft className="flip-rtl" />} onClick={back}>
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
      {look === "panel" && <Preview />}
    </div>
  );
}

/**
 * /login/github: where GitHub sends the browser back (the App's callback URL). It hands GitHub's one-time code and
 * the state to the panel, which signs in, asks for the second factor, or links the GitHub account to the account
 * that started from its account page.
 */
export function GitHubReturnPage() {
  const client = useQueryClient();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [error, setError] = useState("");
  const [linking, setLinking] = useState(false);
  const toast = useToast();
  const t = useT(auth).github;
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    const code = params.get("code");
    const state = params.get("state");
    if (params.get("error")) return setError(t.cancelled);
    if (!code || !state) return setError(t.missing);
    void (async () => {
      try {
        const answer = await api.post<FirstStep | { linked: string }>("/session/github/callback", { code, state });
        if ("linked" in answer) {
          setLinking(true);
          toast("success", t.linked(answer.linked));
          await client.invalidateQueries({ queryKey: keys.account });
          navigate("/account", { replace: true });
        } else if ("redirect" in answer) {
          // signed in at the other address (staff at the panel's, clients at the portal's): nothing started here
          const target = redirectTarget(answer.redirect);
          if (target) window.location.assign(target);
          else navigate("/login", { replace: true });
        } else if ("second_factor" in answer) {
          navigate("/login", { replace: true, state: { challenge: answer.second_factor } });
        } else {
          setCsrfToken(answer.csrf_token);
          client.setQueryData(keys.session, answer);
          navigate("/", { replace: true, viewTransition: true });
        }
      } catch (err) {
        setError(errorText(err));
      }
    })();
  }, [params, client, navigate, t, toast]);

  return (
    <div className="auth">
      <section className="auth-panel">
        <div className="auth-card">
          <div className="row"><Brand to="/login" /><span className="spacer" /><LanguageSwitch /></div>
          {error ? (
            <>
              <header>
                <h1>{t.failed}</h1>
                <p>{error}</p>
              </header>
              <div className="row-wrap">
                <Button variant="primary" onClick={() => navigate("/login", { replace: true })}>{t.toSignIn}</Button>
                <Button variant="ghost" onClick={() => navigate("/account", { replace: true })}>{t.toAccount}</Button>
              </div>
            </>
          ) : (
            <header className="row" aria-live="polite">
              <Spinner />
              <p>{linking ? t.linked("") : t.checking}</p>
            </header>
          )}
        </div>
      </section>
      <Preview />
    </div>
  );
}
