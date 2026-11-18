import { KeyRound, MoreHorizontal, Pencil, ShieldOff, Sparkles, Trash2, UserPlus, Users } from "lucide-react";
import { useState } from "react";
import { api, errorText } from "@/api/client";
import { useCall, useRoles, useUsers } from "@/api/queries";
import type { Role, User } from "@/api/types";
import { Page, PageHead, useMe } from "@/components/Shell";
import { PasswordFields, passwordReady } from "@/components/PasswordFields";
import { Button, Callout, CopyField, Empty, Field, Segmented, Skeleton, Switch, Tag } from "@/components/ui/basics";
import { Menu, Modal, useToast } from "@/components/ui/overlay";
import { ago } from "@/lib/format";
import { generatePassword, type Person } from "@/lib/password";
import { useT } from "@/i18n";
import words from "@/i18n/admin";
import common from "@/i18n/common";

function Secret({ title, value, mustChange, onClose }: { title: string; value: string; mustChange: boolean; onClose: () => void }) {
  const t = useT(words).secret;
  return (
    <Modal title={title} description={mustChange ? t.mustChange : t.signIn} onClose={onClose}
      footer={<Button variant="primary" onClick={onClose}>{t.done}</Button>}>
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
function PasswordChoiceFields({ choice, onChange, person }: { choice: PasswordChoice; onChange: (choice: PasswordChoice) => void; person: Person }) {
  const t = useT(words).passwordChoice;
  return (
    <div className="stack">
      <Segmented<PasswordChoice["mode"]>
        value={choice.mode}
        onChange={(mode) => onChange({ ...choice, mode })}
        options={[
          { value: "generate", label: <><Sparkles size={14} /> {t.generate}</> },
          { value: "type", label: <><KeyRound size={14} /> {t.type}</> },
        ]}
      />
      {choice.mode === "type" && (
        <div className="form-grid">
          <PasswordFields value={choice.value} onChange={(value) => onChange({ ...choice, value })} person={person} />
          <div className="full row" style={{ gap: 8 }}>
            <Button size="sm" variant="ghost" icon={<Sparkles />} onClick={() => { const made = generatePassword(); onChange({ ...choice, value: [made, made] }); }}>
              {t.suggest}
            </Button>
            {choice.value[0] && choice.value[0] === choice.value[1] && <CopyField value={choice.value[0]} />}
          </div>
        </div>
      )}
      {choice.mode === "type" && (
        <Switch checked={choice.mustChange} onChange={(mustChange) => onChange({ ...choice, mustChange })} label={t.mustChange} />
      )}
    </div>
  );
}

const choiceReady = (choice: PasswordChoice, person: Person) => choice.mode === "generate" || passwordReady(choice.value, person);
const choiceBody = (choice: PasswordChoice) =>
  choice.mode === "type" ? { password: choice.value[0], must_change_password: choice.mustChange } : { must_change_password: true };

type Form = { role: Role; first_name: string; last_name: string; email: string; phone: string };
const EMPTY: Form = { role: "developer", first_name: "", last_name: "", email: "", phone: "" };
type Made = { title: string; password: string; mustChange: boolean };

function AccountForm({ user, onClose, onDone }: { user?: User; onClose: () => void; onDone: (made?: Made) => void }) {
  const me = useMe();
  const toast = useToast();
  const { data } = useRoles();
  const [form, setForm] = useState<Form>(
    user ? { role: user.role, first_name: user.first_name ?? "", last_name: user.last_name ?? "", email: user.email ?? "", phone: user.phone ?? "" } : EMPTY,
  );
  const [password, setPassword] = useState<PasswordChoice>(GENERATE);
  const [busy, setBusy] = useState(false);
  const t = useT(words);
  const all = useT(common);
  const set = (key: keyof Form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [key]: e.target.value });
  const roles = (data?.roles ?? []).filter((r) => r.name !== "super_admin" || me.role === "super_admin");
  const self = user?.name === me.user;
  const person: Person = { username: user?.name, email: form.email, names: [form.first_name, form.last_name] };
  const ready = /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(form.email.trim()) && (!!user || choiceReady(password, person));
  const save = async () => {
    if (self && form.role !== user!.role && !confirm(t.form.confirmOwnRole)) return;
    setBusy(true);
    try {
      const body = { ...form, email: form.email.trim() };
      if (user) {
        await api.patch(`/users/${user.name}`, body);
        toast("success", t.form.saved(user.name));
        onDone();
      } else {
        const made = await api.post<User & { password: string }>("/users", { ...body, ...choiceBody(password) });
        onDone({ title: t.form.passwordFor(made.email ?? made.name), password: made.password, mustChange: made.must_change_password });
      }
    } catch (error) {
      toast("error", t.form.notSaved, errorText(error));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal wide title={user ? t.form.editTitle(user.display_name) : t.form.newTitle} description={user ? <span className="mono">{user.name}</span> : t.form.newText} onClose={onClose}
      footer={<><Button onClick={onClose}>{all.common.cancel}</Button><Button variant="primary" loading={busy} disabled={!ready} onClick={save}>{user ? all.common.save : all.common.create}</Button></>}>
      <div className="stack">
        <div className="form-grid">
          <Field label={t.form.firstName}><input className="input" autoFocus value={form.first_name} onChange={set("first_name")} /></Field>
          <Field label={t.form.lastName}><input className="input" value={form.last_name} onChange={set("last_name")} /></Field>
          <Field label={t.form.email} hint={user ? t.form.emailHintEdit : t.form.emailHintNew}>
            <input className="input" type="email" dir="ltr" autoComplete="off" value={form.email} onChange={set("email")} />
          </Field>
          <Field label={t.form.phone} hint={t.form.optional}><input className="input" type="tel" dir="ltr" value={form.phone} onChange={set("phone")} /></Field>
        </div>
        <div className="field">
          <span className="label">{t.form.role}</span>
          <div className="grid-2">
            {roles.map((role) => (
              <button key={role.name} type="button" className="choice" aria-pressed={form.role === role.name} onClick={() => setForm({ ...form, role: role.name })}>
                <span>
                  <span className="t">{all.roles[role.name] ?? role.label}</span>
                  <br />
                  <span className="d">{t.accounts.roleDescriptions[role.name] ?? role.description}</span>
                </span>
              </button>
            ))}
          </div>
        </div>
        {!user && (
          <div className="field">
            <span className="label">{t.form.password}</span>
            <PasswordChoiceFields choice={password} onChange={setPassword} person={person} />
          </div>
        )}
      </div>
    </Modal>
  );
}

function ResetPassword({ user, onClose, onDone }: { user: User; onClose: () => void; onDone: (made: Made) => void }) {
  const toast = useToast();
  const [choice, setChoice] = useState<PasswordChoice>(GENERATE);
  const [busy, setBusy] = useState(false);
  const t = useT(words).reset;
  const c = useT(common).common;
  const person: Person = { username: user.name, email: user.email ?? undefined, names: [user.first_name ?? "", user.last_name ?? ""] };
  const submit = async () => {
    setBusy(true);
    try {
      const answer = await api.post<{ password: string }>(`/users/${user.name}/password`, choiceBody(choice));
      onDone({ title: t.title(user.display_name), password: answer.password, mustChange: choice.mode === "generate" || choice.mustChange });
    } catch (error) {
      toast("error", t.notReset, errorText(error));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal wide title={t.title(user.display_name)} description={t.text} onClose={onClose}
      footer={<><Button onClick={onClose}>{c.cancel}</Button><Button variant="primary" loading={busy} disabled={!choiceReady(choice, person)} onClick={submit}>{t.submit}</Button></>}>
      <PasswordChoiceFields choice={choice} onChange={setChoice} person={person} />
    </Modal>
  );
}

export function AccountsPage() {
  const me = useMe();
  const { data: users, isPending, refetch } = useUsers();
  const toast = useToast();
  const [editing, setEditing] = useState<User | "new" | null>(null);
  const [secret, setSecret] = useState<Made | null>(null);
  const [resetting, setResetting] = useState<User | null>(null);
  const refresh = [["users"]] as const;
  const resetFactors = useCall((user: string) => api.del(`/users/${user}/second-factors`), refresh);
  const remove = useCall((user: string) => api.del(`/users/${user}`), refresh);
  const fail = (title: string) => (error: unknown) => toast("error", title, errorText(error));
  const all = useT(common);
  const t = useT(words).accounts;
  const manageable = (u: User) => u.role !== "super_admin" || me.role === "super_admin";

  return (
    <Page>
      <PageHead title={t.title} actions={<Button variant="primary" icon={<UserPlus />} onClick={() => setEditing("new")}>{t.new}</Button>}>
        {t.lead}
      </PageHead>
      <div className="card">
        {isPending ? (
          <div className="card-body"><Skeleton height={160} /></div>
        ) : !users?.length ? (
          <Empty icon={<Users />} title={t.none} />
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>{t.columns.account}</th><th>{t.columns.role}</th><th>{t.columns.factor}</th><th>{t.columns.lastSignIn}</th><th /></tr></thead>
              <tbody>
                {users.map((u) => (
                  <tr key={u.name}>
                    <td>
                      <div className="row" style={{ gap: 12 }}>
                        <span className="avatar">{(u.first_name?.[0] ?? u.name[0]) + (u.last_name?.[0] ?? "")}</span>
                        <div style={{ minWidth: 0 }}>
                          <div className="truncate"><bdi>{u.display_name}</bdi>{u.name === me.user && <span className="faint">{t.you}</span>}</div>
                          <div className="xsmall faint truncate">{u.email ? <bdi dir="ltr">{u.email}</bdi> : <span className="mono">{u.name}</span>}</div>
                        </div>
                      </div>
                    </td>
                    <td>
                      <Tag tone={u.role === "super_admin" ? "accent" : undefined}>{all.roles[u.role] ?? u.role_label}</Tag>
                      {u.must_change_password && <span className="xsmall faint">{t.pendingPassword}</span>}
                    </td>
                    <td className="small">{u.second_factors.length ? u.second_factors.map((f) => t.factors[f] ?? f).join(t.separator) : <span className="faint">{t.noFactor}</span>}</td>
                    <td className="small muted">{ago(u.last_sign_in_at)}</td>
                    <td className="actions">
                      {manageable(u) && (
                        <Menu trigger={(toggle) => <Button size="sm" variant="ghost" icon={<MoreHorizontal />} onClick={toggle} aria-label={t.actionsFor(u.name)} />}>
                          {(close) => (
                            <>
                              <button onClick={() => { close(); setEditing(u); }}><Pencil /> {t.edit}</button>
                              {u.name !== me.user && (
                                <>
                                  <button onClick={() => { close(); setResetting(u); }}><KeyRound /> {t.setPassword}</button>
                                  <button disabled={!u.second_factors.length} onClick={() => { close(); resetFactors.mutate(u.name, { onSuccess: () => toast("success", t.factorsRemoved(u.name)), onError: fail(t.notRemoved) }); }}>
                                    <ShieldOff /> {t.removeFactors}
                                  </button>
                                  <hr />
                                  <button className="danger" onClick={() => { close(); if (confirm(t.confirmDelete(u.display_name))) remove.mutate(u.name, { onSuccess: () => toast("success", t.deleted(u.name)), onError: fail(t.notDeleted) }); }}>
                                    <Trash2 /> {t.delete}
                                  </button>
                                </>
                              )}
                            </>
                          )}
                        </Menu>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {editing && (
        <AccountForm
          user={editing === "new" ? undefined : editing}
          onClose={() => setEditing(null)}
          onDone={(made) => {
            setEditing(null);
            refetch();
            if (made) setSecret(made);
          }}
        />
      )}
      {resetting && (
        <ResetPassword user={resetting} onClose={() => setResetting(null)} onDone={(made) => { setResetting(null); refetch(); setSecret(made); }} />
      )}
      {secret && <Secret title={secret.title} value={secret.password} mustChange={secret.mustChange} onClose={() => setSecret(null)} />}
    </Page>
  );
}
