import {
  Activity,
  Archive,
  ArrowUpCircle,
  ArrowUpRight,
  Box,
  Check,
  Code2,
  FolderGit2,
  FolderKanban,
  Container,
  Copy,
  Database,
  Gauge,
  Download,
  DownloadCloud,
  GitBranch,
  GitCommitHorizontal,
  Globe,
  History,
  KeyRound,
  Mail,
  Undo2,
  Layers,
  MoreHorizontal,
  Pause,
  Play,
  Plus,
  Power,
  RefreshCw,
  RotateCw,
  ScrollText,
  Settings2,
  ShieldCheck,
  ServerCog,
  SquareTerminal,
  Trash2,
  Zap,
} from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { api, errorText } from "@/api/client";
import { keys, useCall, useCapacity, useCredentials, useHistory, useInstance, useInstancePermissions, useJobs } from "@/api/queries";
import type { Instance, InstanceBackup, Job, Permission, Stage } from "@/api/types";
import { BackupPicker } from "@/components/BackupPicker";
import { LiveChart } from "@/components/charts/LiveChart";
import { JobLine, Monogram } from "@/components/InstanceCard";
import { StageTag } from "@/components/StageTag";
import { DomainsCore } from "@/components/DomainsCore";
import { PremiumSlot } from "@/premium/components";
import { usePremium } from "@/premium/loader";
import { ShellTab } from "@/components/shell/ShellTab";
import { ToolsTab } from "@/components/ToolsTab";
import { UpgradeTab } from "@/components/UpgradeTab";
import { MailTab } from "@/components/MailTab";
import { LockBanner } from "@/components/ProjectAccess";
import { ClosedNotice } from "@/components/ClientAccess";
import { ResourcesCard } from "@/components/ResourcesCard";
import { JobDrawer } from "@/components/JobDrawer";
import { LogsPanel } from "@/components/LogsPanel";
import { ConnectButton } from "@/components/ConnectAs";
import { BackupCommentDialog, ImportDatabaseButton } from "@/components/ImportDatabase";
import { Page, useCan } from "@/components/Shell";
import { StatCard } from "@/components/StatCard";
import { Button, Callout, CopyField, Empty, Field, Progress, Segmented, Skeleton, Status, Switch, Tag } from "@/components/app/basics";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Input } from "@/components/ui/input";
import { SelectBox } from "@/components/app/select";
import { Menu, MenuItem, MenuSeparator, Modal, useToast } from "@/components/app/overlay";
import { useT } from "@/i18n";
import common from "@/i18n/common";
import words from "@/i18n/instance";
import toolWords from "@/i18n/tools";
import { jobTitle } from "@/i18n/jobs";
import { ago, bytes, count, dateTime, duration, elapsed, fullDateTime, ms, number, percent } from "@/lib/format";
import { commitPage } from "@/lib/git";
import { repositoryShortName } from "@/lib/naming";
import { CreatingInstance, MissingInstance } from "./InstanceCreating";
import { CapacityNote, RepositoryPicker, useDebounced } from "./NewInstance";

type Action = { action: string; body?: Record<string, unknown>; title: string };

export function useRun(name: string, openJob: (id: string) => void) {
  const toast = useToast();
  const t = useT(words).run;
  return useCall(
    async ({ action, body, title }: Action) => {
      try {
        const job = await api.post<Job>(`/instances/${name}/actions/${action}`, body ?? {});
        toast("success", t.started(title), t.startedText);
        openJob(job.id);
        return job;
      } catch (error) {
        toast("error", t.notStarted(title), errorText(error));
        throw error;
      }
    },
    [keys.instance(name), keys.jobs(name)],
  );
}

const TABS: { id: string; label: keyof (typeof words)["en"]["tabs"]; icon: React.ReactNode; permission?: Permission }[] = [
  { id: "", label: "overview", icon: <Activity /> },
  { id: "activity", label: "activity", icon: <Layers /> },
  { id: "code", label: "code", icon: <Code2 /> },
  { id: "logs", label: "logs", icon: <ScrollText />, permission: "instances.logs" },
  { id: "backups", label: "backups", icon: <Archive /> },
  { id: "domains", label: "domains", icon: <Globe /> },
  { id: "settings", label: "settings", icon: <Settings2 />, permission: "instances.operate" },
  { id: "access", label: "access", icon: <KeyRound />, permission: "instances.credentials" },
  { id: "mail", label: "mail", icon: <Mail />, permission: "caught_mail.view" },
  { id: "shell", label: "shell", icon: <SquareTerminal />, permission: "instances.shell" },
  { id: "tools", label: "tools", icon: <Gauge />, permission: "instances.profile" },
  { id: "upgrade", label: "upgrade", icon: <ArrowUpCircle />, permission: "instances.upgrade" },
];

/** What the caller may do in this instance: their role, narrowed by the project's access and the branch's lock. */
function useCanIn(instance: Instance | undefined) {
  const roleCan = useCan();
  const { data } = useInstancePermissions(instance?.name ?? "", JSON.stringify([instance?.lock ?? null, instance?.client_access ?? null]));
  return (permission: Permission) => roleCan(permission) && (!data || data.includes(permission));
}

export function InstancePage() {
  const { name = "", tab = "" } = useParams();
  return <InstanceView name={name} tab={tab} />;
}

/** An instance's page: its head, banners and tabs. On its own at /i/<name>/<tab>, or inside a project's branch pane
 *  (`embedded`), where `tabLink` keeps the tabs' links in the project, the project is not named again, and a deleted
 *  instance leads back to `afterDelete`. */
