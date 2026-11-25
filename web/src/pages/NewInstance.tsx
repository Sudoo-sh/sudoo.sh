import { useQuery } from "@tanstack/react-query";
import {
  Archive,
  ArrowUpRight,
  Blocks,
  Box,
  Boxes,
  Building2,
  CircleCheck,
  CircleX,
  Container,
  Cpu,
  FolderGit2,
  GitBranch,
  Layers,
  Lock,
  Package,
  SlidersHorizontal,
} from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router";
import { api, errorText } from "@/api/client";
import { keys, useCall, useCapacity, usePlan, useQuota, useSettings, useVersions } from "@/api/queries";
import type { Engine, GitHubRepository, Job, RepoAccess, Stage } from "@/api/types";
import { BackupPicker } from "@/components/BackupPicker";
import { ModulePicker, useModuleChoice } from "@/components/ModulePicker";
import { Page, PageHead, useCan } from "@/components/Shell";
import { STAGE_ICON, STAGES } from "@/components/StageTag";
import { Tag, Button, Callout, Field, Switch } from "@/components/app/basics";
import { Input } from "@/components/ui/input";
import { SelectBox } from "@/components/app/select";
import { useToast } from "@/components/app/overlay";
import { useT } from "@/i18n";
import words from "@/i18n/new-instance";
import moduleWords from "@/i18n/modules";
import engineWords from "@/i18n/kubernetes";
import { PremiumSlot } from "@/premium/components";
import { ago, number } from "@/lib/format";
import { NEW_INSTANCE_TOUR } from "@/lib/tour";

