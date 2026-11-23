// A project's Builds tab, the Odoo.sh way: one row per branch, grouped by stage, with the branch's last builds side
// by side (newest first; they scroll sideways when there is no room). Pressing a card opens its commit on GitHub in a
// new tab (the branch's history when the repository is not on GitHub); its short hash copies, the reason a build
// failed opens the history, and the live build has Open Odoo. The live event stream ("build") reads the builds again whenever one starts, ends or is dropped.

import { FolderGit2, Hammer, RotateCcw, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router";
import { useBuilds, useInstances } from "@/api/queries";
import type { Build, Instance, ProjectBranch, ProjectDetail, Stage } from "@/api/types";
import { RebuildDialog } from "@/components/BranchDialogs";
import { BuildStatusTag, CommitSubject, liveBuilds } from "@/components/BuildHistory";
import { CommitHash } from "@/components/CommitHash";
import { ConnectButton } from "@/components/ConnectAs";
import { ChecksBadge } from "@/components/RepoGuard";
import { JobDrawer } from "@/components/JobDrawer";
import { branchPath, dotOf, productionOf, useBranchCan } from "@/components/ProjectWorkspace";
import { STAGE_ICON, STAGE_LABEL } from "@/components/StageTag";
import { Button, Empty, Skeleton } from "@/components/app/basics";
import { Input } from "@/components/ui/input";
import { useT } from "@/i18n";
import words from "@/i18n/builds";
import failureWords from "@/i18n/failure";
import { stepTitle } from "@/i18n/jobs";
import { ago, duration, elapsed, fullDateTime, statusLabel } from "@/lib/format";
import { githubCommitUrl } from "@/lib/git";

const STAGES: Stage[] = ["production", "staging", "development"];
const PER_BRANCH = 4;

function BuildTile({
  build,
  live,
  instance,
  repository,
  mayOpen,
  access,
  to,
  project,
}: {
  project: string;
  build: Build;
  live: boolean;
  instance: Instance | undefined;
  repository: string;
  mayOpen: boolean;
  access: string | null;
  to: string;
}) {
  const t = useT(words);
  const fw = useT(failureWords);
  const author = build.commit?.author || build.by || "";
  // pressing the card opens its commit on GitHub (a new tab); without a commit on GitHub, the branch's history
  const linked = !!build.commit && !!githubCommitUrl(repository, build.commit.full_sha || build.commit.sha);
  return (
    <article className={`pb-tile ${build.status}`} data-live={live || undefined} data-linked={linked || undefined}>
      {linked ? (
        <CommitSubject commit={build.commit!} repository={repository} className="pb-subject truncate" />
      ) : (
        <Link to={to} viewTransition className="pb-open">
          <bdi className="pb-subject truncate" title={build.commit?.subject}>{build.commit?.subject ?? t.history.unknownCommit}</bdi>
        </Link>
      )}
      <div className="pb-who">
        {author && <bdi className="pb-author truncate">{author}</bdi>}
        {build.commit && <CommitHash sha={build.commit.full_sha || build.commit.sha} repository={repository} />}
      </div>
      <dl className="pb-facts">
        <div>
          <dt>{t.builds.age}</dt>
          <dd>{build.at ? <time dateTime={build.at} title={fullDateTime(build.at)}>{ago(build.at)}</time> : "—"}</dd>
        </div>
        <div>
          <dt>{t.builds.duration}</dt>
          <dd>{build.status === "building" ? elapsed(build.started_at ?? build.at) : duration(build.duration_seconds)}</dd>
        </div>
      </dl>
      {build.status === "failed" && (build.failure?.reason || build.result) && (
        // why it failed, in short; the branch's history has the step, the error details and the fix
        <Link to={to} viewTransition className="pb-why xsmall" title={fw.title}>
          {build.failure?.step && <b>{stepTitle(build.failure.step)}: </b>}
          {build.failure?.reason || build.result}
        </Link>
      )}
      <div className="pb-foot">
        <BuildStatusTag status={build.status} />
        <ChecksBadge project={project} sha={build.commit?.full_sha} />
        {live && instance?.url && mayOpen && <ConnectButton instance={instance} connectAs={!!access} access={access} size="sm" />}
      </div>
    </article>
  );
}

function BranchRow({
  project,
  branch,
  builds,
  instances,
  production,
  onRebuild,
}: {
  project: ProjectDetail;
  branch: ProjectBranch;
  builds: Build[];
  instances: Map<string, Instance>;
  production: string | null;
  onRebuild: (instance: string) => void;
}) {
  const t = useT(words).builds;
  const can = useBranchCan();
  const members = branch.instances.map((n) => instances.get(n)).filter((i): i is Instance => !!i);
  const dot = dotOf(members);
  const live = useMemo(() => liveBuilds(builds), [builds]);
  const first = branch.instances[0];
  const mayRebuild = branch.stage === "staging" && !!first && !!production && can(branch, "instances.restore");
  const mayOpen = !branch.lock || can(branch, "instances.logs");
  const access = (name: string) =>
    can(branch, "instances.credentials") ? branchPath(project.id, branch.name, "access", name === first ? null : name) : null;
  const history = branchPath(project.id, branch.name);
  return (
    <div className="pb-row" role="group" aria-label={t.rowLabel(branch.name)}>
      <div className="pb-branch">
        <div className="pb-name">
          <span className={`ws-dot ${dot}`} role="img" aria-label={members.length ? statusLabel(members[0]!.status) : ""} />
          <Link to={history} viewTransition className="truncate" title={t.openHistory(branch.name)}>
            <bdi dir="ltr">{branch.name}</bdi>
          </Link>
        </div>
        <div className="pb-actions">
          {branch.on_github !== false && (
            <Button asChild size="sm" variant="ghost">
              <a href={`${project.repository.url}/tree/${branch.name}`} target="_blank" rel="noreferrer"><FolderGit2 /> {t.github}</a>
            </Button>
          )}
          {mayRebuild && <Button size="sm" variant="ghost" icon={<RotateCcw />} onClick={() => onRebuild(first!)}>{t.rebuild}</Button>}
        </div>
      </div>
      <div className="pb-tiles" tabIndex={builds.length > 1 ? 0 : undefined} aria-label={t.rowLabel(branch.name)}>
        {builds.length ? (
          builds.map((build) => (
            <BuildTile key={build.id} project={project.id} build={build} live={live.has(build.id)} instance={instances.get(build.instance)} repository={project.repository.url}
              mayOpen={mayOpen} access={access(build.instance)} to={history} />
          ))
        ) : (
          <p className="pb-none">{t.noBuild}</p>
        )}
      </div>
    </div>
  );
}

export function ProjectBuilds({ project }: { project: ProjectDetail }) {
  const t = useT(words).builds;
  const { data, isPending } = useBuilds(project.id, PER_BRANCH);
  const { data: list } = useInstances();
  const instances = useMemo(() => new Map((list ?? []).map((i) => [i.name, i])), [list]);
  const production = useMemo(() => productionOf(project.branches_list, instances), [project.branches_list, instances]);
  const [filter, setFilter] = useState("");
  const [rebuild, setRebuild] = useState<{ branch: ProjectBranch; instance: string } | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const query = filter.trim().toLowerCase();
  const byName = new Map((data?.branches ?? []).map((b) => [b.branch, b.builds]));
  const branches = project.branches_list.filter((b) => !query || b.name.toLowerCase().includes(query));
  const anyBuild = (data?.branches ?? []).some((b) => b.builds.length);

  return (
    <div className="stack tab-panel">
      <div className="toolbar">
        <div className="input-group">
          <Search />
          <Input placeholder={t.filter} aria-label={t.filter} value={filter} onChange={(e) => setFilter(e.target.value)} />
        </div>
      </div>
      {isPending ? (
        <div className="card pad stack">
          <Skeleton height={20} width="30%" />
          <Skeleton height={132} />
          <Skeleton height={132} />
        </div>
      ) : !anyBuild && !query ? (
        <div className="card">
          <Empty icon={<Hammer />} title={t.none}>{t.noneText}</Empty>
        </div>
      ) : !branches.length ? (
        <div className="card"><Empty icon={<Search />} title={t.noMatch} /></div>
      ) : (
        STAGES.map((stage) => {
          const inStage = branches.filter((b) => b.stage === stage);
          if (!inStage.length) return null;
          const Icon = STAGE_ICON[stage];
          return (
            <section key={stage} className={`card pb-stage ${stage}`} aria-label={STAGE_LABEL[stage]}>
              <div className="card-head">
                <Icon strokeWidth={1.75} />
                <h2>{STAGE_LABEL[stage]}</h2>
                <span className="ws-count">{inStage.length}</span>
              </div>
              <div className="pb-rows">
                {inStage.map((branch) => (
                  <BranchRow key={branch.name} project={project} branch={branch} builds={byName.get(branch.name) ?? []} instances={instances}
                    production={production} onRebuild={(instance) => setRebuild({ branch, instance })} />
                ))}
              </div>
            </section>
          );
        })
      )}
      {rebuild && (
        <RebuildDialog project={project} branch={rebuild.branch} instance={rebuild.instance} production={production}
          onClose={() => setRebuild(null)} onJob={setJobId} />
      )}
      {jobId && <JobDrawer id={jobId} onClose={() => setJobId(null)} />}
    </div>
  );
}
