import { CircleCheck, CircleX, ScrollText, Search } from "lucide-react";
import { useState } from "react";
import { useAudit } from "@/api/queries";
import { Page, PageHead } from "@/components/Shell";
import { Tag, Button, Empty, Skeleton } from "@/components/app/basics";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Input } from "@/components/ui/input";
import { useT } from "@/i18n";
import words from "@/i18n/operations";
import supportWords from "@/i18n/premium";
import { ago, dateTime } from "@/lib/format";

export function AuditPage() {
  const [limit, setLimit] = useState(200);
  const [query, setQuery] = useState("");
  const [onlyRefused, setOnlyRefused] = useState(false);
  const { data: entries, isPending, isFetching } = useAudit(limit);
  const t = useT(words).audit;
  const sw = useT(supportWords);
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
          <Input placeholder={t.search} value={query} onChange={(e) => setQuery(e.target.value)} />
        </div>
        <Button variant={onlyRefused ? "soft" : "default"} icon={<CircleX />} onClick={() => setOnlyRefused(!onlyRefused)}>{t.refusedOnly}</Button>
      </div>
      <div className="card rise">
        {isPending ? <div className="card-body"><Skeleton height={300} /></div> : !shown.length ? (
          <Empty icon={<ScrollText />} title={t.nothing} />
        ) : (
          <Table>
            <TableHeader><TableRow><TableHead>{t.when}</TableHead><TableHead>{t.who}</TableHead><TableHead>{t.what}</TableHead><TableHead>{t.instance}</TableHead><TableHead>{t.address}</TableHead></TableRow></TableHeader>
            <TableBody>
              {shown.map((e, i) => (
                <TableRow key={`${e.ts}-${i}`}>
                  <TableCell className="small nowrap" title={dateTime(e.ts)}>{ago(e.ts)}</TableCell>
                  <TableCell className="small"><bdi>{e.user}</bdi>{e.support && <> <Tag tone="caution">{sw.supportTag}</Tag></>}</TableCell>
                  <TableCell>
                    <div className="row">
                      {e.ok ? <CircleCheck size={15} color="var(--positive)" aria-label={t.allowed} /> : <CircleX size={15} color="var(--negative)" aria-label={t.refused} />}
                      <bdi>{e.event}</bdi>
                    </div>
                    {e.detail && <div className="xsmall faint" dir="auto" style={{ marginInlineStart: 23 }}>{e.detail}</div>}
                  </TableCell>
                  <TableCell>{e.instance && <Tag><bdi>{e.instance}</bdi></Tag>}</TableCell>
                  <TableCell className="xsmall faint"><span className="mono">{e.ip}</span></TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
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
