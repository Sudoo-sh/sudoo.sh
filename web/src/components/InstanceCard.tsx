import { ArrowUpRight, Box, GitBranch } from "lucide-react";
import { Link } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { keys, useJob } from "@/api/queries";
import type { Instance, Job } from "@/api/types";
import { ago, monogram, number, percent } from "@/lib/format";
import { useT } from "@/i18n";
import dashboard from "@/i18n/dashboard";
import { jobTitle, stepTitle } from "@/i18n/jobs";
import { LockTag } from "./LockTag";
import { StageTag } from "./StageTag";
import { Button, Progress, Status, Tag } from "./app/basics";
import { startHero, useHero } from "./app/motion";

export function Monogram({ name, large }: { name: string; large?: boolean }) {
  return <span className={`monogram ${large ? "lg" : ""}`}>{monogram(name).letters}</span>;
}

function RunningJob({ id }: { id: string }) {
  const { data: job } = useJob(id);
  return job ? <JobLine job={job} /> : null;
}

export function JobLine({ job }: { job: Pick<Job, "action" | "status" | "progress_percent" | "steps"> }) {
  const step = job.steps?.findLast?.((s) => s.status === "running")?.title;
  return (
    <div className="stack-sm">
      <div className="row small">
        <span className="truncate">{jobTitle(job.action)}</span>
        <span className="spacer" />
        <span className="mono faint xsmall">{job.progress_percent != null ? percent(job.progress_percent) : ""}</span>
      </div>
      <Progress value={job.progress_percent} status={job.status} />
      {step && <span className="xsmall faint truncate">{stepTitle(step)}</span>}
    </div>
  );
}

export function InstanceCard({ instance }: { instance: Instance }) {
  const usage = instance.usage;
  const t = useT(dashboard).card;
  const hero = useHero<HTMLSpanElement>(`instance:${instance.name}`);
  // the instance's page is read as the pointer comes to the card, so its title is there when the page opens and the
  // card's name can move into it (useHero); a page still loading simply fades in
  const client = useQueryClient();
  const warm = () =>
    void client.prefetchQuery({
      queryKey: keys.instance(instance.name),
      queryFn: () => api.get<Instance>(`/instances/${instance.name}`),
      staleTime: 10_000,
    });
  return (
    <article className="card link instance-card" onPointerEnter={warm} onFocus={warm}>
      <div className="ic-head">
        <Monogram name={instance.name} />
        <div style={{ minWidth: 0, flex: 1 }}>
          {/* the whole card opens the instance: this link covers it (.ic-open::after), the Open button sits above */}
          {/* its name moves into the instance page's title as the page opens (useHero) */}
          <Link
            to={`/i/${instance.name}`}
            viewTransition
            className="ic-name ic-open truncate"
            onClick={() => startHero(`instance:${instance.name}`, hero.current)}
          >
            <span className="hero-text" ref={hero}>
              {instance.name}
            </span>
          </Link>
          <div className="ic-sub">
            {instance.source === "plain" ? <Box /> : <GitBranch />}
            {instance.source === "plain" ? (
              <span className="truncate">{t.plain}</span>
            ) : (
              <bdi className="truncate" dir="ltr">
                {instance.git.branch}
              </bdi>
            )}
          </div>
        </div>
        <Status status={instance.status} />
      </div>
      <div className="ic-body">
        <div className="row-wrap">
          <StageTag stage={instance.stage} />
          {instance.lock && instance.git.branch && <LockTag lock={instance.lock} branch={instance.git.branch} />}
          <Tag mono>{instance.odoo.version}</Tag>
          <Tag>{instance.odoo.edition === "enterprise" ? "Enterprise" : "Community"}</Tag>
          {instance.git.branch_deleted_on_github && <Tag tone="negative">{t.branchDeleted}</Tag>}
          {instance.drift && <Tag tone="negative">{t.drift}</Tag>}
        </div>
        {instance.current_job ? (
          <RunningJob id={instance.current_job.id} />
        ) : instance.git.commit ? (
          <div className="ic-commit">
            <span className="sha">{instance.git.commit.sha}</span>
            <bdi className="truncate">{instance.git.commit.subject}</bdi>
          </div>
        ) : (
          <div className="ic-commit faint">{instance.source === "plain" ? t.noRepository : t.noCode}</div>
        )}
        <div className="ic-stats">
          <div>
            <span className="k">{t.cpu}</span>
            <span className="v">{percent(usage.cpu_percent, 1)}</span>
          </div>
          <div>
            <span className="k">{t.memory}</span>
            <span className="v">{percent(usage.memory_percent)}</span>
          </div>
          <div>
            <span className="k">{t.requests}</span>
            <span className="v">{usage.requests_per_minute != null ? number(usage.requests_per_minute) : "—"}</span>
          </div>
        </div>
      </div>
      <div className="ic-foot">
        <span className="truncate">
          {instance.last_visit_at ? t.visited(ago(instance.last_visit_at)) : t.created(ago(instance.created_at))}
        </span>
        <span className="spacer" />
        {instance.url && instance.status !== "switched_off" && (
          <Button asChild variant="ghost" size="sm" className="ic-external">
            <a href={instance.url} target="_blank" rel="noreferrer">
              {t.open} <ArrowUpRight className="flip-rtl" />
            </a>
          </Button>
        )}
      </div>
    </article>
  );
}
