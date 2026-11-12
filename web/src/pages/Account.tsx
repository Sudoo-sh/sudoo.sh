import { useQueryClient } from "@tanstack/react-query";
import { CircleCheck, KeyRound, LogOut, Mail, ShieldCheck, Smartphone, Trash2 } from "lucide-react";
import QRCode from "qrcode";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router";
import { api, errorText } from "@/api/client";
import { keys, useAccount } from "@/api/queries";
import type { Factor } from "@/api/types";
import { Page, PageHead, useMe } from "@/components/Shell";
import { Brand } from "@/components/ui/Brand";
import { Badge, Button, Callout, CopyField, Field, Skeleton } from "@/components/ui/basics";
import { OtpInput } from "@/components/ui/OtpInput";
import { Modal, useToast } from "@/components/ui/overlay";
import { ago } from "@/lib/format";

const ICON: Record<string, React.ReactNode> = { totp: <Smartphone />, email: <Mail /> };
const TEXT: Record<string, string> = {
  totp: "A code from an app on your phone: Google Authenticator, Microsoft Authenticator, 1Password… Works offline.",
  email: "A code sent to your e-mail address each time you sign in.",
};

function Qr({ value }: { value: string }) {
  const [svg, setSvg] = useState("");
  useEffect(() => {
    QRCode.toString(value, { type: "svg", margin: 0, errorCorrectionLevel: "M", color: { dark: "#1d1823", light: "#ffffff" } }).then(setSvg);
  }, [value]);
  return <div className="qr" dangerouslySetInnerHTML={{ __html: svg }} />;
}

/** Setting up one second factor: the app (QR code) or an e-mail address (confirmed with a code). */
function Setup({ factor, onDone, onCancel }: { factor: Factor; onDone: () => void; onCancel: () => void }) {
  const toast = useToast();
  const [started, setStarted] = useState<{ secret?: string; otpauth_uri?: string; sent_to?: string } | null>(null);
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
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
    if (factor.name === "totp") begin();
  }, []);
  const finish = async (value = code) => {
    setBusy(true);
    setError("");
    try {
      await api.put(`/account/second-factors/${factor.name}`, { code: value });
      toast("success", `${factor.label} is set up`, "You will be asked for a code when you sign in.");
      onDone();
    } catch (e) {
      setCode("");
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="stack">
      {factor.name === "totp" ? (
        started?.otpauth_uri ? (
          <div className="row" style={{ alignItems: "flex-start", gap: 20, flexWrap: "wrap" }}>
            <Qr value={started.otpauth_uri} />
            <div className="stack-sm" style={{ flex: 1, minWidth: 220 }}>
              <b>1. Scan this code with your authenticator app.</b>
              <span className="small muted">Or type the key by hand:</span>
              <CopyField value={started.secret ?? ""} />
              <b style={{ marginTop: 10 }}>2. Type the 6-digit code it shows.</b>
            </div>
          </div>
        ) : (
          <Skeleton height={190} />
        )
      ) : !started ? (
        <form className="row" style={{ alignItems: "flex-end" }} onSubmit={(e) => { e.preventDefault(); begin({ email }); }}>
          <Field label="E-mail address" className="spacer">
            <input className="input" type="email" autoFocus value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" />
          </Field>
          <Button type="submit" variant="primary" loading={busy} disabled={!email}>Send a code</Button>
        </form>
      ) : (
        <Callout tone="success">We sent a code to {started.sent_to}. Type it below to confirm the address.</Callout>
      )}
      {started && <OtpInput value={code} onChange={setCode} onComplete={finish} />}
      {error && <Callout tone="danger">{error}</Callout>}
      <div className="row">
        <Button variant="ghost" onClick={onCancel}>Cancel</Button>
        <span className="spacer" />
        {started && <Button variant="primary" loading={busy} disabled={code.length !== 6} onClick={() => finish()}>Confirm</Button>}
      </div>
    </div>
  );
}

