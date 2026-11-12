import { KeyRound, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { api, errorText } from "@/api/client";
import { useCall, useTokens } from "@/api/queries";
import { Page, PageHead } from "@/components/Shell";
import { Badge, Button, Callout, CopyField, Empty, Field, Segmented, Skeleton } from "@/components/ui/basics";
import { Modal, useToast } from "@/components/ui/overlay";
import { ago } from "@/lib/format";

export function TokensPage() {
  const { data: tokens, isPending } = useTokens();
  const toast = useToast();
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [scope, setScope] = useState<"read" | "write">("read");
  const [made, setMade] = useState<{ name: string; token: string; signing_secret: string } | null>(null);
  const create = useCall(() => api.post<{ name: string; token: string; signing_secret: string }>("/tokens", { name: name.trim(), scope }), [["tokens"]]);
  const revoke = useCall((id: string) => api.del(`/tokens/${id}`), [["tokens"]]);
  return (
    <Page>
      <PageHead eyebrow="Administration" title="API tokens" actions={<Button variant="primary" icon={<Plus />} onClick={() => setCreating(true)}>New token</Button>}>
        For other systems (CI pipelines, bots, scripts) that call the API. Every request is signed; see <a href="/docs/docs/PANEL_API.md" target="_blank" rel="noreferrer">Panel API</a>.
      </PageHead>
      <div className="card rise">
        {isPending ? <div className="card-body"><Skeleton height={120} /></div> : !tokens?.length ? (
          <Empty icon={<KeyRound />} title="No API tokens" action={<Button icon={<Plus />} onClick={() => setCreating(true)}>Create one</Button>}>A token lets another system list, create and manage instances.</Empty>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Name</th><th>Scope</th><th>Created</th><th>Last used</th><th /></tr></thead>
              <tbody>
                {tokens.map((t) => (
                  <tr key={t.id}>
                    <td><div style={{ fontWeight: 600 }}>{t.name}</div><div className="xsmall faint mono">sudoo_{t.id}_…</div></td>
                    <td><Badge tone={t.scope === "write" ? "warning" : "accent"}>{t.scope === "write" ? "Read and write" : "Read only"}</Badge></td>
                    <td className="small muted">{ago(t.created)} · {t.created_by}</td>
                    <td className="small muted">{ago(t.last_used)}</td>
                    <td className="actions">
                      <Button size="sm" variant="ghost" icon={<Trash2 />} onClick={() => confirm(`Revoke ${t.name}? Calls with it stop working at once.`) && revoke.mutate(t.id, { onSuccess: () => toast("success", "Token revoked") })}>Revoke</Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {creating && (
        <Modal title="New API token" onClose={() => setCreating(false)}
          footer={<><Button onClick={() => setCreating(false)}>Cancel</Button><Button variant="primary" loading={create.isPending} disabled={name.trim().length < 3}
            onClick={() => create.mutate(undefined, { onSuccess: (m) => { setCreating(false); setName(""); setMade(m); }, onError: (e) => toast("error", "Not created", errorText(e)) })}>Create</Button></>}>
          <div className="stack">
            <Field label="Name" hint="What will use it, e.g. CI pipeline."><input className="input" autoFocus value={name} onChange={(e) => setName(e.target.value)} /></Field>
            <Field label="Scope">
              <Segmented value={scope} onChange={setScope} options={[{ value: "read", label: "Read only" }, { value: "write", label: "Read and write" }]} />
            </Field>
          </div>
        </Modal>
      )}
      {made && (
        <Modal title={`${made.name} is ready`} description="Store both values in the other system's secrets." onClose={() => setMade(null)} footer={<Button variant="primary" onClick={() => setMade(null)}>Done</Button>}>
          <div className="stack">
            <Field label="Token"><CopyField value={made.token} block /></Field>
            <Field label="Signing secret"><CopyField value={made.signing_secret} block /></Field>
            <Callout tone="warning">Shown once: neither can be shown again.</Callout>
          </div>
        </Modal>
      )}
    </Page>
  );
}
