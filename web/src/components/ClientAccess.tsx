// The staff side of the client portal (src/sudoo/projects/clients.py): the "Closed by client" tag, the notice on a
// closed project, branch or instance with Request access (and Emergency access for super admins), and a project's
// Clients tab. The server decides everything again; a client's switch is never lifted from here.

import { useQueryClient } from "@tanstack/react-query";
import { KeyRound, ShieldAlert, ShieldCheck, ShieldOff, Trash2, UserPlus } from "lucide-react";
import { useState } from "react";
import { errorText } from "@/api/client";
import { clientCalls, clientKeys, useProjectClients } from "@/api/clients";
import { keys } from "@/api/queries";
import type { ClientAccess, ProjectDetail } from "@/api/types";
import { Button, Callout, Field, Skeleton, Switch, Tag } from "@/components/app/basics";
import { Modal, useToast } from "@/components/app/overlay";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { useT } from "@/i18n";
import words from "@/i18n/clients";
import { dateTime } from "@/lib/format";
import { useCan, useMe } from "./Session";

/** The "Closed by client" tag of a branch or an instance. */
export function ClientTag({ access }: { access: ClientAccess | null | undefined }) {
  const t = useT(words);
  if (!access || access.state === "open") return null;
  if (access.state === "timed")
    return <span title={t.staff.timed(dateTime(access.until))}><Tag icon={<ShieldCheck />}>{t.states.timed}</Tag></span>;
  return <span title={t.staff.closedText}><Tag tone="caution" icon={<ShieldOff />}>{t.staff.tag}</Tag></span>;
}

