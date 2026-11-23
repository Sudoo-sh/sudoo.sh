// A project's public status page (/status/<token>): read without signing in, from GET /api/v1/public-status/<token>.
// It shows what the panel lets it show and nothing else: the project's name, the banner, and each part's name, state
// and uptime with a bar per day. Read again every minute.

import { useQuery } from "@tanstack/react-query";
import { Activity, CircleAlert, CircleCheck, TriangleAlert } from "lucide-react";
import { useParams } from "react-router";
import type { PublicStatus } from "@/api/status";
import { LanguageSwitch } from "@/components/LanguageSwitch";
import { dayTone, uptimeText } from "@/components/ProjectStatus";
import { Mark } from "@/components/app/Brand";
import { Callout, Skeleton } from "@/components/app/basics";
import { PublicMaintenanceNotice } from "@/components/Maintenance";
import { useT } from "@/i18n";
import words from "@/i18n/status";
import { ago, day as dayText } from "@/lib/format";

async function read(token: string): Promise<PublicStatus | null> {
  const answer = await fetch(`/api/v1/public-status/${encodeURIComponent(token)}`, { headers: { Accept: "application/json" }, credentials: "omit" });
  if (answer.status === 404) return null;
  if (!answer.ok) throw new Error(`HTTP ${answer.status}`);
  return ((await answer.json()) as { data: PublicStatus }).data;
}

export function PublicStatusPage() {
  const { token = "" } = useParams();
  const all = useT(words);
  const t = all.publicPage;
  const s = all.status;
  const { data, isPending, error } = useQuery({ queryKey: ["public-status", token], queryFn: () => read(token), refetchInterval: 60_000, retry: 1 });
  const icon = data?.state === "operational" ? <CircleCheck /> : data?.state === "down" ? <CircleAlert /> : data?.state === "degraded" ? <TriangleAlert /> : <Activity />;
  return (
    <div className="public-status">
      <header className="row">
        <span className="mark" aria-hidden="true"><Mark /></span>
        <h1 className="spacer">{data?.name ?? t.title}</h1>
        <LanguageSwitch />
      </header>
      <main className="stack">
        {isPending ? (
          <>
            <Skeleton height={88} />
            <Skeleton height={140} />
          </>
        ) : error ? (
          <Callout tone="danger">{String(error)}</Callout>
        ) : !data ? (
          <section className="card card-body stack">
            <h2>{t.gone}</h2>
            <p className="small muted">{t.goneText}</p>
          </section>
        ) : (
          <>
            <PublicMaintenanceNotice items={data.maintenance ?? []} />
            <section className="status-banner card" data-state={data.state} aria-live="polite">
              <span className="status-banner-icon" aria-hidden="true">{icon}</span>
              <div className="spacer">
                <h2>{s.banner[data.state]}</h2>
                <p className="small muted">{t.updated(ago(data.at))}</p>
              </div>
            </section>
            <section className="card">
              <ul className="public-targets">
                {data.targets.map((target) => {
                  const state = target.state ?? "none";
                  return (
                    <li key={`${target.kind}:${target.name}`}>
                      <div className="row" style={{ gap: 8 }}>
                        <strong className="spacer">
                          {s.kind[target.kind]}
                          {target.kind !== "server" && <> <bdi dir="ltr" className="mono small muted">{target.name}</bdi></>}
                        </strong>
                        <span className="small muted">{uptimeText(target.uptime.month)}</span>
                        <span className="target-state" data-state={state}><span className="dot" aria-hidden="true" />{s.state[state]}</span>
                      </div>
                      <ol className="day-bars" aria-label={s.days}>
                        {target.days.map((d) => {
                          const label = s.dayBar(dayText(d.day), d.uptime == null ? s.noData : uptimeText(d.uptime));
                          return (
                            <li key={d.day} data-tone={dayTone(d.uptime)} title={label}>
                              <span className="sr-only">{label}</span>
                            </li>
                          );
                        })}
                      </ol>
                    </li>
                  );
                })}
              </ul>
            </section>
          </>
        )}
      </main>
      <footer className="xsmall muted">{t.powered}</footer>
    </div>
  );
}
