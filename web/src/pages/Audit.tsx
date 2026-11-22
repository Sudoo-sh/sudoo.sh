// The audit log: who did what, from where, and whether it was allowed. Filters (project and branch, instance, user,
// action, result, dates, text) live in the address, so a project's link (/audit?project=<id>) opens it filtered;
// the server filters, sorts and pages (GET /audit), and exports what the filters show as CSV or XLSX.

import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight, CircleCheck, CircleX, Download, FilterX, ScrollText, Search } from "lucide-react";
import { useEffect, useId, useMemo, useState } from "react";
import { useSearchParams } from "react-router";
import { useAudit, useAuditFacets } from "@/api/queries";
import { Page, PageHead } from "@/components/Shell";
import { Tag, Button, Empty, Field, Segmented, Skeleton } from "@/components/app/basics";
import { SelectBox } from "@/components/app/select";
import { Menu, MenuItem } from "@/components/app/overlay";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Input } from "@/components/ui/input";
import { useT } from "@/i18n";
import words from "@/i18n/operations";
import supportWords from "@/i18n/premium";
import { ago, dateTime, number } from "@/lib/format";

const FILTERS = ["q", "project", "branch", "instance", "user", "event", "ok", "since", "until"] as const;
const SIZES = ["50", "100", "200"];
type Sort = "ts" | "user" | "event" | "instance" | "ip";

/** A date input's value ("2026-11-22") as the start of that day, or the start of the next one, in local time. */
const dayStart = (value: string, next = false) => {
  const [y, m, d] = value.split("-").map(Number);
  return y && m && d ? new Date(y, m - 1, d + (next ? 1 : 0)).toISOString() : "";
};

function useDebounced<T>(value: T, delay = 300) {
  const [shown, setShown] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setShown(value), delay);
    return () => window.clearTimeout(timer);
  }, [value, delay]);
  return shown;
}

