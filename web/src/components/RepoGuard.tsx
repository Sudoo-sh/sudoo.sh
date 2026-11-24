// A project's Repository guard (a card of its Settings tab; /projects/<id>/guard, web/v1/repo_guard.py): CI checks
// added to the GitHub repository through a pull request, their results on builds (the Checks badge, also exported
// here), and branch rules on GitHub, previewed as current against new before they are applied. Only the project's
// Admins change it; everyone who sees the project sees it.

import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowUpRight,
  ChevronRight,
  CircleAlert,
  CircleCheck,
  CircleDashed,
  CircleMinus,
  Eye,
  GitPullRequest,
  Power,
  RefreshCw,
  ShieldCheck,
} from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router";
import { api, errorText } from "@/api/client";
import { useCall } from "@/api/queries";
import type {
  ChecksPreview,
  CiResult,
  Guard,
  GuardChecks,
  GuardFile,
  GuardRules,
  RulesPlan,
  RuleTarget,
  Strictness,
} from "@/api/guard";
import type { ProjectDetail } from "@/api/types";
import { Button, Callout, Field, Segmented, Skeleton, Switch, Tag } from "@/components/app/basics";
import { Modal, useToast } from "@/components/app/overlay";
import { SelectBox } from "@/components/app/select";
import { Input } from "@/components/ui/input";
import { useT } from "@/i18n";
import words from "@/i18n/repo-guard";
import { ago } from "@/lib/format";

const guardKey = (project: string) => ["project", project, "guard"] as const;
const path = (project: string) => `/projects/${encodeURIComponent(project)}/guard`;

export function useGuard(project: string) {
  return useQuery({ queryKey: guardKey(project), queryFn: () => api.get<Guard>(path(project)), staleTime: 60_000 });
}

const list = (text: string) =>
  text
    .split(/[\s,]+/)
    .map((s) => s.trim().replace(/^@/, ""))
    .filter(Boolean);

// ---------------------------------------------------------------- the Checks badge (builds and history)

const BADGE_TONE = { success: "positive", failure: "negative", pending: "caution" } as const;

/** The CI results of a build's commit, linked to its checks on GitHub; nothing when the project runs no checks. */
export function ChecksBadge({ project, sha }: { project: string; sha: string | null | undefined }) {
  const t = useT(words).badge;
  const guard = useGuard(project);
  // checks proposed to the repository (following the panel's "checks on" setting alone puts nothing on GitHub)
  const enabled = Boolean(sha && /^[0-9a-f]{7,40}$/.test(sha) && guard.data?.checks.enabled && guard.data.pull);
  const query = useQuery({
    queryKey: [...guardKey(project), "results", sha],
    queryFn: () => api.get<Record<string, CiResult>>(`${path(project)}/results?sha=${sha}`),
    enabled,
    staleTime: 60_000,
  });
  const result = sha ? query.data?.[sha] : undefined;
  if (!enabled || !result) return null;
  const label =
    result.state === "failure"
      ? t.failure(result.failed ?? 1)
      : result.state === "success"
        ? t.success
        : result.state === "pending"
          ? t.pending
          : result.state === "none"
            ? t.none
            : t.unknown;
  const tag = (
    <Tag tone={BADGE_TONE[result.state as keyof typeof BADGE_TONE]} icon={<ShieldCheck />}>
      {label}
    </Tag>
  );
  return result.url ? (
    <a
      className="checks-badge"
      href={result.url}
      target="_blank"
      rel="noreferrer"
      title={t.open}
      onClick={(e) => e.stopPropagation()}
    >
      {tag}
    </a>
  ) : (
    <span className="checks-badge" title={result.error}>
      {tag}
    </span>
  );
}

// ---------------------------------------------------------------- permissions

