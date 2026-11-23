// A branch's history, the Odoo.sh way: its builds (one commit an instance was built from), stage changes and
// restores, newest first and grouped by day; a commit a branch did not build (its Behaviour upon new commits is
// Do nothing) is a small card of its own, and a build that ran tests says how many passed or which module failed. The live build (an instance's newest successful one) has Open Odoo; a
// failed build says why it failed (the step, the reason, the error details and, for a repository the panel cannot
// read, the fix), with Odoo's messages to unfold; every entry with a job opens its steps. The live event
// stream ("build") reads the timeline again whenever an entry is added or changes.

import { useQueries } from "@tanstack/react-query";
import {
  ArchiveRestore,
  ChevronRight,
  CircleCheck,
  CircleX,
  CircleDot,
  ExternalLink,
  FlaskConical,
  History as HistoryIcon,
  LoaderCircle,
  Milestone,
  ScrollText,
  TriangleAlert,
} from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router";
import { branchHistoryQuery } from "@/api/queries";
import type { Build, BuildStatus, CommitEntry, HistoryEntry, Instance, ProjectBranch, ProjectDetail, RestoreEntry, StageChange } from "@/api/types";
import { STAGE_LABEL } from "@/components/StageTag";
import { Button, Empty, Skeleton } from "@/components/app/basics";
import { CommitHash } from "@/components/CommitHash";
import { ConnectButton } from "@/components/ConnectAs";
import { ChecksBadge } from "@/components/RepoGuard";
import { WhyItFailed } from "@/components/WhyItFailed";
import { intlTag, useLocale, useT } from "@/i18n";
import words from "@/i18n/builds";
import failureWords from "@/i18n/failure";
import { ago, dateTime, duration, fullDateTime } from "@/lib/format";
import { githubCommitUrl, shortSha } from "@/lib/git";

const PAGE = 25;
const LIVE: BuildStatus[] = ["success", "warning"];

// ---------------------------------------------------------------- small parts, shared with the Builds tab

/** The ids of the live builds: per instance, its newest successful build (the server drops the older ones). */
export function liveBuilds(builds: Build[]): Set<string> {
  const seen = new Set<string>();
  const live = new Set<string>();
  for (const build of [...builds].sort((a, b) => Date.parse(b.at ?? "") - Date.parse(a.at ?? ""))) {
    if (seen.has(build.instance)) continue;
    if (LIVE.includes(build.status)) {
      live.add(build.id);
      seen.add(build.instance);
    } else if (build.status !== "failed") seen.add(build.instance);
  }
  return live;
}

/** A person's initials on a colour of their own (the same name, the same colour). */
export function Avatar({ name, size = 32 }: { name: string; size?: number }) {
  let hash = 0;
  for (const char of name) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  const letters = name.split(/[\s._-]+/).filter(Boolean).slice(0, 2).map((p) => p[0]).join("") || "?";
  return (
    <span className="bh-avatar" aria-hidden style={{ "--hue": hash % 360, width: size, height: size } as React.CSSProperties}>
      {letters}
    </span>
  );
}

const STATUS_ICON: Record<BuildStatus, React.ReactNode> = {
  success: <CircleCheck />,
  warning: <TriangleAlert />,
  failed: <CircleX />,
  building: <LoaderCircle className="bh-spin" />,
  dropped: null,
};

/** How a build went: "Test: Success" in green, Warning in amber, Failed in red, Building turning, a grey DROPPED pill. */
export function BuildStatusTag({ status }: { status: BuildStatus }) {
  const t = useT(words).status;
  return (
    <span className={`bh-status ${status}`}>
      {STATUS_ICON[status]}
      {t[status]}
    </span>
  );
}

/** A commit's subject, linked to the commit on GitHub (a new tab) when the repository is on GitHub: the link covers
 *  its whole card (styles: .commit-link in _pages.scss), and what acts inside the card sits above it. */
export function CommitSubject({ commit, repository, className }: { commit: NonNullable<Build["commit"]>; repository: string; className: string }) {
  const t = useT(words).history;
  const url = githubCommitUrl(repository, commit.full_sha || commit.sha);
  if (!url) return <bdi className={className} dir="auto" title={commit.subject}>{commit.subject}</bdi>;
  return (
    <a className={`${className} commit-link`} href={url} target="_blank" rel="noreferrer" title={t.openCommit(shortSha(commit.sha))}>
      <bdi dir="auto">{commit.subject}</bdi>
      <ExternalLink className="commit-link-icon" aria-hidden />
    </a>
  );
}

