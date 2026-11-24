// Comparing two branches of a project (GET /projects/<id>/compare?base=&head=, web/v1/promotion.py): what merging the
// compared branch into the base brings, as the panel reads it from its own copy of the repository (or GitHub's
// compare): commits ahead and behind, changed files, modules added, removed and with a new version, the migration
// scripts those run, and a link to GitHub's own comparison. Opened from a branch's menu and from the checklist.

import { useQuery } from "@tanstack/react-query";
import { ArrowLeftRight, ArrowUpRight } from "lucide-react";
import { useState } from "react";
import { api, errorText } from "@/api/client";
import type { BranchCompare, ModuleChange, ProjectDetail } from "@/api/types";
import { Migrations } from "@/components/PromotionChecklist";
import { Button, Callout, Field, Skeleton, Tag } from "@/components/app/basics";
import { Modal } from "@/components/app/overlay";
import { SelectBox } from "@/components/app/select";
import { useT } from "@/i18n";
import common from "@/i18n/common";
import words from "@/i18n/promotion";
import { ago, number } from "@/lib/format";

export function useCompare(project: string, base: string, head: string) {
  return useQuery({
    queryKey: ["project", project, "compare", base, head] as const,
    queryFn: () =>
      api.get<BranchCompare>(
        `/projects/${encodeURIComponent(project)}/compare?base=${encodeURIComponent(base)}&head=${encodeURIComponent(head)}`,
      ),
    enabled: Boolean(base && head && base !== head),
    staleTime: 30_000,
  });
}

const BUMP_TONE: Record<ModuleChange["bump"], "positive" | "negative" | undefined> = {
  up: "positive",
  down: "negative",
  same: undefined,
  unknown: undefined,
};

function Result({ data }: { data: BranchCompare }) {
  const t = useT(words).compare;
  const bump = { up: t.upgraded, down: t.downgraded, same: t.unchanged, unknown: t.unknown };
  const { added, removed, changed } = data.modules;
  const modules = added.length + removed.length + changed.length;
  return (
    <div className="stack">
      <p className="small muted">
        {t.bringsInto(data.head.branch, data.base.branch)}
        {data.source === "github" ? ` ${t.fromGitHub}.` : ""}
      </p>
      <div className="bc-stats">
        <div>
          <span className="bc-number tabular">{number(data.ahead)}</span>
          <span className="small muted">{t.ahead}</span>
        </div>
        <div>
          <span className="bc-number tabular">{number(data.behind)}</span>
          <span className="small muted">{t.behind}</span>
        </div>
        <div>
          <span className="bc-number tabular">{number(data.files_changed)}</span>
          <span className="small muted">{t.files}</span>
        </div>
      </div>
      <section className="bc-section">
        <h3>{t.modules}</h3>
        {!data.modules.known && <p className="small muted">{t.modulesUnknown}</p>}
        {modules ? (
          <ul className="bc-modules">
            {added.map((m) => (
              <li key={`a-${m.name}`}>
                <bdi className="bc-name">{m.name}</bdi>
                <span className="faint tabular" dir="ltr">
                  {m.version}
                </span>
                <Tag tone="accent">{t.added}</Tag>
              </li>
            ))}
            {removed.map((m) => (
              <li key={`r-${m.name}`}>
                <bdi className="bc-name">{m.name}</bdi>
                <span className="faint tabular" dir="ltr">
                  {m.version}
                </span>
                <Tag tone="caution">{t.removed}</Tag>
              </li>
            ))}
            {changed.map((m) => (
              <li key={`c-${m.name}`}>
                <bdi className="bc-name">{m.name}</bdi>
                {(m.from || m.to) && (
                  <span className="faint tabular" dir="ltr">
                    {m.from} → {m.to}
                  </span>
                )}
                <Tag tone={BUMP_TONE[m.bump]}>{bump[m.bump]}</Tag>
              </li>
            ))}
          </ul>
        ) : (
          <p className="small muted">{t.noModules}</p>
        )}
      </section>
      <section className="bc-section">
        <h3>{t.migrations}</h3>
        {data.migrations.length ? (
          <ul className="bc-modules">
            {data.migrations.map((m) => (
              <li key={m.module}>
                <bdi className="bc-name">{m.module}</bdi>
                <span className="faint tabular" dir="ltr">
                  {m.version}
                </span>
                <Tag tone="caution">{m.scripts.length}</Tag>
              </li>
            ))}
          </ul>
        ) : (
          <p className="small muted">{t.noMigrations}</p>
        )}
        <Migrations migrations={data.migrations} />
      </section>
      <section className="bc-section">
        <h3>{t.commits}</h3>
        {data.commits.length ? (
          <ol className="bc-commits">
            {data.commits.map((c) => (
              <li key={c.full_sha}>
                <span className="sha">{c.sha}</span>
                <bdi className="truncate">{c.subject}</bdi>
                <span className="faint xsmall nowrap">
                  {c.author}
                  {c.when ? ` · ${ago(c.when)}` : ""}
                </span>
              </li>
            ))}
          </ol>
        ) : (
          <p className="small muted">{t.noCommits}</p>
        )}
        {data.commits.length > 0 && data.ahead > data.commits.length && (
          <p className="small muted">{t.moreCommits(data.commits.length)}</p>
        )}
      </section>
    </div>
  );
}

/** Compare two branches of the project; `base` and `head` are where it starts. */
export function CompareDialog({
  project,
  base: firstBase,
  head: firstHead,
  onClose,
}: {
  project: ProjectDetail;
  base: string;
  head: string;
  onClose: () => void;
}) {
  const t = useT(words).compare;
  const c = useT(common).common;
  const [base, setBase] = useState(firstBase);
  const [head, setHead] = useState(firstHead);
  const query = useCompare(project.id, base, head);
  const names = project.branches_list.filter((b) => b.on_github !== false).map((b) => b.name);
  const picker = (value: string, set: (v: string) => void, label: string) => (
    <Field label={label}>
      <SelectBox dir="ltr" value={value} onValueChange={set} aria-label={label}>
        {names.map((n) => (
          <option key={n} value={n}>
            {n}
          </option>
        ))}
      </SelectBox>
    </Field>
  );
  const link =
    query.data?.github_url ??
    `https://github.com/${project.repository}/compare/${encodeURIComponent(base)}...${encodeURIComponent(head)}`;
  return (
    <Modal
      wide
      title={t.title}
      onClose={onClose}
      footer={
        <>
          <Button asChild icon={<ArrowUpRight />}>
            <a href={link} target="_blank" rel="noreferrer">
              {t.onGitHub}
            </a>
          </Button>
          <Button onClick={onClose}>{c.close}</Button>
        </>
      }
    >
      <div className="stack">
        <div className="bc-pickers">
          {picker(base, setBase, t.base)}
          <Button
            variant="ghost"
            icon={<ArrowLeftRight />}
            aria-label={t.swap}
            title={t.swap}
            onClick={() => {
              setBase(head);
              setHead(base);
            }}
          />
          {picker(head, setHead, t.head)}
        </div>
        {base === head ? (
          <Callout tone="info">{t.same}</Callout>
        ) : query.isPending ? (
          <div className="stack" aria-busy="true" aria-label={t.loading}>
            <Skeleton height={64} />
            <Skeleton height={120} />
          </div>
        ) : query.error ? (
          <Callout tone="warning">{errorText(query.error)}</Callout>
        ) : (
          <Result data={query.data} />
        )}
      </div>
    </Modal>
  );
}
