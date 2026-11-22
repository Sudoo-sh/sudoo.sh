// What can be done to a project's branch, as dialogs: change its stage, create its instance, rebuild its staging
// instance from production, delete its instance. Used by a project's workspace (ProjectWorkspace.tsx).

import { Plus, RotateCcw, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import { api, errorText } from "@/api/client";
import { keys, useCall } from "@/api/queries";
import type { Instance, Job, ProjectBranch, ProjectDetail, ProjectMeta, Stage } from "@/api/types";
import { PromotionChecklist, promotionKey, usePromotionCheck } from "@/components/PromotionChecklist";
import { STAGE_LABEL } from "@/components/StageTag";
import { Button, Callout, Field, Switch } from "@/components/app/basics";
import { Input } from "@/components/ui/input";
import { Modal, useToast } from "@/components/app/overlay";
import { useT } from "@/i18n";
import common from "@/i18n/common";
import words from "@/i18n/projects";
import promotionWords from "@/i18n/promotion";
import { ago } from "@/lib/format";

export function BranchCommit({ branch }: { branch: ProjectBranch }) {
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

export type BranchDialog =
  | { kind: "lock"; branch: ProjectBranch }
  | { kind: "unlock"; branch: ProjectBranch }
  | { kind: "stage"; branch: ProjectBranch; stage: Stage }
  | { kind: "create"; branch: ProjectBranch }
  | { kind: "rebuild"; branch: ProjectBranch; instance: string }
  | { kind: "delete"; instance: Instance }
  | { kind: "compare"; branch: ProjectBranch; base: string }
  | { kind: "promotion"; branch: ProjectBranch };

export function StageDialog({ project, meta, branch, stage, onClose, onCompare }: { project: ProjectDetail; meta: ProjectMeta; branch: ProjectBranch; stage: Stage; onClose: () => void; onCompare?: (base: string) => void }) {
  const t = useT(words);
  const d = t.stageDialog;
  const c = useT(common).common;
  const toast = useToast();
  const p = useT(promotionWords).check;
  const [understood, setUnderstood] = useState(false);
  const [anyway, setAnyway] = useState(false);
  const production = stage === "production" || branch.stage === "production";
  const promoting = stage === "production" && branch.stage !== "production";
  const check = usePromotionCheck(project.id, branch.name, promoting);
  const stopped = promoting && Boolean(check.data?.blocking) && !anyway;
  const save = useCall(
    () => api.put<ProjectBranch>(`/projects/${project.id}/branches/${branch.name}/stage`, { stage, confirm_production: production, ...(promoting && anyway ? { promote_anyway: true } : {}) }),
    // the branch's resources follow its stage: "branch-resources" and "project-resources" (ProjectResources.tsx)
    [keys.project(project.id), keys.projects, keys.instances, promotionKey(project.id, branch.name),
      ["branch-resources", project.id], ["project-resources", project.id]],
  );
  return (
    <Modal wide={promoting} title={d.title(branch.name, STAGE_LABEL[stage])} onClose={onClose}
      footer={<><Button onClick={onClose}>{c.cancel}</Button>
        <Button variant={production ? "danger" : "primary"} loading={save.isPending} disabled={(production && !understood) || stopped}
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
        {promoting && <PromotionChecklist project={project} branch={branch} onCompare={onCompare} />}
        {promoting && check.data?.blocking && (
          <div className="stack-xs">
            <Switch checked={anyway} onChange={setAnyway} label={p.promoteAnyway} />
            <p className="small muted">{p.promoteAnywayText}</p>
          </div>
        )}
        {production && <Switch checked={understood} onChange={setUnderstood} label={d.confirm} />}
      </div>
    </Modal>
  );
}

export function CreateDialog({ project, branch, production, onClose, onJob }: { project: ProjectDetail; branch: ProjectBranch; production: string | null; onClose: () => void; onJob: (id: string) => void }) {
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
          <Input dir="ltr" value={name} maxLength={40} onChange={(e) => setName(e.target.value)} />
        </Field>
        {branch.stage === "development" && <Switch checked={demo} onChange={setDemo} label={d.demo} />}
        <Switch checked={modules} onChange={setModules} label={d.modules} />
      </div>
    </Modal>
  );
}

export function RebuildDialog({ project, instance, production, onClose, onJob }: { project: ProjectDetail; branch: ProjectBranch; instance: string; production: string | null; onClose: () => void; onJob: (id: string) => void }) {
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

export function DeleteInstanceDialog({ instance, onClose, onJob }: { instance: Instance; onClose: () => void; onJob: (id: string) => void }) {
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
          <Input dir="ltr" autoFocus value={typed} onChange={(e) => setTyped(e.target.value)} />
        </Field>
        {production && (
          <Field label={<>{s.typeId(<code key="p">production</code>)}</>}>
            <Input dir="ltr" value={typedStage} onChange={(e) => setTypedStage(e.target.value)} />
          </Field>
        )}
      </div>
    </Modal>
  );
}
