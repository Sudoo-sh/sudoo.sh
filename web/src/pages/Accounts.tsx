import {
  ExternalLink,
  KeyRound,
  MailPlus,
  MoreHorizontal,
  Pencil,
  Power,
  PowerOff,
  Send,
  ShieldOff,
  Sparkles,
  Trash2,
  UserPlus,
  Users,
  XCircle,
} from "lucide-react";
import { useEffect, useState } from "react";
import { api, errorText } from "@/api/client";
import { useAccessRequests, useCall, useInvitations, useRoles, useUsers } from "@/api/queries";
import { Link } from "react-router";
import type { AccountRequest, Invitation, InvitationPolicy, Role, SignInMethod, User } from "@/api/types";
import { GitHubMark } from "@/components/app/GitHubMark";
import { Checkbox } from "@/components/ui/checkbox";
import { Page, PageHead, useMe } from "@/components/Shell";
import { ACCOUNTS_TOUR } from "@/lib/tour";
import { PasswordFields, passwordReady } from "@/components/PasswordFields";
import { Button, Callout, CopyField, Empty, Field, Segmented, Skeleton, Switch, Tag } from "@/components/app/basics";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Input } from "@/components/ui/input";
import { SelectBox } from "@/components/app/select";
import { SelectItem } from "@/components/ui/select";
import {
  ACCOUNT_TABS,
  accountTab,
  firstTab,
  tabCounts,
  withoutRequiredFactor,
  type AccountTab,
} from "@/lib/account-tabs";
import { useConfirm } from "@/components/app/confirm";
import { Menu, MenuItem, MenuSeparator, Modal, useToast } from "@/components/app/overlay";
import { ago, fullDateTime } from "@/lib/format";
import { generatePassword, type Person } from "@/lib/password";
import { useT } from "@/i18n";
import words from "@/i18n/admin";
import common from "@/i18n/common";

function Secret({
  title,
  value,
  mustChange,
  onClose,
}: {
  title: string;
  value: string;
  mustChange: boolean;
  onClose: () => void;
}) {
  const t = useT(words).secret;
  return (
    <Modal
      title={title}
      description={mustChange ? t.mustChange : t.signIn}
      onClose={onClose}
      footer={
        <Button variant="primary" onClick={onClose}>
          {t.done}
        </Button>
      }
    >
      <div className="stack">
        <CopyField value={value} block />
        <Callout tone="warning">{t.once}</Callout>
      </div>
    </Modal>
  );
}

type PasswordChoice = { mode: "generate" | "type"; value: [string, string]; mustChange: boolean };
const GENERATE: PasswordChoice = { mode: "generate", value: ["", ""], mustChange: true };

/** Generate a password, or type one that follows the rules; and whether its owner must replace it. */
function PasswordChoiceFields({
  choice,
  onChange,
  person,
}: {
  choice: PasswordChoice;
  onChange: (choice: PasswordChoice) => void;
  person: Person;
}) {
  const t = useT(words).passwordChoice;
  return (
    <div className="stack">
      <Segmented<PasswordChoice["mode"]>
        value={choice.mode}
        onChange={(mode) => onChange({ ...choice, mode })}
        options={[
          {
            value: "generate",
            label: (
              <>
                <Sparkles size={14} /> {t.generate}
              </>
            ),
          },
          {
            value: "type",
            label: (
              <>
                <KeyRound size={14} /> {t.type}
              </>
            ),
          },
        ]}
      />
      {choice.mode === "type" && (
        <div className="form-grid">
          <PasswordFields value={choice.value} onChange={(value) => onChange({ ...choice, value })} person={person} />
          <div className="full row" style={{ gap: 8 }}>
            <Button
              size="sm"
              variant="ghost"
              icon={<Sparkles />}
              onClick={() => {
                const made = generatePassword();
                onChange({ ...choice, value: [made, made] });
              }}
            >
              {t.suggest}
            </Button>
            {choice.value[0] && choice.value[0] === choice.value[1] && <CopyField value={choice.value[0]} />}
          </div>
        </div>
      )}
      {choice.mode === "type" && (
        <Switch
          checked={choice.mustChange}
          onChange={(mustChange) => onChange({ ...choice, mustChange })}
          label={t.mustChange}
        />
      )}
    </div>
  );
}

const choiceReady = (choice: PasswordChoice, person: Person) =>
  choice.mode === "generate" || passwordReady(choice.value, person);
