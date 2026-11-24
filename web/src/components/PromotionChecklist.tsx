// The pre-promotion checklist (GET /projects/<id>/branches/<branch>/promotion-check, web/v1/promotion.py): what to know
// before a branch goes into production or is merged into it. Each item is green, amber or red, with the server's
// explanation; red items stop a move into production unless someone who manages the project promotes anyway. Shown
// inside the stage dialog (BranchDialogs.tsx) and, read-only, from a staging branch's menu (PromotionDialog).

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleAlert, CircleCheck, CircleX, GitCompareArrows, HardDriveDownload, RefreshCw } from "lucide-react";
import { api, errorText } from "@/api/client";
import { useCall } from "@/api/queries";
import type {
  CheckItem,
  CheckStatus,
  Job,
  ModuleChanges,
  PendingMigration,
  ProjectBranch,
  ProjectDetail,
  PromotionCheck,
} from "@/api/types";
import { useCan } from "@/components/Shell";
import { Button, Callout, Skeleton, Tag } from "@/components/app/basics";
import { Modal, useToast } from "@/components/app/overlay";
import { useT } from "@/i18n";
import common from "@/i18n/common";
import words from "@/i18n/promotion";

/** A branch name in a path: each segment encoded, the "/" between them kept (the server reads it as one path). */
export const branchSegment = (branch: string) => branch.split("/").map(encodeURIComponent).join("/");

export const promotionKey = (project: string, branch: string, merge = false) =>
  ["project", project, "promotion-check", branch, merge ? "merge" : "move"] as const;
const checkPath = (project: string, branch: string, merge: boolean, refresh = false) => {
  const query = [merge ? "mode=merge" : "", refresh ? "refresh=1" : ""].filter(Boolean).join("&");
  return `/projects/${encodeURIComponent(project)}/branches/${branchSegment(branch)}/promotion-check${query ? `?${query}` : ""}`;
};

/** The checklist; `merge`: the branch is merged into production's branch on GitHub (no capacity item). */
export function usePromotionCheck(project: string, branch: string, enabled = true, merge = false) {
  return useQuery({
    queryKey: promotionKey(project, branch, merge),
    queryFn: () => api.get<PromotionCheck>(checkPath(project, branch, merge)),
    enabled,
    staleTime: 15_000,
  });
}

const ICON: Record<CheckStatus, typeof CircleCheck> = { ok: CircleCheck, warning: CircleAlert, blocked: CircleX };

function Modules({ modules }: { modules: ModuleChanges }) {
  const t = useT(words).check;
  const groups: [string, string[]][] = [
    [t.added, modules.added.map((m) => `${m.name} ${m.version}`.trim())],
    [t.removed, modules.removed.map((m) => `${m.name} ${m.version}`.trim())],
    [
      t.changed,
      modules.changed
        .filter((c) => c.bump === "up" || c.bump === "down" || c.bump === "unknown")
        .map((c) => (c.from || c.to ? `${c.name} ${c.from} → ${c.to}` : c.name)),
    ],
    [t.sameVersion, modules.changed.filter((c) => c.bump === "same").map((c) => c.name)],
  ];
  const shown = groups.filter(([, list]) => list.length);
  if (!shown.length) return null;
  return (
    <details className="pc-details">
      <summary>{t.details}</summary>
      <dl>
        {shown.map(([label, list]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd dir="ltr">
              {list.map((entry) => (
                <code key={entry}>{entry}</code>
              ))}
            </dd>
          </div>
        ))}
      </dl>
    </details>
  );
}

export function Migrations({ migrations }: { migrations: PendingMigration[] }) {
  const t = useT(words).check;
  if (!migrations.length) return null;
  return (
    <details className="pc-details">
      <summary>{t.details}</summary>
      <dl>
        {migrations.map((m) => (
          <div key={m.module}>
            <dt dir="ltr">
              <code>
                {m.module} {m.version}
              </code>{" "}
              <span className="faint">({t.scripts(m.scripts.length)})</span>
            </dt>
            <dd dir="ltr">
              {m.scripts.map((s) => (
                <code key={s}>{s}</code>
              ))}
            </dd>
          </div>
        ))}
      </dl>
    </details>
  );
}

function BackUpNow({ item, onStarted }: { item: CheckItem; onStarted: () => void }) {
  const t = useT(words).check;
  const toast = useToast();
  const backup = useCall(() => api.post<Job>(`/instances/${item.instance}/actions/backup`, {}));
  return (
    <Button
      size="sm"
      icon={<HardDriveDownload />}
      loading={backup.isPending}
      disabled={item.busy}
      onClick={() =>
        backup.mutate(undefined, {
          onSuccess: () => {
            toast("success", t.backingUp, t.backingUpText);
            onStarted();
          },
          onError: (e) => toast("error", t.notBackedUp, errorText(e)),
        })
      }
    >
      {t.backUpNow}
    </Button>
  );
}

