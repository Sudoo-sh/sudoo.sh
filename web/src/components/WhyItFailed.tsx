// "Why it failed": the step that failed, the plain reason and only the lines that explain it (the server picks and
// cleans them, core/failure.py; a job's technical log is never sent). For a repository the panel cannot read (a new
// private submodule, the main repository), the fix is right next to it: the GitHub App first, otherwise a read-only
// deploy key, "Check again", and once every repository can be read, "Retry the update". Only people who manage the
// project (or GitHub access) see those actions.

import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, Check, CircleCheck, CircleX, Copy, KeyRound, RefreshCw, RotateCw } from "lucide-react";
import { useState } from "react";
import { api, errorText } from "@/api/client";
import { useCall, useInstance } from "@/api/queries";
import type { Failure, GitHubAppState, RepoAccess } from "@/api/types";
import { useT } from "@/i18n";
import words from "@/i18n/failure";
import { stepTitle } from "@/i18n/jobs";
import { useCan } from "./Session";
import { Button, Callout, CopyField, Tag } from "./app/basics";

/** The error details: a monospace box, left to right inside right-to-left pages, with a copy button. */
function Details({ lines }: { lines: string[] }) {
  const t = useT(words);
  const [copied, setCopied] = useState(false);
  if (!lines.length) return null;
  const copy = async () => {
    await navigator.clipboard.writeText(lines.join("\n")).catch(() => undefined);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1400);
  };
  return (
    <div className="why-details stack-sm">
      <div className="row">
        <h3 className="small">{t.details}</h3>
        <span className="spacer" />
        <Button
          size="sm"
          variant="ghost"
          icon={copied ? <Check /> : <Copy />}
          onClick={copy}
          aria-label={t.copy}
          title={t.copy}
        >
          {copied ? t.copied : null}
        </Button>
      </div>
      <pre className="why-lines" dir="ltr" tabIndex={0}>
        {lines.join("\n")}
      </pre>
      <p className="xsmall faint">{t.detailsHint}</p>
    </div>
  );
}

