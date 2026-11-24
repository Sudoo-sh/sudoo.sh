// Project roles on a project's Access tab (src/sudoo/projects/project_roles.py, people.py, elevation.py): the matrix of
// what each role may do per stage (editable by the project's admins), who can do what and why, temporary access, and
// the switch of a project made before project roles. The server decides everything again.

import { useQuery } from "@tanstack/react-query";
import { ArrowRightLeft, Check, Clock, RotateCcw, Send, ShieldCheck, UsersRound, X } from "lucide-react";
import { useMemo, useState } from "react";
import { api, errorText } from "@/api/client";
import { keys, useCall } from "@/api/queries";
import type {
  AccessLevel,
  AccessPerson,
  RoleRequest,
  Permission,
  ProjectAccess,
  ProjectDetail,
  RoleScope,
  Stage,
} from "@/api/types";
import { Button, Callout, Field, Skeleton, Tag } from "@/components/app/basics";
import { Modal, useToast } from "@/components/app/overlay";
import { SelectBox } from "@/components/app/select";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { useT } from "@/i18n";
import common from "@/i18n/common";
import words from "@/i18n/teams";
import { dateTime } from "@/lib/format";

const STAGES: Stage[] = ["production", "staging", "development"];
const peopleKey = (id: string) => ["project", id, "access", "people"] as const;
const requestsKey = (id: string) => ["project", id, "access", "requests"] as const;

