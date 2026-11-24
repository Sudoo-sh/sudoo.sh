import { ArrowUpRight, KeyRound, Plus, Store, Trash2 } from "lucide-react";
import { useState } from "react";
import { api, errorText } from "@/api/client";
import { useCall, useTokens } from "@/api/queries";
import { Page, PageHead } from "@/components/Shell";
import { Tag, Button, Callout, CopyField, Empty, Field, Segmented, Skeleton } from "@/components/app/basics";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Input } from "@/components/ui/input";
import { Modal, useToast } from "@/components/app/overlay";
import { ago } from "@/lib/format";
import type { Token } from "@/api/types";
import { useT } from "@/i18n";
import words from "@/i18n/admin";
import common from "@/i18n/common";

type Scope = Token["scope"];

/** The scopes in the order the page offers them (their names and explanations are in i18n/admin.ts). */
const SCOPES: Scope[] = ["read", "instances", "developer", "write"];

/** The Sudoo.sh apps for Odoo on the Odoo Apps store: free, for Odoo 17.0 to 20.0; each page switches versions. */
const ODOO_STORE = "https://apps.odoo.com/apps/modules";
const ODOO_APPS: { name: string; module: string }[] = [
  { name: "Instance Manager", module: "sudoo_base" },
  { name: "CRM Demos", module: "sudoo_crm" },
  { name: "Project Tests", module: "sudoo_project" },
  { name: "Helpdesk Tests", module: "sudoo_helpdesk" },
];

export function TokensPage() {
  const { data: tokens, isPending } = useTokens();
  const toast = useToast();
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [scope, setScope] = useState<Scope>("read");
  const [made, setMade] = useState<{ name: string; token: string; signing_secret: string } | null>(null);
  const create = useCall(
    () => api.post<{ name: string; token: string; signing_secret: string }>("/tokens", { name: name.trim(), scope }),
    [["tokens"]],
  );
  const revoke = useCall((id: string) => api.del(`/tokens/${id}`), [["tokens"]]);
  const t = useT(words).tokens;
  const c = useT(common).common;
  const scopeLabel = (scope: string) => (t.scopes as Record<string, { label: string }>)[scope]?.label ?? scope;
  return (
    <Page>
      <PageHead
        title={t.title}
        actions={
          <Button variant="primary" icon={<Plus />} onClick={() => setCreating(true)}>
            {t.new}
          </Button>
        }
      >
        {t.lead(
          <a key="api" href="/docs/docs/PANEL_API.md" target="_blank" rel="noreferrer">
            {t.apiLink}
          </a>,
        )}
      </PageHead>
      <div className="card rise">
        {isPending ? (
          <div className="card-body">
            <Skeleton height={120} />
          </div>
        ) : !tokens?.length ? (
          <Empty
            icon={<KeyRound />}
            title={t.none}
            action={
              <Button icon={<Plus />} onClick={() => setCreating(true)}>
                {t.createOne}
              </Button>
            }
          >
            {t.noneText}
          </Empty>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t.columns.name}</TableHead>
                <TableHead>{t.columns.scope}</TableHead>
                <TableHead>{t.columns.created}</TableHead>
                <TableHead>{t.columns.lastUsed}</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {tokens.map((token) => (
                <TableRow key={token.id}>
                  <TableCell>
                    <div>
                      <bdi>{token.name}</bdi>
                    </div>
                    <div className="xsmall faint">
                      <span className="mono">sudoo_{token.id}_…</span>
                    </div>
                  </TableCell>
                  <TableCell>
                    <Tag>{scopeLabel(token.scope)}</Tag>
                  </TableCell>
                  <TableCell className="small muted">
                    {ago(token.created)} · <bdi>{token.created_by}</bdi>
                  </TableCell>
                  <TableCell className="small muted">{ago(token.last_used)}</TableCell>
                  <TableCell className="actions">
                    <Button
                      size="sm"
                      variant="ghost"
                      icon={<Trash2 />}
                      onClick={() =>
                        confirm(t.confirmRevoke(token.name)) &&
                        revoke.mutate(token.id, { onSuccess: () => toast("success", t.revoked) })
                      }
                    >
                      {t.revoke}
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </div>
      <section className="card rise" aria-labelledby="odoo-apps-title">
        <div className="card-head">
          <Store />
          <h2 id="odoo-apps-title" className="spacer">
            {t.odooTitle}
          </h2>
          <Button
            size="sm"
            icon={<Plus />}
            onClick={() => {
              setName("Odoo");
              setScope("instances");
              setCreating(true);
            }}
          >
            {t.odooToken}
          </Button>
        </div>
        <div className="card-body stack">
          <p className="small muted">{t.odooText(<b key="scope">{t.scopes.instances.label}</b>)}</p>
          <Table>
            <TableBody>
              {ODOO_APPS.map((app) => (
                <TableRow key={app.module}>
                  <TableCell dir="ltr" style={{ textAlign: "start" }}>
                    Sudoo.sh {app.name}
                  </TableCell>
                  <TableCell className="small muted">{t.apps[app.module]}</TableCell>
                  <TableCell className="actions">
                    <Button asChild variant="ghost" size="sm">
                      <a
                        href={`${ODOO_STORE}/20.0/${app.module}`}
                        target="_blank"
                        rel="noreferrer"
                        aria-label={t.storeLabel(`Sudoo.sh ${app.name}`)}
                      >
                        {t.store} <ArrowUpRight className="flip-rtl" />
                      </a>
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </section>
      {creating && (
        <Modal
          title={t.newTitle}
          onClose={() => setCreating(false)}
          footer={
            <>
              <Button onClick={() => setCreating(false)}>{c.cancel}</Button>
              <Button
                variant="primary"
                loading={create.isPending}
                disabled={name.trim().length < 3}
                onClick={() =>
                  create.mutate(undefined, {
                    onSuccess: (m) => {
                      setCreating(false);
                      setName("");
                      setMade(m);
                    },
                    onError: (e) => toast("error", t.notCreated, errorText(e)),
                  })
                }
              >
                {c.create}
              </Button>
            </>
          }
        >
          <div className="stack">
            <Field label={t.name} hint={t.nameHint}>
              <Input dir="auto" autoFocus value={name} onChange={(e) => setName(e.target.value)} />
            </Field>
            <Field label={t.scope} hint={t.scopes[scope].hint}>
              <Segmented
                value={scope}
                onChange={setScope}
                options={SCOPES.map((value) => ({ value, label: t.scopes[value].label }))}
              />
            </Field>
          </div>
        </Modal>
      )}
      {made && (
        <Modal
          title={t.ready(made.name)}
          description={t.readyText}
          onClose={() => setMade(null)}
          footer={
            <Button variant="primary" onClick={() => setMade(null)}>
              {c.done}
            </Button>
          }
        >
          <div className="stack">
            <Field label={t.token}>
              <CopyField value={made.token} block />
            </Field>
            <Field label={t.signingSecret}>
              <CopyField value={made.signing_secret} block />
            </Field>
            <Callout tone="warning">{t.once}</Callout>
          </div>
        </Modal>
      )}
    </Page>
  );
}