/** The time of an entry, relative, with the date and time in full on hover. */
function When({ at }: { at: string | null }) {
  if (!at) return null;
  return <time dateTime={at} title={fullDateTime(at)}>{ago(at)}</time>;
}

// ---------------------------------------------------------------- the entries

type Context = {
  /** the project's id (the Checks badge reads its CI results) */
  project: string;
  repository: string;
  live: Set<string>;
  instances: Map<string, Instance>;
  /** where the credentials of an instance are, when the caller may see them */
  access: (instance: string) => string | null;
  /** whether the caller may open the instance (a locked branch hides it from those it does not allow) */
  mayOpen: boolean;
  /** whether the caller may open Odoo as one of its users (Connect as…) */
  connectAs: boolean;
  onJob: (id: string) => void;
};

function LogLink({ job, ctx }: { job: string | null; ctx: Context }) {
  const t = useT(words).history;
  if (!job) return null;
  return (
    <Button size="sm" variant="ghost" icon={<ScrollText />} onClick={() => ctx.onJob(job)}>{t.viewSteps}</Button>
  );
}

/** Whether the person who pushed was e-mailed about a failed build of their push (projects/push_failed.py). */
function Notified({ notified }: { notified: Build["notified"] }) {
  const t = useT(failureWords).notified;
  if (!notified) return null;
  if (notified.emailed) return <p className="xsmall muted">{t.emailed}</p>;
  if (notified.reason === "no_address") return <p className="xsmall muted">{t.noAddress}</p>;
  if (notified.reason === "mail_failed") return <p className="xsmall muted">{t.mailFailed}</p>;
  return null;
}

function Messages({ lines }: { lines: string[] }) {
  const t = useT(words).history;
  const [open, setOpen] = useState(false);
  if (!lines.length) return null;
  return (
    <div className="bh-messages">
      <button type="button" className="bh-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
        <ChevronRight className="bh-chevron flip-rtl" />
        {open ? t.hideMessages : t.showMessages(lines.length)}
      </button>
      {open && (
        <ul className="bh-lines" dir="ltr">
          {lines.map((line, i) => <li key={i}>{line}</li>)}
        </ul>
      )}
    </div>
  );
}

function BuildCard({ build, ctx, branch }: { build: Build; ctx: Context; branch: ProjectBranch }) {
  const t = useT(words);
  const instance = ctx.instances.get(build.instance);
  const live = ctx.live.has(build.id);
  const author = build.commit?.author || (build.by && build.by !== "system" ? build.by : t.history.system);
  const ends = live && instance?.expires_at && branch.stage !== "production" ? instance.expires_at : null;
  // a card whose commit is on GitHub opens it there when pressed (its subject is the link)
  const linked = !!build.commit && !!githubCommitUrl(ctx.repository, build.commit.full_sha || build.commit.sha);
  return (
    <article className={`bh-card ${build.status}`} data-live={live || undefined} data-linked={linked || undefined}>
      <div className="bh-card-head">
        <Avatar name={author} />
        <div className="bh-who">
          <bdi className="bh-author truncate">{author}</bdi>
          <span className="bh-meta">
            <When at={build.at} />
            <span aria-hidden>·</span>
            <span>{t.trigger[build.trigger]}</span>
            {build.duration_seconds != null && (
              <>
                <span aria-hidden>·</span>
                <span>{duration(build.duration_seconds)}</span>
              </>
            )}
          </span>
        </div>
        <div className="bh-side">
          <BuildStatusTag status={build.status} />
          <ChecksBadge project={ctx.project} sha={build.commit?.full_sha} />
          {live && instance?.url && ctx.mayOpen && <ConnectButton instance={instance} connectAs={ctx.connectAs} access={ctx.access(build.instance)} size="sm" />}
        </div>
      </div>
      <div className="bh-commit">
        {build.commit ? (
          <>
            <CommitSubject commit={build.commit} repository={ctx.repository} className="bh-subject" />
            <CommitHash sha={build.commit.full_sha || build.commit.sha} repository={ctx.repository} />
          </>
        ) : (
          <span className="faint">{t.history.unknownCommit}</span>
        )}
      </div>
      {build.status === "failed" && build.failure ? (
        <WhyItFailed failure={build.failure} fallback={build.result} instance={ctx.instances.has(build.instance) ? build.instance : null} />
      ) : (
        (build.status === "failed" || build.status === "warning") && build.result && (
          <p className={`bh-result ${build.status}`}>{build.result}</p>
        )
      )}
      {build.status === "failed" && <Notified notified={build.notified} />}
      {build.tests && <TestsLine tests={build.tests} />}
      <Messages lines={build.messages} />
      {(build.job || ends) && (
        <div className="bh-foot">
          {ends && (
            <span className="bh-lifetime" title={t.history.lifetimeHint}>
              {t.history.lifetime[branch.stage === "staging" ? "staging" : "development"](dateTime(ends))}
            </span>
          )}
          <span className="spacer" />
          <LogLink job={build.job} ctx={ctx} />
        </div>
      )}
    </article>
  );
}

