// The Projects pages (the Odoo.sh model): the list of projects (one GitHub repository each), a project's branches by
// stage with what can be done to each, the project's settings, and the new-project wizard (link a repository, or
// create a new, empty one through the GitHub App).

import {
  ArrowUpRight,
  Boxes,
  ChevronRight,
  Ellipsis,
  EyeOff,
  FolderGit2,
  FolderPlus,
  GitBranch,
  Hammer,
  Lock,
  LockOpen,
  Plus,
  RefreshCw,
  RotateCcw,
  ScrollText,
  Search,
  ShieldCheck,
  Trash2,
} from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { api, ApiError, errorText } from "@/api/client";
import { keys, useCall, useInstances, useProject, useProjects, useRepositoryOwners, useVersions } from "@/api/queries";
import type { Instance, Job, Permission, Project, ProjectBranch, ProjectDetail, ProjectMeta, ProjectOptions, Stage } from "@/api/types";
import { LockTag } from "@/components/LockTag";
import { AccessTab, LockDialog, UnlockDialog } from "@/components/ProjectAccess";
import { InstanceCard } from "@/components/InstanceCard";
import { JobDrawer } from "@/components/JobDrawer";
import { Page, PageHead, useCan, useSubCrumb } from "@/components/Shell";
import { useMe } from "@/components/Session";
import { STAGE_ICON, STAGE_LABEL, StageTag } from "@/components/StageTag";
import { Button, Callout, Empty, Field, Skeleton, Status, Switch, Tag } from "@/components/ui/basics";
import { Menu, Modal, useToast } from "@/components/ui/overlay";
import { useT } from "@/i18n";
import common from "@/i18n/common";
import words from "@/i18n/projects";
import teamWords from "@/i18n/teams";
import { ago, count } from "@/lib/format";
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
    <Link className="btn primary" to="/projects/new" viewTransition>
      <FolderPlus /> {t.newProject}
    </Link>
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
            <input className="input" placeholder={t.search} aria-label={t.search} value={query} onChange={(e) => setQuery(e.target.value)} />
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

function BranchCommit({ branch }: { branch: ProjectBranch }) {
  const t = useT(words).page;
  if (!branch.commit) return <span className="faint small">{t.noCommit}</span>;
  return (
    <div className="branch-commit">
      <span className="sha">{branch.commit.sha}</span>
      {branch.commit.subject && <bdi className="truncate">{branch.commit.subject}</bdi>}
      {branch.commit.when && <span className="faint xsmall nowrap">{ago(branch.commit.when)}</span>}
    </div>
  );
}

type Dialog =
  | { kind: "lock"; branch: ProjectBranch }
  | { kind: "unlock"; branch: ProjectBranch }
  | { kind: "stage"; branch: ProjectBranch; stage: Stage }
  | { kind: "create"; branch: ProjectBranch }
  | { kind: "rebuild"; branch: ProjectBranch; instance: string }
  | { kind: "delete"; instance: Instance };

