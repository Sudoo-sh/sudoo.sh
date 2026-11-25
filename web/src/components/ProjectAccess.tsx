// A project's Access tab: who sees it (edited with the premium teams package; without it, shown read only with
// "Remove all restrictions", which is always possible) and the locks of its branches (every plan)
// (src/sudoo/projects/access.py). The server decides everything again; these controls only make it easy to say.

import { GitBranch, Lock, LockOpen, Plus, ShieldCheck, Trash2, UsersRound } from "lucide-react";
import { useState } from "react";
import { api, errorText } from "@/api/client";
import { keys, useCall, useProjectAccess, useTeams, useUsers } from "@/api/queries";
import type {
  AccessLevel,
  AccessSubject,
  BranchLock,
  Grant,
  ProjectAccess,
  ProjectBranch,
  ProjectDetail,
} from "@/api/types";
import { Button, Callout, Field, Segmented, Skeleton, Tag } from "@/components/app/basics";
import { GitHubMark } from "@/components/app/GitHubMark";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ago } from "@/lib/format";
import { Input } from "@/components/ui/input";
import { SelectBox } from "@/components/app/select";
import { Modal, useToast } from "@/components/app/overlay";
import { useT } from "@/i18n";
import common from "@/i18n/common";
import words from "@/i18n/teams";
import { allowedNames } from "./LockTag";
import { PlanCard, PremiumSlot } from "@/premium/components";
import { useCan } from "./Shell";
import { ModelCard, PeopleCard, RequestsCard, RolesMatrix } from "./ProjectRoles";

export const LEVELS: AccessLevel[] = ["none", "view", "tester", "developer", "maintainer", "admin"];

