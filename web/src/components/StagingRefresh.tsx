// The scheduled refresh of a project's staging from production (GET/PUT /projects/<id>/staging-refresh, web/v1/
// promotion.py): off, every day or every week at a chosen time, for every staging branch or the ones chosen. It runs
// what "Rebuild from production" does; "Refresh now" runs it at once. A card of the project's Settings tab.

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import { api, errorText, call } from "@/api/client";
import { keys, useCall } from "@/api/queries";
import type { ProjectDetail, RefreshFrequency, StagingRefresh as Schedule, StagingRefreshMeta, StagingRefreshRun } from "@/api/types";
import { useCan } from "@/components/Shell";
import { Button, Field, Segmented, Skeleton } from "@/components/app/basics";
import { useToast } from "@/components/app/overlay";
import { SelectBox } from "@/components/app/select";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { useT } from "@/i18n";
import words from "@/i18n/promotion";
import { ago } from "@/lib/format";

const key = (id: string) => ["project", id, "staging-refresh"] as const;
const path = (id: string) => `/projects/${encodeURIComponent(id)}/staging-refresh`;

export function StagingRefresh({ project }: { project: ProjectDetail }) {
  const t = useT(words).refresh;
  const can = useCan();
  const toast = useToast();
  const client = useQueryClient();
  const query = useQuery({ queryKey: key(project.id), queryFn: () => call<Schedule>("GET", path(project.id)) });
  const [draft, setDraft] = useState<Schedule | null>(null);
  useEffect(() => { if (query.data) setDraft(query.data.data); }, [query.data]);
  const editable = can("projects.manage") && can("instances.restore");
  const save = useCall((body: Schedule) => api.put<Schedule>(path(project.id), {
    frequency: body.frequency, time: body.time, weekday: body.weekday, branches: body.branches,
  }), [key(project.id)]);
  const run = useCall(() => api.post<StagingRefreshRun>(`${path(project.id)}/run`, {}), [key(project.id), keys.project(project.id), keys.instances]);
  if (query.isPending || !draft) {
    return <section className="card"><div className="card-head"><h2>{t.title}</h2></div><div className="card-body stack"><Skeleton height={36} /><Skeleton height={36} /></div></section>;
  }
  if (query.error) return null;
  const saved = query.data.data;
  const meta = (query.data.meta ?? { branches: [] }) as unknown as StagingRefreshMeta;
  const dirty = JSON.stringify(saved) !== JSON.stringify(draft);
  const set = (values: Partial<Schedule>) => setDraft({ ...draft, ...values });
  const toggle = (branch: string, on: boolean) => set({ branches: on ? [...draft.branches, branch].sort() : draft.branches.filter((b) => b !== branch) });
  return (
    <section className="card staging-refresh">
      <div className="card-head"><h2>{t.title}</h2></div>
      <div className="card-body stack">
        <p className="small muted">{t.text}</p>
        <Field label={t.frequency}>
          <Segmented<RefreshFrequency> value={draft.frequency} onChange={(frequency) => editable && set({ frequency })}
            options={(["off", "daily", "weekly"] as const).map((v) => ({ value: v, label: t[v], disabled: !editable }))} />
        </Field>
        {draft.frequency !== "off" && (
          <div className="form-grid">
            <Field label={t.time} hint={meta.timezone ? t.timezone(meta.timezone) : undefined}>
              <Input type="time" dir="ltr" value={draft.time} disabled={!editable} onChange={(e) => e.target.value && set({ time: e.target.value })} />
            </Field>
            {draft.frequency === "weekly" && (
              <Field label={t.weekday}>
                <SelectBox value={String(draft.weekday)} disabled={!editable} onValueChange={(v) => set({ weekday: Number(v) })}>
                  {t.weekdays.map((day, i) => <option key={day} value={String(i)}>{day}</option>)}
                </SelectBox>
              </Field>
            )}
          </div>
        )}
        <fieldset className="sr-branches">
          <legend className="small">{t.branches}</legend>
          {meta.branches.length ? (
            <>
              <label className="sr-branch">
                <Checkbox checked={draft.branches.length === 0} disabled={!editable} onCheckedChange={(on) => on === true && set({ branches: [] })} />
                <span>{t.allBranches}</span>
              </label>
              {meta.branches.map((b) => (
                <label key={b.name} className="sr-branch">
                  <Checkbox checked={draft.branches.includes(b.name)} disabled={!editable} onCheckedChange={(on) => toggle(b.name, on === true)} />
                  <bdi dir="ltr">{b.name}</bdi>
                  {b.instances.length > 0 && <span className="faint xsmall" dir="ltr">{b.instances.join(", ")}</span>}
                </label>
              ))}
            </>
          ) : <p className="small muted">{t.noStaging}</p>}
        </fieldset>
        <div className="row-wrap">
          <p className="small muted spacer">
            {saved.frequency !== "off" && saved.next_run && <span className="nowrap"><CalendarClock className="inline-icon" aria-hidden /> {t.nextRun(ago(saved.next_run))}</span>}
            {saved.frequency !== "off" && saved.next_run && saved.last_run && " · "}
            {saved.last_run && <span className="nowrap">{t.lastRun(ago(saved.last_run))}</span>}
          </p>
          {can("instances.restore") && meta.branches.length > 0 && (
            <Button icon={<RefreshCw />} loading={run.isPending}
              onClick={() => run.mutate(undefined, {
                onSuccess: (found) => {
                  const skipped = found.skipped.map((s) => t.skipped(s.instance || "—", s.reason)).join("\n");
                  toast(found.started.length ? "success" : "info", found.started.length ? t.started(found.started.length) : t.nothingStarted, skipped || undefined);
                },
                onError: (e) => toast("error", t.notRun, errorText(e)),
              })}>{t.runNow}</Button>
          )}
          {editable && (
            <Button variant="primary" disabled={!dirty} loading={save.isPending}
              onClick={() => save.mutate(draft, {
                onSuccess: () => { toast("success", t.saved); void client.invalidateQueries({ queryKey: key(project.id) }); },
                onError: (e) => toast("error", t.notSaved, errorText(e)),
              })}>{t.save}</Button>
          )}
        </div>
      </div>
    </section>
  );
}
