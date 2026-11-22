// A project's "GitHub connection" card (its Settings tab): one press checks the repository, the GitHub App's
// installation, its permissions and events, its webhook and latest deliveries, and the submodules' access, and says
// what to fix where (POST /projects/<id>/github/check). Nothing is changed by the check.

import { CircleAlert, CircleCheck, CircleMinus, ExternalLink, RefreshCw, TriangleAlert } from "lucide-react";
import { Link } from "react-router";
import { api, errorText } from "@/api/client";
import { useCall } from "@/api/queries";
import type { GitHubCheckResult, GitHubCheckStatus, ProjectDetail } from "@/api/types";
import { useCan } from "@/components/Shell";
import { Button, Callout, Skeleton, Tag } from "@/components/app/basics";
import { useT } from "@/i18n";
import words from "@/i18n/github-check";
import { ago } from "@/lib/format";

const ICON: Record<GitHubCheckStatus, React.ReactNode> = {
  ok: <CircleCheck size={18} color="var(--positive)" />,
  warning: <TriangleAlert size={18} color="var(--caution)" />,
  error: <CircleAlert size={18} color="var(--negative)" />,
  skipped: <CircleMinus size={18} color="var(--idle)" />,
};
const TONE = { ok: "positive", warning: "caution", error: "negative" } as const;

export function GitHubCheckCard({ project }: { project: ProjectDetail }) {
  const t = useT(words);
  const can = useCan();
  const run = useCall(() => api.post<GitHubCheckResult>(`/projects/${project.id}/github/check`));
  if (!can("projects.manage")) return null;
  const result = run.data;
  return (
    <section className="card">
      <div className="card-head">
        <h2 className="spacer">{t.title}</h2>
        {result && <Tag tone={TONE[result.status]}>{t.overall[result.status]}</Tag>}
      </div>
      <div className="card-body stack">
        <p className="small muted">{t.text}</p>
        {run.isPending && !result && (
          <div className="stack" aria-busy="true" aria-label={t.checking}>
            <Skeleton height={18} width="60%" />
            <Skeleton height={18} width="80%" />
            <Skeleton height={18} width="70%" />
          </div>
        )}
        {run.error && <Callout tone="danger"><b>{t.failed}.</b> {errorText(run.error)}</Callout>}
        {result && (
          <div className="check-list" aria-live="polite">
            {result.checks.map((check) => (
              <div className="check-row" key={check.id}>
                <span aria-label={t.statuses[check.status]}>{ICON[check.status]}</span>
                <div>
                  <h3>{t.names[check.id]}</h3>
                  <p className="small muted" dir="auto">{check.message}</p>
                </div>
                {check.fix?.url && (
                  <div className="fix">
                    {check.fix.url.startsWith("/") ? (
                      <Button size="sm" asChild><Link to={check.fix.url} viewTransition>{check.fix.label}</Link></Button>
                    ) : (
                      <Button size="sm" asChild>
                        <a href={check.fix.url} target="_blank" rel="noreferrer">{check.fix.label} <ExternalLink className="flip-rtl" /></a>
                      </Button>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
        <div className="row-wrap">
          <Button variant={result ? "default" : "primary"} icon={<RefreshCw />} loading={run.isPending} onClick={() => run.mutate(undefined)}>
            {run.isPending ? t.checking : result ? t.again : t.check}
          </Button>
          {result && <span className="small faint">{t.checked(ago(Date.parse(result.checked_at) / 1000))}</span>}
        </div>
      </div>
    </section>
  );
}
