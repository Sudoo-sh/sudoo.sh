// A project's workspace, the Odoo.sh way: its branches in a sidebar, grouped by stage (production, staging,
// development), and the chosen branch beside them with its instance's tabs; the branch's name, tags, menu and commands
// to copy are in the project's header (BranchIdentity and BranchTools, given to `head`). A branch dragged
// onto another stage asks for confirmation (StageDialog) before its stage changes; the branch's menu does the same
// for keyboards and screen readers, and the drag itself works with a mouse, a finger (long press) and the keyboard.

import {
  DndContext,
  DragOverlay,
  KeyboardSensor,
  PointerSensor,
  TouchSensor,
  pointerWithin,
  rectIntersection,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
  type Announcements,
  type CollisionDetection,
  type DropAnimation,
  type KeyboardCoordinateGetter,
  type UniqueIdentifier,
} from "@dnd-kit/core";
import {
  Archive,
  Check,
  ChevronDown,
  Copy,
  Ellipsis,
  EyeOff,
  GitBranch,
  GitCommitHorizontal,
  GitCompareArrows,
  ListChecks,
  History,
  Lock,
  LockOpen,
  Plus,
  RefreshCw,
  RotateCcw,
  ScrollText,
  Search,
  SlidersHorizontal,
  Trash2,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { useBranchHistory, useInstance, useInstances } from "@/api/queries";
import type { Instance, Permission, ProjectBranch, ProjectDetail, ProjectMeta, Stage } from "@/api/types";
import { BranchCommands } from "@/components/BranchCommands";
import { BranchHistory } from "@/components/BuildHistory";
import { BranchSettings } from "@/components/BuildOptions";
import { BranchResources } from "@/components/ProjectResources";
import { BranchCommit, CreateDialog, DeleteInstanceDialog, RebuildDialog, StageDialog, type BranchDialog } from "@/components/BranchDialogs";
import { CompareDialog } from "@/components/BranchCompare";
import { JobDrawer } from "@/components/JobDrawer";
import { PromotionDialog } from "@/components/PromotionChecklist";
import { LockTag } from "@/components/LockTag";
import { ClientTag } from "@/components/ClientAccess";
import { LockDialog, UnlockDialog } from "@/components/ProjectAccess";
import { useProjectBackups } from "@/components/ProjectBackups";
import { InsidePage, useCan } from "@/components/Shell";
import { useMe } from "@/components/Session";
import { STAGE_ICON, STAGE_LABEL, StageTag } from "@/components/StageTag";
import { Button, Callout, Empty, Field, Tag } from "@/components/app/basics";
import { SelectBox } from "@/components/app/select";
import { Menu, MenuItem, MenuLabel, MenuSeparator, Modal } from "@/components/app/overlay";
import { Input } from "@/components/ui/input";
import { useT } from "@/i18n";
import common from "@/i18n/common";
import buildWords from "@/i18n/builds";
import optionWords from "@/i18n/build-options";
import words from "@/i18n/projects";
import promotionWords from "@/i18n/promotion";
import teamWords from "@/i18n/teams";
import { ago, count, statusLabel } from "@/lib/format";
import { useMedia } from "@/lib/media";
import { InstanceView } from "@/pages/Instance";

const STAGES: Stage[] = ["production", "staging", "development"];
const ASLEEP = ["paused", "switched_off", "stopped"];
const WORKING = ["busy", "creating", "starting"];
const PHONE = "(max-width: 767px)";
const REDUCED_MOTION = "(prefers-reduced-motion: reduce)";

/** A branch's page in its project. The branch is one path segment, whatever "/" its name holds. */
export function branchPath(project: string, branch: string, view = "", instance?: string | null) {
  const query = instance ? `?instance=${encodeURIComponent(instance)}` : "";
  return `/projects/${project}/branches/${encodeURIComponent(branch)}${view ? `/${view}` : ""}${query}`;
}

/** How a branch's instances are, as one dot: green online, red failing, amber working, grey hollow asleep. */
export type Dot = "online" | "failed" | "busy" | "asleep" | "none";
export function dotOf(members: Instance[]): Dot {
  if (!members.length) return "none";
  if (members.some((i) => i.status === "unreachable" || i.gateway_error || (i.update_failure && !i.update_failure.rolled_back))) return "failed";
  if (members.some((i) => WORKING.includes(i.status) || i.current_job)) return "busy";
  if (members.some((i) => i.status === "online")) return "online";
  if (members.every((i) => ASLEEP.includes(i.status))) return "asleep";
  return "failed";
}

/** The production instance a staging branch is built from: the first production branch's production instance. */
export function productionOf(branches: ProjectBranch[], instances: Map<string, Instance>): string | null {
  for (const branch of branches)
    if (branch.stage === "production")
      for (const name of branch.instances) if (instances.get(name)?.stage === "production" && instances.get(name)?.status !== "creating") return name;
  return null;
}

/** What the caller may do in a branch: its own permissions (role, access, lock) when the server sends them. */
export function useBranchCan() {
  const roleCan = useCan();
  return (branch: ProjectBranch, permission: Permission) => (branch.permissions ? branch.permissions.includes(permission) : roleCan(permission));
}

/** A mouse or a pen drags at once (after a few pixels); a finger goes through the TouchSensor (a long press), so
 *  that swiping the list still scrolls it. */
class MousePointerSensor extends PointerSensor {
  static activators = [
    {
      eventName: "onPointerDown" as const,
      handler: ({ nativeEvent }: ReactPointerEvent) => nativeEvent.isPrimary && nativeEvent.button === 0 && nativeEvent.pointerType !== "touch",
    },
  ];
}

/** With the keyboard, the arrows move a picked-up branch from one stage's group to the next. */
const stageKeys: KeyboardCoordinateGetter = (event, { context }) => {
  const step = event.code === "ArrowDown" ? 1 : event.code === "ArrowUp" ? -1 : 0;
  if (!step) return undefined;
  event.preventDefault();
  const zones = STAGES.map((stage) => ({ stage, rect: context.droppableRects.get(stage) })).filter((z) => z.rect);
  const current = context.over?.id ?? context.active?.data.current?.stage;
  const at = zones.findIndex((z) => z.stage === current);
  const next = zones[Math.min(zones.length - 1, Math.max(0, at + step))];
  return next?.rect ? { x: next.rect.left + 8, y: next.rect.top + 8 } : undefined;
};

/** Where the pointer is; with the keyboard (no pointer), the group the branch overlaps most. */
const collisions: CollisionDetection = (args) => {
  const within = pointerWithin(args);
  return within.length ? within : rectIntersection(args);
};

// ---------------------------------------------------------------- copying

function useCopy() {
  const [copied, setCopied] = useState(false);
  const timer = useRef<number>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const copy = async (value: string) => {
    await navigator.clipboard.writeText(value);
    setCopied(true);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setCopied(false), 1400);
  };
  return { copied, copy };
}

