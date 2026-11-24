// A project's Backups tab: the backups of all its branches (GET /backups?project=<id>), grouped by branch, newest
// first, with what the Backups page offers for each one (download, delete) and Restore into the instance it was taken
// from. What the caller may do is the branch's own permissions (role, project access and the branch's lock).

import { useQuery } from "@tanstack/react-query";
import { Archive, Download, GitBranch, History, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router";
import { api, errorText } from "@/api/client";
import { keys, useCall, useInstances } from "@/api/queries";
import type { Backup, Instance, Job, Permission, ProjectBranch, ProjectDetail } from "@/api/types";
import { useT } from "@/i18n";
import common from "@/i18n/common";
import instanceWords from "@/i18n/instance";
import operations from "@/i18n/operations";
import words from "@/i18n/projects";
import { ago, bytes, count, dateTime } from "@/lib/format";
import { Button, Callout, Empty, Skeleton, Tag } from "./app/basics";
import { Modal, useToast } from "./app/overlay";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "./ui/table";
import { JobDrawer } from "./JobDrawer";
import { useCan } from "./Session";
import { STAGE_ORDER, StageTag } from "./StageTag";

/** The backups of a project's instances (GET /backups?project=<id>), for those who may manage backups. */
export function useProjectBackups(projectId: string, enabled = true) {
  return useQuery({
    queryKey: ["backups", { project: projectId }],
    queryFn: () => api.get<Backup[]>(`/backups?project=${encodeURIComponent(projectId)}`),
    enabled,
  });
}

/** What the caller may do in a branch: its own permissions when the server sent them, else the role's. */
function useCanInBranch(branch: ProjectBranch) {
  const roleCan = useCan();
  return (permission: Permission) =>
    branch.permissions ? branch.permissions.includes(permission) : roleCan(permission);
}

export function ProjectBackups({ project }: { project: ProjectDetail }) {
  const { data: backups, isPending } = useProjectBackups(project.id);
  const { data: instances } = useInstances();
  const [jobId, setJobId] = useState<string | null>(null);
  const t = useT(words).backups;
  const o = useT(operations).backups;

  // each branch with its backups, newest first; the branches with the newest backups first within each stage
  const groups = useMemo(() => {
    const list = backups ?? [];
    return project.branches_list
      .map((branch) => ({
        branch,
        rows: list.filter((b) => branch.instances.includes(b.instance)).sort((a, b) => b.created - a.created),
      }))
      .filter((group) => group.rows.length)
      .sort(
        (a, b) => STAGE_ORDER[a.branch.stage] - STAGE_ORDER[b.branch.stage] || b.rows[0]!.created - a.rows[0]!.created,
      );
  }, [backups, project]);
  const byName = useMemo(() => new Map((instances ?? []).map((i) => [i.name, i])), [instances]);
  const total = groups.reduce((sum, g) => sum + g.rows.reduce((s, b) => s + b.size, 0), 0);
  const shown = groups.reduce((sum, g) => sum + g.rows.length, 0);

  return (
    <div className="stack tab-panel">
      <p className="small muted">
        {t.text}
        {shown ? o.summary(count(shown, "backup"), bytes(total)) : ""}
      </p>
      {isPending ? (
        <div className="card">
          <div className="card-body">
            <Skeleton height={140} />
          </div>
        </div>
      ) : !groups.length ? (
        <div className="card">
          <Empty icon={<Archive />} title={t.none}>
            {t.noneText}
          </Empty>
        </div>
      ) : (
        groups.map(({ branch, rows }) => (
          <BranchBackups key={branch.name} branch={branch} rows={rows} instances={byName} onJob={setJobId} />
        ))
      )}
      {jobId && <JobDrawer id={jobId} onClose={() => setJobId(null)} />}
    </div>
  );
}

function BranchBackups({
  branch,
  rows,
  instances,
  onJob,
}: {
  branch: ProjectBranch;
  rows: Backup[];
  instances: Map<string, Instance>;
  onJob: (id: string) => void;
}) {
  const can = useCanInBranch(branch);
  const toast = useToast();
  const o = useT(operations).backups;
  const t = useT(words).backups;
  const remove = useCall((file: string) => api.del(`/backups/${encodeURIComponent(file)}`), [["backups"]]);
  const [restoring, setRestoring] = useState<{ backup: Backup; instance: Instance } | null>(null);
  const several = branch.instances.length > 1;
  return (
    <section className="card" aria-label={branch.name}>
      <div className="card-head">
        <GitBranch />
        <div className="spacer">
          <h2 className="row-wrap" style={{ gap: 8 }}>
            <bdi dir="ltr">{branch.name}</bdi>
            <StageTag stage={branch.stage} />
          </h2>
          <div className="sub">
            {branch.instances.length
              ? branch.instances.map((name, i) => (
                  <span key={name}>
                    {i > 0 && " · "}
                    <Link to={`/i/${name}/backups`} viewTransition>
                      <bdi>{name}</bdi>
                    </Link>
                  </span>
                ))
              : t.noInstance}
            {" · "}
            {o.local(rows.length)}
          </div>
        </div>
      </div>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>{o.kind}</TableHead>
            <TableHead>{o.taken}</TableHead>
            {several && <TableHead>{t.instance}</TableHead>}
            <TableHead>{o.size}</TableHead>
            <TableHead>{o.details}</TableHead>
            <TableHead />
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((backup) => {
            const instance = instances.get(backup.instance);
            return (
              <TableRow key={backup.file}>
                <TableCell>
                  <Tag tone={backup.kind === "daily" ? "accent" : undefined}>{o.kinds[backup.kind] ?? backup.kind}</Tag>
                </TableCell>
                <TableCell className="small nowrap">
                  {dateTime(backup.created)} <span className="faint">· {ago(backup.created)}</span>
                </TableCell>
                {several && (
                  <TableCell className="small">
                    <bdi>{backup.instance}</bdi>
                  </TableCell>
                )}
                <TableCell className="small tabular nowrap">{bytes(backup.size)}</TableCell>
                <TableCell className="small">
                  <span className="mono">
                    {backup.db} · {backup.series ?? <span className="faint">?</span>}
                    {backup.edition === "enterprise" ? " Enterprise" : ""}
                  </span>
                </TableCell>
                <TableCell className="actions nowrap">
                  {can("backups.manage") && (
                    <Button asChild variant="soft" size="sm">
                      <a href={`/api/v1/backups/${encodeURIComponent(backup.file)}`} download>
                        <Download /> {o.download}
                      </a>
                    </Button>
                  )}{" "}
                  {can("instances.restore") && instance && (
                    <Button
                      size="sm"
                      variant="ghost"
                      icon={<History />}
                      disabled={!!instance.current_job}
                      onClick={() => setRestoring({ backup, instance })}
                    >
                      {o.restore}
                    </Button>
                  )}{" "}
                  {can("backups.manage") && (
                    <Button
                      size="sm"
                      variant="ghost"
                      icon={<Trash2 />}
                      aria-label={o.delete}
                      onClick={() =>
                        confirm(o.confirmDelete(backup.file)) &&
                        remove.mutate(backup.file, {
                          onSuccess: () => toast("success", o.deleted),
                          onError: (e) => toast("error", o.delete, errorText(e)),
                        })
                      }
                    />
                  )}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
      {restoring && (
        <RestoreDialog
          backup={restoring.backup}
          instance={restoring.instance}
          onClose={() => setRestoring(null)}
          onJob={onJob}
        />
      )}
    </section>
  );
}

/** Restoring a backup into the instance it was taken from, asked first (as on the instance's own page). */
function RestoreDialog({
  backup,
  instance,
  onClose,
  onJob,
}: {
  backup: Backup;
  instance: Instance;
  onClose: () => void;
  onJob: (id: string) => void;
}) {
  const all = useT(instanceWords);
  const t = all.backups;
  const o = useT(operations).backups;
  const c = useT(common).common;
  const toast = useToast();
  const production = instance.stage === "production";
  const restore = useCall(
    () =>
      api.post<Job>(`/instances/${instance.name}/actions/restore`, {
        file: backup.file,
        ...(production ? { confirm_production: true } : {}),
      }),
    [keys.instance(instance.name), keys.jobs(instance.name), ["backups"]],
  );
  const when = dateTime(backup.created);
  return (
    <Modal
      title={t.restoreTitle(when)}
      description={`${o.kinds[backup.kind] ?? backup.kind} · ${bytes(backup.size)}`}
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>{c.cancel}</Button>
          <Button
            variant="danger"
            loading={restore.isPending}
            onClick={() =>
              restore.mutate(undefined, {
                onSuccess: (job) => {
                  toast("success", all.run.started(all.jobs.restore), all.run.startedText);
                  onClose();
                  onJob(job.id);
                },
                onError: (e) => toast("error", all.run.notStarted(all.jobs.restore), errorText(e)),
              })
            }
          >
            {t.restore}
          </Button>
        </>
      }
    >
      <div className="stack">
        <Callout tone="warning">
          {production && (
            <>
              <b>{t.productionWarning}</b>{" "}
            </>
          )}
          {t.restoreText(
            <b key="db" className="mono">
              {backup.db}
            </b>,
            when,
          )}
        </Callout>
        <p className="small muted">
          {t.restoreAfter(<b key="r">{t.rollbackWord}</b>, <bdi key="n">{instance.name}</bdi>)}
        </p>
      </div>
    </Modal>
  );
}
