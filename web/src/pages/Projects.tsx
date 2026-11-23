// The Projects pages (the Odoo.sh model): the list of projects (one GitHub repository each), a project (its branches
// by stage in a workspace, components/ProjectWorkspace.tsx), the project's settings, and the new-project wizard (link
// a repository, or create a new, empty one through the GitHub App).

import {
  Archive,
  ArrowUpRight,
  Boxes,
  ChevronRight,
  FolderGit2,
  FolderPlus,
  GitBranch,
  Hammer,
  Layers,
  RefreshCw,
  RotateCcw,
  Search,
  ShieldCheck,
  UserRoundCheck,
  Trash2,
} from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { api, ApiError, errorText } from "@/api/client";
import { keys, useCall, useInstances, useProject, useProjects, useRepositoryOwners, useVersions } from "@/api/queries";
import type { Instance, Project, ProjectDetail, ProjectMeta, ProjectOptions, Stage } from "@/api/types";
import { AccessTab } from "@/components/ProjectAccess";
import { ClientsTab, ClosedNotice } from "@/components/ClientAccess";
import { GitHubCheckCard } from "@/components/GitHubCheckCard";
import { ProjectAuditLink } from "@/components/ProjectAuditLink";
import { InstanceCard } from "@/components/InstanceCard";
import { ProjectBackups } from "@/components/ProjectBackups";
import { ProjectBuildDefaults } from "@/components/BuildOptions";
import { ProjectResources } from "@/components/ProjectResources";
import { ProjectBuilds } from "@/components/ProjectBuilds";
import { ProjectNotifications } from "@/components/ProjectNotifications";
import { ProjectStatus, ProjectStatusTabs } from "@/components/ProjectStatus";
import { ProjectWorkspace, type ProjectHeadBranch } from "@/components/ProjectWorkspace";
import { PullRequestPreviews } from "@/components/PullRequestPreviews";
import { StagingRefresh } from "@/components/StagingRefresh";
import { Page, PageHead, useCan, useSubCrumb } from "@/components/Shell";
import { STAGE_ICON, STAGE_LABEL } from "@/components/StageTag";
import { Button, Callout, Empty, Field, Skeleton, Status, Switch, Tag } from "@/components/app/basics";
import { Input } from "@/components/ui/input";
import { SelectBox } from "@/components/app/select";
import { Modal, useToast } from "@/components/app/overlay";
import { TabInk } from "@/components/app/motion";
import { useT } from "@/i18n";
import common from "@/i18n/common";
import buildWords from "@/i18n/builds";
import words from "@/i18n/projects";
import clientWords from "@/i18n/clients";
import { repositoryShortName } from "@/lib/naming";
import { RepositoryPicker } from "./NewInstance";

const STAGES: Stage[] = ["production", "staging", "development"];
const ASLEEP = ["paused", "switched_off", "stopped"];
const WORKING = ["busy", "creating", "starting"];

function useInstanceMap() {
  const { data } = useInstances();
  return useMemo(() => new Map((data ?? []).map((i) => [i.name, i])), [data]);
}

// ---------------------------------------------------------------- the list

