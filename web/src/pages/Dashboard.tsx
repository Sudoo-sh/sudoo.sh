import { Boxes, Plus, Search } from "lucide-react";
import { useDeferredValue, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router";
import { useHistory, useInstances, useMetrics } from "@/api/queries";
import type { Columns, Instance, InstanceStatus, Stage } from "@/api/types";
import { Bars } from "@/components/charts/Bars";
import { LiveChart } from "@/components/charts/LiveChart";
import { Ring } from "@/components/charts/Ring";
import { InstanceCard } from "@/components/InstanceCard";
import { STAGE_ORDER, STAGES } from "@/components/StageTag";
import { Page, PageHead, useCan } from "@/components/Shell";
import { StatCard } from "@/components/StatCard";
import { Button, Callout, Empty, Segmented, Skeleton } from "@/components/app/basics";
import { Input } from "@/components/ui/input";
import { SelectBox } from "@/components/app/select";
import { useT } from "@/i18n";
import common from "@/i18n/common";
import dashboard from "@/i18n/dashboard";
import { bytes, duration, ms, number, percent } from "@/lib/format";

const average = (values: (number | null)[] | undefined) => {
  const numbers = (values ?? []).filter((v): v is number => v != null);
  return numbers.length ? numbers.reduce((a, b) => a + b, 0) / numbers.length : null;
};

/** One series over every instance, sample by sample (the samples of all instances share their times). */
function combine(instances: Record<string, Columns> | undefined, key: string, how: "sum" | "max" | "average") {
  const byTime = new Map<number, number[]>();
  for (const columns of Object.values(instances ?? {})) {
    columns.t.forEach((t, i) => {
      const value = columns[key]?.[i];
      if (value == null) return;
      const list = byTime.get(t) ?? [];
      list.push(value);
      byTime.set(t, list);
    });
  }
  const t = [...byTime.keys()].sort((a, b) => a - b);
  const values = t.map((x) => {
    const list = byTime.get(x)!;
    if (how === "max") return Math.max(...list);
    const sum = list.reduce((a, b) => a + b, 0);
    return how === "sum" ? sum : sum / list.length;
  });
  return { t, values };
}

type Measure = "cpu" | "memory" | "disk";
const MEASURES: Record<Measure, { value: (i: Instance) => number | null | undefined; format: (v: number) => string }> =
  {
    cpu: { value: (i) => i.usage.cpu_percent, format: (v) => percent(v, 1) },
    memory: { value: (i) => i.usage.memory_bytes, format: (v) => bytes(v) },
    disk: { value: (i) => i.usage.disk_bytes, format: (v) => bytes(v) },
  };

function Charts({ instances }: { instances: Instance[] | undefined }) {
  const navigate = useNavigate();
  const history = useDeferredValue(useHistory().data); // drawn at low priority, as on the page (below)
  const [measure, setMeasure] = useState<Measure>("cpu");
  const words = useT(dashboard);
  const t = words.charts;
  const s = words.states;
  const traffic = useMemo(() => {
    const requests = combine(history?.instances, "requests_per_minute", "sum");
    const errors = combine(
      Object.fromEntries(
        Object.entries(history?.instances ?? {}).map(([name, c]) => [
          name,
          {
            ...c,
            errors: c.t.map((_, i) => ((c.requests_per_minute?.[i] ?? 0) * (c.error_rate_percent?.[i] ?? 0)) / 100),
          },
        ]),
      ),
      "errors",
      "sum",
    );
    return { t: requests.t, requests: requests.values, errors: errors.values };
  }, [history]);
  const response = useMemo(() => {
    const slowest = combine(history?.instances, "response_ms_p95", "max");
    const usual = combine(history?.instances, "response_ms_avg", "average");
    const at = new Map(usual.t.map((t, i) => [t, usual.values[i]!]));
    return { t: slowest.t, slowest: slowest.values, usual: slowest.t.map((t) => at.get(t) ?? null) };
  }, [history]);
  const states = useMemo(() => {
    const count = (statuses: InstanceStatus[]) => instances?.filter((i) => statuses.includes(i.status)).length ?? 0;
    return [
      { label: s.online, value: count(["online", "starting"]), color: "--positive" },
      { label: s.working, value: count(["busy", "creating"]), color: "--working" },
      { label: s.paused, value: count(["paused"]), color: "--idle" },
      { label: s.off, value: count(["switched_off", "stopped", "unknown"]), color: "--text-3" },
    ];
  }, [instances, s]);
  const chosen = MEASURES[measure];
  const bars = useMemo(
    () =>
      (instances ?? [])
        .map((i) => ({ label: i.name, value: chosen.value(i) ?? 0 }))
        .filter((b) => b.value > 0)
        .sort((a, b) => b.value - a.value)
        .slice(0, 8),
    [instances, chosen],
  );
  const totalRequests = traffic.requests.at(-1);

  return (
    <div className="dash-grid rise">
      <section className="card chart-card span-2">
        <div className="chart-head">
          <div>
            <h2>{t.requests}</h2>
            <p className="sub">
              {t.requestsText}
              {totalRequests != null ? t.now(number(totalRequests)) : ""}
            </p>
          </div>
          <span className="spacer" />
          <div className="legend">
            <span>
              <i style={{ background: "var(--brand)" }} /> {t.requests}
            </span>
            <span>
              <i style={{ background: "var(--negative)" }} /> {t.errors}
            </span>
          </div>
        </div>
        <div className="chart-body">
          <LiveChart
            t={traffic.t}
            height={220}
            format={(v) => number(v, v >= 10 ? 0 : 1)}
            series={[
              { label: t.requests, values: traffic.requests, color: "--brand", fill: true },
              { label: t.errors, values: traffic.errors, color: "--negative" },
            ]}
          />
        </div>
      </section>
      <section className="card chart-card">
        <div className="chart-head">
          <div>
            <h2>{t.instances}</h2>
            <p className="sub">{t.byState}</p>
          </div>
        </div>
        <div className="chart-body">
          <Ring
            parts={states}
            total={instances?.length ?? 0}
            caption={instances?.length === 1 ? t.instanceWord : t.instancesWord}
          />
        </div>
      </section>
      <section className="card chart-card span-2">
        <div className="chart-head">
          <div>
            <h2>{t.response}</h2>
            <p className="sub">{t.responseText}</p>
          </div>
          <span className="spacer" />
          <div className="legend">
            <span>
              <i style={{ background: "var(--brand)" }} /> {t.slowest}
            </span>
            <span>
              <i style={{ background: "var(--series-2)" }} /> {t.average}
            </span>
          </div>
        </div>
        <div className="chart-body">
          <LiveChart
            t={response.t}
            height={220}
            format={(v) => ms(v)}
            series={[
              { label: t.percentile, values: response.slowest, color: "--brand", fill: true },
              { label: t.average, values: response.usual, color: "--series-2" },
            ]}
          />
        </div>
      </section>
      <section className="card chart-card">
        <div className="chart-head">
          <div>
            <h2>{t.byInstance}</h2>
            <p className="sub">{t.measureNow(words.measures[measure])}</p>
          </div>
          <span className="spacer" />
          <Segmented<Measure>
            value={measure}
            onChange={setMeasure}
            options={[
              { value: "cpu", label: t.cpu },
              { value: "memory", label: words.measures.memory },
              { value: "disk", label: words.measures.disk },
            ]}
          />
        </div>
        <div className="chart-body">
          {bars.length ? (
            <Bars
              bars={bars}
              format={chosen.format}
              onPick={(name) => navigate(`/i/${name}`, { viewTransition: true })}
            />
          ) : (
            <p className="faint small" style={{ padding: "24px 4px" }}>
              {instances?.length ? t.nothingMeasured : t.noInstances}
            </p>
          )}
        </div>
      </section>
    </div>
  );
}

type Filter = "all" | "online" | "paused" | "busy";
const ASLEEP: InstanceStatus[] = ["paused", "switched_off", "stopped"];

export function DashboardPage() {
  const can = useCan();
  // The figures arrive several times a minute (and the instances on every change): the page draws each new set at
  // low priority, in slices the browser can interrupt, so typing in the search box or a click never waits for it.
  const { data: liveInstances, isPending } = useInstances();
  const instances = useDeferredValue(liveInstances);
  const metrics = useDeferredValue(useMetrics().data);
  const history = useDeferredValue(useHistory().data);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [stage, setStage] = useState<Stage | "all">("all");
  const server = metrics?.server;
  const words = useT(dashboard);
  const st = words.stats;
  const tb = words.toolbar;
  const newInstance = useT(common).nav.newInstance;

  const counts = useMemo(() => {
    const count = (statuses: InstanceStatus[]) => instances?.filter((i) => statuses.includes(i.status)).length ?? 0;
    return { online: count(["online", "starting"]), paused: count(ASLEEP), busy: count(["busy", "creating"]) };
  }, [instances]);
  const stageCounts = useMemo(
    () =>
      Object.fromEntries(
        STAGES.map((st) => [st.value, instances?.filter((i) => i.stage === st.value).length ?? 0]),
      ) as Record<Stage, number>,
    [instances],
  );
  const shown = (instances ?? [])
    .filter((i) => {
      if (stage !== "all" && i.stage !== stage) return false;
      const text = `${i.name} ${i.git.branch ?? "plain"} ${i.git.repository ?? ""} ${i.odoo.version}`.toLowerCase();
      if (query && !text.includes(query.toLowerCase())) return false;
      if (filter === "online") return i.status === "online" || i.status === "starting";
      if (filter === "paused") return ASLEEP.includes(i.status);
      if (filter === "busy") return i.status === "busy" || i.status === "creating";
      return true;
    })
    .sort((a, b) => (STAGE_ORDER[a.stage] ?? 9) - (STAGE_ORDER[b.stage] ?? 9)); // production first; by name within a stage
  const cpuAverage = average(history?.server.cpu_percent);
  const serverT = history?.server.t ?? [];
  const capacity = metrics?.capacity ?? null;
  const panelMemory = server?.panel_memory_bytes;
  const panelGb = panelMemory != null && panelMemory >= 1024 ** 3;
  const freeDisk = server ? bytes(server.disk_total_bytes! - server.disk_used_bytes!) : null;

  return (
    <Page>
      <PageHead
        title={words.title}
        actions={
          can("instances.create") && (
            <Button asChild variant="primary">
              <Link to="/new" viewTransition>
                <Plus /> {newInstance}
              </Link>
            </Button>
          )
        }
      >
        {words.text}
      </PageHead>

      {capacity?.overloaded && capacity.alert && (
        <Callout tone="warning">
          {capacity.alert}{" "}
          <Link to="/help/docs/ADMIN_GUIDE.md#how-many-instances-can-my-server-run" viewTransition>
            {words.howMany}
          </Link>
        </Callout>
      )}

      <div className="grid-5 stagger">
        <StatCard
          label={st.processor}
          value={server?.cpu_percent}
          unit="%"
          trend={{ t: serverT, values: history?.server.cpu_percent ?? [], max: 100 }}
          detail={
            cpuAverage != null
              ? st.cpuDetail(percent(cpuAverage), server?.cores != null ? number(server.cores) : "…")
              : st.cores(server?.cores != null ? number(server.cores) : "…")
          }
        />
        <StatCard
          label={st.memory}
          value={server?.memory_percent}
          unit="%"
          trend={{ t: serverT, values: history?.server.memory_percent ?? [], max: 100 }}
          detail={server ? st.of(bytes(server.memory_used_bytes), bytes(server.memory_total_bytes)) : st.measuring}
        />
        <StatCard
          label={st.disk}
          value={server?.disk_percent}
          unit="%"
          trend={{ t: serverT, values: history?.server.disk_percent ?? [], max: 100 }}
          detail={
            freeDisk
              ? capacity?.disk.more_instances != null
                ? st.freeRoom(freeDisk, number(capacity.disk.more_instances))
                : st.free(freeDisk)
              : st.measuring
          }
        />
        <StatCard
          label={st.panel}
          value={
            panelMemory == null ? undefined : panelGb ? panelMemory / 1024 ** 3 : Math.round(panelMemory / 1024 ** 2)
          }
          decimals={panelGb ? 1 : 0}
          unit={panelGb ? " GB" : " MB"}
          trend={{ t: serverT, values: history?.server.panel_memory_bytes ?? [] }}
          detail={
            server?.panel_memory_peak_bytes != null
              ? st.panelDetail(bytes(server.panel_memory_peak_bytes), percent(server.panel_cpu_percent ?? 0, 1))
              : st.measuring
          }
        />
        <StatCard
          label={st.running}
          value={capacity?.running ?? server?.instances_running ?? (instances ? counts.online : undefined)}
          unit={
            capacity
              ? st.ofAbout(number(capacity.comfortable))
              : instances
                ? st.ofAll(number(instances.length))
                : undefined
          }
          trend={{
            t: serverT,
            values: history?.server.instances_running ?? [],
            max: Math.max(1, capacity?.comfortable ?? 0, instances?.length ?? 1),
            color: capacity && capacity.headroom < 0 ? "--caution" : undefined,
          }}
          detail={
            capacity
              ? capacity.headroom > 0
                ? st.room(number(capacity.headroom), number(instances?.length ?? 0))
                : capacity.headroom === 0
                  ? st.asMany(number(instances?.length ?? 0))
                  : st.more(number(instances?.length ?? 0))
              : server?.uptime_seconds
                ? st.serverUp(duration(server.uptime_seconds))
                : st.online
          }
        />
      </div>

      <Charts instances={instances} />

      <div className="toolbar">
        <div className="input-group">
          <Search />
          <Input
            placeholder={tb.search}
            aria-label={tb.searchLabel}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <Segmented<Filter>
          value={filter}
          onChange={setFilter}
          options={[
            {
              value: "all",
              label: (
                <>
                  {tb.all} <span className="count">{instances?.length ?? 0}</span>
                </>
              ),
            },
            {
              value: "online",
              label: (
                <>
                  {tb.online} <span className="count">{counts.online}</span>
                </>
              ),
            },
            {
              value: "paused",
              label: (
                <>
                  {tb.paused} <span className="count">{counts.paused}</span>
                </>
              ),
            },
            {
              value: "busy",
              label: (
                <>
                  {tb.working} <span className="count">{counts.busy}</span>
                </>
              ),
            },
          ]}
        />
        <SelectBox
          className="stage-filter"
          aria-label={tb.stage}
          value={stage}
          onValueChange={(value) => setStage(value as Stage | "all")}
        >
          <option value="all">{tb.allStages}</option>
          {STAGES.map((st) => (
            <option key={st.value} value={st.value}>
              {st.label} ({stageCounts[st.value]})
            </option>
          ))}
        </SelectBox>
      </div>

      {isPending ? (
        <div className="instance-grid">
          {[0, 1, 2].map((i) => (
            <div key={i} className="card pad stack">
              <Skeleton height={36} width="60%" />
              <Skeleton height={16} />
              <Skeleton height={48} />
            </div>
          ))}
        </div>
      ) : shown.length ? (
        <div className="instance-grid">
          {shown.map((instance) => (
            <InstanceCard key={instance.name} instance={instance} />
          ))}
        </div>
      ) : (
        <div className="card">
          <Empty
            icon={<Boxes />}
            title={instances?.length ? words.empty.nothing : words.empty.none}
            action={
              !instances?.length && can("instances.create") ? (
                <Button asChild variant="primary">
                  <Link to="/new" viewTransition>
                    <Plus /> {newInstance}
                  </Link>
                </Button>
              ) : undefined
            }
          >
            {instances?.length ? words.empty.tryAnother : words.empty.create}
          </Empty>
        </div>
      )}
    </Page>
  );
}
