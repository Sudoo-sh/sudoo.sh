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
import { ago, dateTime } from "@/lib/format";

const STATUS: Record<DomainStatus, { dot: string; label: string }> = {
  waiting_dns: { dot: "paused", label: "Waiting for DNS" },
  issuing: { dot: "running", label: "Getting a certificate" },
  active: { dot: "online", label: "Active" },
  renewing: { dot: "running", label: "Renewing" },
  expiring: { dot: "paused", label: "Expires soon" },
  failed: { dot: "failed", label: "Failed" },
};

/** Why custom domains cannot be added here, in words for the person looking; null when they can. */
function unavailable(data: Domains, canSettings: boolean): React.ReactNode {
  if (data.mode === "off") {
    const tunnel = data.exposure === "cloudflare-quick";
    return (
      <>
        Custom domains are off on this server.{" "}
        {tunnel
          ? "It is reached through Cloudflare quick tunnels, which cannot carry your own domain: it needs ports 80 and 443 open to the internet, or your own Cloudflare tunnel or proxy in front of the instance."
          : "An administrator chooses how https is handled."}{" "}
        {canSettings ? <Link to="/settings?tab=domains" viewTransition>Open Settings, Custom domains</Link> : "Ask an administrator."}
      </>
    );
  }
  if (data.mode === "acme" && !data.target)
    return <>The server's public name is not set yet (DOMAIN_DNS_TARGET), so nobody can be told where to point a domain. {canSettings ? <Link to="/settings?tab=domains" viewTransition>Set it in Settings</Link> : "Ask an administrator."}</>;
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
  if (!data) return <div className="card card-body"><Skeleton height={120} /></div>;
  const blocked = unavailable(data, can("settings.manage"));
  const full = data.domains.length >= data.limit;
  return (
    <div className="domains">
      {data.listener_error && data.mode === "acme" && (
        <Callout tone="danger">
          The gateway cannot answer custom domains: {data.listener_error} Ports 80 and 443 must be free on the server, and the gateway service needs the current compose.yaml.
        </Callout>
      )}
      {data.authority === "staging" && data.mode === "acme" && (
        <Callout tone="warning">Certificates come from Let's Encrypt's test authority (ACME_DIRECTORY=staging): browsers do not trust them. Fine for trying things out, not for clients.</Callout>
      )}
      <div className="card">
        <div className="card-head">
          <h2>Domains</h2>
          <span className="sub">{data.mode === "external" ? "https handled by your own proxy or tunnel" : data.mode === "acme" ? "https with certificates renewed on their own" : ""}</span>
          <span className="spacer" />
          {manage && !blocked && (
            <Button size="sm" variant="primary" icon={<Plus />} disabled={full} title={full ? `At most ${data.limit} per instance` : undefined} onClick={() => setAdding(true)}>
              Add domain
            </Button>
          )}
        </div>
        <div className="card-body domain-default">
          <span className="muted small">Default address</span>
          {data.default_url ? <a className="mono small" href={data.default_url} target="_blank" rel="noreferrer">{data.default_url.replace(/^https?:\/\//, "")}</a> : <span className="faint small">not ready yet</span>}
          <span className="xsmall faint">{data.domains.some((d) => d.primary && d.url) ? "still works, next to the primary domain" : "used until a primary domain is active"}</span>
        </div>
        {blocked ? (
          <div className="card-body"><Callout>{blocked}</Callout></div>
        ) : data.domains.length === 0 ? (
          <Empty icon={<Globe />} title="No custom domain yet"
            action={manage && <Button variant="primary" icon={<Plus />} onClick={() => setAdding(true)}>Add domain</Button>}>
            Give {production ? "this production instance" : "this instance"} your client's own address, such as erp.client.com.
            {data.mode === "acme" ? " The panel gets its certificate and renews it." : ""}
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
  const status = STATUS[domain.status];
  const waiting = domain.status !== "active" && domain.status !== "issuing" && (domain.status !== "expiring" || !!domain.reason);
  const certificate = domain.certificate;
  return (
    <li className="domain-row">
      <div className="domain-main">
        <div className="domain-name">
          {domain.url ? <a href={domain.url} target="_blank" rel="noreferrer"><Lock aria-hidden /> {domain.domain}</a> : <span>{domain.domain}</span>}
          {domain.primary && <Tag tone="accent" icon={<Star />}>Primary</Tag>}
          {certificate?.staging && <Tag tone="caution">Test certificate</Tag>}
        </div>
        <div className="domain-meta">
          <Status status={status.dot} label={status.label} />
          {certificate?.expires_at && <span className="small muted">Certificate until {dateTime(certificate.expires_at)}</span>}
          {domain.renews_at && domain.status === "active" && <span className="xsmall faint">renews {ago(domain.renews_at)}</span>}
          {domain.next_check_at && <span className="xsmall faint">next check {ago(domain.next_check_at)}</span>}
        </div>
        {domain.reason && <p className={`domain-reason small ${domain.status === "failed" || domain.status === "expiring" ? "negative" : "muted"}`}>{domain.reason}</p>}
        {waiting && domain.dns_records.length > 0 && <DnsRecords records={domain.dns_records} />}
      </div>
      {manage && (
        <div className="domain-actions">
          {domain.status !== "active" && domain.status !== "issuing" && (
            <Button size="sm" icon={<RefreshCw />} loading={check.isPending}
              onClick={() => check.mutate(undefined, { onSuccess: () => toast("success", "Checking now"), onError: (e) => toast("error", "Not checked", errorText(e)) })}>
              Check now
            </Button>
          )}
          <Menu trigger={(open) => <Button size="sm" variant="ghost" icon={<MoreHorizontal />} aria-label={`More for ${domain.domain}`} onClick={open} />}>
            {(close) => (
              <>
                {!domain.primary && <button onClick={() => { close(); onPrimary(); }}><Star /> Make primary</button>}
                <button className="danger" onClick={() => { close(); onRemove(); }}><Trash2 /> Remove</button>
              </>
            )}
          </Menu>
        </div>
      )}
    </li>
  );
}

function DnsRecords({ records }: { records: DnsRecord[] }) {
  return (
    <div className="dns-records">
      <div className="xsmall muted">Create this record at the domain's DNS provider{records.length > 1 ? " (all of them)" : ""}:</div>
      <div className="table-wrap">
        <table className="table compact">
          <thead><tr><th>Type</th><th>Name</th><th>Value</th></tr></thead>
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
  const add = useCall(
    () => api.post<CustomDomain>(`/instances/${instance.name}/domains`, { domain: value.trim(), ...(production ? { confirm_production: true } : {}) }),
    [keys.domains(instance.name)],
  );
  const submit = () =>
    add.mutate(undefined, {
      onSuccess: (domain) => {
        toast("success", `${domain.domain} added`);
        if (domain.dns_records.length) setAdded(domain);
        else onClose();
      },
      onError: (e) => setError(errorText(e)),
    });
  if (added)
    return (
      <Modal title={`Point ${added.domain} here`} description="The panel checks DNS on its own and gets the certificate as soon as the record is visible." onClose={onClose}
        footer={<Button variant="primary" onClick={onClose}>Done</Button>}>
        <DnsRecords records={added.dns_records} />
        <p className="small muted">DNS changes usually show within minutes, sometimes a few hours. Behind Cloudflare, set the record to DNS only (grey cloud).</p>
      </Modal>
    );
  return (
    <Modal title="Add a domain" description={data.mode === "external" ? "Your own proxy or tunnel sends the domain to this instance and handles https." : "Visitors reach this instance at the domain, over https."} onClose={onClose}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={add.isPending} disabled={!value.trim()} onClick={submit}>{production ? "Add to production" : "Add"}</Button></>}>
      <form onSubmit={(e) => { e.preventDefault(); if (value.trim()) submit(); }}>
        <Field label="Domain" hint="A full name such as erp.client.com: no https://, no path." error={error || undefined}>
          <input className="input mono" autoFocus autoComplete="off" spellCheck={false} placeholder="erp.client.com" value={value}
            onChange={(e) => { setValue(e.target.value); setError(""); }} />
        </Field>
      </form>
      {data.mode === "acme" && data.target && (
        <p className="small muted">
          You will create a {data.target.record_type} record pointing at <span className="mono">{data.target.host}</span>.
        </p>
      )}
      {data.mode === "external" && (
        <p className="small muted">Point your tunnel or proxy at this instance's gateway port (under Access), then add the domain: the panel checks that it reaches the instance.</p>
      )}
      {production && <Callout tone="warning">This is a production instance. Once the domain is active and primary, links in Odoo's e-mails and portal use it.</Callout>}
    </Modal>
  );
}

function RemoveDialog({ instance, domain, onClose }: { instance: Instance; domain: CustomDomain; onClose: () => void }) {
  const toast = useToast();
  const production = instance.stage === "production";
  const remove = useCall(
    () => api.del(`/instances/${instance.name}/domains/${domain.domain}`, production ? { confirm_production: true } : undefined),
    [keys.domains(instance.name), keys.instance(instance.name)],
  );
  return (
    <Modal title={`Remove ${domain.domain}?`} description="Visitors can no longer reach the instance at this domain." onClose={onClose}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="danger" loading={remove.isPending}
        onClick={() => remove.mutate(undefined, { onSuccess: () => { toast("success", `${domain.domain} removed`); onClose(); }, onError: (e) => toast("error", "Not removed", errorText(e)) })}>
        Remove</Button></>}>
      <p className="small">Its certificate and key are deleted.{domain.primary ? " Another domain becomes primary, or the default address is used again." : ""}</p>
      {production && <Callout tone="warning">{instance.name} is a production instance: its users may be using this domain right now.</Callout>}
    </Modal>
  );
}

function PrimaryDialog({ instance, domain, onClose }: { instance: Instance; domain: CustomDomain; onClose: () => void }) {
  const toast = useToast();
  const production = instance.stage === "production";
  const save = useCall(
    () => api.patch<CustomDomain>(`/instances/${instance.name}/domains/${domain.domain}`, { primary: true, ...(production ? { confirm_production: true } : {}) }),
    [keys.domains(instance.name), keys.instance(instance.name)],
  );
  return (
    <Modal title={`Make ${domain.domain} primary?`} description="The instance's address in the panel and in Odoo (web.base.url) becomes this domain once it is active." onClose={onClose}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={save.isPending}
        onClick={() => save.mutate(undefined, { onSuccess: () => { toast("success", `${domain.domain} is primary`); onClose(); }, onError: (e) => toast("error", "Not changed", errorText(e)) })}>
        Make primary</Button></>}>
      {production && <Callout tone="warning">Links in e-mails Odoo sends from now on use the new domain.</Callout>}
    </Modal>
  );
}
