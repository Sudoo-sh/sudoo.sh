// A project's Access tab (who sees it and at which level, per project and per branch) and the locks of its branches
// (src/sudoo/projects/access.py). The server decides everything again; these controls only make it easy to say.

import { GitBranch, Lock, LockOpen, Plus, ShieldCheck, Trash2, UsersRound } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router";
import { api, errorText } from "@/api/client";
import { keys, useCall, useProjectAccess, useTeams, useUsers } from "@/api/queries";
import type { AccessLevel, AccessMode, AccessSubject, BranchLock, Grant, Permission, ProjectAccess, ProjectBranch, ProjectDetail } from "@/api/types";
import { Button, Callout, Field, Segmented, Skeleton, Switch } from "@/components/ui/basics";
import { Modal, useToast } from "@/components/ui/overlay";
import { useT } from "@/i18n";
import common from "@/i18n/common";
import words from "@/i18n/teams";
import { allowedNames } from "./LockTag";
import { useCan } from "./Shell";

const LEVELS: AccessLevel[] = ["none", "view", "developer", "maintainer", "admin"];

function SubjectPicker({
  taken,
  onAdd,
  withLevel,
}: {
  taken: AccessSubject[];
  onAdd: (subject: AccessSubject & { level?: AccessLevel }) => void;
  withLevel: boolean;
}) {
  const t = useT(words);
  const can = useCan();
  const { data: teams } = useTeams();
  const { data: users } = useUsers();
  const [kind, setKind] = useState<"team" | "user">("team");
  const [id, setId] = useState("");
  const [level, setLevel] = useState<AccessLevel>("developer");
  const isTaken = (type: string, value: string) => taken.some((e) => e.type === type && e.id === value);
  const options =
    kind === "team"
      ? (teams ?? []).filter((team) => !isTaken("team", team.id)).map((team) => ({ id: team.id, label: team.name }))
      : can("accounts.manage")
        ? (users ?? []).filter((u) => !isTaken("user", u.name)).map((u) => ({ id: u.name, label: `${u.display_name} (${u.name})` }))
        : [];
  return (
    <div className="grant-add">
      <Segmented
        value={kind}
        onChange={(value) => { setKind(value); setId(""); }}
        options={[
          { value: "team", label: t.access.addTeam, icon: <UsersRound /> },
          { value: "user", label: t.access.addPerson },
        ]}
      />
      <select className="select" value={id} onChange={(e) => setId(e.target.value)} aria-label={kind === "team" ? t.access.team : t.access.person}>
        <option value="">{t.access.choose}</option>
        {options.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
      </select>
      {withLevel && (
        <select className="select" value={level} onChange={(e) => setLevel(e.target.value as AccessLevel)} aria-label={t.access.level}>
          {LEVELS.map((l) => <option key={l} value={l}>{t.levels[l]}</option>)}
        </select>
      )}
      <Button size="sm" icon={<Plus />} disabled={!id}
        onClick={() => { onAdd({ type: kind, id, name: options.find((o) => o.id === id)?.label ?? id, ...(withLevel ? { level } : {}) }); setId(""); }}>
        {t.access.add}
      </Button>
    </div>
  );
}

function GrantList({ grants, onChange, empty }: { grants: Grant[]; onChange: (grants: Grant[]) => void; empty: string }) {
  const t = useT(words);
  return (
    <div className="stack-sm">
      {grants.length === 0 ? (
        <p className="small faint">{empty}</p>
      ) : (
        <div className="grant-list">
          {grants.map((grant) => (
            <div key={`${grant.type}:${grant.id}`} className="grant-row">
              <span className="row spacer">
                {grant.type === "team" ? <UsersRound aria-label={t.access.team} /> : <ShieldCheck aria-label={t.access.person} />}
                <bdi className="truncate">{grant.name ?? <span className="faint">{t.access.unknown}</span>}</bdi>
                {grant.type === "user" && <span className="xsmall faint"><bdi dir="ltr">{grant.id}</bdi></span>}
              </span>
              <select className="select" value={grant.level} title={t.levelHints[grant.level]} aria-label={t.access.level}
                onChange={(e) => onChange(grants.map((g) => (g === grant ? { ...g, level: e.target.value as AccessLevel } : g)))}>
                {LEVELS.map((l) => <option key={l} value={l}>{t.levels[l]}</option>)}
              </select>
              <Button size="sm" variant="ghost" icon={<Trash2 />} aria-label={t.access.removeLabel(grant.name ?? grant.id)}
                onClick={() => onChange(grants.filter((g) => g !== grant))} />
            </div>
          ))}
        </div>
      )}
      <SubjectPicker taken={grants} withLevel onAdd={(s) => onChange([...grants, { type: s.type, id: s.id, name: s.name, level: s.level ?? "developer" }])} />
    </div>
  );
}

type Rules = ProjectAccess["branches"];

export function AccessTab({ project, permitted }: { project: ProjectDetail; permitted: boolean }) {
  const t = useT(words);
  const roleCan = useCan();
  const can = (permission: Permission) => (permission === "projects.access" ? permitted : roleCan(permission));
  const toast = useToast();
  const { data, isPending, error } = useProjectAccess(project.id, permitted);
  const [mode, setMode] = useState<AccessMode>("panel_roles");
  const [grants, setGrants] = useState<Grant[]>([]);
  const [rules, setRules] = useState<Rules>({});
  const [newBranch, setNewBranch] = useState("");
  const reset = () => {
    if (!data) return;
    setMode(data.mode);
    setGrants(data.grants);
    setRules(data.branches);
  };
  useEffect(reset, [data]);
  const dirty = useMemo(
    () => !!data && JSON.stringify({ mode, grants, branches: rules }) !== JSON.stringify({ mode: data.mode, grants: data.grants, branches: data.branches }),
    [data, mode, grants, rules],
  );
  const save = useCall(
    () =>
      api.put<ProjectAccess>(`/projects/${project.id}/access`, {
        mode,
        grants: grants.map(({ type, id, level }) => ({ type, id, level })),
        branches: Object.fromEntries(
          Object.entries(rules).map(([branch, rule]) => [branch, { restricted: rule.restricted, grants: rule.grants.map(({ type, id, level }) => ({ type, id, level })) }]),
        ),
      }),
    [keys.projectAccess(project.id), keys.project(project.id), keys.projects, keys.teams, keys.instances],
  );
  if (!can("projects.access"))
    return (
      <div className="stack tab-panel">
        <Callout tone="info">{t.access.cannotManage}</Callout>
      </div>
    );
  if (error) return <div className="stack tab-panel"><Callout tone="warning">{errorText(error)}</Callout></div>;
  if (isPending || !data) return <div className="stack tab-panel"><Skeleton height={220} /></div>;
  const branchNames = project.branches_list.map((b) => b.name).filter((b) => !(b in rules));
  const locks = Object.entries(data.locks);
  return (
    <div className="stack tab-panel">
      <section className="card">
        <div className="card-head">
          <h2 className="spacer">{t.access.title}</h2>
          {can("teams.manage") && <Link className="btn sm ghost" to="/teams" viewTransition><UsersRound /> {t.access.manageTeams}</Link>}
        </div>
        <div className="card-body stack">
          <p className="small muted">{t.access.text}</p>
          <Segmented
            value={mode}
            onChange={setMode}
            options={[
              { value: "panel_roles", label: t.access.open },
              { value: "restricted", label: t.access.restricted },
            ]}
          />
          <p className="small muted">{mode === "restricted" ? t.access.restrictedHint : t.access.openHint}</p>
          <h3 className="small">{t.access.grants}</h3>
          <p className="xsmall faint">{t.access.grantsText}</p>
          <GrantList grants={grants} onChange={setGrants} empty={mode === "restricted" ? t.access.noGrantsRestricted : t.access.noGrants} />
        </div>
      </section>
      <section className="card">
        <div className="card-head"><h2>{t.access.branchesTitle}</h2></div>
        <div className="card-body stack">
          <p className="small muted">{t.access.branchesText}</p>
          {Object.keys(rules).length === 0 && <p className="small faint">{t.access.noBranchRules}</p>}
          {Object.entries(rules).map(([branch, rule]) => (
            <div key={branch} className="branch-rule stack-sm">
              <div className="row-wrap">
                <GitBranch />
                <bdi dir="ltr" className="mono small">{branch}</bdi>
                <span className="spacer" />
                <Switch checked={rule.restricted} label={t.access.restrictBranch}
                  onChange={(value) => setRules({ ...rules, [branch]: { ...rule, restricted: value } })} />
                <Button size="sm" variant="ghost" icon={<Trash2 />} aria-label={t.access.removeBranch(branch)}
                  onClick={() => setRules(Object.fromEntries(Object.entries(rules).filter(([b]) => b !== branch)))} />
              </div>
              {rule.restricted && <p className="xsmall faint">{t.access.restrictBranchHint}</p>}
              <GrantList grants={rule.grants} empty={t.access.noGrants} onChange={(value) => setRules({ ...rules, [branch]: { ...rule, grants: value } })} />
            </div>
          ))}
          {branchNames.length > 0 && (
            <div className="row-wrap">
              <select className="select" dir="ltr" value={newBranch} onChange={(e) => setNewBranch(e.target.value)} aria-label={t.access.branch}>
                <option value="">{t.access.choose}</option>
                {branchNames.map((b) => <option key={b} value={b}>{b}</option>)}
              </select>
              <Button size="sm" icon={<Plus />} disabled={!newBranch}
                onClick={() => { setRules({ ...rules, [newBranch]: { restricted: false, grants: [] } }); setNewBranch(""); }}>
                {t.access.addBranch}
              </Button>
            </div>
          )}
        </div>
      </section>
      <section className="card">
        <div className="card-head"><h2>{t.access.locksTitle}</h2></div>
        <div className="card-body stack-sm">
          {locks.length === 0 ? <p className="small faint">{t.access.noLocks}</p> : locks.map(([branch, lock]) => (
            <div key={branch} className="row-wrap small">
              <Lock />
              <bdi dir="ltr" className="mono">{branch}</bdi>
              <span className="muted">{t.lock.lockedBy(lock.locked_by ?? "")}{lock.reason ? ` · ${lock.reason}` : ""}</span>
              <span className="faint">{t.lock.allows(allowedNames(lock))}</span>
            </div>
          ))}
          <p className="xsmall faint">{t.access.locksText}</p>
        </div>
      </section>
      {dirty && (
      <div className="save-bar">
        <span className="small spacer">{t.access.unsaved}</span>
        <Button onClick={reset} disabled={!dirty}>{t.access.discard}</Button>
        <Button variant="primary" loading={save.isPending} disabled={!dirty}
          onClick={() => save.mutate(undefined, {
            onSuccess: () => toast("success", t.access.saved),
            onError: (e) => toast("error", t.access.notSaved, errorText(e)),
          })}>{t.access.save}</Button>
      </div>
      )}
    </div>
  );
}

export function LockDialog({ project, branch, onClose }: { project: ProjectDetail; branch: ProjectBranch; onClose: () => void }) {
  const t = useT(words).lock;
  const c = useT(common).common;
  const toast = useToast();
  const [reason, setReason] = useState(branch.lock?.reason ?? "");
  const [allow, setAllow] = useState<AccessSubject[]>(branch.lock?.allow ?? []);
  const save = useCall(
    () => api.put<BranchLock>(`/projects/${project.id}/branches/${branch.name}/lock`, { reason: reason.trim(), allow: allow.map(({ type, id }) => ({ type, id })) }),
    [keys.project(project.id), keys.projects, keys.instances, keys.projectAccess(project.id)],
  );
  return (
    <Modal title={branch.lock ? t.editTitle(branch.name) : t.title(branch.name)} onClose={onClose}
      footer={<><Button onClick={onClose}>{c.cancel}</Button>
        <Button variant="primary" icon={<Lock />} loading={save.isPending}
          onClick={() => save.mutate(undefined, {
            onSuccess: () => { toast("success", branch.lock ? t.changedToast(branch.name) : t.lockedToast(branch.name)); onClose(); },
            onError: (e) => toast("error", t.notLocked, errorText(e)),
          })}>{branch.lock ? t.saveLock : t.lockNow}</Button></>}>
      <div className="stack">
        <p className="small muted">{t.text}</p>
        <Field label={t.reason} hint={t.reasonHint}>
          <input className="input" dir="auto" maxLength={200} value={reason} onChange={(e) => setReason(e.target.value)} />
        </Field>
        <Field label={t.allow} hint={t.allowHint}>
          <div className="stack-sm">
            {allow.length > 0 && (
              <div className="row-wrap">
                {allow.map((entry) => (
                  <button key={`${entry.type}:${entry.id}`} type="button" className="chip" onClick={() => setAllow(allow.filter((e) => e !== entry))}>
                    {entry.type === "team" ? <UsersRound /> : <ShieldCheck />} <bdi>{entry.name ?? entry.id}</bdi> <Trash2 />
                  </button>
                ))}
              </div>
            )}
            <SubjectPicker taken={allow} withLevel={false} onAdd={(s) => setAllow([...allow, { type: s.type, id: s.id, name: s.name }])} />
          </div>
        </Field>
      </div>
    </Modal>
  );
}

export function UnlockDialog({ project, branch, onClose }: { project: ProjectDetail; branch: ProjectBranch; onClose: () => void }) {
  const t = useT(words).lock;
  const c = useT(common).common;
  const toast = useToast();
  const unlock = useCall(() => api.del(`/projects/${project.id}/branches/${branch.name}/lock`), [keys.project(project.id), keys.projects, keys.instances, keys.projectAccess(project.id)]);
  return (
    <Modal title={t.unlockTitle(branch.name)} onClose={onClose}
      footer={<><Button onClick={onClose}>{c.cancel}</Button>
        <Button variant="primary" icon={<LockOpen />} loading={unlock.isPending}
          onClick={() => unlock.mutate(undefined, {
            onSuccess: () => { toast("success", t.unlocked(branch.name)); onClose(); },
            onError: (e) => toast("error", t.notUnlocked, errorText(e)),
          })}>{t.unlock}</Button></>}>
      <div className="stack">
        {branch.lock && <Callout tone="warning">{t.lockedTitle(branch.name, branch.lock.locked_by ?? "")}{branch.lock.reason ? ` · ${branch.lock.reason}` : ""}</Callout>}
        <p className="small muted">{t.unlockText}</p>
      </div>
    </Modal>
  );
}

/** The banner of an instance whose branch is locked. */
export function LockBanner({ lock, allowed }: { lock: BranchLock; allowed: boolean }) {
  const t = useT(words).lock;
  return (
    <Callout tone="warning">
      <b>{t.banner(lock.locked_by ?? "", lock.reason)}</b>{" "}
      {allowed ? t.bannerAllowed : t.allows(allowedNames(lock))}
    </Callout>
  );
}
