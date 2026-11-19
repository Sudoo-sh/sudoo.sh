// The Backups page: per instance, the backups on this server. Off-site copies (the premium off-site package) add
// their own section (slot "backups.offsite"): their status, checking, copying again, restoring and deleting.

import { Archive, Download, Trash2 } from "lucide-react";
import { useMemo } from "react";
import { api } from "@/api/client";
import { useBackups, useCall, useInstances } from "@/api/queries";
import type { Backup } from "@/api/types";
import { Page, PageHead } from "@/components/Shell";
import { Button, Empty, Skeleton, Tag } from "@/components/ui/basics";
import { useToast } from "@/components/ui/overlay";
import { useT } from "@/i18n";
import words from "@/i18n/operations";
import { ago, bytes, count, dateTime } from "@/lib/format";
import { PremiumSlot } from "@/premium/components";

function Details({ backup: b }: { backup: Backup }) {
  return (
    <span className="mono">
      {b.db} · {b.series ?? <span className="faint">?</span>}{b.edition === "enterprise" ? " Enterprise" : ""}
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

  const groups = useMemo(() => {
    const byInstance = new Map<string, Backup[]>();
    for (const backup of backups ?? []) byInstance.set(backup.instance, [...(byInstance.get(backup.instance) ?? []), backup]);
    return [...byInstance.entries()]
      .map(([name, list]) => ({ name, rows: list.sort((a, b) => b.created - a.created) }))
      .sort((a, b) => b.rows[0]!.created - a.rows[0]!.created);
  }, [backups]);
  const existing = new Set((instances ?? []).map((i) => i.name));

  return (
    <Page>
      <PageHead title={t.title}>
        {t.text}{backups?.length ? t.summary(count(backups.length, "backup"), bytes(total)) : ""}
      </PageHead>
      <PremiumSlot name="backups.offsite" />
      {isPending ? (
        <div className="card rise"><div className="card-body"><Skeleton height={140} /></div></div>
      ) : !groups.length ? (
        <div className="card rise"><Empty icon={<Archive />} title={t.none}>{t.noneText}</Empty></div>
      ) : (
        groups.map((group) => (
          <section className="card rise" key={group.name}>
            <div className="card-head">
              <div className="spacer">
                <h2><bdi>{group.name}</bdi>{!existing.has(group.name) && instances && <> <Tag>{t.instanceGone}</Tag></>}</h2>
                <div className="sub">{t.local(group.rows.length)}</div>
              </div>
            </div>
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>{t.kind}</th><th>{t.taken}</th><th>{t.size}</th><th>{t.details}</th><th /></tr></thead>
                <tbody>
                  {group.rows.map((backup) => (
                    <tr key={backup.file}>
                      <td><Tag tone={backup.kind === "daily" ? "accent" : undefined}>{t.kinds[backup.kind] ?? backup.kind}</Tag></td>
                      <td className="small nowrap">{dateTime(backup.created)} <span className="faint">· {ago(backup.created)}</span></td>
                      <td className="small tabular nowrap">{bytes(backup.size)}</td>
                      <td className="small"><Details backup={backup} /></td>
                      <td className="actions nowrap">
                        <a className="btn sm soft" href={`/api/v1/backups/${encodeURIComponent(backup.file)}`} download><Download /> {t.download}</a>{" "}
                        <Button size="sm" variant="ghost" icon={<Trash2 />} aria-label={t.delete} onClick={() => confirm(t.confirmDelete(backup.file)) && remove.mutate(backup.file, { onSuccess: () => toast("success", t.deleted) })} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        ))
      )}
    </Page>
  );
}
