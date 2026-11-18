import { Gauge } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api, errorText } from "@/api/client";
import { keys, useCall, useInstanceResources } from "@/api/queries";
import { RESOURCE_KNOBS, type Instance, type InstanceResources, type Job, type ResourceKnob } from "@/api/types";
import { useCan } from "@/components/Shell";
import { Button, Callout, Skeleton } from "@/components/ui/basics";
import { Modal, useToast } from "@/components/ui/overlay";
import { useT } from "@/i18n";
import common from "@/i18n/common";
import words from "@/i18n/instance";
import { bytes, number, percent } from "@/lib/format";

const GROUPS: { id: "odoo" | "containers"; knobs: ResourceKnob[] }[] = [
  { id: "odoo", knobs: RESOURCE_KNOBS.slice(0, 6) as ResourceKnob[] },
  { id: "containers", knobs: RESOURCE_KNOBS.slice(6) as ResourceKnob[] },
];
const MEMORY = new Set<ResourceKnob>(["odoo_memory_limit", "db_memory_limit"]);
const CORES = new Set<ResourceKnob>(["odoo_cpu_limit", "db_cpu_limit"]);

/** Docker units ("512m", "4g") to bytes; 0 for "0" or anything else. */
function memoryBytes(text: string | number): number {
  const match = /^(\d+(?:\.\d+)?)([bkmg])$/i.exec(String(text).trim());
  if (!match) return 0;
  return Number(match[1]) * 1024 ** "bkmg".indexOf(match[2]!.toLowerCase());
}

/** The Resources section of an instance's Settings tab: its priority under load, what it uses now against its limits,
 *  and its own limits (administrators change them; an empty field follows the stage's default). */
export function ResourcesCard({ instance }: { instance: Instance }) {
  const { data } = useInstanceResources(instance.name);
  return data ? <Resources instance={instance} data={data} /> : <Skeleton height={320} />;
}