export function useDebounced<T>(value: T, delay = 450): T {
  const [shown, setShown] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setShown(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return shown;
}

const LIFETIMES = [7, 14, 30, 60, 90];
const ADDRESS = /^(https:\/\/|git@)/;

/** Repositories the GitHub App can read, searched by what is typed in the repository field. */
function useRepositoryPicker(typed: string) {
  const search = useDebounced(ADDRESS.test(typed) ? "" : typed, 250);
  const { data } = useQuery({
    queryKey: ["github-repositories", search],
    queryFn: () => api.page<GitHubRepository[]>(`/github/repositories?q=${encodeURIComponent(search)}`),
    staleTime: 60_000,
    retry: false,
  });
  const installations = (data?.meta?.installations ?? []) as { account: string; settings_url: string }[];
  return { connected: data?.meta?.connected === true, repositories: data?.data ?? [], installations };
}

/** Where to choose on GitHub which repositories the app may read: one link per account it is installed on. */
function RepositoryAccessLinks({ installations }: { installations: { account: string; settings_url: string }[] }) {
  const t = useT(words).picker;
  if (!installations.length) return null;
  return (
    <p className="xsmall faint row-wrap" style={{ gap: 8 }}>
      {t.notListed}
      {installations.map((i) => (
        <a
          key={i.account}
          href={i.settings_url}
          target="_blank"
          rel="noreferrer"
          onMouseDown={(e) => e.preventDefault()}
          className="row"
          style={{ gap: 2 }}
        >
          <bdi>{i.account}</bdi> <ArrowUpRight size={12} className="flip-rtl" />
        </a>
      ))}
    </p>
  );
}

export function RepositoryPicker({ typed, onPick }: { typed: string; onPick: (repository: GitHubRepository) => void }) {
  const { connected, repositories, installations } = useRepositoryPicker(typed);
  const t = useT(words).picker;
  if (!connected || ADDRESS.test(typed)) return null;
  return (
    <>
      {repositories.length ? (
        <ul className="repo-picker" role="listbox" aria-label={t.label}>
          {repositories.slice(0, 8).map((r) => (
            <li key={r.full_name} role="option" aria-selected={false}>
              <button type="button" onMouseDown={(e) => e.preventDefault()} onClick={() => onPick(r)}>
                <FolderGit2 />
                <bdi className="name truncate">{r.full_name}</bdi>
                {r.private && <Lock size={12} aria-label={t.private} />}
                {r.description && <span className="desc truncate">{r.description}</span>}
              </button>
            </li>
          ))}
        </ul>
      ) : typed ? (
        <p className="xsmall faint">{t.noMatch(typed)}</p>
      ) : null}
      <RepositoryAccessLinks installations={installations} />
    </>
  );
}

export function NewInstancePage() {
  const navigate = useNavigate();
  const toast = useToast();
  const can = useCan();
  const mayUseBackups = can("instances.restore") && can("backups.manage");
  const { data: versions } = useVersions();
  const { data: quota } = useQuota();
  const capacity = useCapacity();
  const { data: settings } = useSettings();
  const setting = (key: string) => settings?.find((s) => s.key === key)?.value;

  const [source, setSource] = useState<"repository" | "plain">("repository");
  const plain = source === "plain";
  const [repo, setRepo] = useState("");
  const [branch, setBranch] = useState("");
  const [name, setName] = useState("");
  // the name field shows the suggested name (GET /instance-plan without a name) until someone types in it
  const [nameEdited, setNameEdited] = useState(false);
  const [version, setVersion] = useState("");
  const [edition, setEdition] = useState<"community" | "enterprise">("community");
  const [installModules, setInstallModules] = useState(true);
  const [demo, setDemo] = useState(false);
  const [autoUpdate, setAutoUpdate] = useState(true);
  const [autoPause, setAutoPause] = useState(true);
  const [lifetime, setLifetime] = useState<number | null>(null);
  const [fromBackup, setFromBackup] = useState<string | null>(null);
  const [useBackup, setUseBackup] = useState(false);
  const [stage, setStage] = useState<Stage>("development");
  const [server, setServer] = useState(""); // "": the project's default server, else the main one (several servers)
  const [engine, setEngine] = useState<Engine>("docker"); // the Kubernetes engine is offered while its beta is on
  const e = useT(engineWords);
  const production = stage === "production";
  const t = useT(words);

  const [branches, setBranches] = useState<string[] | null>(null);
  const [access, setAccess] = useState<RepoAccess | null>(null);
  const [repoError, setRepoError] = useState("");
  const [checking, setChecking] = useState(false);
  const [picking, setPicking] = useState(false);
  const debouncedRepo = useDebounced(repo.trim());

  useEffect(() => {
    if (settings) {
      setAutoUpdate(setting("auto_update") !== false);
      setAutoPause(setting("auto_pause") !== false);
      setLifetime((current) => current ?? Number(setting("delete_after_days") ?? 30));
    }
  }, [settings]);
  useEffect(() => {
    if (versions?.length && !version) setVersion(versions[versions.length - 1]!.version);
  }, [versions, version]);

  useEffect(() => {
    setBranches(null);
    setAccess(null);
    setRepoError("");
    if (!/^(https:\/\/github\.com\/|git@github\.com:)[\w.-]+\/[\w.-]+/.test(debouncedRepo)) return;
    let current = true;
    setChecking(true);
    api
      .get<{ branches: string[]; default: string }>(`/repositories/branches?url=${encodeURIComponent(debouncedRepo)}`)
      .then((found) => {
        if (!current) return;
        setBranches(found.branches);
        setBranch((b) => (b && found.branches.includes(b) ? b : found.default));
      })
      .catch((error) => {
        if (!current) return;
        setRepoError(errorText(error));
        // the report says what applies (the GitHub App not installed there, the repository not selected...) and links to the fix
        api
          .get<RepoAccess>(`/repositories/access?url=${encodeURIComponent(debouncedRepo)}`)
          .then((report) => current && setAccess(report))
          .catch(() => undefined);
      })
      .finally(() => current && setChecking(false));
    return () => {
      current = false;
    };
  }, [debouncedRepo]);

  useEffect(() => {
    if (!branches || !branch) return;
    let current = true;
    api
      .get<RepoAccess>(
        `/repositories/access?url=${encodeURIComponent(debouncedRepo)}&branch=${encodeURIComponent(branch)}`,
      )
      .then((report) => current && setAccess(report))
      .catch(() => undefined);
    return () => {
      current = false;
    };
  }, [branches, branch, debouncedRepo]);

  const typedName = useDebounced(nameEdited ? name.trim() : "");
  const { data: plan } = usePlan(
    plain ? "" : debouncedRepo,
    plain ? "" : branch,
    typedName,
    plain ? version : "",
    plain ? edition : "",
  );
  const { data: suggestion } = usePlan(
    plain ? "" : debouncedRepo,
    plain ? "" : branch,
    "",
    plain ? version : "",
    plain ? edition : "",
  );
  const suggested = plain || (debouncedRepo && branch) ? (suggestion?.name ?? "") : "";
  const shownName = nameEdited ? name : suggested;
  // the suggestion shown: where it comes from; a name typed (or the field emptied): what the instance will be called
  const nameHint = (suggestedText: string) =>
    nameEdited && plan ? (
      <>
        {t.willBe(
          <b key="n" dir="ltr">
            {plan.name}
          </b>,
          <code key="d">{plan.database}</code>,
        )}
      </>
    ) : (
      suggestedText
    );
  const typeName = (value: string) => {
    setNameEdited(true);
    setName(value);
  };
  // a stage brings its own defaults (production: never paused, kept, new code on demand); each can still be changed
  const chooseStage = (next: Stage) => {
    setStage(next);
    const defaults = plan?.stage_defaults?.[next];
    if (!defaults) {
      if (next === "production") {
        setAutoUpdate(false);
        setAutoPause(false);
      }
      return;
    }
    setAutoUpdate(defaults.auto_update);
    setAutoPause(defaults.auto_pause);
    setLifetime(defaults.delete_after_days);
  };
  // Odoo apps and other modules installed with the repository's (the switch under Options)
  const moduleChoice = useModuleChoice({ version, edition });
  const m = useT(moduleWords);
  const productionRefused = production ? (plan?.production?.refusal ?? null) : null;
  const enterpriseAvailable = versions?.find((v) => v.version === version)?.enterprise ?? false;
  useEffect(() => {
    if (!enterpriseAvailable) setEdition("community");
  }, [enterpriseAvailable]);
  useEffect(() => setFromBackup(null), [version, edition]); // the picker only shows backups that fit both
  const kubernetesOffered = plan?.engines?.includes("kubernetes") ?? false;
  useEffect(() => {
    if (!kubernetesOffered || edition === "enterprise") setEngine("docker"); // Community only on Kubernetes (beta)
  }, [kubernetesOffered, edition]);

  const create = useCall(
    () =>
      api.post<Job>("/instances", {
        ...(plain ? {} : { repo: repo.trim(), branch: branch || "main" }),
        version,
        edition,
        // plain Odoo without a name: the suggested one shown (odoo-<version>-<edition>-<random>)
        // a repository's instance left with the suggested name: the server names it the same way
        name: (nameEdited ? name.trim() : "") || (plain ? (plan?.name ?? "") : ""),
        install_modules: installModules && !plain,
        ...moduleChoice.request,
        demo: demo && !useBackup,
        stage,
        delete_after_days: production ? 0 : (lifetime ?? 30),
        auto_update: autoUpdate && !plain,
        auto_pause: autoPause && !production,
        ...(useBackup && fromBackup ? { from_backup: fromBackup } : {}),
        ...(server ? { server } : {}),
        ...(engine !== "docker" ? { engine } : {}),
      }),
    [keys.instances, ["quota"]],
  );

  const waiting = quota?.next_creation_at && quota.next_creation_at * 1000 > Date.now();
  const full =
    (quota?.max_instances != null && quota.instances >= quota.max_instances) ||
    (quota?.max_instances_per_user != null && quota.own_instances >= quota.max_instances_per_user);
  const canCreate =
    !!version &&
    (plain ? !!name.trim() || !!plan : !!repo.trim() && !!branches && !repoError) &&
    !waiting &&
    !full &&
    !capacity?.blocked &&
    !productionRefused &&
    !moduleChoice.tooMany &&
    (!useBackup || !!fromBackup);

  const submit = () =>
    create.mutate(undefined, {
      onSuccess: (job) => {
        toast("success", t.creating(job.instance), t.creatingText);
        navigate(`/i/${job.instance}/activity`, { viewTransition: true });
      },
      onError: (error) => toast("error", t.notCreated, errorText(error)),
    });

  return (
    <Page>
      <PageHead title={t.title} tour={NEW_INSTANCE_TOUR}>
        {t.text}
      </PageHead>

      {(waiting || full) && (
        <Callout tone="warning">
          {full ? t.limitReached : t.cooldown(number(quota?.cooldown_minutes), ago(quota!.next_creation_at))}
        </Callout>
      )}

      <div className="new-instance-grid">
        <div className="stack">
          <section className="card" data-tour="new-code">
            <div className="card-head">
              <FolderGit2 />
              <h2>{t.code}</h2>
            </div>
            <div className="card-body form-grid">
              <div className="full grid-2" role="radiogroup" aria-label={t.code}>
                <button
                  type="button"
                  className="choice"
                  role="radio"
                  aria-checked={!plain}
                  aria-pressed={!plain}
                  onClick={() => setSource("repository")}
                >
                  <FolderGit2 />
                  <span>
                    <span className="t">{t.fromRepository}</span>
                    <br />
                    <span className="d">{t.fromRepositoryText}</span>
                  </span>
                </button>
                <button
                  type="button"
                  className="choice"
                  role="radio"
                  aria-checked={plain}
                  aria-pressed={plain}
                  onClick={() => setSource("plain")}
                >
                  <Box />
                  <span>
                    <span className="t">{t.plain}</span>
                    <br />
                    <span className="d">{t.plainText}</span>
                  </span>
                </button>
              </div>
              {plain ? (
                <Field className="full" label={t.name} hint={nameHint(t.suggestedPlain)}>
                  <Input
                    dir="ltr"
                    clearable
                    placeholder="sales-demo"
                    value={shownName}
                    onChange={(e) => typeName(e.target.value)}
                    maxLength={40}
                  />
                </Field>
              ) : (
                <>
                  <Field
                    className="full"
                    label={t.repository}
                    error={repoError}
                    hint={checking ? t.checking : branches ? t.branchesFound(branches.length) : t.repositoryHint}
                  >
                    <div className="stack-sm">
                      <Input
                        dir="ltr"
                        autoFocus
                        placeholder="https://github.com/acme/odoo-addons"
                        value={repo}
                        aria-invalid={!!repoError}
                        onChange={(e) => setRepo(e.target.value)}
                        onFocus={() => setPicking(true)}
                        onBlur={() => setPicking(false)}
                      />
                      {picking && (
                        <RepositoryPicker
                          typed={repo.trim()}
                          onPick={(r) => {
                            setRepo(r.url);
                            setBranch(r.default_branch);
                            setPicking(false);
                          }}
                        />
                      )}
                    </div>
                  </Field>
                  <Field label={t.branch}>
                    {branches ? (
                      <SelectBox dir="ltr" value={branch} onValueChange={(value) => setBranch(value)}>
                        {branches.map((b) => (
                          <option key={b}>{b}</option>
                        ))}
                      </SelectBox>
                    ) : (
                      <div className="input-group">
                        <GitBranch />
                        <Input
                          dir="ltr"
                          placeholder="main"
                          value={branch}
                          onChange={(e) => setBranch(e.target.value)}
                          disabled={!!repo.trim()}
                        />
                      </div>
                    )}
                  </Field>
                  <Field label={t.name} hint={nameHint(t.suggestedRepository)}>
                    <Input
                      dir="ltr"
                      clearable
                      placeholder="sales-demo"
                      value={shownName}
                      onChange={(e) => typeName(e.target.value)}
                      maxLength={40}
                    />
                  </Field>
                  {access && (
                    <div className="full stack-sm">
                      {access.repositories.map((r) => (
                        <div key={r.url} className="row small" style={{ flexWrap: "wrap" }}>
                          {r.ok ? (
                            <CircleCheck size={16} color="var(--positive)" />
                          ) : (
                            <CircleX size={16} color="var(--negative)" />
                          )}
                          <bdi className="truncate" dir="ltr">
                            {r.url}
                          </bdi>
                          <Tag>{r.role}</Tag>
                          {!r.ok && r.problem !== repoError && (
                            <span style={{ color: "var(--negative)" }}>{r.problem}</span>
                          )}
                          {!r.ok &&
                            r.fix?.url &&
                            (r.fix.url.startsWith("/") ? (
                              <Button asChild size="sm">
                                <Link to={r.fix.url} viewTransition>
                                  {r.fix.label}
                                </Link>
                              </Button>
                            ) : (
                              <Button asChild size="sm">
                                <a href={r.fix.url} target="_blank" rel="noreferrer">
                                  {r.fix.label} <ArrowUpRight className="flip-rtl" />
                                </a>
                              </Button>
                            ))}
                        </div>
                      ))}
                      {!access.all_ok && !access.repositories.some((r) => r.fix) && (
                        <Callout tone="warning">
                          {t.cannotRead(
                            <Link key="l" to="/github" viewTransition>
                              {t.githubAccess}
                            </Link>,
                          )}
                        </Callout>
                      )}
                    </div>
                  )}
                </>
              )}
            </div>
          </section>

          <section className="card">
            <div className="card-head">
              <Package />
              <h2>Odoo</h2>
            </div>
            <div className="card-body stack">
              <Field label={t.version}>
                {versions ? (
                  <div className="segmented" style={{ alignSelf: "flex-start", flexWrap: "wrap" }}>
                    {versions.map((v) => (
                      <button
                        key={v.version}
                        type="button"
                        aria-pressed={v.version === version}
                        onClick={() => setVersion(v.version)}
                        title={v.enterprise ? t.communityAndEnterprise : "Community"}
                      >
                        <span className="mono">{v.version}</span>
                      </button>
                    ))}
                  </div>
                ) : (
                  <span className="faint">{t.loadingVersions}</span>
                )}
              </Field>
              <div className="grid-2">
                <button
                  type="button"
                  className="choice"
                  aria-pressed={edition === "community"}
                  onClick={() => setEdition("community")}
                >
                  <Package />
                  <span>
                    <span className="t">Community</span>
                    <br />
                    <span className="d">{t.communityText}</span>
                  </span>
                </button>
                <button
                  type="button"
                  className="choice"
                  aria-pressed={edition === "enterprise"}
                  disabled={!enterpriseAvailable}
                  onClick={() => setEdition("enterprise")}
                >
                  <Building2 />
                  <span>
                    <span className="t">Enterprise</span>
                    <br />
                    <span className="d">
                      {enterpriseAvailable
                        ? t.enterpriseText
                        : version
                          ? t.enterpriseMissing(version)
                          : t.enterpriseMissing(t.thisVersion)}
                    </span>
                  </span>
                </button>
              </div>
            </div>
          </section>

          <section className="card" data-tour="new-stage">
            <div className="card-head">
              <Layers />
              <h2>{t.stage}</h2>
            </div>
            <div className="card-body stack">
              <div className="grid-3" role="radiogroup" aria-label={t.stage}>
                {STAGES.map((st) => {
                  const Icon = STAGE_ICON[st.value];
                  return (
                    <button
                      key={st.value}
                      type="button"
                      className="choice"
                      role="radio"
                      aria-checked={stage === st.value}
                      aria-pressed={stage === st.value}
                      onClick={() => chooseStage(st.value)}
                    >
                      <Icon />
                      <span>
                        <span className="t">{st.label}</span>
                        <br />
                        <span className="d">{st.text}</span>
                      </span>
                    </button>
                  );
                })}
              </div>
              {productionRefused && <Callout tone="warning">{productionRefused}</Callout>}
            </div>
          </section>

          <section className="card">
            <div className="card-head">
              <Blocks />
              <h2>{m.title}</h2>
            </div>
            <div className="card-body">
              <ModulePicker choice={moduleChoice} note={useBackup ? m.fromBackup : undefined} />
            </div>
          </section>

          {kubernetesOffered && (
            <section className="card">
              <div className="card-head">
                <Cpu />
                <div className="spacer">
                  <h2>{e.engine}</h2>
                  <div className="sub">{e.engineText}</div>
                </div>
              </div>
              <div className="card-body">
                <div className="grid-2" role="radiogroup" aria-label={e.engine}>
                  <button
                    type="button"
                    className="choice"
                    role="radio"
                    aria-checked={engine === "docker"}
                    aria-pressed={engine === "docker"}
                    onClick={() => setEngine("docker")}
                  >
                    <Container />
                    <span>
                      <span className="t">{e.docker}</span>
                      <br />
                      <span className="d">{e.dockerText}</span>
                    </span>
                  </button>
                  <button
                    type="button"
                    className="choice"
                    role="radio"
                    aria-checked={engine === "kubernetes"}
                    aria-pressed={engine === "kubernetes"}
                    disabled={edition === "enterprise"}
                    onClick={() => setEngine("kubernetes")}
                  >
                    <Boxes />
                    <span>
                      <span className="t">
                        {e.kubernetes} <Tag tone="accent">{e.beta}</Tag>
                      </span>
                      <br />
                      <span className="d">{e.kubernetesText}</span>
                    </span>
                  </button>
                </div>
              </div>
            </section>
          )}

          {/* several servers (premium): which server the instance goes to */}
          <PremiumSlot
            name="instance.create.server"
            props={{ value: server, onChange: setServer, servers: plan?.servers ?? [], fromBackup: useBackup }}
          />

          {mayUseBackups && (
            <section className="card">
              <div className="card-head">
                <Archive />
                <h2>{t.fromBackup}</h2>
                <span className="sub">{t.optional}</span>
                <span className="spacer" />
                <Switch checked={useBackup} onChange={setUseBackup} label={t.useBackup} />
              </div>
              {useBackup && (
                <div className="card-body stack">
                  <p className="small muted">{t.backupText(version)}</p>
                  <BackupPicker
                    version={version}
                    edition={edition}
                    value={fromBackup}
                    onChange={setFromBackup}
                    onlyCompatible
                  />
                </div>
              )}
            </section>
          )}
        </div>

        <aside className="stack">
          <section className="card">
            <div className="card-head">
              <SlidersHorizontal />
              <h2>{t.options}</h2>
            </div>
            <div className="card-body stack">
              {!plain && (
                <Switch
                  checked={installModules}
                  onChange={setInstallModules}
                  label={useBackup ? t.installMissing : t.install}
                />
              )}
              {!useBackup && <Switch checked={demo} onChange={setDemo} label={t.demo} />}
              {!plain && <Switch checked={autoUpdate} onChange={setAutoUpdate} label={t.autoUpdate} />}
              {production ? (
                <p className="small muted">{t.productionNote}</p>
              ) : (
                <>
                  <Switch checked={autoPause} onChange={setAutoPause} label={t.autoPause} />
                  <Field label={t.lifetime} hint={t.lifetimeHint}>
                    <SelectBox value={lifetime ?? 30} onValueChange={(value) => setLifetime(Number(value))}>
                      {[...new Set([...LIFETIMES, lifetime ?? 30])]
                        .filter((d) => d > 0)
                        .sort((a, b) => a - b)
                        .map((d) => (
                          <option key={d} value={d}>
                            {t.days(d)}
                          </option>
                        ))}
                      <option value={0}>{t.keep}</option>
                    </SelectBox>
                  </Field>
                </>
              )}
            </div>
            <div className="card-foot">
              <Button
                variant="primary"
                size="lg"
                block
                loading={create.isPending}
                disabled={!canCreate}
                onClick={submit}
                data-tour="new-create"
              >
                {t.create}
              </Button>
            </div>
          </section>
          <CapacityNote />
          {quota?.max_running_own ? (
            <p className="xsmall faint" style={{ textAlign: "center" }}>
              {t.running(quota.running_own ?? 0, quota.max_running_own)}
            </p>
          ) : null}
          {quota && (quota.max_instances || quota.max_instances_per_user) && (
            <p className="xsmall faint" style={{ textAlign: "center" }}>
              {quota.max_instances ? t.onServer(quota.instances, quota.max_instances) : ""}
              {quota.max_instances && quota.max_instances_per_user ? " · " : ""}
              {quota.max_instances_per_user ? t.yours(quota.own_instances, quota.max_instances_per_user) : ""}
            </p>
          )}
        </aside>
      </div>
    </Page>
  );
}

/** Before one more instance starts: says when the server already runs as many as it takes comfortably (never
 * blocks, unless MAX_RUNNING_INSTANCES is set and reached). Shown on New instance and before Start. */
export function CapacityNote() {
  const capacity = useCapacity();
  const t = useT(words).capacity;
  if (!capacity) return null;
  if (capacity.blocked) return <Callout tone="danger">{t.blocked(capacity.hard_limit ?? 0, capacity.running)}</Callout>;
  if (!capacity.warning || !capacity.message) return null;
  return (
    <Callout tone="warning">
      {capacity.message}{" "}
      <Link to="/help/docs/ADMIN_GUIDE.md#how-many-instances-can-my-server-run" viewTransition>
        {t.why}
      </Link>
    </Callout>
  );
}
