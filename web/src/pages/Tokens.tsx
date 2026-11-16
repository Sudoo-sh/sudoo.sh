import { ArrowUpRight, KeyRound, Plus, Store, Trash2 } from "lucide-react";
import { useState } from "react";
import { api, errorText } from "@/api/client";
import { useCall, useTokens } from "@/api/queries";
import { Page, PageHead } from "@/components/Shell";
import { Tag, Button, Callout, CopyField, Empty, Field, Segmented, Skeleton } from "@/components/ui/basics";
import { Modal, useToast } from "@/components/ui/overlay";
import { ago } from "@/lib/format";
import type { Token } from "@/api/types";

type Scope = Token["scope"];

/** Each scope, as the page names it and explains it in one line. */
const SCOPES: { value: Scope; label: string; hint: string }[] = [
  { value: "read", label: "Read only", hint: "Reads instances, jobs and versions; changes nothing." },
  { value: "instances", label: "Instances", hint: "For connected apps such as the Odoo module: list, create, start, pause, restart, back up, extend and delete instances; nothing else." },
  { value: "write", label: "Read and write", hint: "Acts as an administrator: everything except the panel's settings." },
];

/** The Sudoo.sh apps for Odoo on the Odoo Apps store: free, for Odoo 17.0 to 20.0; each page switches versions. */
const ODOO_STORE = "https://apps.odoo.com/apps/modules";
const ODOO_APPS: { name: string; module: string; adds: string }[] = [
  { name: "Instance Manager", module: "sudoo_base", adds: "Create and manage instances from Odoo, within the rules an administrator sets." },
  { name: "CRM Demos", module: "sudoo_crm", adds: "A Create demo button on opportunities." },
  { name: "Project Tests", module: "sudoo_project", adds: "A Test instance button on tasks." },
  { name: "Helpdesk Tests", module: "sudoo_helpdesk", adds: "A Test instance button on tickets (Odoo Enterprise)." },
];

export function TokensPage() {
  const { data: tokens, isPending } = useTokens();
  const toast = useToast();
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [scope, setScope] = useState<Scope>("read");
  const [made, setMade] = useState<{ name: string; token: string; signing_secret: string } | null>(null);
  const create = useCall(() => api.post<{ name: string; token: string; signing_secret: string }>("/tokens", { name: name.trim(), scope }), [["tokens"]]);
  const revoke = useCall((id: string) => api.del(`/tokens/${id}`), [["tokens"]]);
  return (
    <Page>
      <PageHead title="API tokens" actions={<Button variant="primary" icon={<Plus />} onClick={() => setCreating(true)}>New token</Button>}>
        For other systems (CI pipelines, bots, scripts, the Odoo module) that call the API. Every request is signed; see <a href="/docs/docs/PANEL_API.md" target="_blank" rel="noreferrer">Panel API</a>.
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
                    <td><div>{t.name}</div><div className="xsmall faint mono">sudoo_{t.id}_…</div></td>
                    <td><Tag>{SCOPES.find((s) => s.value === t.scope)?.label ?? t.scope}</Tag></td>
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
      <section className="card rise" aria-labelledby="odoo-apps-title">
        <div className="card-head">
          <Store />
          <h2 id="odoo-apps-title" className="spacer">Use this panel from your company's Odoo</h2>
          <Button size="sm" icon={<Plus />} onClick={() => { setName("Odoo"); setScope("instances"); setCreating(true); }}>Token for Odoo</Button>
        </div>
        <div className="card-body stack">
          <p className="small muted">
            Free apps on the Odoo Apps store, for Odoo 17.0 to 20.0, in English and Arabic. People create and manage instances from Odoo;
            connect them with a token of the <b>Instances</b> scope, which can only manage instances.
          </p>
          <div className="table-wrap">
            <table className="table">
              <tbody>
                {ODOO_APPS.map((app) => (
                  <tr key={app.module}>
                    <td>Sudoo.sh {app.name}</td>
                    <td className="small muted">{app.adds}</td>
                    <td className="actions">
                      <a className="btn sm ghost" href={`${ODOO_STORE}/20.0/${app.module}`} target="_blank" rel="noreferrer" aria-label={`Sudoo.sh ${app.name} on the Odoo Apps store`}>Odoo Apps store <ArrowUpRight /></a>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </section>
      {creating && (
        <Modal title="New API token" onClose={() => setCreating(false)}
          footer={<><Button onClick={() => setCreating(false)}>Cancel</Button><Button variant="primary" loading={create.isPending} disabled={name.trim().length < 3}
            onClick={() => create.mutate(undefined, { onSuccess: (m) => { setCreating(false); setName(""); setMade(m); }, onError: (e) => toast("error", "Not created", errorText(e)) })}>Create</Button></>}>
          <div className="stack">
            <Field label="Name" hint="What will use it, e.g. CI pipeline."><input className="input" autoFocus value={name} onChange={(e) => setName(e.target.value)} /></Field>
            <Field label="Scope" hint={SCOPES.find((s) => s.value === scope)?.hint}>
              <Segmented value={scope} onChange={setScope} options={SCOPES.map(({ value, label }) => ({ value, label }))} />
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
