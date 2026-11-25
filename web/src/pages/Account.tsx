import { useQueryClient } from "@tanstack/react-query";
import {
  BellRing,
  CircleCheck,
  Languages,
  KeyRound,
  LogIn,
  LogOut,
  Mail,
  MonitorSmartphone,
  RefreshCw,
  ShieldCheck,
  Smartphone,
  Trash2,
  UserRound,
} from "lucide-react";
import QRCode from "qrcode";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router";
import { api, call, errorText } from "@/api/client";
import { keys, useAccount } from "@/api/queries";
import type { Factor } from "@/api/types";
import { Page, PageHead, useMe } from "@/components/Shell";
import { PasswordFields, passwordReady } from "@/components/PasswordFields";
import { Brand } from "@/components/app/Brand";
import { GitHubMark } from "@/components/app/GitHubMark";
import { RecoveryCodesRow, RecoveryCodesSheet } from "@/components/RecoveryCodes";
import { Tag, Button, Callout, CopyField, Field, Skeleton, Switch } from "@/components/app/basics";
import { Input } from "@/components/ui/input";
import { SelectBox } from "@/components/app/select";
import { OtpInput } from "@/components/app/OtpInput";
import { Modal, useToast } from "@/components/app/overlay";
import { LOCALES, useT, type Locale } from "@/i18n";
import accountWords from "@/i18n/account";
import common from "@/i18n/common";
import { useSaveLanguage } from "@/components/LanguageSwitch";
import { ago } from "@/lib/format";
import type { Person } from "@/lib/password";

const ICON: Record<string, React.ReactNode> = { totp: <Smartphone />, email: <Mail /> };

function Qr({ value }: { value: string }) {
  const [svg, setSvg] = useState("");
  useEffect(() => {
    QRCode.toString(value, {
      type: "svg",
      margin: 0,
      errorCorrectionLevel: "M",
      color: { dark: "#1d1823", light: "#ffffff" },
    }).then(setSvg);
  }, [value]);
  return <div className="qr" dangerouslySetInnerHTML={{ __html: svg }} />;
}

/** Setting up one second factor: the app (QR code) or an e-mail address (confirmed with a code). Replacing one that
 * is already set up, or adding one next to another (`confirm`), starts with the account's password, as removing it
 * does. */
