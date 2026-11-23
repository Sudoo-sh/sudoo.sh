import { ArrowUpRight, Building2, FolderGit2, KeyRound, Plug, Plus, RefreshCw, Trash2, Unplug, User } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";
import { api, errorText } from "@/api/client";
import { useCall, useDeployKeys, useGitHubApp } from "@/api/queries";
import type { GitHubApp, GitHubInstallation } from "@/api/types";
import { Page, PageHead } from "@/components/Shell";
import { Button, Callout, CopyField, Empty, Field, Skeleton, Spinner, Switch, Tag } from "@/components/app/basics";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Input } from "@/components/ui/input";
import { Modal, useToast } from "@/components/app/overlay";
import { ago } from "@/lib/format";
import { useT } from "@/i18n";
import common from "@/i18n/common";
import words from "@/i18n/github";

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
  const [kind, setKind] = useState<"personal" | "organization" | null>(null);
  const [organization, setOrganization] = useState("");
  const [createRepositories, setCreateRepositories] = useState(false);
  const [repositoryGuard, setRepositoryGuard] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const t = useT(words).connect;
  const c = useT(common).common;
  const start = async () => {
    setLeaving(true);
    try {
      const made = await api.post<{ action: string; manifest: unknown }>("/github/app/manifest", {
        organization: kind === "organization" ? organization.trim() : "",
        create_repositories: kind === "organization" && createRepositories,
        repository_guard: repositoryGuard,
      });
      postToGitHub(made.action, made.manifest);
    } catch (error) {
      setLeaving(false);
      toast("error", t.cannot, errorText(error));
    }
  };
  return (
    <Modal
      title={t.title}
      description={t.text}
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>{c.cancel}</Button>
          <Button variant="primary" icon={<ArrowUpRight className="flip-rtl" />} loading={leaving} disabled={!kind || (kind === "organization" && !organization.trim())} onClick={start}>
            {t.continue}
          </Button>
        </>
      }
    >
      <div className="stack">
        <div className="stack-sm">
          <h3>{t.whose}</h3>
          <p className="small muted">{t.whoseText}</p>
        </div>
        <div className="grid-2">
          <button type="button" className="choice" aria-pressed={kind === "organization"} onClick={() => setKind("organization")}>
            <Building2 />
            <span><span className="t">{t.organisation}</span><br /><span className="d">{t.organisationText}</span></span>
          </button>
          <button type="button" className="choice" aria-pressed={kind === "personal"} onClick={() => setKind("personal")}>
            <User />
            <span><span className="t">{t.personal}</span><br /><span className="d">{t.personalText}</span></span>
          </button>
        </div>
        {kind === "organization" && (
          <Field label={t.organisationField} hint={t.organisationHint}>
            <Input dir="ltr" autoFocus value={organization} onChange={(e) => setOrganization(e.target.value)} placeholder="acme" />
          </Field>
        )}
        {kind === "organization" && (
          <div className="stack-xs">
            <Switch checked={createRepositories} onChange={setCreateRepositories} label={t.createRepositories} />
            <span className="xsmall faint">{t.createRepositoriesText}</span>
          </div>
        )}
        {kind && (
          <div className="stack-xs">
            <Switch checked={repositoryGuard} onChange={setRepositoryGuard} label={t.repositoryGuard} />
            <span className="xsmall faint">{t.repositoryGuardText}</span>
          </div>
        )}
        <p className="small muted">
          {t.then(<b key="b" dir="ltr">{t.createButton}</b>)}
        </p>
      </div>
    </Modal>
  );
}