const choiceBody = (choice: PasswordChoice) =>
  choice.mode === "type"
    ? { password: choice.value[0], must_change_password: choice.mustChange }
    : { must_change_password: true };

type Form = { role: Role; first_name: string; last_name: string; email: string; phone: string };
const EMPTY: Form = { role: "developer", first_name: "", last_name: "", email: "", phone: "" };
type Made = { title: string; password: string; mustChange: boolean };
const METHODS: SignInMethod[] = ["password", "email_code", "github"];

/** How one account signs in: the panel's usual ways, or a choice of its own (auth/methods.py). */
function SignInFields({
  methods,
  fallback,
  onChange,
}: {
  methods: SignInMethod[] | null;
  fallback: SignInMethod[];
  onChange: (methods: SignInMethod[] | null) => void;
}) {
  const t = useT(words).signIn;
  const shown = methods ?? fallback;
  const toggle = (method: SignInMethod, on: boolean) =>
    onChange(METHODS.filter((m) => (m === method ? on : shown.includes(m))));
  return (
    <fieldset className="stack-sm">
      <legend className="label">{t.title}</legend>
      <label className="check-row">
        <Checkbox
          checked={methods === null}
          onCheckedChange={(value) => onChange(value === true ? null : [...fallback])}
        />
        <span>{t.useDefault}</span>
      </label>
      {methods === null ? (
        <p className="xsmall faint">{t.defaultHint(fallback.map((m) => t.methods[m]).join(" · "))}</p>
      ) : (
        <div className="stack-sm" style={{ paddingInlineStart: 28 }}>
          {METHODS.map((method) => (
            <label key={method} className="check-row">
              <Checkbox checked={shown.includes(method)} onCheckedChange={(value) => toggle(method, value === true)} />
              <span>
                {t.methods[method]} <span className="xsmall faint">{t.methodHints[method]}</span>
              </span>
            </label>
          ))}
          {!shown.length && (
            <p className="xsmall" style={{ color: "var(--negative)" }}>
              {t.noMethod}
            </p>
          )}
        </div>
      )}
    </fieldset>
  );
}

/** Edit an account, or add one: by invitation (the person chooses their own password from the e-mailed link), or,
 *  when the Invitations settings allow it (by default only while e-mail is not set up), with a password set here. */