function BranchRow({
  branch,
  instances,
  onDialog,
}: {
  branch: ProjectBranch;
  instances: Map<string, Instance>;
  onDialog: (dialog: Dialog) => void;
}) {
  const roleCan = useCan();
  const can = (permission: Permission) => (branch.permissions ? branch.permissions.includes(permission) : roleCan(permission));
  const t = useT(words).page;
  const l = useT(teamWords);
  const members = branch.instances.map((n) => instances.get(n)).filter((i): i is Instance => !!i);
  const first = members[0];
  const lockedOut = !!branch.lock && !can("instances.logs");
  const me = useMe();
  // changing or removing a lock: whoever it allows (they keep projects.access in the branch) and super admins
  const mayUnlock = can("projects.access") || me.role === "super_admin";
  const others = STAGES.filter((s) => s !== branch.stage);
  return (
    <div className="branch-row" role="row">
      <div className="br-branch" role="cell">
        <GitBranch />
        <div className="stack-xs">
          <bdi className="br-name truncate" dir="ltr">{branch.name}</bdi>
          <div className="row-wrap xsmall">
            {branch.default && <Tag>{t.defaultBranch}</Tag>}
            {branch.lock && <LockTag lock={branch.lock} branch={branch.name} />}
            {branch.restricted && <span title={l.lock.restrictedHint}><Tag icon={<EyeOff />}>{l.lock.restricted}</Tag></span>}
            {branch.on_github === false && <Tag tone="negative">{t.deletedOnGitHub}</Tag>}
            {branch.auto_created && branch.instances.includes(branch.auto_created) && <span className="faint">{t.autoCreated}</span>}
          </div>
        </div>
      </div>
      <div className="br-commit" role="cell"><BranchCommit branch={branch} /></div>
      <div className="br-instance" role="cell">
        {members.length ? (
          <div className="stack-xs">
            {members.map((instance) => (
              <div key={instance.name} className="row small">
                <Status status={instance.status} label="" />
                <Link to={`/i/${instance.name}`} viewTransition className="truncate">{instance.name}</Link>
                {instance.stage !== branch.stage && <StageTag stage={instance.stage} />}
              </div>
            ))}
          </div>
        ) : (
          <span className="faint small">{t.noInstance}</span>
        )}
      </div>
      <div className="br-actions" role="cell">
        {first ? (
          first.url && first.status !== "switched_off" && !lockedOut ? (
            <a className="btn sm" href={first.url} target="_blank" rel="noreferrer">{t.open} <ArrowUpRight className="flip-rtl" /></a>
          ) : (
            <Link className="btn sm" to={`/i/${first.name}`} viewTransition>{t.details}</Link>
          )
        ) : (
          can("instances.create") && branch.on_github !== false && (
            <Button size="sm" variant="soft" icon={<Plus />} onClick={() => onDialog({ kind: "create", branch })}>{t.createInstance}</Button>
          )
        )}
        <Menu
          trigger={(toggle) => <Button size="sm" variant="ghost" icon={<Ellipsis />} onClick={toggle} aria-label={t.moreActions(branch.name)} />}
        >
          {(close) => (
            <>
              {first && (
                <Link className="menu-item" to={`/i/${first.name}`} viewTransition onClick={close}><Boxes /> {t.details}</Link>
              )}
              {first && can("instances.logs") && (
                <Link className="menu-item" to={`/i/${first.name}/logs`} viewTransition onClick={close}><ScrollText /> {t.logs}</Link>
              )}
              {first && branch.stage === "staging" && can("instances.restore") && (
                <button type="button" className="menu-item" onClick={() => { close(); onDialog({ kind: "rebuild", branch, instance: first.name }); }}>
                  <RotateCcw /> {t.rebuild}
                </button>
              )}
              {can("projects.manage") && (
                <>
                  <hr /><div className="menu-label">{t.moveTo}</div>
                  {others.map((stage) => {
                    const Icon = STAGE_ICON[stage];
                    return (
                      <button key={stage} type="button" className="menu-item" onClick={() => { close(); onDialog({ kind: "stage", branch, stage }); }}>
                        <Icon /> {STAGE_LABEL[stage]}
                      </button>
                    );
                  })}
                </>
              )}
              {!first && can("instances.create") && branch.on_github !== false && (
                <button type="button" className="menu-item" onClick={() => { close(); onDialog({ kind: "create", branch }); }}>
                  <Plus /> {t.createInstance}
                </button>
              )}
              {roleCan("projects.access") && (branch.lock ? mayUnlock : can("projects.access")) && (
                <>
                  <hr />
                  {branch.lock ? (
                    <>
                      <button type="button" className="menu-item" onClick={() => { close(); onDialog({ kind: "lock", branch }); }}>
                        <Lock /> {l.lock.changeLock}
                      </button>
                      <button type="button" className="menu-item" onClick={() => { close(); onDialog({ kind: "unlock", branch }); }}>
                        <LockOpen /> {l.lock.unlock}
                      </button>
                    </>
                  ) : (
                    <button type="button" className="menu-item" onClick={() => { close(); onDialog({ kind: "lock", branch }); }}>
                      <Lock /> {l.lock.lock}
                    </button>
                  )}
                </>
              )}
              {first && can("instances.delete") && (
                <button type="button" className="menu-item danger" onClick={() => { close(); onDialog({ kind: "delete", instance: first }); }}>
                  <Trash2 /> {t.deleteInstance}
                </button>
              )}
            </>
          )}
        </Menu>
      </div>
    </div>
  );
}