function Resources({ instance, data }: { instance: Instance; data: InstanceResources }) {
  const all = useT(words);
  const t = all.resources;
  const c = useT(common);
  const can = useCan();
  const toast = useToast();
  const editable = can("instances.resources");
  const initial = useMemo(
    () => Object.fromEntries(RESOURCE_KNOBS.map((k) => [k, data.own[k] == null ? "" : String(data.own[k])])) as Record<ResourceKnob, string>,
    [data.own],
  );
  const [draft, setDraft] = useState(initial);
  useEffect(() => setDraft(initial), [initial]);
  const [asking, setAsking] = useState(false);
  const dirty = RESOURCE_KNOBS.some((k) => draft[k] !== initial[k]);
  const production = data.stage === "production";
  const apply = useCall(
    (body: Record<string, unknown>) => api.put<Job>(`/instances/${instance.name}/resources`, body),
    [keys.resources(instance.name), keys.instance(instance.name), keys.jobs(instance.name)],
  );

  const shown = (knob: ResourceKnob, value: number | string): string => {
    const zero = value === 0 || value === "0" || value === "";
    if (zero) {
      if (knob === "odoo_workers") return t.threaded;
      const odoo = data.odoo_defaults[knob];
      if (odoo != null) return t.odooDefault(shown(knob, odoo));
      return t.noLimit;
    }
    if (knob.endsWith("_mb")) return `${number(Number(value))} MB`;
    if (knob.startsWith("odoo_limit_time")) return `${number(Number(value))} s`;
    if (CORES.has(knob)) return number(Number(value), 2);
    if (MEMORY.has(knob)) return bytes(memoryBytes(value));
    return number(Number(value));
  };

  const run = () =>
    apply.mutate(
      {
        limits: Object.fromEntries(RESOURCE_KNOBS.map((k) => [k, draft[k].trim() === "" ? null : draft[k].trim()])),
        ...(production ? { confirm_production: true } : {}),
      },
      {
        onSuccess: () => {
          toast("success", t.started, t.startedText);
          setAsking(false);
        },
        onError: (error) => toast("error", t.notApplied, errorText(error)),
      },
    );

  const memoryLimit = memoryBytes(data.limits.odoo_memory_limit);
  const warning = (w: InstanceResources["warnings"][number]): string => {
    const v = w.values;
    switch (w.code) {
      case "reserved_over_total":
        return t.warnings.reserved_over_total(bytes(v.reserved_bytes), bytes(v.total_bytes));
      case "reserved_over_usable":
        return t.warnings.reserved_over_usable(bytes(v.reserved_bytes), bytes(v.usable_bytes));
      case "workers_memory":
        return t.warnings.workers_memory(number(v.workers), number(v.cron), bytes(v.limit_bytes));
      case "limit_over_total":
        return t.warnings.limit_over_total(bytes(v.limit_bytes), bytes(v.total_bytes));
      case "time_cpu_over_real":
        return t.warnings.time_cpu_over_real;
      case "workers_over_cores":
        return t.warnings.workers_over_cores(number(v.workers), number(v.cores), number(v.suggested));
      default:
        return w.text;
    }
  };
  const reservation = (value: string) => (memoryBytes(value) ? bytes(memoryBytes(value)) : t.none);

  return (
    <section className="card resources-card">
      <div className="card-head">
        <Gauge />
        <div className="spacer">
          <h2>{t.title}</h2>
          <div className="sub">{t.text}</div>
        </div>
      </div>
      <div className="card-body stack">
        <div className="resources-priority">
          <div className="stack-sm">
            <div className="small muted">{t.priority}</div>
            <b className={`resources-rank rank-${data.priority.rank}`}>{t.rank[data.stage]}</b>
            <p className="small muted">{t.rankText[data.stage]} {t.onlyUnderLoad}</p>
          </div>
          <dl className="dl small">
            <dt>{t.cpuWeight}</dt>
            <dd className="tabular">{number(data.priority.cpu_weight)}</dd>
            <dt>{t.reservation}</dt>
            <dd>{t.reservationValue(reservation(data.priority.memory_reservation), reservation(data.priority.db_memory_reservation))}</dd>
            <dt>{t.oomScore}</dt>
            <dd className="tabular" dir="ltr">{number(data.priority.oom_score_adj)}</dd>
          </dl>
        </div>
        <div className="resources-usage">
          <div>
            <div className="small muted">{t.usage}: {t.processor}</div>
            <div className="tabular">{t.ofServer(percent(instance.usage.cpu_percent, 1))}</div>
          </div>
          <div>
            <div className="small muted">{t.usage}: {t.memory}</div>
            <div className="tabular">{t.used(bytes(instance.usage.memory_bytes), memoryLimit ? bytes(memoryLimit) : t.noLimit)}</div>
          </div>
        </div>
        {data.unlimited && <p className="small muted">{t.unlimited}</p>}
        {data.pending && <Callout tone="info">{t.pending}</Callout>}
        {data.warnings.length > 0 && (
          <Callout tone="warning">
            <ul className="resources-warnings">{data.warnings.map((w) => <li key={w.code}>{warning(w)}</li>)}</ul>
          </Callout>
        )}
      </div>
      <div className="card-head resources-limits-head">
        <div className="spacer">
          <h2>{t.limits}</h2>
          <div className="sub">{editable ? t.limitsText : t.readOnly}</div>
        </div>
      </div>
      {GROUPS.map((group) => (
        <div key={group.id} role="group" aria-label={t.groups[group.id]}>
          <div className="resources-group small">{t.groups[group.id]}</div>
          {group.knobs.map((knob) => {
            const words = t.knobs[knob];
            const id = `resource-${knob}`;
            return (
              <div className="settings-row" key={knob}>
                <div>
                  <label className="name" htmlFor={id}>{words.label}</label>
                  <div className="desc">{words.hint}</div>
                </div>
                <div className="stack-sm">
                  <input
                    id={id}
                    className="input tabular"
                    dir="ltr"
                    inputMode={MEMORY.has(knob) ? "text" : CORES.has(knob) ? "decimal" : "numeric"}
                    disabled={!editable || apply.isPending}
                    placeholder={String(data.stage_defaults[knob])}
                    value={draft[knob]}
                    onChange={(e) => setDraft((d) => ({ ...d, [knob]: e.target.value }))}
                  />
                  <span className="small faint">
                    {draft[knob] === "" ? t.stageDefault(shown(knob, data.stage_defaults[knob])) : shown(knob, draft[knob])}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      ))}
      {editable && (
        <div className="card-foot row-wrap">
          <span className="spacer" />
          <Button variant="ghost" disabled={!dirty || apply.isPending} onClick={() => setDraft(initial)}>{t.discard}</Button>
          <Button variant="primary" disabled={!dirty && !data.pending} loading={apply.isPending} onClick={() => setAsking(true)}>{t.apply}</Button>
        </div>
      )}
      {asking && (
        <Modal title={t.applyTitle(instance.name)} description={t.applyText} onClose={() => setAsking(false)}
          footer={<><Button onClick={() => setAsking(false)}>{c.common.cancel}</Button>
            <Button variant={production ? "danger" : "primary"} loading={apply.isPending} onClick={run}>{t.apply}</Button></>}>
          {production ? <Callout tone="warning">{t.applyProduction}</Callout> : null}
        </Modal>
      )}
    </section>
  );
}