function Factors({ onChange }: { onChange?: () => void }) {
  const { data: account, refetch } = useAccount();
  const toast = useToast();
  const [setting, setSetting] = useState<Factor | null>(null);
  const [removing, setRemoving] = useState<Factor | null>(null);
  const [password, setPassword] = useState("");
  if (!account) return <Skeleton height={150} />;
  const done = async () => {
    setSetting(null);
    await refetch();
    onChange?.();
  };
  return (
    <div className="stack">
      {!account.second_factors.length && <Callout tone="warning">No second factor can be used on this server. Ask the administrator to enable one.</Callout>}
      {account.second_factors.map((f) =>
        setting?.name === f.name ? (
          <div key={f.name} className="card pad"><Setup factor={f} onDone={done} onCancel={() => setSetting(null)} /></div>
        ) : (
          <div key={f.name} className="choice" style={{ cursor: "default" }}>
            <span className="icon">{ICON[f.name] ?? <KeyRound />}</span>
            <span style={{ flex: 1 }}>
              <span className="t row">{f.label} {f.enrolled && <Badge tone="success" icon={<CircleCheck />}>on since {ago(f.added_at)}</Badge>}</span>
              <span className="d">{f.name === "email" && account.email && f.enrolled ? `Codes go to ${account.email}.` : TEXT[f.name]}</span>
            </span>
            {f.enrolled ? (
              <Button size="sm" variant="ghost" icon={<Trash2 />} onClick={() => setRemoving(f)}>Remove</Button>
            ) : (
              <Button size="sm" variant="soft" onClick={() => setSetting(f)}>Set up</Button>
            )}
          </div>
        ),
      )}
      {removing && (
        <Modal title={`Remove ${removing.label}?`} description="Your other sessions end. Type your password to confirm." onClose={() => setRemoving(null)}
          footer={<><Button onClick={() => setRemoving(null)}>Cancel</Button><Button variant="danger" disabled={!password} onClick={async () => {
            try {
              await api.del(`/account/second-factors/${removing.name}`, { password });
              toast("success", `${removing.label} removed`);
              setRemoving(null);
              setPassword("");
              refetch();
            } catch (e) {
              toast("error", "Not removed", errorText(e));
            }
          }}>Remove</Button></>}>
          <Field label="Password"><input className="input" type="password" autoFocus value={password} onChange={(e) => setPassword(e.target.value)} /></Field>
        </Modal>
      )}
    </div>
  );
}

function PasswordCard() {
  const toast = useToast();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [busy, setBusy] = useState(false);
  const mismatch = again.length > 0 && next !== again;
  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    try {
      await api.put("/session/password", { current, new: next });
      toast("success", "Password changed", "Your other sessions were signed out.");
      setCurrent(""); setNext(""); setAgain("");
    } catch (e) {
      toast("error", "Not changed", errorText(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="card" onSubmit={submit}>
      <div className="card-head"><KeyRound size={18} /><h2>Password</h2></div>
      <div className="card-body stack">
        <Field label="Current password"><input className="input" type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} /></Field>
        <Field label="New password" hint="At least 12 characters; a few words make a good one.">
          <input className="input" type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} />
        </Field>
        <Field label="New password again" error={mismatch && "The two new passwords are not the same."}>
          <input className="input" type="password" autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} aria-invalid={mismatch} />
        </Field>
      </div>
      <div className="card-foot row">
        <span className="spacer" />
        <Button type="submit" variant="primary" loading={busy} disabled={!current || next.length < 12 || next !== again}>Change password</Button>
      </div>
    </form>
  );
}

export function AccountPage() {
  const me = useMe();
  const { data: account } = useAccount();
  return (
    <Page>
      <PageHead eyebrow="My account" title={me.user}>
        {me.role_label}
        {account?.email ? ` · ${account.email}` : ""}
      </PageHead>
      <div className="grid-2 stagger" style={{ alignItems: "start" }}>
        <section className="card">
          <div className="card-head">
            <ShieldCheck size={18} />
            <h2>Second factor</h2>
            {account?.second_factor_required && <Badge tone="primary">required</Badge>}
          </div>
          <div className="card-body"><Factors /></div>
        </section>
        {account?.password_managed_here !== false && <PasswordCard />}
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
  return (
    <div className="auth-panel" style={{ minHeight: "100vh" }}>
      <div className="auth-card" style={{ width: "min(560px, 100%)" }}>
        <Brand />
        <header>
          <span className="badge primary" style={{ alignSelf: "flex-start" }}><ShieldCheck /> One more step</span>
          <h1>Protect your account</h1>
          <p>This panel asks for a second factor: a password alone will not be enough to sign in. Set one up now; it takes a minute.</p>
        </header>
        <Factors onChange={async () => {
          const session = await api.get<{ must_set_up_second_factor: boolean }>("/session");
          client.setQueryData(keys.session, session);
          if (!session.must_set_up_second_factor) navigate("/", { replace: true, viewTransition: true });
        }} />
        <Button variant="ghost" icon={<LogOut />} onClick={signOut} style={{ alignSelf: "flex-start" }}>Sign out</Button>
      </div>
    </div>
  );
}
