import { CircleCheck, CircleX, ScrollText, Search } from "lucide-react";
import { useState } from "react";
import { useAudit } from "@/api/queries";
import { Page, PageHead } from "@/components/Shell";
import { Badge, Button, Empty, Skeleton } from "@/components/ui/basics";
import { ago, dateTime } from "@/lib/format";

export function AuditPage() {
  const [limit, setLimit] = useState(200);
  const [query, setQuery] = useState("");
  const [onlyRefused, setOnlyRefused] = useState(false);
  const { data: entries, isPending, isFetching } = useAudit(limit);
  const shown = (entries ?? []).filter(
    (e) => (!onlyRefused || !e.ok) && (!query || `${e.user} ${e.event} ${e.instance ?? ""} ${e.ip ?? ""} ${e.detail ?? ""}`.toLowerCase().includes(query.toLowerCase())),
  );
  return (
    <Page>
      <PageHead eyebrow="Operations" title="Audit log">
        Who did what, from where, and whether it was allowed: sign-ins, refusals, every action on an instance, account and key changes.
      </PageHead>
      <div className="toolbar rise">
        <div className="input-group">
          <Search />
          <input className="input" placeholder="Search user, event, instance, address…" value={query} onChange={(e) => setQuery(e.target.value)} />
        </div>
        <Button variant={onlyRefused ? "soft" : "default"} icon={<CircleX />} onClick={() => setOnlyRefused(!onlyRefused)}>Refused only</Button>
      </div>
      <div className="card rise">
        {isPending ? <div className="card-body"><Skeleton height={300} /></div> : !shown.length ? (
          <Empty icon={<ScrollText />} title="Nothing to show" />
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>When</th><th>Who</th><th>What</th><th>Instance</th><th>Address</th></tr></thead>
              <tbody>
                {shown.map((e, i) => (
                  <tr key={`${e.ts}-${i}`}>
                    <td className="small nowrap" title={dateTime(e.ts)}>{ago(e.ts)}</td>
                    <td className="small"><b>{e.user}</b></td>
                    <td>
                      <div className="row">
                        {e.ok ? <CircleCheck size={15} color="var(--success)" /> : <CircleX size={15} color="var(--danger)" />}
                        <span>{e.event}</span>
                      </div>
                      {e.detail && <div className="xsmall faint" style={{ marginLeft: 23 }}>{e.detail}</div>}
                    </td>
                    <td>{e.instance && <Badge>{e.instance}</Badge>}</td>
                    <td className="mono xsmall faint">{e.ip}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {entries && entries.length >= limit && (
          <div className="card-foot" style={{ textAlign: "center" }}>
            <Button loading={isFetching} onClick={() => setLimit(limit + 300)}>Show older entries</Button>
          </div>
        )}
      </div>
    </Page>
  );
}