export function InstanceView({
  name,
  tab,
  tabLink = (id: string) => `/i/${name}${id ? `/${id}` : ""}`,
  embedded = false,
  afterDelete = "/",
  lead,
  trail,
}: {
  name: string;
  tab: string;
  tabLink?: (tab: string) => string;
  embedded?: boolean;
  afterDelete?: string;
  /** a tab of the page around it, first of the tabs (a project branch's History) */
  lead?: { id: string; label: string; icon: React.ReactNode; content: React.ReactNode };
  /** a tab of the page around it, last of the tabs (a project branch's Branch settings, as on Odoo.sh) */
  trail?: { id: string; label: string; icon: React.ReactNode; content: React.ReactNode };
}) {
  const { data: instance, isPending, error } = useInstance(name);
  const can = useCanIn(instance);
  // an alert links to /i/<name>/activity?job=<id>: that job opens at once
  const [jobId, setJobId] = useState<string | null>(() => new URLSearchParams(window.location.search).get("job"));
  const severalServers = usePremium().data?.packages.some((p) => p.id === "servers" && p.status === "active") ?? false;
  const run = useRun(name, setJobId);
  const capacity = useCapacity();
  const all = useT(words);
  const t = all.head;
  const j = all.jobs;
  const b = all.banners;
  const [dialog, setDialog] = useState<null | "upgrade" | "branch" | "delete" | "submodule" | "rollback" | "connect" | "pause" | "switch-off">(null);

  if (error && !instance) return <MissingInstance name={name} error={error} />;
  if (instance?.status === "creating" && instance.current_job) return <CreatingInstance instance={instance} />;
  if (isPending || !instance)
    return (
      <Page>
        <Skeleton height={64} width="50%" />
        <Skeleton height={300} />
      </Page>
    );

  const busy = !!instance.current_job;
  const plain = instance.source === "plain"; // no repository: nothing to update, no branch, no submodules
  const asleep = ["paused", "switched_off", "stopped"].includes(instance.status);
  const production = instance.stage === "production";
  // on a production instance, what replaces or stops its data says so: the dialogs ask first, the request confirms
  const act = (action: string, title: string, body?: Record<string, unknown>) =>
    run.mutate({ action, title, body: production && PRODUCTION_CONFIRMED.includes(action) ? { ...body, confirm_production: true } : body });

  return (
    <Page>
      <div className="page-head">
        <div className="hero spacer">
          {!embedded && <Monogram name={instance.name} large />}
          <div className="titles" style={{ minWidth: 0 }}>
            <div className="row-wrap" style={{ gap: 12 }}>
              {embedded ? (
                <h2 className="truncate instance-title"><bdi>{instance.name}</bdi></h2>
              ) : (
                <h1 className="truncate"><bdi>{instance.name}</bdi></h1>
              )}
              <StageTag stage={instance.stage} />
              <Status status={instance.status} />
            </div>
            <div className="row-wrap small muted">
              {instance.project && !embedded && (
                <Link to={`/projects/${instance.project.id}`} viewTransition className="row xsmall" style={{ gap: 4 }} title={t.project}>
                  <FolderKanban size={14} strokeWidth={1.5} /> <bdi>{instance.project.name}</bdi>
                </Link>
              )}
              {instance.server && (severalServers || instance.server.id !== "main") && (
                <span className="row xsmall" style={{ gap: 4 }} title={t.server}>
                  <ServerCog size={14} strokeWidth={1.5} /> <bdi>{instance.server.region ? `${instance.server.name} · ${instance.server.region}` : instance.server.name}</bdi>
                </span>
              )}
              <Tag mono>{instance.odoo.version}</Tag>
              <Tag>{instance.odoo.edition === "enterprise" ? "Enterprise" : "Community"}</Tag>
              {plain ? (
                <span className="row xsmall" style={{ gap: 4 }}>
                  <Box size={14} strokeWidth={1.5} /> {t.plain}
                </span>
              ) : (
                <span className="row mono xsmall" style={{ gap: 4 }}>
                  <GitBranch size={14} strokeWidth={1.5} /> <bdi dir="ltr">{instance.git.branch}</bdi>
                </span>
              )}
              {instance.url && (!instance.lock || can("instances.logs")) && (
                <a href={instance.url} target="_blank" rel="noreferrer" className="row truncate mono xsmall" style={{ gap: 4 }}>
                  <Globe size={14} strokeWidth={1.5} /> <bdi dir="ltr">{instance.url.replace(/^https?:\/\//, "")}</bdi>
                </a>
              )}
            </div>
          </div>
        </div>
        {/* one way to open Odoo; on narrow screens Restart and Get latest code move into the "more" menu
            (.header-secondary, styles/_layout.scss) */}
        <div className="actions">
          {instance.url && !asleep && (!instance.lock || can("instances.logs")) && (
            <ConnectButton instance={instance} connectAs={can("instances.credentials")} />
          )}
          {can("instances.update") &&
            (asleep ? (
              <Button variant="primary" icon={<Play />} disabled={busy || !!capacity?.blocked} onClick={() => act("start", j.start)}>
                {t.start}
              </Button>
            ) : (
              <Button className="header-secondary" icon={<RotateCw />} disabled={busy} onClick={() => act("restart", j.restart)}>
                {t.restart}
              </Button>
            ))}
          {can("instances.update") && !plain && (
            <Button className="header-secondary" icon={<DownloadCloud />} disabled={busy || !instance.git.repository} onClick={() => act("update", j.update)}>
              {t.getLatest}
            </Button>
          )}
          {/* several servers (premium): Move to another server */}
          <PremiumSlot name="instance.actions" props={{ instance, busy, onJob: setJobId }} />
          {(can("instances.update") || can("instances.operate")) && (
          <Menu trigger={(toggle) => <Button icon={<MoreHorizontal />} onClick={toggle} aria-label={t.moreActions} />}>
            {(close) => {
              const item = (fn: () => void) => () => {
                close();
                fn();
              };
              return (
                <>
                  {/* the header's own buttons, when the header is too narrow for them */}
                  {can("instances.update") && !asleep && (
                    <MenuItem className="header-secondary-item" disabled={busy} onSelect={item(() => act("restart", j.restart))}>
                      <RotateCw /> {t.restart}
                    </MenuItem>
                  )}
                  {can("instances.update") && !plain && (
                    <MenuItem className="header-secondary-item" disabled={busy || !instance.git.repository} onSelect={item(() => act("update", j.update))}>
                      <DownloadCloud /> {t.getLatest}
                    </MenuItem>
                  )}
                  {!plain && (
                    <MenuItem disabled={busy || !instance.git.repository} onSelect={item(() => act("update-submodules", j.updateSubmodules))}>
                      <RefreshCw /> {t.updateSubmodules}
                    </MenuItem>
                  )}
                  {can("instances.operate") && (
                    <>
                      <MenuItem disabled={busy} onSelect={item(() => setDialog("upgrade"))}>
                        <Zap /> {t.upgradeModules}
                      </MenuItem>
                      {plain ? (
                        <MenuItem disabled={busy} onSelect={item(() => setDialog("connect"))}>
                          <FolderGit2 /> {t.connectRepository}
                        </MenuItem>
                      ) : (
                        <MenuItem disabled={busy || !instance.git.repository} onSelect={item(() => setDialog("branch"))}>
                          <GitBranch /> {t.switchBranch}
                        </MenuItem>
                      )}
                      <MenuItem disabled={busy} onSelect={item(() => act("update-images", j.updateImages))}>
                        <Container /> {t.updateImages}
                      </MenuItem>
                      <MenuSeparator />
                      {!asleep && (
                        <MenuItem disabled={busy} onSelect={item(() => (production ? setDialog("pause") : act("pause", j.pause)))}>
                          <Pause /> {t.pause}
                        </MenuItem>
                      )}
                      {instance.status !== "switched_off" && (
                        <MenuItem disabled={busy} onSelect={item(() => (production ? setDialog("switch-off") : act("switch-off", j.switchOff)))}>
                          <Power /> {t.switchOff}
                        </MenuItem>
                      )}
                    </>
                  )}
                  {can("instances.delete") && (
                    <MenuItem danger disabled={busy} onSelect={item(() => setDialog("delete"))}>
                      <Trash2 /> {t.delete}
                    </MenuItem>
                  )}
                </>
              );
            }}
          </Menu>
          )}
        </div>
      </div>

      {instance.lock && <LockBanner lock={instance.lock} allowed={can("instances.logs")} />}
      {instance.project && (
        <ClosedNotice projectId={instance.project.id} projectName={instance.project.name} branch={instance.git.branch} access={instance.client_access && { ...instance.client_access, lets_you_through: can("instances.logs") }} />
      )}
      {instance.current_job && (
        <button className="card pad" style={{ textAlign: "start", cursor: "pointer" }} onClick={() => setJobId(instance.current_job!.id)}>
          <JobLine job={instance.current_job} />
        </button>
      )}
      {instance.rollback && can("instances.restore") && (
        <div className="rollback-banner" role="status">
          <Undo2 />
          <div className="text small">
            <b>{b.restored(ago(instance.rollback.taken_at))}</b>{b.keptUntil(dateTime(instance.rollback.until))}
          </div>
          <Button variant="primary" icon={<Undo2 />} disabled={busy} onClick={() => setDialog("rollback")}>
            {b.rollback}
          </Button>
        </div>
      )}
      {asleep && !busy && can("instances.update") && <CapacityNote />}
      {instance.git.branch_deleted_on_github && (
        <Callout tone="warning">
          {b.branchGone(<b key="b"><bdi dir="ltr">{instance.git.branch}</bdi></b>)}
        </Callout>
      )}
      {instance.update_failure && (
        <Callout tone={instance.update_failure.rolled_back ? "warning" : "danger"}>
          <b>{instance.update_failure.rolled_back ? b.updateUndone : b.updateNotUndone}{instance.update_failure.failed_at ? ` ${ago(instance.update_failure.failed_at)}` : ""}.</b>{" "}
          {instance.update_failure.message}
          {instance.update_failure.job && (
            <div>
              <Button size="sm" variant="ghost" onClick={() => setJobId(instance.update_failure!.job!.split("/").pop()!)}>
                {b.showJob}
              </Button>
            </div>
          )}
        </Callout>
      )}
      {instance.newer_odoo_image && (
        <Callout tone="info">
          <b>{b.newerImage(instance.odoo.version)}</b> {b.newerImageText}
          {can("instances.operate") && (
            <div>
              <Button size="sm" icon={<Container />} disabled={busy} onClick={() => act("update-images", j.updateImages)}>
                {t.updateImages}
              </Button>
            </div>
          )}
        </Callout>
      )}
      {instance.gateway_error && <Callout tone="danger">{instance.gateway_error}</Callout>}
      {instance.drift && (
        <Callout tone="danger">
          <b>{b.drift(instance.drift.detected_at ? ago(instance.drift.detected_at) : "")}</b>{" "}
          {instance.drift.action === "restore" ? b.driftRestore : b.driftWarn}
          <ul className="drift-list mono xsmall" dir="ltr">
            {instance.drift.items.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
          {instance.drift.action === "warn" && can("instances.operate") && (
            <Button icon={<ShieldCheck />} disabled={busy} onClick={() => act("repair", j.repair)}>
              {b.restoreConfig}
            </Button>
          )}
        </Callout>
      )}

      <nav className="tabs" aria-label={instance.name}>
        {lead && (
          <Link to={tabLink(lead.id)} viewTransition className={tab === lead.id ? "active" : ""} aria-current={tab === lead.id ? "page" : undefined}>
            {lead.icon} {lead.label}
          </Link>
        )}
        {TABS.filter((t) => (!t.permission || can(t.permission)) && !(plain && t.id === "code") && !(production && t.id === "mail")).map((t) => (
          <Link key={t.id} to={tabLink(t.id)} viewTransition className={tab === t.id ? "active" : ""} aria-current={tab === t.id ? "page" : undefined}>
            {t.icon} {all.tabs[t.label]}
          </Link>
        ))}
        {trail && (
          <Link to={tabLink(trail.id)} viewTransition className={tab === trail.id ? "active" : ""} aria-current={tab === trail.id ? "page" : undefined}>
            {trail.icon} {trail.label}
          </Link>
        )}
      </nav>

      <div className="rise tab-panel" key={tab}>
        {lead && tab === lead.id && lead.content}
        {trail && tab === trail.id && trail.content}
        {tab === "" && <Overview instance={instance} busy={busy} act={act} />}
        {tab === "activity" && <ActivityTab name={name} onOpen={setJobId} />}
        {tab === "code" && !plain && <CodeTab instance={instance} canAdd={can("instances.update")} canRemove={can("instances.operate")} busy={busy} onAdd={() => setDialog("submodule")} act={act} />}
        {tab === "logs" && can("instances.logs") && <LogsTab name={name} />}
        {tab === "domains" && <PremiumSlot name="instance.domains" props={{ instance }} fallback={<DomainsCore instance={instance} />} />}
        {tab === "settings" && can("instances.operate") && <SettingsTab instance={instance} plain={plain} />}
        {tab === "access" && can("instances.credentials") && <AccessTab instance={instance} />}
        {tab === "mail" && can("caught_mail.view") && !production && <MailTab instance={instance} />}
        {tab === "shell" && can("instances.shell") && <ShellTab instance={instance} />}
        {tab === "tools" && can("instances.profile") && <ToolsTab instance={instance} />}
        {tab === "upgrade" && can("instances.upgrade") && <UpgradeTab instance={instance} onJob={setJobId} />}
        {tab === "backups" && <BackupsCard instance={instance} busy={busy} act={act} />}
      </div>

      {jobId && <JobDrawer id={jobId} onClose={() => setJobId(null)} />}
      {dialog === "upgrade" && <UpgradeDialog onClose={() => setDialog(null)} onRun={(modules) => act("upgrade", j.upgrade, { modules })} />}
      {dialog === "branch" && <BranchDialog instance={instance} onClose={() => setDialog(null)} onRun={(branch) => act("switch-branch", j.switchBranch, { branch })} />}
      {dialog === "connect" && <ConnectDialog onClose={() => setDialog(null)} onRun={(body) => act("connect-repository", j.connect, body)} />}
      {dialog === "submodule" && <SubmoduleDialog version={instance.odoo.version} onClose={() => setDialog(null)} onRun={(body) => act("add-submodule", j.addSubmodule, body)} />}
      {dialog === "delete" && <DeleteDialog name={name} production={production} after={afterDelete} onClose={() => setDialog(null)} />}
      {(dialog === "pause" || dialog === "switch-off") && (
        <StopProductionDialog name={name} action={dialog} onClose={() => setDialog(null)}
          onRun={() => act(dialog, dialog === "pause" ? j.pause : j.switchOff)} />
      )}
      {dialog === "rollback" && instance.rollback && (
        <RollbackDialog instance={instance} onClose={() => setDialog(null)} onRun={() => act("rollback", j.rollback)} />
      )}
    </Page>
  );
}

const PRODUCTION_CONFIRMED = ["pause", "switch-off", "restore", "rollback"];

/** Pausing or switching off a production instance takes its users offline: asked once more. */
function StopProductionDialog({ name, action, onClose, onRun }: { name: string; action: "pause" | "switch-off"; onClose: () => void; onRun: () => void }) {
  const all = useT(words);
  const t = all.stop;
  const c = useT(common).common;
  const verb = action === "pause" ? all.head.pause : all.head.switchOff;
  return (
    <Modal title={action === "pause" ? t.pauseTitle(name) : t.switchOffTitle(name)} description={t.production} onClose={onClose}
      footer={<><Button onClick={onClose}>{c.cancel}</Button><Button variant="danger" icon={action === "pause" ? <Pause /> : <Power />} onClick={() => { onRun(); onClose(); }}>{verb}</Button></>}>
      <Callout tone="warning">
        {action === "pause" ? t.pauseText : t.switchOffText}
      </Callout>
    </Modal>
  );
}

/** What the production stage changes, shown on a production instance's overview. */
function ProductionNote() {
  const t = useT(words).productionNote;
  return (
    <Callout tone="info">
      <b>{t.title}</b>{t.text}
    </Callout>
  );
}

// ---------------------------------------------------------------- overview

const METRICS = {
  cpu_percent: { label: "processor", format: (v: number) => percent(v, 1) },
  memory_percent: { label: "memory", format: (v: number) => percent(v), max: 100 },
  requests_per_minute: { label: "requestsPerMinute", format: (v: number) => number(v) },
  response_ms_p95: { label: "responseP95", format: (v: number) => ms(v) },
} as const;
type Metric = keyof typeof METRICS;

function Overview({ instance, busy, act }: { instance: Instance; busy: boolean; act: (action: string, title: string, body?: Record<string, unknown>) => void }) {
  const { data: history } = useHistory();
  const [metric, setMetric] = useState<Metric>("cpu_percent");
  const series = history?.instances[instance.name];
  const u = instance.usage;
  const chosen = METRICS[metric];
  const t = useT(words).overview;
  const label = t[chosen.label];
  return (
    <div className="stack">
      {instance.stage === "production" && <ProductionNote />}
      <div className="grid-4 stagger">
        <StatCard label={t.processor} value={u.cpu_percent ?? null} unit="%" decimals={1} detail={t.ofServer}
          trend={{ t: series?.t ?? [], values: series?.cpu_percent ?? [] }} />
        <StatCard label={t.memory} value={u.memory_percent != null ? Math.round(u.memory_percent) : null} unit="%"
          detail={t.memoryOf(bytes(u.memory_bytes), bytes(u.memory_limit_bytes))}
          trend={{ t: series?.t ?? [], values: series?.memory_percent ?? [], max: 100 }} />
        <StatCard label={t.requests} value={u.requests_per_minute != null ? Math.round(u.requests_per_minute) : null} unit={t.perMinute}
          detail={u.error_rate_percent ? t.errors(percent(u.error_rate_percent, 1)) : t.noErrors}
          trend={{ t: series?.t ?? [], values: series?.requests_per_minute ?? [] }} />
        <StatCard label={t.responseTime} value={u.response_ms_p95 != null ? Math.round(u.response_ms_p95) : null} unit="ms"
          detail={t.responseDetail(ms(u.response_ms_avg))}
          trend={{ t: series?.t ?? [], values: series?.response_ms_p95 ?? [] }} />
      </div>

      <div className="overview">
        <section className="card chart-card">
          <div className="chart-head">
            <h2>{label}</h2>
            <span className="spacer" />
            <Segmented<Metric>
              value={metric}
              onChange={setMetric}
              options={[
                { value: "cpu_percent", label: t.cpu },
                { value: "memory_percent", label: t.memory },
                { value: "requests_per_minute", label: t.requests },
                { value: "response_ms_p95", label: t.response },
              ]}
            />
          </div>
          <div className="chart-body">
            <LiveChart
              t={series?.t ?? []}
              max={"max" in chosen ? chosen.max : undefined}
              format={chosen.format}
              series={[{ label, values: series?.[metric] ?? [], color: "--brand", fill: true }]}
            />
          </div>
        </section>
        <section className="card">
          <div className="card-head"><h2>{t.details}</h2></div>
          <div className="card-body">
            <dl className="dl small">
              {instance.managed_name && (
                <>
                  <dt>{t.managedName}</dt>
                  <dd className="truncate">
                    {instance.managed_name.url ? (
                      <a href={instance.managed_name.url} target="_blank" rel="noreferrer" dir="ltr">{instance.managed_name.hostname}</a>
                    ) : (
                      <><bdi dir="ltr" className="mono">{instance.managed_name.hostname}</bdi> <span className="faint">{t.managedPending}</span></>
                    )}
                  </dd>
                </>
              )}
              <dt>{t.database}</dt>
              <dd className="mono" dir="ltr">{instance.odoo.database}</dd>
              <dt>{t.repository}</dt>
              <dd className="truncate">{instance.git.repository ? <a href={instance.git.repository} target="_blank" rel="noreferrer" dir="ltr">{instance.git.repository.replace(/^https:\/\/github\.com\//, "")}</a> : <span className="faint">{instance.source === "plain" ? t.nonePlain : t.none}</span>}</dd>
              <dt>{t.runningFor}</dt>
              <dd>{duration(u.uptime_seconds)}</dd>
              <dt>{t.disk}</dt>
              <dd>{instance.disk ? t.diskSplit(bytes(instance.disk.database_bytes), bytes(instance.disk.files_bytes)) : bytes(u.disk_bytes)}</dd>
              <dt>{t.lastVisit}</dt>
              <dd>{ago(instance.last_visit_at)}</dd>
              <dt>{t.pauses}</dt>
              <dd>{instance.pauses_at ? t.pausesAt(ago(instance.pauses_at)) : instance.settings.auto_pause ? t.whenIdle : t.never}</dd>
              <dt>{t.created}</dt>
              <dd>{dateTime(instance.created_at)}{instance.created_by ? t.createdBy(instance.created_by) : ""}</dd>
              <dt>{t.deleted}</dt>
              <dd>{instance.expires_at ? t.deletedAt(dateTime(instance.expires_at)) : t.keptByHand}</dd>
            </dl>
          </div>
        </section>
      </div>
      <BackupsCard instance={instance} busy={busy} act={act} />
    </div>
  );
}

// ---------------------------------------------------------------- backups

function BackupsCard({ instance, busy, act }: { instance: Instance; busy: boolean; act: (action: string, title: string, body?: Record<string, unknown>) => void }) {
  const can = useCanIn(instance);
  const [restoring, setRestoring] = useState<InstanceBackup | null>(null);
  const [picking, setPicking] = useState(false);
  const [commenting, setCommenting] = useState(false);
  const list = instance.backups ?? [];
  const latest = list.find((b) => b.file === instance.restorable_backup);
  const all = useT(words);
  const t = all.backups;
  const tb = useT(toolWords).backups;
  const KIND_LABEL = all.backupKinds;
  return (
    <section className="card">
      <div className="card-head">
        <h2>{t.title}</h2>
        <span className="sub">{list.length ? t.kept(count(list.length, "backup")) : t.noneYet}</span>
        <span className="spacer" />
        {can("instances.operate") && (
          <Button size="sm" icon={<Database />} disabled={busy} onClick={() => setCommenting(true)}>{t.backUpNow}</Button>
        )}
        {can("instances.restore") && (
          <Button size="sm" icon={<History />} disabled={busy || !latest} onClick={() => latest && setRestoring(latest)}>
            {t.restoreLatest}
          </Button>
        )}
        {can("instances.restore") && can("backups.manage") && (
          <Button size="sm" variant="ghost" disabled={busy} onClick={() => setPicking(true)}>
            {t.restoreAnother}
          </Button>
        )}
        {can("instances.restore") && <ImportDatabaseButton instance={instance} busy={busy} />}
      </div>
      {latest ? (
        <div className="backup-latest">
          <div>
            <div className="small muted">{t.latest}</div>
            <div className="when">{dateTime(latest.created_at)}</div>
            <div className="xsmall faint">{KIND_LABEL[latest.kind] ?? latest.kind} · {bytes(latest.size_bytes)} · {ago(latest.created_at)}</div>
          </div>
        </div>
      ) : (
        <p className="card-body small muted">
          {t.nightly}
        </p>
      )}
      {list.length > 0 && (
        <Table>
          <TableHeader><TableRow><TableHead>{t.taken}</TableHead><TableHead>{t.kind}</TableHead><TableHead>{tb.revision}</TableHead><TableHead>{tb.comment}</TableHead><TableHead>{t.size}</TableHead><TableHead /></TableRow></TableHeader>
          <TableBody>
            {list.map((b) => (
              <TableRow key={b.file}>
                <TableCell>{dateTime(b.created_at)} <span className="faint xsmall">· {ago(b.created_at)}</span></TableCell>
                <TableCell><Tag tone={b.kind === "daily" ? "accent" : undefined}>{KIND_LABEL[b.kind] ?? b.kind}</Tag></TableCell>
                <TableCell className="mono small" dir="ltr">
                  {b.revision ? (commitPage(instance.git.repository, b.revision)
                    ? <a href={commitPage(instance.git.repository, b.revision)!} target="_blank" rel="noreferrer">{b.revision.slice(0, 7)}</a>
                    : b.revision.slice(0, 7)) : <span className="faint">—</span>}
                </TableCell>
                <TableCell className="small"><span className="backup-comment" title={b.comment ?? undefined}><bdi>{b.comment || tb.noComment}</bdi></span></TableCell>
                <TableCell className="mono small">{bytes(b.size_bytes)}</TableCell>
                <TableCell className="actions">
                  {can("backups.manage") && (
                    <Button asChild variant="ghost" size="sm"><a href={`/api/v1/backups/${b.file}`} download aria-label={t.download(b.file)}><Download /></a></Button>
                  )}
                  {can("instances.restore") && (
                    <Button size="sm" variant="ghost" disabled={busy} onClick={() => setRestoring(b)}>{t.restore}</Button>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      {restoring && (
        <RestoreDialog instance={instance} backup={restoring} onClose={() => setRestoring(null)}
          onRun={() => act("restore", all.jobs.restore, { file: restoring.file })} />
      )}
      {picking && (
        <AnotherBackupDialog instance={instance} onClose={() => setPicking(false)}
          onRun={(file) => act("restore", all.jobs.restore, { file })} />
      )}
      {commenting && (
        <BackupCommentDialog onClose={() => setCommenting(false)}
          onRun={(comment) => act("backup", all.jobs.backup, comment ? { comment } : undefined)} />
      )}
    </section>
  );
}

function RestoreDialog({ instance, backup, onClose, onRun }: { instance: Instance; backup: InstanceBackup; onClose: () => void; onRun: () => void }) {
  const all = useT(words);
  const t = all.backups;
  const c = useT(common).common;
  return (
    <Modal title={t.restoreTitle(dateTime(backup.created_at))} description={`${all.backupKinds[backup.kind] ?? backup.kind} · ${bytes(backup.size_bytes)}`} onClose={onClose}
      footer={<><Button onClick={onClose}>{c.cancel}</Button><Button variant="danger" onClick={() => { onRun(); onClose(); }}>{t.restore}</Button></>}>
      <div className="stack">
        <Callout tone="warning">
          {instance.stage === "production" && <><b>{t.productionWarning}</b> </>}
          {t.restoreText(<b key="db" className="mono">{backup.database}</b>, dateTime(backup.created_at))}
        </Callout>
        <p className="small muted">
          {t.restoreAfter(<b key="r">{t.rollbackWord}</b>, <bdi key="n">{instance.name}</bdi>)}
        </p>
      </div>
    </Modal>
  );
}

function AnotherBackupDialog({ instance, onClose, onRun }: { instance: Instance; onClose: () => void; onRun: (file: string) => void }) {
  const [file, setFile] = useState<string | null>(null);
  const foreign = !!file && !file.startsWith(`${instance.name}__`);
  const t = useT(words).backups;
  const c = useT(common).common;
  return (
    <Modal wide title={t.anotherTitle} description={t.anotherText(instance.odoo.version, instance.name)} onClose={onClose}
      footer={<><Button onClick={onClose}>{c.cancel}</Button><Button variant="danger" disabled={!file} onClick={() => { if (file) { onRun(file); onClose(); } }}>{t.restore}</Button></>}>
      <div className="stack">
        <BackupPicker version={instance.odoo.version} edition={instance.odoo.edition} value={file} onChange={setFile} />
        {file && (
          <Callout tone="warning">
            {instance.stage === "production" && <><b>{t.productionWarning}</b> </>}
            {t.anotherReplace(<b key="db" className="mono">{instance.odoo.database}</b>, <b key="r">{t.rollbackWord}</b>)}
            {foreign && t.neutralised}
          </Callout>
        )}
      </div>
    </Modal>
  );
}

function RollbackDialog({ instance, onClose, onRun }: { instance: Instance; onClose: () => void; onRun: () => void }) {
  const rollback = instance.rollback!;
  const t = useT(words).backups;
  const c = useT(common).common;
  return (
    <Modal title={t.rollbackTitle} description={t.rollbackTo(dateTime(rollback.taken_at))} onClose={onClose}
      footer={<><Button onClick={onClose}>{c.cancel}</Button><Button variant="danger" icon={<Undo2 />} onClick={() => { onRun(); onClose(); }}>{t.rollbackWord}</Button></>}>
      <div className="stack">
        <Callout tone="warning">
          {instance.stage === "production" && <><b>{t.productionWarning}</b> </>}
          {t.rollbackText(dateTime(rollback.taken_at))}
        </Callout>
        <p className="small muted">
          {t.rollbackAfter(dateTime(rollback.until), <bdi key="n">{instance.name}</bdi>)}
        </p>
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------- activity

function JobRow({ job, onOpen }: { job: Job; onOpen: (id: string) => void }) {
  const t = useT(words).activity;
  return (
    <button className="job-row" onClick={() => onOpen(job.id)}>
      <Status status={job.status} />
      <div style={{ minWidth: 0 }}>
        <div className="truncate">{jobTitle(job.action)}</div>
        <div className="xsmall faint">
          <bdi>{job.instance}</bdi> · <bdi>{job.requested_by}</bdi> · {ago(job.started_at)}
          {job.finished_at && t.took(elapsed(job.started_at, job.finished_at))}
        </div>
        {job.error && <div className="xsmall" style={{ color: "var(--negative)" }}>{job.error}</div>}
      </div>
      <Progress value={job.progress_percent} status={job.status} />
    </button>
  );
}

export function JobList({ jobs, onOpen }: { jobs: Job[] | undefined; onOpen: (id: string) => void }) {
  const t = useT(words).activity;
  if (!jobs) return <div className="card-body"><Skeleton height={120} /></div>;
  if (!jobs.length) return <Empty icon={<Layers />} title={t.emptyTitle}>{t.emptyText}</Empty>;
  return <>{jobs.map((job) => <JobRow key={job.id} job={job} onOpen={onOpen} />)}</>;
}

function ActivityTab({ name, onOpen }: { name: string; onOpen: (id: string) => void }) {
  const { data: jobs } = useJobs(name);
  return (
    <div className="card">
      <JobList jobs={jobs} onOpen={onOpen} />
    </div>
  );
}

// ---------------------------------------------------------------- code

type Commit = NonNullable<Instance["recent_commits"]>[number];

/** One commit: its message, then its short hash (a link to the commit when the repository's page is known, else a
 *  button that copies it), its author and when, as a relative time with the full date and time on hover. */
function CommitRow({ commit, latest, repository }: { commit: Commit; latest: boolean; repository: string | null }) {
  const t = useT(words).code;
  const c = useT(common).common;
  const [copied, setCopied] = useState(false);
  const date = /^\d{4}-\d\d-\d\dT/.test(commit.when) ? new Date(commit.when) : null;
  const valid = date && !Number.isNaN(date.getTime());
  const page = commitPage(repository, commit.full_sha || commit.sha);
  const copy = async () => {
    await navigator.clipboard.writeText(commit.full_sha || commit.sha).catch(() => undefined);
    setCopied(true);
    setTimeout(() => setCopied(false), 1400);
  };
  return (
    <li className="commit">
      <span className={`node ${latest ? "done" : ""}`}><GitCommitHorizontal /></span>
      <div className="commit-body">
        <div className="commit-message truncate" title={commit.subject}><bdi>{commit.subject}</bdi></div>
        <div className="commit-meta xsmall">
          {page ? (
            <a className="commit-sha" href={page} target="_blank" rel="noreferrer" dir="ltr" title={t.openCommit}>
              {commit.sha}
              <ArrowUpRight aria-hidden="true" />
            </a>
          ) : (
            <button type="button" className="commit-sha" dir="ltr" onClick={copy} title={copied ? c.copied : t.copyCommit} aria-label={copied ? c.copied : t.copyCommit}>
              {commit.sha}
              {copied ? <Check aria-hidden="true" /> : <Copy aria-hidden="true" />}
            </button>
          )}
          <span className="faint" aria-hidden="true">·</span>
          <bdi className="faint truncate">{commit.author}</bdi>
          <span className="faint" aria-hidden="true">·</span>
          {valid ? (
            <time className="faint nowrap" dateTime={commit.when} title={fullDateTime(commit.when)}>{ago(commit.when)}</time>
          ) : (
            <span className="faint nowrap">{commit.when}</span>
          )}
        </div>
      </div>
    </li>
  );
}

function CodeTab({ instance, canAdd, canRemove, busy, onAdd, act }: { instance: Instance; canAdd: boolean; canRemove: boolean; busy: boolean; onAdd: () => void; act: (action: string, title: string, body?: Record<string, unknown>) => void }) {
  const all = useT(words);
  const t = all.code;
  return (
    <div className="grid-2" style={{ alignItems: "start" }}>
      <div className="card">
        <div className="card-head">
          <h2>{t.commits}</h2>
          <span className="sub mono">{instance.git.branch}</span>
        </div>
        {instance.recent_commits?.length ? (
          <ol className="timeline card-body">
            {instance.recent_commits.map((c, i) => (
              <CommitRow key={c.sha} commit={c} latest={i === 0} repository={instance.git.repository} />
            ))}
          </ol>
        ) : (
          <Empty icon={<GitCommitHorizontal />} title={t.noCommits}>{t.noCommitsText}</Empty>
        )}
      </div>
      <div className="card">
        <div className="card-head">
          <h2>{t.submodules}</h2>
          <span className="spacer" />
          {canAdd && <Button size="sm" icon={<Plus />} disabled={busy || !instance.git.repository} onClick={onAdd}>{t.add}</Button>}
        </div>
        {instance.submodules?.length ? (
          <Table>
            <TableBody>
              {instance.submodules.map((s) => (
                <TableRow key={s.path}>
                  <TableCell>
                    <div className="mono" dir="ltr">{s.path}</div>
                    <div className="xsmall faint truncate" dir="ltr">{s.url}</div>
                  </TableCell>
                  <TableCell className="mono xsmall">{s.sha}</TableCell>
                  <TableCell className="actions">
                    {canRemove && (
                      <Button size="sm" variant="ghost" icon={<Trash2 />} disabled={busy} aria-label={t.remove} onClick={() => confirm(t.removeConfirm(s.path)) && act("remove-submodule", all.jobs.removeSubmodule, { path: s.path })} />
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <Empty icon={<Layers />} title={t.noSubmodules}>{t.noSubmodulesText}</Empty>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- logs

function LogsTab({ name }: { name: string }) {
  return <LogsPanel name={name} />;
}

// ---------------------------------------------------------------- settings

function SettingsTab({ instance, plain }: { instance: Instance; plain: boolean }) {
  const toast = useToast();
  const can = useCanIn(instance);
  const t = useT(words).settings;
  const c = useT(common);
  const stageLabel = (stage: Stage) => c.stages[stage]?.label ?? stage;
  const save = useCall((body: Record<string, unknown>) => api.patch<Instance>(`/instances/${instance.name}`, body), [keys.instance(instance.name), keys.instances, keys.resources(instance.name)]);
  const set = (body: Record<string, unknown>) =>
    save.mutate(body, { onSuccess: () => toast("success", t.saved), onError: (e) => toast("error", t.notSaved, errorText(e)) });
  const s = instance.settings;
  const production = instance.stage === "production";
  const [leaving, setLeaving] = useState<Stage | null>(null);
  const changeStage = (stage: Stage) => {
    if (production && stage !== "production") setLeaving(stage); // leaving production is asked first
    else set({ stage });
  };
  // a lifetime that ends sooner deletes the instance sooner: only roles that may delete instances choose one
  const mayShorten = can("instances.delete");
  const sooner = (days: number) => days > 0 && (s.delete_after_days === 0 || days < s.delete_after_days);
  const rows: [keyof typeof s, string, string][] = [
    // plain Odoo has no code to update: only what applies to it
    ...(plain
      ? []
      : ([
          ["auto_update", t.autoUpdate, t.autoUpdateText],
          ["auto_upgrade", t.autoUpgrade, t.autoUpgradeText],
        ] as [keyof typeof s, string, string][])),
    ...(production
      ? []
      : ([["auto_pause", t.autoPause, t.autoPauseText]] as [keyof typeof s, string, string][])),
  ];
  return (
    <div className="stack">
      <div className="card">
        <div className="settings-row">
          <div>
            <div className="name">{t.stage}</div>
            <div className="desc">{c.stages[instance.stage]?.text}</div>
          </div>
          <SelectBox aria-label={t.stage} value={instance.stage} disabled={save.isPending} onValueChange={(value) => changeStage(value as Stage)}>
            {(["production", "staging", "development"] as Stage[]).map((st) => <option key={st} value={st}>{stageLabel(st)}</option>)}
          </SelectBox>
        </div>
        {rows.map(([key, title, text]) => (
          <div className="settings-row" key={key}>
            <div><div className="name">{title}</div><div className="desc">{text}</div></div>
            <div style={{ justifySelf: "end" }}><Switch checked={s[key] as boolean} onChange={(v) => set({ [key]: v })} /></div>
          </div>
        ))}
        {production ? (
          <div className="settings-row">
            <div>
              <div className="name">{t.lifetime}</div>
              <div className="desc">{t.productionLifetime}</div>
            </div>
            <span className="small muted" style={{ justifySelf: "end" }}>{t.kept}</span>
          </div>
        ) : (
        <div className="settings-row">
          <div>
            <div className="name">{t.lifetime}</div>
            <div className="desc">
              {t.lifetimeText}
              {!mayShorten && t.longerOnly}
            </div>
          </div>
          <SelectBox value={s.delete_after_days} onValueChange={(value) => set({ delete_after_days: Number(value) })}>
            {[...new Set([7, 14, 30, 60, 90, 180, s.delete_after_days])].filter((d) => d > 0).sort((a, b) => a - b).map((d) => (
              <option key={d} value={d} disabled={!mayShorten && sooner(d)}>{count(d, "day")}</option>
            ))}
            <option value={0}>{t.keepByHand}</option>
          </SelectBox>
        </div>
        )}
        {leaving && (
          <Modal title={t.moveTitle(instance.name, stageLabel(leaving))} description={t.leavesProduction} onClose={() => setLeaving(null)}
            footer={<><Button onClick={() => setLeaving(null)}>{c.common.cancel}</Button><Button variant="danger" loading={save.isPending}
              onClick={() => save.mutate({ stage: leaving, confirm_production: true }, {
                onSuccess: () => { toast("success", t.moved(stageLabel(leaving))); setLeaving(null); },
                onError: (e) => toast("error", t.notMoved, errorText(e)),
              })}>{t.leave}</Button></>}>
            <Callout tone="warning">
              {t.leaveText}
            </Callout>
          </Modal>
        )}
      </div>
      <ResourcesCard instance={instance} />
    </div>
  );
}

// ---------------------------------------------------------------- access

function AccessTab({ instance }: { instance: Instance }) {
  const [shown, setShown] = useState(false);
  const { data, isFetching } = useCredentials(instance.name, shown);
  const t = useT(words).access;
  return (
    <div className="stack">
    <div className="card">
      <div className="card-head"><h2>{t.title}</h2><span className="spacer" /><span className="sub">{t.recorded}</span></div>
      <div className="card-body">
        {!shown ? (
          <Button icon={<KeyRound />} onClick={() => setShown(true)}>{t.show}</Button>
        ) : !data ? (
          isFetching && <Skeleton height={100} />
        ) : (
          <dl className="dl">
            <dt>{t.address}</dt><dd>{instance.url ? <CopyField value={instance.url} /> : "—"}</dd>
            <dt>{t.login}</dt><dd><CopyField value={data.login} /></dd>
            <dt>{t.password}</dt><dd><CopyField value={data.admin_password} secret /></dd>
            <dt>{t.master}</dt><dd><CopyField value={data.master_password} secret /> <span className="xsmall faint">{t.forManager}</span></dd>
            <dt>{t.ports}</dt><dd className="mono small" dir="ltr">{Object.entries(data.ports).map(([k, v]) => `${k} ${v}`).join(" · ")}</dd>
          </dl>
        )}
      </div>
    </div>
    </div>
  );
}

// ---------------------------------------------------------------- dialogs

function UpgradeDialog({ onClose, onRun }: { onClose: () => void; onRun: (modules: string) => void }) {
  const [modules, setModules] = useState("");
  const t = useT(words).upgrade;
  const c = useT(common).common;
  return (
    <Modal title={t.title} description={t.text} onClose={onClose}
      footer={<><Button onClick={onClose}>{c.cancel}</Button><Button variant="primary" disabled={!modules.trim()} onClick={() => { onRun(modules.trim()); onClose(); }}>{t.run}</Button></>}>
      <Field label={t.modules} hint={<>{t.hint(<code key="all">all</code>)}</>}>
        <Input className="mono" dir="ltr" autoFocus placeholder="sale_custom,stock_custom" value={modules} onChange={(e) => setModules(e.target.value)} />
      </Field>
    </Modal>
  );
}

function BranchDialog({ instance, onClose, onRun }: { instance: Instance; onClose: () => void; onRun: (branch: string) => void }) {
  const [branch, setBranch] = useState("");
  const [branches, setBranches] = useState<string[] | null>(null);
  const [error, setError] = useState("");
  const t = useT(words).branch;
  const c = useT(common).common;
  if (branches === null && !error && instance.git.repository)
    api.get<{ branches: string[] }>(`/repositories/branches?url=${encodeURIComponent(instance.git.repository)}`).then((r) => setBranches(r.branches), (e) => setError(errorText(e)));
  return (
    <Modal title={t.title} description={t.text} onClose={onClose}
      footer={<><Button onClick={onClose}>{c.cancel}</Button><Button variant="primary" disabled={!branch} onClick={() => { onRun(branch); onClose(); }}>{t.run}</Button></>}>
      {error ? <Callout tone="danger">{error}</Callout> : (
        <Field label={t.branch}>
          <SelectBox value={branch} onValueChange={(value) => setBranch(value)} disabled={!branches}>
            <option value="">{branches ? t.choose : t.loading}</option>
            {branches?.filter((b) => b !== instance.git.branch).map((b) => <option key={b}>{b}</option>)}
          </SelectBox>
        </Field>
      )}
    </Modal>
  );
}

const REPOSITORY_ADDRESS = /^(https:\/\/github\.com\/|git@github\.com:)[\w.-]+\/[\w.-]+/;

/** Give an instance of plain Odoo a repository: pick it (or paste its address), then a branch. */
function ConnectDialog({ onClose, onRun }: { onClose: () => void; onRun: (body: Record<string, unknown>) => void }) {
  const [repo, setRepo] = useState("");
  const [picking, setPicking] = useState(false);
  const [branch, setBranch] = useState("");
  const [branches, setBranches] = useState<string[] | null>(null);
  const [error, setError] = useState("");
  const [install, setInstall] = useState(true);
  const t = useT(words).connect;
  const c = useT(common).common;
  const address = useDebounced(repo.trim());
  useEffect(() => {
    setBranches(null);
    setError("");
    if (!REPOSITORY_ADDRESS.test(address)) return;
    let current = true;
    api.get<{ branches: string[]; default: string }>(`/repositories/branches?url=${encodeURIComponent(address)}`).then(
      (r) => { if (current) { setBranches(r.branches); setBranch((b) => (b && r.branches.includes(b) ? b : r.default || r.branches[0] || "")); } },
      (e) => { if (current) setError(errorText(e)); },
    );
    return () => { current = false; };
  }, [address]);
  return (
    <Modal title={t.title} description={t.text} onClose={onClose}
      footer={<><Button onClick={onClose}>{c.cancel}</Button><Button variant="primary" disabled={!branches || !branch} onClick={() => { onRun({ repo: repo.trim(), branch, install_modules: install }); onClose(); }}>{t.run}</Button></>}>
      <div className="stack">
        <Field label={t.repository} error={error} hint={branches ? t.found(branches.length) : t.hint}>
          <div className="stack-sm">
            <Input  dir="ltr" autoFocus placeholder="https://github.com/acme/odoo-addons" value={repo} aria-invalid={!!error}
              onChange={(e) => setRepo(e.target.value)} onFocus={() => setPicking(true)} onBlur={() => setPicking(false)} />
            {picking && <RepositoryPicker typed={repo.trim()} onPick={(r) => { setRepo(r.url); setBranch(r.default_branch); setPicking(false); }} />}
          </div>
        </Field>
        <Field label={t.branch}>
          <SelectBox value={branch} onValueChange={(value) => setBranch(value)} disabled={!branches}>
            {!branches && <option value="">{REPOSITORY_ADDRESS.test(address) && !error ? t.loading : t.first}</option>}
            {branches?.map((b) => <option key={b}>{b}</option>)}
          </SelectBox>
        </Field>
        <Switch checked={install} onChange={setInstall} label={t.install} />
      </div>
    </Modal>
  );
}

export function SubmoduleDialog({ version, onClose, onRun }: { version: string; onClose: () => void; onRun: (body: Record<string, unknown>) => void }) {
  const [url, setUrl] = useState("");
  const [path, setPath] = useState("");
  // the folder shows the repository's own name (the server's choice when none is given) until typed in
  const [pathEdited, setPathEdited] = useState(false);
  const shownPath = pathEdited ? path : repositoryShortName(url);
  const [branch, setBranch] = useState(version);
  const t = useT(words).submodule;
  const c = useT(common).common;
  return (
    <Modal title={t.title} description={t.text} onClose={onClose}
      footer={<><Button onClick={onClose}>{c.cancel}</Button><Button variant="primary" disabled={!url} onClick={() => { onRun({ url, path: pathEdited ? path.trim() : "", branch }); onClose(); }}>{t.add}</Button></>}>
      <div className="stack">
        <Field label={t.repository}><Input dir="ltr" autoFocus placeholder="https://github.com/OCA/web" value={url} onChange={(e) => setUrl(e.target.value)} /></Field>
        <div className="grid-2">
          <Field label={t.folder} hint={t.folderHint}>
            <Input dir="ltr" clearable placeholder="oca/web" value={shownPath} onChange={(e) => { setPathEdited(true); setPath(e.target.value); }} />
          </Field>
          <Field label={t.branch}><Input dir="ltr" value={branch} onChange={(e) => setBranch(e.target.value)} /></Field>
        </div>
      </div>
    </Modal>
  );
}

function DeleteDialog({ name, production, after = "/", onClose }: { name: string; production: boolean; after?: string; onClose: () => void }) {
  const [typed, setTyped] = useState("");
  const [typedStage, setTypedStage] = useState("");
  const confirmed = typed === name && (!production || typedStage === "production");
  const [withoutBackup, setWithoutBackup] = useState(false);
  const can = useCan();
  const navigate = useNavigate();
  const toast = useToast();
  const t = useT(words).remove;
  const c = useT(common).common;
  const remove = useCall(
    () =>
      api.del<Job>(`/instances/${name}`, {
        confirm: name,
        ...(production ? { confirm_stage: "production" } : {}),
        ...(withoutBackup ? { without_backup: true } : {}),
      }),
    [keys.jobs()],
  );
  return (
    <Modal title={t.title(name)} onClose={onClose}
      footer={<><Button onClick={onClose}>{c.cancel}</Button><Button variant="danger" icon={<Trash2 />} loading={remove.isPending} disabled={!confirmed}
        onClick={() => remove.mutate(undefined, { onSuccess: () => { toast("success", t.started, t.startedText); navigate(after, { viewTransition: true }); }, onError: (e) => toast("error", t.notDeleted, errorText(e)) })}>{t.delete}</Button></>}>
      <div className="stack">
        <Callout tone="danger">
          <b>{t.removed}</b>{t.backupFirst}
          {withoutBackup ? t.evenIfFails : t.nothingIfFails}
        </Callout>
        {production && (
          <Callout tone="danger">
            <b>{t.productionWarning}</b>{t.productionText}
          </Callout>
        )}
        <Field label={<>{t.typeName(<code key="n">{name}</code>)}</>}>
          <Input dir="ltr" autoFocus value={typed} onChange={(e) => setTyped(e.target.value)} />
        </Field>
        {production && (
          <Field label={<>{t.typeProduction(<code key="p">production</code>)}</>}>
            <Input dir="ltr" value={typedStage} onChange={(e) => setTypedStage(e.target.value)} />
          </Field>
        )}
        {can("backups.manage") && (
          <Switch checked={withoutBackup} onChange={setWithoutBackup} label={t.withoutBackup} />
        )}
      </div>
    </Modal>
  );
}
