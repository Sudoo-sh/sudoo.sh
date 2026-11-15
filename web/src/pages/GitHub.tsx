import { ArrowUpRight, Building2, FolderGit2, KeyRound, Plug, Plus, RefreshCw, Trash2, Unplug, User } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";
import { api, errorText } from "@/api/client";
import { useCall, useDeployKeys, useGitHubApp } from "@/api/queries";
import type { GitHubApp, GitHubInstallation } from "@/api/types";
import { Page, PageHead } from "@/components/Shell";
import { Button, Callout, CopyField, Empty, Field, Skeleton, Spinner, Tag } from "@/components/ui/basics";
import { Modal, useToast } from "@/components/ui/overlay";
import { ago } from "@/lib/format";

const APP_KEY = [["github-app"]] as const;

/** The browser posts the manifest to GitHub as a form (GitHub's App Manifest flow), leaving the panel. */
function postToGitHub(action: string, manifest: unknown) {
  const form = document.createElement("form");
  form.method = "post";
  form.action = action;
  const input = document.createElement("input");
  input.type = "hidden";
  input.name = "manifest";
  input.value = JSON.stringify(manifest);
  form.appendChild(input);
  document.body.appendChild(form);
  form.submit();
}

function ConnectModal({ onClose }: { onClose: () => void }) {
  const toast = useToast();
  const [kind, setKind] = useState<"personal" | "organization">("personal");
  const [organization, setOrganization] = useState("");
  const [leaving, setLeaving] = useState(false);
  const start = async () => {
    setLeaving(true);
    try {
      const made = await api.post<{ action: string; manifest: unknown }>("/github/app/manifest", {
        organization: kind === "organization" ? organization.trim() : "",
      });
      postToGitHub(made.action, made.manifest);
    } catch (error) {
      setLeaving(false);
      toast("error", "Cannot connect", errorText(error));
    }
  };
  return (
    <Modal
      title="Connect GitHub"
      description="GitHub creates a private app for this panel that can only read code. You then choose which repositories it may read."
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" icon={<ArrowUpRight />} loading={leaving} disabled={kind === "organization" && !organization.trim()} onClick={start}>
            Continue on GitHub
          </Button>
        </>
      }
    >
      <div className="stack">
        <div className="grid-2">
          <button type="button" className="choice" aria-pressed={kind === "personal"} onClick={() => setKind("personal")}>
            <User />
            <span><span className="t">My account</span><br /><span className="d">The app belongs to your GitHub account.</span></span>
          </button>
          <button type="button" className="choice" aria-pressed={kind === "organization"} onClick={() => setKind("organization")}>
            <Building2 />
            <span><span className="t">An organisation</span><br /><span className="d">The app belongs to your company's organisation; you must be one of its owners.</span></span>
          </button>
        </div>
        {kind === "organization" && (
          <Field label="Organisation" hint="As in github.com/acme: acme">
            <input className="input" autoFocus value={organization} onChange={(e) => setOrganization(e.target.value)} placeholder="acme" />
          </Field>
        )}
        <p className="small muted">
          On GitHub, check the name and press <b>Create GitHub App</b>. GitHub brings you back here; then install the app on the accounts whose repositories the instances use.
        </p>
      </div>
    </Modal>
  );
}