function AccountForm({
  user,
  policy,
  onClose,
  onDone,
}: {
  user?: User;
  policy?: InvitationPolicy;
  onClose: () => void;
  onDone: (made?: Made) => void;
}) {
  const me = useMe();
  const toast = useToast();
  const [confirm, dialog] = useConfirm();
  const { data } = useRoles();
  const [form, setForm] = useState<Form>(
    user
      ? {
          role: user.role,
          first_name: user.first_name ?? "",
          last_name: user.last_name ?? "",
          email: user.email ?? "",
          phone: user.phone ?? "",
        }
      : EMPTY,
  );
  const [password, setPassword] = useState<PasswordChoice>(GENERATE);
  const [methods, setMethods] = useState<SignInMethod[] | null>(
    user?.sign_in_methods_chosen ? user.sign_in_methods : null,
  );
  const [githubLogin, setGithubLogin] = useState(user?.github_login ?? "");
  const [runLimit, setRunLimit] = useState(user?.running_limit == null ? "" : String(user.running_limit));
  const canInvite = !!policy?.mail_ready;
  const [mode, setMode] = useState<"invite" | "password">(
    canInvite || !policy?.password_allowed ? "invite" : "password",
  );
  const [busy, setBusy] = useState(false);
  const t = useT(words);
  const all = useT(common);
  const set = (key: keyof Form) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm({ ...form, [key]: e.target.value });
  const roles = (data?.roles ?? []).filter((r) => r.name !== "super_admin" || me.role === "super_admin");
  const self = user?.name === me.user;
  const inviting = !user && mode === "invite";
  const person: Person = { username: user?.name, email: form.email, names: [form.first_name, form.last_name] };
  const emailOk = /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(form.email.trim());
  const ready =
    emailOk &&
    (methods === null || methods.length > 0) &&
    /^\d{0,4}$/.test(runLimit.trim()) &&
    (!!user || (inviting ? canInvite : choiceReady(password, person)));
  const save = async () => {
    if (self && form.role !== user!.role && !(await confirm(t.form.confirmOwnRole))) return;
    setBusy(true);
    try {
      const body = { ...form, email: form.email.trim() };
      if (user) {
        const changed = methods !== null ? methods : user.sign_in_methods_chosen ? null : undefined;
        await api.patch(`/users/${user.name}`, {
          ...body,
          ...(changed !== undefined ? { sign_in_methods: changed } : {}),
          ...(user.github ? {} : { github_login: githubLogin.trim() }),
          running_limit: runLimit.trim() === "" ? null : Number(runLimit),
        });
        toast("success", t.form.saved(user.name));
        onDone();
      } else if (inviting) {
        await api.post<Invitation>("/invitations", {
          email: body.email,
          role: body.role,
          first_name: body.first_name,
          last_name: body.last_name,
          github_login: githubLogin.trim(),
        });
        toast("success", t.invite.sent(body.email));
        onDone();
      } else {
        const made = await api.post<User & { password: string }>("/users", { ...body, ...choiceBody(password) });
        onDone({
          title: t.form.passwordFor(made.email ?? made.name),
          password: made.password,
          mustChange: made.must_change_password,
        });
      }
    } catch (error) {
      toast("error", inviting ? t.invite.notSent : t.form.notSaved, errorText(error));
    } finally {
      setBusy(false);
    }
  };
  const title = user ? t.form.editTitle(user.display_name) : inviting ? t.invite.title : t.form.newTitle;
  const description = user ? <span className="mono">{user.name}</span> : inviting ? t.invite.text : t.form.newText;
  const submit = user ? all.common.save : inviting ? t.invite.send : all.common.create;
  return (
    <>
      <Modal
        wide
        title={title}
        description={description}
        onClose={onClose}
        footer={
          <>
            <Button onClick={onClose}>{all.common.cancel}</Button>
            <Button
              variant="primary"
              icon={inviting ? <Send /> : undefined}
              loading={busy}
              disabled={!ready}
              onClick={save}
            >
              {submit}
            </Button>
          </>
        }
      >
        <div className="stack">
          {!user && policy?.password_allowed && canInvite && (
            <div className="field">
              <span className="label">{t.invite.how}</span>
              <Segmented<"invite" | "password">
                value={mode}
                onChange={setMode}
                options={[
                  {
                    value: "invite",
                    label: (
                      <>
                        <MailPlus size={14} /> {t.invite.byEmail}
                      </>
                    ),
                  },
                  {
                    value: "password",
                    label: (
                      <>
                        <KeyRound size={14} /> {t.invite.byPassword}
                      </>
                    ),
                  },
                ]}
              />
            </div>
          )}
          {!user && !canInvite && (
            <Callout tone={policy?.password_allowed ? "info" : "warning"}>
              {policy?.password_allowed ? t.invite.passwordNote : t.invite.noMail}{" "}
              <Link to="/settings?tab=email" viewTransition>
                {t.invite.setUpMail}
              </Link>
            </Callout>
          )}
          {!user && canInvite && mode === "password" && <Callout tone="info">{t.invite.passwordAlways}</Callout>}
          <div className="form-grid">
            <Field
              label={t.form.email}
              hint={
                user
                  ? t.form.emailHintEdit
                  : inviting && policy
                    ? t.invite.expiresIn(policy.invitation_hours)
                    : t.form.emailHintNew
              }
              className={inviting ? "full" : undefined}
            >
              <Input
                type="email"
                dir="ltr"
                autoComplete="off"
                autoFocus={!user}
                value={form.email}
                onChange={set("email")}
              />
            </Field>
            {!inviting && (
              <Field label={t.form.phone} hint={t.form.optional}>
                <Input type="tel" dir="ltr" value={form.phone} onChange={set("phone")} />
              </Field>
            )}
            <Field label={t.form.firstName} hint={inviting ? t.invite.namesOptional : undefined}>
              <Input autoFocus={!!user} value={form.first_name} onChange={set("first_name")} />
            </Field>
            <Field label={t.form.lastName} hint={inviting ? t.invite.namesOptional : undefined}>
              <Input value={form.last_name} onChange={set("last_name")} />
            </Field>
          </div>
          <div className="field">
            <span className="label">{t.form.role}</span>
            <div className="grid-2">
              {roles.map((role) => (
                <button
                  key={role.name}
                  type="button"
                  className="choice"
                  aria-pressed={form.role === role.name}
                  onClick={() => setForm({ ...form, role: role.name })}
                >
                  <span>
                    <span className="t">{all.roles[role.name] ?? role.label}</span>
                    <br />
                    <span className="d">{t.accounts.roleDescriptions[role.name] ?? role.description}</span>
                  </span>
                </button>
              ))}
            </div>
          </div>
          {(inviting || (user && !user.github)) && (
            <Field label={t.signIn.githubLogin} hint={t.signIn.githubLoginHint}>
              <div className="input-group">
                <GitHubMark />
                <Input
                  dir="ltr"
                  autoComplete="off"
                  spellCheck={false}
                  placeholder="octocat"
                  maxLength={40}
                  value={githubLogin}
                  onChange={(e) => setGithubLogin(e.target.value)}
                />
              </div>
            </Field>
          )}
          {user && (
            <Field
              label={t.signIn.runLimit}
              hint={t.signIn.runLimitHint(t.signIn.runLimitSetting(user.running_limit_default))}
            >
              <Input
                dir="ltr"
                inputMode="numeric"
                autoComplete="off"
                placeholder={t.signIn.runLimitSetting(user.running_limit_default)}
                maxLength={4}
                value={runLimit}
                onChange={(e) => setRunLimit(e.target.value)}
              />
            </Field>
          )}
          {user && (
            <SignInFields
              methods={methods}
              fallback={user.sign_in_methods_chosen ? ["password", "github"] : user.sign_in_methods}
              onChange={setMethods}
            />
          )}
          {!user && !inviting && (
            <div className="field">
              <span className="label">{t.form.password}</span>
              <PasswordChoiceFields choice={password} onChange={setPassword} person={person} />
            </div>
          )}
        </div>
      </Modal>
      {dialog}
    </>
  );
}