function ProjectCard({ project, instances }: { project: Project; instances: Map<string, Instance> }) {
  const t = useT(words).list;
  const members = project.instances.names.map((n) => instances.get(n)).filter((i): i is Instance => !!i);
  const running = members.filter((i) => i.status === "online").length;
  const asleep = members.filter((i) => ASLEEP.includes(i.status)).length;
  const working = members.filter((i) => WORKING.includes(i.status)).length;
  return (
    <article className="card link project-card">
      <div className="pc-head">
        <span className="pc-icon"><FolderGit2 /></span>
        <div className="pc-title">
          <Link to={`/projects/${project.id}`} viewTransition className="pc-name pc-open truncate">{project.name}</Link>
          <bdi className="pc-repo truncate" dir="ltr">{project.repository.full_name}</bdi>
        </div>
        <ChevronRight className="pc-chevron flip-rtl" />
      </div>
      <div className="row-wrap">
        <Tag mono>{project.odoo.version}</Tag>
        <Tag>{project.odoo.edition === "enterprise" ? "Enterprise" : "Community"}</Tag>
        {project.origin === "created" && <Tag>{t.created}</Tag>}
        {project.attention.length > 0 && <Tag tone="negative">{t.attention(project.attention.length)}</Tag>}
      </div>
      <div className="pc-stages" aria-label={t.branches}>
        {STAGES.map((stage) => {
          const Icon = STAGE_ICON[stage];
          return (
            <div key={stage} className={`pc-stage ${stage}`}>
              <span className="k"><Icon strokeWidth={1.75} /> <span className="truncate">{STAGE_LABEL[stage]}</span></span>
              <span className="v">{project.branches[stage]}</span>
            </div>
          );
        })}
      </div>
      <div className="pc-foot">
        {members.length ? (
          <>
            {running > 0 && <Status status="online" label={t.running(running)} />}
            {working > 0 && <Status status="busy" label={t.working(working)} />}
            {asleep > 0 && <Status status="paused" label={t.asleep(asleep)} />}
          </>
        ) : (
          <span className="faint small">{t.noInstances}</span>
        )}
      </div>
    </article>
  );
}

export function ProjectsPage() {
  const can = useCan();
  const { data, isPending } = useProjects();
  const instances = useInstanceMap();
  const [query, setQuery] = useState("");
  const t = useT(words).list;
  const shown = (data?.projects ?? []).filter((p) =>
    `${p.name} ${p.repository.full_name}`.toLowerCase().includes(query.trim().toLowerCase()),
  );
  const standalone = (data?.standalone ?? []).map((n) => instances.get(n)).filter((i): i is Instance => !!i);
  const newProject = can("projects.manage") && (
    <Button asChild variant="primary"><Link to="/projects/new" viewTransition>
      <FolderPlus /> {t.newProject}
    </Link></Button>
  );
  return (
    <Page>
      <PageHead title={t.title} actions={newProject}>
        {t.text}
      </PageHead>
      {(data?.projects.length ?? 0) > 6 && (
        <div className="toolbar">
          <div className="input-group">
            <Search />
            <Input placeholder={t.search} aria-label={t.search} value={query} onChange={(e) => setQuery(e.target.value)} />
          </div>
        </div>
      )}
      {isPending ? (
        <div className="project-grid">
          {[0, 1, 2].map((i) => (
            <div key={i} className="card pad stack">
              <Skeleton height={36} width="60%" />
              <Skeleton height={56} />
            </div>
          ))}
        </div>
      ) : shown.length ? (
        <div className="project-grid stagger">
          {shown.map((project) => (
            <ProjectCard key={project.id} project={project} instances={instances} />
          ))}
        </div>
      ) : (
        <div className="card">
          <Empty icon={<FolderGit2 />} title={data?.projects.length ? t.nothing : t.noneTitle} action={!data?.projects.length ? newProject : undefined}>
            {data?.projects.length ? undefined : t.noneText}
          </Empty>
        </div>
      )}
      {standalone.length > 0 && (
        <section className="stack">
          <div>
            <h2 className="section-title">{t.standalone}</h2>
            <p className="small muted">{t.standaloneText}</p>
          </div>
          <div className="instance-grid">
            {standalone.map((instance) => (
              <InstanceCard key={instance.name} instance={instance} />
            ))}
          </div>
        </section>
      )}
    </Page>
  );
}

// ---------------------------------------------------------------- a project

type Own = Partial<Record<keyof ProjectOptions, ProjectOptions[keyof ProjectOptions] | null>>;

function OptionRow({ label, children, onReset, own }: { label: string; children: ReactNode; onReset: () => void; own: boolean }) {
  const s = useT(words).settings;
  return (
    <div className="settings-row">
      <div>
        <div className="name">{label}</div>
        <div className="desc">{own ? s.ownValue : s.follows}</div>
      </div>
      <div className="row option-control">
        {children}
        {own && <Button size="sm" variant="ghost" icon={<RotateCcw />} onClick={onReset} aria-label={s.useDefault} title={s.useDefault} />}
      </div>
    </div>
  );
}