function Installations({ app, canManage }: { app: GitHubApp; canManage: boolean }) {
  const toast = useToast();
  const sync = useCall(() => api.post<GitHubInstallation[]>("/github/app/installations/sync"), APP_KEY);
  const forget = useCall((id: number) => api.del(`/github/app/installations/${id}`), APP_KEY);
  const t = useT(words).installations;
  const refresh = useT(common).common.refresh;
  return (
    <div className="stack-sm">
      <div className="row" style={{ flexWrap: "wrap" }}>
        <h3 className="spacer">{t.title}</h3>
        {canManage && (
          <Button size="sm" variant="ghost" icon={<RefreshCw />} loading={sync.isPending}
            onClick={() => sync.mutate(undefined, { onSuccess: () => toast("success", t.read), onError: (e) => toast("error", t.notRead, errorText(e)) })}>
            {refresh}
          </Button>
        )}
        {canManage && <Button asChild variant="primary" size="sm"><a href={app.install_url} target="_blank" rel="noreferrer"><Plus /> {t.install}</a></Button>}
      </div>
      {!app.installations.length ? (
        <Callout tone="warning">
          {t.notInstalled(<b key="b">{t.install}</b>)}
        </Callout>
      ) : (
        <Table>
          <TableHeader><TableRow><TableHead>{t.columns.account}</TableHead><TableHead>{t.columns.repositories}</TableHead><TableHead>{t.columns.since}</TableHead><TableHead /></TableRow></TableHeader>
          <TableBody>
            {app.installations.map((i) => (
              <TableRow key={i.id}>
                <TableCell>{i.account_type === "Organization" ? <Building2 size={14} /> : <User size={14} />} <bdi>{i.account}</bdi> {i.suspended && <Tag tone="caution">{t.suspended}</Tag>}</TableCell>
                <TableCell>{i.repository_selection === "all" ? t.all : t.selected}</TableCell>
                <TableCell className="small muted">{ago(i.added)}</TableCell>
                <TableCell className="actions">
                  {i.settings_url && <Button asChild variant="ghost" size="sm"><a href={i.settings_url} target="_blank" rel="noreferrer">{t.choose} <ArrowUpRight className="flip-rtl" /></a></Button>}
                  {canManage && (
                    <Button size="sm" variant="ghost" icon={<Trash2 />} aria-label={t.forget}
                      onClick={() => confirm(t.confirmForget(i.account)) && forget.mutate(i.id)} />
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}

function AppCard() {
  const { data: state, isPending } = useGitHubApp();
  const toast = useToast();
  const [connecting, setConnecting] = useState(false);
  const [removed, setRemoved] = useState<{ settings_url: string; installations: { account: string; settings_url: string }[] } | null>(null);
  const t = useT(words).app;
  const done = useT(common).common.done;
  const disconnect = useCall(() => api.del<{ settings_url: string; installations: { account: string; settings_url: string }[] }>("/github/app"), APP_KEY);
  if (isPending || !state) return <div className="card"><div className="card-body"><Skeleton height={120} /></div></div>;
  const app = state.app;
  return (
    <section className="card rise">
      <div className="card-head">
        <Plug />
        <h2>{t.title}</h2>
        <span className="sub">{app ? t.connected : t.readOnly}</span>
      </div>
      <div className="card-body stack">
        {!app ? (
          <>
            <p className="small muted">{t.intro}</p>
            {state.can_manage ? (
              <div><Button variant="primary" icon={<Plug />} onClick={() => setConnecting(true)}>{t.connect}</Button></div>
            ) : (
              <Callout>{t.superAdmin}</Callout>
            )}
          </>
        ) : (
          <>
            <div className="row" style={{ flexWrap: "wrap" }}>
              <span className="small spacer">
                <b><bdi>{app.name}</bdi></b>{" "}
                <span className="muted">{t.owned(app.owner.login, app.owner.type === "Organization")}</span>
              </span>
              <Button asChild variant="ghost" size="sm"><a href={app.settings_url} target="_blank" rel="noreferrer">{t.settings} <ArrowUpRight className="flip-rtl" /></a></Button>
              {state.can_manage && (
                <Button size="sm" variant="ghost" icon={<Unplug />} loading={disconnect.isPending}
                  onClick={() => confirm(t.confirmDisconnect) &&
                    disconnect.mutate(undefined, { onSuccess: setRemoved, onError: (e) => toast("error", t.notDisconnected, errorText(e)) })}>
                  {t.disconnect}
                </Button>
              )}
            </div>
            <Installations app={app} canManage={state.can_manage} />
            {!state.trigger_running && (
              <Callout>
                {t.polling(<code key="c">github-app</code>, <Link key="l" to="/settings?tab=github" viewTransition>{t.settingsLink}</Link>)}
              </Callout>
            )}
          </>
        )}
      </div>
      {connecting && <ConnectModal onClose={() => setConnecting(false)} />}
      {removed && (
        <Modal title={t.removeTitle} description={t.removeText} onClose={() => setRemoved(null)}
          footer={<><Button onClick={() => setRemoved(null)}>{done}</Button><Button asChild variant="primary"><a href={removed.settings_url} target="_blank" rel="noreferrer">{t.deleteApp} <ArrowUpRight className="flip-rtl" /></a></Button></>}>
          <div className="stack-sm">
            {removed.installations.map((i) => (
              <a key={i.account} className="row small" href={i.settings_url} target="_blank" rel="noreferrer">{t.uninstallFrom(i.account)} <ArrowUpRight size={14} className="flip-rtl" /></a>
            ))}
            <p className="small muted">{t.where}</p>
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
  const all = useT(words);
  const t = all.keys;
  const c = useT(common).common;
  return (
    <Page>
      <PageHead title={all.title}>{all.lead}</PageHead>
      <AppCard />
      <section className="card rise">
        <div className="card-head">
          <KeyRound />
          <h2>{t.title}</h2>
          <span className="sub">{appConnected ? t.fallback : t.perRepository}</span>
          <span className="spacer" />
          <Button size="sm" icon={<Plus />} onClick={() => setAdding(true)}>{c.add}</Button>
        </div>
        {appConnected && !!keysList?.length && (
          <div className="card-body">
            <Callout>
              {t.switch}
            </Callout>
          </div>
        )}
        {isPending ? <div className="card-body"><Skeleton height={120} /></div> : !keysList?.length ? (
          <Empty icon={<FolderGit2 />} title={t.none}>{appConnected ? t.notNeeded : t.addOne}</Empty>
        ) : (
          <Table>
            <TableHeader><TableRow><TableHead>{t.columns.repository}</TableHead><TableHead>{t.columns.key}</TableHead><TableHead>{t.columns.created}</TableHead><TableHead /></TableRow></TableHeader>
            <TableBody>
              {keysList.map((k) => (
                <TableRow key={k.repo}>
                  <TableCell><span className="mono">{k.spelling || k.repo}</span></TableCell>
                  <TableCell style={{ maxWidth: 320 }}><CopyField value={k.public_key} /></TableCell>
                  <TableCell className="small muted">{ago(k.created)}</TableCell>
                  <TableCell className="actions">
                    <Button asChild variant="ghost" size="sm"><a href={k.github_url} target="_blank" rel="noreferrer">GitHub <ArrowUpRight className="flip-rtl" /></a></Button>
                    <Button size="sm" variant="ghost" icon={<Trash2 />} aria-label={c.delete} onClick={() => confirm(t.confirmDelete(k.repo)) && remove.mutate(k.repo, { onSuccess: () => toast("success", t.deleted) })} />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </section>
      {adding && (
        <Modal title={t.addTitle} onClose={() => setAdding(false)}
          footer={<><Button onClick={() => setAdding(false)}>{c.cancel}</Button><Button variant="primary" icon={<KeyRound />} loading={create.isPending} disabled={!repo.trim()}
            onClick={() => create.mutate(undefined, { onSuccess: (m) => { setAdding(false); setRepo(""); setMade(m); }, onError: (e) => toast("error", t.notCreated, errorText(e)) })}>{t.create}</Button></>}>
          <Field label={t.repository} hint={<span dir="ltr">{t.repositoryHint}</span>}>
            <Input dir="ltr" autoFocus value={repo} onChange={(e) => setRepo(e.target.value)} />
          </Field>
        </Modal>
      )}
      {made && (
        <Modal title={t.addOnGitHub} description={t.addOnGitHubText(made.repo)} onClose={() => setMade(null)}
          footer={<><Button onClick={() => setMade(null)}>{c.done}</Button><Button asChild variant="primary"><a href={made.github_url} target="_blank" rel="noreferrer">{t.openGitHub} <ArrowUpRight className="flip-rtl" /></a></Button></>}>
          <div className="stack">
            <CopyField value={made.public_key} block />
            <Callout>{t.privateStays}</Callout>
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
  const all = useT(words);
  const t = all.return;
  const finish = useCall(async () => {
    if (step === "created") {
      await api.post("/github/app", { code: params.get("code") ?? "", state: params.get("state") ?? "" });
      return { title: t.created, detail: t.createdText };
    }
    const id = params.get("installation_id");
    if (!id) {
      return params.get("setup_action") === "request"
        ? { title: t.requested, detail: t.requestedText }
        : { title: t.nothing, detail: "" };
    }
    await api.post("/github/app/installations", { installation_id: id });
    return { title: t.installed, detail: t.installedText };
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
      <PageHead title={all.title} />
      <div className="card"><div className="card-body stack">
        {problem ? (
          <>
            <Callout tone="danger">{problem}</Callout>
            <div><Button asChild><Link to="/github" viewTransition>{t.back}</Link></Button></div>
          </>
        ) : (
          <div className="row"><Spinner /> <span className="muted">{t.finishing}</span></div>
        )}
      </div></div>
    </Page>
  );
}
