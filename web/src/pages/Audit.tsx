import { CircleCheck, CircleX, ScrollText, Search } from "lucide-react";
import { useState } from "react";
import { useAudit } from "@/api/queries";
import { Page, PageHead } from "@/components/Shell";
import { Tag, Button, Empty, Skeleton } from "@/components/ui/basics";
import { useT } from "@/i18n";
import words from "@/i18n/operations";
import { ago, dateTime } from "@/lib/format";

export function AuditPage() {
  const [limit, setLimit] = useState(200);
  const [query, setQuery] = useState("");
  const [onlyRefused, setOnlyRefused] = useState(false);
  const { data: entries, isPending, isFetching } = useAudit(limit);
  const t = useT(words).audit;
  const shown = (entries ?? []).filter(
    (e) => (!onlyRefused || !e.ok) && (!query || `${e.user} ${e.event} ${e.instance ?? ""} ${e.ip ?? ""} ${e.detail ?? ""}`.toLowerCase().includes(query.toLowerCase())),
  );
  return (
    <Page>
      <PageHead title={t.title}>
        {t.text}
      </PageHead>
      <div className="toolbar rise">
        <div className="input-group">
          <Search />
          <input className="input" placeholder={t.search} value={query} onChange={(e) => setQuery(e.target.value)} />
        </div>
        <Button variant={onlyRefused ? "soft" : "default"} icon={<CircleX />} onClick={() => setOnlyRefused(!onlyRefused)}>{t.refusedOnly}</Button>
      </div>
      <div className="card rise">
        {isPending ? <div className="card-body"><Skeleton height={300} /></div> : !shown.length ? (
          <Empty icon={<ScrollText />} title={t.nothing} />
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>{t.when}</th><th>{t.who}</th><th>{t.what}</th><th>{t.instance}</th><th>{t.address}</th></tr></thead>
              <tbody>
                {shown.map((e, i) => (
                  <tr key={`${e.ts}-${i}`}>
                    <td className="small nowrap" title={dateTime(e.ts)}>{ago(e.ts)}</td>
                    <td className="small"><bdi>{e.user}</bdi></td>
                    <td>
                      <div className="row">
                        {e.ok ? <CircleCheck size={15} color="var(--positive)" aria-label={t.allowed} /> : <CircleX size={15} color="var(--negative)" aria-label={t.refused} />}
                        <bdi>{e.event}</bdi>
                      </div>
                      {e.detail && <div className="xsmall faint" dir="auto" style={{ marginInlineStart: 23 }}>{e.detail}</div>}
                    </td>
                    <td>{e.instance && <Tag><bdi>{e.instance}</bdi></Tag>}</td>
                    <td className="xsmall faint"><span className="mono">{e.ip}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {entries && entries.length >= limit && (
          <div className="card-foot" style={{ textAlign: "center" }}>
            <Button loading={isFetching} onClick={() => setLimit(limit + 300)}>{t.older}</Button>
          </div>
        )}
      </div>
    </Page>
  );
}