function SettingsTab({ project, meta }: { project: ProjectDetail; meta: ProjectMeta }) {
  const s = useT(words).settings;
  const can = useCan();
  const toast = useToast();
  const navigate = useNavigate();
  const { data: versions } = useVersions();
  const [name, setName] = useState(project.name);
  const [deleting, setDeleting] = useState(false);
  const [typed, setTyped] = useState("");
  const c = useT(common).common;
  const editable = can("projects.manage");
  const save = useCall((body: Record<string, unknown>) => api.patch<Project>(`/projects/${project.id}`, body), [keys.project(project.id), keys.projects]);
  const remove = useCall(() => api.del(`/projects/${project.id}`, { confirm: project.id }), [keys.projects]);
  const send = (body: Record<string, unknown>) =>
    save.mutate(body, { onSuccess: () => toast("success", s.saved), onError: (e) => toast("error", s.notSaved, errorText(e)) });
  const setOption = (key: keyof ProjectOptions, value: Own[keyof ProjectOptions]) => send({ options: { [key]: value } });
  const own = project.own_options;
  const value = <K extends keyof ProjectOptions>(key: K) => project.options[key];
  const flag = (key: "auto_development" | "upgrade_test_on_push" | "email_pusher") => (
    <OptionRow label={s[key]} own={key in own} onReset={() => setOption(key, null)}>
      <Switch checked={Boolean(value(key))} disabled={!editable} onChange={(v) => setOption(key, v)} label="" />
    </OptionRow>
  );
  const choice = (key: "staging_on_push" | "branch_deleted", options: string[]) => (
    <OptionRow label={s[key]} own={key in own} onReset={() => setOption(key, null)}>
      <SelectBox value={String(value(key))} disabled={!editable} onValueChange={(value) => setOption(key, value as ProjectOptions[typeof key])}>
        {options.map((o) => <option key={o} value={o}>{s[o as "rebuild"]}</option>)}
      </SelectBox>
    </OptionRow>
  );
  const days = (key: "max_auto_development" | "development_lifetime_days" | "staging_lifetime_days") => (
    <OptionRow label={s[key]} own={key in own} onReset={() => setOption(key, null)}>
      <Input className="number" type="number" min={0} max={key === "max_auto_development" ? 100 : 3650} disabled={!editable}
        defaultValue={value(key)} key={`${key}-${value(key)}`}
        onBlur={(e) => { const n = Number(e.target.value); if (Number.isInteger(n) && n >= 0 && n !== value(key)) setOption(key, n); }} />
    </OptionRow>
  );
  return (
    <div className="stack tab-panel">
      <section className="card">
        <div className="card-head"><h2>{s.general}</h2></div>
        <div className="card-body stack">
          <p className="small muted">{s.generalText}</p>
          <div className="form-grid">
            <Field label={s.name}>
              <Input  value={name} maxLength={60} disabled={!editable} onChange={(e) => setName(e.target.value)}
                onBlur={() => name.trim() && name.trim() !== project.name && send({ name: name.trim() })} />
            </Field>
            <Field label={s.version}>
              <SelectBox dir="ltr" value={project.odoo.version} disabled={!editable} onValueChange={(value) => send({ odoo_version: value })}>
                {(versions ?? [{ version: project.odoo.version, enterprise: true }]).map((v) => <option key={v.version}>{v.version}</option>)}
              </SelectBox>
            </Field>
            <Field label={s.edition}>
              <SelectBox value={project.odoo.edition} disabled={!editable} onValueChange={(value) => send({ edition: value })}>
                <option value="community">Community</option>
                <option value="enterprise">Enterprise</option>
              </SelectBox>
            </Field>
          </div>
        </div>
      </section>
      <section className="card">
        <div className="card-head"><h2>{s.automation}</h2></div>
        <div className="card-body stack">
          <p className="small muted">{s.automationText}</p>
          <div className="settings-list card-flush">
            {flag("auto_development")}
            {days("max_auto_development")}
            {choice("staging_on_push", ["rebuild", "keep_data"])}
            {choice("branch_deleted", ["keep", "archive", "delete"])}
            {flag("upgrade_test_on_push")}
            {flag("email_pusher")}
          </div>
        </div>
      </section>
      <ProjectBuildDefaults project={project} meta={meta} editable={editable} />
      <ProjectResources project={project} meta={meta} />
      <StagingRefresh project={project} />
      <PullRequestPreviews project={project} />
      <section className="card">
        <div className="card-head"><h2>{s.lifetimes}</h2></div>
        <div className="card-body stack">
          <p className="small muted">{s.lifetimesText}</p>
          <div className="settings-list card-flush">
            {days("development_lifetime_days")}
            {days("staging_lifetime_days")}
          </div>
        </div>
      </section>
      <GitHubCheckCard project={project} />
      <section className="card">
        <div className="card-head"><h2 className="spacer">{s.access}</h2>
          {can("projects.access") && <Button asChild size="sm"><Link to={`/projects/${project.id}/access`} viewTransition><ShieldCheck /> {s.manageAccess}</Link></Button>}
        </div>
        <div className="card-body"><p className="small muted">{project.access.mode === "restricted" ? s.accessRestricted : s.accessText}</p></div>
      </section>
      {editable && (
        <section className="card danger-zone">
          <div className="card-head"><h2>{s.danger}</h2></div>
          <div className="card-body row-wrap">
            <p className="small muted spacer">{s.dangerText}</p>
            <Button variant="danger" icon={<Trash2 />} disabled={project.instances.total > 0} onClick={() => setDeleting(true)}>{s.deleteProject}</Button>
          </div>
        </section>
      )}
      {deleting && (
        <Modal title={s.deleteTitle(project.name)} onClose={() => setDeleting(false)}
          footer={<><Button onClick={() => setDeleting(false)}>{c.cancel}</Button>
            <Button variant="danger" icon={<Trash2 />} disabled={typed !== project.id} loading={remove.isPending}
              onClick={() => remove.mutate(undefined, {
                onSuccess: () => { toast("success", s.deleted); navigate("/projects", { viewTransition: true }); },
                onError: (e) => toast("error", s.notDeleted, errorText(e)),
              })}>{s.deleteProject}</Button></>}>
          <div className="stack">
            <p className="small muted">{s.dangerText}</p>
            <Field label={<>{s.typeId(<code key="id">{project.id}</code>)}</>}>
              <Input dir="ltr" autoFocus value={typed} onChange={(e) => setTyped(e.target.value)} />
            </Field>
          </div>
        </Modal>
      )}
    </div>
  );
}

