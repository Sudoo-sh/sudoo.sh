// An instance's custom domains: add one, see the DNS record to create, follow its state (DNS, certificate,
// renewal) as it changes (live event "domains"), make one primary, check now, remove.

import { Globe, Lock, MoreHorizontal, Plus, RefreshCw, Star, Trash2 } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";
import { api, errorText } from "@/api/client";
import { keys, useCall, useDomains } from "@/api/queries";
import type { CustomDomain, DnsRecord, Domains, DomainStatus, Instance } from "@/api/types";
import { useCan } from "@/components/Shell";
import { Button, Callout, CopyField, Empty, Field, Skeleton, Status, Tag } from "@/components/ui/basics";
import { Menu, Modal, useToast } from "@/components/ui/overlay";
import { useT, words } from "@/i18n";
import common from "@/i18n/common";
import domainWords from "@/i18n/domains";
import { ago, dateTime } from "@/lib/format";

const STATUS_DOT: Record<DomainStatus, string> = {
  waiting_dns: "paused",
  issuing: "running",
  active: "online",
  renewing: "running",
  expiring: "paused",
  failed: "failed",
};

/** Why custom domains cannot be added here, in words for the person looking; null when they can. */
function unavailable(data: Domains, canSettings: boolean): React.ReactNode {
  const t = words(domainWords);
  if (data.mode === "off") {
    const tunnel = data.exposure === "cloudflare-quick";
    return (
      <>
        {t.off}{" "}
        {tunnel ? t.offTunnel : t.offChoose}{" "}
        {canSettings ? <Link to="/settings?tab=domains" viewTransition>{t.openSettings}</Link> : t.askAdmin}
      </>
    );
  }
  if (data.mode === "acme" && !data.target)
    return <>{t.noTarget} {canSettings ? <Link to="/settings?tab=domains" viewTransition>{t.setInSettings}</Link> : t.askAdmin}</>;
  return null;
}

