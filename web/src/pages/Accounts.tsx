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

function Secret({ title, value, mustChange, onClose }: { title: string; value: string; mustChange: boolean; onClose: () => void }) {
  return (
    <Modal title={title} description={mustChange ? "They choose their own password the first time they sign in with it" : "They sign in with it"} onClose={onClose}
      footer={<Button variant="primary" onClick={onClose}>Done</Button>}>
      <div className="stack">
        <CopyField value={value} block />
        <Callout tone="warning">Shown once. Give it to the person through a safe channel.</Callout>
      </div>
    </Modal>
  );
}

type PasswordChoice = { mode: "generate" | "type"; value: [string, string]; mustChange: boolean };
const GENERATE: PasswordChoice = { mode: "generate", value: ["", ""], mustChange: true };

/** Generate a password, or type one that follows the rules; and whether its owner must replace it. */
function PasswordChoiceFields({ choice, onChange, person }: { choice: PasswordChoice; onChange: (choice: PasswordChoice) => void; person: Person }) {
  return (
    <div className="stack">
      <Segmented<PasswordChoice["mode"]>
        value={choice.mode}
        onChange={(mode) => onChange({ ...choice, mode })}
        options={[
          { value: "generate", label: <><Sparkles size={14} /> Generate</> },
          { value: "type", label: <><KeyRound size={14} /> Type a password</> },
        ]}
      />
      {choice.mode === "type" && (
        <div className="form-grid">
          <PasswordFields value={choice.value} onChange={(value) => onChange({ ...choice, value })} person={person} />
          <div className="full row" style={{ gap: 8 }}>
            <Button size="sm" variant="ghost" icon={<Sparkles />} onClick={() => { const made = generatePassword(); onChange({ ...choice, value: [made, made] }); }}>
              Suggest a strong one
            </Button>
            {choice.value[0] && choice.value[0] === choice.value[1] && <CopyField value={choice.value[0]} />}
          </div>
        </div>
      )}
      {choice.mode === "type" && (
        <Switch checked={choice.mustChange} onChange={(mustChange) => onChange({ ...choice, mustChange })} label="Ask them to choose their own password when they first sign in" />
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
  const set = (key: keyof Form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [key]: e.target.value });
  const roles = (data?.roles ?? []).filter((r) => r.name !== "super_admin" || me.role === "super_admin");
  const self = user?.name === me.user;
  const person: Person = { username: user?.name, email: form.email, names: [form.first_name, form.last_name] };
  const ready = /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(form.email.trim()) && (!!user || choiceReady(password, person));
  const save = async () => {
    if (self && form.role !== user!.role && !confirm("Change your own role? You are signed out and sign in again with the new role.")) return;
    setBusy(true);
    try {
      const body = { ...form, email: form.email.trim() };
      if (user) {
        await api.patch(`/users/${user.name}`, body);
        toast("success", `${user.name} saved`);
        onDone();
      } else {
        const made = await api.post<User & { password: string }>("/users", { ...body, ...choiceBody(password) });
        onDone({ title: `Password for ${made.email}`, password: made.password, mustChange: made.must_change_password });
      }
    } catch (error) {
      toast("error", "Not saved", errorText(error));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal wide title={user ? `Edit ${user.display_name}` : "New account"} description={user ? <span className="mono">{user.name}</span> : "They sign in with their e-mail address"} onClose={onClose}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={busy} disabled={!ready} onClick={save}>{user ? "Save" : "Create"}</Button></>}>
      <div className="stack">
        <div className="form-grid">
          <Field label="First name"><input className="input" autoFocus value={form.first_name} onChange={set("first_name")} /></Field>
          <Field label="Last name"><input className="input" value={form.last_name} onChange={set("last_name")} /></Field>
          <Field label="E-mail" hint={user ? "They sign in with it" : "They sign in with it; the user name is made from it"}>
            <input className="input" type="email" autoComplete="off" value={form.email} onChange={set("email")} />
          </Field>
          <Field label="Phone" hint="Optional"><input className="input" type="tel" value={form.phone} onChange={set("phone")} /></Field>
        </div>
        <div className="field">
          <span className="label">Role</span>
          <div className="grid-2">
            {roles.map((role) => (
              <button key={role.name} type="button" className="choice" aria-pressed={form.role === role.name} onClick={() => setForm({ ...form, role: role.name })}>
                <span>
                  <span className="t">{role.label}</span>
                  <br />
                  <span className="d">{role.description}</span>
                </span>
              </button>
            ))}
          </div>
        </div>
        {!user && (
          <div className="field">
            <span className="label">Password</span>
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
  const person: Person = { username: user.name, email: user.email ?? undefined, names: [user.first_name ?? "", user.last_name ?? ""] };
  const submit = async () => {
    setBusy(true);
    try {
      const answer = await api.post<{ password: string }>(`/users/${user.name}/password`, choiceBody(choice));
      onDone({ title: `New password for ${user.display_name}`, password: answer.password, mustChange: choice.mode === "generate" || choice.mustChange });
    } catch (error) {
      toast("error", "Not reset", errorText(error));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal wide title={`New password for ${user.display_name}`} description="Their current sessions end" onClose={onClose}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={busy} disabled={!choiceReady(choice, person)} onClick={submit}>Set password</Button></>}>
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
  const manageable = (u: User) => u.role !== "super_admin" || me.role === "super_admin";

  return (
    <Page>
      <PageHead title="Accounts" actions={<Button variant="primary" icon={<UserPlus />} onClick={() => setEditing("new")}>New account</Button>}>
        Each account has a role that decides what it can do. Super admins can do everything, viewers can only look.
      </PageHead>
      <div className="card">
        {isPending ? (
          <div className="card-body"><Skeleton height={160} /></div>
        ) : !users?.length ? (
          <Empty icon={<Users />} title="No accounts" />
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Account</th><th>Role</th><th>Second factor</th><th>Last sign-in</th><th /></tr></thead>
              <tbody>
                {users.map((u) => (
                  <tr key={u.name}>
                    <td>
                      <div className="row" style={{ gap: 12 }}>
                        <span className="avatar">{(u.first_name?.[0] ?? u.name[0]) + (u.last_name?.[0] ?? "")}</span>
                        <div style={{ minWidth: 0 }}>
                          <div className="truncate">{u.display_name}{u.name === me.user && <span className="faint"> (you)</span>}</div>
                          <div className="xsmall faint truncate">{u.email ?? <span className="mono">{u.name}</span>}</div>
                        </div>
                      </div>
                    </td>
                    <td>
                      <Tag tone={u.role === "super_admin" ? "accent" : undefined}>{u.role_label}</Tag>
                      {u.must_change_password && <span className="xsmall faint"> · new password pending</span>}
                    </td>
                    <td className="small">{u.second_factors.length ? u.second_factors.map((f) => (f === "totp" ? "App" : "E-mail")).join(", ") : <span className="faint">None</span>}</td>
                    <td className="small muted">{ago(u.last_sign_in_at)}</td>
                    <td className="actions">
                      {manageable(u) && (
                        <Menu trigger={(toggle) => <Button size="sm" variant="ghost" icon={<MoreHorizontal />} onClick={toggle} aria-label={`Actions for ${u.name}`} />}>
                          {(close) => (
                            <>
                              <button onClick={() => { close(); setEditing(u); }}><Pencil /> Edit</button>
                              {u.name !== me.user && (
                                <>
                                  <button onClick={() => { close(); setResetting(u); }}><KeyRound /> Set a new password</button>
                                  <button disabled={!u.second_factors.length} onClick={() => { close(); resetFactors.mutate(u.name, { onSuccess: () => toast("success", `${u.name}'s second factors removed`), onError: fail("Not removed") }); }}>
                                    <ShieldOff /> Remove second factors
                                  </button>
                                  <hr />
                                  <button className="danger" onClick={() => { close(); if (confirm(`Delete the account of ${u.display_name}?`)) remove.mutate(u.name, { onSuccess: () => toast("success", `${u.name} deleted`), onError: fail("Not deleted") }); }}>
                                    <Trash2 /> Delete
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
