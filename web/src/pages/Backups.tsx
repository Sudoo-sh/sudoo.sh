// The Backups page: per instance, the backups on this server and the off-site copies, each with where it is and,
// for an off-site copy, its status (uploading, stored, verifying, verified, failed and why). Off-site copies can be
// checked again, copied again, restored (downloaded and checked first: nothing changes when they do not match) and
// deleted, each with its own permission (src/sudoo/web/v1/offsite.py).

import { Archive, CloudOff, CloudUpload, Download, FileKey, MoreHorizontal, RefreshCcw, RotateCcw, ScrollText, ShieldCheck, Trash2 } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router";
import { api, errorText } from "@/api/client";
import { useBackups, useCall, useInstances, useOffsite, useOffsiteCopies } from "@/api/queries";
import type { Backup, Job, OffsiteCopy } from "@/api/types";
import { JobDrawer } from "@/components/JobDrawer";
import { Page, PageHead, useMe } from "@/components/Shell";
import { Button, Callout, Empty, Segmented, Skeleton, Spinner, Tag } from "@/components/ui/basics";
import { Menu, Modal, useToast } from "@/components/ui/overlay";
import { useT } from "@/i18n";
import common from "@/i18n/common";
import words from "@/i18n/operations";
import { ago, bytes, count, dateTime } from "@/lib/format";

type View = "all" | "local" | "offsite";
type Row = { where: "local"; at: number; backup: Backup } | { where: "offsite"; at: number; copy: OffsiteCopy };

const STATUS_TONE: Record<OffsiteCopy["status"], "positive" | "negative" | "accent" | undefined> = {
  verified: "positive",
  failed: "negative",
  uploading: "accent",
  verifying: "accent",
  stored: undefined,
};

