// An instance's Domains tab without the premium domains package: the custom domains set up before keep being
// served (the core does that), and each can be removed. With the package, its tab (slot "instance.domains") takes
// this one's place; RemoveDomainDialog is shared with it.

import { Globe, Trash2 } from "lucide-react";
import { useState } from "react";
import { api, errorText } from "@/api/client";
import { keys, useCall, useDomains } from "@/api/queries";
import type { CustomDomain, Instance } from "@/api/types";
import { useCan } from "@/components/Session";
import { Button, Callout, Skeleton, Status, Tag } from "@/components/app/basics";
import { Modal, useToast } from "@/components/app/overlay";
import { useT } from "@/i18n";
import common from "@/i18n/common";
import words from "@/i18n/domains";
import { PlanCard } from "@/premium/components";

export function RemoveDomainDialog({ instance, domain, onClose }: { instance: Instance; domain: CustomDomain; onClose: () => void }) {
  const toast = useToast();
  const production = instance.stage === "production";
  const t = useT(words);
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

export function DomainsCore({ instance }: { instance: Instance }) {
  const t = useT(words);
  const can = useCan();
  const { data, isPending } = useDomains(instance.name);
  const [removing, setRemoving] = useState<CustomDomain | null>(null);
  if (isPending || !data) return <div className="stack tab-panel"><Skeleton height={140} /></div>;
  return (
    <div className="stack tab-panel">
      <section className="card">
        <div className="card-head"><h2>{t.title}</h2></div>
        <div className="card-body stack-sm">
          {data.domains.length === 0 ? <p className="small faint">{t.none}</p> : (
            <>
              {data.domains.map((domain) => (
                <div key={domain.domain} className="row-wrap small">
                  <Globe />
                  {domain.url ? <a className="mono" dir="ltr" href={domain.url} target="_blank" rel="noreferrer">{domain.domain}</a>
                    : <bdi dir="ltr" className="mono">{domain.domain}</bdi>}
                  {domain.primary && <Tag tone="accent">{t.primary}</Tag>}
                  <Status status={domain.status} label={t.status[domain.status] ?? domain.status} />
                  <span className="spacer" />
                  {can("domains.manage") && (
                    <Button size="sm" variant="ghost" icon={<Trash2 />} aria-label={t.removeLabel(domain.domain)} onClick={() => setRemoving(domain)} />
                  )}
                </div>
              ))}
              <p className="xsmall faint">{t.keepServing}</p>
            </>
          )}
        </div>
      </section>
      <PlanCard id="domains" />
      {removing && <RemoveDomainDialog instance={instance} domain={removing} onClose={() => setRemoving(null)} />}
    </div>
  );
}
