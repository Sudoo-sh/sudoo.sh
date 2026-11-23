import { useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  ArrowRight,
  Boxes,
  CircleCheck,
  Cpu,
  GitBranch,
  Globe,
  HardDrive,
  KeyRound,
  Layers,
  Link2,
  LogOut,
  MemoryStick,
  Network,
  RefreshCw,
  RotateCw,
  Send,
  Server,
  Ship,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { Navigate, useNavigate } from "react-router";
import { api, errorText, setCsrfToken, setSetupCode } from "@/api/client";
import { keys, useLicenceText, useSetup } from "@/api/queries";
import type { Engine, LicenceAcceptance as Acceptance, ServerCheck, Session, SetupDraft, SetupNeeds, SetupResult, SetupStep } from "@/api/types";
import { LicenceAcceptance } from "@/components/LicenceAcceptance";
import licenceWords from "@/i18n/licence";
import { useSignOut } from "@/components/Shell";
import { Brand } from "@/components/app/Brand";
import { Button, Callout, CopyField, Field, Segmented, Spinner, Switch, Tag } from "@/components/app/basics";
import { Input } from "@/components/ui/input";
import { PasswordFields, passwordReady } from "@/components/PasswordFields";
import { SetupStepper } from "@/components/SetupStepper";
import type { Person } from "@/lib/password";
import { back, blocker, codeFromHash, indexOf, next, OPTIONAL, resumeAt } from "@/lib/setup-steps";
import { useT } from "@/i18n";
import auth from "@/i18n/auth";
import common from "@/i18n/common";
import setupWords from "@/i18n/setup";
import { LanguageSwitch } from "@/components/LanguageSwitch";

/** What this browser keeps of a setup in progress (sessionStorage): the setup code, the licence acceptance and the
 *  furthest step reached. The choices themselves are kept by the panel (GET/PUT /setup/draft); passwords never. */
const STORE = "sudoo.setup";
interface Kept {
  code: string;
  licence: Acceptance | null;
  reached: SetupStep;
  /** after the final confirm: what was applied and the choices, so the last step survives the page being drawn
   *  again (signed in now, the panel may switch to the account's language); the code is forgotten then */
  done?: { result: SetupResult; draft: SetupDraft };
}
function readKept(): Kept {
  try {
    const kept = JSON.parse(sessionStorage.getItem(STORE) ?? "null") as Partial<Kept> | null;
    return { code: kept?.code ?? "", licence: kept?.licence ?? null, reached: kept?.reached ?? "welcome", done: kept?.done };
  } catch {
    return { code: "", licence: null, reached: "welcome" };
  }
}
function keep(kept: Kept | null) {
  try {
    if (kept) sessionStorage.setItem(STORE, JSON.stringify(kept));
    else sessionStorage.removeItem(STORE);
  } catch {
    /* private window: the panel still keeps the draft */
  }
}

/** First run: whoever has the setup code printed by the installer walks through the setup wizard. Each step is
 *  saved as a draft on the panel (resumable from any browser with the code); nothing is applied before the final
 *  confirm on the super admin step, which creates the account and applies the choices together (POST /setup). */
export function SetupPage() {
  const { data: setup, isPending } = useSetup();
  if (isPending) return <div style={{ display: "grid", placeItems: "center", height: "100vh" }}><Spinner /></div>;
  if (!setup?.needed && !readKept().done) return <Navigate to="/login" replace />; // the last step is shown once after the confirm
  return <Wizard />;
}

function Wizard() {
  const t = useT(setupWords);
  const l = useT(licenceWords);
  const client = useQueryClient();
  const navigate = useNavigate();
  const licenceText = useLicenceText();
  const [kept, setKept] = useState<Kept>(() => {
    const stored = readKept();
    const fromLink = codeFromHash(location.hash);
    return fromLink ? { ...stored, code: fromLink } : stored;
  });
  const [fromLink] = useState(() => !!codeFromHash(location.hash));
  const [step, setStep] = useState<SetupStep>(() => (readKept().done ? "done" : "welcome"));
  const [direction, setDirection] = useState<"forward" | "backward">("forward");
  const [draft, setDraft] = useState<SetupDraft | null>(() => readKept().done?.draft ?? null);
  const [check, setCheck] = useState<ServerCheck | null>(null);
  const [checkFailed, setCheckFailed] = useState(false);
  const [checking, setChecking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [resumed, setResumed] = useState(false);
  const [smtpPassword, setSmtpPassword] = useState("");
  const [password, setPassword] = useState<[string, string]>(["", ""]);
  const [result, setResult] = useState<SetupResult | null>(() => readKept().done?.result ?? null);
  const heading = useRef<HTMLHeadingElement>(null);

  // the code leaves the address bar at once: it is kept in this tab only
  useEffect(() => {
    if (location.hash) history.replaceState(null, "", location.pathname + location.search);
  }, []);
  useEffect(() => keep(kept), [kept]);
  useEffect(() => setSetupCode(kept.code), [kept.code]);

  const loadDraft = useCallback(async (code: string) => {
    setSetupCode(code);
    const loaded = await api.get<SetupDraft>("/setup/draft");
    setDraft(loaded);
    return loaded;
  }, []);

  const runCheck = useCallback(async (fresh = false, needs?: SetupNeeds) => {
    setChecking(true);
    const query = new URLSearchParams();
    if (fresh) query.set("fresh", "1");
    for (const [key, value] of Object.entries(needs ?? {})) if (value !== null) query.set(key, value ? "yes" : "no");
    try {
      setCheck(await api.get<ServerCheck>(`/setup/server-check${query.size ? `?${query}` : ""}`));
      setCheckFailed(false);
    } catch {
      setCheckFailed(true);
    } finally {
      setChecking(false);
    }
  }, []);

  // a setup opened again (a reload, another tab with the same code): the saved choices, at the saved step
  useEffect(() => {
    if (!kept.code || !kept.licence || kept.done) return;
    loadDraft(kept.code)
      .then((loaded) => {
        const at = resumeAt(loaded.step, true);
        if (at !== "welcome") setResumed(true);
        setStep(at);
        void runCheck(false, loaded.needs);
      })
      .catch(() => setKept((k) => ({ ...k, licence: null })));
    // once, when the page opens
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    heading.current?.focus({ preventScroll: true });
    window.scrollTo({ top: 0, behavior: "smooth" });
  }, [step]);

  const go = async (to: SetupStep, changes: Partial<SetupDraft> = {}) => {
    setError("");
    setDirection(indexOf(to) >= indexOf(step) ? "forward" : "backward");
    if (draft && to !== "done") {
      setBusy(true);
      try {
        const body: Record<string, unknown> = { ...changes, step: to };
        if (changes.mail && smtpPassword) body.mail = { ...changes.mail, password: smtpPassword };
        setDraft(await api.put<SetupDraft>("/setup/draft", body));
        if (smtpPassword) setSmtpPassword("");
      } catch (err) {
        setError(errorText(err));
        setBusy(false);
        return;
      }
      setBusy(false);
    }
    setStep(to);
    if (indexOf(to) > indexOf(kept.reached)) setKept((k) => ({ ...k, reached: to }));
  };

  const update = (changes: Partial<SetupDraft>) => setDraft((d) => (d ? { ...d, ...changes } : d));

  const accept = async (acceptance: Acceptance) => {
    if (!kept.code.trim()) return setError(t.welcome.codeWrong);
    setBusy(true);
    setError("");
    try {
      const loaded = await loadDraft(kept.code.trim().toUpperCase());
      setKept((k) => ({ ...k, code: k.code.trim().toUpperCase(), licence: acceptance }));
      void runCheck(false, loaded.needs);
      setBusy(false);
      setDirection("forward");
      setStep("server");
      if (indexOf(kept.reached) < 1) setKept((k) => ({ ...k, reached: "server" }));
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  };

  const confirm = async (event: FormEvent) => {
    event.preventDefault();
    if (!draft) return;
    setBusy(true);
    setError("");
    try {
      await api.put<SetupDraft>("/setup/draft", { admin: draft.admin, step: "admin" });
      const me = await api.post<Session & { setup: SetupResult }>("/setup", {
        ...draft.admin,
        email: draft.admin.email?.trim(),
        setup_code: kept.code,
        password: password[0],
        licence: kept.licence,
      });
      setCsrfToken(me.csrf_token);
      setSetupCode("");
      // kept before the session is set: signed in, the page may be drawn anew in the account's language
      keep({ code: "", licence: null, reached: "done", done: { result: me.setup, draft } });
      client.setQueryData(keys.session, me);
      setResult(me.setup);
      setDirection("forward");
      setStep("done");
      setKept({ code: "", licence: null, reached: "done", done: { result: me.setup, draft } });
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  const finish = () => {
    keep(null);
    client.setQueryData(["setup"], (old: object | undefined) => ({ ...(old ?? {}), needed: false }));
    navigate("/", { replace: true, viewTransition: true });
  };

  const stopped = draft ? blocker(step, draft) : null;
  const person: Person = { email: draft?.admin.email ?? "", names: [draft?.admin.first_name ?? "", draft?.admin.last_name ?? ""] };

  return (
    <div className="setup">
      <header className="setup-top">
        <Brand to="/setup" />
        <span className="spacer" />
        <LanguageSwitch />
      </header>
      <SetupStepper current={step} reached={kept.reached} onOpen={(to) => void go(to)} />
      <main className="setup-main">
        <section key={step} className="setup-card" data-direction={direction}>
          {resumed && step !== "done" && <Callout>{t.resumed}</Callout>}
          {step === "welcome" && (
            <>
              <StepHead heading={heading} title={t.welcome.title} text={t.welcome.text} />
              <Field
                label={t.welcome.code}
                hint={fromLink ? t.welcome.codeFromLink : <>{t.welcome.codeHint(<code key="c">docker exec sudoo-panel sudoo.sh setup-code</code>)}</>}
              >
                <div className="input-group">
                  <KeyRound />
                  <Input className="mono" dir="ltr" placeholder="XXXX-XXXX-XXXX" value={kept.code} autoComplete="off"
                    onChange={(e) => setKept({ ...kept, code: e.target.value })} />
                </div>
              </Field>
              {kept.licence && draft ? (
                <>
                  <Callout tone="success">{t.welcome.accepted(kept.licence.version)}</Callout>
                  <Footer>
                    <Button variant="ghost" onClick={() => setKept({ ...kept, licence: null })}>{t.welcome.acceptAgain}</Button>
                    <Button variant="primary" icon={<ArrowRight className="flip-rtl" />} onClick={() => void go("server")}>{t.next}</Button>
                  </Footer>
                </>
              ) : (
                <>
                  <p className="muted">{t.welcome.licence}</p>
                  {licenceText.data ? (
                    <LicenceAcceptance text={licenceText.data} busy={busy} error={error} submitLabel={l.submit} onAccept={(a) => void accept(a)} />
                  ) : licenceText.isError ? (
                    <Callout tone="danger">{l.loadFailed}</Callout>
                  ) : (
                    <Spinner />
                  )}
                </>
              )}
            </>
          )}

          {step === "server" && draft && (
            <ServerStep heading={heading} check={check} failed={checkFailed} checking={checking} engine={draft.engine} needs={draft.needs}
              onEngine={(engine) => update({ engine })} onCheck={() => void runCheck(true, draft.needs)}
              onNeeds={(needs) => { update({ needs }); void runCheck(false, needs); }} />
          )}

          {step === "address" && draft && <AddressStep heading={heading} draft={draft} check={check} onChange={update} />}

          {step === "email" && draft && (
            <EmailStep heading={heading} draft={draft} password={smtpPassword} onPassword={setSmtpPassword} onChange={update} />
          )}

          {step === "github" && draft && (
            <>
              <StepHead heading={heading} title={t.github.title} text={t.github.text} optional />
              <button type="button" className="choice" aria-pressed={draft.github.connect} onClick={() => update({ github: { connect: !draft.github.connect } })}>
                <GitBranch />
                <span><span className="t">{t.github.connect}</span><br /><span className="d">{t.github.later}</span></span>
              </button>
              <p className="faint small">{t.github.skip}</p>
            </>
          )}

          {step === "account" && draft && <AccountStep heading={heading} draft={draft} onChange={update} />}

          {step === "admin" && draft && (
            <form className="setup-form" onSubmit={confirm} id="setup-confirm">
              <StepHead heading={heading} title={t.admin.title} text={t.admin.text} />
              <AdminFields draft={draft} onChange={update} />
              <PasswordFields value={password} onChange={setPassword} person={person} />
              <Confirming draft={draft} check={check} />
            </form>
          )}

          {step === "done" && result && draft && (
            <DoneStep heading={heading} draft={draft} result={result} onOpen={finish} />
          )}

          {error && step !== "welcome" && <Callout tone="danger">{error}</Callout>}
          {stopped && (
            <p className="setup-blocker" role="status">
              {{ nameNeeded: t.address.nameNeeded, mailIncomplete: t.email.mailIncomplete, linkCodeNeeded: t.account.linkCodeNeeded }[stopped]}
            </p>
          )}

          {step !== "welcome" && step !== "done" && (
            <Footer>
              <Button variant="ghost" icon={<ArrowLeft className="flip-rtl" />} disabled={busy} onClick={() => void go(back(step))}>{t.back}</Button>
              <span className="spacer" />
              {OPTIONAL.has(step) && <Tag>{t.optional}</Tag>}
              {step === "admin" ? (
                <Button type="submit" form="setup-confirm" variant="primary" loading={busy}
                  disabled={!draft?.admin.first_name || !draft?.admin.email?.includes("@") || !passwordReady(password, person)}>
                  {t.admin.submit}
                </Button>
              ) : (
                <Button variant="primary" loading={busy} disabled={!!stopped} icon={<ArrowRight className="flip-rtl" />}
                  onClick={() => draft && void go(next(step), changesOf(step, draft))}>
                  {t.next}
                </Button>
              )}
            </Footer>
          )}
        </section>
      </main>
    </div>
  );
}

/** The part of the draft a step saves when Next is pressed. */
function changesOf(step: SetupStep, draft: SetupDraft): Partial<SetupDraft> {
  switch (step) {
    case "server": return { engine: draft.engine, needs: draft.needs };
    case "address": return { exposure: draft.exposure, name: draft.name };
    case "email": return { mail: draft.mail };
    case "github": return { github: draft.github };
    case "account": return { account: draft.account };
    case "admin": return { admin: draft.admin };
    default: return {};
  }
}

function StepHead({ heading, title, text, optional }: { heading: React.RefObject<HTMLHeadingElement | null>; title: string; text: string; optional?: boolean }) {
  const t = useT(setupWords);
  return (
    <header className="setup-head">
      <h1 ref={heading} tabIndex={-1}>{title}{optional && <span className="faint small"> · {t.optional}</span>}</h1>
      <p>{text}</p>
    </header>
  );
}

const Footer = ({ children }: { children: ReactNode }) => <footer className="setup-footer">{children}</footer>;

function Fact({ icon, label, value, tone, note }: { icon: ReactNode; label: string; value: ReactNode; tone?: "good" | "caution"; note?: string }) {
  return (
    <div className="setup-fact" data-tone={tone}>
      {icon}
      <span className="label">{label}</span>
      <span className="value">{value}</span>
      {note && <span className="note">{note}</span>}
    </div>
  );
}

function ServerStep({
  heading, check, failed, checking, engine, needs, onEngine, onCheck, onNeeds,
}: {
  heading: React.RefObject<HTMLHeadingElement | null>;
  check: ServerCheck | null;
  failed: boolean;
  checking: boolean;
  engine: Engine;
  needs: SetupNeeds;
  onEngine: (engine: Engine) => void;
  onCheck: () => void;
  onNeeds: (needs: SetupNeeds) => void;
}) {
  const t = useT(setupWords).server;
  const gb = (mb: number) => Math.round(mb / 1024);
  const advice = check?.recommendation;
  const figures = check?.figures;
  const yesNo = (key: "grow" | "separation") => (
    <Segmented<"" | "yes" | "no">
      value={needs[key] === null ? "" : needs[key] ? "yes" : "no"}
      onChange={(value) => onNeeds({ ...needs, [key]: value === "yes" })}
      options={[{ value: "yes", label: t.yes }, { value: "no", label: t.no }]}
    />
  );
  const answered = Object.values(needs).some((value) => value !== null);
  const prepared = check ? check.engine.current : "docker";
  const port = (p: "80" | "443") => {
    const state = check?.ports[p];
    return !state ? t.unknown : state.free ? t.free : state.sudoo ? t.bySudoo : t.busy;
  };
  const portsTone = check && (check.ports["80"].free || check.ports["80"].sudoo) && (check.ports["443"].free || check.ports["443"].sudoo) ? "good" : "caution";
  return (
    <>
      <StepHead heading={heading} title={t.title} text={t.text} />
      {failed && <Callout tone="warning">{t.failed}</Callout>}
      <div className="setup-facts" aria-busy={checking}>
        <Fact icon={<MemoryStick />} label={t.memory} value={check ? t.gb(gb(check.memory_mb)) : <Spinner />}
          tone={check && advice?.recommended === "kubernetes" ? "good" : undefined} />
        <Fact icon={<Cpu />} label={t.processors} value={check ? t.cores(check.processors) : <Spinner />} />
        <Fact icon={<HardDrive />} label={t.disk} value={check ? t.gb(check.disk_free_gb) : <Spinner />} tone={check && check.disk_free_gb < 20 ? "caution" : undefined} />
        <Fact icon={<Server />} label={t.system} value={check ? <span dir="ltr">{check.system.name || t.unknown}</span> : <Spinner />} />
        <Fact icon={<Layers />} label={t.virtualisation} value={check ? t.kinds[check.virtualisation.kind] : <Spinner />}
          note={check?.virtualisation.from_installer ? `${check.virtualisation.name} · ${t.fromInstaller}` : undefined}
          tone={check?.virtualisation.kind === "container" ? "caution" : undefined} />
        <Fact icon={<Network />} label={t.ports} value={check ? <>{port("80")} · {port("443")}</> : <Spinner />} tone={check ? portsTone : undefined} />
      </div>
      <div className="row">
        <Button size="sm" variant="ghost" icon={<RefreshCw className={checking ? "spinning" : undefined} />} disabled={checking} onClick={onCheck}>{t.again}</Button>
      </div>

      {figures && (
        <p className="faint small setup-minimum">
          {t.minimum(figures.min_cpus, Math.round(figures.min_memory_mb / 1024))}{" "}
          {!figures.measured && <Tag tone="caution">{t.toBeMeasured}</Tag>}
        </p>
      )}

      {advice && !advice.fits ? (
        <div className="setup-why">
          <span className="faint small">{t.cannotRun}</span>
          <ul>{advice.reasons.map((reason) => <li key={reason} dir="auto">{reason}</li>)}</ul>
        </div>
      ) : (
        <div className="setup-section">
          <h2>{t.questions}</h2>
          <div className="setup-question"><span>{t.grow}</span>{yesNo("grow")}</div>
          <div className="setup-question"><span>{t.separation}</span>{yesNo("separation")}</div>
          <div className="setup-question">
            <span>{t.team}</span>
            <Segmented<"" | "one" | "team">
              value={needs.team === null ? "" : needs.team ? "team" : "one"}
              onChange={(value) => onNeeds({ ...needs, team: value === "team" })}
              options={[{ value: "one", label: t.onePerson }, { value: "team", label: t.aTeam }]}
            />
          </div>
        </div>
      )}

      <div className="setup-section">
        <h2>{t.engine}</h2>
        <p className="muted">{t.engineText}</p>
        <div className="setup-choices" role="radiogroup" aria-label={t.engine}>
          <EngineChoice name={t.docker} text={t.dockerText} icon={<Boxes />} chosen={engine === "docker"} recommended={advice?.recommended === "docker"} onChoose={() => onEngine("docker")} />
          <EngineChoice name={t.kubernetes} beta text={t.kubernetesText} icon={<Ship />}
            chosen={engine === "kubernetes"} recommended={advice?.recommended === "kubernetes"} onChoose={() => onEngine("kubernetes")} />
        </div>
        {advice && advice.fits && (
          <div className="setup-why" aria-live="polite">
            <span className="faint small">{t.why}</span>
            <p>
              {answered && advice.reason_code in t.reasons
                ? t.reasons[advice.reason_code as keyof typeof t.reasons]
                : answered ? advice.reason : t.answerFirst}
            </p>
            {advice.beta && advice.recommended === "kubernetes" && <p className="faint small">{t.betaDefault}</p>}
          </div>
        )}
        <p className="faint small">{t.noHa}</p>
        {check?.engine.wanted && <Callout>{t.wanted}</Callout>}
        {check && engine !== prepared && (
          <div className="setup-switch">
            <strong>{t.switchTitle}</strong>
            <p className="muted">{t.prepared(prepared === "kubernetes" ? t.kubernetes : t.docker)} {t.switchText}</p>
            <CopyField block value={check.engine.switch_command.replace("{engine}", engine)} />
          </div>
        )}
      </div>
    </>
  );
}

function EngineChoice({ name, text, icon, chosen, recommended, beta, onChoose }: {
  name: string; text: string; icon: ReactNode; chosen: boolean; recommended: boolean; beta?: boolean; onChoose: () => void;
}) {
  const t = useT(setupWords).server;
  return (
    <button type="button" className="choice setup-choice" role="radio" aria-checked={chosen} aria-pressed={chosen} onClick={onChoose}>
      {icon}
      <span>
        <span className="t">{name}</span>{" "}
        {beta && <Tag>{t.beta}</Tag>}{" "}
        {recommended && <Tag tone="accent">{t.recommended}</Tag>}
        <br />
        <span className="d">{text}</span>
      </span>
      <span className="setup-radio" aria-hidden="true" />
    </button>
  );
}

function AddressStep({ heading, draft, check, onChange }: { heading: React.RefObject<HTMLHeadingElement | null>; draft: SetupDraft; check: ServerCheck | null; onChange: (c: Partial<SetupDraft>) => void }) {
  const t = useT(setupWords).address;
  const busyPorts = check && ((!check.ports["80"].free && !check.ports["80"].sudoo) || (!check.ports["443"].free && !check.ports["443"].sudoo));
  return (
    <>
      <StepHead heading={heading} title={t.title} text={t.text} />
      <div className="setup-choices" role="radiogroup">
        {(["direct", "proxy", "tunnel"] as const).map((mode) => (
          <button key={mode} type="button" className="choice setup-choice" role="radio" aria-checked={draft.exposure === mode} aria-pressed={draft.exposure === mode}
            onClick={() => onChange({ exposure: mode })}>
            {mode === "direct" ? <Globe /> : mode === "proxy" ? <Network /> : <Link2 />}
            <span><span className="t">{t.modes[mode].title}</span><br /><span className="d">{t.modes[mode].text}</span></span>
            <span className="setup-radio" aria-hidden="true" />
          </button>
        ))}
      </div>
      {busyPorts && draft.exposure === "direct" && <Callout tone="warning">{t.portsBusy}</Callout>}
      <div className="setup-section">
        <h2>{t.name}</h2>
        <Segmented
          value={draft.name.kind}
          onChange={(kind) => onChange({ name: { ...draft.name, kind } })}
          options={(["server", "domain", "managed"] as const).map((kind) => ({ value: kind, label: t.names[kind].title, disabled: kind === "managed" && draft.exposure !== "direct" }))}
        />
        <p className="muted small">{t.names[draft.name.kind].text}</p>
        {draft.name.kind === "domain" && (
          <Field label={t.domain}>
            <Input dir="ltr" className="mono" placeholder="panel.example.com" value={draft.name.domain}
              onChange={(e) => onChange({ name: { kind: "domain", domain: e.target.value.trim().toLowerCase() } })} />
          </Field>
        )}
      </div>
      <p className="faint small">{t.restart}</p>
    </>
  );
}

function EmailStep({ heading, draft, password, onPassword, onChange }: {
  heading: React.RefObject<HTMLHeadingElement | null>; draft: SetupDraft; password: string; onPassword: (p: string) => void; onChange: (c: Partial<SetupDraft>) => void;
}) {
  const t = useT(setupWords).email;
  const mail = draft.mail;
  const set = (changes: Partial<SetupDraft["mail"]>) => onChange({ mail: { ...mail, ...changes } });
  const [to, setTo] = useState(draft.admin.email ?? "");
  const [testing, setTesting] = useState(false);
  const [outcome, setOutcome] = useState<{ ok: boolean; text: string } | null>(null);
  const test = async () => {
    setTesting(true);
    setOutcome(null);
    try {
      await api.post("/setup/test-email", { to, mail, ...(password ? { password } : {}) });
      setOutcome({ ok: true, text: t.sent(to) });
    } catch (err) {
      setOutcome({ ok: false, text: errorText(err) });
    } finally {
      setTesting(false);
    }
  };
  return (
    <>
      <StepHead heading={heading} title={t.title} text={t.text} optional />
      <Switch checked={mail.enabled} onChange={(enabled) => set({ enabled })} label={t.enable} />
      {mail.enabled && (
        <div className="setup-reveal">
          <div className="form-grid">
            <Field label={t.host}><Input dir="ltr" placeholder="smtp.example.com" value={mail.host} onChange={(e) => set({ host: e.target.value.trim() })} /></Field>
            <Field label={t.port}><Input dir="ltr" inputMode="numeric" value={String(mail.port)} onChange={(e) => set({ port: Number(e.target.value.replace(/\D/g, "")) || 0 })} /></Field>
            <Field label={t.security} className="full">
              <Segmented value={mail.security} onChange={(security) => set({ security, port: security === "ssl" ? 465 : security === "starttls" ? 587 : mail.port })}
                options={[{ value: "starttls", label: "STARTTLS" }, { value: "ssl", label: "SSL/TLS" }, { value: "none", label: "—" }]} />
            </Field>
            <Field label={t.username}><Input dir="ltr" autoComplete="off" value={mail.username} onChange={(e) => set({ username: e.target.value.trim() })} /></Field>
            <Field label={t.password} hint={mail.password_set && !password ? t.passwordSet : undefined}>
              <Input type="password" autoComplete="new-password" value={password} onChange={(e) => onPassword(e.target.value)} />
            </Field>
            <Field label={t.sender} hint={t.senderHint} className="full"><Input dir="ltr" value={mail.sender} onChange={(e) => set({ sender: e.target.value })} /></Field>
          </div>
          <div className="setup-test">
            <Field label={t.testTo}><Input dir="ltr" type="email" value={to} onChange={(e) => setTo(e.target.value)} /></Field>
            <Button icon={<Send className="flip-rtl" />} loading={testing} disabled={!to.includes("@") || !mail.host} onClick={() => void test()}>{t.test}</Button>
          </div>
          {outcome && <Callout tone={outcome.ok ? "success" : "danger"}>{outcome.text}</Callout>}
        </div>
      )}
    </>
  );
}

function AccountStep({ heading, draft, onChange }: { heading: React.RefObject<HTMLHeadingElement | null>; draft: SetupDraft; onChange: (c: Partial<SetupDraft>) => void }) {
  const t = useT(setupWords).account;
  const account = draft.account;
  const set = (changes: Partial<SetupDraft["account"]>) => onChange({ account: { ...account, ...changes } });
  return (
    <>
      <StepHead heading={heading} title={t.title} text={t.text} optional />
      <Switch checked={account.link} onChange={(link) => set({ link })} label={t.link} />
      {account.link ? (
        <div className="setup-reveal form-grid">
          <Field label={t.code} hint={account.from_install_code ? t.fromInstallCode : t.codeHint}>
            <Input dir="ltr" className="mono" autoComplete="off" value={account.link_code} onChange={(e) => set({ link_code: e.target.value.toUpperCase() })} />
          </Field>
          <Field label={t.company} hint={t.companyHint}>
            <Input dir="ltr" className="mono" value={account.company_label} onChange={(e) => set({ company_label: e.target.value.toLowerCase() })} />
          </Field>
        </div>
      ) : (
        <p className="faint small">{t.later}</p>
      )}
    </>
  );
}

function AdminFields({ draft, onChange }: { draft: SetupDraft; onChange: (c: Partial<SetupDraft>) => void }) {
  const t = useT(auth).setup;
  const admin = draft.admin;
  const set = (key: keyof SetupDraft["admin"]) => (e: React.ChangeEvent<HTMLInputElement>) => onChange({ admin: { ...admin, [key]: e.target.value } });
  return (
    <div className="form-grid">
      <Field label={t.firstName}><Input autoComplete="given-name" value={admin.first_name ?? ""} onChange={set("first_name")} /></Field>
      <Field label={t.lastName}><Input autoComplete="family-name" value={admin.last_name ?? ""} onChange={set("last_name")} /></Field>
      <Field label={t.email} hint={t.emailHint}><Input type="email" autoComplete="username email" value={admin.email ?? ""} onChange={set("email")} /></Field>
      <Field label={t.phone} hint={t.optional}><Input type="tel" dir="ltr" autoComplete="tel" value={admin.phone ?? ""} onChange={set("phone")} /></Field>
    </div>
  );
}

/** What the final confirm does, listed before it happens. */
function Confirming({ draft, check }: { draft: SetupDraft; check: ServerCheck | null }) {
  const t = useT(setupWords);
  const choices = [draft.exposure, draft.name.kind !== "server", draft.mail.enabled].filter(Boolean).length;
  return (
    <div className="setup-confirm">
      <span className="faint small">{t.admin.summary}</span>
      <ul>
        <li><CircleCheck /> {t.admin.willCreate(draft.admin.email || "…")}</li>
        <li><CircleCheck /> {t.admin.willApply(choices)}</li>
        {draft.account.link && <li><CircleCheck /> {t.admin.willLink}</li>}
        {check && draft.engine !== check.engine.current && <li><Server /> {t.server.switchTitle}</li>}
      </ul>
    </div>
  );
}

function DoneStep({ heading, draft, result, onOpen }: { heading: React.RefObject<HTMLHeadingElement | null>; draft: SetupDraft; result: SetupResult; onOpen: () => void }) {
  const t = useT(setupWords);
  const d = t.done;
  const [restarting, setRestarting] = useState(false);
  const navigate = useNavigate();
  const restart = async () => {
    setRestarting(true);
    try {
      await api.post("/server/restart");
    } catch {
      /* the answer may not come back: the panel is restarting */
    }
    setTimeout(() => location.assign("/"), 30000);
  };
  const engineName = (engine: Engine) => (engine === "kubernetes" ? t.server.kubernetes : t.server.docker);
  const rows: [string, ReactNode][] = [
    [d.engine, engineName(result.engine.current)],
    [d.address, `${t.address.modes[draft.exposure].title}${draft.name.kind === "domain" ? ` · ${draft.name.domain}` : draft.name.kind === "managed" ? " · sudoo.cloud" : ""}`],
    [d.email, draft.mail.enabled ? <span dir="ltr">{draft.mail.host}</span> : d.off],
    [d.github, result.github ? d.on : d.off],
    [d.account, result.linked ? d.linked(result.linked.account_name ?? "") : result.link_error ? d.linkFailed(result.link_error) : d.notLinked],
  ];
  return (
    <>
      <div className="setup-done-mark" aria-hidden="true">
        <svg viewBox="0 0 52 52"><circle cx="26" cy="26" r="24" /><path d="M15 27l7.5 7.5L37 19" /></svg>
      </div>
      <StepHead heading={heading} title={d.title} text={d.text} />
      <dl className="setup-summary">
        {rows.map(([label, value]) => (
          <div key={label}><dt>{label}</dt><dd>{value}</dd></div>
        ))}
      </dl>
      {result.engine.command && (
        <div className="setup-switch">
          <p className="muted">{d.engineNext}</p>
          <CopyField block value={result.engine.command} />
        </div>
      )}
      {result.restart_needed && (
        <Callout tone="warning">
          <strong>{d.restartTitle}</strong> {restarting ? d.restarting : d.restartText}
        </Callout>
      )}
      <Footer>
        {result.restart_needed && (
          <Button icon={<RotateCw />} loading={restarting} onClick={() => void restart()}>{d.restart}</Button>
        )}
        {result.github && <Button icon={<GitBranch />} onClick={() => navigate("/github")}>{d.connectGithub}</Button>}
        <span className="spacer" />
        <Button variant="primary" size="lg" icon={<ArrowRight className="flip-rtl" />} onClick={onOpen}>{d.open}</Button>
      </Footer>
    </>
  );
}

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
          <Input type="password" autoComplete="current-password" autoFocus value={current} onChange={(e) => setCurrent(e.target.value)} />
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