function RequestDialog({ projectId, branch, where, onClose }: { projectId: string; branch: string | null; where: string; onClose: () => void }) {
  const t = useT(words).staff;
  const toast = useToast();
  const client = useQueryClient();
  const meta = useProjectClients(projectId).data?.meta;
  const [reason, setReason] = useState("");
  const [typedHours, setHours] = useState<string | null>(null);
  const hours = typedHours ?? String(meta?.request_hours ?? 4);
  const [busy, setBusy] = useState(false);
  const send = async () => {
    setBusy(true);
    try {
      await clientCalls.ask(projectId, { branch, reason, hours: Number(hours) });
      toast("success", t.sent);
      void client.invalidateQueries({ queryKey: clientKeys.staff(projectId) });
      onClose();
    } catch (error) {
      toast("error", t.notSent, errorText(error));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title={t.requestTitle(where)} description={t.closedText} onClose={onClose}
      footer={<Button variant="primary" loading={busy} disabled={reason.trim().length < 10 || !(Number(hours) > 0)} onClick={send}>{t.send}</Button>}>
      <div className="stack">
        <Field label={t.reason} hint={t.reasonHint} htmlFor="request-reason">
          <Textarea id="request-reason" rows={3} maxLength={300} value={reason} onChange={(e) => setReason(e.target.value)} autoFocus />
        </Field>
        <Field label={t.hours} htmlFor="request-hours">
          <Input id="request-hours" type="number" inputMode="numeric" min={1} max={meta?.request_max_hours ?? 168} dir="ltr" value={hours} onChange={(e) => setHours(e.target.value)} style={{ maxWidth: 120 }} />
        </Field>
      </div>
    </Modal>
  );
}

function EmergencyDialog({ projectId, branch, name, onClose }: { projectId: string; branch: string | null; name: string; onClose: () => void }) {
  const t = useT(words).staff;
  const minutes = useProjectClients(projectId).data?.meta.emergency_minutes ?? 60;
  const toast = useToast();
  const client = useQueryClient();
  const [reason, setReason] = useState("");
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const force = async () => {
    setBusy(true);
    try {
      await clientCalls.emergency(projectId, { branch, reason, confirm: typed });
      toast("success", t.forced);
      void client.invalidateQueries({ queryKey: keys.project(projectId) });
      void client.invalidateQueries({ queryKey: clientKeys.staff(projectId) });
      void client.invalidateQueries({ queryKey: keys.instances });
      onClose();
    } catch (error) {
      toast("error", t.notForced, errorText(error));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title={t.emergencyTitle(name)} onClose={onClose}
      footer={<Button variant="danger" loading={busy} disabled={reason.trim().length < 10 || typed.trim() !== name} onClick={force}>{t.force}</Button>}>
      <div className="stack">
        <Callout tone="warning">{t.emergencyText(minutes)}</Callout>
        <Field label={t.reason} hint={t.reasonHint} htmlFor="emergency-reason">
          <Textarea id="emergency-reason" rows={3} maxLength={300} value={reason} onChange={(e) => setReason(e.target.value)} autoFocus />
        </Field>
        <Field label={t.confirm(name)} htmlFor="emergency-confirm">
          <Input id="emergency-confirm" dir="ltr" autoComplete="off" spellCheck={false} value={typed} onChange={(e) => setTyped(e.target.value)} />
        </Field>
      </div>
    </Modal>
  );
}

/** The notice of a closed project, branch or instance: what is closed, and Request access / Emergency access. */
export function ClosedNotice({
  projectId,
  projectName,
  branch,
  access,
}: {
  projectId: string;
  projectName: string;
  branch: string | null;
  access: ClientAccess | null | undefined;
}) {
  const t = useT(words).staff;
  const me = useMe();
  const [dialog, setDialog] = useState<"request" | "emergency" | null>(null);
  if (!access || access.state !== "closed" || me.role === "client") return null;
  // a branch that follows the project's switch: asking for the project asks for every branch
  const asked = access.scope === "branch" ? branch : null;
  const where = asked ?? projectName;
  const name = asked ?? projectId;
  return (
    <>
      <Callout tone="warning">
        <b>{asked ? t.closedBranch(asked) : t.closedProject(projectName)}</b>{" "}
        {access.lets_you_through ? t.letThrough : t.closedText}
        {!access.lets_you_through && (
          <span className="row-wrap" style={{ marginTop: 8 }}>
            <Button size="sm" icon={<KeyRound />} onClick={() => setDialog("request")}>{t.request}</Button>
            {me.role === "super_admin" && access.emergency_allowed && (
              <Button size="sm" variant="ghost" icon={<ShieldAlert />} onClick={() => setDialog("emergency")}>{t.emergencyButton}</Button>
            )}
            {me.role === "super_admin" && !access.emergency_allowed && <span className="xsmall faint">{t.emergencyOff}</span>}
          </span>
        )}
      </Callout>
      {dialog === "request" && <RequestDialog projectId={projectId} branch={asked} where={where} onClose={() => setDialog(null)} />}
      {dialog === "emergency" && <EmergencyDialog projectId={projectId} branch={asked} name={name} onClose={() => setDialog(null)} />}
    </>
  );
}

function InviteDialog({ projectId, onClose }: { projectId: string; onClose: () => void }) {
  const t = useT(words).staff;
  const toast = useToast();
  const client = useQueryClient();
  const [email, setEmail] = useState("");
  const [firstName, setFirstName] = useState("");
  const [backups, setBackups] = useState(true);
  const [shell, setShell] = useState(false);
  const [busy, setBusy] = useState(false);
  const add = async () => {
    setBusy(true);
    try {
      const answer = await clientCalls.addClient(projectId, { email, first_name: firstName, backups, shell });
      toast("success", answer.invited ? t.invited : t.added);
      void client.invalidateQueries({ queryKey: clientKeys.staff(projectId) });
      void client.invalidateQueries({ queryKey: keys.project(projectId) });
      onClose();
    } catch (error) {
      toast("error", t.notAdded, errorText(error));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title={t.inviteTitle} description={t.inviteText} onClose={onClose}
      footer={<Button variant="primary" loading={busy} disabled={!email.includes("@")} onClick={add}>{t.add}</Button>}>
      <div className="stack">
        <Field label={t.email} htmlFor="client-email">
          <Input id="client-email" type="email" dir="ltr" autoComplete="off" value={email} onChange={(e) => setEmail(e.target.value)} autoFocus />
        </Field>
        <Field label={t.firstName} htmlFor="client-first">
          <Input id="client-first" value={firstName} onChange={(e) => setFirstName(e.target.value)} />
        </Field>
        <Switch checked={backups} onChange={setBackups} label={t.backups} />
        <Switch checked={shell} onChange={setShell} label={t.shell} />
        <p className="xsmall faint">{t.planNote}</p>
      </div>
    </Modal>
  );
}

/** A project's Clients tab: its clients, the switches, requests and access in use (GET /projects/<id>/clients). */
export function ClientsTab({ project }: { project: ProjectDetail }) {
  const all = useT(words);
  const t = all.staff;
  const can = useCan();
  const toast = useToast();
  const client = useQueryClient();
  const { data, isPending, error } = useProjectClients(project.id);
  const [inviting, setInviting] = useState(false);
  const [removing, setRemoving] = useState<string | null>(null);
  if (error) return <div className="stack tab-panel"><Callout tone="warning">{errorText(error)}</Callout></div>;
  if (isPending || !data) return <div className="stack tab-panel"><Skeleton height={220} /></div>;
  const { clients: found, meta } = data;
  const remove = async (name: string) => {
    try {
      await clientCalls.removeClient(project.id, name);
      toast("success", t.removed);
      void client.invalidateQueries({ queryKey: clientKeys.staff(project.id) });
    } catch (err) {
      toast("error", t.notRemoved, errorText(err));
    } finally {
      setRemoving(null);
    }
  };
  const closedBranches = Object.entries(found.branches).filter(([, v]) => v && v.state !== "open");
  return (
    <div className="stack tab-panel">
      <ClosedNotice projectId={project.id} projectName={project.name} branch={null} access={found.project} />
      {closedBranches.filter(([, v]) => v?.scope === "branch" && v.state === "closed").map(([branch, v]) => (
        <ClosedNotice key={branch} projectId={project.id} projectName={project.name} branch={branch} access={v} />
      ))}
      <section className="card">
        <div className="card-head">
          <h2 className="spacer">{t.clientsTitle}</h2>
          {meta.may_manage && can("clients.manage") && (
            <Button size="sm" icon={<UserPlus />} onClick={() => setInviting(true)}>{t.invite}</Button>
          )}
        </div>
        <div className="card-body stack-sm">
          <p className="small muted">{t.clientsText}</p>
          {found.clients.length === 0 && found.invited.length === 0 ? (
            <p className="small faint">{t.noClients}</p>
          ) : (
            <ul className="client-list">
              {found.clients.map((c) => (
                <li key={c.name} className="row-wrap small">
                  <b><bdi>{c.display_name}</bdi></b>
                  {c.email && <bdi dir="ltr" className="faint">{c.email}</bdi>}
                  {c.backups && <Tag>{t.backups}</Tag>}
                  {c.shell && <Tag>{t.shell}</Tag>}
                  <span className="spacer" />
                  {can("clients.manage") && (
                    <Button size="sm" variant="ghost" icon={<Trash2 />} aria-label={t.remove} title={t.remove} onClick={() => setRemoving(c.name)} />
                  )}
                </li>
              ))}
              {found.invited.map((i) => (
                <li key={i.id} className="row-wrap small">
                  <bdi dir="ltr">{i.email ?? "…"}</bdi>
                  <Tag>{t.pending}</Tag>
                </li>
              ))}
            </ul>
          )}
          {meta.portal_url && <p className="xsmall faint">{t.portal(meta.portal_url)}</p>}
        </div>
      </section>
      <section className="card">
        <div className="card-head"><h2>{all.requests.title}</h2></div>
        <div className="card-body stack-sm">
          {found.requests.length === 0 ? <p className="small faint">{all.requests.none}</p> : found.requests.map((r) => (
            <div key={r.id} className="row-wrap small">
              <Tag tone={r.status === "approved" ? "positive" : r.status === "refused" ? "negative" : undefined}>{all.requests.status[r.status] ?? r.status}</Tag>
              <span>{all.requests.asks(r.by.display_name, r.branch ?? all.requests.whole, r.hours)}</span>
              <span className="faint">{dateTime(r.asked_at)}</span>
              {r.until && <span className="faint">{all.requests.until(dateTime(r.until))}</span>}
            </div>
          ))}
        </div>
      </section>
      {found.passes.length > 0 && (
        <section className="card">
          <div className="card-head"><h2>{all.passes.title}</h2></div>
          <div className="card-body stack-sm">
            {found.passes.map((p) => (
              <div key={p.id} className="row-wrap small">
                <Tag tone={p.kind === "emergency" ? "negative" : undefined}>{p.kind === "emergency" ? all.passes.emergency : all.passes.request}</Tag>
                <span>{p.people.map((x) => x.display_name).join(", ")}</span>
                <span className="faint">{p.branch ?? all.requests.whole} · {all.passes.ends(dateTime(p.until))}</span>
              </div>
            ))}
          </div>
        </section>
      )}
      {inviting && <InviteDialog projectId={project.id} onClose={() => setInviting(false)} />}
      {removing && (
        <Modal title={t.removeTitle(found.clients.find((c) => c.name === removing)?.display_name ?? removing)} description={t.removeText}
          onClose={() => setRemoving(null)} footer={<Button variant="danger" onClick={() => remove(removing)}>{t.remove}</Button>} />
      )}
    </div>
  );
}
