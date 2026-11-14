import { useQuery } from "@tanstack/react-query";
import { Archive, Building2, CircleCheck, CircleX, FolderGit2, GitBranch, Lock, Package, SlidersHorizontal } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router";
import { api, errorText } from "@/api/client";
import { keys, useCall, usePlan, useQuota, useSettings, useVersions } from "@/api/queries";
import type { GitHubRepository, Job, RepoAccess } from "@/api/types";
import { BackupPicker } from "@/components/BackupPicker";
import { Page, PageHead, useCan } from "@/components/Shell";
import { Tag, Button, Callout, Field, Switch } from "@/components/ui/basics";
import { useToast } from "@/components/ui/overlay";
import { ago } from "@/lib/format";

function useDebounced<T>(value: T, delay = 450): T {
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
  return { connected: data?.meta?.connected === true, repositories: data?.data ?? [] };
}

function RepositoryPicker({ typed, onPick }: { typed: string; onPick: (repository: GitHubRepository) => void }) {
  const { connected, repositories } = useRepositoryPicker(typed);
  if (!connected || ADDRESS.test(typed)) return null;
  if (!repositories.length) return typed ? <p className="xsmall faint">No repository of the GitHub App matches "{typed}".</p> : null;
  return (
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
  );
}

export function NewInstancePage() {
  const navigate = useNavigate();
  const toast = useToast();
  const can = useCan();
  const mayUseBackups = can("instances.restore") && can("backups.manage");
  const { data: versions } = useVersions();
  const { data: quota } = useQuota();
  const { data: settings } = useSettings();
  const setting = (key: string) => settings?.find((s) => s.key === key)?.value;

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
      .catch((error) => current && setRepoError(errorText(error)))
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

  const { data: plan } = usePlan(debouncedRepo, branch, useDebounced(name.trim()));
  const enterpriseAvailable = versions?.find((v) => v.version === version)?.enterprise ?? false;
  useEffect(() => {
    if (!enterpriseAvailable) setEdition("community");
  }, [enterpriseAvailable]);
  useEffect(() => setFromBackup(null), [version, edition]); // the picker only shows backups that fit both

  const create = useCall(
    () =>
      api.post<Job>("/instances", {
        repo: repo.trim(),
        branch: branch || "main",
        version,
        edition,
        name: name.trim(),
        install_modules: installModules,
        demo: demo && !useBackup,
        delete_after_days: lifetime ?? 30,
        auto_update: autoUpdate,
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
    !!version && (!!repo.trim() ? !!branches && !repoError : !!name.trim()) && !waiting && !full && (!useBackup || !!fromBackup);

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
        Pick a branch. Sudoo.sh downloads it, starts Odoo with its own database, installs your modules and gives it a link.
      </PageHead>

      {(waiting || full) && (
        <Callout tone="warning">
          {full
            ? "An instance limit is reached. Delete an instance first."
            : `One instance per ${quota?.cooldown_minutes} minutes: you can create the next one ${ago(quota!.next_creation_at)}.`}
        </Callout>
      )}

      <div className="grid-3" style={{ gridTemplateColumns: "minmax(0, 2fr) minmax(280px, 1fr)", alignItems: "start" }}>
        <div className="stack">
          <section className="card">
            <div className="card-head">
              <FolderGit2 />
              <h2>Code</h2>
              <span className="sub">optional: leave empty for an Odoo without custom modules</span>
            </div>
            <div className="card-body form-grid">
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
                    <div key={r.url} className="row small">
                      {r.ok ? <CircleCheck size={16} color="var(--positive)" /> : <CircleX size={16} color="var(--negative)" />}
                      <span className="truncate">{r.url}</span>
                      <Tag>{r.role}</Tag>
                      {!r.ok && <span style={{ color: "var(--negative)" }}>{r.problem}</span>}
                    </div>
                  ))}
                  {!access.all_ok && (
                    <Callout tone="warning">
                      The server cannot read every repository. For private ones, connect the GitHub App (or add a deploy key) under <Link to="/github">GitHub access</Link>.
                    </Callout>
                  )}
                </div>
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

        <aside className="stack" style={{ position: "sticky", top: 84 }}>
          <section className="card">
            <div className="card-head"><SlidersHorizontal /><h2>Options</h2></div>
            <div className="card-body stack">
              <Switch checked={installModules} onChange={setInstallModules} label={useBackup ? "Install the repository's modules the backup lacks" : "Install the repository's modules"} />
              {!useBackup && <Switch checked={demo} onChange={setDemo} label="Load demo data" />}
              <Switch checked={autoUpdate} onChange={setAutoUpdate} label="Get new code automatically" />
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