export function ProjectPage() {
  const { id = "", tab: pageTab = "", branch, view = "" } = useParams();
  // a branch (/projects/<id>/branches/<branch>/<view>) is shown in the Branches tab, the workspace
  const tab = branch === undefined && ["builds", "status", "notifications", "settings", "backups", "access", "clients"].includes(pageTab) ? pageTab : "";
  const can = useCan();
  const { data, isPending, error, refetch } = useProject(id);
  const t = useT(words).page;
  const b = useT(buildWords).builds;
  const c = useT(clientWords).staff;
  const [refreshing, setRefreshing] = useState(false);
  useSubCrumb(data?.project.name ?? null);
  if (isPending)
    return (
      <Page>
        <Skeleton height={44} width="40%" />
        <Skeleton height={240} />
      </Page>
    );
  if (!data || (error instanceof ApiError && error.status === 404))
    return (
      <Page>
        <div className="card">
          <Empty icon={<FolderGit2 />} title={t.notFound} action={<Button asChild><Link to="/projects" viewTransition>{t.allProjects}</Link></Button>}>
            {t.notFoundText}
          </Empty>
        </div>
      </Page>
    );
  const { project, meta } = data;
  const mayManageAccess = meta.permissions ? meta.permissions.includes("projects.access") : can("projects.access");
  const mayManageBackups = meta.permissions ? meta.permissions.includes("backups.manage") : can("backups.manage");
  const mayManage = meta.permissions ? meta.permissions.includes("projects.manage") : can("projects.manage");
  const refresh = async () => {
    setRefreshing(true);
    try {
      await api.page(`/projects/${project.id}?refresh=1`);
      await refetch();
    } finally {
      setRefreshing(false);
    }
  };
  // the project's header and its tabs; on the Branches tab the workspace adds the chosen branch to it (its name, tags
  // and menu under the project's details, its commands under Open on GitHub)
  const head = (branchArea?: ProjectHeadBranch) => (
    <>
      <PageHead
        title={project.name}
        below={branchArea?.identity}
        side={branchArea?.tools}
        actions={
          <Button asChild><a href={project.repository.url} target="_blank" rel="noreferrer">
            <FolderGit2 /> {t.openOnGitHub} <ArrowUpRight className="flip-rtl" />
          </a></Button>
        }
      >
        <span className="row-wrap">
          <bdi dir="ltr" className="mono small">{project.repository.full_name}</bdi>
          <Tag mono>{project.odoo.version}</Tag>
          <Tag>{project.odoo.edition === "enterprise" ? "Enterprise" : "Community"}</Tag>
          <ProjectAuditLink project={project.id} />
        </span>
      </PageHead>
      <nav className="tabs" aria-label={project.name}>
        <Link to={`/projects/${project.id}`} viewTransition className={`tab ${tab === "" ? "active" : ""}`} aria-current={tab === "" ? "page" : undefined}>
          <GitBranch /> {t.branchesTab}
        </Link>
        <Link to={`/projects/${project.id}/builds`} viewTransition className={`tab ${tab === "builds" ? "active" : ""}`} aria-current={tab === "builds" ? "page" : undefined}>
          <Layers /> {b.tab}
        </Link>
        <ProjectStatusTabs project={project} tab={tab} manage={mayManage} />
        <Link to={`/projects/${project.id}/settings`} viewTransition className={`tab ${tab === "settings" ? "active" : ""}`} aria-current={tab === "settings" ? "page" : undefined}>
          <Hammer /> {t.settingsTab}
        </Link>
        {mayManageBackups && (
          <Link to={`/projects/${project.id}/backups`} viewTransition className={`tab ${tab === "backups" ? "active" : ""}`} aria-current={tab === "backups" ? "page" : undefined}>
            <Archive /> {t.backupsTab}
          </Link>
        )}
        {mayManageAccess && (
          <Link to={`/projects/${project.id}/access`} viewTransition className={`tab ${tab === "access" ? "active" : ""}`} aria-current={tab === "access" ? "page" : undefined}>
            <ShieldCheck /> {t.accessTab}
          </Link>
        )}
        {(project.client_access || can("clients.manage")) && (
          <Link to={`/projects/${project.id}/clients`} viewTransition className={`tab ${tab === "clients" ? "active" : ""}`} aria-current={tab === "clients" ? "page" : undefined}>
            <UserRoundCheck /> {c.clientsTitle}
          </Link>
        )}
        <TabInk />
      </nav>
      {tab !== "clients" && <ClosedNotice projectId={project.id} projectName={project.name} branch={null} access={project.client_access} />}
    </>
  );
  return (
    <Page wide>
      {tab !== "" && head()}
      {tab === "access" ? (
        <AccessTab key={project.id} project={project} permitted={mayManageAccess} />
      ) : tab === "clients" ? (
        <ClientsTab key={project.id} project={project} />
      ) : tab === "backups" && mayManageBackups ? (
        <ProjectBackups key={project.id} project={project} />
      ) : tab === "status" ? (
        <ProjectStatus key={project.id} project={project} />
      ) : tab === "notifications" && mayManage ? (
        <ProjectNotifications key={project.id} project={project} />
      ) : tab === "builds" ? (
        <ProjectBuilds key={project.id} project={project} />
      ) : tab === "settings" ? (
        <SettingsTab key={project.id} project={project} meta={meta} />
      ) : (
        <ProjectWorkspace project={project} meta={meta} selected={branch} view={view} onRefresh={refresh} refreshing={refreshing} backups={mayManageBackups}
          head={head} />
      )}
    </Page>
  );
}

