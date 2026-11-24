// A project's Status tab, as Odoo.sh's status page: one banner (all systems operational, degraded, down), a card per
// checked part (each production instance, its backups and off-site copies, the server) with its uptime over a day, a
// week and a month and a bar per day, the production instance's response time per day, the incidents of 30 days,
// and the public status page's switch for those who manage the project.

import { useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  Archive,
  Bell,
  CircleAlert,
  CircleCheck,
  CloudUpload,
  Globe,
  RefreshCw,
  Server,
  TriangleAlert,
} from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router";
import { api, errorText } from "@/api/client";
import { useCall } from "@/api/queries";
import {
  statusKeys,
  useProjectStatus,
  type Overall,
  type PublicPageState,
  type StatusDay,
  type StatusTarget,
  type TargetKind,
} from "@/api/status";
import type { ProjectDetail } from "@/api/types";
import { useT } from "@/i18n";
import words from "@/i18n/status";
import { ago, day as dayText, duration, fullDateTime, ms, number } from "@/lib/format";
import { dayTone } from "@/lib/uptime";
import { Sparkline } from "./charts/Sparkline";
import { Button, Callout, CopyField, Empty, Skeleton, Switch } from "./app/basics";
import { useToast } from "./app/overlay";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "./ui/table";

const KIND_ICON: Record<TargetKind, ReactNode> = {
  instance: <Globe />,
  backups: <Archive />,
  offsite: <CloudUpload />,
  server: <Server />,
};

/** Uptime as people read it: never rounded up to 100 % while a minute was down. */
export function uptimeText(value: number | null | undefined): string {
  if (value == null) return "—";
  if (value >= 100) return `${number(100)}%`;
  const floored = Math.floor(value * 100) / 100;
  return `${new Intl.NumberFormat(document.documentElement.lang === "ar" ? "ar-u-nu-latn" : "en", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(floored)}%`;
}

export { dayTone };

/** The Status and Notifications tabs of a project's page (pages/Projects.tsx). */
export function ProjectStatusTabs({ project, tab, manage }: { project: { id: string }; tab: string; manage: boolean }) {
  const t = useT(words).tabs;
  return (
    <>
      <Link
        to={`/projects/${project.id}/status`}
        viewTransition
        className={`tab ${tab === "status" ? "active" : ""}`}
        aria-current={tab === "status" ? "page" : undefined}
      >
        <Activity /> {t.status}
      </Link>
      {manage && (
        <Link
          to={`/projects/${project.id}/notifications`}
          viewTransition
          className={`tab ${tab === "notifications" ? "active" : ""}`}
          aria-current={tab === "notifications" ? "page" : undefined}
        >
          <Bell /> {t.notifications}
        </Link>
      )}
    </>
  );
}

export function ProjectStatus({ project }: { project: ProjectDetail }) {
  const { data, isPending, error, refetch, isFetching } = useProjectStatus(project.id);
  const t = useT(words).status;
  if (isPending)
    return (
      <div className="stack tab-panel" aria-busy="true" aria-label={t.loading}>
        <Skeleton height={88} />
        <div className="status-grid">
          <Skeleton height={220} />
          <Skeleton height={220} />
        </div>
      </div>
    );
  if (!data)
    return (
      <div className="stack tab-panel">
        <Callout tone="danger">{error ? errorText(error) : t.notRead}</Callout>
      </div>
    );
  return (
    <div className="stack tab-panel project-status">
      <Banner state={data.state} interval={data.interval_seconds} refreshing={isFetching} onRefresh={() => refetch()} />
      {!data.checks && <Callout tone="warning">{t.checksOff}</Callout>}
      {!data.production && <Callout>{t.noProduction}</Callout>}
      <div className="status-grid">
        {data.targets.map((target) => (
          <TargetCard key={target.id} target={target} />
        ))}
      </div>
      <Incidents data={data.incidents} />
      {data.public && <PublicPage project={project} state={data.public} />}
    </div>
  );
}

