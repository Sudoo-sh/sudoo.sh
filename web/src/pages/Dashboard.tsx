import { Boxes, Plus, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router";
import { useHistory, useInstances, useMetrics } from "@/api/queries";
import type { InstanceStatus } from "@/api/types";
import { LiveChart } from "@/components/charts/LiveChart";
import { InstanceCard } from "@/components/InstanceCard";
import { Page, PageHead, useCan } from "@/components/Shell";
import { AnimatedNumber, Empty, Segmented, Skeleton } from "@/components/ui/basics";
import { bytes, duration } from "@/lib/format";

const average = (values: (number | null)[] | undefined) => {
  const numbers = (values ?? []).filter((v): v is number => v != null);
  return numbers.length ? numbers.reduce((a, b) => a + b, 0) / numbers.length : null;
};

function Stat({ label, value, unit, detail }: { label: string; value: number | undefined; unit?: string; detail: string }) {
  return (
    <div className="card stat">
      <span className="label">{label}</span>
      <span className="figure">
        {value == null ? <Skeleton height={32} width={72} /> : <AnimatedNumber value={value} />}
        {value != null && unit && <small>{unit}</small>}
      </span>
      <span className="detail">{detail}</span>
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
  const busiest = useMemo(
    () =>
      (instances ?? [])
        .map((i) => ({ name: i.name, cpu: i.usage.cpu_percent ?? 0, memory: i.usage.memory_percent ?? 0 }))
        .sort((a, b) => b.cpu - a.cpu)
        .slice(0, 6),
    [instances],
  );
  const shown = (instances ?? []).filter((i) => {
    const text = `${i.name} ${i.git.branch} ${i.git.repository ?? ""} ${i.odoo.version}`.toLowerCase();
    if (query && !text.includes(query.toLowerCase())) return false;
    if (filter === "online") return i.status === "online" || i.status === "starting";
    if (filter === "paused") return ASLEEP.includes(i.status);
    if (filter === "busy") return i.status === "busy";
    return true;
  });
  const cpuAverage = average(history?.server.cpu_percent);
  const topCpu = Math.max(1, ...busiest.map((b) => b.cpu));

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
        <Stat label="Processor" value={server?.cpu_percent} unit="%"
          detail={cpuAverage != null ? `${cpuAverage.toFixed(0)}% average, last hour` : `${server?.cores ?? "…"} cores`} />
        <Stat label="Memory" value={server?.memory_percent} unit="%"
          detail={server ? `${bytes(server.memory_used_bytes)} of ${bytes(server.memory_total_bytes)}` : "Measuring"} />
        <Stat label="Disk" value={server?.disk_percent} unit="%"
          detail={server ? `${bytes(server.disk_total_bytes! - server.disk_used_bytes!)} free` : "Measuring"} />
        <Stat label="Running" value={server?.instances_running ?? (instances ? counts.online : undefined)}
          unit={instances ? ` of ${instances.length}` : undefined}
          detail={server?.uptime_seconds ? `Server up ${duration(server.uptime_seconds)}` : "Instances online"} />
      </div>

      <div className="overview rise">
        <section className="card chart-card">
          <div className="chart-head">
            <h2>Server, last hour</h2>
            <span className="spacer" />
            <div className="legend">
              <span><i style={{ background: "var(--accent)" }} /> Processor</span>
              <span><i style={{ background: "var(--series-2)" }} /> Memory</span>
            </div>
          </div>
          <div className="chart-body">
            <LiveChart
              t={history?.server.t ?? []}
              max={100}
              format={(v) => `${Math.round(v)}%`}
              series={[
                { label: "Processor", values: history?.server.cpu_percent ?? [], color: "--accent", fill: true },
                { label: "Memory", values: history?.server.memory_percent ?? [], color: "--series-2" },
              ]}
            />
          </div>
        </section>
        <section className="card">
          <div className="card-head">
            <h2>Busiest now</h2>
            <span className="spacer" />
            <span className="sub">processor</span>
          </div>
          {busiest.length ? (
            <ol className="ranked">
              {busiest.map((b, i) => (
                <li key={b.name}>
                  <Link to={`/i/${b.name}`} viewTransition className="name truncate">{b.name}</Link>
                  <span className="value">{b.cpu.toFixed(1)}%</span>
                  <div className={`track ${i === 0 && b.cpu > 0 ? "lead" : ""}`}>
                    <span style={{ width: `${(b.cpu / topCpu) * 100}%` }} />
                  </div>
                </li>
              ))}
            </ol>
          ) : (
            <p className="card-body faint small">No instances yet.</p>
          )}
        </section>
      </div>

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
