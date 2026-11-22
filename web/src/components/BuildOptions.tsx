// Build options, the Odoo.sh way: a branch's "Behaviour upon new commits" and "New build options" (its Branch
// settings tab), and the project's defaults for development branches (its Settings tab). A change is shown at once
// and sent to the server; when the server refuses it, the value goes back and the reason is shown under the field.
// Each value says, quietly, where it comes from: the branch, the project, or the panel's setting.

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowUpRight, RotateCcw } from "lucide-react";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router";
import { api, errorText } from "@/api/client";
import { keys, useBranchOptions } from "@/api/queries";
import type {
  BranchBuildOptions,
  BuildOptionSource,
  BuildOptionValues,
  BuildOptionsChange,
  ModuleInstall,
  OnNewCommit,
  Project,
  ProjectBranch,
  ProjectDetail,
  ProjectMeta,
  ProjectOptions,
  Stage,
  TestSuite,
} from "@/api/types";
import { Button, Callout, Skeleton, Switch } from "@/components/app/basics";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { useT } from "@/i18n";
import words from "@/i18n/build-options";
import { ago } from "@/lib/format";

type Key = keyof BuildOptionValues;
type Errors = Partial<Record<Key | "general", string>>;
/** where a value comes from, with the panel's setting told apart from the project's own value */
type Origin = BuildOptionSource | "panel";

const NEW_BUILD_KEYS: Key[] = ["module_install", "modules", "test_suite", "test_tags", "demo_data"];
/** a branch's option and the project's option that gives its default (projects/build_options.PROJECT_OPTION) */
const PROJECT_OPTION: Record<Key, keyof ProjectOptions> = {
  on_new_commit: "development_on_new_commit",
  module_install: "module_install",
  modules: "modules",
  test_suite: "test_suite",
  test_tags: "test_tags",
  demo_data: "development_demo_data",
};
const ADMIN_GUIDE = "/help/docs/ADMIN_GUIDE.md";

/** Odoo's documentation of --test-tags, for the project's version when Odoo publishes it, else the newest. */
export function testTagsDocs(version: string) {
  const major = Number(version.split(".")[0]);
  const shown = /^\d+\.0$/.test(version) && major >= 17 ? version : "master";
  return `https://www.odoo.com/documentation/${shown}/developer/reference/backend/testing.html#test-selection`;
}

/** Module names and test tags as the server keeps them: commas, no spaces. */
const tidy = (text: string) =>
  text
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean)
    .join(",");

/** The field a refusal is about: the first key it names, else the last one sent. */
function fieldOf(detail: string, sent: Key[]): Key {
  for (const key of ["modules", "test_tags", "module_install", "test_suite", "on_new_commit", "demo_data"] as Key[])
    if (detail.includes(key)) return key === "module_install" ? "modules" : key === "test_suite" ? "test_tags" : key;
  return sent.at(-1) ?? "on_new_commit";
}

// ---------------------------------------------------------------- small parts

/** Where a value comes from: a quiet label, its explanation on hover. */
function Origin({ origin, label, hint }: { origin: Origin | "own"; label: string; hint?: string }) {
  return (
    <span className="bo-origin" data-origin={origin} title={hint}>
      {label}
    </span>
  );
}

/** A section's field: its name, where its value comes from, and Use the default again when it has its own. */
function FieldHead({ title, origin, onReset, resetLabel }: { title: string; origin: ReactNode; onReset?: () => void; resetLabel: string }) {
  return (
    <div className="bo-field-head">
      {title && <h4>{title}</h4>}
      {origin}
      {onReset && <Button size="sm" variant="ghost" icon={<RotateCcw />} onClick={onReset} aria-label={resetLabel} title={resetLabel} />}
    </div>
  );
}