function StageDialog({ project, meta, branch, stage, onClose }: { project: ProjectDetail; meta: ProjectMeta; branch: ProjectBranch; stage: Stage; onClose: () => void }) {
  const t = useT(words);
  const d = t.stageDialog;
  const c = useT(common).common;
  const toast = useToast();
  const [understood, setUnderstood] = useState(false);
  const production = stage === "production" || branch.stage === "production";
  const save = useCall(
    () => api.put<ProjectBranch>(`/projects/${project.id}/branches/${branch.name}/stage`, { stage, confirm_production: production }),
    [keys.project(project.id), keys.projects, keys.instances],
  );
  return (
    <Modal title={d.title(branch.name, STAGE_LABEL[stage])} onClose={onClose}
      footer={<><Button onClick={onClose}>{c.cancel}</Button>
        <Button variant={production ? "danger" : "primary"} loading={save.isPending} disabled={production && !understood}
          onClick={() => save.mutate(undefined, {
            onSuccess: () => { toast("success", t.page.stageChanged(branch.name, STAGE_LABEL[stage])); onClose(); },
            onError: (e) => toast("error", t.page.stageNotChanged, errorText(e)),
          })}>{d.move}</Button></>}>
      <div className="stack">
        {stage === "production" && (
          <Callout tone="warning">
            {d.production}
            {meta.production_limit ? <> {d.productionLimit(meta.production_limit)}</> : null}
          </Callout>
        )}
        {branch.stage === "production" && <Callout tone="warning">{d.leaving}</Callout>}
        {stage === "staging" && branch.instances.length > 0 && <p className="small muted">{d.staging}</p>}
        {stage === "development" && branch.instances.length > 0 && branch.stage !== "production" && <p className="small muted">{d.development}</p>}
        <p className="small">{branch.instances.length ? d.instances(branch.instances.join(", ")) : d.noInstances}</p>
        {production && <Switch checked={understood} onChange={setUnderstood} label={d.confirm} />}
      </div>
    </Modal>
  );
}

function CreateDialog({ project, branch, production, onClose, onJob }: { project: ProjectDetail; branch: ProjectBranch; production: string | null; onClose: () => void; onJob: (id: string) => void }) {
  const t = useT(words);
  const d = t.createDialog;
  const c = useT(common).common;
  const toast = useToast();
  const [demo, setDemo] = useState(project.options.development_demo_data);
  const [modules, setModules] = useState(true);
  const [name, setName] = useState("");
  const edition = project.odoo.edition === "enterprise" ? "Enterprise" : "Community";
  const blocked = branch.stage === "staging" && !production;
  const create = useCall(
    () =>
      api.post<Job>(`/projects/${project.id}/branches/${branch.name}/instances`, {
        ...(name.trim() ? { name: name.trim() } : {}),
        ...(branch.stage === "development" ? { demo } : {}),
        install_modules: modules,
      }),
    [keys.project(project.id), keys.instances],
  );
  return (
    <Modal title={d.title(branch.name)} description={d.text(STAGE_LABEL[branch.stage], project.odoo.version, edition)} onClose={onClose}
      footer={<><Button onClick={onClose}>{c.cancel}</Button>
        <Button variant="primary" icon={<Plus />} loading={create.isPending} disabled={blocked}
          onClick={() => create.mutate(undefined, {
            onSuccess: (job) => { toast("success", t.page.created(job.instance), t.page.createdText); onClose(); onJob(job.id); },
            onError: (e) => toast("error", t.page.notCreated, errorText(e)),
          })}>{d.create}</Button></>}>
      <div className="stack">
        {branch.stage === "production" && <Callout tone="info">{d.production}</Callout>}
        {branch.stage === "staging" && (production ? <Callout tone="info">{d.staging(production)}</Callout> : <Callout tone="warning">{d.stagingWithout}</Callout>)}
        {branch.stage === "development" && <p className="small muted">{d.development}</p>}
        <Field label={d.name} hint={d.nameHint}>
          <input className="input" dir="ltr" value={name} maxLength={40} onChange={(e) => setName(e.target.value)} />
        </Field>
        {branch.stage === "development" && <Switch checked={demo} onChange={setDemo} label={d.demo} />}
        <Switch checked={modules} onChange={setModules} label={d.modules} />
      </div>
    </Modal>
  );
}