function ResetPassword({ user, onClose, onDone }: { user: User; onClose: () => void; onDone: (made: Made) => void }) {
  const toast = useToast();
  const [choice, setChoice] = useState<PasswordChoice>(GENERATE);
  const [busy, setBusy] = useState(false);
  const t = useT(words).reset;
  const c = useT(common).common;
  const person: Person = {
    username: user.name,
    email: user.email ?? undefined,
    names: [user.first_name ?? "", user.last_name ?? ""],
  };
  const submit = async () => {
    setBusy(true);
    try {
      const answer = await api.post<{ password: string }>(`/users/${user.name}/password`, choiceBody(choice));
      onDone({
        title: t.title(user.display_name),
        password: answer.password,
        mustChange: choice.mode === "generate" || choice.mustChange,
      });
    } catch (error) {
      toast("error", t.notReset, errorText(error));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal
      wide
      title={t.title(user.display_name)}
      description={t.text}
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>{c.cancel}</Button>
          <Button variant="primary" loading={busy} disabled={!choiceReady(choice, person)} onClick={submit}>
            {t.submit}
          </Button>
        </>
      }
    >
      <PasswordChoiceFields choice={choice} onChange={setChoice} person={person} />
    </Modal>
  );
}

/** An invitation not accepted yet, among the accounts: who, as what, until when; send again or revoke. */
function InvitationRow({
  invitation: v,
  manageable,
  onResend,
  onRevoke,
}: {
  invitation: Invitation;
  manageable: boolean;
  onResend: () => void;
  onRevoke: () => void;
}) {
  const all = useT(common);
  const t = useT(words).accounts;
  const i = useT(words).invite;
  const name = [v.first_name, v.last_name].filter(Boolean).join(" ");
  return (
    <TableRow className="invited">
      <TableCell>
        <div className="row" style={{ gap: 12 }}>
          <span className="avatar invited-avatar" aria-hidden="true">
            <MailPlus size={15} />
          </span>
          <div style={{ minWidth: 0 }}>
            <div className="truncate">{name ? <bdi>{name}</bdi> : <bdi dir="ltr">{v.email}</bdi>}</div>
            {name && (
              <div className="xsmall faint truncate">
                <bdi dir="ltr">{v.email}</bdi>
              </div>
            )}
          </div>
        </div>
      </TableCell>
      <TableCell>
        <Tag tone={v.role === "super_admin" ? "accent" : undefined}>{all.roles[v.role] ?? v.role_label}</Tag>
      </TableCell>
      <TableCell className="small faint">—</TableCell>
      <TableCell className="small">
        {v.expired ? (
          <Tag tone="negative">{i.expired}</Tag>
        ) : (
          <span className="invited-state">
            <Tag tone="caution">{i.invited}</Tag>{" "}
            <time className="muted" dateTime={v.expires_at} title={fullDateTime(v.expires_at)}>
              {i.expiresAt(ago(v.expires_at))}
            </time>
          </span>
        )}
        {v.sent_count > 1 && <div className="xsmall faint">{i.sentTimes(v.sent_count)}</div>}
      </TableCell>
      <TableCell className="actions">
        {manageable && (
          <Menu
            trigger={(toggle) => (
              <Button
                size="sm"
                variant="ghost"
                icon={<MoreHorizontal />}
                onClick={toggle}
                aria-label={t.actionsFor(v.email)}
              />
            )}
          >
            {(close) => (
              <>
                <MenuItem
                  onSelect={() => {
                    close();
                    onResend();
                  }}
                >
                  <Send /> {i.resend}
                </MenuItem>
                {v.link && (
                  <MenuItem asChild>
                    <Link to={v.link} onClick={close}>
                      <ExternalLink /> {i.openLink}
                    </Link>
                  </MenuItem>
                )}
                <MenuSeparator />
                <MenuItem
                  danger
                  onSelect={() => {
                    close();
                    onRevoke();
                  }}
                >
                  <XCircle /> {i.revoke}
                </MenuItem>
              </>
            )}
          </Menu>
        )}
      </TableCell>
    </TableRow>
  );
}