function Banner({
  state,
  interval,
  refreshing,
  onRefresh,
}: {
  state: Overall;
  interval: number;
  refreshing: boolean;
  onRefresh: () => void;
}) {
  const t = useT(words).status;
  const icon =
    state === "operational" ? (
      <CircleCheck />
    ) : state === "down" ? (
      <CircleAlert />
    ) : state === "degraded" ? (
      <TriangleAlert />
    ) : (
      <Activity />
    );
  return (
    <section className="status-banner card" data-state={state} aria-live="polite">
      <span className="status-banner-icon" aria-hidden="true">
        {icon}
      </span>
      <div className="spacer">
        <h2>{t.banner[state]}</h2>
        <p className="small muted">{t.bannerText[state]}</p>
      </div>
      <span className="xsmall muted banner-interval">{t.checkedEvery(duration(interval))}</span>
      <Button
        variant="ghost"
        size="sm"
        icon={<RefreshCw className={refreshing ? "spin" : undefined} />}
        onClick={onRefresh}
        aria-label={t.checkedEvery(duration(interval))}
      />
    </section>
  );
}

function TargetCard({ target }: { target: StatusTarget }) {
  const t = useT(words).status;
  const state = target.state ?? "none";
  const responses = (target.days ?? []).map((d) => d.avg_ms ?? null);
  return (
    <section className="card status-card" aria-label={`${t.kind[target.kind]} ${target.name}`}>
      <div className="card-head">
        <span className="status-kind" aria-hidden="true">
          {KIND_ICON[target.kind]}
        </span>
        <div className="spacer" style={{ minWidth: 0 }}>
          <h3 className="row-wrap" style={{ gap: 6 }}>
            {t.kind[target.kind]}
            {target.kind !== "server" && (
              <bdi dir="ltr" className="mono small muted truncate">
                {target.name}
              </bdi>
            )}
          </h3>
          <div className="sub">{t.kindText[target.kind]}</div>
        </div>
        <span className="target-state" data-state={state}>
          <span className="dot" aria-hidden="true" />
          {t.state[state]}
        </span>
      </div>
      <div className="card-body stack">
        {(target.detail || target.since) && (
          <p className="xsmall muted">
            {target.detail}
            {target.detail && target.since ? " · " : ""}
            {target.since && <span title={fullDateTime(target.since)}>{t.since(ago(target.since))}</span>}
          </p>
        )}
        <dl className="uptime-row" aria-label={t.uptime}>
          {(["day", "week", "month"] as const).map((window) => (
            <div key={window}>
              <dt className="xsmall muted">{t.windows[window]}</dt>
              <dd data-tone={dayTone(target.uptime[window])}>
                {target.uptime[window] == null ? t.noData : uptimeText(target.uptime[window])}
              </dd>
            </div>
          ))}
        </dl>
        <DayBars days={target.days} />
        {target.kind === "instance" && responses.some((v) => v != null) && (
          <div className="response">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <span className="xsmall muted">
                {t.response} · {t.responseDays}
              </span>
              {target.response_ms != null && (
                <span className="xsmall mono">{t.responseNow(ms(target.response_ms))}</span>
              )}
            </div>
            <Sparkline t={target.days.map((d) => d.day)} values={responses} />
          </div>
        )}
      </div>
    </section>
  );
}

function DayBars({ days }: { days: StatusDay[] }) {
  const t = useT(words).status;
  const checked = days.filter((d) => d.uptime != null);
  const summary = checked.length
    ? `${t.days}: ${uptimeText(Math.min(...checked.map((d) => d.uptime!)))} – ${uptimeText(Math.max(...checked.map((d) => d.uptime!)))}`
    : `${t.days}: ${t.noData}`;
  return (
    <div className="day-bars-wrap">
      <ol className="day-bars" aria-label={summary}>
        {days.map((d) => {
          const label = t.dayBar(dayText(d.day), d.uptime == null ? t.noData : uptimeText(d.uptime));
          return (
            <li key={d.day} data-tone={dayTone(d.uptime)} title={label}>
              <span className="sr-only">{label}</span>
            </li>
          );
        })}
      </ol>
      <div className="row xsmall muted" style={{ justifyContent: "space-between" }} aria-hidden="true">
        <span>{days.length ? dayText(days[0]!.day) : ""}</span>
        <span>{t.days}</span>
      </div>
    </div>
  );
}

