import { Boxes, Plus, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router";
import { useHistory, useInstances, useMetrics } from "@/api/queries";
import type { Columns, Instance, InstanceStatus } from "@/api/types";
import { Bars } from "@/components/charts/Bars";
import { LiveChart } from "@/components/charts/LiveChart";
import { Ring } from "@/components/charts/Ring";
import { InstanceCard } from "@/components/InstanceCard";
import { Page, PageHead, useCan } from "@/components/Shell";
import { StatCard } from "@/components/StatCard";
import { Empty, Segmented, Skeleton } from "@/components/ui/basics";
import { bytes, duration } from "@/lib/format";

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
const MEASURES: Record<Measure, { label: string; value: (i: Instance) => number | null | undefined; format: (v: number) => string }> = {
  cpu: { label: "Processor", value: (i) => i.usage.cpu_percent, format: (v) => `${v.toFixed(1)}%` },
  memory: { label: "Memory", value: (i) => i.usage.memory_bytes, format: (v) => bytes(v) },
  disk: { label: "Disk", value: (i) => i.usage.disk_bytes, format: (v) => bytes(v) },
};

function Charts({ instances }: { instances: Instance[] | undefined }) {
  const navigate = useNavigate();
  const { data: history } = useHistory();
  const [measure, setMeasure] = useState<Measure>("cpu");
  const traffic = useMemo(() => {
    const requests = combine(history?.instances, "requests_per_minute", "sum");
    const errors = combine(
      Object.fromEntries(
        Object.entries(history?.instances ?? {}).map(([name, c]) => [
          name,
          { ...c, errors: c.t.map((_, i) => ((c.requests_per_minute?.[i] ?? 0) * (c.error_rate_percent?.[i] ?? 0)) / 100) },
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
      { label: "Online", value: count(["online", "starting"]), color: "--positive" },
      { label: "Working", value: count(["busy"]), color: "--working" },
      { label: "Paused", value: count(["paused"]), color: "--idle" },
      { label: "Switched off", value: count(["switched_off", "stopped", "unknown"]), color: "--text-3" },
    ];
  }, [instances]);
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
            <h2>Requests</h2>
            <p className="sub">Per minute, all instances together{totalRequests != null ? ` · ${Math.round(totalRequests)} now` : ""}</p>
          </div>
          <span className="spacer" />
          <div className="legend">
            <span><i style={{ background: "var(--accent)" }} /> Requests</span>
            <span><i style={{ background: "var(--negative)" }} /> Errors</span>
          </div>
        </div>
        <div className="chart-body">
          <LiveChart t={traffic.t} height={220} format={(v) => (v >= 10 ? Math.round(v).toString() : v.toFixed(1))}
            series={[
              { label: "Requests", values: traffic.requests, color: "--accent", fill: true },
              { label: "Errors", values: traffic.errors, color: "--negative" },
            ]} />
        </div>
      </section>
      <section className="card chart-card">
        <div className="chart-head"><div><h2>Instances</h2><p className="sub">By state, now</p></div></div>
        <div className="chart-body"><Ring parts={states} total={instances?.length ?? 0} caption={instances?.length === 1 ? "instance" : "instances"} /></div>
      </section>
      <section className="card chart-card span-2">
        <div className="chart-head">
          <div>
            <h2>Response time</h2>
            <p className="sub">Until Odoo starts answering</p>
          </div>
          <span className="spacer" />
          <div className="legend">
            <span><i style={{ background: "var(--accent)" }} /> 95% faster than, slowest instance</span>
            <span><i style={{ background: "var(--series-2)" }} /> Average</span>
          </div>
        </div>
        <div className="chart-body">
          <LiveChart t={response.t} height={220} format={(v) => `${Math.round(v)} ms`}
            series={[
              { label: "95th percentile", values: response.slowest, color: "--accent", fill: true },
              { label: "Average", values: response.usual, color: "--series-2" },
            ]} />
        </div>
      </section>
      <section className="card chart-card">
        <div className="chart-head">
          <div><h2>By instance</h2><p className="sub">{chosen.label}, now</p></div>
          <span className="spacer" />
          <Segmented<Measure> value={measure} onChange={setMeasure} options={[
            { value: "cpu", label: "CPU" },
            { value: "memory", label: "Memory" },
            { value: "disk", label: "Disk" },
          ]} />
        </div>
        <div className="chart-body">
          {bars.length ? (
            <Bars bars={bars} format={chosen.format} onPick={(name) => navigate(`/i/${name}`, { viewTransition: true })} />
          ) : (
            <p className="faint small" style={{ padding: "24px 4px" }}>{instances?.length ? "Nothing measured yet." : "No instances yet."}</p>
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
  const { data: instances, isPending } = useInstances();
  const { data: metrics } = useMetrics();
  const { data: history } = useHistory();
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const server = metrics?.server;

  const counts = useMemo(() => {
    const count = (statuses: InstanceStatus[]) => instances?.filter((i) => statuses.includes(i.status)).length ?? 0;
    return { online: count(["online", "starting"]), paused: count(ASLEEP), busy: count(["busy"]) };
  }, [instances]);
  const shown = (instances ?? []).filter((i) => {
    const text = `${i.name} ${i.git.branch} ${i.git.repository ?? ""} ${i.odoo.version}`.toLowerCase();
    if (query && !text.includes(query.toLowerCase())) return false;
    if (filter === "online") return i.status === "online" || i.status === "starting";
    if (filter === "paused") return ASLEEP.includes(i.status);
    if (filter === "busy") return i.status === "busy";
    return true;
  });
  const cpuAverage = average(history?.server.cpu_percent);
  const serverT = history?.server.t ?? [];

  return (
    <Page>
      <PageHead
        title="Instances"
        actions={
          can("instances.create") && (
            <Link className="btn primary" to="/new" viewTransition>
              <Plus /> New instance
            </Link>
          )
        }
      >
        Every branch you run, with the server they share. Paused instances start again when someone opens them.
      </PageHead>

      <div className="grid-4 stagger">
        <StatCard label="Processor" value={server?.cpu_percent} unit="%"
          trend={{ t: serverT, values: history?.server.cpu_percent ?? [], max: 100 }}
          detail={cpuAverage != null ? `${cpuAverage.toFixed(0)}% average, last hour · ${server?.cores ?? "…"} cores` : `${server?.cores ?? "…"} cores`} />
        <StatCard label="Memory" value={server?.memory_percent} unit="%"
          trend={{ t: serverT, values: history?.server.memory_percent ?? [], max: 100 }}
          detail={server ? `${bytes(server.memory_used_bytes)} of ${bytes(server.memory_total_bytes)}` : "Measuring"} />
        <StatCard label="Disk" value={server?.disk_percent} unit="%"
          trend={{ t: serverT, values: history?.server.disk_percent ?? [], max: 100 }}
          detail={server ? `${bytes(server.disk_total_bytes! - server.disk_used_bytes!)} free` : "Measuring"} />
        <StatCard label="Running" value={server?.instances_running ?? (instances ? counts.online : undefined)}
          unit={instances ? ` of ${instances.length}` : undefined}
          trend={{ t: serverT, values: history?.server.instances_running ?? [], max: Math.max(1, instances?.length ?? 1) }}
          detail={server?.uptime_seconds ? `Server up ${duration(server.uptime_seconds)}` : "Instances online"} />
      </div>

      <Charts instances={instances} />

      <div className="toolbar">
        <div className="input-group">
          <Search />
          <input className="input" placeholder="Search" aria-label="Search instances" value={query} onChange={(e) => setQuery(e.target.value)} />
        </div>
        <Segmented<Filter>
          value={filter}
          onChange={setFilter}
          options={[
            { value: "all", label: <>All <span className="count">{instances?.length ?? 0}</span></> },
            { value: "online", label: <>Online <span className="count">{counts.online}</span></> },
            { value: "paused", label: <>Paused <span className="count">{counts.paused}</span></> },
            { value: "busy", label: <>Working <span className="count">{counts.busy}</span></> },
          ]}
        />
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
            title={instances?.length ? "Nothing matches" : "No instances yet"}
            action={
              !instances?.length && can("instances.create") ? (
                <Link className="btn primary" to="/new" viewTransition>
                  <Plus /> New instance
                </Link>
              ) : undefined
            }
          >
            {instances?.length ? "Try another search or filter." : "Create one from any GitHub branch. It runs in a few minutes."}
          </Empty>
        </div>
      )}
    </Page>
  );
}
