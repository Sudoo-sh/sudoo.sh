import { KeyRound, MoreHorizontal, Pencil, ShieldOff, Trash2, UserPlus, Users } from "lucide-react";
import { useState } from "react";
import { api, errorText } from "@/api/client";
import { useCall, useRoles, useUsers } from "@/api/queries";
import type { Role, User } from "@/api/types";
import { Page, PageHead, useMe } from "@/components/Shell";
import { Button, Callout, CopyField, Empty, Field, Skeleton, Tag } from "@/components/ui/basics";
import { Menu, Modal, useToast } from "@/components/ui/overlay";
import { ago } from "@/lib/format";

function Secret({ title, value, onClose }: { title: string; value: string; onClose: () => void }) {
  return (
    <Modal title={title} description="They choose their own password the first time they sign in with it" onClose={onClose}
      footer={<Button variant="primary" onClick={onClose}>Done</Button>}>
      <div className="stack">
        <CopyField value={value} block />
        <Callout tone="warning">Shown once. Give it to the person through a safe channel.</Callout>
      </div>
    </Modal>
  );
}

type Form = { name: string; role: Role; first_name: string; last_name: string; email: string; phone: string };
const EMPTY: Form = { name: "", role: "developer", first_name: "", last_name: "", email: "", phone: "" };

function AccountForm({ user, onClose, onDone }: { user?: User; onClose: () => void; onDone: (made?: { name: string; password: string }) => void }) {
  const me = useMe();
  const toast = useToast();
  const { data } = useRoles();
  const [form, setForm] = useState<Form>(
    user ? { name: user.name, role: user.role, first_name: user.first_name ?? "", last_name: user.last_name ?? "", email: user.email ?? "", phone: user.phone ?? "" } : EMPTY,
  );
  const [busy, setBusy] = useState(false);
  const set = (key: keyof Form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [key]: e.target.value });
  const roles = (data?.roles ?? []).filter((r) => r.name !== "super_admin" || me.role === "super_admin");
  const save = async () => {
    setBusy(true);
    try {
      if (user) {
        const { name: _, ...changes } = form;
        await api.patch(`/users/${user.name}`, changes);
        toast("success", `${user.name} saved`);
        onDone();
      } else {
        const made = await api.post<User & { password: string }>("/users", { ...form, name: form.name.trim().toLowerCase() });
        onDone({ name: made.name, password: made.password });
      }
    } catch (error) {
      toast("error", "Not saved", errorText(error));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal wide title={user ? `Edit ${user.name}` : "New account"} description={user ? undefined : "A password is generated and shown once"} onClose={onClose}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={busy} disabled={form.name.trim().length < 3} onClick={save}>{user ? "Save" : "Create"}</Button></>}>
      <div className="stack">
        <div className="form-grid">
          <Field label="First name"><input className="input" autoFocus value={form.first_name} onChange={set("first_name")} /></Field>
          <Field label="Last name"><input className="input" value={form.last_name} onChange={set("last_name")} /></Field>
          <Field label="E-mail"><input className="input" type="email" value={form.email} onChange={set("email")} /></Field>
          <Field label="Phone"><input className="input" type="tel" value={form.phone} onChange={set("phone")} /></Field>
          {!user && (
            <Field label="User name" className="full" hint="3 to 32 lower-case letters, digits, dots and dashes, starting with a letter">
              <input className="input" value={form.name} onChange={set("name")} />
            </Field>
          )}
        </div>
        <div className="field">
          <span className="label">Role</span>
          <div className="grid-2">
            {roles.map((role) => (
              <button key={role.name} type="button" className="choice" aria-pressed={form.role === role.name}
                disabled={user?.name === me.user} onClick={() => setForm({ ...form, role: role.name })}>
                <span>
                  <span className="t">{role.label}</span>
                  <br />
                  <span className="d">{role.description}</span>
                </span>
              </button>
            ))}
          </div>
        </div>
      </div>
    </Modal>
  );
}

export function AccountsPage() {
  const me = useMe();
  const { data: users, isPending, refetch } = useUsers();
  const toast = useToast();
  const [editing, setEditing] = useState<User | "new" | null>(null);
  const [secret, setSecret] = useState<{ title: string; value: string } | null>(null);
  const refresh = [["users"]] as const;
  const reset = useCall((user: string) => api.post<{ password: string }>(`/users/${user}/password`), refresh);
  const resetFactors = useCall((user: string) => api.del(`/users/${user}/second-factors`), refresh);
  const remove = useCall((user: string) => api.del(`/users/${user}`), refresh);
  const fail = (title: string) => (error: unknown) => toast("error", title, errorText(error));
  const manageable = (u: User) => u.name !== me.user && (u.role !== "super_admin" || me.role === "super_admin");

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
                          <div className="xsmall faint mono truncate">{u.name}{u.email ? ` · ${u.email}` : ""}</div>
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
                              <button onClick={() => { close(); reset.mutate(u.name, { onSuccess: (r) => setSecret({ title: `New password for ${u.name}`, value: r.password }), onError: fail("Not reset") }); }}>
                                <KeyRound /> Reset password
                              </button>
                              <button disabled={!u.second_factors.length} onClick={() => { close(); resetFactors.mutate(u.name, { onSuccess: () => toast("success", `${u.name}'s second factors removed`), onError: fail("Not removed") }); }}>
                                <ShieldOff /> Remove second factors
                              </button>
                              <hr />
                              <button className="danger" onClick={() => { close(); if (confirm(`Delete the account ${u.name}?`)) remove.mutate(u.name, { onSuccess: () => toast("success", `${u.name} deleted`), onError: fail("Not deleted") }); }}>
                                <Trash2 /> Delete
                              </button>
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
            if (made) setSecret({ title: `Password for ${made.name}`, value: made.password });
          }}
        />
      )}
      {secret && <Secret title={secret.title} value={secret.value} onClose={() => setSecret(null)} />}
    </Page>
  );
}