/** A choice among options, each with its explanation; the chosen one may show more under it (a field to fill). */
function Choices<T extends string>({
  label,
  value,
  options,
  disabled,
  onChange,
}: {
  label: string;
  value: T;
  options: { value: T; label: string; hint?: string; extra?: ReactNode; disabled?: boolean }[];
  disabled?: boolean;
  onChange: (value: T) => void;
}) {
  const id = useId();
  return (
    <RadioGroup className="bo-choices" value={value} onValueChange={(v) => onChange(v as T)} disabled={disabled} aria-label={label}>
      {options.map((option) => (
        <div key={option.value} className="bo-choice" data-checked={option.value === value || undefined} data-disabled={disabled || option.disabled || undefined}>
          <label className="bo-choice-row" htmlFor={`${id}-${option.value}`}>
            <RadioGroupItem id={`${id}-${option.value}`} value={option.value} disabled={option.disabled} />
            <span className="bo-choice-text">
              <span className="bo-choice-label">{option.label}</span>
              {option.hint && <span className="bo-choice-hint">{option.hint}</span>}
            </span>
          </label>
          {option.value === value && option.extra && <div className="bo-extra">{option.extra}</div>}
        </div>
      ))}
    </RadioGroup>
  );
}

/** A text field (module names, test tags) kept when it is left or Enter is pressed. */
function TextOption({
  label,
  value,
  placeholder,
  error,
  disabled,
  autoFocus,
  onCommit,
}: {
  label: string;
  value: string;
  placeholder: string;
  error?: string;
  disabled?: boolean;
  autoFocus?: boolean;
  onCommit: (value: string) => void;
}) {
  const [text, setText] = useState(value);
  const id = useId();
  useEffect(() => setText(value), [value]);
  const commit = () => {
    if (tidy(text) !== tidy(value) || !tidy(text)) onCommit(tidy(text));
  };
  return (
    <div className="bo-text" data-invalid={error ? true : undefined}>
      <label htmlFor={id} className="sr-only">{label}</label>
      <Input id={id} dir="ltr" className="mono" value={text} placeholder={placeholder} disabled={disabled} autoFocus={autoFocus}
        maxLength={4000} spellCheck={false} autoComplete="off" aria-invalid={error ? true : undefined} aria-describedby={error ? `${id}-error` : undefined}
        onChange={(e) => setText(e.target.value)} onBlur={commit}
        onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); commit(); } }} />
      {error && <p id={`${id}-error`} className="bo-error" role="alert">{error}</p>}
    </div>
  );
}

// ---------------------------------------------------------------- the controls, shared by the branch and the project

type Controls = {
  values: BuildOptionValues;
  stage: Stage;
  editable: boolean;
  errors: Errors;
  version: string;
  /** a field's name, origin and reset */
  head: (key: Key, title: string) => ReactNode;
  save: (change: BuildOptionsChange) => void;
  /** a choice that needs text (a list of modules, custom tags) waits for it before it is sent */
  pending: { module_install?: ModuleInstall; test_suite?: TestSuite };
  setPending: (pending: { module_install?: ModuleInstall; test_suite?: TestSuite }) => void;
  setError: (key: Key, message: string | undefined) => void;
};

function BehaviourField({ c, stage, title }: { c: Controls; stage: "development" | "staging"; title?: string }) {
  const t = useT(words);
  const help = t.onNewCommitHelp[stage];
  return (
    <div className="bo-field">
      {c.head("on_new_commit", title ?? t.behaviour)}
      <Choices<OnNewCommit>
        label={t.behaviour}
        value={c.values.on_new_commit}
        disabled={!c.editable}
        onChange={(value) => c.save({ on_new_commit: value })}
        options={(["new_build", "update", "nothing"] as OnNewCommit[]).map((value) => ({ value, label: t.onNewCommit[value], hint: help[value] }))}
      />
      {c.errors.on_new_commit && <p className="bo-error" role="alert">{c.errors.on_new_commit}</p>}
    </div>
  );
}