export function DomainsTab({ instance }: { instance: Instance }) {
  const can = useCan();
  const { data } = useDomains(instance.name);
  const [adding, setAdding] = useState(false);
  const [removing, setRemoving] = useState<CustomDomain | null>(null);
  const [primary, setPrimary] = useState<CustomDomain | null>(null);
  const manage = can("domains.manage");
  const production = instance.stage === "production";
  const t = useT(domainWords);
  if (!data) return <div className="card card-body"><Skeleton height={120} /></div>;
  const blocked = unavailable(data, can("settings.manage"));
  const full = data.domains.length >= data.limit;
  return (
    <div className="domains">
      {data.listener_error && data.mode === "acme" && (
        <Callout tone="danger">
          {t.listenerError(data.listener_error)}
        </Callout>
      )}
      {data.authority === "staging" && data.mode === "acme" && (
        <Callout tone="warning">{t.staging}</Callout>
      )}
      <div className="card">
        <div className="card-head">
          <h2>{t.title}</h2>
          <span className="sub">{data.mode === "external" ? t.external : data.mode === "acme" ? t.acme : ""}</span>
          <span className="spacer" />
          {manage && !blocked && (
            <Button size="sm" variant="primary" icon={<Plus />} disabled={full} title={full ? t.atMost(data.limit) : undefined} onClick={() => setAdding(true)}>
              {t.add}
            </Button>
          )}
        </div>
        <div className="card-body domain-default">
          <span className="muted small">{t.defaultAddress}</span>
          {data.default_url ? <a className="mono small" dir="ltr" href={data.default_url} target="_blank" rel="noreferrer">{data.default_url.replace(/^https?:\/\//, "")}</a> : <span className="faint small">{t.notReady}</span>}
          <span className="xsmall faint">{data.domains.some((d) => d.primary && d.url) ? t.stillWorks : t.usedUntil}</span>
        </div>
        {blocked ? (
          <div className="card-body"><Callout>{blocked}</Callout></div>
        ) : data.domains.length === 0 ? (
          <Empty icon={<Globe />} title={t.emptyTitle}
            action={manage && <Button variant="primary" icon={<Plus />} onClick={() => setAdding(true)}>{t.add}</Button>}>
            {production ? t.emptyProduction : t.empty}
            {data.mode === "acme" ? t.emptyAcme : ""}
          </Empty>
        ) : (
          <ul className="domain-list">
            {data.domains.map((domain) => (
              <DomainRow key={domain.domain} domain={domain} manage={manage}
                onPrimary={() => setPrimary(domain)} onRemove={() => setRemoving(domain)} name={instance.name} />
            ))}
          </ul>
        )}
      </div>
      {adding && <AddDialog instance={instance} data={data} onClose={() => setAdding(false)} />}
      {removing && <RemoveDialog instance={instance} domain={removing} onClose={() => setRemoving(null)} />}
      {primary && <PrimaryDialog instance={instance} domain={primary} onClose={() => setPrimary(null)} />}
    </div>
  );
}

function DomainRow({ domain, manage, name, onPrimary, onRemove }: { domain: CustomDomain; manage: boolean; name: string; onPrimary: () => void; onRemove: () => void }) {
  const toast = useToast();
  const check = useCall(() => api.post<CustomDomain>(`/instances/${name}/domains/${domain.domain}/check`), [keys.domains(name)]);
  const t = useT(domainWords);
  const dot = STATUS_DOT[domain.status] ?? "unknown";
  const waiting = domain.status !== "active" && domain.status !== "issuing" && (domain.status !== "expiring" || !!domain.reason);
  const certificate = domain.certificate;
  return (
    <li className="domain-row">
      <div className="domain-main">
        <div className="domain-name">
          {domain.url ? <a href={domain.url} target="_blank" rel="noreferrer"><Lock aria-hidden /> <bdi dir="ltr">{domain.domain}</bdi></a> : <bdi dir="ltr">{domain.domain}</bdi>}
          {domain.primary && <Tag tone="accent" icon={<Star />}>{t.primary}</Tag>}
          {certificate?.staging && <Tag tone="caution">{t.testCertificate}</Tag>}
        </div>
        <div className="domain-meta">
          <Status status={dot} label={t.status[domain.status] ?? domain.status} />
          {certificate?.expires_at && <span className="small muted">{t.certificateUntil(dateTime(certificate.expires_at))}</span>}
          {domain.renews_at && domain.status === "active" && <span className="xsmall faint">{t.renews(ago(domain.renews_at))}</span>}
          {domain.next_check_at && <span className="xsmall faint">{t.nextCheck(ago(domain.next_check_at))}</span>}
        </div>
        {domain.reason && <p className={`domain-reason small ${domain.status === "failed" || domain.status === "expiring" ? "negative" : "muted"}`}>{domain.reason}</p>}
        {waiting && domain.dns_records.length > 0 && <DnsRecords records={domain.dns_records} />}
      </div>
      {manage && (
        <div className="domain-actions">
          {domain.status !== "active" && domain.status !== "issuing" && (
            <Button size="sm" icon={<RefreshCw />} loading={check.isPending}
              onClick={() => check.mutate(undefined, { onSuccess: () => toast("success", t.checking), onError: (e) => toast("error", t.notChecked, errorText(e)) })}>
              {t.checkNow}
            </Button>
          )}
          <Menu trigger={(open) => <Button size="sm" variant="ghost" icon={<MoreHorizontal />} aria-label={t.moreFor(domain.domain)} onClick={open} />}>
            {(close) => (
              <>
                {!domain.primary && <button onClick={() => { close(); onPrimary(); }}><Star /> {t.makePrimary}</button>}
                <button className="danger" onClick={() => { close(); onRemove(); }}><Trash2 /> {t.remove}</button>
              </>
            )}
          </Menu>
        </div>
      )}
    </li>
  );
}

function DnsRecords({ records }: { records: DnsRecord[] }) {
  const t = useT(domainWords);
  return (
    <div className="dns-records">
      <div className="xsmall muted">{records.length > 1 ? t.createRecords : t.createRecord}</div>
      <div className="table-wrap">
        <table className="table compact">
          <thead><tr><th>{t.type}</th><th>{t.name}</th><th>{t.value}</th></tr></thead>
          <tbody>
            {records.map((r) => (
              <tr key={`${r.type}${r.value}`}>
                <td className="mono">{r.type}</td>
                <td><CopyField value={r.name} /></td>
                <td><CopyField value={r.value} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function AddDialog({ instance, data, onClose }: { instance: Instance; data: Domains; onClose: () => void }) {
  const toast = useToast();
  const [value, setValue] = useState("");
  const [error, setError] = useState("");
  const [added, setAdded] = useState<CustomDomain | null>(null);
  const production = instance.stage === "production";
  const t = useT(domainWords);
  const c = useT(common).common;
  const add = useCall(
    () => api.post<CustomDomain>(`/instances/${instance.name}/domains`, { domain: value.trim(), ...(production ? { confirm_production: true } : {}) }),
    [keys.domains(instance.name)],
  );
  const submit = () =>
    add.mutate(undefined, {
      onSuccess: (domain) => {
        toast("success", t.added(domain.domain));
        if (domain.dns_records.length) setAdded(domain);
        else onClose();
      },
      onError: (e) => setError(errorText(e)),
    });
  if (added)
    return (
      <Modal title={t.pointTitle(added.domain)} description={t.pointText} onClose={onClose}
        footer={<Button variant="primary" onClick={onClose}>{t.done}</Button>}>
        <DnsRecords records={added.dns_records} />
        <p className="small muted">{t.dnsDelay}</p>
      </Modal>
    );
  return (
    <Modal title={t.addTitle} description={data.mode === "external" ? t.addExternal : t.addAcme} onClose={onClose}
      footer={<><Button onClick={onClose}>{c.cancel}</Button><Button variant="primary" loading={add.isPending} disabled={!value.trim()} onClick={submit}>{production ? t.addProduction : t.addButton}</Button></>}>
      <form onSubmit={(e) => { e.preventDefault(); if (value.trim()) submit(); }}>
        <Field label={t.domain} hint={t.domainHint} error={error || undefined}>
          <input className="input mono" dir="ltr" autoFocus autoComplete="off" spellCheck={false} placeholder="erp.client.com" value={value}
            onChange={(e) => { setValue(e.target.value); setError(""); }} />
        </Field>
      </form>
      {data.mode === "acme" && data.target && (
        <p className="small muted">
          {t.willCreate(data.target.record_type, <span key="h" className="mono">{data.target.host}</span>)}
        </p>
      )}
      {data.mode === "external" && (
        <p className="small muted">{t.externalHint}</p>
      )}
      {production && <Callout tone="warning">{t.productionAdd}</Callout>}
    </Modal>
  );
}

function RemoveDialog({ instance, domain, onClose }: { instance: Instance; domain: CustomDomain; onClose: () => void }) {
  const toast = useToast();
  const production = instance.stage === "production";
  const t = useT(domainWords);
  const c = useT(common).common;
  const remove = useCall(
    () => api.del(`/instances/${instance.name}/domains/${domain.domain}`, production ? { confirm_production: true } : undefined),
    [keys.domains(instance.name), keys.instance(instance.name)],
  );
  return (
    <Modal title={t.removeTitle(domain.domain)} description={t.removeText} onClose={onClose}
      footer={<><Button onClick={onClose}>{c.cancel}</Button><Button variant="danger" loading={remove.isPending}
        onClick={() => remove.mutate(undefined, { onSuccess: () => { toast("success", t.removed(domain.domain)); onClose(); }, onError: (e) => toast("error", t.notRemoved, errorText(e)) })}>
        {t.remove}</Button></>}>
      <p className="small">{t.certificateDeleted}{domain.primary ? t.anotherPrimary : ""}</p>
      {production && <Callout tone="warning">{t.productionRemove(<bdi key="n">{instance.name}</bdi>)}</Callout>}
    </Modal>
  );
}

function PrimaryDialog({ instance, domain, onClose }: { instance: Instance; domain: CustomDomain; onClose: () => void }) {
  const toast = useToast();
  const production = instance.stage === "production";
  const t = useT(domainWords);
  const c = useT(common).common;
  const save = useCall(
    () => api.patch<CustomDomain>(`/instances/${instance.name}/domains/${domain.domain}`, { primary: true, ...(production ? { confirm_production: true } : {}) }),
    [keys.domains(instance.name), keys.instance(instance.name)],
  );
  return (
    <Modal title={t.primaryTitle(domain.domain)} description={t.primaryText} onClose={onClose}
      footer={<><Button onClick={onClose}>{c.cancel}</Button><Button variant="primary" loading={save.isPending}
        onClick={() => save.mutate(undefined, { onSuccess: () => { toast("success", t.isPrimary(domain.domain)); onClose(); }, onError: (e) => toast("error", t.notChanged, errorText(e)) })}>
        {t.makePrimary}</Button></>}>
      {production && <Callout tone="warning">{t.productionPrimary}</Callout>}
    </Modal>
  );
}
