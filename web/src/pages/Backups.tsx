// The Backups page: per instance, the backups on this server. Off-site copies (the premium off-site package) add
// their own section (slot "backups.offsite"): their status, checking, copying again, restoring and deleting.

import { Archive, Download, Trash2 } from "lucide-react";
import { useMemo } from "react";
import { api } from "@/api/client";
import { useBackups, useCall, useInstances } from "@/api/queries";
import type { Backup } from "@/api/types";
import { Page, PageHead } from "@/components/Shell";
import { Button, Empty, Skeleton, Tag } from "@/components/app/basics";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useConfirm } from "@/components/app/confirm";
import { useToast } from "@/components/app/overlay";
import { useT } from "@/i18n";
import words from "@/i18n/operations";
import { ago, bytes, count, dateTime } from "@/lib/format";
import { BACKUPS_TOUR } from "@/lib/tour";
import { PremiumSlot } from "@/premium/components";

function Details({ backup: b }: { backup: Backup }) {
  return (
    <span className="mono">
      {b.db} · {b.series ?? <span className="faint">?</span>}
      {b.edition === "enterprise" ? " Enterprise" : ""}
    </span>
  );
}

export function BackupsPage() {
  const { data: backups, isPending } = useBackups();
  const toast = useToast();
  const remove = useCall((file: string) => api.del(`/backups/${encodeURIComponent(file)}`), [["backups"]]);
  const { data: instances } = useInstances();
  const total = backups?.reduce((sum, b) => sum + b.size, 0) ?? 0;
  const t = useT(words).backups;
  const [confirm, askDialog] = useConfirm();

  const groups = useMemo(() => {
    const byInstance = new Map<string, Backup[]>();
    for (const backup of backups ?? [])
      byInstance.set(backup.instance, [...(byInstance.get(backup.instance) ?? []), backup]);
    return [...byInstance.entries()]
      .map(([name, list]) => ({ name, rows: list.sort((a, b) => b.created - a.created) }))
      .sort((a, b) => b.rows[0]!.created - a.rows[0]!.created);
  }, [backups]);
  const existing = new Set((instances ?? []).map((i) => i.name));

  return (
    <Page>
      {askDialog}
      <PageHead title={t.title} tour={BACKUPS_TOUR}>
        {t.text}
        {backups?.length ? t.summary(count(backups.length, "backup"), bytes(total)) : ""}
      </PageHead>
      <PremiumSlot name="backups.offsite" />
      {isPending ? (
        <div className="card rise">
          <div className="card-body">
            <Skeleton height={140} />
          </div>
        </div>
      ) : !groups.length ? (
        <div className="card rise">
          <Empty icon={<Archive />} title={t.none}>
            {t.noneText}
          </Empty>
        </div>
      ) : (
        groups.map((group, groupIndex) => (
          <section className="card rise" key={group.name}>
            <div className="card-head" data-tour={groupIndex === 0 ? "backups-list" : undefined}>
              <div className="spacer">
                <h2>
                  <bdi>{group.name}</bdi>
                  {!existing.has(group.name) && instances && (
                    <>
                      {" "}
                      <Tag>{t.instanceGone}</Tag>
                    </>
                  )}
                </h2>
                <div className="sub">{t.local(group.rows.length)}</div>
              </div>
            </div>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t.kind}</TableHead>
                  <TableHead>{t.taken}</TableHead>
                  <TableHead>{t.size}</TableHead>
                  <TableHead>{t.details}</TableHead>
                  <TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {group.rows.map((backup, rowIndex) => (
                  <TableRow key={backup.file}>
                    <TableCell>
                      <Tag tone={backup.kind === "daily" ? "accent" : undefined}>
                        {t.kinds[backup.kind] ?? backup.kind}
                      </Tag>
                    </TableCell>
                    <TableCell className="small nowrap">
                      {dateTime(backup.created)} <span className="faint">· {ago(backup.created)}</span>
                    </TableCell>
                    <TableCell className="small tabular nowrap">{bytes(backup.size)}</TableCell>
                    <TableCell className="small">
                      <Details backup={backup} />
                    </TableCell>
                    <TableCell
                      className="actions nowrap"
                      data-tour={groupIndex === 0 && rowIndex === 0 ? "backups-actions" : undefined}
                    >
                      <Button asChild variant="soft" size="sm">
                        <a href={`/api/v1/backups/${encodeURIComponent(backup.file)}`} download>
                          <Download /> {t.download}
                        </a>
                      </Button>{" "}
                      <Button
                        size="sm"
                        variant="ghost"
                        icon={<Trash2 />}
                        aria-label={t.delete}
                        onClick={() =>
                          void confirm(t.confirmDelete(backup.file), { action: t.delete, danger: true }).then((yes) => {
                            if (yes) remove.mutate(backup.file, { onSuccess: () => toast("success", t.deleted) });
                          })
                        }
                      />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </section>
        ))
      )}
    </Page>
  );
}