/** Disable or enable an account. Both ask for a reason, which the audit log keeps. */
function SwitchAccount({
  user,
  busy,
  onClose,
  onSave,
}: {
  user: User;
  busy: boolean;
  onClose: () => void;
  onSave: (reason: string) => void;
}) {
  const all = useT(common);
  const a = useT(words).access;
  const [reason, setReason] = useState("");
  const enabling = !!user.disabled;
  return (
    <Modal
      title={enabling ? a.enableTitle(user.display_name) : a.disableTitle(user.display_name)}
      description={enabling ? a.enableText : a.disableText}
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>{all.common.cancel}</Button>
          <Button
            variant={enabling ? "primary" : "danger"}
            loading={busy}
            disabled={!reason.trim()}
            onClick={() => onSave(reason.trim())}
          >
            {enabling ? a.enable : a.disable}
          </Button>
        </>
      }
    >
      <Field label={a.reasonLabel} hint={a.reasonHint}>
        <Input value={reason} maxLength={300} onChange={(e) => setReason(e.target.value)} autoFocus />
      </Field>
    </Modal>
  );
}

function DecideRequest({
  request: r,
  how,
  onClose,
  onDone,
}: {
  request: AccountRequest;
  how: "approve" | "reject";
  onClose: () => void;
  onDone: () => void;
}) {
  const me = useMe();
  const toast = useToast();
  const all = useT(common);
  const a = useT(words).access;
  const { data } = useRoles();
  const roles = (data?.roles ?? []).filter((x) => x.name !== "super_admin" || me.role === "super_admin");
  const [role, setRole] = useState("viewer");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const approving = how === "approve";
  const save = async () => {
    setBusy(true);
    try {
      await api.post(`/access-requests/${r.id}/${how}`, approving ? { role } : { reason });
      toast("success", approving ? a.approved(r.email) : a.rejected(r.email));
      onDone();
    } catch (error) {
      toast("error", approving ? a.notApproved : a.notRejected, errorText(error));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal
      title={approving ? a.approveTitle(r.email) : a.rejectTitle(r.email)}
      description={approving ? a.approveText : a.rejectText}
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>{all.common.cancel}</Button>
          <Button variant="primary" loading={busy} disabled={approving ? !role : !reason.trim()} onClick={save}>
            {approving ? a.approveSend : a.rejectSend}
          </Button>
        </>
      }
    >
      {approving ? (
        <Field label={a.role}>
          <SelectBox value={role} onValueChange={setRole} aria-label={a.role}>
            {roles.map((x) => (
              <SelectItem key={x.name} value={x.name}>
                {all.roles[x.name] ?? x.label}
              </SelectItem>
            ))}
          </SelectBox>
        </Field>
      ) : (
        <Field label={a.reason}>
          <Input value={reason} maxLength={300} onChange={(e) => setReason(e.target.value)} />
        </Field>
      )}
    </Modal>
  );
}