/** A command to copy: its name, the command (cut short when long, whole on hover), and a copy button. */
function Command({ label, command }: { label: string; command: string }) {
  const c = useT(common).common;
  const { copied, copy } = useCopy();
  return (
    <div className="ws-command">
      <span className="ws-command-label">{label}</span>
      <code dir="ltr" className="truncate" title={command}>{command}</code>
      <Button size="sm" variant="ghost" icon={copied ? <Check /> : <Copy />} onClick={() => copy(command)} aria-label={`${c.copy}: ${label}`} title={copied ? c.copied : c.copy} />
    </div>
  );
}

// ---------------------------------------------------------------- the sidebar

function RowBody({ branch, members, version }: { branch: ProjectBranch; members: Instance[]; version: string }) {
  const w = useT(words).workspace;
  const dot = dotOf(members);
  const label = members.length ? statusLabel(members[0]!.status) : w.noInstance;
  return (
    <>
      <span className={`ws-dot ${dot}`} title={label} role="img" aria-label={label} />
      <bdi className="ws-name truncate" dir="ltr">{branch.name}</bdi>
      {branch.lock && <Lock className="ws-lock" aria-label={w.locked} />}
      <span className="ws-version">{version}</span>
    </>
  );
}

function SideRow({
  branch,
  members,
  version,
  to,
  selected,
  movable,
  onPick,
}: {
  branch: ProjectBranch;
  members: Instance[];
  version: string;
  to: string;
  selected: boolean;
  movable: boolean;
  onPick: () => void;
}) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({ id: branch.name, data: { stage: branch.stage }, disabled: !movable });
  // a link stays a link for screen readers; the drag is described by aria-describedby (the instructions)
  const { role: _role, ...dragAttributes } = attributes;
  return (
    <Link
      ref={setNodeRef}
      to={to}
      viewTransition
      draggable={false}
      className="ws-row"
      aria-current={selected ? "page" : undefined}
      data-dragging={isDragging || undefined}
      data-movable={movable || undefined}
      onClick={onPick}
      {...(movable ? { ...dragAttributes, ...listeners } : {})}
    >
      <RowBody branch={branch} members={members} version={version} />
    </Link>
  );
}

