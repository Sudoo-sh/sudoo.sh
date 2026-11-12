import { KeyRound, MoreHorizontal, ShieldCheck, ShieldOff, Trash2, UserPlus, Users } from "lucide-react";
import { useState } from "react";
import { api, errorText } from "@/api/client";
import { useCall, useUsers } from "@/api/queries";
import type { User } from "@/api/types";
import { Page, PageHead, useMe } from "@/components/Shell";
import { Badge, Button, Callout, CopyField, Empty, Field, Segmented, Skeleton } from "@/components/ui/basics";
import { Menu, Modal, useToast } from "@/components/ui/overlay";
import { ago } from "@/lib/format";

function Secret({ title, text, value, onClose }: { title: string; text: string; value: string; onClose: () => void }) {
  return (
    <Modal title={title} description={text} onClose={onClose} footer={<Button variant="primary" onClick={onClose}>Done</Button>}>
      <div className="stack">
        <CopyField value={value} block />
        <Callout tone="warning">Shown once. Give it to the person through a safe channel; they can change it under My account.</Callout>
      </div>
    </Modal>
  );
}

export function AccountsPage() {
  const me = useMe();
  const { data: users, isPending } = useUsers();
  const toast = useToast();
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [role, setRole] = useState<"admin" | "operator">("operator");
  const [secret, setSecret] = useState<{ title: string; value: string } | null>(null);
  const refresh = [["users"]] as const;
  const create = useCall(() => api.post<User & { password: string }>("/users", { name: name.trim().toLowerCase(), role }), refresh);
  const reset = useCall((user: string) => api.post<{ password: string }>(`/users/${user}/password`), refresh);
  const resetFactors = useCall((user: string) => api.del(`/users/${user}/second-factors`), refresh);
  const remove = useCall((user: string) => api.del(`/users/${user}`), refresh);
  const fail = (title: string) => (error: unknown) => toast("error", title, errorText(error));

  return (
    <Page>
      <PageHead eyebrow="Administration" title="Accounts" actions={<Button variant="primary" icon={<UserPlus />} onClick={() => setCreating(true)}>New account</Button>}>
        Administrators can do everything. Sync operators can update code, add submodules and restart instances.
      </PageHead>
      <div className="card rise">
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
                      <div className="row">
                        <span className="avatar">{u.name.slice(0, 2)}</span>
                        <div>
                          <div style={{ fontWeight: 600 }}>{u.name}{u.name === me.user && <span className="faint small"> (you)</span>}</div>
                          <div className="xsmall faint">{u.email ?? "no e-mail"}</div>
                        </div>
                      </div>
                    </td>
                    <td><Badge tone={u.role === "admin" ? "primary" : undefined}>{u.role_label}</Badge></td>
                    <td>{u.second_factors.length ? <Badge tone="success" icon={<ShieldCheck />}>{u.second_factors.join(", ")}</Badge> : <span className="faint small">none</span>}</td>
                    <td className="small muted">{ago(u.last_sign_in_at)}</td>
                    <td className="actions">
                      {u.name !== me.user && (
                        <Menu trigger={(toggle) => <Button size="sm" variant="ghost" icon={<MoreHorizontal />} onClick={toggle} aria-label="Actions" />}>
                          {(close) => (
                            <>
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

      {creating && (
        <Modal title="New account" description="A strong password is generated and shown once." onClose={() => setCreating(false)}
          footer={<><Button onClick={() => setCreating(false)}>Cancel</Button><Button variant="primary" loading={create.isPending} disabled={name.trim().length < 3}
            onClick={() => create.mutate(undefined, { onSuccess: (made) => { setCreating(false); setName(""); setSecret({ title: `Password for ${made.name}`, value: made.password }); }, onError: fail("Not created") })}>Create</Button></>}>
          <div className="stack">
            <Field label="User name" hint="3 to 32 lower-case letters, digits, dots, dashes; starting with a letter.">
              <input className="input" autoFocus value={name} onChange={(e) => setName(e.target.value)} />
            </Field>
            <Field label="Role">
              <Segmented value={role} onChange={setRole} options={[{ value: "operator", label: "Sync operator" }, { value: "admin", label: "Administrator" }]} />
            </Field>
          </div>
        </Modal>
      )}
      {secret && <Secret title={secret.title} text="The account can sign in with it now." value={secret.value} onClose={() => setSecret(null)} />}
    </Page>
  );
}
