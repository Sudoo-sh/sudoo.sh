import { Archive, Download, Trash2 } from "lucide-react";
import { api } from "@/api/client";
import { useBackups, useCall } from "@/api/queries";
import { Page, PageHead } from "@/components/Shell";
import { Button, Empty, Skeleton } from "@/components/ui/basics";
import { useToast } from "@/components/ui/overlay";
import { ago, bytes, dateTime } from "@/lib/format";

export function BackupsPage() {
  const { data: backups, isPending } = useBackups();
  const toast = useToast();
  const remove = useCall((file: string) => api.del(`/backups/${encodeURIComponent(file)}`), [["backups"]]);
  const total = backups?.reduce((sum, b) => sum + b.size, 0) ?? 0;
  return (
    <Page>
      <PageHead eyebrow="Operations" title="Backups">
        Odoo-format zips (database and files), taken before every deletion and on request. Restore one with Odoo's database manager. {backups?.length ? `${backups.length} backups · ${bytes(total)}` : ""}
      </PageHead>
      <div className="card rise">
        {isPending ? <div className="card-body"><Skeleton height={140} /></div> : !backups?.length ? (
          <Empty icon={<Archive />} title="No backups yet">An instance is backed up before it is deleted, or with Back up now on its page.</Empty>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Instance</th><th>Database</th><th>Taken</th><th>Size</th><th /></tr></thead>
              <tbody>
                {backups.map((b) => (
                  <tr key={b.file}>
                    <td style={{ fontWeight: 600 }}>{b.instance}</td>
                    <td className="mono small">{b.db}</td>
                    <td className="small"><span title={dateTime(b.created)}>{ago(b.created)}</span></td>
                    <td className="small tabular">{bytes(b.size)}</td>
                    <td className="actions">
                      <a className="btn sm soft" href={`/api/v1/backups/${encodeURIComponent(b.file)}`} download><Download /> Download</a>{" "}
                      <Button size="sm" variant="ghost" icon={<Trash2 />} aria-label="Delete" onClick={() => confirm(`Delete ${b.file}? This cannot be undone.`) && remove.mutate(b.file, { onSuccess: () => toast("success", "Backup deleted") })} />
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