function Incidents({
  data,
}: {
  data: {
    id: string;
    kind: TargetKind;
    name: string;
    start: number;
    end: number | null;
    duration: number;
    severity: "down" | "degraded";
    what: string;
  }[];
}) {
  const t = useT(words).status;
  return (
    <section className="card" aria-labelledby="incidents-title">
      <div className="card-head">
        <TriangleAlert />
        <div className="spacer">
          <h2 id="incidents-title">{t.incidents}</h2>
          <div className="sub">{t.incidentsText}</div>
        </div>
      </div>
      {!data.length ? (
        <Empty icon={<CircleCheck />} title={t.noIncidents} />
      ) : (
        <div className="table-scroll">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t.what}</TableHead>
                <TableHead>{t.started}</TableHead>
                <TableHead>{t.ended}</TableHead>
                <TableHead>{t.lasted}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.map((incident) => (
                <TableRow key={incident.id}>
                  <TableCell>
                    <div className="row-wrap" style={{ gap: 6 }}>
                      <span className="target-state" data-state={incident.severity}>
                        <span className="dot" aria-hidden="true" />
                        {t.severity[incident.severity]}
                      </span>
                      <span className="small">{t.kind[incident.kind]}</span>
                      {incident.kind !== "server" && (
                        <bdi dir="ltr" className="mono xsmall muted">
                          {incident.name}
                        </bdi>
                      )}
                    </div>
                    {incident.what && <div className="xsmall muted">{incident.what}</div>}
                  </TableCell>
                  <TableCell className="small" title={fullDateTime(incident.start)}>
                    {ago(incident.start)}
                  </TableCell>
                  <TableCell className="small" title={incident.end ? fullDateTime(incident.end) : undefined}>
                    {incident.end ? (
                      ago(incident.end)
                    ) : (
                      <span className="target-state" data-state="down">
                        <span className="dot" aria-hidden="true" />
                        {t.ongoing}
                      </span>
                    )}
                  </TableCell>
                  <TableCell className="small mono">{duration(incident.duration)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </section>
  );
}

function PublicPage({ project, state }: { project: ProjectDetail; state: PublicPageState }) {
  const t = useT(words).status.public;
  const toast = useToast();
  const client = useQueryClient();
  const change = useCall(
    (body: { enabled: boolean; new_link?: boolean }) =>
      api.put<PublicPageState>(`/projects/${project.id}/status/public`, body),
    [statusKeys.status(project.id)],
  );
  const save = (body: { enabled: boolean; new_link?: boolean }) =>
    change.mutate(body, {
      onSuccess: (next) => {
        client.setQueryData(statusKeys.status(project.id), (old: unknown) =>
          old ? { ...(old as object), public: next } : old,
        );
        toast("success", t.saved);
      },
      onError: (error) => toast("error", t.notSaved, errorText(error)),
    });
  const link = state.path ? `${window.location.origin}${state.path}` : null;
  return (
    <section className="card" aria-labelledby="public-status-title">
      <div className="card-head">
        <Globe />
        <div className="spacer">
          <h2 id="public-status-title">{t.title}</h2>
          <div className="sub">{t.text}</div>
        </div>
      </div>
      <div className="card-body stack">
        {!state.allowed && <Callout tone="warning">{t.notAllowed}</Callout>}
        <Switch
          checked={state.enabled}
          disabled={!state.allowed || change.isPending}
          onChange={(enabled) => save({ enabled })}
          label={t.switch}
        />
        {state.enabled && link ? (
          <div className="row-wrap" style={{ gap: 8 }}>
            <CopyField value={link} />
            <Button
              size="sm"
              icon={<RefreshCw />}
              disabled={change.isPending}
              onClick={() => save({ enabled: true, new_link: true })}
              title={t.newLinkText}
            >
              {t.newLink}
            </Button>
          </div>
        ) : (
          <p className="xsmall muted">{t.off}</p>
        )}
      </div>
    </section>
  );
}