function RebuildDialog({ project, instance, production, onClose, onJob }: { project: ProjectDetail; branch: ProjectBranch; instance: string; production: string | null; onClose: () => void; onJob: (id: string) => void }) {
  const t = useT(words);
  const d = t.rebuildDialog;
  const c = useT(common).common;
  const toast = useToast();
  const branch = useMemo(() => project.branches_list.find((b) => b.instances.includes(instance)), [project, instance]);
  const rebuild = useCall(
    () => api.post<Job>(`/projects/${project.id}/branches/${branch?.name ?? ""}/rebuild`, { instance }),
    [keys.instance(instance), keys.jobs(instance)],
  );
  return (
    <Modal title={d.title(instance)} onClose={onClose}
      footer={<><Button onClick={onClose}>{c.cancel}</Button>
        <Button variant="danger" icon={<RotateCcw />} loading={rebuild.isPending} disabled={!production}
          onClick={() => rebuild.mutate(undefined, {
            onSuccess: (job) => { toast("success", t.page.rebuildStarted); onClose(); onJob(job.id); },
            onError: (e) => toast("error", t.page.rebuildNotStarted, errorText(e)),
          })}>{d.rebuild}</Button></>}>
      <div className="stack">
        {production ? <p>{d.text(production)}</p> : <Callout tone="warning">{t.createDialog.stagingWithout}</Callout>}
        <Callout tone="warning">{d.lost}</Callout>
      </div>
    </Modal>
  );
}

function DeleteInstanceDialog({ instance, onClose, onJob }: { instance: Instance; onClose: () => void; onJob: (id: string) => void }) {
  const [typed, setTyped] = useState("");
  const [typedStage, setTypedStage] = useState("");
  const production = instance.stage === "production";
  const confirmed = typed === instance.name && (!production || typedStage === "production");
  const toast = useToast();
  const all = useT(words);
  const t = all.page;
  const s = all.settings;
  const c = useT(common).common;
  const remove = useCall(
    () => api.del<Job>(`/instances/${instance.name}`, { confirm: instance.name, ...(production ? { confirm_stage: "production" } : {}) }),
    [keys.jobs()],
  );
  return (
    <Modal title={t.deleteInstance} description={instance.name} onClose={onClose}
      footer={<><Button onClick={onClose}>{c.cancel}</Button>
        <Button variant="danger" icon={<Trash2 />} loading={remove.isPending} disabled={!confirmed}
          onClick={() => remove.mutate(undefined, {
            onSuccess: (job) => { onClose(); if (job) onJob(job.id); },
            onError: (e) => toast("error", t.deleteInstance, errorText(e)),
          })}>{t.deleteInstance}</Button></>}>
      <div className="stack">
        {production && <Callout tone="danger">{all.stageDialog.production}</Callout>}
        <Field label={<>{s.typeId(<code key="n">{instance.name}</code>)}</>}>
          <input className="input" dir="ltr" autoFocus value={typed} onChange={(e) => setTyped(e.target.value)} />
        </Field>
        {production && (
          <Field label={<>{s.typeId(<code key="p">production</code>)}</>}>
            <input className="input" dir="ltr" value={typedStage} onChange={(e) => setTypedStage(e.target.value)} />
          </Field>
        )}
      </div>
    </Modal>
  );
}