function ModulesField({ c }: { c: Controls }) {
  const t = useT(words);
  const shown = c.pending.module_install ?? c.values.module_install;
  const choose = (value: ModuleInstall) => {
    c.setError("modules", undefined);
    if (value === "list" && !c.values.modules) return c.setPending({ ...c.pending, module_install: "list" });
    c.setPending({ ...c.pending, module_install: undefined });
    c.save({ module_install: value });
  };
  const commit = (modules: string) => {
    if (!modules) return c.setError("modules", t.modulesRequired);
    c.setPending({ ...c.pending, module_install: undefined });
    c.save(shown === "list" && c.values.module_install !== "list" ? { module_install: "list", modules } : { modules });
  };
  return (
    <div className="bo-field">
      {c.head("module_install", t.modules)}
      <Choices<ModuleInstall>
        label={t.modules}
        value={shown}
        disabled={!c.editable}
        onChange={choose}
        options={(["repository", "all", "list"] as ModuleInstall[]).map((value) => ({
          value,
          label: t.moduleInstall[value],
          hint: t.moduleInstallHint[value],
          extra:
            value === "list" ? (
              <TextOption label={t.modulesLabel} value={c.values.modules} placeholder={t.modulesPlaceholder} error={c.errors.modules}
                disabled={!c.editable} autoFocus={c.pending.module_install === "list"} onCommit={commit} />
            ) : undefined,
        }))}
      />
    </div>
  );
}

function TestsField({ c }: { c: Controls }) {
  const t = useT(words);
  const every = (c.pending.module_install ?? c.values.module_install) === "all" && c.stage === "development";
  const shown = every ? "off" : (c.pending.test_suite ?? c.values.test_suite);
  const choose = (value: TestSuite) => {
    c.setError("test_tags", undefined);
    if (value === "custom" && !c.values.test_tags) return c.setPending({ ...c.pending, test_suite: "custom" });
    c.setPending({ ...c.pending, test_suite: undefined });
    c.save({ test_suite: value });
  };
  const commit = (tags: string) => {
    if (!tags) return c.setError("test_tags", t.tagsRequired);
    c.setPending({ ...c.pending, test_suite: undefined });
    c.save(shown === "custom" && c.values.test_suite !== "custom" ? { test_suite: "custom", test_tags: tags } : { test_tags: tags });
  };
  return (
    <div className="bo-field">
      {c.head("test_suite", t.tests)}
      {every && <p className="bo-note">{t.testsOffAll}</p>}
      <Choices<TestSuite>
        label={t.tests}
        value={shown}
        disabled={!c.editable || every}
        onChange={choose}
        options={(["off", "selection", "custom"] as TestSuite[]).map((value) => ({
          value,
          label: t.testSuite[value],
          hint: t.testSuiteHint[value],
          extra:
            value === "custom" ? (
              <div className="stack-sm">
                <TextOption label={t.tagsLabel} value={c.values.test_tags} placeholder={t.tagsPlaceholder} error={c.errors.test_tags}
                  disabled={!c.editable} autoFocus={c.pending.test_suite === "custom"} onCommit={commit} />
                <a className="bo-docs" href={testTagsDocs(c.version)} target="_blank" rel="noreferrer">
                  {t.tagsDocs} <ArrowUpRight className="flip-rtl" />
                </a>
              </div>
            ) : undefined,
        }))}
      />
    </div>
  );
}

function DemoField({ c }: { c: Controls }) {
  const t = useT(words);
  const id = useId();
  return (
    <div className="bo-field">
      {c.head("demo_data", t.demo)}
      <label className="bo-check" htmlFor={id} data-disabled={!c.editable || undefined}>
        <Checkbox id={id} checked={c.values.demo_data} disabled={!c.editable} onCheckedChange={(v) => c.save({ demo_data: v === true })} />
        <span className="bo-choice-text">
          <span className="bo-choice-label">{t.demoLabel}</span>
          <span className="bo-choice-hint">{t.demoHint}</span>
        </span>
      </label>
      {c.errors.demo_data && <p className="bo-error" role="alert">{c.errors.demo_data}</p>}
    </div>
  );
}

