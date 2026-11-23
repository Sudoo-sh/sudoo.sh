// The client portal (src/sudoo/projects/clients.py, web/v1/clients.py): what a client account sees, on the portal's
// own address (PORTAL_DOMAIN) or on the panel's. Its own small frame and navigation: the client's projects, each with
// its branches and instances (status, Open Odoo), the switch per project and per branch, access requests to answer,
// who can get in now, the people it lets in, emergency access, backups where given, and the activity log. Nothing of
// the panel itself; the server checks every call again.

import { useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  ArrowUpRight,
  CircleHelp,
  Download,
  FolderKanban,
  Lock,
  LockOpen,
  LogOut,
  Mail,
  ScrollText,
  ShieldAlert,
  ShieldCheck,
  ShieldOff,
  SquareTerminal,
  UserRound,
  Wrench,
  X,
} from "lucide-react";
import { lazy, Suspense, useEffect, useState, type ReactNode } from "react";
import { Link, matchPath, Navigate, NavLink, useLocation, useNavigate } from "react-router";
import { api, errorText } from "@/api/client";
import {
  activityExportUrl,
  clientCalls,
  clientKeys,
  useClientActivity,
  useClientBackups,
  useClientProject,
  useClientProjects,
  usePortal,
} from "@/api/clients";
import { keys } from "@/api/queries";
import type { AccessRequest, ClientAccess, ClientActionAnswer, ClientConfirmation, ClientProject, ClientProjectMeta, Instance, SwitchState } from "@/api/types";
import { Mark } from "@/components/app/Brand";
import { Button, Callout, Empty, Field, Segmented, Skeleton, Status, Switch, Tag } from "@/components/app/basics";
import { Menu, MenuItem, MenuSeparator, Modal, useToast } from "@/components/app/overlay";
import { SelectBox } from "@/components/app/select";
import { LanguageSwitch } from "@/components/LanguageSwitch";
import { MaintenanceBanner, MaintenanceList } from "@/components/Maintenance";
import { useMe } from "@/components/Session";
import { ThemeButton } from "@/components/ThemeButton";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { countIn, useLocale, useT } from "@/i18n";
import words from "@/i18n/clients";
import maintenanceWords from "@/i18n/maintenance";
import { bytes, dateTime } from "@/lib/format";

const AccountPage = lazy(() => import("./Account").then((m) => ({ default: m.AccountPage })));
const ShellTab = lazy(() => import("@/components/shell/ShellTab").then((m) => ({ default: m.ShellTab })));

function useSignOut() {
  const client = useQueryClient();
  const navigate = useNavigate();
  return async () => {
    await api.del("/session").catch(() => undefined);
    client.setQueryData(keys.session, null);
    client.clear();
    navigate("/login");
  };
}