function SideGroup({
  stage,
  count: shown,
  dragFrom,
  onNew,
  children,
}: {
  stage: Stage;
  count: number;
  /** the stage of the branch being dragged, if any */
  dragFrom: Stage | null;
  onNew?: () => void;
  children: ReactNode;
}) {
  const w = useT(words).workspace;
  const { setNodeRef, isOver } = useDroppable({ id: stage });
  const target = !!dragFrom && dragFrom !== stage;
  const Icon = STAGE_ICON[stage];
  return (
    <section ref={setNodeRef} className={`ws-group ${stage}`} aria-label={STAGE_LABEL[stage]} data-target={target || undefined} data-over={(target && isOver) || undefined}>
      <div className="ws-group-head">
        <Icon strokeWidth={1.75} />
        <span className="truncate">{STAGE_LABEL[stage]}</span>
        <span className="ws-count">{shown}</span>
        <span className="spacer" />
        {onNew && <Button size="sm" variant="ghost" icon={<Plus />} onClick={onNew} aria-label={w.newBranch(STAGE_LABEL[stage])} title={w.newBranch(STAGE_LABEL[stage])} />}
      </div>
      <div className="ws-list">{children}</div>
    </section>
  );
}

// ---------------------------------------------------------------- the branch's menu

function BranchMenu({
  project,
  branch,
  instance,
  backups,
  onDialog,
}: {
  project: ProjectDetail;
  branch: ProjectBranch;
  instance: Instance | undefined;
  backups: number | null;
  onDialog: (dialog: BranchDialog) => void;
}) {
  const t = useT(words).page;
  const w = useT(words).workspace;
  const l = useT(teamWords);
  const pw = useT(promotionWords);
  const roleCan = useCan();
  const branchCan = useBranchCan();
  const can = (permission: Permission) => branchCan(branch, permission);
  const me = useMe();
  // changing or removing a lock: whoever it allows (they keep projects.access in the branch) and super admins
  const mayUnlock = can("projects.access") || me.role === "super_admin";
  const others = STAGES.filter((s) => s !== branch.stage);
  const view = (tab: string) => branchPath(project.id, branch.name, tab, instance?.name);
  return (
    <Menu trigger={(toggle) => <Button size="sm" variant="ghost" icon={<Ellipsis />} onClick={toggle} aria-label={w.branchActions(branch.name)} title={w.branchActions(branch.name)} />}>
      {(close) => (
        <>
          {instance && can("instances.logs") && (
            <MenuItem asChild><Link to={view("logs")} viewTransition onClick={close}><ScrollText /> {t.logs}</Link></MenuItem>
          )}
          {instance && (
            <MenuItem asChild><Link to={view("backups")} viewTransition onClick={close}>
              <Archive /> {backups ? count(backups, "backup") : w.backups}
            </Link></MenuItem>
          )}
          {branch.on_github !== false && project.branches_list.some((b) => b.name !== branch.name && b.on_github !== false) && (
            <MenuItem onSelect={() => { close(); onDialog({ kind: "compare", branch, base: project.branches_list.find((b) => b.stage === "production" && b.name !== branch.name)?.name ?? project.branches_list.find((b) => b.name !== branch.name)!.name }); }}>
              <GitCompareArrows /> {pw.compare.menu}
            </MenuItem>
          )}
          {branch.stage === "staging" && (
            <MenuItem onSelect={() => { close(); onDialog({ kind: "promotion", branch }); }}>
              <ListChecks /> {pw.check.menu}
            </MenuItem>
          )}
          {instance && branch.stage === "staging" && can("instances.restore") && (
            <MenuItem onSelect={() => { close(); onDialog({ kind: "rebuild", branch, instance: instance.name }); }}>
              <RotateCcw /> {t.rebuild}
            </MenuItem>
          )}
          {can("projects.manage") && (
            <>
              <MenuSeparator /><MenuLabel>{t.moveTo}</MenuLabel>
              {others.map((stage) => {
                const Icon = STAGE_ICON[stage];
                return (
                  <MenuItem key={stage} onSelect={() => { close(); onDialog({ kind: "stage", branch, stage }); }}>
                    <Icon /> {STAGE_LABEL[stage]}
                  </MenuItem>
                );
              })}
            </>
          )}
          {!branch.instances.length && can("instances.create") && branch.on_github !== false && (
            <MenuItem onSelect={() => { close(); onDialog({ kind: "create", branch }); }}>
              <Plus /> {t.createInstance}
            </MenuItem>
          )}
          {roleCan("projects.access") && (branch.lock ? mayUnlock : can("projects.access")) && (
            <>
              <MenuSeparator />
              {branch.lock ? (
                <>
                  <MenuItem onSelect={() => { close(); onDialog({ kind: "lock", branch }); }}><Lock /> {l.lock.changeLock}</MenuItem>
                  <MenuItem onSelect={() => { close(); onDialog({ kind: "unlock", branch }); }}><LockOpen /> {l.lock.unlock}</MenuItem>
                </>
              ) : (
                <MenuItem onSelect={() => { close(); onDialog({ kind: "lock", branch }); }}><Lock /> {l.lock.lock}</MenuItem>
              )}
            </>
          )}
          {instance && can("instances.delete") && (
            <MenuItem danger onSelect={() => { close(); onDialog({ kind: "delete", instance }); }}>
              <Trash2 /> {t.deleteInstance}
            </MenuItem>
          )}
        </>
      )}
    </Menu>
  );
}