function RequestsTable({
  requests,
  onDecide,
}: {
  requests: AccountRequest[];
  onDecide: (request: AccountRequest, how: "approve" | "reject") => void;
}) {
  const a = useT(words).access;
  const state = (r: AccountRequest) =>
    r.status === "pending" ? a.statePending : r.status === "approved" ? a.stateApproved : a.stateRejected;
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>{a.requestColumns.person}</TableHead>
          <TableHead>{a.requestColumns.asked}</TableHead>
          <TableHead>{a.requestColumns.state}</TableHead>
          <TableHead />
        </TableRow>
      </TableHeader>
      <TableBody>
        {requests.map((r) => {
          const name = [r.first_name, r.last_name].filter(Boolean).join(" ");
          return (
            <TableRow key={r.id}>
              <TableCell>
                <div className="truncate">{name ? <bdi>{name}</bdi> : <bdi dir="ltr">{r.email}</bdi>}</div>
                {name && (
                  <div className="xsmall faint truncate">
                    <bdi dir="ltr">{r.email}</bdi>
                  </div>
                )}
              </TableCell>
              <TableCell className="small muted">
                <div>{a.submittedAgo(ago(r.requested_at))}</div>
                <div className="xsmall faint">{a.confirmedAgo(ago(r.confirmed_at))}</div>
              </TableCell>
              <TableCell className="small">
                <Tag tone={r.status === "pending" ? "caution" : r.status === "rejected" ? "negative" : undefined}>
                  {state(r)}
                </Tag>
                {r.reason && <div className="xsmall faint">{r.reason}</div>}
                {r.decided_by && <div className="xsmall faint">{a.decidedBy(r.decided_by)}</div>}
              </TableCell>
              <TableCell className="actions">
                {r.status === "pending" && (
                  <div className="row" style={{ gap: 8, justifyContent: "flex-end" }}>
                    <Button size="sm" variant="primary" onClick={() => onDecide(r, "approve")}>
                      {a.approve}
                    </Button>
                    <Button size="sm" onClick={() => onDecide(r, "reject")}>
                      {a.reject}
                    </Button>
                  </div>
                )}
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}

export function AccountsPage() {
  const me = useMe();
  const { data: users, isPending, refetch } = useUsers();
  const { data: invited, refetch: refetchInvitations } = useInvitations();
  const { data: asked, refetch: refetchAsked } = useAccessRequests();
  const [tab, setTab] = useState<AccountTab | null>(null);
  const [deciding, setDeciding] = useState<{ request: AccountRequest; how: "approve" | "reject" } | null>(null);
  const a = useT(words).access;
  const counts = tabCounts(users ?? [], invited?.invitations ?? [], asked?.requests ?? []);
  const loaded = users !== undefined && invited !== undefined && asked !== undefined;
  // the first tab is Pending approval when someone waits; after that the choice is the person's
  useEffect(() => {
    if (loaded && tab === null) setTab(firstTab(counts));
  }, [loaded, tab, counts]);
  const current: AccountTab = tab ?? "active";
  const toast = useToast();
  const [editing, setEditing] = useState<User | "new" | null>(null);
  const [secret, setSecret] = useState<Made | null>(null);
  const [resetting, setResetting] = useState<User | null>(null);
  const [switching, setSwitching] = useState<User | null>(null);
  const [confirm, askDialog] = useConfirm();
  const refresh = [["users"]] as const;
  const resetFactors = useCall((user: string) => api.del(`/users/${user}/second-factors`), refresh);
  const setDisabled = useCall(
    (p: { user: string; disabled: boolean; reason: string }) =>
      api.post(`/users/${p.user}/${p.disabled ? "disable" : "enable"}`, { reason: p.reason }),
    refresh,
  );
  const remove = useCall((user: string) => api.del(`/users/${user}`), refresh);
  const unlink = useCall((user: string) => api.del(`/users/${user}/github`), refresh);
  const s = useT(words).signIn;
  const resend = useCall((id: string) => api.post<Invitation>(`/invitations/${id}/resend`), [["invitations"]]);
  const revoke = useCall((id: string) => api.del(`/invitations/${id}`), [["invitations"]]);
  const i = useT(words).invite;
  const fail = (title: string) => (error: unknown) => toast("error", title, errorText(error));
  const all = useT(common);
  const t = useT(words).accounts;
  const manageable = (u: User) => u.role !== "super_admin" || me.role === "super_admin";

  return (
    <Page>
      <PageHead
        title={t.title}
        tour={ACCOUNTS_TOUR}
        actions={
          <Button variant="primary" icon={<UserPlus />} onClick={() => setEditing("new")} data-tour="accounts-new">
            {invited?.policy.mail_ready || !invited?.policy.password_allowed ? i.open : t.new}
          </Button>
        }
      >
        {t.lead}
      </PageHead>
      {(() => {
        const bare = withoutRequiredFactor(users ?? []);
        return bare.length ? (
          <div style={{ marginBottom: 12 }}>
            <Callout tone="warning">{a.noSecondFactor(bare.length)}</Callout>
          </div>
        ) : null;
      })()}
      <div style={{ marginBottom: 12 }}>
        <Segmented<AccountTab>
          value={current}
          onChange={setTab}
          options={ACCOUNT_TABS.map((value) => ({
            value,
            label: `${a.tabs[value]} (${counts[value]})`,
          }))}
        />
      </div>
      <div className="card">
        {current === "pending" && !!asked?.policy.awaiting_confirmation && (
          <div className="card-body small muted">{a.awaiting(asked.policy.awaiting_confirmation)}</div>
        )}
        {current === "pending" ? (
          asked?.requests.length ? (
            <RequestsTable requests={asked.requests} onDecide={(request, how) => setDeciding({ request, how })} />
          ) : (
            <Empty icon={<Users />} title={a.nonePending}>
              {asked?.policy.enabled ? a.domains(asked.policy.domains.join(", ")) : a.off}
            </Empty>
          )
        ) : isPending ? (
          <div className="card-body">
            <Skeleton height={160} />
          </div>
        ) : current === "invited" && !invited?.invitations.length ? (
          <Empty icon={<MailPlus />} title={a.noneInvited} />
        ) : current === "disabled" && !counts.disabled ? (
          <Empty icon={<PowerOff />} title={a.noneDisabled} />
        ) : !users?.length ? (
          <Empty icon={<Users />} title={t.none} />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t.columns.account}</TableHead>
                <TableHead>{t.columns.role}</TableHead>
                <TableHead>{t.columns.factor}</TableHead>
                <TableHead>{t.columns.lastSignIn}</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {(current === "invited" ? [] : users.filter((u) => accountTab(u) === current)).map((u, index) => (
                <TableRow
                  key={u.name}
                  className={u.disabled ? "disabled-account" : undefined}
                  data-tour={index === 0 ? "accounts-rows" : undefined}
                >
                  <TableCell>
                    <div className="row" style={{ gap: 12 }}>
                      <span className="avatar">{(u.first_name?.[0] ?? u.name[0]) + (u.last_name?.[0] ?? "")}</span>
                      <div style={{ minWidth: 0 }}>
                        <div className="truncate">
                          <bdi>{u.display_name}</bdi>
                          {u.name === me.user && <span className="faint">{t.you}</span>}
                        </div>
                        <div className="xsmall faint truncate">
                          {u.email ? <bdi dir="ltr">{u.email}</bdi> : <span className="mono">{u.name}</span>}
                        </div>
                      </div>
                    </div>
                  </TableCell>
                  <TableCell>
                    <Tag tone={u.role === "super_admin" ? "accent" : undefined}>
                      {all.roles[u.role] ?? u.role_label}
                    </Tag>
                    {u.disabled && (
                      <span style={{ marginInlineStart: 6 }}>
                        <Tag tone="negative">{a.disabledTag}</Tag>
                      </span>
                    )}
                    {u.disabled && u.disabled_reason && (
                      <div className="xsmall faint">{a.disabledBecause(u.disabled_reason)}</div>
                    )}
                    {u.must_change_password && <span className="xsmall faint">{t.pendingPassword}</span>}
                    {u.github && (
                      <div className="xsmall faint row" style={{ gap: 4 }}>
                        <GitHubMark size={12} /> <bdi dir="ltr">@{u.github.login}</bdi>
                      </div>
                    )}
                  </TableCell>
                  <TableCell className="small">
                    {u.second_factors.length ? (
                      u.second_factors.map((f) => t.factors[f] ?? f).join(t.separator)
                    ) : u.second_factor_required && !u.disabled ? (
                      <Tag tone="caution">{a.noSecondFactorTag}</Tag>
                    ) : (
                      <span className="faint">{t.noFactor}</span>
                    )}
                  </TableCell>
                  <TableCell className="small muted">{ago(u.last_sign_in_at)}</TableCell>
                  <TableCell className="actions">
                    {manageable(u) && (
                      <Menu
                        trigger={(toggle) => (
                          <Button
                            size="sm"
                            variant="ghost"
                            icon={<MoreHorizontal />}
                            onClick={toggle}
                            aria-label={t.actionsFor(u.name)}
                          />
                        )}
                      >
                        {(close) => (
                          <>
                            <MenuItem
                              onSelect={() => {
                                close();
                                setEditing(u);
                              }}
                            >
                              <Pencil /> {t.edit}
                            </MenuItem>
                            {u.name !== me.user && (
                              <>
                                <MenuItem
                                  onSelect={() => {
                                    close();
                                    setResetting(u);
                                  }}
                                >
                                  <KeyRound /> {t.setPassword}
                                </MenuItem>
                                {u.github && (
                                  <MenuItem
                                    onSelect={() => {
                                      close();
                                      unlink.mutate(u.name, {
                                        onSuccess: () => toast("success", s.unlinked(u.name)),
                                        onError: fail(s.notUnlinked),
                                      });
                                    }}
                                  >
                                    <GitHubMark /> {s.unlink}
                                  </MenuItem>
                                )}
                                <MenuItem
                                  onSelect={() => {
                                    close();
                                    setSwitching(u);
                                  }}
                                >
                                  {u.disabled ? <Power /> : <PowerOff />} {u.disabled ? a.enable : a.disable}
                                </MenuItem>
                                <MenuItem
                                  disabled={!u.second_factors.length}
                                  onSelect={() => {
                                    close();
                                    resetFactors.mutate(u.name, {
                                      onSuccess: () => toast("success", t.factorsRemoved(u.name)),
                                      onError: fail(t.notRemoved),
                                    });
                                  }}
                                >
                                  <ShieldOff /> {t.removeFactors}
                                </MenuItem>
                                <MenuSeparator />
                                <MenuItem
                                  danger
                                  onSelect={() => {
                                    close();
                                    void confirm(t.confirmDelete(u.display_name), {
                                      action: all.common.delete,
                                      danger: true,
                                    }).then((yes) => {
                                      if (yes)
                                        remove.mutate(u.name, {
                                          onSuccess: () => toast("success", t.deleted(u.name)),
                                          onError: fail(t.notDeleted),
                                        });
                                    });
                                  }}
                                >
                                  <Trash2 /> {t.delete}
                                </MenuItem>
                              </>
                            )}
                          </>
                        )}
                      </Menu>
                    )}
                  </TableCell>
                </TableRow>
              ))}
              {(current === "invited" ? (invited?.invitations ?? []) : []).map((v) => (
                <InvitationRow
                  key={v.id}
                  invitation={v}
                  manageable={v.role !== "super_admin" || me.role === "super_admin"}
                  onResend={() =>
                    resend.mutate(v.id, {
                      onSuccess: () => toast("success", i.resent(v.email), i.resendNote),
                      onError: fail(i.notSent),
                    })
                  }
                  onRevoke={() =>
                    void confirm(i.confirmRevoke(v.email), { action: i.revoke, danger: true }).then((yes) => {
                      if (yes)
                        revoke.mutate(v.id, {
                          onSuccess: () => toast("success", i.revoked(v.email)),
                          onError: fail(i.notRevoked),
                        });
                    })
                  }
                />
              ))}
            </TableBody>
          </Table>
        )}
      </div>
      {editing && (
        <AccountForm
          user={editing === "new" ? undefined : editing}
          policy={invited?.policy}
          onClose={() => setEditing(null)}
          onDone={(made) => {
            setEditing(null);
            refetch();
            refetchInvitations();
            if (made) setSecret(made);
          }}
        />
      )}
      {deciding && (
        <DecideRequest
          request={deciding.request}
          how={deciding.how}
          onClose={() => setDeciding(null)}
          onDone={() => {
            setDeciding(null);
            asked && void refetchAsked();
            refetchInvitations();
          }}
        />
      )}
      {resetting && (
        <ResetPassword
          user={resetting}
          onClose={() => setResetting(null)}
          onDone={(made) => {
            setResetting(null);
            refetch();
            setSecret(made);
          }}
        />
      )}
      {askDialog}
      {switching && (
        <SwitchAccount
          user={switching}
          busy={setDisabled.isPending}
          onClose={() => setSwitching(null)}
          onSave={(reason) =>
            setDisabled.mutate(
              { user: switching.name, disabled: !switching.disabled, reason },
              {
                onSuccess: () => {
                  toast("success", switching.disabled ? a.enabled(switching.name) : a.disabled(switching.name));
                  setSwitching(null);
                },
                onError: fail(switching.disabled ? a.notEnabled : a.notDisabled),
              },
            )
          }
        />
      )}
      {secret && (
        <Secret
          title={secret.title}
          value={secret.password}
          mustChange={secret.mustChange}
          onClose={() => setSecret(null)}
        />
      )}
    </Page>
  );
}