/** What a build's tests found: "Tests: 132 passed", or how many failed and in which modules. */
function TestsLine({ tests }: { tests: NonNullable<Build["tests"]> }) {
  const t = useT(words).history;
  const bad = tests.failed + tests.errors;
  return (
    <p className={`bh-tests ${bad ? "failed" : tests.status}`}>
      <FlaskConical aria-hidden />
      <span>{bad ? t.testsFailed(bad, tests.tests) : t.testsPassed(tests.tests)}</span>
      {tests.warnings > 0 && !bad && <span className="bh-tests-more">· {t.testsWarnings(tests.warnings)}</span>}
      {tests.modules.length > 0 && (
        <span className="bh-tests-more">
          · {t.failingIn}{" "}
          {tests.modules.map((m, i) => <span key={m}>{i > 0 && ", "}<code dir="ltr">{m}</code></span>)}
        </span>
      )}
    </p>
  );
}

/** A commit the branch did not build (Behaviour upon new commits: Do nothing). */
function CommitCard({ entry, ctx, settings }: { entry: CommitEntry; ctx: Context; settings: string }) {
  const t = useT(words).history;
  return (
    <article className="bh-card note bh-commit-card" data-linked={(entry.commit && githubCommitUrl(ctx.repository, entry.commit.full_sha || entry.commit.sha)) ? true : undefined}>
      <div className="bh-note-head">
        <span className="bh-kind">{t.commitReceived}</span>
        <span className="bh-skip">{t.notBuiltTag}</span>
        <span className="spacer" />
        <span className="bh-meta"><When at={entry.at} /></span>
      </div>
      <div className="bh-commit">
        {entry.commit?.subject && <CommitSubject commit={entry.commit} repository={ctx.repository} className="bh-subject" />}
        {entry.commit && <CommitHash sha={entry.commit.full_sha || entry.commit.sha} repository={ctx.repository} />}
      </div>
      <p className="small muted">
        {t.notBuilt} <Link to={settings} viewTransition>{t.branchSettings}</Link>
      </p>
    </article>
  );
}

function StageCard({ entry }: { entry: StageChange }) {
  const t = useT(words).history;
  return (
    <article className="bh-card note">
      <div className="bh-note-head">
        <span className="bh-kind">{t.stageChange}:</span>
        <s>{STAGE_LABEL[entry.from]}</s>
        <ChevronRight className="bh-arrow flip-rtl" aria-hidden />
        <b>{STAGE_LABEL[entry.to]}</b>
        <span className="spacer" />
        <span className="bh-meta"><bdi>{entry.by && entry.by !== "system" ? entry.by : t.system}</bdi> · <When at={entry.at} /></span>
      </div>
      {entry.explanation && <blockquote className="bh-quote">{entry.explanation}</blockquote>}
      {entry.reason && <p className="small muted">{entry.reason}</p>}
    </article>
  );
}

function RestoreCard({ entry, ctx }: { entry: RestoreEntry; ctx: Context }) {
  const t = useT(words).history;
  const failed = entry.status === "failed";
  return (
    <article className={`bh-card note ${failed ? "failed" : ""}`}>
      <div className="bh-note-head">
        <span className="bh-kind">{failed ? t.restoreFailed : t.restore}:</span>
        <span className="truncate">{t.restoreInto(entry.instance)}</span>
        <span className="spacer" />
        <span className="bh-meta"><bdi>{entry.by && entry.by !== "system" ? entry.by : t.system}</bdi> · <When at={entry.at} /></span>
      </div>
      {entry.result && <blockquote className={`bh-quote ${failed ? "failed" : ""}`}>{entry.result}</blockquote>}
      {entry.job && <div className="bh-foot"><span className="spacer" /><LogLink job={entry.job} ctx={ctx} /></div>}
    </article>
  );
}