function Installations({ app, canManage }: { app: GitHubApp; canManage: boolean }) {
  const toast = useToast();
  const sync = useCall(() => api.post<GitHubInstallation[]>("/github/app/installations/sync"), APP_KEY);
  const forget = useCall((id: number) => api.del(`/github/app/installations/${id}`), APP_KEY);
  return (
    <div className="stack-sm">
      <div className="row" style={{ flexWrap: "wrap" }}>
        <h3 className="spacer">Installed on</h3>
        {canManage && (
          <Button size="sm" variant="ghost" icon={<RefreshCw />} loading={sync.isPending}
            onClick={() => sync.mutate(undefined, { onSuccess: () => toast("success", "Read from GitHub"), onError: (e) => toast("error", "Not read", errorText(e)) })}>
            Refresh
          </Button>
        )}
        {canManage && <a className="btn sm primary" href={app.install_url} target="_blank" rel="noreferrer"><Plus /> Install on GitHub</a>}
      </div>
      {!app.installations.length ? (
        <Callout tone="warning">
          Not installed yet: press <b>Install on GitHub</b>, choose your account or organisation and the repositories (all, or selected ones). Installed elsewhere already? Press <b>Refresh</b>.
        </Callout>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Account</th><th>Repositories</th><th>Since</th><th /></tr></thead>
            <tbody>
              {app.installations.map((i) => (
                <tr key={i.id}>
                  <td>{i.account_type === "Organization" ? <Building2 size={14} /> : <User size={14} />} {i.account} {i.suspended && <Tag tone="caution">suspended</Tag>}</td>
                  <td>{i.repository_selection === "all" ? "All" : "Selected"}</td>
                  <td className="small muted">{ago(i.added)}</td>
                  <td className="actions">
                    {i.settings_url && <a className="btn sm ghost" href={i.settings_url} target="_blank" rel="noreferrer">Choose repositories <ArrowUpRight /></a>}
                    {canManage && (
                      <Button size="sm" variant="ghost" icon={<Trash2 />} aria-label="Forget"
                        onClick={() => confirm(`Forget the installation on ${i.account}? Uninstall the app there on GitHub too.`) && forget.mutate(i.id)} />
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function AppCard() {
  const { data: state, isPending } = useGitHubApp();
  const toast = useToast();
  const [connecting, setConnecting] = useState(false);
  const [removed, setRemoved] = useState<{ settings_url: string; installations: { account: string; settings_url: string }[] } | null>(null);
  const disconnect = useCall(() => api.del<{ settings_url: string; installations: { account: string; settings_url: string }[] }>("/github/app"), APP_KEY);
  if (isPending || !state) return <div className="card"><div className="card-body"><Skeleton height={120} /></div></div>;
  const app = state.app;
  return (
    <section className="card rise">
      <div className="card-head">
        <Plug />
        <h2>GitHub App</h2>
        <span className="sub">{app ? "connected" : "read-only access to whole accounts"}</span>
      </div>
      <div className="card-body stack">
        {!app ? (
          <>
            <p className="small muted">
              Connect your GitHub account or your company's organisation once, and the panel reads every repository you allow, submodules included: no key per repository. Access is read-only and can be limited to selected repositories.
            </p>
            {state.can_manage ? (
              <div><Button variant="primary" icon={<Plug />} onClick={() => setConnecting(true)}>Connect GitHub</Button></div>
            ) : (
              <Callout>A super admin connects the GitHub App.</Callout>
            )}
          </>
        ) : (
          <>
            <div className="row" style={{ flexWrap: "wrap" }}>
              <span className="small spacer">
                <b>{app.name}</b> <span className="muted">· owned by {app.owner.login}</span>
              </span>
              <a className="btn sm ghost" href={app.settings_url} target="_blank" rel="noreferrer">App settings <ArrowUpRight /></a>
              {state.can_manage && (
                <Button size="sm" variant="ghost" icon={<Unplug />} loading={disconnect.isPending}
                  onClick={() => confirm("Disconnect the GitHub App? The panel forgets its keys; instances of private repositories cannot get new code until it is connected again.") &&
                    disconnect.mutate(undefined, { onSuccess: setRemoved, onError: (e) => toast("error", "Not disconnected", errorText(e)) })}>
                  Disconnect
                </Button>
              )}
            </div>
            <Installations app={app} canManage={state.can_manage} />
            {!state.trigger_running && (
              <Callout>
                New code arrives through polling. For updates seconds after a push, add <code>github-app</code> to the update triggers (<Link to="/settings">Settings</Link>, Plugins) and restart the panel.
              </Callout>
            )}
          </>
        )}
      </div>
      {connecting && <ConnectModal onClose={() => setConnecting(false)} />}
      {removed && (
        <Modal title="Remove the app on GitHub too" description="The panel no longer holds its keys. To end its access completely, uninstall it and delete it on GitHub." onClose={() => setRemoved(null)}
          footer={<><Button onClick={() => setRemoved(null)}>Done</Button><a className="btn primary" href={removed.settings_url} target="_blank" rel="noreferrer">Delete the app <ArrowUpRight /></a></>}>
          <div className="stack-sm">
            {removed.installations.map((i) => (
              <a key={i.account} className="row small" href={i.settings_url} target="_blank" rel="noreferrer">Uninstall from {i.account} <ArrowUpRight size={14} /></a>
            ))}
            <p className="small muted">On the app's page: Advanced, Delete GitHub App.</p>
          </div>
        </Modal>
      )}
    </section>
  );
}

export function GitHubPage() {
  const { data: keysList, isPending } = useDeployKeys();
  const { data: appState } = useGitHubApp();
  const toast = useToast();
  const [adding, setAdding] = useState(false);
  const [repo, setRepo] = useState("");
  const [made, setMade] = useState<{ repo: string; public_key: string; github_url: string } | null>(null);
  const create = useCall(() => api.post<{ repo: string; public_key: string; github_url: string }>("/deploy-keys", { repo: repo.trim() }), [["deploy-keys"]]);
  const remove = useCall((rid: string) => api.del(`/deploy-keys/${rid}`), [["deploy-keys"]]);
  const appConnected = !!appState?.connected;
  return (
    <Page>
      <PageHead title="GitHub access">
        Public repositories need nothing. For private ones, connect the GitHub App; deploy keys (one per repository) remain for servers without it.
      </PageHead>
      <AppCard />
      <section className="card rise">
        <div className="card-head">
          <KeyRound />
          <h2>Deploy keys</h2>
          <span className="sub">{appConnected ? "fallback" : "one per repository"}</span>
          <span className="spacer" />
          <Button size="sm" icon={<Plus />} onClick={() => setAdding(true)}>Add</Button>
        </div>
        {appConnected && !!keysList?.length && (
          <div className="card-body">
            <Callout>
              Switch to the GitHub App: once it is installed on a repository's owner, delete that repository's deploy key here and on GitHub. A repository with a deploy key keeps using the key; instances go on working either way.
            </Callout>
          </div>
        )}
        {isPending ? <div className="card-body"><Skeleton height={120} /></div> : !keysList?.length ? (
          <Empty icon={<FolderGit2 />} title="No deploy keys">{appConnected ? "Not needed while the GitHub App covers your repositories." : "Add one for every private repository the instances use, or connect the GitHub App."}</Empty>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Repository</th><th>Key</th><th>Created</th><th /></tr></thead>
              <tbody>
                {keysList.map((k) => (
                  <tr key={k.repo}>
                    <td>{k.spelling || k.repo}</td>
                    <td style={{ maxWidth: 320 }}><CopyField value={k.public_key} /></td>
                    <td className="small muted">{ago(k.created)}</td>
                    <td className="actions">
                      <a className="btn sm ghost" href={k.github_url} target="_blank" rel="noreferrer">GitHub <ArrowUpRight /></a>
                      <Button size="sm" variant="ghost" icon={<Trash2 />} aria-label="Delete" onClick={() => confirm(`Delete the key of ${k.repo}? Remove it on GitHub too.`) && remove.mutate(k.repo, { onSuccess: () => toast("success", "Key deleted") })} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      {adding && (
        <Modal title="Add a private repository" onClose={() => setAdding(false)}
          footer={<><Button onClick={() => setAdding(false)}>Cancel</Button><Button variant="primary" icon={<KeyRound />} loading={create.isPending} disabled={!repo.trim()}
            onClick={() => create.mutate(undefined, { onSuccess: (m) => { setAdding(false); setRepo(""); setMade(m); }, onError: (e) => toast("error", "No key created", errorText(e)) })}>Create key</Button></>}>
          <Field label="Repository" hint="https://github.com/owner/repository, or owner/repository">
            <input className="input" autoFocus value={repo} onChange={(e) => setRepo(e.target.value)} />
          </Field>
        </Modal>
      )}
      {made && (
        <Modal title="Add this key on GitHub" description={`In ${made.repo}: Settings, Deploy keys, Add deploy key. Leave "Allow write access" off.`} onClose={() => setMade(null)}
          footer={<><Button onClick={() => setMade(null)}>Done</Button><a className="btn primary" href={made.github_url} target="_blank" rel="noreferrer">Open GitHub <ArrowUpRight /></a></>}>
          <div className="stack">
            <CopyField value={made.public_key} block />
            <Callout>Only the public part leaves the server; the private key is stored encrypted.</Callout>
          </div>
        </Modal>
      )}
    </Page>
  );
}

/** Where GitHub sends the browser back: /github/app/created (?code, state) and /github/app/installed
 * (?installation_id, setup_action). The panel finishes the step and returns to GitHub access. */
export function GitHubReturnPage() {
  const { step } = useParams();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const toast = useToast();
  const [problem, setProblem] = useState("");
  const done = useRef(false);
  const finish = useCall(async () => {
    if (step === "created") {
      await api.post("/github/app", { code: params.get("code") ?? "", state: params.get("state") ?? "" });
      return { title: "GitHub App created", detail: "Now install it on your account or organisation." };
    }
    const id = params.get("installation_id");
    if (!id) {
      return params.get("setup_action") === "request"
        ? { title: "Installation requested", detail: "An owner of the organisation must approve it on GitHub; then press Refresh." }
        : { title: "Nothing to record", detail: "" };
    }
    await api.post("/github/app/installations", { installation_id: id });
    return { title: "Installed", detail: "The panel can now read the repositories you allowed." };
  }, APP_KEY);
  useEffect(() => {
    if (done.current) return;
    done.current = true;
    finish.mutate(undefined, {
      onSuccess: (result) => {
        toast("success", result.title, result.detail);
        navigate("/github", { replace: true });
      },
      onError: (error) => setProblem(errorText(error)),
    });
  }, []);
  return (
    <Page>
      <PageHead title="GitHub access" />
      <div className="card"><div className="card-body stack">
        {problem ? (
          <>
            <Callout tone="danger">{problem}</Callout>
            <div><Link className="btn" to="/github">Back to GitHub access</Link></div>
          </>
        ) : (
          <div className="row"><Spinner /> <span className="muted">Finishing with GitHub…</span></div>
        )}
      </div></div>
    </Page>
  );
}