function BranchesTab({ project, meta, onRefresh, refreshing }: { project: ProjectDetail; meta: ProjectMeta; onRefresh: () => void; refreshing: boolean }) {
  const t = useT(words).page;
  const instances = useInstanceMap();
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const production = useMemo(() => {
    for (const branch of project.branches_list)
      if (branch.stage === "production")
        for (const name of branch.instances) if (instances.get(name)?.stage === "production" && instances.get(name)?.status !== "creating") return name;
    return null;
  }, [project, instances]);
  const running = project.instances.names.filter((n) => instances.get(n)?.status === "online").length;
  const capacity = meta.capacity;
  return (
    <div className="stack tab-panel">
      {capacity?.warning && capacity.message && (
        <Callout tone="warning">
          {t.capacity(capacity.message)} {running > 1 && t.parallel(running)}{" "}
          <Link to="/help/docs/ADMIN_GUIDE.md#how-many-instances-can-my-server-run" viewTransition>{t.howMany}</Link>
        </Callout>
      )}
      {meta.branches_error && <Callout tone="warning">{t.cannotRead} {meta.branches_error}</Callout>}
      <div className="row small faint">
        <span>{meta.branches_read_at ? t.readAt(ago(meta.branches_read_at)) : ""}</span>
        <span className="spacer" />
        <Button size="sm" variant="ghost" icon={<RefreshCw />} loading={refreshing} onClick={onRefresh}>{t.refresh}</Button>
      </div>
      {project.branches_list.length === 0 ? (
        <div className="card">
          <Empty icon={<GitBranch />} title={t.empty}>
            {t.emptyText}
          </Empty>
          <div className="card-body stack-sm">
            <p className="small muted">{t.pushHelp}</p>
            <pre className="terminal" dir="ltr">{`git init -b main\ngit add .\ngit commit -m "First modules"\ngit remote add origin ${project.repository.url}.git\ngit push -u origin main`}</pre>
          </div>
        </div>
      ) : (
        STAGES.map((stage) => {
          const listed = project.branches_list.filter((b) => b.stage === stage);
          return (
            <section key={stage} className={`card branch-group ${stage}`} aria-label={STAGE_LABEL[stage]}>
              <div className="card-head">
                <StageTag stage={stage} />
                <span className="faint small">{count(listed.length, "branch")}</span>
              </div>
              {listed.length ? (
                <div className="branch-table" role="table">
                  <div className="branch-row head" role="row">
                    <div role="columnheader">{t.branch}</div>
                    <div role="columnheader">{t.lastCommit}</div>
                    <div role="columnheader">{t.instance}</div>
                    <div role="columnheader" className="br-actions">{t.actions}</div>
                  </div>
                  {listed.map((branch) => (
                    <BranchRow key={branch.name} branch={branch} instances={instances} onDialog={setDialog} />
                  ))}
                </div>
              ) : (
                <p className="card-body small faint">{t.stageEmpty[stage]}</p>
              )}
            </section>
          );
        })
      )}
      {dialog?.kind === "lock" && <LockDialog project={project} branch={dialog.branch} onClose={() => setDialog(null)} />}
      {dialog?.kind === "unlock" && <UnlockDialog project={project} branch={dialog.branch} onClose={() => setDialog(null)} />}
      {dialog?.kind === "stage" && <StageDialog project={project} meta={meta} branch={dialog.branch} stage={dialog.stage} onClose={() => setDialog(null)} />}
      {dialog?.kind === "create" && <CreateDialog project={project} branch={dialog.branch} production={production} onClose={() => setDialog(null)} onJob={setJobId} />}
      {dialog?.kind === "rebuild" && <RebuildDialog project={project} branch={dialog.branch} instance={dialog.instance} production={production} onClose={() => setDialog(null)} onJob={setJobId} />}
      {dialog?.kind === "delete" && <DeleteInstanceDialog instance={dialog.instance} onClose={() => setDialog(null)} onJob={setJobId} />}
      {jobId && <JobDrawer id={jobId} onClose={() => setJobId(null)} />}
    </div>
  );
}

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