/** The portal's frame: its own header (the provider's name, the navigation, language, theme, the account). */
export function ClientPortal() {
  const t = useT(words).portal;
  const me = useMe();
  const { data: portal } = usePortal();
  const { pathname } = useLocation();
  const signOut = useSignOut();
  const maintenance = useT(maintenanceWords).nav;
  const project = matchPath("/p/:id/*", pathname) ?? matchPath("/p/:id", pathname);
  const id = project?.params.id ?? "";
  const shell = matchPath("/p/:id/shell/:instance", pathname);
  let page: ReactNode;
  if (pathname === "/" || pathname === "/p") page = <HomePage />;
  else if (shell?.params.id && shell.params.instance) page = <ShellPage id={shell.params.id} name={shell.params.instance} />;
  else if (pathname === "/confirm") page = <ConfirmPage />;
  else if (id && pathname.endsWith("/activity")) page = <ActivityPage id={id} />;
  else if (id) page = <ProjectPage id={id} />;
  else if (pathname === "/account") page = <Suspense fallback={<Skeleton height={240} />}><AccountPage /></Suspense>;
  else if (pathname === "/help") page = <HelpPage />;
  else if (pathname === "/maintenance") page = <MaintenancePage />;
  else page = <Navigate to="/" replace />;
  return (
    <div className="portal">
      <header className="portal-top">
        <Link to="/" className="portal-brand" viewTransition>
          <span className="mark"><Mark /></span>
          <span className="portal-names">
            <b className="truncate">{portal?.name ?? t.title}</b>
            {portal?.name && <span className="xsmall faint">{t.title}</span>}
          </span>
        </Link>
        <nav className="portal-nav" aria-label={t.title}>
          <NavLink to="/" className={() => `portal-link ${pathname === "/" || pathname.startsWith("/p/") ? "active" : ""}`} viewTransition>
            <FolderKanban /> <span>{t.projects}</span>
          </NavLink>
          <NavLink to="/maintenance" className={({ isActive }) => `portal-link ${isActive ? "active" : ""}`} viewTransition>
            <Wrench /> <span>{maintenance}</span>
          </NavLink>
          <NavLink to="/help" className={({ isActive }) => `portal-link ${isActive ? "active" : ""}`} viewTransition>
            <CircleHelp /> <span>{t.help}</span>
          </NavLink>
        </nav>
        <span className="spacer" />
        <span className="portal-actions">
          <LanguageSwitch signedIn />
          <ThemeButton />
          <Menu
            trigger={(toggle) => (
              <Button variant="ghost" size="sm" icon={<UserRound />} onClick={toggle} aria-label={t.account}>
                <span className="hide-sm truncate" style={{ maxWidth: 160 }}>{me.display_name}</span>
              </Button>
            )}
          >
            {(close) => (
              <>
                <MenuItem asChild><Link to="/account" viewTransition onClick={close}><UserRound /> {t.account}</Link></MenuItem>
                <MenuSeparator />
                <MenuItem danger onSelect={signOut}><LogOut /> {t.signOut}</MenuItem>
              </>
            )}
          </Menu>
        </span>
      </header>
      <div className="portal-main">
        {pathname !== "/maintenance" && pathname !== "/confirm" && <MaintenanceBanner client />}
        {page}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- small pieces

function StateTag({ access }: { access: ClientAccess | null | undefined }) {
  const t = useT(words).states;
  if (!access || access.state === "open") return <Tag tone="positive" icon={<ShieldCheck />}>{t.open}</Tag>;
  if (access.state === "timed") return <Tag icon={<ShieldCheck />}>{t.until(dateTime(access.until))}</Tag>;
  return <Tag tone="caution" icon={<ShieldOff />}>{t.closed}</Tag>;
}

function useAfterChange(id: string) {
  const client = useQueryClient();
  return () => {
    void client.invalidateQueries({ queryKey: clientKeys.project(id) });
    void client.invalidateQueries({ queryKey: clientKeys.mine });
    void client.invalidateQueries({ queryKey: ["client", "project", id, "activity"] });
  };
}

function Limit() {
  const t = useT(words).limit;
  return (
    <Callout tone="info">
      <b>{t.title}.</b> {t.text} {t.stronger}
    </Callout>
  );
}

// ---------------------------------------------------------------- the client's projects

function HomePage() {
  const t = useT(words).home;
  const locale = useLocale();
  const { data, isPending, error } = useClientProjects();
  const listed = data?.data ?? [];
  return (
    <main className="page portal-page" tabIndex={-1}>
      <div className="page-head"><div className="titles"><h1>{t.title}</h1><p>{t.text}</p></div></div>
      {error && <Callout tone="warning">{errorText(error)}</Callout>}
      {isPending ? (
        <Skeleton height={160} />
      ) : listed.length === 0 ? (
        <div className="card"><Empty icon={<FolderKanban />} title={t.empty}>{t.emptyText}</Empty></div>
      ) : (
        <div className="portal-grid">
          {listed.map((p) => (
            <Link key={p.id} to={`/p/${encodeURIComponent(p.id)}`} className="card link pad portal-card" viewTransition>
              <div className="row-wrap">
                <h2 className="spacer truncate">{p.name}</h2>
                <StateTag access={p.switch} />
              </div>
              <p className="small muted">{countIn(locale, p.instances, t.instances)}</p>
              {p.closed_branches.length > 0 && p.switch?.state !== "closed" && <p className="small">{t.closedBranches(p.closed_branches.join(", "))}</p>}
              {p.pending_requests > 0 && <p className="small"><Tag tone="accent">{countIn(locale, p.pending_requests, t.waiting)}</Tag></p>}
              {p.emergency_active && <p className="small"><Tag tone="negative" icon={<ShieldAlert />}>{t.emergencyNow}</Tag></p>}
            </Link>
          ))}
        </div>
      )}
      <Limit />
    </main>
  );
}

// ---------------------------------------------------------------- one project

/** Open / Open for a time / Closed, with the durations the panel offers and one of the client's own. */
function SwitchControl({ access, meta, busy, onSet }: {
  access: ClientAccess | null;
  meta: ClientProjectMeta;
  busy: boolean;
  onSet: (state: SwitchState, hours?: number) => void;
}) {
  const t = useT(words);
  const current: SwitchState = access?.state ?? "open";
  const [choice, setChoice] = useState<SwitchState>(current);
  const [custom, setCustom] = useState("");
  const shown = choice === "timed" ? "timed" : current;
  return (
    <div className="stack">
      <Segmented<SwitchState>
        value={shown}
        onChange={(next) => {
          setChoice(next);
          if (next !== "timed") onSet(next);
        }}
        options={[
          { value: "open", label: t.states.open, icon: <ShieldCheck /> },
          { value: "timed", label: t.project.openFor },
          { value: "closed", label: t.states.closed, icon: <ShieldOff /> },
        ]}
      />
      {shown === "timed" && (
        <div className="row-wrap">
          {meta.open_hours.map((h) => (
            <Button key={h} size="sm" loading={busy} onClick={() => onSet("timed", h)}>{t.project.hoursShort(h)}</Button>
          ))}
          <Input type="number" inputMode="numeric" dir="ltr" min={1} max={meta.open_max_hours} placeholder={t.project.custom} aria-label={t.project.hours}
            value={custom} onChange={(e) => setCustom(e.target.value)} style={{ width: 110 }} />
          <Button size="sm" variant="primary" disabled={!(Number(custom) > 0)} loading={busy} onClick={() => onSet("timed", Number(custom))}>
            {t.project.openForHours(Number(custom) || 0)}
          </Button>
          <span className="xsmall faint">{t.project.maxHours(meta.open_max_hours)}</span>
        </div>
      )}
      <p className="small muted">
        {current === "closed" ? t.project.closedText : current === "timed" ? t.project.timedText(dateTime(access?.until)) : t.project.openText}
      </p>
    </div>
  );
}

function ApproveDialog({ request, project, onClose, onDone }: { request: AccessRequest; project: ClientProject; onClose: () => void; onDone: () => void }) {
  const t = useT(words).requests;
  const c = useT(words).confirm;
  const toast = useToast();
  const [hours, setHours] = useState(String(request.hours));
  const [people, setPeople] = useState<string[]>([request.by.name]);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const approve = async () => {
    setBusy(true);
    try {
      const answer = await clientCalls.approve(request.id, { hours: Number(hours), people, note });
      if (answer.done) toast("success", t.approved);
      else toast("info", c.checkMail, c.checkMailText);
      onDone();
      onClose();
    } catch (error) {
      toast("error", t.notDone, errorText(error));
    } finally {
      setBusy(false);
    }
  };
  const toggle = (name: string, on: boolean) => setPeople((list) => (on ? [...list, name] : list.filter((n) => n !== name)));
  return (
    <Modal title={t.approveTitle(request.by.display_name)} description={t.approveText} onClose={onClose}
      footer={<Button variant="primary" loading={busy} disabled={!people.length || !(Number(hours) > 0)} onClick={approve}>{t.approve}</Button>}>
      <div className="stack">
        <Field label={t.people} hint={t.peopleHint}>
          <div className="stack-sm">
            {project.people.map((p) => (
              <Switch key={p.name} checked={people.includes(p.name)} onChange={(on) => toggle(p.name, on)} label={<bdi>{p.display_name}</bdi>} />
            ))}
          </div>
        </Field>
        <Field label={useT(words).project.hours} htmlFor="approve-hours">
          <Input id="approve-hours" type="number" inputMode="numeric" dir="ltr" min={1} value={hours} onChange={(e) => setHours(e.target.value)} style={{ maxWidth: 120 }} />
        </Field>
        <Field label={t.note} hint={t.noteHint} htmlFor="approve-note">
          <Textarea id="approve-note" rows={2} maxLength={300} value={note} onChange={(e) => setNote(e.target.value)} />
        </Field>
      </div>
    </Modal>
  );
}

function RefuseDialog({ request, onClose, onDone }: { request: AccessRequest; onClose: () => void; onDone: () => void }) {
  const t = useT(words).requests;
  const toast = useToast();
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const refuse = async () => {
    setBusy(true);
    try {
      await clientCalls.refuse(request.id, note);
      toast("success", t.refused);
      onDone();
      onClose();
    } catch (error) {
      toast("error", t.notDone, errorText(error));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title={t.refuse} onClose={onClose} footer={<Button variant="danger" loading={busy} onClick={refuse}>{t.refuse}</Button>}>
      <Field label={t.note} hint={t.noteHint} htmlFor="refuse-note">
        <Textarea id="refuse-note" rows={2} maxLength={300} value={note} onChange={(e) => setNote(e.target.value)} autoFocus />
      </Field>
    </Modal>
  );
}

function ProjectPage({ id }: { id: string }) {
  const all = useT(words);
  const t = all.project;
  const toast = useToast();
  const after = useAfterChange(id);
  const { data, isPending, error } = useClientProject(id);
  const [busy, setBusy] = useState(false);
  const [approving, setApproving] = useState<AccessRequest | null>(null);
  const [refusing, setRefusing] = useState<AccessRequest | null>(null);
  const [adding, setAdding] = useState("");
  const { data: backups } = useClientBackups(id, Boolean(data?.project.given.backups));
  if (isPending) return <main className="page portal-page"><Skeleton height={44} width="40%" /><Skeleton height={240} /></main>;
  if (error || !data)
    return (
      <main className="page portal-page">
        <Callout tone="warning">{errorText(error)}</Callout>
        <Button asChild><Link to="/"><ArrowLeft className="flip-rtl" /> {t.back}</Link></Button>
      </main>
    );
  const { project, meta } = data;
  const run = async (call: () => Promise<ClientActionAnswer | unknown>, done: string) => {
    setBusy(true);
    try {
      const answer = (await call()) as ClientActionAnswer | undefined;
      // a major change waits for the client's confirmation by e-mail (202)
      if (answer && answer.done === false) toast("info", all.confirm.checkMail, all.confirm.checkMailText);
      else toast("success", done);
      after();
    } catch (err) {
      toast("error", t.notChanged, errorText(err));
    } finally {
      setBusy(false);
    }
  };
  const setSwitch = (branch: string | null, state: SwitchState, hours?: number, inherit = false) =>
    run(() => clientCalls.setSwitch(id, { branch, state, hours, inherit }), t.changed);
  const pending = project.requests.filter((r) => r.status === "pending");
  const answered = project.requests.filter((r) => r.status !== "pending").slice(0, 10);
  const allowNames = project.allow.map((a) => a.name);
  const saveAllow = (names: string[]) => run(() => clientCalls.setAllow(id, names), all.allow.saved);
  const setLock = (branch: string | null, locked: boolean) =>
    run(() => clientCalls.setLock(id, { branch, locked }), locked ? all.locks.locked : all.locks.unlocked);
  const now = Date.now();
  const emergencies = project.notices.filter((n) => Date.parse(n.until) > now - 24 * 3600_000);
  return (
    <main className="page portal-page" tabIndex={-1}>
      <Link to="/" className="portal-back small" viewTransition><ArrowLeft className="flip-rtl" /> {t.back}</Link>
      <div className="page-head">
        <div className="titles">
          <div className="row-wrap" style={{ gap: 12 }}><h1>{project.name}</h1><StateTag access={project.switch} /></div>
        </div>
        <div className="actions">
          <Button asChild><Link to={`/p/${encodeURIComponent(id)}/activity`} viewTransition><ScrollText /> {all.activity.title}</Link></Button>
        </div>
      </div>

      {emergencies.map((n) => (
        <Callout key={n.id} tone="danger">
          <b>{all.emergency.banner(n.people.map((p) => p.display_name).join(", ") || n.by, n.branch ?? all.requests.whole, dateTime(n.until))}</b>{" "}
          {n.reason && all.emergency.reason(n.reason)}
        </Callout>
      ))}

      {project.confirmations.length > 0 && <Waiting id={id} items={project.confirmations} minutes={meta.confirm_minutes} />}

      {project.locked && (
        <Callout tone="warning"><b>{all.locks.projectLocked}</b> {all.locks.text}</Callout>
      )}

      {pending.length > 0 && (
        <section className="card portal-requests">
          <div className="card-head"><h2 className="spacer">{all.requests.title}</h2></div>
          <div className="card-body stack">
            {pending.map((r) => (
              <div key={r.id} className="portal-request">
                <div className="stack-sm spacer">
                  <b>{all.requests.asks(r.by.display_name, r.branch ?? all.requests.whole, r.hours)}</b>
                  <span className="small muted"><bdi>“{r.reason}”</bdi></span>
                  <span className="xsmall faint">{dateTime(r.asked_at)}</span>
                </div>
                <div className="row-wrap">
                  <Button size="sm" variant="primary" onClick={() => setApproving(r)}>{all.requests.approve}</Button>
                  <Button size="sm" onClick={() => setRefusing(r)}>{all.requests.refuse}</Button>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      <section className="card">
        <div className="card-head"><h2>{t.switchTitle}</h2></div>
        <div className="card-body">
          <SwitchControl key={`${project.switch?.state}-${project.switch?.until}`} access={project.switch} meta={meta} busy={busy}
            onSet={(state, hours) => setSwitch(null, state, hours)} />
        </div>
        <div className="card-foot">
          <span className="small muted spacer">{project.locked ? all.locks.projectLocked : all.locks.lockHint}</span>
          <Button size="sm" variant={project.locked ? "default" : "danger"} icon={project.locked ? <LockOpen /> : <Lock />} loading={busy}
            onClick={() => setLock(null, !project.locked)}>
            {project.locked ? all.locks.unlockProject : all.locks.lockProject}
          </Button>
        </div>
      </section>

      <section className="card">
        <div className="card-head"><h2>{t.branchesTitle}</h2></div>
        <div className="card-body stack">
          <p className="small muted">{t.branchesText}</p>
          {project.branches.map((b) => (
            <div key={b.name} className="portal-branch">
              <div className="row-wrap">
                <bdi dir="ltr" className="mono"><b>{b.name}</b></bdi>
                <Tag>{t.stage[b.stage] ?? b.stage}</Tag>
                <StateTag access={b.switch} />
                {b.switch?.locked && <Tag tone="negative" icon={<Lock />}>{all.locks.tag}</Tag>}
                {b.switch?.scope === "project" && <span className="xsmall faint">{all.states.followsProject}</span>}
                <span className="spacer" />
                {!project.locked && (
                  <Button size="sm" variant="ghost" icon={b.switch?.locked ? <LockOpen /> : <Lock />} loading={busy} onClick={() => setLock(b.name, !b.switch?.locked)}>
                    {b.switch?.locked ? all.locks.unlock : all.locks.lock}
                  </Button>
                )}
                <SelectBox size="sm" style={{ width: 200 }} aria-label={t.branchMenu(b.name)} value={b.switch?.scope === "branch" ? (b.switch.state === "timed" ? "timed" : b.switch.state) : "follow"}
                  onValueChange={(value) => {
                    if (value === "follow") void setSwitch(b.name, "open", undefined, true);
                    else if (value === "timed") void setSwitch(b.name, "timed", meta.open_hours[0] ?? 1);
                    else void setSwitch(b.name, value as SwitchState);
                  }}>
                  <option value="follow">{t.follow}</option>
                  <option value="open">{all.states.open}</option>
                  <option value="timed">{t.openForHours(meta.open_hours[0] ?? 1)}</option>
                  <option value="closed">{all.states.closed}</option>
                </SelectBox>
              </div>
              {b.instances.length === 0 ? <p className="xsmall faint">{t.noInstance}</p> : b.instances.map((i) => (
                <div key={i.name} className="row-wrap small portal-instance">
                  <Status status={i.status} />
                  <bdi dir="ltr" className="mono truncate">{i.name}</bdi>
                  {i.odoo && <Tag mono>{i.odoo}</Tag>}
                  <span className="spacer" />
                  {project.given.shell && i.status === "online" && (
                    <Button size="sm" variant="ghost" asChild><Link to={`/p/${encodeURIComponent(id)}/shell/${encodeURIComponent(i.name)}`} viewTransition><SquareTerminal /> {t.shell}</Link></Button>
                  )}
                  {i.url && (
                    <Button size="sm" asChild><a href={i.url} target="_blank" rel="noreferrer">{t.openOdoo} <ArrowUpRight className="flip-rtl" /></a></Button>
                  )}
                </div>
              ))}
            </div>
          ))}
        </div>
      </section>

      <div className="portal-two">
        <section className="card">
          <div className="card-head"><h2>{all.passes.title}</h2></div>
          <div className="card-body stack-sm">
            {project.passes.length === 0 ? <p className="small faint">{all.passes.none}</p> : project.passes.map((p) => (
              <div key={p.id} className="row-wrap small">
                <Tag tone={p.kind === "emergency" ? "negative" : undefined}>{p.kind === "emergency" ? all.passes.emergency : all.passes.request}</Tag>
                <span><bdi>{p.people.map((x) => x.display_name).join(", ")}</bdi> · {p.branch ?? all.requests.whole}</span>
                <span className="faint">{all.passes.ends(dateTime(p.until))}</span>
                <span className="spacer" />
                <Button size="sm" loading={busy} onClick={() => run(() => clientCalls.endPass(id, p.id), all.passes.ended)}>{all.passes.end}</Button>
              </div>
            ))}
          </div>
        </section>

        <section className="card">
          <div className="card-head"><h2>{all.allow.title}</h2></div>
          <div className="card-body stack-sm">
            <p className="small muted">{all.allow.text}</p>
            {project.allow.length === 0 ? <p className="small faint">{all.allow.none}</p> : (
              <div className="row-wrap">
                {project.allow.map((a) => (
                  <span key={a.name} className="portal-chip">
                    <bdi>{a.display_name}</bdi>
                    <button type="button" aria-label={all.allow.remove(a.display_name)} onClick={() => saveAllow(allowNames.filter((n) => n !== a.name))}><X /></button>
                  </span>
                ))}
              </div>
            )}
            <div className="row-wrap">
              <SelectBox size="sm" aria-label={all.allow.choose} value={adding} onValueChange={setAdding}>
                <option value="">{all.allow.choose}</option>
                {project.people.filter((p) => !allowNames.includes(p.name)).map((p) => <option key={p.name} value={p.name}>{p.display_name}</option>)}
              </SelectBox>
              <Button size="sm" disabled={!adding} loading={busy} onClick={() => { void saveAllow([...allowNames, adding]); setAdding(""); }}>{all.allow.add}</Button>
            </div>
          </div>
        </section>
      </div>

      <section className="card">
        <div className="card-head"><h2>{all.emergency.title}</h2></div>
        <div className="card-body stack-sm">
          <Switch checked={project.emergency_allowed} disabled={busy} label={all.emergency.allow}
            onChange={(on) => run(() => clientCalls.setEmergency(id, on), on ? all.emergency.turnedOn : all.emergency.turnedOff)} />
          <p className="small muted">{project.emergency_allowed ? all.emergency.onText(meta.emergency_minutes) : all.emergency.offText}</p>
        </div>
      </section>

      <section className="card">
        <div className="card-head"><h2>{all.backups.title}</h2></div>
        <div className="card-body stack-sm">
          {!project.given.backups ? <p className="small faint">{all.backups.notGiven}</p> : (
            <>
              <p className="small muted">{all.backups.text}</p>
              {!backups?.length ? <p className="small faint">{all.backups.none}</p> : backups.slice(0, 20).map((b) => (
                <div key={b.file} className="row-wrap small">
                  <bdi dir="ltr" className="mono truncate">{b.instance}</bdi>
                  <span className="faint">{dateTime(b.created_at)} · {bytes(b.size_bytes)}</span>
                  <span className="spacer" />
                  <Button size="sm" asChild><a href={`/api/v1/backups/${encodeURIComponent(b.file)}`} download><Download /> {all.backups.download}</a></Button>
                </div>
              ))}
            </>
          )}
        </div>
      </section>

      {answered.length > 0 && (
        <section className="card">
          <div className="card-head"><h2>{all.requests.title}</h2></div>
          <div className="card-body stack-sm">
            {answered.map((r) => (
              <div key={r.id} className="row-wrap small">
                <Tag tone={r.status === "approved" ? "positive" : r.status === "refused" ? "negative" : undefined}>{all.requests.status[r.status] ?? r.status}</Tag>
                <span>{all.requests.asks(r.by.display_name, r.branch ?? all.requests.whole, r.hours)}</span>
                <span className="faint">{dateTime(r.decided_at ?? r.asked_at)}</span>
              </div>
            ))}
          </div>
        </section>
      )}

      <Limit />
      {approving && <ApproveDialog request={approving} project={project} onClose={() => setApproving(null)} onDone={after} />}
      {refusing && <RefuseDialog request={refusing} onClose={() => setRefusing(null)} onDone={after} />}
    </main>
  );
}

// ---------------------------------------------------------------- confirmation by e-mail

/** "Waiting for your e-mail confirmation": each change that waits, with the code to type and Cancel. */
function Waiting({ id, items, minutes }: { id: string; items: ClientConfirmation[]; minutes: number }) {
  const t = useT(words).confirm;
  const toast = useToast();
  const after = useAfterChange(id);
  const [codes, setCodes] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState("");
  const confirm = async (item: ClientConfirmation) => {
    setBusy(item.id);
    try {
      await clientCalls.confirm(item.id, { code: codes[item.id] ?? "" });
      toast("success", t.done);
      after();
    } catch (error) {
      toast("error", t.notDone, errorText(error));
    } finally {
      setBusy("");
    }
  };
  const cancel = async (item: ClientConfirmation) => {
    await clientCalls.cancel(item.id).catch(() => undefined);
    after();
  };
  return (
    <section className="card portal-waiting" aria-live="polite">
      <div className="card-head"><Mail /><h2 className="spacer">{t.title}</h2></div>
      <div className="card-body stack">
        <p className="small muted">{t.text(minutes)}</p>
        {items.map((item) => (
          <div key={item.id} className="portal-request">
            <div className="stack-sm spacer">
              <b><bdi>{item.summary}</bdi></b>
              <span className="xsmall faint">{t.until(dateTime(item.expires_at))}</span>
              {item.demo_code && <span className="xsmall faint">{t.demoCode(item.demo_code)}</span>}
            </div>
            <div className="row-wrap">
              <Input dir="ltr" inputMode="numeric" autoComplete="one-time-code" maxLength={6} placeholder="000000" aria-label={t.code}
                className="mono" style={{ width: 110 }} value={codes[item.id] ?? ""} onChange={(e) => setCodes({ ...codes, [item.id]: e.target.value.replace(/\D/g, "") })} />
              <Button size="sm" variant="primary" loading={busy === item.id} disabled={(codes[item.id] ?? "").length !== 6} onClick={() => confirm(item)}>{t.confirm}</Button>
              <Button size="sm" variant="ghost" onClick={() => cancel(item)}>{t.cancel}</Button>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

/** /confirm#<id>.<secret>: the link of the e-mail, opened in the browser that asked. */
function ConfirmPage() {
  const t = useT(words).confirm;
  const client = useQueryClient();
  const [state, setState] = useState<{ status: "working" | "done" | "failed"; text?: string; project?: string }>({ status: "working" });
  useEffect(() => {
    const [confirmationId, token] = window.location.hash.replace(/^#/, "").split(".", 2);
    if (!confirmationId || !token) {
      setState({ status: "failed", text: t.badLink });
      return;
    }
    history.replaceState(null, "", "/confirm"); // the secret leaves the address bar
    clientCalls.confirm(confirmationId, { token }).then(
      (answer) => {
        setState({ status: "done", project: answer.project });
        void client.invalidateQueries({ queryKey: ["client"] });
      },
      (error: unknown) => setState({ status: "failed", text: errorText(error) }),
    );
  }, [client, t.badLink]);
  return (
    <main className="page portal-page" tabIndex={-1}>
      <div className="page-head"><div className="titles"><h1>{t.title}</h1></div></div>
      {state.status === "working" ? <Skeleton height={60} /> : state.status === "done" ? (
        <Callout tone="success">{t.done}</Callout>
      ) : (
        <Callout tone="warning">{state.text}</Callout>
      )}
      <Button asChild><Link to={state.project ? `/p/${encodeURIComponent(state.project)}` : "/"}><ArrowLeft className="flip-rtl" /> {useT(words).project.back}</Link></Button>
    </main>
  );
}

// ---------------------------------------------------------------- the activity log

function ActivityPage({ id }: { id: string }) {
  const all = useT(words);
  const t = all.activity;
  const [kind, setKind] = useState<"all" | "access">("all");
  const [offset, setOffset] = useState(0);
  const limit = 50;
  const { data, isPending, error } = useClientActivity(id, kind, offset, limit);
  const entries = data?.data ?? [];
  const total = Number(data?.meta?.total ?? 0);
  return (
    <main className="page portal-page" tabIndex={-1}>
      <Link to={`/p/${encodeURIComponent(id)}`} className="portal-back small" viewTransition><ArrowLeft className="flip-rtl" /> {all.portal.projects}</Link>
      <div className="page-head">
        <div className="titles"><h1>{t.title}</h1><p>{t.text}</p></div>
        <div className="actions">
          <Button asChild><a href={activityExportUrl(id, kind)} download><Download /> {t.export}</a></Button>
        </div>
      </div>
      <Segmented<"all" | "access"> value={kind} onChange={(v) => { setKind(v); setOffset(0); }}
        options={[{ value: "all", label: t.all }, { value: "access", label: t.access }]} />
      {error && <Callout tone="warning">{errorText(error)}</Callout>}
      <section className="card">
        {isPending ? <div className="card-body"><Skeleton height={200} /></div> : entries.length === 0 ? (
          <div className="card-body"><p className="small faint">{t.empty}</p></div>
        ) : (
          <ol className="portal-log">
            {entries.map((e, i) => (
              <li key={`${e.at}-${i}`} className={e.emergency ? "emergency" : !e.allowed ? "refused" : undefined}>
                <span className="xsmall faint portal-log-when">{dateTime(e.at)}</span>
                <span className="portal-log-what">
                  <b><bdi>{e.user === "system" ? t.system : e.user}</bdi></b> {e.event}
                  {e.emergency && <> <Tag tone="negative">{t.emergency}</Tag></>}
                  {!e.allowed && <> <Tag tone="caution">{t.refused}</Tag></>}
                  {(e.instance || e.branch) && <span className="xsmall faint"> · <bdi dir="ltr">{e.instance ?? e.branch}</bdi></span>}
                  {e.detail && <span className="xsmall muted portal-log-detail"><bdi>{e.detail}</bdi></span>}
                </span>
              </li>
            ))}
          </ol>
        )}
      </section>
      <div className="row-wrap">
        <Button size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))}>{t.previous}</Button>
        <Button size="sm" disabled={offset + limit >= total} onClick={() => setOffset(offset + limit)}>{t.next}</Button>
      </div>
    </main>
  );
}

// ---------------------------------------------------------------- a shell, where the client was given one

function ShellPage({ id, name }: { id: string; name: string }) {
  const all = useT(words);
  const { data } = useClientProject(id);
  const found = data?.project.branches.flatMap((b) => b.instances).find((i) => i.name === name);
  return (
    <main className="page portal-page" tabIndex={-1}>
      <Link to={`/p/${encodeURIComponent(id)}`} className="portal-back small" viewTransition><ArrowLeft className="flip-rtl" /> {data?.project.name ?? all.portal.projects}</Link>
      <div className="page-head"><div className="titles"><h1><bdi dir="ltr">{name}</bdi></h1></div></div>
      {!data ? <Skeleton height={200} /> : !found || !data.project.given.shell ? (
        <Callout tone="warning">{all.project.noShell}</Callout>
      ) : (
        <Suspense fallback={<Skeleton height={200} />}>
          {/* the Shell tab needs the instance's name and stage only; the server checks the shell again */}
          <ShellTab instance={{ name, stage: found.stage ?? "development" } as Instance} />
        </Suspense>
      )}
    </main>
  );
}

// ---------------------------------------------------------------- how it works

/** The maintenance planned on the client's projects; choosing its time where the provider allows it. */
function MaintenancePage() {
  const t = useT(maintenanceWords);
  return (
    <main className="page portal-page" tabIndex={-1}>
      <div className="page-head"><div className="titles"><h1>{t.title}</h1><p>{t.clientLead}</p></div></div>
      <MaintenanceList client />
    </main>
  );
}

function HelpPage() {
  const all = useT(words);
  return (
    <main className="page portal-page" tabIndex={-1}>
      <div className="page-head"><div className="titles"><h1>{all.portal.help}</h1></div></div>
      <section className="card">
        <div className="card-body stack">
          <p><b>{all.states.open}.</b> {all.project.openText}</p>
          <p><b>{all.states.closed}.</b> {all.project.closedText}</p>
          <p><b>{all.project.openFor}.</b> {all.project.timedText("…")}</p>
          <p><b>{all.requests.title}.</b> {all.requests.text}</p>
          <p><b>{all.emergency.title}.</b> {all.emergency.offText}</p>
          <p><b>{all.activity.title}.</b> {all.activity.text}</p>
        </div>
      </section>
      <Limit />
    </main>
  );
}