/** For a repository the panel cannot read: which repositories it can read now, and how to give it access. */
function RepositoryFix({ instance, repository }: { instance: string; repository: string | null }) {
  const t = useT(words).fix;
  const can = useCan();
  const { data: found } = useInstance(instance);
  const url = found?.git.repository ?? null;
  const branch = found?.git.branch ?? null;
  const access = useQuery({
    queryKey: ["repository-access", url, branch],
    queryFn: () =>
      api.get<RepoAccess>(
        `/repositories/access?url=${encodeURIComponent(url!)}${branch ? `&branch=${encodeURIComponent(branch)}` : ""}`,
      ),
    enabled: !!url,
    staleTime: 0,
  });
  // where the GitHub App's installation is (reading it needs github.manage; the access report's own fix link
  // serves the others)
  const app = useQuery({
    queryKey: ["github-app"],
    queryFn: async () => {
      const answer = await api.page<GitHubAppState>("/github/app");
      return { ...answer.data, can_manage: Boolean(answer.meta?.can_manage) };
    },
    enabled: can("github.manage"),
    retry: false,
  });
  const [keys, setKeys] = useState<Record<string, { public_key: string; github_url: string }>>({});
  const createKey = useCall(
    (repo: string) => api.post<{ repo: string; public_key: string; github_url: string }>("/deploy-keys", { repo }),
    [["deploy-keys"]],
  );
  const retry = useCall(() => api.post(`/instances/${encodeURIComponent(instance)}/actions/update`, {}), [["jobs"]]);
  if (!can("projects.manage") && !can("github.manage")) return null;
  const report = access.data;
  const owner = (repository ?? "").split("/")[0]?.toLowerCase();
  const installation = app.data?.app?.installations.find((i) => i.account.toLowerCase() === owner);
  const appLink = installation?.settings_url ?? app.data?.app?.install_url ?? null;
  const missing = report?.repositories.filter((r) => !r.ok) ?? [];
  return (
    <section className="why-fix stack-sm" aria-label={t.title}>
      <h3 className="small">{t.title}</h3>
      {repository && (
        <p className="small">
          {t.intro(
            <bdi key="r" dir="ltr">
              <code>{repository}</code>
            </bdi>,
          )}
        </p>
      )}
      {report && (
        <ul className="why-repos stack-sm">
          {report.repositories.map((r) => (
            <li key={r.url} className="row small" style={{ flexWrap: "wrap" }}>
              {r.ok ? <CircleCheck size={16} color="var(--positive)" /> : <CircleX size={16} color="var(--negative)" />}
              <bdi className="truncate" dir="ltr">
                {r.url}
              </bdi>
              <Tag>{r.role === "main" ? t.main : t.submodule}</Tag>
              <span style={{ color: r.ok ? "var(--positive)" : "var(--negative)" }}>
                {r.ok ? t.accessOk : t.noAccess}
              </span>
            </li>
          ))}
        </ul>
      )}
      {missing.length > 0 && (
        <ol className="why-ways stack-sm">
          <li>
            <b>{t.appTitle}</b>
            <p className="xsmall muted">{t.appText}</p>
            {missing.map((r) =>
              r.fix?.url ? (
                <Button key={r.url} asChild size="sm">
                  <a href={r.fix.url} target="_blank" rel="noreferrer">
                    {r.fix.label} <ArrowUpRight className="flip-rtl" />
                  </a>
                </Button>
              ) : null,
            )}
            {!missing.some((r) => r.fix?.url) && appLink && (
              <Button asChild size="sm">
                <a href={appLink} target="_blank" rel="noreferrer">
                  {t.openInstallation} <ArrowUpRight className="flip-rtl" />
                </a>
              </Button>
            )}
          </li>
          <li>
            <b>{t.keyTitle}</b>
            <p className="xsmall muted">{t.keyText}</p>
            {missing.map((r) => {
              const repo = r.repo ?? "";
              const made = keys[repo];
              return (
                <div key={r.url} className="stack-sm">
                  {made ? (
                    <>
                      <CopyField value={made.public_key} block />
                      <Button asChild size="sm">
                        <a href={made.github_url} target="_blank" rel="noreferrer">
                          {t.addOnGitHub} <ArrowUpRight className="flip-rtl" />
                        </a>
                      </Button>
                    </>
                  ) : (
                    can("github.manage") &&
                    repo && (
                      <Button
                        size="sm"
                        icon={<KeyRound />}
                        disabled={createKey.isPending}
                        onClick={() =>
                          createKey.mutate(repo, { onSuccess: (key) => setKeys((all) => ({ ...all, [repo]: key })) })
                        }
                      >
                        {t.createKey} <bdi dir="ltr">{repo}</bdi>
                      </Button>
                    )
                  )}
                </div>
              );
            })}
            {createKey.error && <Callout tone="danger">{errorText(createKey.error)}</Callout>}
          </li>
        </ol>
      )}
      <div className="row-wrap">
        <Button size="sm" icon={<RefreshCw />} disabled={access.isFetching || !url} onClick={() => access.refetch()}>
          {access.isFetching ? t.checking : t.checkAgain}
        </Button>
        {report?.all_ok && can("instances.update") && (
          <Button
            size="sm"
            variant="primary"
            icon={<RotateCw />}
            disabled={retry.isPending || retry.isSuccess}
            onClick={() => retry.mutate(undefined)}
          >
            {t.retry}
          </Button>
        )}
      </div>
      {report?.all_ok && (
        <p className="small" style={{ color: "var(--positive)" }}>
          {t.allOk}
        </p>
      )}
      {retry.isSuccess && <p className="small muted">{t.retried}</p>}
      {retry.error && <Callout tone="danger">{errorText(retry.error)}</Callout>}
    </section>
  );
}

/** "Why it failed" of a job or a build. `instance`: offer the fix for a repository the panel cannot read. */
export function WhyItFailed({
  failure,
  fallback,
  instance,
}: {
  failure?: Failure | null;
  fallback?: string | null;
  instance?: string | null;
}) {
  const t = useT(words);
  const reason = failure?.reason || fallback || t.noReason;
  return (
    <section className="why-failed stack-sm" aria-label={t.title}>
      <Callout tone="danger">
        <b>{t.title}</b>
        {failure?.step && (
          <div className="small">
            {t.step}: <b>{stepTitle(failure.step)}</b>
          </div>
        )}
        <div>{reason}</div>
      </Callout>
      {failure && <Details lines={failure.details} />}
      {failure?.kind === "repository" && instance && (
        <RepositoryFix instance={instance} repository={failure.repository} />
      )}
    </section>
  );
}
