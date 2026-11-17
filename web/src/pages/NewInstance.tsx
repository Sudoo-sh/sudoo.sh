import { useQuery } from "@tanstack/react-query";
import { Archive, ArrowUpRight, Box, Building2, CircleCheck, CircleX, FolderGit2, GitBranch, Lock, Package, SlidersHorizontal } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router";
import { api, errorText } from "@/api/client";
import { keys, useCall, useCapacity, usePlan, useQuota, useSettings, useVersions } from "@/api/queries";
import type { GitHubRepository, Job, RepoAccess } from "@/api/types";
import { BackupPicker } from "@/components/BackupPicker";
import { Page, PageHead, useCan } from "@/components/Shell";
import { Tag, Button, Callout, Field, Switch } from "@/components/ui/basics";
import { useToast } from "@/components/ui/overlay";
import { ago } from "@/lib/format";

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
  if (!installations.length) return null;
  return (
    <p className="xsmall faint row-wrap" style={{ gap: 8 }}>
      Not listed? Choose which repositories the app can read:
      {installations.map((i) => (
        <a key={i.account} href={i.settings_url} target="_blank" rel="noreferrer" onMouseDown={(e) => e.preventDefault()} className="row" style={{ gap: 2 }}>
          {i.account} <ArrowUpRight size={12} />
        </a>
      ))}
    </p>
  );
}