function Permissions({ guard, wanted }: { guard: Guard; wanted: ("checks" | "results" | "rules")[] }) {
  const t = useT(words);
  const g = guard.github;
  if (!g.app_connected)
    return (
      <Callout tone="info">
        {t.noApp}{" "}
        <Link to="/github" viewTransition>
          {t.openGitHubAccess}
        </Link>
      </Callout>
    );
  if (!g.installed)
    return (
      <Callout tone="warning">
        {t.notInstalled}{" "}
        {g.install_url && (
          <a href={g.install_url} target="_blank" rel="noreferrer">
            {t.install} <ArrowUpRight className="inline-icon flip-rtl" aria-hidden />
          </a>
        )}
      </Callout>
    );
  const lacking = g.permissions.filter((p) => !p.ok && wanted.includes(p.feature));
  if (!lacking.length)
    return (
      <p className="small muted guard-ok">
        <CircleCheck className="inline-icon" aria-hidden /> {t.allGranted}
      </p>
    );
  return (
    <div className="guard-permissions">
      <Callout tone="warning">
        <b>{t.missing}.</b> {t.permissionSteps}
      </Callout>
      <ul className="guard-permission-list">
        {g.permissions
          .filter((p) => wanted.includes(p.feature))
          .map((p) => (
            <li key={p.key} data-ok={p.ok || undefined}>
              {p.ok ? (
                <CircleCheck className="guard-icon ok" aria-hidden />
              ) : (
                <CircleAlert className="guard-icon missing" aria-hidden />
              )}
              <div>
                <div className="guard-permission-name">
                  <span>{t.permissionNames[p.key] ?? p.key}</span>
                  <Tag tone={p.ok ? "positive" : "caution"}>{p.ok ? t.granted(p.granted ?? "") : t.needs(p.level)}</Tag>
                </div>
                <p className="xsmall muted">{t.why[p.key] ?? p.why}</p>
              </div>
            </li>
          ))}
      </ul>
      <div className="row-wrap">
        {g.app_permissions_url && (
          <Button size="sm" asChild>
            <a href={g.app_permissions_url} target="_blank" rel="noreferrer">
              {t.appPermissions} <ArrowUpRight className="flip-rtl" />
            </a>
          </Button>
        )}
        {g.installation_url && (
          <Button size="sm" asChild>
            <a href={g.installation_url} target="_blank" rel="noreferrer">
              {t.acceptPermissions} <ArrowUpRight className="flip-rtl" />
            </a>
          </Button>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- CI checks

const FILE_TONE = { add: "positive", update: "accent", remove: "negative", kept: "caution" } as const;

function FileRow({ file }: { file: GuardFile }) {
  const t = useT(words);
  const [open, setOpen] = useState(false);
  return (
    <li className="guard-file">
      <div className="guard-file-head">
        <code dir="ltr">{file.path}</code>
        <Tag tone={FILE_TONE[file.action as keyof typeof FILE_TONE]}>{t.fileActions[file.action]}</Tag>
        <span className="spacer" />
        {file.content && (
          <button type="button" className="bh-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
            <ChevronRight className="bh-chevron flip-rtl" /> {open ? t.hideContent : t.showContent}
          </button>
        )}
      </div>
      {file.reason && <p className="xsmall muted">{file.reason}</p>}
      {open && file.content && (
        <pre className="guard-code" dir="ltr">
          {file.content}
        </pre>
      )}
    </li>
  );
}

function ChecksSection({ project, guard }: { project: ProjectDetail; guard: Guard }) {
  const t = useT(words);
  const toast = useToast();
  const client = useQueryClient();
  const [checks, setChecks] = useState<GuardChecks>(guard.checks);
  const [preview, setPreview] = useState<ChecksPreview | null>(null);
  useEffect(() => setChecks(guard.checks), [guard.checks]);
  const editable = guard.may_change;
  const refresh = () => client.invalidateQueries({ queryKey: guardKey(project.id) });
  const look = useCall((body: GuardChecks) =>
    api.post<ChecksPreview>(`${path(project.id)}/checks/preview`, { checks: body }),
  );
  const save = useCall(
    (body: GuardChecks) => api.put<ChecksPreview>(`${path(project.id)}/checks`, { checks: body }),
    [guardKey(project.id)],
  );
  const reread = useCall(() => api.post(`${path(project.id)}/pull/refresh`), [guardKey(project.id)]);
  const set = (values: Partial<GuardChecks>) => setChecks({ ...checks, ...values });
  const changed = JSON.stringify(checks) !== JSON.stringify(guard.checks);
  const removing = guard.checks.enabled && !checks.enabled;
  const series = [...new Set([...guard.versions.default, ...Object.values(guard.versions.branches).flat()])]
    .sort()
    .join(", ");
  const submit = () =>
    save.mutate(checks, {
      onSuccess: (answer) => {
        const title = answer.result === "opened" ? t.opened : answer.result === "updated" ? t.updated : t.upToDate;
        toast("success", title, answer.result === "up_to_date" ? t.noChanges : answer.pull?.url);
        void refresh();
      },
      onError: (e) => toast("error", t.notSaved, errorText(e)),
    });
  const pull = guard.pull;
  return (
    <div className="guard-section">
      <h3>{t.checks}</h3>
      <p className="small muted">{t.checksText}</p>
      <Switch checked={checks.enabled} disabled={!editable} onChange={(v) => set({ enabled: v })} label={t.checksOn} />
      {checks.enabled && (
        <div className="guard-options">
          <div className="row-wrap">
            <Switch checked={checks.lint} disabled={!editable} onChange={(v) => set({ lint: v })} label={t.lint} />
            {checks.lint && (
              <SelectBox
                className="guard-linter"
                style={{ inlineSize: "12rem", maxInlineSize: "100%" }}
                aria-label={t.linter}
                value={checks.linter}
                disabled={!editable}
                onValueChange={(v) => set({ linter: v as GuardChecks["linter"] })}
              >
                <option value="ruff">ruff</option>
                <option value="flake8">flake8</option>
              </SelectBox>
            )}
          </div>
          <Switch
            checked={checks.pylint_odoo}
            disabled={!editable}
            onChange={(v) => set({ pylint_odoo: v })}
            label={t.pylintOdoo}
          />
          <Switch
            checked={checks.manifest}
            disabled={!editable}
            onChange={(v) => set({ manifest: v })}
            label={t.manifest}
          />
          <Switch
            checked={checks.xml_csv}
            disabled={!editable}
            onChange={(v) => set({ xml_csv: v })}
            label={t.xmlCsv}
          />
          <Field hint={t.testsHint}>
            <Switch checked={checks.tests} disabled={!editable} onChange={(v) => set({ tests: v })} label={t.tests} />
          </Field>
          <Field label={t.strictness} hint={t.strictnessHint[checks.strictness]}>
            {editable ? (
              <Segmented<Strictness>
                value={checks.strictness}
                onChange={(v) => set({ strictness: v })}
                options={(["relaxed", "standard", "strict"] as const).map((v) => ({
                  value: v,
                  label: t.strictnessNames[v],
                }))}
              />
            ) : (
              <span>{t.strictnessNames[checks.strictness]}</span>
            )}
          </Field>
          <p className="xsmall faint" dir="auto">
            {t.series(series)}
          </p>
        </div>
      )}
      <div className="guard-pull">
        <GitPullRequest className="guard-icon" aria-hidden />
        {pull ? (
          <>
            <a href={pull.url} target="_blank" rel="noreferrer">
              {t.pull}{" "}
              <span className="tabular" dir="ltr">
                #{pull.number}
              </span>{" "}
              <ArrowUpRight className="inline-icon flip-rtl" aria-hidden />
            </a>
            <Tag tone={pull.state === "merged" ? "positive" : pull.state === "open" ? "accent" : undefined}>
              {t.pullState[pull.state]}
            </Tag>
            {pull.at && <span className="xsmall faint">{ago(pull.at)}</span>}
            <Button
              size="sm"
              variant="ghost"
              icon={<RefreshCw />}
              loading={reread.isPending}
              onClick={() => reread.mutate(undefined, { onError: (e) => toast("error", t.notSaved, errorText(e)) })}
            >
              {t.refresh}
            </Button>
          </>
        ) : (
          <span className="small muted">{t.noPull}</span>
        )}
      </div>
      {editable && (
        <div className="row-wrap">
          <Button
            icon={<Eye />}
            loading={look.isPending}
            onClick={() =>
              look.mutate(checks, { onSuccess: setPreview, onError: (e) => toast("error", t.notSaved, errorText(e)) })
            }
          >
            {t.preview}
          </Button>
          <Button
            variant="primary"
            icon={<GitPullRequest />}
            disabled={!changed && !(checks.enabled && !pull)}
            loading={save.isPending}
            onClick={submit}
          >
            {removing ? t.saveRemoval : t.saveChecks}
          </Button>
        </div>
      )}
      {preview && (
        <Modal
          title={t.filesTitle}
          wide
          onClose={() => setPreview(null)}
          footer={<Button onClick={() => setPreview(null)}>{t.close}</Button>}
        >
          <div className="stack">
            <Callout tone="info">{t.dryRun}</Callout>
            {preview.note && <Callout tone="warning">{preview.note}</Callout>}
            {preview.changes === 0 && <p className="small muted">{t.noChanges}</p>}
            <ul className="guard-files">
              {preview.files.map((f) => (
                <FileRow key={f.path} file={f} />
              ))}
            </ul>
          </div>
        </Modal>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- results

function ResultsSection({ project, guard }: { project: ProjectDetail; guard: Guard }) {
  const t = useT(words);
  const toast = useToast();
  const save = useCall(
    (value: boolean) => api.patch<Guard>(path(project.id), { block_promotion: value }),
    [guardKey(project.id), ["promotion"]],
  );
  return (
    <div className="guard-section">
      <h3>{t.results}</h3>
      <p className="small muted">{t.resultsText}</p>
      <Field hint={t.blockHint}>
        <Switch
          checked={guard.block_promotion}
          disabled={!guard.may_change || !guard.checks.enabled}
          onChange={(v) =>
            save.mutate(v, {
              onSuccess: () => toast("success", t.saved),
              onError: (e) => toast("error", t.notSaved, errorText(e)),
            })
          }
          label={t.blockPromotion}
        />
      </Field>
    </div>
  );
}

// ---------------------------------------------------------------- branch rules

const RULE_TONE = { create: "positive", update: "accent", delete: "negative", blocked: "caution" } as const;

function TargetDiff({ target }: { target: RuleTarget }) {
  const t = useT(words);
  return (
    <section className="guard-target">
      <div className="guard-target-head">
        <h4>{t.targetNames[target.key] ?? target.name}</h4>
        <Tag tone={RULE_TONE[target.action as keyof typeof RULE_TONE]}>{t.ruleActions[target.action]}</Tag>
      </div>
      <p className="xsmall faint" dir="ltr">
        {target.name}
      </p>
      {target.reason && <p className="small muted">{target.reason}</p>}
      {target.changes.length > 0 && (
        <table className="guard-diff">
          <thead>
            <tr>
              <th scope="col">{t.field}</th>
              <th scope="col">{t.current}</th>
              <th scope="col">{t.next}</th>
            </tr>
          </thead>
          <tbody>
            {target.changes.map((c) => (
              <tr key={c.field}>
                <th scope="row">{c.label}</th>
                <td className="before" dir="auto">
                  {c.before || t.nothing}
                </td>
                <td className="after" dir="auto">
                  {c.after || t.nothing}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function PlanModal({
  project,
  plan,
  turnOff,
  rules,
  onClose,
}: {
  project: ProjectDetail;
  plan: RulesPlan;
  turnOff: boolean;
  rules: GuardRules;
  onClose: () => void;
}) {
  const t = useT(words);
  const toast = useToast();
  const [typed, setTyped] = useState("");
  const apply = useCall(
    () =>
      turnOff
        ? api.del<RulesPlan>(`${path(project.id)}/rules`, { confirm: typed })
        : api.put<RulesPlan>(`${path(project.id)}/rules`, { rules, confirm: typed }),
    [guardKey(project.id)],
  );
  const confirmed =
    !plan.affects_production || typed.trim().toLowerCase() === project.repository.full_name.toLowerCase();
  const method = t.methodNames[plan.method] ?? plan.method;
  return (
    <Modal
      wide
      title={turnOff ? t.planTurnOff : t.planTitle}
      onClose={onClose}
      description={
        <span dir="auto">
          {t.repository(plan.repository.full_name, plan.repository.visibility)} · {t.using(method)}
        </span>
      }
      footer={
        <>
          <Button onClick={onClose}>{t.close}</Button>
          {plan.available && plan.changes > 0 && (
            <Button
              variant={turnOff ? "danger" : "primary"}
              icon={turnOff ? <Power /> : <ShieldCheck />}
              disabled={!confirmed}
              loading={apply.isPending}
              onClick={() =>
                apply.mutate(undefined, {
                  onSuccess: () => {
                    toast("success", turnOff ? t.removedToast : t.appliedToast);
                    onClose();
                  },
                  onError: (e) => toast("error", t.notApplied2, errorText(e)),
                })
              }
            >
              {turnOff ? t.remove : t.apply}
            </Button>
          )}
        </>
      }
    >
      <div className="stack">
        {!plan.available && (
          <Callout tone="danger">
            <b>{t.unavailable}.</b>
          </Callout>
        )}
        {plan.limitations.map((l) => (
          <Callout key={l.reason} tone={l.reason === "plan" ? "danger" : "warning"}>
            {l.message}
          </Callout>
        ))}
        {plan.available && plan.changes === 0 && <p className="small muted">{t.noRuleChanges}</p>}
        {plan.targets.map((target) => (
          <TargetDiff key={target.key} target={target} />
        ))}
        {plan.untouched.length > 0 && (
          <div>
            <h4 className="small">{t.untouched}</h4>
            <ul className="guard-untouched">
              {plan.untouched.map((name) => (
                <li key={name} dir="auto">
                  <CircleMinus className="inline-icon" aria-hidden /> {name}
                </li>
              ))}
            </ul>
          </div>
        )}
        {plan.available && plan.changes > 0 && plan.affects_production && (
          <Field label={t.confirmLabel(project.repository.full_name)}>
            <Input
              dir="ltr"
              autoFocus
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              autoComplete="off"
              spellCheck={false}
            />
          </Field>
        )}
      </div>
    </Modal>
  );
}

function RulesSection({ project, guard }: { project: ProjectDetail; guard: Guard }) {
  const t = useT(words);
  const toast = useToast();
  const [rules, setRules] = useState<GuardRules>(guard.rules);
  const [teams, setTeams] = useState(guard.rules.pushers.teams.join(", "));
  const [users, setUsers] = useState(guard.rules.pushers.users.join(", "));
  const [plan, setPlan] = useState<{ plan: RulesPlan; turnOff: boolean; rules: GuardRules } | null>(null);
  useEffect(() => setRules(guard.rules), [guard.rules]);
  const editable = guard.may_change;
  const chosen = (): GuardRules => ({
    ...rules,
    enabled: true,
    pushers: { ...rules.pushers, teams: list(teams), users: list(users) },
  });
  const look = useCall((body: { rules: GuardRules; turn_off: boolean }) =>
    api.post<RulesPlan>(`${path(project.id)}/rules/preview`, body),
  );
  const open = (turnOff: boolean) => {
    const body = chosen();
    look.mutate(
      { rules: body, turn_off: turnOff },
      {
        onSuccess: (found) => setPlan({ plan: found, turnOff, rules: body }),
        onError: (e) => toast("error", t.notApplied2, errorText(e)),
      },
    );
  };
  const stage = (key: "production" | "staging", values: Partial<GuardRules["production"]>) =>
    setRules({ ...rules, [key]: { ...rules[key], ...values } });
  const approvals = (key: "production" | "staging") => (
    <Field label={t.approvals}>
      <Input
        className="number"
        type="number"
        min={0}
        max={10}
        disabled={!editable}
        value={rules[key].approvals}
        onChange={(e) => {
          const n = Number(e.target.value);
          if (Number.isInteger(n) && n >= 0 && n <= 10) stage(key, { approvals: n });
        }}
      />
    </Field>
  );
  const flags = (key: "production" | "staging") => (
    <>
      <Switch
        checked={rules[key].require_checks}
        disabled={!editable}
        onChange={(v) => stage(key, { require_checks: v })}
        label={t.requireChecks(guard.required_check)}
      />
      <Switch
        checked={rules[key].linear_history}
        disabled={!editable}
        onChange={(v) => stage(key, { linear_history: v })}
        label={t.linearHistory}
      />
      <Switch
        checked={rules[key].signed_commits}
        disabled={!editable}
        onChange={(v) => stage(key, { signed_commits: v })}
        label={t.signedCommits}
      />
    </>
  );
  const applied = guard.applied;
  return (
    <div className="guard-section">
      <h3>{t.rules}</h3>
      <p className="small muted">{t.rulesText}</p>
      <p className="small guard-applied">
        {applied ? (
          <CircleCheck className="inline-icon ok" aria-hidden />
        ) : (
          <CircleDashed className="inline-icon" aria-hidden />
        )}{" "}
        {applied
          ? t.applied(t.methodNames[applied.method] ?? applied.method, applied.by, ago(applied.at))
          : t.notApplied}
      </p>
      <div className="guard-columns">
        <fieldset className="guard-stage" disabled={!editable}>
          <legend>{t.production}</legend>
          {approvals("production")}
          {flags("production")}
        </fieldset>
        <fieldset className="guard-stage" disabled={!editable}>
          <legend>{t.staging}</legend>
          <Switch
            checked={rules.staging.enabled}
            disabled={!editable}
            onChange={(v) => setRules({ ...rules, staging: { ...rules.staging, enabled: v } })}
            label={t.stagingOn}
          />
          {rules.staging.enabled && (
            <>
              {approvals("staging")}
              {flags("staging")}
            </>
          )}
        </fieldset>
      </div>
      <p className="xsmall faint">{t.alwaysBlocked}</p>
      <Field label={t.pushers} hint={t.pushersHint[rules.pushers.mode]}>
        <SelectBox
          value={rules.pushers.mode}
          disabled={!editable}
          onValueChange={(v) =>
            setRules({ ...rules, pushers: { ...rules.pushers, mode: v as GuardRules["pushers"]["mode"] } })
          }
        >
          {(["roles", "named", "anyone"] as const).map((m) => (
            <option key={m} value={m}>
              {t.pushersNames[m]}
            </option>
          ))}
        </SelectBox>
      </Field>
      {rules.pushers.mode === "named" && (
        <div className="form-grid">
          <Field label={t.teams} hint={t.teamsHint}>
            <Input dir="ltr" value={teams} disabled={!editable} onChange={(e) => setTeams(e.target.value)} />
          </Field>
          <Field label={t.users} hint={t.usersHint}>
            <Input dir="ltr" value={users} disabled={!editable} onChange={(e) => setUsers(e.target.value)} />
          </Field>
        </div>
      )}
      <Field label={t.method} hint={t.methodHint}>
        <SelectBox
          value={rules.method}
          disabled={!editable}
          onValueChange={(v) => setRules({ ...rules, method: v as GuardRules["method"] })}
        >
          {(["auto", "rulesets", "branch_protection"] as const).map((m) => (
            <option key={m} value={m}>
              {t.methodNames[m]}
            </option>
          ))}
        </SelectBox>
      </Field>
      {editable && (
        <div className="row-wrap">
          <Button variant="primary" icon={<Eye />} loading={look.isPending && !plan} onClick={() => open(false)}>
            {t.previewRules}
          </Button>
          {applied && (
            <Button variant="danger" icon={<Power />} onClick={() => open(true)}>
              {t.turnOff}
            </Button>
          )}
        </div>
      )}
      {plan && (
        <PlanModal
          project={project}
          plan={plan.plan}
          turnOff={plan.turnOff}
          rules={plan.rules}
          onClose={() => setPlan(null)}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------- the card

export function RepoGuard({ project }: { project: ProjectDetail }) {
  const t = useT(words);
  const query = useGuard(project.id);
  const guard = query.data;
  let body: ReactNode;
  if (query.isPending) body = <Skeleton height={120} />;
  else if (query.error || !guard) body = <Callout tone="danger">{errorText(query.error)}</Callout>;
  else
    body = (
      <>
        {!guard.may_change && <Callout tone="info">{t.readOnly}</Callout>}
        <div className="guard-section">
          <h3>{t.permissions}</h3>
          <p className="small muted">{t.permissionsText}</p>
          <Permissions
            guard={guard}
            wanted={[
              ...(guard.checks.enabled ? (["checks", "results"] as const) : []),
              ...(guard.rules.enabled ? (["rules"] as const) : []),
              ...(!guard.checks.enabled && !guard.rules.enabled ? (["checks", "results", "rules"] as const) : []),
            ]}
          />
        </div>
        <ChecksSection project={project} guard={guard} />
        <ResultsSection project={project} guard={guard} />
        <RulesSection project={project} guard={guard} />
      </>
    );
  return (
    <section className="card repo-guard" aria-busy={query.isPending || undefined}>
      <div className="card-head">
        <h2 className="spacer">{t.title}</h2>
        {guard && (guard.checks.enabled || guard.applied) && (
          <Tag tone="positive" icon={<ShieldCheck />}>
            {guard.applied ? t.rules : t.checks}
          </Tag>
        )}
      </div>
      <div className="card-body stack">
        <p className="small muted">{t.text}</p>
        <p className="xsmall faint">{t.defaultsHint}</p>
        {body}
      </div>
    </section>
  );
}
