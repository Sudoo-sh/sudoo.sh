// An instance's Tools tab, as on Odoo.sh: the code profiler. Choose how long to record (1 minute up to
// PROFILER_MAX_MINUTES), start it (confirmed on production, every time), watch the countdown, stop it early, then
// read the flame graph of any of the last recordings, download it (a speedscope file) or delete it. Odoo's own
// profiler, for one request in detail, is pointed to from here.

import { Download, Flame, FlameKindling, Play, Square, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api, errorText } from "@/api/client";
import { useCall, useProfile, useProfiler } from "@/api/queries";
import type { Instance, ProfileRecording, ProfilerRun, ProfileTree } from "@/api/types";
import { Button, Callout, Empty, Field, Progress, Skeleton, Tag } from "@/components/app/basics";
import { SelectBox } from "@/components/app/select";
import { Modal, useToast } from "@/components/app/overlay";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useT } from "@/i18n";
import common from "@/i18n/common";
import words from "@/i18n/profiler";
import { ago, bytes, dateTime } from "@/lib/format";
import { FlameGraph } from "./FlameGraph";

/** Odoo's documentation of its own profiler, for the instance's version. */
const odooDocs = (version: string) =>
  `https://www.odoo.com/documentation/${/^\d+\.\d$/.test(version) ? version : "18.0"}/developer/reference/backend/performance.html`;

