// Resources of a project (its Settings tab: a card per stage) and of a branch (its Branch settings tab: the same editor,
// collapsed under "Override for this branch"), as Odoo.sh sizes a project: workers, timeouts, memory, storage
// (web/v1/project_resources.py).

import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, Cpu, Database, HardDrive, Minus, Plus, RotateCcw } from "lucide-react";
import { useEffect, useId, useMemo, useState } from "react";
import { api, errorText } from "@/api/client";
import { keys, useCall } from "@/api/queries";
import type {
  BranchResources as BranchResourcesData,
  Permission,
  ProjectBranch,
  ProjectDetail,
  ProjectMeta,
  ProjectResources as ProjectResourcesData,
  ResourceKnob,
  ResourceLayerView,
  ResourceLimits,
  ResourcePlan,
  ResourceServer,
  ResourcesChange,
  Stage,
  StorageUse,
} from "@/api/types";
import { useCan } from "@/components/Shell";
import { STAGE_LABEL, StageTag } from "@/components/StageTag";
import { Button, Callout, Skeleton, Tag } from "@/components/app/basics";
import { Modal, useToast } from "@/components/app/overlay";
import { Input } from "@/components/ui/input";
import { useT } from "@/i18n";
import common from "@/i18n/common";
import words from "@/i18n/resources";
import { ago, bytes, number } from "@/lib/format";

const STAGES: Stage[] = ["production", "staging", "development"];
const STEPPED: ResourceKnob[] = ["odoo_workers", "odoo_cron_threads"];
const TIMES: ResourceKnob[] = ["odoo_limit_time_real", "odoo_limit_time_real_cron"];
const MORE: ResourceKnob[] = [
  "odoo_limit_time_cpu",
  "odoo_limit_memory_soft_mb",
  "odoo_limit_memory_hard_mb",
  "odoo_memory_limit",
  "odoo_cpu_limit",
  "db_memory_limit",
  "db_cpu_limit",
  "db_max_connections",
];
const EDITED: ResourceKnob[] = [...STEPPED, ...TIMES, ...MORE];
const MEMORY = new Set<ResourceKnob>(["odoo_memory_limit", "db_memory_limit"]);
const CORES = new Set<ResourceKnob>(["odoo_cpu_limit", "db_cpu_limit"]);

export const resourceKeys = {
  project: (id: string) => ["project-resources", id] as const,
  branch: (id: string, branch: string) => ["branch-resources", id, branch] as const,
};
const branchPath = (id: string, branch: string) =>
  `/projects/${encodeURIComponent(id)}/branches/${branch.split("/").map(encodeURIComponent).join("/")}/resources`;

/** Docker units ("512m", "4g") to bytes; 0 for "0" or anything else. */
export function memoryBytes(text: string | number | undefined | null): number {
  const match = /^(\d+(?:\.\d+)?)([bkmg])$/i.exec(String(text ?? "").trim());
  if (!match) return 0;
  return Number(match[1]) * 1024 ** "bkmg".indexOf(match[2]!.toLowerCase());
}

type T = (typeof words)["en"];
const zero = (value: unknown) => value == null || value === "" || value === 0 || value === "0";

/** A value as people read it: "Threaded mode", "Odoo's default (120 s)", "4 GB", "2 cores". */
function shown(
  t: T,
  knob: ResourceKnob,
  value: number | string | undefined,
  odoo: Partial<Record<ResourceKnob, number>>,
): string {
  const zero = value == null || value === "" || value === 0 || value === "0";
  if (zero) {
    if (knob === "odoo_workers") return t.threaded;
    const fallback = odoo[knob];
    if (fallback != null) return `${t.odooDefault} (${shown(t, knob, fallback, {})})`;
    return t.noLimit;
  }
  const unit = t.knobs[knob].unit;
  if (MEMORY.has(knob)) return bytes(memoryBytes(value));
  if (CORES.has(knob)) return number(Number(value), 2);
  return unit ? `${number(Number(value))} ${unit}` : number(Number(value));
}