// ---------------------------------------------------------------- a branch's settings

/** What a change does to the branch's options, before the server answers. */
function applied(found: BranchBuildOptions, change: BuildOptionsChange): BranchBuildOptions {
  const options = { ...found.options };
  const sources = { ...found.sources };
  const own: Partial<BuildOptionValues> = { ...found.own };
  for (const [key, value] of Object.entries(change) as [Key, BuildOptionValues[Key] | null][]) {
    if (value === null) {
      delete own[key];
      (options as Record<Key, unknown>)[key] = found.project_defaults[key];
      sources[key] = "project";
    } else {
      (own as Record<Key, unknown>)[key] = value;
      (options as Record<Key, unknown>)[key] = value;
      sources[key] = "branch";
    }
  }
  return { ...found, options, sources, own };
}

/** A branch's settings, the Odoo.sh way: what a new commit does and how a new build is made. */
export function BranchSettings({ project, branch, editable }: { project: ProjectDetail; branch: ProjectBranch; editable: boolean }) {
  const t = useT(words);
  const production = branch.stage === "production";
  const { data, isPending, error } = useBranchOptions(project.id, branch.name, !production);
  const client = useQueryClient();
  const key = keys.branchOptions(project.id, branch.name);
  const [errors, setErrors] = useState<Errors>({});
  const [pending, setPending] = useState<Controls["pending"]>({});
  const [customising, setCustomising] = useState(false);
  const [saved, setSaved] = useState(false);
  const timer = useRef<number>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  // the tab is the last of the branch's tabs: on a narrow pane the row scrolls, so it is brought into view
  useEffect(() => {
    document.querySelector('.ws-pane .tabs a[aria-current="page"]')?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, []);
  const path = `/projects/${encodeURIComponent(project.id)}/branches/${branch.name.split("/").map(encodeURIComponent).join("/")}/options`;
  const change = useMutation({
    mutationFn: (body: BuildOptionsChange) => api.put<BranchBuildOptions>(path, body),
    onMutate: async (body) => {
      await client.cancelQueries({ queryKey: key });
      const before = client.getQueryData<BranchBuildOptions>(key);
      if (before) client.setQueryData(key, applied(before, body));
      return { before };
    },
    onError: (failure, body, context) => {
      if (context?.before) client.setQueryData(key, context.before);
      const detail = errorText(failure);
      setErrors((found) => ({ ...found, [fieldOf(detail, Object.keys(body) as Key[])]: detail }));
    },
    onSuccess: (answer) => {
      client.setQueryData(key, answer);
      setSaved(true);
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => setSaved(false), 1600);
    },
    onSettled: () => client.invalidateQueries({ queryKey: keys.project(project.id) }),
  });

  if (production)
    return (
      <div className="stack tab-panel">
        <Callout tone="info">{t.production}</Callout>
      </div>
    );
  if (isPending)
    return (
      <div className="stack" aria-busy="true" aria-label={t.loading}>
        <Skeleton height={180} />
        <Skeleton height={320} />
      </div>
    );
  if (!data) return <Callout tone="warning">{t.notRead} {error ? errorText(error) : ""}</Callout>;

  const stage = data.stage === "staging" ? "staging" : "development";
  const hasOwn = NEW_BUILD_KEYS.some((k) => k in data.own);
  const useDefaults = !hasOwn && !customising;
  const origin = (k: Key): Origin => {
    const source = data.sources[k];
    if (source !== "project") return source;
    const option = stage === "staging" && k === "on_new_commit" ? "staging_on_push" : stage === "staging" && k === "test_suite" ? "staging_tests" : PROJECT_OPTION[k];
    return option in project.own_options ? "project" : "panel";
  };
  const save = (body: BuildOptionsChange) => {
    setErrors((found) => {
      const next = { ...found };
      for (const k of Object.keys(body)) delete next[k as Key];
      delete next.general;
      return next;
    });
    change.mutate(body);
  };
  const controls: Controls = {
    values: data.options,
    stage: data.stage,
    editable: editable,
    errors,
    version: project.odoo.version,
    save,
    pending,
    setPending,
    setError: (k, message) => setErrors((found) => ({ ...found, [k]: message })),
    head: (k, title) => {
      const from = origin(k);
      const inNewBuild = NEW_BUILD_KEYS.includes(k);
      return (
        <FieldHead title={title} resetLabel={t.reset}
          origin={<Origin origin={from} label={t.source[from]} hint={t.sourceHint[from]} />}
          onReset={editable && data.own[k] !== undefined && !(inNewBuild && useDefaults) ? () => save({ [k]: null }) : undefined} />
      );
    },
  };
  const newBuildControls: Controls = { ...controls, editable: editable && !useDefaults };
  const toggleDefaults = (checked: boolean) => {
    if (checked) {
      setCustomising(false);
      setPending({});
      if (hasOwn) save(Object.fromEntries(NEW_BUILD_KEYS.map((k) => [k, null])) as BuildOptionsChange);
    } else setCustomising(true);
  };

  return (
    <div className="stack tab-panel bo">
      {!editable && <Callout tone="info">{t.readOnly}</Callout>}
      <section className="card">
        <div className="card-head">
          <h2 className="spacer">{t.behaviour}</h2>
          <span className="bo-saved" data-shown={saved || undefined} aria-live="polite">{saved ? t.saved : ""}</span>
        </div>
        <div className="card-body stack">
          <p className="small muted">{t.behaviourText}</p>
          <BehaviourField c={controls} stage={stage} title="" />
        </div>
      </section>
      <section className="card">
        <div className="card-head"><h2>{stage === "staging" ? t.tests : t.newBuild}</h2></div>
        <div className="card-body stack">
          {stage === "staging" ? (
            <>
              <p className="small muted">{t.stagingNote}</p>
              <TestsField c={controls} />
            </>
          ) : (
            <>
              <p className="small muted">{t.newBuildText}</p>
              <label className="bo-check bo-defaults" data-disabled={!editable || undefined}>
                <Checkbox checked={useDefaults} disabled={!editable} onCheckedChange={(v) => toggleDefaults(v === true)} />
                <span className="bo-choice-text">
                  <span className="bo-choice-label">{t.useDefaults}</span>
                  <span className="bo-choice-hint">{t.useDefaultsHint}</span>
                </span>
              </label>
              <div className="bo-group" data-muted={useDefaults || undefined}>
                <ModulesField c={newBuildControls} />
                <TestsField c={newBuildControls} />
                <DemoField c={newBuildControls} />
              </div>
            </>
          )}
          {data.changed_by && data.changed_at && <p className="xsmall faint">{t.changedBy(data.changed_by, ago(data.changed_at))}</p>}
        </div>
      </section>
    </div>
  );
}