const clock = (seconds: number) => {
  const s = Math.max(0, Math.round(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};

export function ToolsTab({ instance }: { instance: Instance }) {
  const t = useT(words);
  const c = useT(common).common;
  const toast = useToast();
  const name = instance.name;
  const { data: state, error, refetch } = useProfiler(name);
  const [minutes, setMinutes] = useState(1);
  const [asking, setAsking] = useState(false);
  const [shown, setShown] = useState<string | null>(null);
  const [removing, setRemoving] = useState<ProfileRecording | null>(null);
  const running = useRef<string | null>(null);

  const start = useCall(
    (confirmed: boolean) => api.post<ProfilerRun>(`/instances/${name}/profiler`, { minutes, confirm_production: confirmed }),
    [["profiler", name]],
  );
  const stop = useCall(() => api.post<ProfilerRun>(`/instances/${name}/profiler/stop`), [["profiler", name]]);
  const remove = useCall((id: string) => api.del(`/instances/${name}/profiles/${id}`), [["profiler", name]]);

  // a recording that just finished opens by itself
  const current = state?.current ?? null;
  const newest = state?.recordings[0]?.id ?? null;
  useEffect(() => {
    if (current) running.current = current.id;
    else if (running.current && newest === running.current) {
      setShown(newest);
      running.current = null;
    }
  }, [current, newest]);
  useEffect(() => {
    if (shown && state && !state.recordings.some((r) => r.id === shown)) setShown(null);
  }, [shown, state]);

  if (error) return <Callout tone="danger">{errorText(error)}</Callout>;
  if (!state) return <div className="card card-body"><Skeleton height={180} /></div>;

  const max = Math.max(1, state.max_minutes);
  const chosen = Math.min(minutes, max);
  const blocked = !state.enabled ? t.disabled : state.production && !state.production_allowed ? t.productionOff : !state.running ? t.notRunning : null;
  const begin = (confirmed: boolean) =>
    start.mutate(confirmed, {
      onSuccess: () => toast("success", t.started),
      onError: (e) => toast("error", t.notStarted, errorText(e)),
    });

  return (
    <div className="tools-tab">
      <section className="card">
        <div className="card-head">
          <h2 className="spacer">{t.title}</h2>
          {state.production && <Tag tone="caution">{t.production}</Tag>}
        </div>
        <div className="card-body stack">
          <p>{t.text}</p>
          <p className="small muted">{t.how}</p>
          <Callout>
            {t.odooProfiler(<a key="docs" href={odooDocs(instance.odoo.version)} target="_blank" rel="noreferrer">{t.odooProfilerLink}</a>)}
          </Callout>
          {state.last_error && !current && (
            <Callout tone="danger"><b>{t.failed}.</b> {state.last_error.message}</Callout>
          )}
          {current ? (
            <RunProgress run={current} onStop={() => stop.mutate(undefined, { onError: (e) => toast("error", c.error, errorText(e)) })} stopping={stop.isPending || current.stopping} />
          ) : blocked ? (
            <Callout tone="warning">{blocked}</Callout>
          ) : (
            <div className="profiler-start">
              <Field label={t.duration}>
                <SelectBox value={String(chosen)} onValueChange={(v) => setMinutes(Number(v))}>
                  {Array.from({ length: max }, (_, i) => i + 1).map((n) => <option key={n} value={String(n)}>{t.minutes(n)}</option>)}
                </SelectBox>
              </Field>
              <Button variant="primary" icon={<Play />} loading={start.isPending} onClick={() => (state.production ? setAsking(true) : begin(false))}>
                {t.start}
              </Button>
            </div>
          )}
        </div>
      </section>

      <section className="card">
        <div className="card-head">
          <h2 className="spacer">{t.recordings}</h2>
          <span className="small muted">{t.recordingsText(state.keep)}</span>
        </div>
        {!state.recordings.length ? (
          <Empty icon={<FlameKindling />} title={t.none}>{t.noneText}</Empty>
        ) : (
          <Table className="profiler-recordings">
            <TableHeader>
              <TableRow>
                <TableHead>{t.when}</TableHead>
                <TableHead>{t.length}</TableHead>
                <TableHead>{t.samples}</TableHead>
                <TableHead>{t.by}</TableHead>
                <TableHead><span className="sr-only">{t.open}</span></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {state.recordings.map((r) => (
                <TableRow key={r.id} data-state={shown === r.id ? "selected" : undefined}>
                  <TableCell className="nowrap" title={dateTime(Date.parse(r.started_at) / 1000)}>
                    {ago(Date.parse(r.started_at) / 1000)}
                    {r.production && <> <Tag tone="caution">{t.production}</Tag></>}
                  </TableCell>
                  <TableCell className="nowrap">
                    {t.seconds(r.seconds)}
                    {r.stopped_early && <span className="xsmall faint"> · {t.stoppedEarly}</span>}
                  </TableCell>
                  <TableCell className="nowrap small">{t.sampleCount(r.samples)}</TableCell>
                  <TableCell className="small"><bdi>{r.user}</bdi></TableCell>
                  <TableCell>
                    <div className="row" style={{ justifyContent: "flex-end" }}>
                      <Button size="sm" variant={shown === r.id ? "soft" : "default"} icon={<Flame />} aria-label={shown === r.id ? t.opened : t.open} aria-pressed={shown === r.id} onClick={() => setShown(shown === r.id ? null : r.id)}>
                        <span className="open-label">{shown === r.id ? t.opened : t.open}</span>
                      </Button>
                      <Button size="sm" variant="ghost" icon={<Download />} asChild title={t.downloadHint}>
                        <a href={r.links.download} download aria-label={`${t.download} (${bytes(r.size)})`} />
                      </Button>
                      <Button size="sm" variant="ghost" icon={<Trash2 />} aria-label={t.remove} onClick={() => setRemoving(r)} />
                    </div>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </section>

      {shown && <Recording name={name} id={shown} />}

      {asking && (
        <Modal
          title={t.confirmTitle}
          description={t.confirmText(<b key="n"><bdi>{name}</bdi></b>)}
          onClose={() => setAsking(false)}
          footer={
            <>
              <Button onClick={() => setAsking(false)}>{c.cancel}</Button>
              <Button variant="primary" icon={<Play />} onClick={() => { begin(true); setAsking(false); }}>{t.confirm}</Button>
            </>
          }
        />
      )}
      {removing && (
        <Modal
          title={t.removeTitle}
          description={t.removeText}
          onClose={() => setRemoving(null)}
          footer={
            <>
              <Button onClick={() => setRemoving(null)}>{c.cancel}</Button>
              <Button variant="danger" icon={<Trash2 />} loading={remove.isPending} onClick={() => remove.mutate(removing.id, {
                onSuccess: () => { toast("success", t.removed); setRemoving(null); void refetch(); },
                onError: (e) => toast("error", c.error, errorText(e)),
              })}>{t.remove}</Button>
            </>
          }
        />
      )}
    </div>
  );
}

/** The recording being made: its phase, a countdown and a bar while it records, and Stop. */
function RunProgress({ run, onStop, stopping }: { run: ProfilerRun; onStop: () => void; stopping: boolean }) {
  const t = useT(words);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 500);
    return () => window.clearInterval(timer);
  }, []);
  const total = run.minutes * 60;
  const since = run.recording_since ? Date.parse(run.recording_since) : null;
  const left = since ? total - (now - since) / 1000 : total;
  const share = since ? Math.min(100, Math.max(0, (100 * (total - left)) / total)) : null;
  return (
    <div className="profiler-run" aria-live="polite">
      <div className="row-wrap">
        <span>
          <b>{t.phases[run.phase]}</b>
          {run.phase === "recording" && <span className="profiler-countdown muted"> · {t.left(clock(left))}</span>}
        </span>
        <Button variant="danger" size="sm" icon={<Square />} loading={stopping} disabled={run.phase === "saving"} onClick={onStop}>
          {stopping ? t.stopping : t.stop}
        </Button>
      </div>
      <Progress value={run.phase === "saving" ? null : share} status={run.phase === "saving" ? undefined : "running"} />
      <span className="xsmall faint">
        {t.startedBy(<bdi key="u">{run.user}</bdi>)}
        {run.phase === "preparing" && <> · {t.preparingHint}</>}
      </span>
    </div>
  );
}

/** One recording: its flame graph and the functions that took the most time. */
function Recording({ name, id }: { name: string; id: string }) {
  const t = useT(words);
  const { data, error } = useProfile(name, id);
  if (error) return <Callout tone="danger">{errorText(error)}</Callout>;
  if (!data) return <div className="card card-body" aria-busy="true"><Skeleton height={320} /></div>;
  const label = t.graphOf(ago(Date.parse(data.started_at) / 1000));
  return (
    <section className="card">
      <div className="card-head">
        <h2 className="spacer">{t.graphTitle}</h2>
        <span className="small muted">{label} · {t.seconds(data.seconds)} · {t.sampleCount(data.samples)}</span>
      </div>
      <div className="card-body stack">
        {data.tree.total ? <FlameGraph tree={data.tree} label={label} /> : <Callout>{t.empty}</Callout>}
      </div>
      {data.tree.top.length > 0 && <HotFunctions tree={data.tree} />}
    </section>
  );
}

function HotFunctions({ tree }: { tree: ProfileTree }) {
  const t = useT(words);
  const top = tree.top.slice(0, 15);
  const most = top[0]?.self || 1;
  const share = (n: number) => `${((100 * n) / tree.total).toFixed(1)}%`;
  return (
    <>
      <div className="card-head"><h3 className="spacer">{t.hot}</h3><span className="small muted">{t.hotText}</span></div>
      <Table className="flame-hot">
        <TableHeader>
          <TableRow>
            <TableHead>{t.function}</TableHead>
            <TableHead className="nowrap">{t.own}</TableHead>
            <TableHead className="nowrap">{t.total}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {top.map((f) => (
            <TableRow key={`${f.name}@${f.where}`}>
              <TableCell>
                <div className="mono small" dir="ltr">{f.name}</div>
                {f.where && <div className="mono xsmall faint" dir="ltr">{f.where}</div>}
                <div className="bar" style={{ width: `${(100 * f.self) / most}%` }} />
              </TableCell>
              <TableCell className="nowrap small">{share(f.self)}</TableCell>
              <TableCell className="nowrap small">{share(f.total)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </>
  );
}