export function RepositoryPicker({ typed, onPick }: { typed: string; onPick: (repository: GitHubRepository) => void }) {
  const { connected, repositories, installations } = useRepositoryPicker(typed);
  if (!connected || ADDRESS.test(typed)) return null;
  return (
    <>
      {repositories.length ? (
        <ul className="repo-picker" role="listbox" aria-label="Repositories on GitHub">
          {repositories.slice(0, 8).map((r) => (
            <li key={r.full_name} role="option" aria-selected={false}>
              <button type="button" onMouseDown={(e) => e.preventDefault()} onClick={() => onPick(r)}>
                <FolderGit2 />
                <span className="name truncate">{r.full_name}</span>
                {r.private && <Lock size={12} aria-label="private" />}
                {r.description && <span className="desc truncate">{r.description}</span>}
              </button>
            </li>
          ))}
        </ul>
      ) : typed ? (
        <p className="xsmall faint">No repository of the GitHub App matches "{typed}".</p>
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
  const [version, setVersion] = useState("");
  const [edition, setEdition] = useState<"community" | "enterprise">("community");
  const [installModules, setInstallModules] = useState(true);
  const [demo, setDemo] = useState(false);
  const [autoUpdate, setAutoUpdate] = useState(true);
  const [autoPause, setAutoPause] = useState(true);
  const [lifetime, setLifetime] = useState<number | null>(null);
  const [fromBackup, setFromBackup] = useState<string | null>(null);
  const [useBackup, setUseBackup] = useState(false);

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
        api.get<RepoAccess>(`/repositories/access?url=${encodeURIComponent(debouncedRepo)}`).then((report) => current && setAccess(report)).catch(() => undefined);
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
      .get<RepoAccess>(`/repositories/access?url=${encodeURIComponent(debouncedRepo)}&branch=${encodeURIComponent(branch)}`)
      .then((report) => current && setAccess(report))
      .catch(() => undefined);
    return () => {
      current = false;
    };
  }, [branches, branch, debouncedRepo]);

  const { data: plan } = usePlan(plain ? "" : debouncedRepo, plain ? "" : branch, useDebounced(name.trim()), plain ? version : "", plain ? edition : "");
  const enterpriseAvailable = versions?.find((v) => v.version === version)?.enterprise ?? false;
  useEffect(() => {
    if (!enterpriseAvailable) setEdition("community");
  }, [enterpriseAvailable]);
  useEffect(() => setFromBackup(null), [version, edition]); // the picker only shows backups that fit both

  const create = useCall(
    () =>
      api.post<Job>("/instances", {
        ...(plain ? {} : { repo: repo.trim(), branch: branch || "main" }),
        version,
        edition,
        // plain Odoo without a name: the suggested one shown (odoo-<version>-<edition>-<random>)
        name: name.trim() || (plain ? (plan?.name ?? "") : ""),
        install_modules: installModules && !plain,
        demo: demo && !useBackup,
        delete_after_days: lifetime ?? 30,
        auto_update: autoUpdate && !plain,
        auto_pause: autoPause,
        ...(useBackup && fromBackup ? { from_backup: fromBackup } : {}),
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
    (!useBackup || !!fromBackup);

  const submit = () =>
    create.mutate(undefined, {
      onSuccess: (job) => {
        toast("success", `Creating ${job.instance}`, "It takes a few minutes; follow it live.");
        navigate(`/i/${job.instance}/activity`, { viewTransition: true });
      },
      onError: (error) => toast("error", "Not created", errorText(error)),
    });

  return (
    <Page>
      <PageHead title="New instance">
        Pick a branch, or plain Odoo. Sudoo.sh downloads the code, starts Odoo with its own database, installs your modules and gives it a link.
      </PageHead>

      {(waiting || full) && (
        <Callout tone="warning">
          {full
            ? "An instance limit is reached. Delete an instance first."
            : `One instance per ${quota?.cooldown_minutes} minutes: you can create the next one ${ago(quota!.next_creation_at)}.`}
        </Callout>
      )}

      <div className="new-instance-grid">
        <div className="stack">
          <section className="card">
            <div className="card-head">
              <FolderGit2 />
              <h2>Code</h2>
            </div>
            <div className="card-body form-grid">
              <div className="full grid-2" role="radiogroup" aria-label="Code">
                <button type="button" className="choice" role="radio" aria-checked={!plain} aria-pressed={!plain} onClick={() => setSource("repository")}>
                  <FolderGit2 />
                  <span><span className="t">From a repository</span><br /><span className="d">A branch of your GitHub repository, with its modules.</span></span>
                </button>
                <button type="button" className="choice" role="radio" aria-checked={plain} aria-pressed={plain} onClick={() => setSource("plain")}>
                  <Box />
                  <span><span className="t">Plain Odoo (no repository)</span><br /><span className="d">The official Odoo only. No code to update.</span></span>
                </button>
              </div>
              {plain ? (
                <Field className="full" label="Name" hint={plan ? <>Will be <b>{plan.name}</b> · database <code>{plan.database}</code></> : "Empty: Odoo, version, edition"}>
                  <input className="input" placeholder={plan?.name ?? "sales-demo"} value={name} onChange={(e) => setName(e.target.value)} maxLength={40} />
                </Field>
              ) : (
              <>
              <Field
                className="full"
                label="GitHub repository"
                error={repoError}
                hint={checking ? "Checking the repository…" : branches ? `${branches.length} branches found` : "Search your repositories, or paste https://github.com/owner/repository"}
              >
                <div className="stack-sm">
                  <input className="input" autoFocus placeholder="https://github.com/acme/odoo-addons" value={repo} aria-invalid={!!repoError}
                    onChange={(e) => setRepo(e.target.value)} onFocus={() => setPicking(true)} onBlur={() => setPicking(false)} />
                  {picking && (
                    <RepositoryPicker typed={repo.trim()} onPick={(r) => { setRepo(r.url); setBranch(r.default_branch); setPicking(false); }} />
                  )}
                </div>
              </Field>
              <Field label="Branch">
                {branches ? (
                  <select className="select" value={branch} onChange={(e) => setBranch(e.target.value)}>
                    {branches.map((b) => <option key={b}>{b}</option>)}
                  </select>
                ) : (
                  <div className="input-group">
                    <GitBranch />
                    <input className="input" placeholder="main" value={branch} onChange={(e) => setBranch(e.target.value)} disabled={!!repo.trim()} />
                  </div>
                )}
              </Field>
              <Field label="Name" hint={plan ? <>Will be <b>{plan.name}</b> · database <code>{plan.database}</code></> : "Empty: repository and branch"}>
                <input className="input" placeholder={plan?.name ?? "sales-demo"} value={name} onChange={(e) => setName(e.target.value)} maxLength={40} />
              </Field>
              {access && (
                <div className="full stack-sm">
                  {access.repositories.map((r) => (
                    <div key={r.url} className="row small" style={{ flexWrap: "wrap" }}>
                      {r.ok ? <CircleCheck size={16} color="var(--positive)" /> : <CircleX size={16} color="var(--negative)" />}
                      <span className="truncate">{r.url}</span>
                      <Tag>{r.role}</Tag>
                      {!r.ok && r.problem !== repoError && <span style={{ color: "var(--negative)" }}>{r.problem}</span>}
                      {!r.ok && r.fix?.url && (r.fix.url.startsWith("/") ? (
                        <Link className="btn sm" to={r.fix.url} viewTransition>{r.fix.label}</Link>
                      ) : (
                        <a className="btn sm" href={r.fix.url} target="_blank" rel="noreferrer">{r.fix.label} <ArrowUpRight /></a>
                      ))}
                    </div>
                  ))}
                  {!access.all_ok && !access.repositories.some((r) => r.fix) && (
                    <Callout tone="warning">
                      The server cannot read every repository. For private ones, connect the GitHub App (or add a deploy key) under <Link to="/github" viewTransition>GitHub access</Link>.
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
              <Field label="Version">
                {versions ? (
                  <div className="segmented" style={{ alignSelf: "flex-start", flexWrap: "wrap" }}>
                    {versions.map((v) => (
                      <button key={v.version} type="button" aria-pressed={v.version === version} onClick={() => setVersion(v.version)}
                        title={v.enterprise ? "Community and Enterprise" : "Community"}>
                        <span className="mono">{v.version}</span>
                      </button>
                    ))}
                  </div>
                ) : (
                  <span className="faint">Loading versions</span>
                )}
              </Field>
              <div className="grid-2">
                <button type="button" className="choice" aria-pressed={edition === "community"} onClick={() => setEdition("community")}>
                  <Package />
                  <span><span className="t">Community</span><br /><span className="d">Odoo's free edition.</span></span>
                </button>
                <button type="button" className="choice" aria-pressed={edition === "enterprise"} disabled={!enterpriseAvailable} onClick={() => setEdition("enterprise")}>
                  <Building2 />
                  <span><span className="t">Enterprise</span><br /><span className="d">{enterpriseAvailable ? "With the Enterprise apps and interface." : `Not available for ${version || "this version"} on this server.`}</span></span>
                </button>
              </div>
            </div>
          </section>

          {mayUseBackups && (
            <section className="card">
              <div className="card-head">
                <Archive />
                <h2>Start from a backup</h2>
                <span className="sub">optional</span>
                <span className="spacer" />
                <Switch checked={useBackup} onChange={setUseBackup} label="Use a backup" />
              </div>
              {useBackup && (
                <div className="card-body stack">
                  <p className="small muted">
                    The new instance starts with this backup's data instead of an empty database. Only backups of Odoo {version} fit. A backup of another instance is neutralised: no e-mail goes out and no scheduled actions run until you switch them back on in Odoo.
                  </p>
                  <BackupPicker version={version} edition={edition} value={fromBackup} onChange={setFromBackup} onlyCompatible />
                </div>
              )}
            </section>
          )}
        </div>

        <aside className="stack">
          <section className="card">
            <div className="card-head"><SlidersHorizontal /><h2>Options</h2></div>
            <div className="card-body stack">
              {!plain && <Switch checked={installModules} onChange={setInstallModules} label={useBackup ? "Install the repository's modules the backup lacks" : "Install the repository's modules"} />}
              {!useBackup && <Switch checked={demo} onChange={setDemo} label="Load demo data" />}
              {!plain && <Switch checked={autoUpdate} onChange={setAutoUpdate} label="Get new code automatically" />}
              <Switch checked={autoPause} onChange={setAutoPause} label="Pause when nobody uses it" />
              <Field label="Lifetime" hint="Deleted after this, with a backup first.">
                <select className="select" value={lifetime ?? 30} onChange={(e) => setLifetime(Number(e.target.value))}>
                  {[...new Set([...LIFETIMES, lifetime ?? 30])].filter((d) => d > 0).sort((a, b) => a - b).map((d) => <option key={d} value={d}>{d} days</option>)}
                  <option value={0}>Keep until deleted by hand</option>
                </select>
              </Field>
            </div>
            <div className="card-foot">
              <Button variant="primary" size="lg" block loading={create.isPending} disabled={!canCreate} onClick={submit}>
                Create instance
              </Button>
            </div>
          </section>
          <CapacityNote />
          {quota && (quota.max_instances || quota.max_instances_per_user) && (
            <p className="xsmall faint" style={{ textAlign: "center" }}>
              {quota.max_instances ? `${quota.instances} of ${quota.max_instances} on this server` : ""}
              {quota.max_instances && quota.max_instances_per_user ? " · " : ""}
              {quota.max_instances_per_user ? `${quota.own_instances} of ${quota.max_instances_per_user} yours` : ""}
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
  if (!capacity) return null;
  if (capacity.blocked)
    return (
      <Callout tone="danger">
        This server runs at most {capacity.hard_limit} instances at once, and {capacity.running} are running or
        starting. Pause one first.
      </Callout>
    );
  if (!capacity.warning || !capacity.message) return null;
  return (
    <Callout tone="warning">
      {capacity.message}{" "}
      <Link to="/help/docs/ADMIN_GUIDE.md#how-many-instances-can-my-server-run" viewTransition>
        Why
      </Link>
    </Callout>
  );
}