// ---------------------------------------------------------------- pieces

/** − value + : a whole number from `min` to its maximum; the arrow keys work on the field itself. */
function Stepper({
  id,
  value,
  min = 0,
  max,
  label,
  disabled,
  onChange,
}: {
  id: string;
  value: number;
  min?: number;
  max: number;
  label: string;
  disabled?: boolean;
  onChange: (value: number) => void;
}) {
  const t = useT(words);
  const set = (next: number) => onChange(Math.max(min, max ? Math.min(max, next) : next));
  return (
    <div className="stepper" role="group" aria-labelledby={`${id}-label`}>
      <Button
        size="sm"
        variant="ghost"
        icon={<Minus />}
        aria-label={t.decrease(label)}
        disabled={disabled || value <= min}
        onClick={() => set(value - 1)}
      />
      <input
        id={id}
        className="stepper-value tabular"
        inputMode="numeric"
        dir="ltr"
        value={value}
        disabled={disabled}
        role="spinbutton"
        aria-valuenow={value}
        aria-valuemin={min}
        aria-valuemax={max || undefined}
        onChange={(e) => /^\d{0,4}$/.test(e.target.value) && set(Number(e.target.value || 0))}
        onKeyDown={(e) => {
          if (e.key === "ArrowUp") {
            e.preventDefault();
            set(value + 1);
          }
          if (e.key === "ArrowDown") {
            e.preventDefault();
            set(value - 1);
          }
        }}
      />
      <Button
        size="sm"
        variant="ghost"
        icon={<Plus />}
        aria-label={t.increase(label)}
        disabled={disabled || (max > 0 && value >= max)}
        onClick={() => set(value + 1)}
      />
    </div>
  );
}

/** Used against quota as a bar; the bar turns amber near the quota and red over it. */
export function StorageBar({ use, label }: { use: StorageUse; label?: string }) {
  const t = useT(words).storage;
  const share =
    use.quota_bytes && use.total_bytes != null ? Math.min(100, (100 * use.total_bytes) / use.quota_bytes) : 0;
  return (
    <div className="storage-bar stack-xs">
      <div className="row-wrap small">
        {label && (
          <b className="truncate" dir="auto">
            {label}
          </b>
        )}
        <span className="spacer" />
        {use.level === "warning" && <Tag tone="caution">{t.warning}</Tag>}
        {use.level === "over" && <Tag tone="negative">{t.over}</Tag>}
        <span className="tabular muted">
          {use.total_bytes == null
            ? t.notMeasured
            : use.quota_bytes
              ? t.used(bytes(use.total_bytes), bytes(use.quota_bytes))
              : t.usedNoQuota(bytes(use.total_bytes))}
        </span>
      </div>
      {use.quota_bytes != null && (
        <div
          className="meter"
          data-level={use.level}
          role="meter"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(share)}
          aria-label={label ?? t.title}
        >
          <span style={{ inlineSize: `${share}%` }} />
        </div>
      )}
      {use.total_bytes != null && (
        <div className="xsmall faint tabular">
          {t.split(bytes(use.database_bytes ?? 0), bytes(use.files_bytes ?? 0))}
          {use.measured_at ? ` · ${t.measured(ago(use.measured_at))}` : ""}
        </div>
      )}
    </div>
  );
}

/** "This server: 9.1 GB of 14 GB reserved (after this change)". */
function ServerLine({ server, plan }: { server: ResourceServer; plan: ResourcePlan | null }) {
  const t = useT(words);
  const usable = plan?.usable_bytes ?? server.usable_bytes;
  if (!usable) return <span className="small faint">{t.serverUnknown}</span>;
  const reserved = plan ? plan.after_bytes : server.reserved_bytes;
  const over = reserved > usable;
  return (
    <span className={`small tabular server-line${over ? " over" : ""}`} aria-live="polite">
      <Cpu aria-hidden />
      {plan ? t.serverLineAfter(bytes(reserved), bytes(usable)) : t.serverLine(bytes(reserved), bytes(usable))}
    </span>
  );
}