// ---------------------------------------------------------------- the wizard

export function NewProjectPage() {
  const t = useT(words).wizard;
  const githubWord = useT(common).nav.github;
  useSubCrumb(t.title);
  const can = useCan();
  const toast = useToast();
  const navigate = useNavigate();
  const { data: versions } = useVersions();
  const [mode, setMode] = useState<"link" | "create">("link");
  const [repository, setRepository] = useState("");
  const [picking, setPicking] = useState(false);
  const [owner, setOwner] = useState("");
  const [repoName, setRepoName] = useState("");
  const [isPrivate, setPrivate] = useState(true);
  const [description, setDescription] = useState("");
  const [name, setName] = useState("");
  // the project's name shows the repository's own name (what the server calls it when none is given) until typed in
  const [nameEdited, setNameEdited] = useState(false);
  const [version, setVersion] = useState("");
  const [edition, setEdition] = useState<"community" | "enterprise">("community");
  const owners = useRepositoryOwners(mode === "create" && can("github.manage"));
  const [checking, setChecking] = useState(false);
  const chosenVersion = version || versions?.at(-1)?.version || "";
  const enterpriseAvailable = versions?.find((v) => v.version === chosenVersion)?.enterprise ?? false;
  const chosenOwner = owners.data?.owners.find((o) => o.account === owner) ?? owners.data?.owners.find((o) => o.possible) ?? owners.data?.owners[0];
  const create = useCall(
    () =>
      api.post<Project>("/projects", {
        ...(mode === "link"
          ? { repository: repository.trim() }
          : { create: { owner: chosenOwner?.account ?? "", name: repoName.trim(), private: isPrivate, description: description.trim() } }),
        ...(nameEdited && name.trim() ? { name: name.trim() } : {}),
        odoo_version: chosenVersion,
        edition: enterpriseAvailable ? edition : "community",
      }),
    [keys.projects],
  );
  const ready =
    !!chosenVersion &&
    (mode === "link" ? repository.trim().length > 2 : !!chosenOwner?.possible && /^[A-Za-z0-9._-]{1,100}$/.test(repoName.trim()));
  const checkAgain = async () => {
    setChecking(true);
    try {
      await api.page("/projects/repository-owners?refresh=1");
      await owners.refetch();
    } finally {
      setChecking(false);
    }
  };
  const suggestedName = repositoryShortName(mode === "link" ? repository : `${chosenOwner?.account ?? "owner"}/${repoName}`);
  const shownRepo = mode === "link" ? repository.trim().replace(/^https:\/\/github\.com\//, "") : `${chosenOwner?.account ?? "…"}/${repoName.trim() || "…"}`;
  return (
    <Page>
      <PageHead title={t.title}>{t.text}</PageHead>
      <div className="stack narrow-page">
        <section className="card">
          <div className="card-head"><FolderGit2 /><h2>{t.steps[0]}</h2></div>
          <div className="card-body form-grid">
            <div className="full grid-2" role="radiogroup" aria-label={t.steps[0]}>
              <button type="button" className="choice" role="radio" aria-checked={mode === "link"} aria-pressed={mode === "link"} onClick={() => setMode("link")}>
                <FolderGit2 />
                <span><span className="t">{t.link}</span><br /><span className="d">{t.linkText}</span></span>
              </button>
              <button type="button" className="choice" role="radio" aria-checked={mode === "create"} aria-pressed={mode === "create"} disabled={!can("github.manage")} onClick={() => setMode("create")}>
                <FolderPlus />
                <span><span className="t">{t.create}</span><br /><span className="d">{t.createText}</span></span>
              </button>
            </div>
            {mode === "link" ? (
              <Field className="full" label={t.repository} hint={t.repositoryHint}>
                <div className="stack-sm">
                  <Input  dir="ltr" autoFocus placeholder="https://github.com/acme/odoo-addons" value={repository}
                    onChange={(e) => setRepository(e.target.value)} onFocus={() => setPicking(true)} onBlur={() => setPicking(false)} />
                  {picking && <RepositoryPicker typed={repository.trim()} onPick={(r) => { setRepository(r.url); setPicking(false); }} />}
                </div>
              </Field>
            ) : owners.isPending ? (
              <Skeleton height={64} />
            ) : !owners.data?.owners.length ? (
              <Callout tone="warning" >
                {t.noApp} <Link to="/github" viewTransition>{githubWord}</Link>
              </Callout>
            ) : (
              <>
                <Field label={t.organisation}>
                  <SelectBox dir="ltr" value={chosenOwner?.account ?? ""} onValueChange={(value) => setOwner(value)}>
                    {owners.data.owners.map((o) => <option key={o.account} value={o.account}>{o.account}</option>)}
                  </SelectBox>
                </Field>
                <Field label={t.repositoryName} hint={t.repositoryNameHint}>
                  <Input dir="ltr" placeholder="client-erp" value={repoName} maxLength={100} onChange={(e) => setRepoName(e.target.value)} />
                </Field>
                {chosenOwner && !chosenOwner.possible && (
                  <div className="full">
                    <Callout tone="warning">
                      <span className="stack-sm">
                        <span>{chosenOwner.message}</span>
                        <span className="row-wrap">
                          {chosenOwner.url && (chosenOwner.url.startsWith("/") ? (
                            <Button asChild size="sm"><Link to={chosenOwner.url} viewTransition>{chosenOwner.label}</Link></Button>
                          ) : (
                            <Button asChild size="sm"><a href={chosenOwner.url} target="_blank" rel="noreferrer">{chosenOwner.label} <ArrowUpRight className="flip-rtl" /></a></Button>
                          ))}
                          {chosenOwner.accept_url && (
                            <Button asChild size="sm"><a href={chosenOwner.accept_url} target="_blank" rel="noreferrer">{t.acceptHere} <ArrowUpRight className="flip-rtl" /></a></Button>
                          )}
                          <Button size="sm" variant="ghost" icon={<RefreshCw />} loading={checking} onClick={checkAgain}>{t.checkAgain}</Button>
                        </span>
                      </span>
                    </Callout>
                  </div>
                )}
                <Field className="full" label={t.description}>
                  <Input value={description} maxLength={350} onChange={(e) => setDescription(e.target.value)} />
                </Field>
                <div className="full"><Switch checked={isPrivate} onChange={setPrivate} label={t.private} /></div>
              </>
            )}
          </div>
        </section>
        <section className="card">
          <div className="card-head"><Boxes /><h2>{t.steps[1]}</h2></div>
          <div className="card-body form-grid">
            <Field label={t.projectName} hint={t.projectNameHint}>
              <Input clearable value={nameEdited ? name : suggestedName} maxLength={60}
                onChange={(e) => { setNameEdited(true); setName(e.target.value); }} />
            </Field>
            <Field label={t.version}>
              <SelectBox dir="ltr" value={chosenVersion} onValueChange={(value) => setVersion(value)}>
                {(versions ?? []).map((v) => <option key={v.version}>{v.version}</option>)}
              </SelectBox>
            </Field>
            <Field label={t.edition}>
              <SelectBox value={enterpriseAvailable ? edition : "community"} onValueChange={(value) => setEdition(value as "community" | "enterprise")}>
                <option value="community">{t.community}</option>
                <option value="enterprise" disabled={!enterpriseAvailable}>{t.enterprise}</option>
              </SelectBox>
            </Field>
          </div>
        </section>
        <section className="card form-end">
          <div className="card-body row-wrap">
            <p className="small muted spacer">
              {mode === "link" ? t.summaryLink(<bdi key="r" dir="ltr" className="mono">{shownRepo || "…"}</bdi>) : t.summaryCreate(<bdi key="r" dir="ltr" className="mono">{shownRepo}</bdi>)}
            </p>
            <Button variant="primary" icon={<FolderPlus />} disabled={!ready} loading={create.isPending}
              onClick={() => create.mutate(undefined, {
                onSuccess: (project) => { toast("success", t.createdToast(project.name)); navigate(`/projects/${project.id}`, { viewTransition: true }); },
                onError: (e) => toast("error", t.notCreated, errorText(e)),
              })}>{t.createProject}</Button>
          </div>
        </section>
      </div>
    </Page>
  );
}