function SettingsTab({ project }: { project: ProjectDetail }) {
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
  const flag = (key: "auto_development" | "development_demo_data") => (
    <OptionRow label={s[key]} own={key in own} onReset={() => setOption(key, null)}>
      <Switch checked={Boolean(value(key))} disabled={!editable} onChange={(v) => setOption(key, v)} label="" />
    </OptionRow>
  );
  const choice = (key: "staging_on_push" | "branch_deleted", options: string[]) => (
    <OptionRow label={s[key]} own={key in own} onReset={() => setOption(key, null)}>
      <select className="select" value={String(value(key))} disabled={!editable} onChange={(e) => setOption(key, e.target.value as ProjectOptions[typeof key])}>
        {options.map((o) => <option key={o} value={o}>{s[o as "rebuild"]}</option>)}
      </select>
    </OptionRow>
  );
  const days = (key: "max_auto_development" | "development_lifetime_days" | "staging_lifetime_days") => (
    <OptionRow label={s[key]} own={key in own} onReset={() => setOption(key, null)}>
      <input className="input number" type="number" min={0} max={key === "max_auto_development" ? 100 : 3650} disabled={!editable}
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
              <input className="input" value={name} maxLength={60} disabled={!editable} onChange={(e) => setName(e.target.value)}
                onBlur={() => name.trim() && name.trim() !== project.name && send({ name: name.trim() })} />
            </Field>
            <Field label={s.version}>
              <select className="select" dir="ltr" value={project.odoo.version} disabled={!editable} onChange={(e) => send({ odoo_version: e.target.value })}>
                {(versions ?? [{ version: project.odoo.version, enterprise: true }]).map((v) => <option key={v.version}>{v.version}</option>)}
              </select>
            </Field>
            <Field label={s.edition}>
              <select className="select" value={project.odoo.edition} disabled={!editable} onChange={(e) => send({ edition: e.target.value })}>
                <option value="community">Community</option>
                <option value="enterprise">Enterprise</option>
              </select>
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
            {flag("development_demo_data")}
            {choice("staging_on_push", ["rebuild", "keep_data"])}
            {choice("branch_deleted", ["keep", "archive", "delete"])}
          </div>
        </div>
      </section>
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
      <section className="card">
        <div className="card-head"><h2 className="spacer">{s.access}</h2>
          {can("projects.access") && <Link className="btn sm" to={`/projects/${project.id}/access`} viewTransition><ShieldCheck /> {s.manageAccess}</Link>}
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
              <input className="input" dir="ltr" autoFocus value={typed} onChange={(e) => setTyped(e.target.value)} />
            </Field>
          </div>
        </Modal>
      )}
    </div>
  );
}