// ---------------------------------------------------------------- the branch in the project's header

/** The chosen branch's part of the project's header (pages/Projects.tsx): its name, tags and menu under the project's
 *  details, and its commands under Open on GitHub. */
export type ProjectHeadBranch = { identity: ReactNode; tools: ReactNode };

/** The instance shown in a branch: the one the address names (?instance=), else its first. */
function useChosen(branch: ProjectBranch, instances: Map<string, Instance>) {
  const [search] = useSearchParams();
  const asked = search.get("instance");
  const chosen = asked && branch.instances.includes(asked) ? asked : (branch.instances[0] ?? null);
  return { chosen, instance: chosen ? instances.get(chosen) : undefined };
}

/** The branch's name (to copy), its instance when it has several, its tags and its menu. When another branch is
 *  chosen this row cross-fades (styles/_motion.scss, data-nav="branch"); it keeps one line's height on a computer. */
function BranchIdentity({
  project,
  branch,
  view,
  instances,
  backups,
  onDialog,
}: {
  project: ProjectDetail;
  branch: ProjectBranch;
  view: string;
  instances: Map<string, Instance>;
  backups: Map<string, number> | null;
  onDialog: (dialog: BranchDialog) => void;
}) {
  const t = useT(words).page;
  const w = useT(words).workspace;
  const l = useT(teamWords);
  const c = useT(common).common;
  const can = useBranchCan();
  const navigate = useNavigate();
  const { copied, copy } = useCopy();
  const { chosen, instance } = useChosen(branch, instances);
  return (
    <div className="ph-branch" role="group" aria-label={w.branchArea(branch.name)}>
      <span className="ph-branch-name">
        <GitBranch className="ph-branch-icon" aria-hidden />
        <bdi dir="ltr" className="truncate" title={branch.name}>{branch.name}</bdi>
        <Button size="sm" variant="ghost" icon={copied ? <Check /> : <Copy />} onClick={() => copy(branch.name)} aria-label={w.copyName} title={copied ? c.copied : w.copyName} />
      </span>
      {branch.instances.length > 1 && chosen && (
        <SelectBox dir="ltr" size="sm" aria-label={w.instance} value={chosen}
          onValueChange={(name) => navigate(branchPath(project.id, branch.name, view, name), { viewTransition: true })}>
          {branch.instances.map((name) => <option key={name} value={name}>{name}</option>)}
        </SelectBox>
      )}
      <span className="ph-branch-tags xsmall">
        <StageTag stage={branch.stage} />
        {branch.default && <Tag>{t.defaultBranch}</Tag>}
        {branch.lock && <LockTag lock={branch.lock} branch={branch.name} />}
        <ClientTag access={branch.client_access} />
        {branch.restricted && <span title={l.lock.restrictedHint}><Tag icon={<EyeOff />}>{l.lock.restricted}</Tag></span>}
        {branch.on_github === false && <Tag tone="negative">{t.deletedOnGitHub}</Tag>}
        {branch.auto_created && branch.instances.includes(branch.auto_created) && <span className="faint">{t.autoCreated}</span>}
        {/* in the tags' group, so that it never wraps to a line of its own */}
        <BranchMenu project={project} branch={branch} instance={instance} backups={chosen ? (backups?.get(chosen) ?? null) : null} onDialog={onDialog} />
      </span>
      {!chosen && can(branch, "instances.create") && branch.on_github !== false && (
        <Button size="sm" variant="primary" icon={<Plus />} onClick={() => onDialog({ kind: "create", branch })}>{t.createInstance}</Button>
      )}
    </div>
  );
}