function Setup({
  factor,
  confirm,
  onDone,
  onCancel,
}: {
  factor: Factor;
  confirm: boolean;
  onDone: (recoveryCodes?: string[]) => void;
  onCancel: () => void;
}) {
  const toast = useToast();
  const replacing = factor.enrolled;
  const needsPassword = replacing || confirm;
  const [started, setStarted] = useState<{ secret?: string; otpauth_uri?: string; sent_to?: string } | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [shake, setShake] = useState(false);
  const t = useT(accountWords).factors;
  const c = useT(common).common;
  const label = t.labels[factor.name] ?? factor.label;
  const begin = async (body: Record<string, unknown> = {}) => {
    setBusy(true);
    setError("");
    try {
      setStarted(await api.post(`/account/second-factors/${factor.name}`, body));
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => {
    if (factor.name === "totp" && !needsPassword) begin();
  }, []);
  const finish = async (value = code) => {
    setBusy(true);
    setError("");
    try {
      const answer = await call<Factor[]>("PUT", `/account/second-factors/${factor.name}`, { code: value });
      if (replacing) toast("success", t.replaced(label), t.replacedText);
      else toast("success", t.isSetUp(label), t.isSetUpText);
      const codes = answer.meta?.recovery_codes;
      onDone(Array.isArray(codes) ? (codes as string[]) : undefined);
    } catch (e) {
      setCode("");
      setError(errorText(e));
      setShake(true);
      setTimeout(() => setShake(false), 350);
    } finally {
      setBusy(false);
    }
  };
  const passwordField = (
    <Field label={t.yourPassword} className="spacer">
      <Input
        type="password"
        autoComplete="current-password"
        autoFocus={factor.name === "totp"}
        value={password}
        onChange={(e) => setPassword(e.target.value)}
      />
    </Field>
  );
  return (
    <div className="stack">
      {replacing && !started && <span className="small muted">{t.replacingText}</span>}
      {factor.name === "totp" && needsPassword && !started ? (
        <form
          className="row"
          style={{ alignItems: "flex-end" }}
          onSubmit={(e) => {
            e.preventDefault();
            begin({ password });
          }}
        >
          {passwordField}
          <Button type="submit" variant="primary" loading={busy} disabled={!password}>
            {t.continue}
          </Button>
        </form>
      ) : factor.name === "totp" ? (
        started?.otpauth_uri ? (
          <div className="row" style={{ alignItems: "flex-start", gap: 20, flexWrap: "wrap" }}>
            <Qr value={started.otpauth_uri} />
            <div className="stack-sm" style={{ flex: 1, minWidth: 220 }}>
              <b>{t.scan}</b>
              <span className="small muted">{t.byHand}</span>
              <CopyField value={started.secret ?? ""} />
              <b style={{ marginTop: 10 }}>{t.typeCode}</b>
            </div>
          </div>
        ) : (
          <Skeleton height={190} />
        )
      ) : !started ? (
        <form
          className="row"
          style={{ alignItems: "flex-end", flexWrap: "wrap" }}
          onSubmit={(e) => {
            e.preventDefault();
            begin(needsPassword ? { email, password } : { email });
          }}
        >
          <Field label={t.emailAddress} className="spacer">
            <Input
              type="email"
              autoFocus
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@example.com"
            />
          </Field>
          {needsPassword && passwordField}
          <Button type="submit" variant="primary" loading={busy} disabled={!email || (needsPassword && !password)}>
            {t.sendCode}
          </Button>
        </form>
      ) : (
        <Callout tone="success">{t.sentTo(started.sent_to ?? "")}</Callout>
      )}
      {started && (
        <OtpInput
          value={code}
          onChange={setCode}
          onComplete={finish}
          shake={shake}
          label={factor.name === "totp" ? t.typeCode : undefined}
        />
      )}
      {error && <Callout tone="danger">{error}</Callout>}
      <div className="row">
        <Button variant="ghost" onClick={onCancel}>
          {c.cancel}
        </Button>
        <span className="spacer" />
        {started && (
          <Button variant="primary" loading={busy} disabled={code.length !== 6} onClick={() => finish()}>
            {t.confirm}
          </Button>
        )}
      </div>
    </div>
  );
}

export function Factors({ onChange }: { onChange?: () => void }) {
  const { data: account, refetch } = useAccount();
  const toast = useToast();
  const [setting, setSetting] = useState<Factor | null>(null);
  const [removing, setRemoving] = useState<Factor | null>(null);
  const [password, setPassword] = useState("");
  const [newCodes, setNewCodes] = useState<string[] | null>(null);
  const t = useT(accountWords).factors;
  const c = useT(common).common;
  if (!account) return <Skeleton height={150} />;
  const done = async (codes?: string[]) => {
    setSetting(null);
    await refetch();
    if (codes)
      setNewCodes(codes); // shown once; whoever waits for the change hears of it when the sheet closes
    else onChange?.();
  };
  return (
    <div className="stack">
      {!account.second_factors.length && <Callout tone="warning">{t.none}</Callout>}
      {account.second_factors.map((f) =>
        setting?.name === f.name ? (
          <div key={f.name} className="card pad">
            <Setup
              factor={f}
              confirm={account.second_factors.some((other) => other.enrolled)}
              onDone={done}
              onCancel={() => setSetting(null)}
            />
          </div>
        ) : (
          <div key={f.name} className="choice" style={{ cursor: "default" }}>
            {ICON[f.name] ?? <KeyRound />}
            <span style={{ flex: 1 }}>
              <span className="t row">
                {t.labels[f.name] ?? f.label}{" "}
                {f.enrolled && (
                  <Tag tone="positive" icon={<CircleCheck />}>
                    {t.onSince(ago(f.added_at))}
                  </Tag>
                )}
              </span>
              <span className="d">
                {f.name === "email" && account.email && f.enrolled ? t.codesGoTo(account.email) : t.text[f.name]}
              </span>
            </span>
            {f.enrolled ? (
              <>
                <Button size="sm" variant="ghost" icon={<RefreshCw />} onClick={() => setSetting(f)}>
                  {t.replace}
                </Button>
                <Button size="sm" variant="ghost" icon={<Trash2 />} onClick={() => setRemoving(f)}>
                  {t.remove}
                </Button>
              </>
            ) : (
              <Button size="sm" variant="soft" onClick={() => setSetting(f)}>
                {t.setUp}
              </Button>
            )}
          </div>
        ),
      )}
      {account.second_factors.some((f) => f.name === "totp" && f.enrolled) && (
        <RecoveryCodesRow
          state={account.recovery_codes}
          needed={account.recovery_codes_needed}
          onChange={() => refetch()}
        />
      )}
      {newCodes && (
        <RecoveryCodesSheet
          codes={newCodes}
          onClose={() => {
            setNewCodes(null);
            onChange?.();
          }}
        />
      )}
      {removing && (
        <Modal
          title={t.removeTitle(t.labels[removing.name] ?? removing.label)}
          description={t.removeText}
          onClose={() => setRemoving(null)}
          footer={
            <>
              <Button onClick={() => setRemoving(null)}>{c.cancel}</Button>
              <Button
                variant="danger"
                disabled={!password}
                onClick={async () => {
                  try {
                    await api.del(`/account/second-factors/${removing.name}`, { password });
                    toast("success", t.removed(t.labels[removing.name] ?? removing.label));
                    setRemoving(null);
                    setPassword("");
                    refetch();
                  } catch (e) {
                    toast("error", t.notRemoved, errorText(e));
                  }
                }}
              >
                {t.remove}
              </Button>
            </>
          }
        >
          <Field label={t.password}>
            <Input type="password" autoFocus value={password} onChange={(e) => setPassword(e.target.value)} />
          </Field>
        </Modal>
      )}
    </div>
  );
}

function PasswordCard() {
  const toast = useToast();
  const me = useMe();
  const person: Person = {
    username: me.user,
    email: me.email ?? undefined,
    names: [me.first_name ?? "", me.last_name ?? ""],
  };
  const [current, setCurrent] = useState("");
  const [password, setPassword] = useState<[string, string]>(["", ""]);
  const [busy, setBusy] = useState(false);
  const t = useT(accountWords).password;
  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    try {
      await api.put("/session/password", { current, new: password[0] });
      toast("success", t.changed, t.changedText);
      setCurrent("");
      setPassword(["", ""]);
    } catch (e) {
      toast("error", t.notChanged, errorText(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="card" onSubmit={submit}>
      <div className="card-head">
        <KeyRound size={18} />
        <h2>{t.title}</h2>
      </div>
      <div className="card-body stack">
        <Field label={t.current}>
          <Input
            type="password"
            autoComplete="current-password"
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
          />
        </Field>
        <PasswordFields label={t.new} value={password} onChange={setPassword} person={person} />
      </div>
      <div className="card-foot row">
        <span className="spacer" />
        <Button type="submit" variant="primary" loading={busy} disabled={!current || !passwordReady(password, person)}>
          {t.change}
        </Button>
      </div>
    </form>
  );
}

function ProfileCard() {
  const { data: account, refetch } = useAccount();
  const client = useQueryClient();
  const toast = useToast();
  const [form, setForm] = useState<Record<string, string> | null>(null);
  const [busy, setBusy] = useState(false);
  const t = useT(accountWords).profile;
  const all = useT(common);
  if (!account) return <Skeleton height={200} />;
  const values = form ?? {
    first_name: account.first_name ?? "",
    last_name: account.last_name ?? "",
    email: account.email ?? "",
    phone: account.phone ?? "",
  };
  const emailLocked = account.second_factors.some((f) => f.name === "email" && f.enrolled);
  const set = (key: string) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm({ ...values, [key]: e.target.value });
  const save = async () => {
    setBusy(true);
    try {
      const changes = emailLocked ? { ...values, email: undefined } : values;
      await api.patch("/account", changes);
      await refetch();
      await client.invalidateQueries({ queryKey: keys.session });
      setForm(null);
      toast("success", t.saved);
    } catch (e) {
      toast("error", t.notSaved, errorText(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="card">
      <div className="card-head">
        <UserRound />
        <h2>{t.title}</h2>
      </div>
      <div className="card-body form-grid">
        <Field label={t.firstName}>
          <Input value={values.first_name} onChange={set("first_name")} />
        </Field>
        <Field label={t.lastName}>
          <Input value={values.last_name} onChange={set("last_name")} />
        </Field>
        <Field label={t.email} hint={emailLocked ? t.emailLocked : undefined}>
          <Input type="email" value={values.email} disabled={emailLocked} onChange={set("email")} />
        </Field>
        <Field label={t.phone}>
          <Input type="tel" dir="ltr" value={values.phone} onChange={set("phone")} />
        </Field>
      </div>
      <div className="card-foot">
        <span className="xsmall faint">
          <span className="mono">{account.name}</span> · {all.roles[account.role] ?? account.role_label}
        </span>
        <span className="spacer" />
        <Button variant="primary" loading={busy} disabled={!form} onClick={save}>
          {all.common.save}
        </Button>
      </div>
    </section>
  );
}

/** The "new sign-in" e-mail, and signing out every other session. */
function SecurityCard() {
  const { data: account, refetch } = useAccount();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [ending, setEnding] = useState(false);
  const t = useT(accountWords).security;
  if (!account) return <Skeleton height={160} />;
  const mail = account.sign_in_email;
  const locked = mail.enabled && !mail.can_turn_off;
  const choose = async (on: boolean) => {
    setBusy(true);
    try {
      await api.patch("/account", { sign_in_email: on === mail.default ? null : on });
      await refetch();
      toast("success", on ? t.on : t.off);
    } catch (e) {
      toast("error", t.notChanged, errorText(e));
    } finally {
      setBusy(false);
    }
  };
  const chooseExpiry = async (on: boolean) => {
    setBusy(true);
    try {
      await api.patch("/account", { expiry_email: on ? null : false });
      await refetch();
      toast("success", on ? t.on : t.off);
    } catch (e) {
      toast("error", t.notChanged, errorText(e));
    } finally {
      setBusy(false);
    }
  };
  const endOthers = async () => {
    setEnding(true);
    try {
      await api.del("/account/sessions");
      toast("success", t.signedOutElsewhere, t.signedOutElsewhereText);
    } catch (e) {
      toast("error", t.notSignedOut, errorText(e));
    } finally {
      setEnding(false);
    }
  };
  const hint = !mail.mail_ready
    ? t.mailNotReady
    : !account.email
      ? t.noEmail
      : locked
        ? t.locked
        : t.hint(mail.default);
  return (
    <section className="card">
      <div className="card-head">
        <BellRing size={18} />
        <h2>{t.title}</h2>
      </div>
      <div className="card-body stack">
        <div className="row" style={{ alignItems: "flex-start" }}>
          <span className="spacer">
            <span className="t" style={{ fontWeight: 500 }}>
              {t.emailMe}
            </span>
            <br />
            <span className="small muted">{hint}</span>
          </span>
          <Switch checked={mail.enabled} disabled={busy || locked} onChange={choose} />
        </div>
        <div className="row" style={{ alignItems: "flex-start" }}>
          <span className="spacer">
            <span className="t" style={{ fontWeight: 500 }}>
              {t.expiryEmail}
            </span>
            <br />
            <span className="small muted">{t.expiryEmailText}</span>
          </span>
          <Switch checked={account.expiry_email !== false} disabled={busy || !account.email} onChange={chooseExpiry} />
        </div>
        <div className="row" style={{ alignItems: "flex-start" }}>
          <span className="spacer">
            <span className="t" style={{ fontWeight: 500 }}>
              {t.elsewhere}
            </span>
            <br />
            <span className="small muted">{t.elsewhereText}</span>
          </span>
          <Button size="sm" icon={<MonitorSmartphone />} loading={ending} onClick={endOthers}>
            {t.signOutOthers}
          </Button>
        </div>
      </div>
    </section>
  );
}

/** How this account signs in (chosen by an administrator, or the panel's default), and its GitHub account. */
function SignInCard() {
  const { data: account, refetch } = useAccount();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [unlinking, setUnlinking] = useState(false);
  const [password, setPassword] = useState("");
  const t = useT(accountWords).signIn;
  const c = useT(common).common;
  if (!account) return <Skeleton height={160} />;
  const ways = account.sign_in_methods;
  const link = async () => {
    setBusy(true);
    try {
      const { url } = await api.post<{ url: string }>("/account/github", {});
      window.location.assign(url);
    } catch (e) {
      toast("error", t.notLinked2, errorText(e));
      setBusy(false);
    }
  };
  const unlink = async () => {
    setBusy(true);
    try {
      await api.del("/account/github", ways.github && account.password_managed_here ? { password } : undefined);
      await refetch();
      setUnlinking(false);
      setPassword("");
      toast("success", t.unlinked);
    } catch (e) {
      toast("error", t.notUnlinked, errorText(e));
    } finally {
      setBusy(false);
    }
  };
  const ICONS: Record<string, React.ReactNode> = {
    password: <KeyRound size={16} />,
    email_code: <Mail size={16} />,
    github: <GitHubMark />,
  };
  return (
    <section className="card">
      <div className="card-head">
        <LogIn size={18} />
        <div className="spacer">
          <h2>{t.title}</h2>
          <div className="sub">{ways.own_choice ? t.chosen : t.byDefault}</div>
        </div>
      </div>
      <div className="card-body stack">
        {ways.methods.map((method) => {
          const offered = ways.offered[method];
          return (
            <div key={method} className="row" style={{ alignItems: "flex-start" }}>
              <span className="muted" style={{ paddingTop: 2 }}>
                {ICONS[method]}
              </span>
              <span className="spacer">
                <span className="t" style={{ fontWeight: 500 }}>
                  {t.methods[method]}
                </span>
                <br />
                <span className="small muted">
                  {!offered
                    ? t.off
                    : method === "github"
                      ? ways.github
                        ? t.linkedAs(ways.github.login)
                        : t.notLinked
                      : method === "password" && !ways.usable.includes("password")
                        ? t.noPassword
                        : t.methodText[method]}
                </span>
              </span>
              {method === "github" &&
                offered &&
                (ways.github ? (
                  <Button size="sm" variant="ghost" onClick={() => setUnlinking(true)}>
                    {t.unlink}
                  </Button>
                ) : (
                  <Button size="sm" icon={<GitHubMark />} loading={busy} onClick={link}>
                    {t.link}
                  </Button>
                ))}
            </div>
          );
        })}
      </div>
      {unlinking && (
        <Modal
          title={t.unlinkTitle}
          onClose={() => setUnlinking(false)}
          footer={
            <>
              <Button onClick={() => setUnlinking(false)}>{c.cancel}</Button>
              <Button variant="danger" loading={busy} onClick={unlink}>
                {t.unlink}
              </Button>
            </>
          }
        >
          <div className="stack">
            <p className="small muted">{t.unlinkText}</p>
            {account.password_managed_here && ways.usable.includes("password") && (
              <Field label={t.password} htmlFor="unlink-password">
                <Input
                  id="unlink-password"
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </Field>
            )}
          </div>
        </Modal>
      )}
    </section>
  );
}

/** The panel's language for this account, kept on the server so it follows the person to every browser. */
function LanguageCard() {
  const { data: account } = useAccount();
  const save = useSaveLanguage();
  const t = useT(accountWords).language;
  if (!account) return <Skeleton height={120} />;
  const language = account.language;
  return (
    <section className="card">
      <div className="card-head">
        <Languages size={18} />
        <div className="spacer">
          <h2>{t.title}</h2>
          <div className="sub">{t.text}</div>
        </div>
      </div>
      <div className="card-body">
        <SelectBox
          aria-label={t.choice}
          value={language.own_choice ?? ""}
          style={{ maxWidth: 320 }}
          onValueChange={(value) => save((value || null) as Locale | null)}
        >
          <option value="">{t.default(LOCALES[language.default].name)}</option>
          {(Object.keys(LOCALES) as Locale[]).map((code) => (
            <option key={code} value={code} lang={code}>
              {LOCALES[code].name}
            </option>
          ))}
        </SelectBox>
      </div>
    </section>
  );
}

export function AccountPage() {
  const me = useMe();
  const all = useT(common);
  const t = useT(accountWords).factors;
  return (
    <Page>
      <PageHead title={<bdi>{me.display_name}</bdi>}>{all.roles[me.role] ?? me.role_label}</PageHead>
      <ProfileCard />
      <LanguageCard />
      <div className="grid-2 stagger" style={{ alignItems: "start" }}>
        <section className="card">
          <div className="card-head">
            <ShieldCheck size={18} />
            <h2>{t.title}</h2>
          </div>
          <div className="card-body">
            <Factors />
          </div>
        </section>
        <div className="stack">
          <SignInCard />
          <PasswordCard />
          <SecurityCard />
        </div>
      </div>
    </Page>
  );
}

/** Shown after signing in when REQUIRE_SECOND_FACTOR applies and the account has none yet. */
export function SetupSecondFactorPage() {
  const client = useQueryClient();
  const navigate = useNavigate();
  const signOut = async () => {
    await api.del("/session").catch(() => undefined);
    client.setQueryData(keys.session, null);
    navigate("/login");
  };
  const t = useT(accountWords).setupFactor;
  const signOutLabel = useT(common).nav.signOut;
  return (
    <div className="auth-panel" style={{ minHeight: "100vh" }}>
      <div className="auth-card" style={{ width: "min(560px, 100%)" }}>
        <Brand />
        <header>
          <h1>{t.title}</h1>
          <p>{t.text}</p>
        </header>
        <Factors
          onChange={async () => {
            const session = await api.get<{ must_set_up_second_factor: boolean }>("/session");
            client.setQueryData(keys.session, session);
            if (!session.must_set_up_second_factor) navigate("/", { replace: true, viewTransition: true });
          }}
        />
        <Button variant="ghost" icon={<LogOut />} onClick={signOut} style={{ alignSelf: "flex-start" }}>
          {signOutLabel}
        </Button>
      </div>
    </div>
  );
}