/** Choose a team or a person (and a level): for locks here, and for grants in the teams package. */
export function SubjectPicker({
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
  const [kind, setKind] = useState<"team" | "user" | "github_team">("team");
  const [id, setId] = useState("");
  const [level, setLevel] = useState<AccessLevel>("developer");
  const isTaken = (type: string, value: string) => taken.some((e) => e.type === type && e.id === value);
  const options =
    kind === "github_team"
      ? /^[A-Za-z0-9][A-Za-z0-9-]{0,38}\/[A-Za-z0-9._-]{1,100}$/.test(id) && !isTaken("github_team", id)
        ? [{ id, label: `${t.githubTeam} ${id}` }]
        : []
      : kind === "team"
        ? (teams ?? []).filter((team) => !isTaken("team", team.id)).map((team) => ({ id: team.id, label: team.name }))
        : can("accounts.manage")
          ? (users ?? [])
              .filter((u) => !isTaken("user", u.name))
              .map((u) => ({ id: u.name, label: `${u.display_name} (${u.name})` }))
          : [];
  return (
    <div className="grant-add">
      <Segmented
        value={kind}
        onChange={(value) => {
          setKind(value);
          setId("");
        }}
        options={[
          { value: "team", label: t.access.addTeam, icon: <UsersRound /> },
          { value: "user", label: t.access.addPerson },
          ...(withLevel ? [{ value: "github_team" as const, label: t.githubTeam, icon: <GitHubMark /> }] : []),
        ]}
      />
      {kind === "github_team" ? (
        <Input
          dir="ltr"
          spellCheck={false}
          placeholder={t.githubTeamHint}
          aria-label={t.githubTeam}
          value={id}
          onChange={(e) => setId(e.target.value.trim())}
        />
      ) : (
        <SelectBox
          value={id}
          onValueChange={(value) => setId(value)}
          aria-label={kind === "team" ? t.access.team : t.access.person}
        >
          <option value="">{t.access.choose}</option>
          {options.map((o) => (
            <option key={o.id} value={o.id}>
              {o.label}
            </option>
          ))}
        </SelectBox>
      )}
      {withLevel && (
        <SelectBox value={level} onValueChange={(value) => setLevel(value as AccessLevel)} aria-label={t.access.level}>
          {LEVELS.map((l) => (
            <option key={l} value={l}>
              {t.levels[l]}
            </option>
          ))}
        </SelectBox>
      )}
      <Button
        size="sm"
        icon={<Plus />}
        disabled={!id || !options.some((o) => o.id === id)}
        onClick={() => {
          onAdd({
            type: kind,
            id,
            name: options.find((o) => o.id === id)?.label ?? id,
            ...(withLevel ? { level } : {}),
          });
          setId("");
        }}
      >
        {t.access.add}
      </Button>
    </div>
  );
}

type Rules = ProjectAccess["branches"];

/** Who has what, read only: with the teams package the editor (slot "project.access") takes its place. */
function AccessSummary({ project, data }: { project: ProjectDetail; data: ProjectAccess }) {
  const all = useT(words);
  const t = all.access;
  const toast = useToast();
  const rules: [string, Rules[string]][] = Object.entries(data.branches);
  const restricted = data.mode === "restricted" || data.grants.length > 0 || rules.length > 0;
  const clear = useCall(
    () => api.put<ProjectAccess>(`/projects/${project.id}/access`, { mode: "panel_roles", grants: [], branches: {} }),
    [keys.projectAccess(project.id), keys.project(project.id), keys.projects, keys.instances],
  );
  const grantNames = (grants: Grant[]) => grants.map((g) => `${g.name ?? g.id} (${all.levels[g.level]})`).join(", ");
  return (
    <section className="card">
      <div className="card-head">
        <h2 className="spacer">{t.title}</h2>
      </div>
      <div className="card-body stack">
        <p className="small">
          <b>{data.mode === "restricted" ? t.restricted : t.open}.</b>{" "}
          {data.mode === "restricted" ? t.restrictedHint : t.openHint}
        </p>
        {data.grants.length > 0 && (
          <p className="small">
            <span className="muted">{t.grants}:</span> <bdi>{grantNames(data.grants)}</bdi>
          </p>
        )}
        {rules.map(([branch, rule]) => (
          <p key={branch} className="small row-wrap">
            <GitBranch />{" "}
            <bdi dir="ltr" className="mono">
              {branch}
            </bdi>
            {rule.restricted && <span className="faint">{t.restrictBranch}</span>}
            {rule.grants.length > 0 && <bdi className="muted">{grantNames(rule.grants)}</bdi>}
          </p>
        ))}
        {restricted && (
          <div className="row-wrap">
            <p className="xsmall faint spacer">{t.removeAllText}</p>
            <Button
              size="sm"
              icon={<Trash2 />}
              loading={clear.isPending}
              onClick={() =>
                clear.mutate(undefined, {
                  onSuccess: () => toast("success", t.removedAll),
                  onError: (e) => toast("error", t.notSaved, errorText(e)),
                })
              }
            >
              {t.removeAll}
            </Button>
          </div>
        )}
        <PlanCard id="teams" compact />
      </div>
    </section>
  );
}

/** Access from GitHub (auth/github_access.py): each linked person's permission on the repository and its level. */
function GitHubAccessCard({
  project,
  data,
  permitted,
}: {
  project: ProjectDetail;
  data: ProjectAccess;
  permitted: boolean;
}) {
  const all = useT(words);
  const t = all.fromGitHub;
  const github = data.github;
  const toast = useToast();
  const change = useCall(
    (role: string) => api.put<ProjectAccess>(`/projects/${project.id}/access/github-read-role`, { role }),
    [keys.projectAccess(project.id), keys.project(project.id), keys.projects, keys.instances],
  );
  if (!github) return null;
  return (
    <section className="card">
      <div className="card-head">
        <GitHubMark size={18} />
        <h2 className="spacer">{t.title}</h2>
      </div>
      <div className="card-body stack-sm">
        <p className="small muted">{github.enabled ? t.on(github.repository) : t.off}</p>
        <Field label={t.readGives}>
          <SelectBox
            value={data.github_read_role}
            disabled={!permitted || change.isPending}
            onValueChange={(role) =>
              change.mutate(role, {
                onSuccess: () => toast("success", t.readChanged),
                onError: (e) => toast("error", t.notSaved, errorText(e)),
              })
            }
            aria-label={t.readGives}
          >
            <option value="viewer">{all.levels.view}</option>
            <option value="tester">{all.levels.tester}</option>
          </SelectBox>
        </Field>
        <p className="xsmall faint">{t.readHint}</p>
        {github.people.length === 0 ? (
          <p className="small faint">{t.nobody}</p>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t.person}</TableHead>
                  <TableHead>{t.permission}</TableHead>
                  <TableHead>{t.level}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {github.people.map((p) => (
                  <TableRow key={p.user}>
                    <TableCell>
                      <bdi>{p.name}</bdi>{" "}
                      <span className="xsmall faint">
                        <bdi dir="ltr">@{p.github_login}</bdi>
                      </span>
                    </TableCell>
                    <TableCell className="small mono">
                      {p.covered === false ? (
                        <span className="faint">{t.notCovered}</span>
                      ) : (
                        (p.permission ?? <span className="faint">{t.notChecked}</span>)
                      )}
                      {p.via && (
                        <div className="xsmall faint">
                          {all.githubTeam} {p.via}
                        </div>
                      )}
                    </TableCell>
                    <TableCell className="small">
                      {!p.applies ? (
                        <span className="faint">{github.enabled ? t.administrators : "-"}</span>
                      ) : p.level ? (
                        <Tag>{all.levels[p.level]}</Tag>
                      ) : (
                        <span className="faint">{t.notChecked}</span>
                      )}
                      {p.checked && <div className="xsmall faint">{t.checked(ago(p.checked))}</div>}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </div>
    </section>
  );
}

export function AccessTab({ project, permitted }: { project: ProjectDetail; permitted: boolean }) {
  const t = useT(words);
  const { data, isPending, error } = useProjectAccess(project.id, permitted);
  if (!permitted)
    return (
      <div className="stack tab-panel">
        <Callout tone="info">{t.access.cannotManage}</Callout>
        <RequestsCard project={project} maxHours={8} enabled />
      </div>
    );
  if (error)
    return (
      <div className="stack tab-panel">
        <Callout tone="warning">{errorText(error)}</Callout>
      </div>
    );
  if (isPending || !data)
    return (
      <div className="stack tab-panel">
        <Skeleton height={220} />
      </div>
    );
  const locks = Object.entries(data.locks);
  return (
    <div className="stack tab-panel">
      {data.model === "legacy" && <ModelCard project={project} />}
      <PremiumSlot
        name="project.access"
        props={{ project, permitted, access: data }}
        fallback={<AccessSummary project={project} data={data} />}
      />
      {data.model === "project_roles" && <RolesMatrix project={project} data={data} editable={permitted} />}
      <PeopleCard project={project} />
      <GitHubAccessCard project={project} data={data} permitted={permitted} />
      {data.model === "project_roles" && (
        <RequestsCard project={project} maxHours={data.request_max_hours} enabled={data.requests_enabled} />
      )}
      <section className="card">
        <div className="card-head">
          <h2>{t.access.locksTitle}</h2>
        </div>
        <div className="card-body stack-sm">
          {locks.length === 0 ? (
            <p className="small faint">{t.access.noLocks}</p>
          ) : (
            locks.map(([branch, lock]) => (
              <div key={branch} className="row-wrap small">
                <Lock />
                <bdi dir="ltr" className="mono">
                  {branch}
                </bdi>
                <span className="muted">
                  {t.lock.lockedBy(lock.locked_by ?? "")}
                  {lock.reason ? ` · ${lock.reason}` : ""}
                </span>
                <span className="faint">{t.lock.allows(allowedNames(lock))}</span>
              </div>
            ))
          )}
          <p className="xsmall faint">{t.access.locksText}</p>
        </div>
      </section>
    </div>
  );
}

export function LockDialog({
  project,
  branch,
  onClose,
}: {
  project: ProjectDetail;
  branch: ProjectBranch;
  onClose: () => void;
}) {
  const t = useT(words).lock;
  const c = useT(common).common;
  const toast = useToast();
  const [reason, setReason] = useState(branch.lock?.reason ?? "");
  const [allow, setAllow] = useState<AccessSubject[]>(branch.lock?.allow ?? []);
  const save = useCall(
    () =>
      api.put<BranchLock>(`/projects/${project.id}/branches/${branch.name}/lock`, {
        reason: reason.trim(),
        allow: allow.map(({ type, id }) => ({ type, id })),
      }),
    [keys.project(project.id), keys.projects, keys.instances, keys.projectAccess(project.id)],
  );
  return (
    <Modal
      title={branch.lock ? t.editTitle(branch.name) : t.title(branch.name)}
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>{c.cancel}</Button>
          <Button
            variant="primary"
            icon={<Lock />}
            loading={save.isPending}
            onClick={() =>
              save.mutate(undefined, {
                onSuccess: () => {
                  toast("success", branch.lock ? t.changedToast(branch.name) : t.lockedToast(branch.name));
                  onClose();
                },
                onError: (e) => toast("error", t.notLocked, errorText(e)),
              })
            }
          >
            {branch.lock ? t.saveLock : t.lockNow}
          </Button>
        </>
      }
    >
      <div className="stack">
        <p className="small muted">{t.text}</p>
        <Field label={t.reason} hint={t.reasonHint}>
          <Input dir="auto" maxLength={200} value={reason} onChange={(e) => setReason(e.target.value)} />
        </Field>
        <Field label={t.allow} hint={t.allowHint}>
          <div className="stack-sm">
            {allow.length > 0 && (
              <div className="row-wrap">
                {allow.map((entry) => (
                  <button
                    key={`${entry.type}:${entry.id}`}
                    type="button"
                    className="chip"
                    onClick={() => setAllow(allow.filter((e) => e !== entry))}
                  >
                    {entry.type === "team" ? <UsersRound /> : <ShieldCheck />} <bdi>{entry.name ?? entry.id}</bdi>{" "}
                    <Trash2 />
                  </button>
                ))}
              </div>
            )}
            <SubjectPicker
              taken={allow}
              withLevel={false}
              onAdd={(s) => setAllow([...allow, { type: s.type, id: s.id, name: s.name }])}
            />
          </div>
        </Field>
      </div>
    </Modal>
  );
}

export function UnlockDialog({
  project,
  branch,
  onClose,
}: {
  project: ProjectDetail;
  branch: ProjectBranch;
  onClose: () => void;
}) {
  const t = useT(words).lock;
  const c = useT(common).common;
  const toast = useToast();
  const unlock = useCall(
    () => api.del(`/projects/${project.id}/branches/${branch.name}/lock`),
    [keys.project(project.id), keys.projects, keys.instances, keys.projectAccess(project.id)],
  );
  return (
    <Modal
      title={t.unlockTitle(branch.name)}
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>{c.cancel}</Button>
          <Button
            variant="primary"
            icon={<LockOpen />}
            loading={unlock.isPending}
            onClick={() =>
              unlock.mutate(undefined, {
                onSuccess: () => {
                  toast("success", t.unlocked(branch.name));
                  onClose();
                },
                onError: (e) => toast("error", t.notUnlocked, errorText(e)),
              })
            }
          >
            {t.unlock}
          </Button>
        </>
      }
    >
      <div className="stack">
        {branch.lock && (
          <Callout tone="warning">
            {t.lockedTitle(branch.name, branch.lock.locked_by ?? "")}
            {branch.lock.reason ? ` · ${branch.lock.reason}` : ""}
          </Callout>
        )}
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
      <b>{t.banner(lock.locked_by ?? "", lock.reason)}</b> {allowed ? t.bannerAllowed : t.allows(allowedNames(lock))}
    </Callout>
  );
}