/** The branch's command toolbar (BranchCommands), for the instance shown. */
function BranchTools({
  project,
  branch,
  instances,
  onDialog,
  onJob,
}: {
  project: ProjectDetail;
  branch: ProjectBranch;
  instances: Map<string, Instance>;
  onDialog: (dialog: BranchDialog) => void;
  onJob: (id: string) => void;
}) {
  const can = useBranchCan();
  const { chosen, instance } = useChosen(branch, instances);
  // the instance in full (the page below reads it too): the names its SSH and SQL commands need
  const { data: detail } = useInstance(chosen ?? "", !!chosen);
  return (
    <BranchCommands project={project} branch={branch} instance={detail ?? instance} can={(p) => can(branch, p)}
      shellLink={chosen ? branchPath(project.id, branch.name, "shell", chosen === branch.instances[0] ? null : chosen) : null}
      onDialog={onDialog} onJob={onJob} />
  );
}

// ---------------------------------------------------------------- the branch's pane

function BranchPane({
  project,
  branch,
  view,
  instances,
  onJob,
}: {
  project: ProjectDetail;
  branch: ProjectBranch;
  view: string;
  instances: Map<string, Instance>;
  onJob: (id: string) => void;
}) {
  const w = useT(words).workspace;
  const can = useBranchCan();
  const { chosen, instance } = useChosen(branch, instances);
  const { data: detail } = useInstance(chosen ?? "", !!chosen);
  const hb = useT(buildWords).history;
  // the branch's History comes first (the address without a view); the instance's own overview is "overview"
  const history = (
    <BranchHistory
      project={project}
      branch={branch}
      instances={instances}
      mayOpen={!branch.lock || can(branch, "instances.logs")}
      connectAs={can(branch, "instances.credentials")}
      access={(name) => (can(branch, "instances.credentials") ? branchPath(project.id, branch.name, "access", name === branch.instances[0] ? null : name) : null)}
      onJob={onJob}
    />
  );
  const instanceTab = view === "" || view === "history" ? "history" : view === "overview" ? "" : view;
  // the branch's settings (Odoo.sh's SETTINGS tab): what a new commit does, how a new build is made, its tests
  const bo = useT(optionWords);
  // shown only with an instance: they describe how the running branch builds; its Odoo version is the instance's, and
  // moving it to another one is an upgrade (the instance's Upgrade tab)
  const runningVersion = (detail ?? instance)?.odoo.version;
  const settings = (
    <div className="stack">
      {runningVersion && (
        <p className="small row-wrap" style={{ gap: 6 }}>
          <span className="muted">{w.odooVersion}</span>
          <bdi dir="ltr">{w.instanceVersion(runningVersion)}</bdi>
          {can(branch, "instances.upgrade") && chosen && (
            <span className="faint">
              {w.upgradeHint}{" "}
              <Link to={branchPath(project.id, branch.name, "upgrade", chosen === branch.instances[0] ? null : chosen)} viewTransition>{w.upgradeLink}</Link>
            </span>
          )}
        </p>
      )}
      <BranchSettings project={project} branch={branch} editable={can(branch, "projects.manage")} />
      <BranchResources project={project} branch={branch} />
    </div>
  );
  // without an instance the branch's History shows only when it has entries (a branch that ran before)
  const { data: pastHistory } = useBranchHistory(project.id, branch.name, 1, 0, !chosen);
  return (
    // the branch's name, tags, menu and commands are in the project's header (BranchIdentity, BranchTools)
    <section className="ws-pane" aria-label={branch.name}>
      {chosen ? (
        <InsidePage.Provider value={true}>
          <InstanceView
            key={chosen}
            name={chosen}
            tab={instanceTab}
            embedded
            lead={{ id: "history", label: hb.title, icon: <History />, content: history }}
            trail={{ id: "branch-settings", label: bo.tab, icon: <SlidersHorizontal />, content: settings }}
            tabLink={(tab) => branchPath(project.id, branch.name, tab === "history" ? "" : tab === "" ? "overview" : tab, chosen === branch.instances[0] ? null : chosen)}
            afterDelete={branchPath(project.id, branch.name)}
          />
        </InsidePage.Provider>
      ) : (
        <div className="card">
          <div className="card-body stack">
            <div className="stack-sm">
              <span className="xsmall faint row" style={{ gap: 6 }}><GitCommitHorizontal size={14} /> {w.lastCommit}</span>
              <BranchCommit branch={branch} />
              {branch.commit?.author && <span className="xsmall faint">{w.by(branch.commit.author)}</span>}
            </div>
          </div>
          {/* Create instance is in the head, next to the branch's menu */}
          <Empty icon={<GitBranch />} title={w.noInstanceTitle}>
            {branch.on_github === false ? w.noInstanceDeleted : w.noInstanceText}
            {branch.on_github !== false && <span className="xsmall faint" style={{ display: "block", marginTop: 6 }}>{w.settingsLater}</span>}
          </Empty>
        </div>
      )}
      {!chosen && !!pastHistory?.total && (
        <section className="stack" aria-label={hb.title}>
          <h3 className="section-title row" style={{ gap: 8 }}><History size={16} /> {hb.title}</h3>
          {history}
        </section>
      )}
    </section>
  );
}

