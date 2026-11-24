// Settings › Custom domains: managed subdomains (every plan), as Sudoo.sh offers them and this panel uses them.

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Globe, RefreshCcw } from "lucide-react";
import { useState } from "react";
import { api, errorText } from "@/api/client";
import type { ManagedSubdomains } from "@/api/types";
import { useCan } from "@/components/Session";
import { Button, Callout, Skeleton, Tag } from "@/components/app/basics";
import { useToast } from "@/components/app/overlay";
import { useT } from "@/i18n";
import words from "@/i18n/settings";
import { ago } from "@/lib/format";

const key = ["managed-subdomains"] as const;

export const useManagedSubdomains = () =>
  useQuery({ queryKey: key, queryFn: () => api.get<ManagedSubdomains>("/server/managed-subdomains") });

export function ManagedSubdomainsCard() {
  const t = useT(words);
  const m = t.managed;
  const { data, isPending } = useManagedSubdomains();
  const can = useCan();
  const toast = useToast();
  const client = useQueryClient();
  const [busy, setBusy] = useState(false);
  const check = async () => {
    setBusy(true);
    try {
      client.setQueryData(key, await api.post<ManagedSubdomains>("/server/managed-subdomains/sync"));
      toast("success", m.checked);
    } catch (error) {
      toast("error", m.notChecked, errorText(error));
    } finally {
      setBusy(false);
    }
  };
  const state = !data
    ? null
    : !data.linked
      ? { tone: undefined, text: m.notLinked }
      : !data.setting
        ? { tone: "caution" as const, text: m.off }
        : !data.enabled
          ? { tone: undefined, text: m.notOffered }
          : { tone: "positive" as const, text: m.active };
  const addresses = Object.values(data?.addresses ?? {});
  return (
    <section className="card">
      <div className="card-head">
        <Globe />
        <div className="spacer">
          <h2>{t.groups.managedSubdomains.title}</h2>
          <div className="sub">{t.groups.managedSubdomains.text}</div>
        </div>
        {can("settings.manage") && data?.linked && (
          <Button size="sm" icon={<RefreshCcw />} loading={busy} onClick={check}>
            {m.checkNow}
          </Button>
        )}
      </div>
      <div className="card-body stack">
        {isPending || !data ? (
          <Skeleton height={80} />
        ) : (
          <>
            <dl className="dl small">
              <dt>{m.status}</dt>
              <dd>{state && <Tag tone={state.tone}>{state.text}</Tag>}</dd>
              {data.base_domain && (
                <>
                  <dt>{m.base}</dt>
                  <dd>
                    <bdi dir="ltr" className="mono">
                      .{data.company ?? "<company>"}.{data.base_domain}
                    </bdi>
                  </dd>
                </>
              )}
              {data.enabled && (
                <>
                  <dt>{m.company}</dt>
                  <dd>
                    {data.company ? (
                      <bdi dir="ltr" className="mono">
                        {data.company}
                      </bdi>
                    ) : (
                      <span className="faint">{m.companyLater}</span>
                    )}
                  </dd>
                </>
              )}
              {data.enabled && (
                <>
                  <dt>{m.used}</dt>
                  <dd>{m.usedOf(data.used ?? data.held, data.limit)}</dd>
                </>
              )}
              {data.linked && (
                <>
                  <dt>{m.addresses}</dt>
                  <dd>
                    {addresses.length ? (
                      <bdi dir="ltr" className="mono">
                        {addresses.join(" · ")}
                      </bdi>
                    ) : (
                      <span className="faint">{m.noAddress}</span>
                    )}
                  </dd>
                </>
              )}
              <dt>{m.lastChecked}</dt>
              <dd>{data.checked_at ? ago(data.checked_at) : m.never}</dd>
            </dl>
            {data.error && <Callout tone="warning">{data.error}</Callout>}
            {data.pending_release.length > 0 && (
              <p className="xsmall faint">{m.pending(data.pending_release.length)}</p>
            )}
          </>
        )}
      </div>
    </section>
  );
}
