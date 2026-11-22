// Pull-request previews of a project (GET /projects/<id>/previews, web/v1/promotion.py; the options go through PATCH
// /projects/<id>): a development instance for each open pull request, linked from a comment on GitHub and deleted
// when the pull request closes. Pull requests from forks run code written outside the repository: off unless
// turned on, with a warning. A card of the project's Settings tab.

import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, GitPullRequest, MessageSquare } from "lucide-react";
import { Link } from "react-router";
import { api, call, errorText } from "@/api/client";
import { keys, useCall } from "@/api/queries";
import type { Preview, PreviewStatus, PreviewsMeta, Project, ProjectDetail } from "@/api/types";
import { useCan } from "@/components/Shell";
import { Callout, Field, Skeleton, Switch, Tag } from "@/components/app/basics";
import { useToast } from "@/components/app/overlay";
import { Input } from "@/components/ui/input";
import { useT } from "@/i18n";
import projectWords from "@/i18n/projects";
import words from "@/i18n/promotion";
import { ago } from "@/lib/format";

const TONE: Record<PreviewStatus, "positive" | "caution" | "negative" | undefined> = {
  creating: undefined, building: "caution", ready: "positive", failed: "negative", deleted: undefined, refused: "caution",
};

export function PullRequestPreviews({ project }: { project: ProjectDetail }) {
  const t = useT(words).previews;
  const s = useT(projectWords).settings;
  const can = useCan();
  const toast = useToast();
  const editable = can("projects.manage");
  const key = ["project", project.id, "previews"] as const;
  const query = useQuery({ queryKey: key, queryFn: () => call<Preview[]>("GET", `/projects/${encodeURIComponent(project.id)}/previews`) });
  const save = useCall((options: Record<string, unknown>) => api.patch<Project>(`/projects/${project.id}`, { options }), [keys.project(project.id), keys.projects, key]);
  const set = (options: Record<string, unknown>) =>
    save.mutate(options, { onSuccess: () => toast("success", s.saved), onError: (e) => toast("error", s.notSaved, errorText(e)) });
  const options = project.options;
  const meta = query.data?.meta as unknown as PreviewsMeta | undefined;
  const list = query.data?.data ?? [];
  return (
    <section className="card pr-previews">
      <div className="card-head"><h2>{t.title}</h2></div>
      <div className="card-body stack">
        <p className="small muted">{t.text}</p>
        {meta && !meta.app_connected && <Callout tone="info">{t.noApp}</Callout>}
        {meta && meta.app_connected && !meta.can_comment && options.pr_previews && <Callout tone="warning">{t.noComment}</Callout>}
        <Switch checked={options.pr_previews} disabled={!editable} onChange={(v) => set({ pr_previews: v })} label={t.enabled} />
        {options.pr_previews && (
          <>
            <Field label={t.limit} hint={t.limitHint}>
              <Input className="number" type="number" min={0} max={50} disabled={!editable} defaultValue={options.pr_preview_limit}
                key={`limit-${options.pr_preview_limit}`}
                onBlur={(e) => { const n = Number(e.target.value); if (Number.isInteger(n) && n >= 0 && n <= 50 && n !== options.pr_preview_limit) set({ pr_preview_limit: n }); }} />
            </Field>
            <Switch checked={options.pr_previews_forks} disabled={!editable} onChange={(v) => set({ pr_previews_forks: v })} label={t.forks} />
            {options.pr_previews_forks && <Callout tone="danger">{t.forksWarning}</Callout>}
          </>
        )}
        <div className="pr-list-head">
          <h3>{t.list}</h3>
          {meta && meta.enabled && <span className="small muted">{t.count(meta.created, meta.limit)}</span>}
        </div>
        {query.isPending ? (
          <Skeleton height={48} />
        ) : list.length ? (
          <ul className="pr-list">
            {list.map((p) => (
              <li key={p.number} className="pr-row">
                <GitPullRequest className="pr-icon" aria-hidden />
                <div className="pr-main">
                  <div className="pr-title">
                    <a href={p.url} target="_blank" rel="noreferrer" title={t.open}>
                      <span className="tabular" dir="ltr">#{p.number}</span> <bdi>{p.title}</bdi> <ArrowUpRight className="inline-icon" aria-hidden />
                    </a>
                  </div>
                  <div className="pr-facts small muted">
                    <bdi dir="ltr" className="sha">{p.fork ? `${p.repository}:${p.branch}` : p.branch}</bdi>
                    {p.fork && <Tag tone="caution">{t.fork}</Tag>}
                    {p.instance && p.instance_exists ? <Link to={`/instances/${p.instance}`} viewTransition dir="ltr">{p.instance}</Link> : p.instance ? <span dir="ltr">{p.instance}</span> : null}
                    {p.existing && <span>{t.existing}</span>}
                    {p.commented && <span className="nowrap"><MessageSquare className="inline-icon" aria-hidden /> {t.commented}</span>}
                    {p.opened_at && <span className="nowrap">{ago(p.opened_at)}</span>}
                  </div>
                  {p.reason && <p className="small muted plain-bidi">{p.reason}</p>}
                </div>
                <Tag tone={TONE[p.status]}>{t.status[p.status]}</Tag>
              </li>
            ))}
          </ul>
        ) : (
          <p className="small muted">{t.none}</p>
        )}
      </div>
    </section>
  );
}