// ---------------------------------------------------------------- the project's defaults

type ProjectData = { project: ProjectDetail; meta: ProjectMeta };

/** The project's defaults for its development branches (and staging's tests, and commit statuses on GitHub). */
export function ProjectBuildDefaults({ project, meta, editable }: { project: ProjectDetail; meta: ProjectMeta; editable: boolean }) {
  const t = useT(words);
  const client = useQueryClient();
  const key = keys.project(project.id);
  const [errors, setErrors] = useState<Errors>({});
  const [pending, setPending] = useState<Controls["pending"]>({});
  const change = useMutation({
    mutationFn: (options: Partial<Record<keyof ProjectOptions, unknown>>) => api.patch<Project>(`/projects/${project.id}`, { options }),
    onMutate: async (options) => {
      await client.cancelQueries({ queryKey: key });
      const before = client.getQueryData<ProjectData>(key);
      if (before) {
        const own = { ...before.project.own_options } as Record<string, unknown>;
        const values = { ...before.project.options } as Record<string, unknown>;
        for (const [k, v] of Object.entries(options)) {
          if (v === null) delete own[k];
          else own[k] = values[k] = v;
        }
        client.setQueryData<ProjectData>(key, { ...before, project: { ...before.project, options: values as unknown as ProjectOptions, own_options: own } });
      }
      return { before };
    },
    onError: (failure, options, context) => {
      if (context?.before) client.setQueryData(key, context.before);
      const detail = errorText(failure);
      const sent = Object.keys(options).map((k) => (Object.entries(PROJECT_OPTION).find(([, p]) => p === k)?.[0] ?? k) as Key);
      setErrors((found) => ({ ...found, [fieldOf(detail, sent)]: detail }));
    },
    onSettled: () => Promise.all([client.invalidateQueries({ queryKey: key }), client.invalidateQueries({ queryKey: keys.projects })]),
  });
  const o = project.options;
  const values: BuildOptionValues = {
    on_new_commit: o.development_on_new_commit,
    module_install: o.module_install,
    modules: o.modules,
    test_suite: o.test_suite,
    test_tags: o.test_tags,
    demo_data: o.development_demo_data,
  };
  const send = (options: Partial<Record<keyof ProjectOptions, unknown>>) => {
    setErrors({});
    change.mutate(options);
  };
  const save = (body: BuildOptionsChange) =>
    send(Object.fromEntries(Object.entries(body).map(([k, v]) => [PROJECT_OPTION[k as Key], v])));
  const own = (option: keyof ProjectOptions) => option in project.own_options;
  const originOf = (option: keyof ProjectOptions) =>
    own(option) ? <Origin origin="project" label={t.setForProject} /> : <Origin origin="panel" label={t.followsPanel} hint={t.sourceHint.panel} />;
  const controls: Controls = {
    values,
    stage: "development",
    editable,
    errors,
    version: project.odoo.version,
    save,
    pending,
    setPending,
    setError: (k, message) => setErrors((found) => ({ ...found, [k]: message })),
    head: (k, title) => (
      <FieldHead title={title} resetLabel={t.reset} origin={originOf(PROJECT_OPTION[k])}
        onReset={editable && own(PROJECT_OPTION[k]) ? () => send({ [PROJECT_OPTION[k]]: null }) : undefined} />
    ),
  };
  const available = meta.commit_statuses?.available ?? false;
  const flag = (option: "staging_tests" | "commit_statuses", label: string, hint: ReactNode, disabled = false) => (
    <div className="bo-field">
      <div className="bo-switch-row">
        <span className="bo-choice-text">
          <span className="bo-choice-label">{label}</span>
          <span className="bo-choice-hint">{hint}</span>
          <span className="bo-switch-origin">
            {originOf(option)}
            {editable && own(option) && <Button size="sm" variant="ghost" icon={<RotateCcw />} onClick={() => send({ [option]: null })} aria-label={t.reset} title={t.reset} />}
          </span>
        </span>
        <Switch checked={Boolean(o[option]) && !disabled} disabled={!editable || disabled} onChange={(v) => send({ [option]: v })} label={<span className="sr-only">{label}</span>} />
      </div>
    </div>
  );
  return (
    <section className="card bo">
      <div className="card-head"><h2>{t.projectCard}</h2></div>
      <div className="card-body stack">
        <p className="small muted">{t.newBuildProject}</p>
        {errors.general && <p className="bo-error" role="alert">{errors.general}</p>}
        <BehaviourField c={controls} stage="development" />
        <ModulesField c={controls} />
        <TestsField c={controls} />
        <DemoField c={controls} />
        {flag("staging_tests", t.stagingTests, t.stagingTestsHint)}
        {flag(
          "commit_statuses",
          t.commitStatuses,
          available ? (
            t.commitStatusesHint
          ) : (
            <>
              {t.commitStatusesOff}{" "}
              <Link to={`${ADMIN_GUIDE}#commit-statuses`} viewTransition>{t.commitStatusesHow}</Link>
            </>
          ),
          !available,
        )}
      </div>
    </section>
  );
}