/** The public key given for a check, typed or loaded from a .pem file (optional). */
function KeyField({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const t = useT(words).backups;
  const load = async (file: File | undefined) => file && onChange((await file.text()).slice(0, 2000));
  return (
    <div className="stack-sm">
      <div className="small backup-key-label">{t.keyTitle}</div>
      <div className="xsmall muted">{t.keyText}</div>
      <textarea className="input mono backup-key" dir="ltr" rows={4} spellCheck={false} placeholder="-----BEGIN PUBLIC KEY-----" value={value} onChange={(e) => onChange(e.target.value)} />
      <label className="btn sm ghost" style={{ alignSelf: "flex-start" }}>
        <FileKey /> {t.keyLoad}
        <input type="file" accept=".pem,.pub,text/plain" hidden onChange={(e) => void load(e.target.files?.[0])} />
      </label>
    </div>
  );
}

function VerifyDialog({ copy, onClose, onStarted }: { copy: OffsiteCopy; onClose: () => void; onStarted: (job: Job) => void }) {
  const t = useT(words).backups;
  const c = useT(common).common;
  const toast = useToast();
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const start = async () => {
    setBusy(true);
    try {
      const job = await api.post<Job>(`/offsite/copies/${encodeURIComponent(copy.id)}/verify`, key.trim() ? { public_key: key.trim() } : {});
      toast("info", t.verifyStarted, t.verifyStartedText);
      onStarted(job);
      onClose();
    } catch (error) {
      toast("error", t.notStarted, errorText(error));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title={t.verifyTitle} description={t.verifyText(copy.id)} onClose={onClose}
      footer={<><Button onClick={onClose}>{c.cancel}</Button><Button variant="primary" icon={<ShieldCheck />} loading={busy} onClick={start}>{t.verifyStart}</Button></>}>
      <KeyField value={key} onChange={setKey} />
    </Modal>
  );
}

function RestoreDialog({ copy, onClose, onStarted }: { copy: OffsiteCopy; onClose: () => void; onStarted: (job: Job) => void }) {
  const t = useT(words).backups;
  const c = useT(common).common;
  const toast = useToast();
  const { data: instances } = useInstances();
  const own = instances?.find((i) => i.name === copy.instance);
  const [target, setTarget] = useState(own ? copy.instance : "");
  const [key, setKey] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const chosen = instances?.find((i) => i.name === (target || (own ? copy.instance : "")));
  const name = chosen?.name ?? "";
  const production = chosen?.stage === "production";
  const start = async () => {
    setBusy(true);
    try {
      const body: Record<string, unknown> = { instance: name };
      if (key.trim()) body.public_key = key.trim();
      if (production) body.confirm_production = true;
      const job = await api.post<Job>(`/offsite/copies/${encodeURIComponent(copy.id)}/restore`, body);
      toast("info", t.restoreStarted, t.restoreStartedText(name));
      onStarted(job);
      onClose();
    } catch (error) {
      toast("error", t.notStarted, errorText(error));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title={t.restoreTitle} description={t.restoreText(copy.id)} onClose={onClose} wide
      footer={<><Button onClick={onClose}>{c.cancel}</Button><Button variant="primary" icon={<RotateCcw />} loading={busy} disabled={!name || (production && !confirmed)} onClick={start}>{t.restoreStart}</Button></>}>
      <div className="stack">
        {instances && instances.length === 0 ? (
          <Callout tone="warning">{t.noTarget}</Callout>
        ) : (
          <label className="stack-sm">
            <span className="small backup-key-label">{t.into}</span>
            <select className="select" value={name} onChange={(e) => { setTarget(e.target.value); setConfirmed(false); }}>
              {!name && <option value="">…</option>}
              {(instances ?? []).map((i) => <option key={i.name} value={i.name}>{i.name} · Odoo {i.odoo.version}</option>)}
            </select>
          </label>
        )}
        {name && <Callout>{t.current}{name !== copy.instance ? ` ${t.foreign}` : ""}</Callout>}
        {production && (
          <label className="row small">
            <input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />
            <span>{t.confirmProduction(name)}</span>
          </label>
        )}
        <KeyField value={key} onChange={setKey} />
      </div>
    </Modal>
  );
}

/** Where off-site copies go, or why there are none, at the top of the page. */
function OffsiteSummary({ copies }: { copies: OffsiteCopy[] }) {
  const { data: state } = useOffsite();
  const me = useMe();
  const t = useT(words).backups;
  const toast = useToast();
  const scan = useCall(() => api.post<{ found: number; missing: number }>("/offsite/scan"), [["offsite"]]);
  if (!state) return null;
  const settingsLink = me.permissions.includes("settings.manage") && (
    <Link className="btn sm" to="/settings?tab=backups" viewTransition>{t.setUp}</Link>
  );
  if (!state.active)
    return (
      <div className="card rise">
        <div className="card-head">
          <CloudOff />
          <div className="spacer">
            <h2>{t.offTitle}</h2>
            <div className="sub">{state.enabled ? t.notConfiguredText : t.offText}</div>
          </div>
          {settingsLink}
        </div>
      </div>
    );
  const failed = copies.filter((c) => c.status === "failed").length;
  return (
    <div className="card rise">
      <div className="card-head">
        <CloudUpload />
        <div className="spacer">
          <h2>{t.onTitle}</h2>
          <div className="sub">
            {t.onText(state.location ?? "")}{state.other_stages ? ` ${t.otherStages}` : ""}
            {state.last_scan ? ` ${t.lastRead(ago(state.last_scan.at))}` : ""}
          </div>
          {state.fingerprint && (
            <div className="xsmall faint" style={{ marginTop: 4 }}>
              {t.signedBy}: <span className="mono" dir="ltr">{state.fingerprint.slice(0, 23)}…</span>
            </div>
          )}
        </div>
        <div className="row">
          {failed > 0 && <Tag tone="negative">{failed} {t.statuses.failed}</Tag>}
          <Button size="sm" icon={<RefreshCcw />} loading={scan.isPending}
            onClick={() => scan.mutate(undefined, {
              onSuccess: (r) => toast("success", t.readBucket, t.readDone(r.found, r.missing)),
              onError: (error) => toast("error", t.readBucket, errorText(error)),
            })}>
            {t.readBucket}
          </Button>
        </div>
      </div>
    </div>
  );
}

function CopyStatus({ copy }: { copy: OffsiteCopy }) {
  const t = useT(words).backups;
  const moving = copy.status === "uploading" || copy.status === "verifying";
  return (
    <Tag tone={STATUS_TONE[copy.status]} icon={moving ? <Spinner /> : copy.status === "verified" ? <ShieldCheck /> : undefined}>
      {t.statuses[copy.status] ?? copy.status}
    </Tag>
  );
}

function Details({ row }: { row: Row }) {
  const t = useT(words).backups;
  if (row.where === "local") {
    const b = row.backup;
    return (
      <span className="mono">
        {b.db} · {b.series ?? <span className="faint">?</span>}{b.edition === "enterprise" ? " Enterprise" : ""}
      </span>
    );
  }
  const copy = row.copy;
  if (copy.status === "failed" && copy.reason) return <span className="backup-reason negative">{copy.reason}</span>;
  const parts: ReactNode[] = [];
  if (copy.verified_at && copy.status === "verified") parts.push(t.verifiedAt(ago(copy.verified_at)));
  else if (copy.uploaded_at) parts.push(t.uploadedAt(ago(copy.uploaded_at)));
  if (copy.found_in_bucket) parts.push(t.foundInBucket);
  if (copy.reason) parts.push(copy.reason);
  return <span className="backup-reason muted">{parts.join(" · ")}</span>;
}

export function BackupsPage() {
  const me = useMe();
  const can = (permission: string) => me.permissions.includes(permission as never);
  const { data: backups, isPending } = useBackups();
  const { data: copies = [] } = useOffsiteCopies();
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const view = (["all", "local", "offsite"].includes(params.get("view") ?? "") ? params.get("view") : "all") as View;
  const [verifying, setVerifying] = useState<OffsiteCopy | null>(null);
  const [restoring, setRestoring] = useState<OffsiteCopy | null>(null);
  const [job, setJob] = useState<string | null>(null);
  const remove = useCall((file: string) => api.del(`/backups/${encodeURIComponent(file)}`), [["backups"]]);
  const removeCopy = useCall((id: string) => api.del(`/offsite/copies/${encodeURIComponent(id)}`), [["offsite"]]);
  const retry = useCall((id: string) => api.post<Job>(`/offsite/copies/${encodeURIComponent(id)}/retry`), [["offsite"]]);
  const { data: instances } = useInstances();
  const total = backups?.reduce((sum, b) => sum + b.size, 0) ?? 0;
  const t = useT(words).backups;

  const groups = useMemo(() => {
    const rows: Row[] = [
      ...(view !== "offsite" ? (backups ?? []).map((backup): Row => ({ where: "local", at: backup.created, backup })) : []),
      ...(view !== "local" ? copies.map((copy): Row => ({ where: "offsite", at: copy.created ?? 0, copy })) : []),
    ];
    const byInstance = new Map<string, Row[]>();
    for (const row of rows) {
      const name = row.where === "local" ? row.backup.instance : row.copy.instance;
      byInstance.set(name, [...(byInstance.get(name) ?? []), row]);
    }
    return [...byInstance.entries()]
      .map(([name, list]) => ({ name, rows: list.sort((a, b) => b.at - a.at || (a.where === "local" ? -1 : 1)) }))
      .sort((a, b) => b.rows[0]!.at - a.rows[0]!.at);
  }, [backups, copies, view]);

  const choose = (next: View) => {
    const changed = new URLSearchParams(params);
    if (next === "all") changed.delete("view");
    else changed.set("view", next);
    setParams(changed, { preventScrollReset: true });
  };
  const existing = new Set((instances ?? []).map((i) => i.name));
  const localCount = backups?.length ?? 0;

  return (
    <Page>
      <PageHead title={t.title}>
        {t.text}{backups?.length ? t.summary(count(backups.length, "backup"), bytes(total)) : ""}
      </PageHead>
      <OffsiteSummary copies={copies} />
      {(copies.length > 0 || view !== "all") && (
        <div className="row-wrap" role="group" aria-label={t.viewLabel}>
          <Segmented<View> value={view} onChange={choose}
            options={(["all", "local", "offsite"] as View[]).map((v) => ({ value: v, label: t.view[v] }))} />
          <span className="small muted">{t.local(localCount)} · {t.copies(copies.length)}</span>
        </div>
      )}
      {isPending ? (
        <div className="card rise"><div className="card-body"><Skeleton height={140} /></div></div>
      ) : !groups.length ? (
        <div className="card rise">
          {view === "all" ? <Empty icon={<Archive />} title={t.none}>{t.noneText}</Empty> : <Empty icon={<Archive />} title={t.nothingHere}>{t.nothingHereText}</Empty>}
        </div>
      ) : (
        groups.map((group) => (
          <section className="card rise" key={group.name}>
            <div className="card-head">
              <div className="spacer">
                <h2><bdi>{group.name}</bdi>{!existing.has(group.name) && instances && <> <Tag>{t.instanceGone}</Tag></>}</h2>
                <div className="sub">
                  {[t.local(group.rows.filter((r) => r.where === "local").length), t.copies(group.rows.filter((r) => r.where === "offsite").length)].join(" · ")}
                </div>
              </div>
            </div>
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>{t.where}</th><th>{t.kind}</th><th>{t.taken}</th><th>{t.size}</th><th>{t.status}</th><th>{t.details}</th><th /></tr></thead>
                <tbody>
                  {group.rows.map((row) => row.where === "local" ? (
                    <tr key={`l:${row.backup.file}`}>
                      <td><Tag icon={<Archive />}>{t.view.local}</Tag></td>
                      <td><Tag tone={row.backup.kind === "daily" ? "accent" : undefined}>{t.kinds[row.backup.kind] ?? row.backup.kind}</Tag></td>
                      <td className="small nowrap">{dateTime(row.backup.created)} <span className="faint">· {ago(row.backup.created)}</span></td>
                      <td className="small tabular nowrap">{bytes(row.backup.size)}</td>
                      <td />
                      <td className="small"><Details row={row} /></td>
                      <td className="actions nowrap">
                        <a className="btn sm soft" href={`/api/v1/backups/${encodeURIComponent(row.backup.file)}`} download><Download /> {t.download}</a>{" "}
                        <Button size="sm" variant="ghost" icon={<Trash2 />} aria-label={t.delete} onClick={() => confirm(t.confirmDelete(row.backup.file)) && remove.mutate(row.backup.file, { onSuccess: () => toast("success", t.deleted) })} />
                      </td>
                    </tr>
                  ) : (
                    <tr key={`o:${row.copy.id}`}>
                      <td><Tag tone="accent" icon={<CloudUpload />}>{t.view.offsite}</Tag></td>
                      <td><Tag tone={row.copy.kind === "daily" ? "accent" : undefined}>{t.kinds[row.copy.kind] ?? row.copy.kind}</Tag></td>
                      <td className="small nowrap">{row.copy.created ? <>{dateTime(row.copy.created)} <span className="faint">· {ago(row.copy.created)}</span></> : row.copy.stamp}</td>
                      <td className="small tabular nowrap">{row.copy.size ? bytes(row.copy.size) : "–"}</td>
                      <td><CopyStatus copy={row.copy} /></td>
                      <td className="small" style={{ maxWidth: 360 }}><Details row={row} /></td>
                      <td className="actions nowrap">
                        <OffsiteActions copy={row.copy} canRestore={can("offsite.restore") && can("instances.restore")} canDelete={can("offsite.delete")}
                          onVerify={() => setVerifying(row.copy)} onRestore={() => setRestoring(row.copy)} onLog={() => row.copy.job && setJob(row.copy.job)}
                          onRetry={() => retry.mutate(row.copy.id, { onSuccess: (j) => { toast("info", t.retryStarted); setJob(j.id); }, onError: (e) => toast("error", t.notStarted, errorText(e)) })}
                          onDelete={() => confirm(t.confirmDeleteCopy(row.copy.id)) && removeCopy.mutate(row.copy.id, { onSuccess: () => toast("success", t.copyDeleted), onError: (e) => toast("error", t.deleteCopy, errorText(e)) })} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        ))
      )}
      {verifying && <VerifyDialog copy={verifying} onClose={() => setVerifying(null)} onStarted={(j) => setJob(j.id)} />}
      {restoring && <RestoreDialog copy={restoring} onClose={() => setRestoring(null)} onStarted={(j) => setJob(j.id)} />}
      {job && <JobDrawer id={job} onClose={() => setJob(null)} />}
    </Page>
  );
}

function OffsiteActions({ copy, canRestore, canDelete, onVerify, onRestore, onRetry, onLog, onDelete }: {
  copy: OffsiteCopy;
  canRestore: boolean;
  canDelete: boolean;
  onVerify: () => void;
  onRestore: () => void;
  onRetry: () => void;
  onLog: () => void;
  onDelete: () => void;
}) {
  const t = useT(words).backups;
  const moving = copy.status === "uploading" || copy.status === "verifying";
  const complete = copy.status === "stored" || copy.status === "verified";
  return (
    <>
      {canRestore && complete && <Button size="sm" variant="soft" icon={<RotateCcw />} onClick={onRestore}>{t.restore}</Button>}{" "}
      <Menu trigger={(toggle) => <Button size="sm" variant="ghost" icon={<MoreHorizontal />} onClick={toggle} aria-label={`${t.more}: ${copy.id}`} />}>
        {(close) => (
          <>
            <button disabled={moving} onClick={() => { close(); onVerify(); }}><ShieldCheck /> {t.verify}</button>
            {copy.status === "failed" && <button onClick={() => { close(); onRetry(); }}><CloudUpload /> {t.retry}</button>}
            {copy.job && <button onClick={() => { close(); onLog(); }}><ScrollText /> {t.log}</button>}
            {canDelete && (
              <>
                <hr />
                <button className="danger" disabled={moving} onClick={() => { close(); onDelete(); }}><Trash2 /> {t.deleteCopy}</button>
              </>
            )}
          </>
        )}
      </Menu>
    </>
  );
}