function Item({ item, onBackup }: { item: CheckItem; onBackup: () => void }) {
  const t = useT(words).check;
  const can = useCan();
  const Icon = ICON[item.status];
  const canBackUp = item.key === "backup" && item.instance && item.status !== "ok" && can("instances.operate");
  return (
    <li className="pc-item" data-status={item.status}>
      <Icon className="pc-icon" aria-hidden />
      <div className="pc-body">
        <div className="pc-title">
          <span>{t.items[item.key]}</span>
          <span className="sr-only">: {t.status[item.status]}</span>
        </div>
        <p className="pc-text">{item.text}</p>
        {item.key === "backup" && item.busy && <p className="pc-text faint">{t.busy}</p>}
        {canBackUp && (
          <div className="pc-actions">
            <BackUpNow item={item} onStarted={onBackup} />
          </div>
        )}
        {item.key === "ci" && item.ci?.url && (
          <p className="pc-text">
            <a href={item.ci.url} target="_blank" rel="noreferrer">
              {t.openChecks}
            </a>
          </p>
        )}
        {item.key === "modules" && item.modules && <Modules modules={item.modules} />}
        {item.key === "migrations" && item.migrations && <Migrations migrations={item.migrations} />}
      </div>
    </li>
  );
}

/** The checklist of `branch` going into production; `onCompare` opens the comparison with production's branch. */
export function PromotionChecklist({
  project,
  branch,
  onCompare,
  merge = false,
}: {
  project: ProjectDetail;
  branch: ProjectBranch;
  onCompare?: (base: string) => void;
  merge?: boolean;
}) {
  const t = useT(words).check;
  const client = useQueryClient();
  const query = usePromotionCheck(project.id, branch.name, true, merge);
  const again = async () => {
    const fresh = await api.get<PromotionCheck>(checkPath(project.id, branch.name, merge, true));
    client.setQueryData(promotionKey(project.id, branch.name, merge), fresh);
  };
  if (query.isPending) {
    return (
      <section className="pc" aria-busy="true" aria-label={t.loading}>
        <Skeleton height={18} width="45%" />
        <Skeleton height={44} />
        <Skeleton height={44} />
        <Skeleton height={44} />
      </section>
    );
  }
  if (query.error)
    return (
      <Callout tone="warning">
        {t.failed}: {errorText(query.error)}
      </Callout>
    );
  const check = query.data;
  const red = check.items.filter((i) => i.status === "blocked").length;
  const amber = check.items.filter((i) => i.status === "warning").length;
  const tone = red ? "negative" : amber ? "caution" : "positive";
  const base = check.production.branch;
  return (
    <section className="pc" aria-label={t.title}>
      <div className="pc-head">
        <div className="spacer">
          <h3>{t.title}</h3>
          <Tag tone={tone}>{red ? t.blocked(red) : amber ? t.attention(amber) : t.ready}</Tag>
        </div>
        {onCompare && base && base !== branch.name && (
          <Button size="sm" variant="ghost" icon={<GitCompareArrows />} onClick={() => onCompare(base)}>
            {t.compare}
          </Button>
        )}
        <Button
          size="sm"
          variant="ghost"
          icon={<RefreshCw />}
          aria-label={t.refresh}
          title={t.refresh}
          onClick={() => void again()}
        />
      </div>
      <p className="small muted">{base && base !== branch.name ? <>{t.against(base)}</> : t.noProduction}</p>
      <ul className="pc-list">
        {check.items.map((item) => (
          <Item
            key={item.key}
            item={item}
            onBackup={() => void client.invalidateQueries({ queryKey: promotionKey(project.id, branch.name, merge) })}
          />
        ))}
      </ul>
      {red > 0 && !check.blocking && <p className="small muted">{t.blockedNotEnforced}</p>}
    </section>
  );
}

/** The checklist on its own, for a staging branch about to be merged into production on GitHub. */
export function PromotionDialog({
  project,
  branch,
  onClose,
  onCompare,
}: {
  project: ProjectDetail;
  branch: ProjectBranch;
  onClose: () => void;
  onCompare: (base: string) => void;
}) {
  const t = useT(words).check;
  const c = useT(common).common;
  return (
    <Modal
      wide
      title={t.readOnlyTitle(branch.name)}
      description={t.readOnlyText}
      onClose={onClose}
      footer={<Button onClick={onClose}>{c.close}</Button>}
    >
      <PromotionChecklist project={project} branch={branch} onCompare={onCompare} merge />
    </Modal>
  );
}