// ---------------------------------------------------------------- a new branch

const BRANCH_NAME = /^(?!-)(?!.*\.\.)[A-Za-z0-9._/-]{1,200}$/;

function NewBranchDialog({ project, stage, from: initial, onClose }: { project: ProjectDetail; stage: Stage; from: string; onClose: () => void }) {
  const w = useT(words).workspace;
  const c = useT(common).common;
  const sources = project.branches_list.filter((b) => b.on_github !== false).map((b) => b.name);
  const [from, setFrom] = useState(sources.includes(initial) ? initial : (sources[0] ?? ""));
  const [name, setName] = useState("");
  const typed = name.trim();
  const shown = BRANCH_NAME.test(typed) ? typed : "new-branch";
  return (
    <Modal title={w.newBranchTitle(STAGE_LABEL[stage])} onClose={onClose} footer={<Button onClick={onClose}>{c.close}</Button>}>
      <div className="stack">
        <p className="small muted">{w.newBranchText}</p>
        {stage === "staging" && <p className="small muted">{w.newStagingText}</p>}
        <div className="form-grid">
          <Field label={w.fromBranch}>
            <SelectBox dir="ltr" value={from} onValueChange={setFrom} aria-label={w.fromBranch}>
              {sources.map((b) => <option key={b} value={b}>{b}</option>)}
            </SelectBox>
          </Field>
          <Field label={w.newName} hint={w.newNameHint} error={typed && !BRANCH_NAME.test(typed) ? w.newNameHint : undefined}>
            <Input dir="ltr" autoFocus placeholder="new-branch" value={name} maxLength={200} onChange={(e) => setName(e.target.value)} />
          </Field>
        </div>
        <p className="small">{w.run}</p>
        <Command label="git" command={`git checkout -b ${shown} origin/${from || "<branch>"} && git push -u origin ${shown}`} />
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------- the workspace

export function ProjectWorkspace({
  project,
  meta,
  selected,
  view,
  onRefresh,
  refreshing,
  backups: mayManageBackups,
  head,
}: {
  project: ProjectDetail;
  meta: ProjectMeta;
  /** the branch named in the address, if any */
  selected: string | undefined;
  /** the instance's tab shown in the branch's pane */
  view: string;
  onRefresh: () => void;
  refreshing: boolean;
  backups: boolean;
  /** the project's header and tabs, with the chosen branch's part when there is one */
  head: (branch?: ProjectHeadBranch) => ReactNode;
}) {
  const t = useT(words).page;
  const w = useT(words).workspace;
  const branchCan = useBranchCan();
  const { data: list } = useInstances();
  const instances = useMemo(() => new Map((list ?? []).map((i) => [i.name, i])), [list]);
  // how many backups each instance has, for the branch menu's backups link
  const { data: backupList } = useProjectBackups(project.id, mayManageBackups);
  const backupCounts = useMemo(() => {
    if (!backupList) return null;
    const counts = new Map<string, number>();
    for (const b of backupList) counts.set(b.instance, (counts.get(b.instance) ?? 0) + 1);
    return counts;
  }, [backupList]);
  const [dialog, setDialog] = useState<BranchDialog | null>(null);
  const [newBranch, setNewBranch] = useState<Stage | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const phone = useMedia(PHONE);
  const reduced = useMedia(REDUCED_MOTION);
  const [open, setOpen] = useState(false);
  const [dragging, setDragging] = useState<ProjectBranch | null>(null);
  const [overStage, setOverStage] = useState<Stage | null>(null);

  const branches = project.branches_list;
  const current =
    branches.find((b) => b.name === selected) ??
    branches.find((b) => b.stage === "production") ??
    STAGES.map((s) => branches.find((b) => b.stage === s)).find(Boolean) ??
    null;
  const production = useMemo(() => productionOf(branches, instances), [branches, instances]);
  const membersOf = (branch: ProjectBranch) => branch.instances.map((n) => instances.get(n)).filter((i): i is Instance => !!i);
  const query = filter.trim().toLowerCase();
  const running = project.instances.names.filter((n) => instances.get(n)?.status === "online").length;
  const capacity = meta.capacity;

  // a mouse or pen drags after 6 pixels (a click still opens the branch), a finger after a long press, the keyboard
  // with Space (Enter still follows the link)
  const sensors = useSensors(
    useSensor(MousePointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 250, tolerance: 8 } }),
    useSensor(KeyboardSensor, { coordinateGetter: stageKeys, keyboardCodes: { start: ["Space"], cancel: ["Escape"], end: ["Space"] } }),
  );
  const nameOf = (id: UniqueIdentifier) => String(id);
  const stageOf = (id: UniqueIdentifier | undefined) => (id ? STAGE_LABEL[id as Stage] : "");
  const announcements: Announcements = {
    onDragStart: ({ active }) => w.picked(nameOf(active.id)),
    onDragOver: ({ active, over }) => (over ? w.over(nameOf(active.id), stageOf(over.id)) : w.notOver(nameOf(active.id))),
    onDragEnd: ({ active, over }) =>
      over && over.id !== active.data.current?.stage ? w.dropped(nameOf(active.id), stageOf(over.id)) : w.stays(nameOf(active.id)),
    onDragCancel: ({ active }) => w.cancelled(nameOf(active.id)),
  };
  // dropped on another stage: the dialog takes over and the branch stays in its group until the change is saved;
  // anywhere else it glides back to where it was
  const movesStage = !!dragging && !!overStage && overStage !== dragging.stage;
  const dropAnimation: DropAnimation | null = reduced || movesStage ? null : { duration: 200, easing: "cubic-bezier(0.23, 1, 0.32, 1)" };
  const endDrag = () => {
    setDragging(null);
    setOverStage(null);
    // the click that ends a drag with the mouse must not open the branch it started on
    const block = (event: MouseEvent) => {
      event.preventDefault();
      event.stopPropagation();
    };
    window.addEventListener("click", block, { capture: true, once: true });
    window.setTimeout(() => window.removeEventListener("click", block, { capture: true }), 0);
  };

  if (branches.length === 0)
    return (
      <>
        {head()}
      <div className="stack">
        {meta.branches_error && <Callout tone="warning">{t.cannotRead} {meta.branches_error}</Callout>}
        <div className="card">
          <Empty icon={<GitBranch />} title={t.empty}
            action={<Button size="sm" variant="ghost" icon={<RefreshCw />} loading={refreshing} onClick={onRefresh}>{t.refresh}</Button>}>
            {t.emptyText}
          </Empty>
          <div className="card-body stack-sm">
            <p className="small muted">{t.pushHelp}</p>
            <pre className="terminal" dir="ltr">{`git init -b main\ngit add .\ngit commit -m "First modules"\ngit remote add origin ${project.repository.url}.git\ngit push -u origin main`}</pre>
          </div>
        </div>
      </div>
      </>
    );

  const listOpen = !phone || open;
  return (
    <>
    {head(current ? {
      // keyed by the branch: its commands' own state (a fork's name, the branch to merge) starts again
      identity: <BranchIdentity key={current.name} project={project} branch={current} view={view} instances={instances}
        backups={backupCounts} onDialog={setDialog} />,
      tools: <BranchTools key={current.name} project={project} branch={current} instances={instances} onDialog={setDialog} onJob={setJobId} />,
    } : undefined)}
    <div className="stack">
      {capacity?.warning && capacity.message && (
        <Callout tone="warning">
          {t.capacity(capacity.message)} {running > 1 && t.parallel(running)}{" "}
          <Link to="/help/docs/ADMIN_GUIDE.md#how-many-instances-can-my-server-run" viewTransition>{t.howMany}</Link>
        </Callout>
      )}
      {meta.branches_error && <Callout tone="warning">{t.cannotRead} {meta.branches_error}</Callout>}
      <div className="workspace">
        <aside className="ws-side" aria-label={w.branches} data-open={listOpen || undefined}>
          {phone && (
            <button type="button" className="ws-toggle" aria-expanded={open} aria-controls="ws-branches" onClick={() => setOpen(!open)}>
              <GitBranch />
              <span className="ws-toggle-text">
                <span className="xsmall faint">{w.showBranches}</span>
                {current && <bdi dir="ltr" className="truncate mono">{current.name}</bdi>}
              </span>
              <ChevronDown className="ws-chevron" />
            </button>
          )}
          {listOpen && (
            <div id="ws-branches" className="ws-side-body">
              <div className="ws-side-tools">
                <div className="input-group">
                  <Search />
                  <Input data-branch-filter placeholder={w.filter} aria-label={w.filter} aria-keyshortcuts="/" value={filter} onChange={(e) => setFilter(e.target.value)} />
                </div>
                <Button variant="ghost" icon={<RefreshCw />} loading={refreshing} onClick={onRefresh} aria-label={w.readAgain} title={w.readAgain} />
              </div>
              <DndContext
                sensors={sensors}
                collisionDetection={collisions}
                accessibility={{ announcements, screenReaderInstructions: { draggable: w.instructions } }}
                onDragStart={({ active }) => setDragging(branches.find((b) => b.name === active.id) ?? null)}
                onDragOver={({ over }) => setOverStage((over?.id as Stage | undefined) ?? null)}
                onDragCancel={endDrag}
                onDragEnd={({ active, over }) => {
                  const branch = branches.find((b) => b.name === active.id);
                  const stage = over?.id as Stage | undefined;
                  endDrag();
                  if (branch && stage && stage !== branch.stage) setDialog({ kind: "stage", branch, stage });
                }}
              >
                {STAGES.map((stage) => {
                  const inStage = branches.filter((b) => b.stage === stage);
                  const listed = inStage.filter((b) => !query || b.name.toLowerCase().includes(query));
                  return (
                    <SideGroup key={stage} stage={stage} count={inStage.length} dragFrom={dragging?.stage ?? null}
                      onNew={stage === "production" ? undefined : () => setNewBranch(stage)}>
                      {listed.length ? (
                        listed.map((branch) => (
                          <SideRow
                            key={branch.name}
                            branch={branch}
                            members={membersOf(branch)}
                            version={membersOf(branch)[0]?.odoo.version ?? branch.odoo_version ?? project.odoo.version}
                            to={branchPath(project.id, branch.name)}
                            selected={branch.name === current?.name}
                            movable={branchCan(branch, "projects.manage")}
                            onPick={() => setOpen(false)}
                          />
                        ))
                      ) : (
                        // an empty group says where a dragged branch can go; the outline says it for the others
                        <p className={dragging && dragging.stage !== stage ? "ws-empty ws-drop-hint" : "ws-empty"}>
                          {dragging && dragging.stage !== stage ? w.dropHere(STAGE_LABEL[stage]) : query && inStage.length ? w.noMatch : w.noBranch}
                        </p>
                      )}
                    </SideGroup>
                  );
                })}
                <DragOverlay dropAnimation={dropAnimation}>
                  {dragging ? (
                    <div className="ws-row ws-overlay">
                      <RowBody branch={dragging} members={membersOf(dragging)} version={membersOf(dragging)[0]?.odoo.version ?? dragging.odoo_version ?? project.odoo.version} />
                    </div>
                  ) : null}
                </DragOverlay>
              </DndContext>
              {branches.some((b) => branchCan(b, "projects.manage")) && <p className="ws-hint">{w.dragHint}</p>}
              {meta.branches_read_at && <p className="ws-hint">{t.readAt(ago(meta.branches_read_at))}</p>}
            </div>
          )}
        </aside>
        {current && (
          <BranchPane key={current.name} project={project} branch={current} view={view} instances={instances} onJob={setJobId} />
        )}
      </div>
      {dialog?.kind === "lock" && <LockDialog project={project} branch={dialog.branch} onClose={() => setDialog(null)} />}
      {dialog?.kind === "unlock" && <UnlockDialog project={project} branch={dialog.branch} onClose={() => setDialog(null)} />}
      {dialog?.kind === "stage" && <StageDialog project={project} meta={meta} branch={dialog.branch} stage={dialog.stage} onClose={() => setDialog(null)}
        onCompare={(base) => setDialog({ kind: "compare", branch: dialog.branch, base })} />}
      {dialog?.kind === "compare" && <CompareDialog project={project} base={dialog.base} head={dialog.branch.name} onClose={() => setDialog(null)} />}
      {dialog?.kind === "promotion" && <PromotionDialog project={project} branch={dialog.branch} onClose={() => setDialog(null)}
        onCompare={(base) => setDialog({ kind: "compare", branch: dialog.branch, base })} />}
      {dialog?.kind === "create" && <CreateDialog project={project} branch={dialog.branch} production={production} onClose={() => setDialog(null)} onJob={setJobId} />}
      {dialog?.kind === "rebuild" && <RebuildDialog project={project} branch={dialog.branch} instance={dialog.instance} production={production} onClose={() => setDialog(null)} onJob={setJobId} />}
      {dialog?.kind === "delete" && <DeleteInstanceDialog instance={dialog.instance} onClose={() => setDialog(null)} onJob={setJobId} />}
      {newBranch && <NewBranchDialog project={project} stage={newBranch} from={current?.name ?? ""} onClose={() => setNewBranch(null)} />}
      {jobId && <JobDrawer id={jobId} onClose={() => setJobId(null)} />}
    </div>
    </>
  );
}