const ENTRY_ICON = (entry: HistoryEntry) =>
  entry.type === "stage" ? <Milestone /> : entry.type === "restore" ? <ArchiveRestore /> : entry.type === "commit" ? <CircleDot /> : STATUS_ICON[entry.status] ?? <span className="bh-hollow" />;

// ---------------------------------------------------------------- the timeline

/** The day an entry belongs to, in the panel's language: "November 10, 2026". */
function dayOf(at: string | null, tag: string): string {
  if (!at) return "";
  return new Date(at).toLocaleDateString(tag, { dateStyle: "long" });
}

export function BranchHistory({
  project,
  branch,
  instances,
  access,
  mayOpen,
  connectAs = false,
  onJob,
}: {
  project: ProjectDetail;
  branch: ProjectBranch;
  instances: Map<string, Instance>;
  access: (instance: string) => string | null;
  mayOpen: boolean;
  connectAs?: boolean;
  onJob: (id: string) => void;
}) {
  const t = useT(words).history;
  const locale = useLocale();
  const [pages, setPages] = useState(1);
  const results = useQueries({
    queries: Array.from({ length: pages }, (_, i) => ({ ...branchHistoryQuery(project.id, branch.name, PAGE, i * PAGE), enabled: Boolean(project.id) })),
  });
  const first = results[0];
  const total = results.reduce((most, r) => Math.max(most, r.data?.total ?? 0), 0);
  const entries = useMemo(() => {
    const seen = new Set<string>();
    const all: HistoryEntry[] = [];
    for (const r of results)
      for (const e of r.data?.entries ?? []) {
        if (seen.has(e.id)) continue;
        seen.add(e.id);
        all.push(e);
      }
    return all;
    // the data of each page is what changes; the list of results is new on every render
  }, [results.map((r) => r.dataUpdatedAt).join()]);
  const loadingMore = results.some((r) => r.isFetching && !r.data);
  const live = useMemo(() => liveBuilds(entries.filter((e): e is Build => e.type === "build")), [entries]);
  const ctx: Context = { project: project.id, repository: project.repository.url, live, instances, access, mayOpen, connectAs, onJob };

  if (first?.isPending)
    return (
      <div className="bh" aria-busy="true" aria-label={t.loading}>
        <Skeleton height={20} width="30%" />
        <Skeleton height={104} />
        <Skeleton height={104} />
      </div>
    );
  if (!entries.length)
    return (
      <div className="card">
        <Empty icon={<HistoryIcon />} title={t.empty}>{branch.instances.length ? t.emptyText : t.noInstanceText}</Empty>
      </div>
    );

  const tag = intlTag(locale);
  const days: { day: string; entries: HistoryEntry[] }[] = [];
  for (const entry of entries) {
    const day = dayOf(entry.at, tag);
    const last = days.at(-1);
    if (last && last.day === day) last.entries.push(entry);
    else days.push({ day, entries: [entry] });
  }
  return (
    <div className="bh">
      {days.map(({ day, entries: list }) => (
        <section key={day} className="bh-day" aria-label={day}>
          <h3 className="bh-day-title">{day}</h3>
          <ol className="bh-list">
            {list.map((entry) => (
              <li key={entry.id} className={`bh-item ${entry.type === "build" ? entry.status : entry.type === "restore" && entry.status === "failed" ? "failed" : "note"}`}>
                <span className="bh-node" aria-hidden>{ENTRY_ICON(entry)}</span>
                {entry.type === "build" ? (
                  <BuildCard build={entry} ctx={ctx} branch={branch} />
                ) : entry.type === "stage" ? (
                  <StageCard entry={entry} />
                ) : entry.type === "commit" ? (
                  <CommitCard entry={entry} ctx={ctx} settings={`/projects/${project.id}/branches/${encodeURIComponent(branch.name)}/branch-settings`} />
                ) : (
                  <RestoreCard entry={entry} ctx={ctx} />
                )}
              </li>
            ))}
          </ol>
        </section>
      ))}
      {entries.length < total && (
        <div className="bh-more">
          <Button loading={loadingMore} onClick={() => setPages(pages + 1)}>{t.showOlder}</Button>
        </div>
      )}
    </div>
  );
}