/** A project made before project roles: what switching changes, and the switch. */
export function ModelCard({ project }: { project: ProjectDetail }) {
  const t = useT(words);
  const r = t.roles;
  const c = useT(common).common;
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const preview = useQuery({
    queryKey: ["project", project.id, "access", "model"],
    queryFn: () =>
      api.get<{
        model: string;
        changes: {
          user: string;
          name: string;
          before: AccessLevel;
          after: AccessLevel;
          grant: boolean;
          lost: Permission[];
        }[];
      }>(`/projects/${project.id}/access/model`),
    enabled: open,
  });
  const change = useCall(
    () => api.post(`/projects/${project.id}/access/model`, { model: "project_roles" }),
    [keys.projectAccess(project.id), keys.project(project.id), peopleKey(project.id)],
  );
  return (
    <section className="card">
      <div className="card-head">
        <ArrowRightLeft size={18} />
        <h2 className="spacer">{r.legacyTitle}</h2>
      </div>
      <div className="card-body stack-sm">
        <p className="small muted">{r.legacyText}</p>
        <div>
          <Button size="sm" onClick={() => setOpen(true)}>
            {r.preview}
          </Button>
        </div>
      </div>
      {open && (
        <Modal
          wide
          title={r.changesTitle}
          onClose={() => setOpen(false)}
          footer={
            <>
              <Button onClick={() => setOpen(false)}>{c.cancel}</Button>
              <Button
                variant="primary"
                loading={change.isPending}
                onClick={() =>
                  change.mutate(undefined, {
                    onSuccess: () => {
                      toast("success", r.switched);
                      setOpen(false);
                    },
                    onError: (e) => toast("error", r.notSwitched, errorText(e)),
                  })
                }
              >
                {r.switch}
              </Button>
            </>
          }
        >
          <div className="stack">
            <p className="small muted">{r.changesText}</p>
            {preview.isPending ? (
              <Skeleton height={120} />
            ) : !preview.data?.changes.length ? (
              <p className="small faint">{r.nothingChanges}</p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{r.person}</TableHead>
                    <TableHead>{r.before}</TableHead>
                    <TableHead>{r.after}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {preview.data.changes.map((ch) => (
                    <TableRow key={ch.user}>
                      <TableCell>
                        <bdi>{ch.name}</bdi>
                      </TableCell>
                      <TableCell className="small">{t.levels[ch.before]}</TableCell>
                      <TableCell className="small">
                        {t.levels[ch.after]}
                        {ch.grant && <span className="faint"> · {r.kept}</span>}
                        {ch.lost.length > 0 && (
                          <div className="xsmall faint">{r.loses(ch.lost.map((p) => r.rights[p] ?? p).join(", "))}</div>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </div>
        </Modal>
      )}
    </section>
  );
}

/** The matrix: one row per right and stage, one column per role; admins of the project tick and untick. */
export function RolesMatrix({
  project,
  data,
  editable,
}: {
  project: ProjectDetail;
  data: ProjectAccess;
  editable: boolean;
}) {
  const t = useT(words);
  const r = t.roles;
  const toast = useToast();
  const roles = data.roles;
  const [draft, setDraft] = useState(roles.matrix);
  const dirty = JSON.stringify(draft) !== JSON.stringify(roles.matrix);
  const save = useCall(
    (matrix: ProjectAccess["roles"]["matrix"] | null) =>
      api.put<ProjectAccess>(`/projects/${project.id}/access/matrix`, { matrix }),
    [keys.projectAccess(project.id), peopleKey(project.id)],
  );
  const toggle = (level: string, scope: RoleScope, right: Permission, on: boolean) =>
    setDraft((current) => {
      const rights = new Set(current[level]![scope]);
      if (on) rights.add(right);
      else rights.delete(right);
      return { ...current, [level]: { ...current[level]!, [scope]: [...rights] } };
    });
  const submit = (matrix: ProjectAccess["roles"]["matrix"] | null) =>
    save.mutate(matrix ? Object.fromEntries(roles.editable.map((level) => [level, matrix[level]!])) : null, {
      onSuccess: (answer) => {
        setDraft(answer.roles.matrix);
        toast("success", r.saved);
      },
      onError: (e) => toast("error", r.notSaved, errorText(e)),
    });
  const scopes: RoleScope[] = [...STAGES, "project"];
  return (
    <section className="card">
      <div className="card-head">
        <ShieldCheck size={18} />
        <div className="spacer">
          <h2>{r.title}</h2>
          <div className="sub">{r.text}</div>
        </div>
      </div>
      <div className="card-body" style={{ overflowX: "auto" }}>
        <Table className="roles-matrix">
          <TableHeader>
            <TableRow>
              <TableHead>{r.right}</TableHead>
              {roles.levels.map((level) => (
                <TableHead key={level} className="center">
                  {t.levels[level]}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {scopes.map((scope) => (
              <ScopeRows
                key={scope}
                scope={scope}
                rights={scope === "project" ? roles.rights.project : roles.rights.stage}
                levels={roles.levels}
                draft={draft}
                changed={roles.changed}
                editable={editable && roles.editable}
                onToggle={toggle}
              />
            ))}
          </TableBody>
        </Table>
      </div>
      {editable && (
        <div className="card-foot row-wrap">
          <Button
            size="sm"
            variant="ghost"
            icon={<RotateCcw />}
            disabled={!Object.keys(roles.changed).length || save.isPending}
            onClick={() => submit(null)}
          >
            {r.reset}
          </Button>
          <span className="spacer" />
          <Button size="sm" variant="primary" disabled={!dirty} loading={save.isPending} onClick={() => submit(draft)}>
            {useT(common).common.save}
          </Button>
        </div>
      )}
    </section>
  );
}

function ScopeRows({
  scope,
  rights,
  levels,
  draft,
  changed,
  editable,
  onToggle,
}: {
  scope: RoleScope;
  rights: Permission[];
  levels: AccessLevel[];
  draft: ProjectAccess["roles"]["matrix"];
  changed: ProjectAccess["roles"]["changed"];
  editable: false | AccessLevel[];
  onToggle: (level: string, scope: RoleScope, right: Permission, on: boolean) => void;
}) {
  const r = useT(words).roles;
  return (
    <>
      <TableRow className="group-row">
        <TableCell colSpan={levels.length + 1}>
          <b>{r.stages[scope]}</b>
        </TableCell>
      </TableRow>
      {rights.map((right) => (
        <TableRow key={`${scope}:${right}`}>
          <TableCell className="small">{r.rights[right] ?? right}</TableCell>
          {levels.map((level) => {
            const on = draft[level]?.[scope]?.includes(right) ?? false;
            const canEdit = editable && editable.includes(level);
            return (
              <TableCell key={level} className="center" data-changed={changed[level]?.includes(scope) || undefined}>
                {canEdit ? (
                  <Checkbox
                    checked={on}
                    aria-label={`${level} ${scope} ${right}`}
                    onCheckedChange={(value) => onToggle(level, scope, right, value === true)}
                  />
                ) : on ? (
                  <Check size={16} aria-label={r.always} className="positive" />
                ) : (
                  <span className="faint" aria-hidden>
                    ·
                  </span>
                )}
              </TableCell>
            );
          })}
        </TableRow>
      ))}
    </>
  );
}

/** Who can do what: each person, their role, its source and their rights per stage. */
export function PeopleCard({ project }: { project: ProjectDetail }) {
  const t = useT(words);
  const p = t.people;
  const all = useT(common);
  const { data, isPending, error } = useQuery({
    queryKey: peopleKey(project.id),
    queryFn: () => api.get<AccessPerson[]>(`/projects/${project.id}/access/people`),
  });
  const shown = useMemo(() => (data ?? []).filter((person) => person.level !== "none"), [data]);
  return (
    <section className="card">
      <div className="card-head">
        <UsersRound size={18} />
        <div className="spacer">
          <h2>{p.title}</h2>
          <div className="sub">{p.text}</div>
        </div>
      </div>
      <div className="card-body" style={{ overflowX: "auto" }}>
        {error ? (
          <Callout tone="warning">{errorText(error)}</Callout>
        ) : isPending ? (
          <Skeleton height={140} />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{p.person}</TableHead>
                <TableHead>{p.role}</TableHead>
                <TableHead>{p.from}</TableHead>
                {STAGES.map((stage) => (
                  <TableHead key={stage}>{t.roles.stages[stage]}</TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {shown.map((person) => (
                <TableRow key={person.user}>
                  <TableCell>
                    <bdi>{person.name}</bdi>
                    <div className="xsmall faint">{all.roles[person.panel_role] ?? person.panel_role}</div>
                  </TableCell>
                  <TableCell>
                    <Tag tone={person.level === "admin" ? "accent" : undefined}>{t.levels[person.level]}</Tag>
                    {person.temporary.map((tmp) => (
                      <div key={tmp.id} className="xsmall">
                        <Clock size={12} /> {p.temporary(t.roles.stages[tmp.stage] ?? tmp.stage, t.levels[tmp.level])}
                      </div>
                    ))}
                  </TableCell>
                  <TableCell className="small muted">
                    {p.sources[person.source]}
                    {(person.source === "team" || person.source === "github") && person.source_label && (
                      <div className="xsmall faint">
                        <bdi dir="ltr">{person.source_label}</bdi>
                      </div>
                    )}
                  </TableCell>
                  {STAGES.map((stage) => (
                    <TableCell
                      key={stage}
                      className="xsmall"
                      title={person.stages[stage].map((r) => t.roles.rights[r] ?? r).join("\n")}
                    >
                      {person.stages[stage].length ? (
                        p.count(person.stages[stage].length)
                      ) : (
                        <span className="faint">{p.nothing}</span>
                      )}
                    </TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </div>
    </section>
  );
}

/** Temporary access: ask for a higher role in one stage; the project's admins approve or refuse; it ends by itself. */
export function RequestsCard({
  project,
  maxHours,
  enabled,
}: {
  project: ProjectDetail;
  maxHours: number;
  enabled: boolean;
}) {
  const t = useT(words);
  const q = t.requests;
  const toast = useToast();
  const [asking, setAsking] = useState(false);
  const { data } = useQuery({
    queryKey: requestsKey(project.id),
    queryFn: () => api.get<RoleRequest[]>(`/projects/${project.id}/role-requests`),
  });
  const refresh = [requestsKey(project.id), peopleKey(project.id), keys.project(project.id)];
  const act = useCall(
    ({ id, how }: { id: string; how: "approve" | "refuse" | "end" }) =>
      how === "end"
        ? api.del(`/projects/${project.id}/role-requests/${id}`)
        : api.post(`/projects/${project.id}/role-requests/${id}/${how}`, {}),
    refresh,
  );
  const run = (id: string, how: "approve" | "refuse" | "end", done: string) =>
    act.mutate(
      { id, how },
      { onSuccess: () => toast("success", done), onError: (e) => toast("error", q.notDone, errorText(e)) },
    );
  return (
    <section className="card">
      <div className="card-head">
        <Clock size={18} />
        <div className="spacer">
          <h2>{q.title}</h2>
          <div className="sub">{enabled ? q.text(maxHours) : q.off}</div>
        </div>
        {enabled && (
          <Button size="sm" icon={<Send />} onClick={() => setAsking(true)}>
            {q.ask}
          </Button>
        )}
      </div>
      <div className="card-body stack-sm">
        {!data?.length ? (
          <p className="small faint">{q.none}</p>
        ) : (
          data.map((req) => (
            <div key={req.id} className="row-wrap small">
              <span className="spacer">
                <bdi>{q.asks(req.name, t.levels[req.level], t.roles.stages[req.stage] ?? req.stage, req.hours)}</bdi>
                <div className="xsmall faint">
                  <bdi dir="auto">{req.reason}</bdi>
                  {req.expires_at && req.status === "approved" ? ` · ${q.until(dateTime(req.expires_at))}` : ""}
                </div>
              </span>
              <Tag tone={req.status === "approved" ? "positive" : req.status === "pending" ? "caution" : undefined}>
                {q.status[req.status]}
              </Tag>
              {req.status === "pending" && (
                <>
                  <Button size="sm" variant="ghost" icon={<Check />} onClick={() => run(req.id, "approve", q.approved)}>
                    {q.approve}
                  </Button>
                  <Button size="sm" variant="ghost" icon={<X />} onClick={() => run(req.id, "refuse", q.refused)}>
                    {q.refuse}
                  </Button>
                </>
              )}
              {(req.status === "pending" || req.status === "approved") && (
                <Button size="sm" variant="ghost" onClick={() => run(req.id, "end", q.ended)}>
                  {req.status === "pending" ? q.withdraw : q.end}
                </Button>
              )}
            </div>
          ))
        )}
      </div>
      {asking && <RequestDialog project={project} maxHours={maxHours} onClose={() => setAsking(false)} />}
    </section>
  );
}

export function RequestDialog({
  project,
  maxHours,
  onClose,
  stage: initialStage,
}: {
  project: ProjectDetail;
  maxHours: number;
  onClose: () => void;
  stage?: Stage;
}) {
  const t = useT(words);
  const q = t.requests;
  const c = useT(common).common;
  const toast = useToast();
  const [stage, setStage] = useState<Stage>(initialStage ?? "production");
  const [level, setLevel] = useState<AccessLevel>("maintainer");
  const [hours, setHours] = useState(Math.min(2, maxHours));
  const [reason, setReason] = useState("");
  const send = useCall(
    () => api.post(`/projects/${project.id}/role-requests`, { stage, level, hours, reason: reason.trim() }),
    [requestsKey(project.id)],
  );
  return (
    <Modal
      title={q.ask}
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>{c.cancel}</Button>
          <Button
            variant="primary"
            icon={<Send />}
            loading={send.isPending}
            disabled={reason.trim().length < 10}
            onClick={() =>
              send.mutate(undefined, {
                onSuccess: () => {
                  toast("success", q.sent);
                  onClose();
                },
                onError: (e) => toast("error", q.notSent, errorText(e)),
              })
            }
          >
            {q.send}
          </Button>
        </>
      }
    >
      <div className="stack">
        <div className="form-grid">
          <Field label={q.stage}>
            <SelectBox value={stage} onValueChange={(v) => setStage(v as Stage)} aria-label={q.stage}>
              {STAGES.map((s) => (
                <option key={s} value={s}>
                  {t.roles.stages[s]}
                </option>
              ))}
            </SelectBox>
          </Field>
          <Field label={q.role}>
            <SelectBox value={level} onValueChange={(v) => setLevel(v as AccessLevel)} aria-label={q.role}>
              {(["tester", "developer", "maintainer", "admin"] as AccessLevel[]).map((l) => (
                <option key={l} value={l}>
                  {t.levels[l]}
                </option>
              ))}
            </SelectBox>
          </Field>
          <Field label={q.hours}>
            <Input
              type="number"
              min={1}
              max={maxHours}
              value={hours}
              onChange={(e) => setHours(Math.max(1, Math.min(maxHours, Number(e.target.value) || 1)))}
            />
          </Field>
        </div>
        <Field label={q.reason} hint={q.reasonHint}>
          <Textarea dir="auto" maxLength={500} rows={3} value={reason} onChange={(e) => setReason(e.target.value)} />
        </Field>
      </div>
    </Modal>
  );
}