// ---------------------------------------------------------------- the editor

type Draft = Partial<Record<ResourceKnob, string>>;
const draftOf = (own: Partial<ResourceLimits>): Draft =>
  Object.fromEntries(Object.entries(own).map(([k, v]) => [k, String(v)]));
const limitsOf = (draft: Draft) =>
  Object.fromEntries(EDITED.map((k) => [k, draft[k] == null || draft[k]!.trim() === "" ? null : draft[k]!.trim()]));
const same = (a: Draft, b: Draft) => EDITED.every((k) => (a[k] ?? "") === (b[k] ?? ""));

/** The values of one stage of a project, or of one branch: steppers for the workers, the timeouts, more limits behind
 *  a disclosure, what is computed, and the memory the change reserves on the server, checked as it is typed. */
function ResourceEditor({
  view,
  maxima,
  odoo,
  server,
  path,
  body,
  stage,
  count,
  editable,
  refresh,
  idPrefix,
}: {
  view: ResourceLayerView;
  maxima: ResourceLimits;
  odoo: Partial<Record<ResourceKnob, number>>;
  server: ResourceServer;
  path: string;
  body: Record<string, unknown>;
  stage: Stage;
  count: number;
  editable: boolean;
  refresh: readonly (readonly unknown[])[];
  idPrefix: string;
}) {
  const t = useT(words);
  const c = useT(common).common;
  const toast = useToast();
  const initial = useMemo(() => draftOf(view.own), [view.own]);
  const [draft, setDraft] = useState<Draft>(initial);
  useEffect(() => setDraft(initial), [initial]);
  const [more, setMore] = useState(() => MORE.some((k) => k in view.own));
  const [asking, setAsking] = useState(false);
  const dirty = !same(draft, initial);
  const limits = limitsOf(draft);

  // what the change reserves, asked again a moment after the last keystroke
  const [asked, setAsked] = useState(limits);
  useEffect(() => {
    const timer = window.setTimeout(() => setAsked(limitsOf(draft)), 350);
    return () => window.clearTimeout(timer);
  }, [draft]);
  const preview = useQuery({
    queryKey: ["resources-preview", path, body, asked],
    queryFn: () =>
      api.put<{ plan: ResourcePlan; changes: string[]; view: ResourceLayerView }>(path, {
        ...body,
        limits: asked,
        preview: true,
      }),
    enabled: editable && dirty,
    placeholderData: keepPreviousData,
    retry: false,
  });
  const previewError = preview.error ? errorText(preview.error) : null;
  const shownView = dirty && preview.data ? preview.data.view : view;
  const plan = dirty && preview.data ? preview.data.plan : null;

  const apply = useCall((payload: Record<string, unknown>) => api.put<ResourcesChange>(path, payload), refresh);
  const run = () =>
    apply.mutate(
      { ...body, limits, ...(stage === "production" ? { confirm_production: true } : {}) },
      {
        onSuccess: (answer) => {
          setAsking(false);
          toast(
            "success",
            t.applied,
            [t.appliedText(answer.jobs.length), answer.waiting.length ? t.waiting(answer.waiting.join(", ")) : ""]
              .filter(Boolean)
              .join(" "),
          );
        },
        onError: (error) => toast("error", t.notApplied, errorText(error)),
      },
    );

  const value = (knob: ResourceKnob) =>
    draft[knob] != null && draft[knob] !== "" ? draft[knob]! : shownView.inherited[knob];
  const set = (knob: ResourceKnob, next: string | null) =>
    setDraft((d) => {
      const copy = { ...d };
      if (next == null) delete copy[knob];
      else copy[knob] = next;
      return copy;
    });
  const origin = (knob: ResourceKnob) =>
    draft[knob] != null && draft[knob] !== ""
      ? null
      : (t.source[shownView.sources[knob] === "own" ? "stage" : shownView.sources[knob]] ?? t.source.stage);
  const reset = (knob: ResourceKnob) =>
    draft[knob] != null && editable ? (
      <Button
        size="sm"
        variant="ghost"
        className="follow"
        icon={<RotateCcw />}
        onClick={() => set(knob, null)}
        aria-label={`${t.followDefaults}: ${t.knobs[knob].label}`}
        title={t.followDefaults}
      />
    ) : null;

  const field = (knob: ResourceKnob) => {
    const id = `${idPrefix}-${knob}`;
    const words = t.knobs[knob];
    const own = draft[knob] != null && draft[knob] !== "";
    const computed = shownView.computed.includes(knob);
    const hint = computed
      ? `${t.computed}: ${shown(t, knob, shownView.applies[knob], odoo)}`
      : own
        ? t.source.own
        : t.follows(origin(knob)!, shown(t, knob, shownView.inherited[knob], odoo));
    return (
      <div className="resource-field" key={knob}>
        <label id={`${id}-label`} htmlFor={id} className="name">
          {words.label}
        </label>
        {STEPPED.includes(knob) ? (
          <div className="row">
            <Stepper
              id={id}
              label={words.label}
              value={Number(value(knob) ?? 0) || (knob === "odoo_cron_threads" ? Number(odoo[knob] ?? 0) : 0)}
              min={knob === "odoo_cron_threads" ? 1 : 0}
              max={Number(maxima[knob] ?? 0)}
              disabled={!editable || apply.isPending}
              onChange={(n) => set(knob, String(n))}
            />
            {reset(knob)}
          </div>
        ) : (
          <div className="row">
            <Input
              id={id}
              className="tabular"
              dir="ltr"
              disabled={!editable || apply.isPending}
              inputMode={MEMORY.has(knob) ? "text" : CORES.has(knob) ? "decimal" : "numeric"}
              placeholder={String(
                computed ? shownView.applies[knob] : zero(shownView.inherited[knob]) ? "" : shownView.inherited[knob],
              )}
              value={draft[knob] ?? ""}
              onChange={(e) => set(knob, e.target.value)}
              aria-describedby={`${id}-hint`}
            />
            {reset(knob)}
          </div>
        )}
        <div id={`${id}-hint`} className="xsmall faint">
          {hint}
          {Number(maxima[knob] ?? 0) || memoryBytes(maxima[knob])
            ? ` · ${t.maximum(shown(t, knob, maxima[knob], odoo))}`
            : ""}
        </div>
      </div>
    );
  };

  const computedMemory =
    shownView.computed.includes("odoo_memory_limit") || shownView.computed.includes("odoo_cpu_limit");
  return (
    <div className="resource-editor stack">
      <div className="resource-grid">{STEPPED.map(field)}</div>
      {computedMemory && (
        <p className="small muted computed-line">
          <Cpu aria-hidden />{" "}
          {t.computedValue(
            shown(t, "odoo_memory_limit", shownView.applies.odoo_memory_limit, odoo),
            number(Number(shownView.applies.odoo_cpu_limit), 2),
          )}
        </p>
      )}
      <div className="resource-grid">{TIMES.map(field)}</div>
      <button
        type="button"
        className="disclosure small"
        aria-expanded={more}
        aria-controls={`${idPrefix}-more`}
        onClick={() => setMore(!more)}
      >
        <ChevronDown aria-hidden /> {more ? t.fewer : t.more}
      </button>
      {more && (
        <div className="resource-grid disclosed" id={`${idPrefix}-more`}>
          {MORE.map(field)}
        </div>
      )}
      {plan?.refusal && <Callout tone="warning">{plan.refusal}</Callout>}
      {previewError && <Callout tone="warning">{previewError}</Callout>}
      <div className="resource-foot row-wrap">
        <ServerLine server={server} plan={plan} />
        <span className="spacer" />
        {editable && (
          <>
            <Button variant="ghost" disabled={!dirty || apply.isPending} onClick={() => setDraft(initial)}>
              {t.discard}
            </Button>
            <Button
              variant="primary"
              disabled={!dirty || Boolean(plan?.refusal) || Boolean(previewError)}
              loading={apply.isPending}
              onClick={() => setAsking(true)}
            >
              {t.apply}
            </Button>
          </>
        )}
      </div>
      {asking && (
        <Modal
          title={t.applyTitle(STAGE_LABEL[stage])}
          description={t.applyText(preview.data?.changes.length ?? count)}
          onClose={() => setAsking(false)}
          footer={
            <>
              <Button onClick={() => setAsking(false)}>{c.cancel}</Button>
              <Button variant={stage === "production" ? "danger" : "primary"} loading={apply.isPending} onClick={run}>
                {t.apply}
              </Button>
            </>
          }
        >
          {stage === "production" ? <Callout tone="warning">{t.applyProduction}</Callout> : null}
        </Modal>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- a project's Settings tab

export const useProjectResources = (id: string) =>
  useQuery({
    queryKey: resourceKeys.project(id),
    queryFn: () => api.get<ProjectResourcesData>(`/projects/${encodeURIComponent(id)}/resources`),
  });

/** The Resources section of a project's Settings tab: one card per stage, and the storage of its instances. */
export function ProjectResources({ project, meta }: { project: ProjectDetail; meta: ProjectMeta }) {
  const t = useT(words);
  const roleCan = useCan();
  const can = (permission: Permission) =>
    meta.permissions ? meta.permissions.includes(permission) : roleCan(permission);
  const { data, error, isPending } = useProjectResources(project.id);
  const editable = can("projects.resources");
  const refresh = [
    resourceKeys.project(project.id),
    keys.project(project.id),
    ["branch-resources", project.id],
  ] as const;
  return (
    <section className="stack project-resources" aria-labelledby="project-resources-title">
      <div className="section-head">
        <h2 id="project-resources-title">{t.title}</h2>
        <p className="small muted">{t.text}</p>
        <p className="small faint">{t.sizing}</p>
        {!editable && <p className="small faint">{t.readOnly}</p>}
      </div>
      {isPending ? (
        <div className="stage-cards" aria-busy="true" aria-label={t.loading}>
          {STAGES.map((s) => (
            <Skeleton key={s} height={300} />
          ))}
        </div>
      ) : !data ? (
        <Callout tone="warning">
          {t.notRead} {error ? errorText(error) : ""}
        </Callout>
      ) : (
        <>
          <div className="stage-cards">
            {STAGES.map((stage) => {
              const instances = data.instances.filter((i) => i.stage === stage);
              return (
                <section className="card stage-card" key={stage} aria-label={STAGE_LABEL[stage]}>
                  <div className="card-head">
                    <StageTag stage={stage} />
                    <span className="spacer" />
                    <span className="small muted">{t.instances(instances.length)}</span>
                    {instances.some((i) => i.pending) && <Tag tone="caution">{t.pending}</Tag>}
                  </div>
                  <div className="card-body">
                    <ResourceEditor
                      view={data.stages[stage]}
                      maxima={data.maxima}
                      odoo={data.odoo_defaults}
                      server={data.server}
                      path={`/projects/${encodeURIComponent(project.id)}/resources`}
                      body={{ stage }}
                      stage={stage}
                      count={instances.length}
                      editable={editable}
                      refresh={refresh}
                      idPrefix={`res-${stage}`}
                    />
                  </div>
                </section>
              );
            })}
          </div>
          <ProjectStorage data={data} />
        </>
      )}
    </section>
  );
}

/** Storage of the project's instances: production's database size first, as Odoo.sh shows it. */
function ProjectStorage({ data }: { data: ProjectResourcesData }) {
  const t = useT(words).storage;
  const production = data.instances.filter((i) => i.stage === "production");
  const others = data.instances
    .filter((i) => i.stage !== "production")
    .sort((a, b) => STAGES.indexOf(a.stage) - STAGES.indexOf(b.stage) || a.name.localeCompare(b.name));
  return (
    <section className="card">
      <div className="card-head">
        <HardDrive />
        <div className="spacer">
          <h2>{t.title}</h2>
          <div className="sub">{t.text}</div>
        </div>
        <span className="small muted tabular">
          {t.total}: {bytes(data.storage.total_bytes)}
        </span>
      </div>
      <div className="card-body stack">
        {data.instances.length === 0 && <p className="small muted">{t.none}</p>}
        {production.map((i) => (
          <div key={i.name} className="stack-xs production-size">
            <div className="xsmall muted">
              <Database aria-hidden /> {t.productionSize}
            </div>
            <StorageBar use={i.storage} label={i.name} />
          </div>
        ))}
        {others.map((i) => (
          <StorageBar key={i.name} use={i.storage} label={i.name} />
        ))}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------- a branch's Branch settings tab

/** The branch's own resources, collapsed until someone overrides the project's values for it. */
export function BranchResources({ project, branch }: { project: ProjectDetail; branch: ProjectBranch }) {
  const t = useT(words);
  const roleCan = useCan();
  const editable = branch.permissions
    ? branch.permissions.includes("projects.resources")
    : roleCan("projects.resources");
  const client = useQueryClient();
  const key = resourceKeys.branch(project.id, branch.name);
  const { data, error } = useQuery({
    queryKey: key,
    queryFn: () => api.get<BranchResourcesData>(branchPath(project.id, branch.name)),
  });
  const own = Boolean(data && Object.keys(data.own).length);
  const [open, setOpen] = useState(false);
  const id = useId();
  useEffect(() => setOpen(own), [own]);
  const stop = useCall(
    () =>
      api.put<ResourcesChange>(branchPath(project.id, branch.name), {
        limits: {},
        ...(data?.stage === "production" ? { confirm_production: true } : {}),
      }),
    [key, resourceKeys.project(project.id)],
  );
  if (error)
    return (
      <Callout tone="warning">
        {t.notRead} {errorText(error)}
      </Callout>
    );
  if (!data) return <Skeleton height={120} />;
  return (
    <section className="card branch-resources" aria-labelledby={`${id}-title`}>
      <div className="card-head">
        <Cpu />
        <div className="spacer">
          <h2 id={`${id}-title`}>{t.branch.title}</h2>
          <div className="sub">{own ? t.branch.own : t.branch.follows(STAGE_LABEL[data.stage])}</div>
        </div>
        {editable && !open && (
          <Button size="sm" onClick={() => setOpen(true)}>
            {t.branch.override}
          </Button>
        )}
        {editable && own && (
          <Button
            size="sm"
            variant="ghost"
            loading={stop.isPending}
            onClick={() =>
              stop.mutate(undefined, {
                onSuccess: () => client.invalidateQueries({ queryKey: keys.project(project.id) }),
              })
            }
          >
            {t.branch.stop}
          </Button>
        )}
      </div>
      {open && (
        <div className="card-body stack">
          <p className="small muted">{t.branch.text}</p>
          <ResourceEditor
            view={data}
            maxima={data.maxima}
            odoo={data.odoo_defaults}
            server={data.server}
            path={branchPath(project.id, branch.name)}
            body={{}}
            stage={data.stage}
            count={data.instances.length}
            editable={editable}
            refresh={[key, resourceKeys.project(project.id)]}
            idPrefix={`${id}-res`}
          />
        </div>
      )}
      {data.instances.length > 0 && (
        <div className="card-body stack branch-storage">
          {data.instances.map((i) => (
            <StorageBar key={i.name} use={i.storage} label={i.name} />
          ))}
        </div>
      )}
    </section>
  );
}