export function AuditPage() {
  const t = useT(words).audit;
  const searchId = useId();
  const sw = useT(supportWords);
  const [params, setParams] = useSearchParams();
  const get = (key: string) => params.get(key) ?? "";
  const set = (changes: Record<string, string>) =>
    setParams((current) => {
      const next = new URLSearchParams(current);
      for (const [key, value] of Object.entries(changes)) {
        if (value) next.set(key, value);
        else next.delete(key);
      }
      if (!("page" in changes)) next.delete("page");
      return next;
    }, { replace: true });
  const [text, setText] = useState(get("q"));
  const query = useDebounced(text);
  useEffect(() => {
    if (query !== get("q")) set({ q: query });
    // the address follows the search box once typing stops
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query]);

  const size = SIZES.includes(get("size")) ? get("size") : "50";
  const page = Math.max(1, Number(get("page")) || 1);
  const sort = (get("sort") || "-ts") as `${"-" | ""}${Sort}`;
  const column = sort.replace("-", "") as Sort;
  const descending = sort.startsWith("-");

  const request = useMemo(() => {
    const found = new URLSearchParams();
    for (const key of FILTERS) {
      const value = params.get(key);
      if (!value) continue;
      if (key === "since") found.set(key, dayStart(value));
      else if (key === "until") found.set(key, dayStart(value, true));
      else found.set(key, value);
    }
    if (!found.get("project")) found.delete("branch");
    return found;
  }, [params]);
  const listing = new URLSearchParams(request);
  listing.set("limit", size);
  listing.set("offset", String((page - 1) * Number(size)));
  listing.set("sort", sort);
  const { data, isPending, isFetching } = useAudit(listing.toString());
  const { data: facets } = useAuditFacets();
  const total = data?.total ?? 0;
  const entries = data?.entries ?? [];
  const first = total ? (page - 1) * Number(size) + 1 : 0;
  const last = Math.min(total, page * Number(size));
  const exported = new URLSearchParams(request);
  exported.set("sort", sort);
  const exportUrl = (format: "csv" | "xlsx") => `/api/v1/audit/export?format=${format}&${exported.toString()}`;
  const projectName = facets?.projects.find((p) => p.id === get("project"))?.name ?? get("project");
  const filtered = FILTERS.some((key) => params.get(key));

  const head = (key: Sort, label: string) => (
    <TableHead aria-sort={column === key ? (descending ? "descending" : "ascending") : undefined}>
      <button type="button" className="sort" title={t.sortBy(label)} onClick={() => set({ sort: column === key && descending ? key : `-${key}` })}>
        {label}
        {column === key && (descending ? <ArrowDown size={13} /> : <ArrowUp size={13} />)}
      </button>
    </TableHead>
  );

  return (
    <Page>
      <PageHead
        title={t.title}
        actions={
          <Menu trigger={(open) => <Button icon={<Download />} onClick={open}>{t.export}</Button>}>
            {() => (
              <>
                <MenuItem asChild><a href={exportUrl("csv")} download>{t.csv}</a></MenuItem>
                <MenuItem asChild><a href={exportUrl("xlsx")} download>{t.xlsx}</a></MenuItem>
              </>
            )}
          </Menu>
        }
      >
        {get("project") ? t.forProject(projectName) : t.text}
      </PageHead>

      <section className="card rise">
        <div className="card-body stack">
          <div className="audit-filters">
            <Field label={t.filters} className="wide" htmlFor={searchId}>
              <div className="input-group">
                <Search />
                <Input id={searchId} placeholder={t.search} value={text} onChange={(e) => setText(e.target.value)} />
              </div>
            </Field>
            <Field label={t.project}>
              <SelectBox value={get("project")} onValueChange={(v) => set({ project: v, branch: "" })}>
                <option value="">{t.anyProject}</option>
                {(facets?.projects ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                {get("project") && !facets?.projects.some((p) => p.id === get("project")) && <option value={get("project")}>{get("project")}</option>}
              </SelectBox>
            </Field>
            <Field label={t.branch} hint={t.branchHint}>
              <Input dir="ltr" disabled={!get("project")} defaultValue={get("branch")} key={`branch-${get("project")}`}
                onBlur={(e) => e.target.value.trim() !== get("branch") && set({ branch: e.target.value.trim() })}
                onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()} />
            </Field>
            <Field label={t.instance} hint={t.instanceHint}>
              <Input dir="ltr" defaultValue={get("instance")} key={`instance-${get("instance")}`}
                onBlur={(e) => e.target.value.trim() !== get("instance") && set({ instance: e.target.value.trim() })}
                onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()} />
            </Field>
            <Field label={t.user}>
              <SelectBox dir="ltr" value={get("user")} onValueChange={(v) => set({ user: v })}>
                <option value="">{t.anyUser}</option>
                {(facets?.users ?? []).map((u) => <option key={u} value={u}>{u}</option>)}
              </SelectBox>
            </Field>
            <Field label={t.action}>
              <SelectBox value={get("event")} onValueChange={(v) => set({ event: v })}>
                <option value="">{t.anyAction}</option>
                {(facets?.events ?? []).map((e) => <option key={e} value={e}>{e}</option>)}
              </SelectBox>
            </Field>
            <Field label={t.from}>
              <Input type="date" value={get("since")} max={get("until") || undefined} onChange={(e) => set({ since: e.target.value })} />
            </Field>
            <Field label={t.to}>
              <Input type="date" value={get("until")} min={get("since") || undefined} onChange={(e) => set({ until: e.target.value })} />
            </Field>
            <Field label={t.result}>
              <Segmented
                value={get("ok")}
                onChange={(v) => set({ ok: v })}
                options={[
                  { value: "", label: t.all },
                  { value: "allowed", label: t.allowed },
                  { value: "refused", label: t.refused },
                ]}
              />
            </Field>
          </div>
          <div className="row-wrap">
            <span className="small faint spacer">{t.exportHint(number(facets?.export_max_rows ?? 100000))}</span>
            {filtered && <Button size="sm" variant="ghost" icon={<FilterX />} onClick={() => { setText(""); setParams(new URLSearchParams(), { replace: true }); }}>{t.clear}</Button>}
          </div>
        </div>
      </section>

      <div className="card rise">
        {isPending ? <div className="card-body"><Skeleton height={300} /></div> : !entries.length ? (
          <Empty icon={<ScrollText />} title={t.nothing} />
        ) : (
          <Table aria-busy={isFetching} className="audit-table">
            <TableHeader>
              <TableRow>
                {head("ts", t.when)}
                {head("user", t.who)}
                {head("event", t.what)}
                {head("instance", t.instance)}
                {head("ip", t.address)}
              </TableRow>
            </TableHeader>
            <TableBody>
              {entries.map((e, i) => (
                <TableRow key={`${e.ts}-${i}`}>
                  <TableCell className="small nowrap" title={dateTime(e.ts)}>{ago(e.ts)}</TableCell>
                  <TableCell className="small"><bdi>{e.user}</bdi>{e.support && <> <Tag tone="caution">{sw.supportTag}</Tag></>}</TableCell>
                  <TableCell>
                    <div className="row">
                      {e.ok ? <CircleCheck size={15} color="var(--positive)" aria-label={t.allowed} /> : <CircleX size={15} color="var(--negative)" aria-label={t.refused} />}
                      <bdi>{e.event}</bdi>
                    </div>
                    {e.detail && <div className="xsmall faint" dir="auto" style={{ marginInlineStart: 23 }}>{e.detail}</div>}
                    {e.instance && <div className="xsmall faint phone-only" style={{ marginInlineStart: 23 }}><bdi>{e.instance}</bdi></div>}
                  </TableCell>
                  <TableCell>
                    <div className="row-wrap">
                      {e.instance && <Tag><bdi>{e.instance}</bdi></Tag>}
                      {!e.instance && e.project && <Tag><bdi>{e.project}</bdi></Tag>}
                      {e.branch && <span className="xsmall faint mono" dir="ltr">{e.branch}</span>}
                    </div>
                  </TableCell>
                  <TableCell className="xsmall faint"><span className="mono">{e.ip}</span></TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
        {total > 0 && (
          <div className="card-foot audit-pager">
            <span className="small muted">{t.range(number(first), number(last), number(total))}</span>
            <div className="row">
              <span className="small faint">{t.perPage}</span>
              <SelectBox size="sm" value={size} onValueChange={(v) => set({ size: v })} aria-label={t.perPage}>
                {SIZES.map((s) => <option key={s} value={s}>{s}</option>)}
              </SelectBox>
              <Button size="sm" icon={<ChevronLeft className="flip-rtl" />} aria-label={t.previous} disabled={page <= 1} onClick={() => set({ page: String(page - 1) })} />
              <Button size="sm" icon={<ChevronRight className="flip-rtl" />} aria-label={t.next} disabled={last >= total} onClick={() => set({ page: String(page + 1) })} />
            </div>
          </div>
        )}
      </div>
    </Page>
  );
}
