import { ExternalLink, GitBranch, GitCommitHorizontal } from "lucide-react";
import { Link } from "react-router";
import type { Columns, Instance, Job } from "@/api/types";
import { useJob } from "@/api/queries";
import { ago, monogram, percent } from "@/lib/format";
import { Sparkline } from "./charts/charts";
import { Badge, Progress, StatusPill } from "./ui/basics";

export function Monogram({ name, size }: { name: string; size?: number }) {
  const m = monogram(name);
  return (
    <span className="ic-logo" style={{ ["--from" as string]: m.from, ["--to" as string]: m.to, ...(size ? { width: size, height: size } : {}) }}>
      {m.letters}
    </span>
  );
}

function RunningJob({ id }: { id: string }) {
  const { data: job } = useJob(id);
  if (!job) return null;
  return <JobLine job={job} />;
}

export function JobLine({ job }: { job: Pick<Job, "action" | "status" | "progress_percent" | "steps"> }) {
  const step = job.steps?.findLast?.((s) => s.status === "running")?.title;
  return (
    <div className="stack-sm">
      <div className="row xsmall">
        <span className="truncate" style={{ fontWeight: 600 }}>
          {job.action}
        </span>
        <span className="spacer" />
        <span className="faint tabular">{job.progress_percent != null ? `${job.progress_percent}%` : ""}</span>
      </div>
      <Progress value={job.progress_percent} status={job.status} />
      {step && <span className="xsmall faint truncate">{step}</span>}
    </div>
  );
}

export function InstanceCard({ instance, history }: { instance: Instance; history?: Columns }) {
  const usage = instance.usage;
  const cpu = history?.cpu_percent ?? [];
  return (
    <Link to={`/i/${instance.name}`} viewTransition className="card hover instance-card">
      <div className="ic-head">
        <Monogram name={instance.name} />
        <div style={{ minWidth: 0, flex: 1 }}>
          <div className="ic-name truncate">{instance.name}</div>
          <div className="ic-sub">
            <GitBranch />
            <span className="truncate">{instance.git.branch}</span>
          </div>
        </div>
        <StatusPill status={instance.status} />
      </div>
      <div className="ic-body">
        <div className="row-wrap">
          <Badge tone="primary">Odoo {instance.odoo.version}</Badge>
          <Badge tone={instance.odoo.edition === "enterprise" ? "accent" : undefined}>
            {instance.odoo.edition === "enterprise" ? "Enterprise" : "Community"}
          </Badge>
          {instance.git.branch_deleted_on_github && <Badge tone="danger">Branch deleted</Badge>}
        </div>
        {instance.current_job ? (
          <RunningJob id={instance.current_job.id} />
        ) : instance.git.commit ? (
          <div className="ic-commit">
            <GitCommitHorizontal size={14} className="faint" style={{ flex: "none", alignSelf: "center" }} />
            <span className="sha">{instance.git.commit.sha}</span>
            <span className="truncate">{instance.git.commit.subject}</span>
          </div>
        ) : (
          <div className="ic-commit faint">No code yet</div>
        )}
        <div className="ic-stats">
          <div className="stat">
            <span className="k">CPU</span>
            <span className="v">{percent(usage.cpu_percent, 1)}</span>
          </div>
          <div className="stat">
            <span className="k">Memory</span>
            <span className="v">{usage.memory_percent != null ? percent(usage.memory_percent) : "—"}</span>
          </div>
          <div className="stat">
            <span className="k">Req/min</span>
            <span className="v">{usage.requests_per_minute != null ? Math.round(usage.requests_per_minute) : "—"}</span>
          </div>
        </div>
        {cpu.length > 2 && (
          <div style={{ margin: "0 -20px -16px" }}>
            <Sparkline values={cpu} height={38} />
          </div>
        )}
      </div>
      <div className="ic-foot">
        <span className="truncate">
          {instance.last_visit_at ? `Visited ${ago(instance.last_visit_at)}` : `Created ${ago(instance.created_at)}`}
        </span>
        <span className="spacer" />
        {instance.url && instance.status !== "switched_off" && (
          <a
            className="btn sm soft"
            href={instance.url}
            target="_blank"
            rel="noreferrer"
            onClick={(e) => e.stopPropagation()}
          >
            Open <ExternalLink />
          </a>
        )}
      </div>
    </Link>
  );
}
