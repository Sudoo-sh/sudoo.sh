import { Archive, Download, Trash2 } from "lucide-react";
import { api } from "@/api/client";
import { useBackups, useCall } from "@/api/queries";
import { Page, PageHead } from "@/components/Shell";
import { Button, Empty, Skeleton, Tag } from "@/components/ui/basics";
import { useToast } from "@/components/ui/overlay";
import { useT } from "@/i18n";
import words from "@/i18n/operations";
import { ago, bytes, count, dateTime } from "@/lib/format";

export function BackupsPage() {
  const { data: backups, isPending } = useBackups();
  const toast = useToast();
  const remove = useCall((file: string) => api.del(`/backups/${encodeURIComponent(file)}`), [["backups"]]);
  const total = backups?.reduce((sum, b) => sum + b.size, 0) ?? 0;
  const t = useT(words).backups;
  return (
    <Page>
      <PageHead title={t.title}>
        {t.text}{backups?.length ? t.summary(count(backups.length, "backup"), bytes(total)) : ""}
      </PageHead>
      <div className="card rise">
        {isPending ? <div className="card-body"><Skeleton height={140} /></div> : !backups?.length ? (
          <Empty icon={<Archive />} title={t.none}>{t.noneText}</Empty>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>{t.instance}</th><th>{t.database}</th><th>{t.odoo}</th><th>{t.kind}</th><th>{t.taken}</th><th>{t.size}</th><th /></tr></thead>
              <tbody>
                {backups.map((b) => (
                  <tr key={b.file}>
                    <td><bdi>{b.instance}</bdi></td>
                    <td className="small"><span className="mono">{b.db}</span></td>
                    <td className="small"><span className="mono">{b.series ?? <span className="faint">?</span>}{b.edition === "enterprise" ? " Enterprise" : ""}</span></td>
                    <td><Tag tone={b.kind === "daily" ? "accent" : undefined}>{t.kinds[b.kind] ?? b.kind}</Tag></td>
                    <td className="small">{dateTime(b.created)} <span className="faint">· {ago(b.created)}</span></td>
                    <td className="small tabular">{bytes(b.size)}</td>
                    <td className="actions">
                      <a className="btn sm soft" href={`/api/v1/backups/${encodeURIComponent(b.file)}`} download><Download /> {t.download}</a>{" "}
                      <Button size="sm" variant="ghost" icon={<Trash2 />} aria-label={t.delete} onClick={() => confirm(t.confirmDelete(b.file)) && remove.mutate(b.file, { onSuccess: () => toast("success", t.deleted) })} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </Page>
  );
}