export function ProjectPage() {
  const { id = "", tab = "" } = useParams();
  const can = useCan();
  const { data, isPending, error, refetch } = useProject(id);
  const t = useT(words).page;
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
          <Empty icon={<FolderGit2 />} title={t.notFound} action={<Link className="btn" to="/projects" viewTransition>{t.allProjects}</Link>}>
            {t.notFoundText}
          </Empty>
        </div>
      </Page>
    );
  const { project, meta } = data;
  const mayManageAccess = meta.permissions ? meta.permissions.includes("projects.access") : can("projects.access");
  const refresh = async () => {
    setRefreshing(true);
    try {
      await api.page(`/projects/${project.id}?refresh=1`);
      await refetch();
    } finally {
      setRefreshing(false);
    }
  };
  return (
    <Page>
      <PageHead
        title={project.name}
        actions={
          <a className="btn" href={project.repository.url} target="_blank" rel="noreferrer">
            <FolderGit2 /> {t.openOnGitHub} <ArrowUpRight className="flip-rtl" />
          </a>
        }
      >
        <span className="row-wrap">
          <bdi dir="ltr" className="mono small">{project.repository.full_name}</bdi>
          <Tag mono>{project.odoo.version}</Tag>
          <Tag>{project.odoo.edition === "enterprise" ? "Enterprise" : "Community"}</Tag>
        </span>
      </PageHead>
      <nav className="tabs" aria-label={project.name}>
        <Link to={`/projects/${project.id}`} viewTransition className={`tab ${tab === "" ? "active" : ""}`} aria-current={tab === "" ? "page" : undefined}>
          <GitBranch /> {t.branchesTab}
        </Link>
        <Link to={`/projects/${project.id}/settings`} viewTransition className={`tab ${tab === "settings" ? "active" : ""}`} aria-current={tab === "settings" ? "page" : undefined}>
          <Hammer /> {t.settingsTab}
        </Link>
        {mayManageAccess && (
          <Link to={`/projects/${project.id}/access`} viewTransition className={`tab ${tab === "access" ? "active" : ""}`} aria-current={tab === "access" ? "page" : undefined}>
            <ShieldCheck /> {t.accessTab}
          </Link>
        )}
      </nav>
      {tab === "access" ? (
        <AccessTab key={project.id} project={project} permitted={mayManageAccess} />
      ) : tab === "settings" ? (
        <SettingsTab key={project.id} project={project} />
      ) : (
        <BranchesTab project={project} meta={meta} onRefresh={refresh} refreshing={refreshing} />
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
        ...(name.trim() ? { name: name.trim() } : {}),
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
                  <input className="input" dir="ltr" autoFocus placeholder="https://github.com/acme/odoo-addons" value={repository}
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
                  <select className="select" dir="ltr" value={chosenOwner?.account ?? ""} onChange={(e) => setOwner(e.target.value)}>
                    {owners.data.owners.map((o) => <option key={o.account} value={o.account}>{o.account}</option>)}
                  </select>
                </Field>
                <Field label={t.repositoryName} hint={t.repositoryNameHint}>
                  <input className="input" dir="ltr" placeholder="client-erp" value={repoName} maxLength={100} onChange={(e) => setRepoName(e.target.value)} />
                </Field>
                {chosenOwner && !chosenOwner.possible && (
                  <div className="full">
                    <Callout tone="warning">
                      <span className="stack-sm">
                        <span>{chosenOwner.message}</span>
                        <span className="row-wrap">
                          {chosenOwner.url && (chosenOwner.url.startsWith("/") ? (
                            <Link className="btn sm" to={chosenOwner.url} viewTransition>{chosenOwner.label}</Link>
                          ) : (
                            <a className="btn sm" href={chosenOwner.url} target="_blank" rel="noreferrer">{chosenOwner.label} <ArrowUpRight className="flip-rtl" /></a>
                          ))}
                          {chosenOwner.accept_url && (
                            <a className="btn sm" href={chosenOwner.accept_url} target="_blank" rel="noreferrer">{t.acceptHere} <ArrowUpRight className="flip-rtl" /></a>
                          )}
                          <Button size="sm" variant="ghost" icon={<RefreshCw />} loading={checking} onClick={checkAgain}>{t.checkAgain}</Button>
                        </span>
                      </span>
                    </Callout>
                  </div>
                )}
                <Field className="full" label={t.description}>
                  <input className="input" value={description} maxLength={350} onChange={(e) => setDescription(e.target.value)} />
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
              <input className="input" value={name} maxLength={60} onChange={(e) => setName(e.target.value)} />
            </Field>
            <Field label={t.version}>
              <select className="select" dir="ltr" value={chosenVersion} onChange={(e) => setVersion(e.target.value)}>
                {(versions ?? []).map((v) => <option key={v.version}>{v.version}</option>)}
              </select>
            </Field>
            <Field label={t.edition}>
              <select className="select" value={enterpriseAvailable ? edition : "community"} onChange={(e) => setEdition(e.target.value as "community" | "enterprise")}>
                <option value="community">{t.community}</option>
                <option value="enterprise" disabled={!enterpriseAvailable}>{t.enterprise}</option>
              </select>
            </Field>
          </div>
        </section>
        <section className="card">
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
