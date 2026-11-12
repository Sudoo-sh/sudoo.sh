import { Boxes, Cpu, HardDrive, MemoryStick, Plus, Search, Server } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router";
import { useHistory, useInstances, useMetrics } from "@/api/queries";
import type { InstanceStatus } from "@/api/types";
import { Ring, Sparkline } from "@/components/charts/charts";
import { InstanceCard } from "@/components/InstanceCard";
import { Page, PageHead, useIsAdmin } from "@/components/Shell";
import { AnimatedNumber, Empty, Segmented, Skeleton } from "@/components/ui/basics";
import { bytes, duration } from "@/lib/format";

function Kpi({ label, icon, tone, value, unit, detail, ring, spark }: {
  label: string;
  icon: React.ReactNode;
  tone: string;
  value: number | undefined;
  unit?: string;
  detail: React.ReactNode;
  ring?: number;
  spark?: (number | null)[];
}) {
  return (
    <div className="card kpi" style={{ ["--tone" as string]: tone }}>
      <div className="top">
        <span className="icon">{icon}</span>
        <span className="label">{label}</span>
        <span className="spacer" />
        {ring != null && <Ring value={ring} size={46} tone={tone} label="" />}
      </div>
      <div className="value">
        {value == null ? <Skeleton height={34} width={90} /> : <AnimatedNumber value={value} format={(n) => (unit === "%" ? n.toFixed(0) : n.toFixed(0))} />}
        {value != null && unit && <small>{unit}</small>}
      </div>
      <div className="detail">{detail}</div>
      {spark && spark.length > 2 && (
        <div className="spark">
          <Sparkline values={spark} max={100} color={tone} />
        </div>
      )}
    </div>
  );
}

type Filter = "all" | "online" | "paused" | "busy";

export function DashboardPage() {
  const admin = useIsAdmin();
  const { data: instances, isPending } = useInstances();
  const { data: metrics } = useMetrics();
  const { data: history } = useHistory();
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const server = metrics?.server;
  const counts = useMemo(() => {
    const by = (statuses: InstanceStatus[]) => instances?.filter((i) => statuses.includes(i.status)).length ?? 0;
    return { online: by(["online", "starting"]), paused: by(["paused", "switched_off", "stopped"]), busy: by(["busy"]) };
  }, [instances]);
  const shown = (instances ?? []).filter((i) => {
    const text = `${i.name} ${i.git.branch} ${i.git.repository ?? ""} ${i.odoo.version}`.toLowerCase();
    if (query && !text.includes(query.toLowerCase())) return false;
    if (filter === "online") return i.status === "online" || i.status === "starting";
    if (filter === "paused") return ["paused", "switched_off", "stopped"].includes(i.status);
    if (filter === "busy") return i.status === "busy";
    return true;
  });

  return (
    <Page>
      <PageHead
        eyebrow="Overview"
        title="Instances"
        actions={
          admin && (
            <Link className="btn primary" to="/new" viewTransition>
              <Plus /> New instance
            </Link>
          )
        }
      >
        Every branch you run, live. Paused instances start again by themselves when someone opens them.
      </PageHead>

      <div className="grid-4 stagger">
        <Kpi
          label="Processor"
          icon={<Cpu />}
          tone="var(--primary)"
          value={server?.cpu_percent}
          unit="%"
          ring={server?.cpu_percent}
          detail={server ? `${server.cores} cores · load ${server.load_1m?.toFixed(2)}` : "Measuring…"}
          spark={history?.server.cpu_percent}
        />
        <Kpi
          label="Memory"
          icon={<MemoryStick />}
          tone="var(--accent)"
          value={server?.memory_percent}
          unit="%"
          ring={server?.memory_percent}
          detail={server ? `${bytes(server.memory_used_bytes)} of ${bytes(server.memory_total_bytes)}` : "Measuring…"}
          spark={history?.server.memory_percent}
        />
        <Kpi
          label="Disk"
          icon={<HardDrive />}
          tone="var(--warning)"
          value={server?.disk_percent}
          unit="%"
          ring={server?.disk_percent}
          detail={server ? `${bytes(server.disk_used_bytes)} of ${bytes(server.disk_total_bytes)}` : "Measuring…"}
          spark={history?.server.disk_percent}
        />
        <Kpi
          label="Running"
          icon={<Server />}
          tone="var(--success)"
          value={server?.instances_running ?? (instances ? counts.online : undefined)}
          detail={
            <>
              of {instances?.length ?? "…"} instances{server?.uptime_seconds ? ` · server up ${duration(server.uptime_seconds)}` : ""}
            </>
          }
        />
      </div>

      <div className="toolbar rise">
        <div className="input-group">
          <Search />
          <input className="input" placeholder="Search name, branch, repository…" value={query} onChange={(e) => setQuery(e.target.value)} />
        </div>
        <Segmented<Filter>
          value={filter}
          onChange={setFilter}
          options={[
            { value: "all", label: `All ${instances?.length ?? ""}` },
            { value: "online", label: `Online ${counts.online}` },
            { value: "paused", label: `Paused ${counts.paused}` },
            { value: "busy", label: `Working ${counts.busy}` },
          ]}
        />
      </div>

      {isPending ? (
        <div className="instance-grid">
          {[0, 1, 2].map((i) => (
            <div key={i} className="card pad stack">
              <Skeleton height={40} width="60%" />
              <Skeleton height={16} />
              <Skeleton height={56} />
            </div>
          ))}
        </div>
      ) : shown.length ? (
        <div className="instance-grid stagger">
          {shown.map((instance) => (
            <InstanceCard key={instance.name} instance={instance} history={history?.instances[instance.name]} />
          ))}
        </div>
      ) : (
        <div className="card">
          <Empty
            icon={<Boxes />}
            title={instances?.length ? "Nothing matches" : "No instances yet"}
            action={
              !instances?.length && admin ? (
                <Link className="btn primary" to="/new" viewTransition>
                  <Plus /> Create the first one
                </Link>
              ) : undefined
            }
          >
            {instances?.length ? "Try another search or filter." : "Create one from any GitHub branch: it runs in a few minutes."}
          </Empty>
        </div>
      )}
    </Page>
  );
}
