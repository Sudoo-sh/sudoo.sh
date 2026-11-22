// Connect as (the instance's Access tab): open Odoo signed in as one of its users, without their password. The
// split button opens Odoo, or, from its menu, a dialog to choose the user: the panel gives a single-use link valid
// for a minute (POST /api/v1/instances/<name>/connect, web/v1/connect.py) that the person then opens. Production asks
// for "production" to be typed first. Every link is recorded in the audit log.

import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, ChevronDown, LogIn, Search, UserRound } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api, errorText } from "@/api/client";
import { useCall } from "@/api/queries";
import type { Instance } from "@/api/types";
import { useT } from "@/i18n";
import common from "@/i18n/common";
import toolWords from "@/i18n/tools";
import { Button, Callout, Field, Skeleton } from "./app/basics";
import { Menu, MenuItem, Modal, useToast } from "./app/overlay";
import { Input } from "./ui/input";

interface OdooUser {
  id: number;
  login: string;
  name: string;
}
interface ConnectLink {
  url: string;
  expires_at: string;
  login: string;
}

const ASLEEP = ["paused", "switched_off", "stopped"];

/** "Open Odoo" with a menu holding "Connect as…". */
export function ConnectButton({ instance }: { instance: Instance }) {
  const t = useT(toolWords).connect;
  const [open, setOpen] = useState(false);
  if (!instance.url) return null;
  const asleep = ASLEEP.includes(instance.status);
  return (
    <>
      <span className="split-button">
        <Button asChild variant="primary"><a href={instance.url} target="_blank" rel="noreferrer">
          {t.open} <ArrowUpRight className="flip-rtl" />
        </a></Button>
        <Menu trigger={(toggle) => <Button variant="primary" icon={<ChevronDown />} onClick={toggle} aria-label={t.connectAs} />}>
          {(close) => (
            <MenuItem disabled={asleep} onSelect={() => { close(); setOpen(true); }}>
              <LogIn /> {t.connectAs}
            </MenuItem>
          )}
        </Menu>
      </span>
      {open && <ConnectDialog instance={instance} onClose={() => setOpen(false)} />}
    </>
  );
}

/** The Access tab's card: what Connect as does, and the button. */
export function ConnectAsCard({ instance }: { instance: Instance }) {
  const t = useT(toolWords).connect;
  const asleep = ASLEEP.includes(instance.status);
  return (
    <section className="card">
      <div className="card-head"><h2>{t.title}</h2></div>
      <div className="card-body stack">
        <p className="small muted">{t.text}</p>
        {asleep ? <Callout tone="info">{t.asleep}</Callout> : <div className="row"><ConnectButton instance={instance} /></div>}
      </div>
    </section>
  );
}

function useSecondsLeft(until: string | null): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!until) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [until]);
  return until ? Math.max(0, Math.round((Date.parse(until) - now) / 1000)) : 0;
}

export function ConnectDialog({ instance, onClose }: { instance: Instance; onClose: () => void }) {
  const t = useT(toolWords).connect;
  const c = useT(common).common;
  const toast = useToast();
  const production = instance.stage === "production";
  const users = useQuery({
    queryKey: ["odoo-users", instance.name],
    queryFn: () => api.get<OdooUser[]>(`/instances/${instance.name}/odoo-users`),
    staleTime: 30_000,
  });
  const [find, setFind] = useState("");
  const [chosen, setChosen] = useState<OdooUser | null>(null);
  const [typed, setTyped] = useState("");
  const [link, setLink] = useState<ConnectLink | null>(null);
  const left = useSecondsLeft(link?.expires_at ?? null);
  const shown = useMemo(() => {
    const wanted = find.trim().toLowerCase();
    return (users.data ?? []).filter((u) => !wanted || u.name.toLowerCase().includes(wanted) || u.login.toLowerCase().includes(wanted));
  }, [users.data, find]);
  const give = useCall(
    (user: OdooUser) =>
      api.post<ConnectLink>(`/instances/${instance.name}/connect`, { uid: user.id, ...(production ? { confirm_stage: "production" } : {}) }),
    [],
  );
  const ready = !!chosen && (!production || typed === "production");
  const getLink = () =>
    chosen &&
    give.mutate(chosen, {
      onSuccess: (answer) => setLink(answer),
      onError: (error) => toast("error", t.notConnected, errorText(error)),
    });

  const footer = link && left > 0 ? (
    <>
      <Button onClick={onClose}>{c.close}</Button>
      <Button asChild variant="primary" icon={<LogIn />}>
        <a href={link.url} target="_blank" rel="noreferrer" onClick={() => setTimeout(onClose, 300)}>{t.openAs(chosen?.name ?? link.login)}</a>
      </Button>
    </>
  ) : (
    <>
      <Button onClick={onClose}>{c.cancel}</Button>
      <Button variant="primary" icon={<LogIn />} disabled={!ready} loading={give.isPending} onClick={() => { setLink(null); getLink(); }}>{t.getLink}</Button>
    </>
  );
  return (
    <Modal title={t.title} description={t.text} onClose={onClose} footer={footer}>
      <div className="stack">
        {production && <Callout tone="warning">{t.productionWarning}</Callout>}
        {link ? (
          <Callout tone={left > 0 ? "success" : "warning"}>{left > 0 ? t.expires(left) : t.expired}</Callout>
        ) : null}
        <div className="log-search connect-find">
          <Search aria-hidden="true" />
          <input type="search" value={find} placeholder={t.search} aria-label={t.search} onChange={(e) => setFind(e.target.value)} />
        </div>
        <div className="user-pick" role="radiogroup" aria-label={t.choose}>
          {users.isPending ? (
            <><Skeleton height={44} /><Skeleton height={44} /></>
          ) : users.error ? (
            <Callout tone="danger">{errorText(users.error)}</Callout>
          ) : !shown.length ? (
            <p className="small muted">{t.none}</p>
          ) : (
            shown.map((user) => (
              <button key={user.id} type="button" role="radio" aria-checked={chosen?.id === user.id}
                onClick={() => { setChosen(user); setLink(null); }}>
                <UserRound aria-hidden="true" />
                <span className="who"><b><bdi>{user.name}</bdi></b><span className="xsmall faint mono" dir="ltr">{user.login}</span></span>
              </button>
            ))
          )}
        </div>
        {production && (
          <Field label={<>{t.typeProduction(<code key="p">production</code>)}</>}>
            <Input dir="ltr" value={typed} onChange={(e) => setTyped(e.target.value)} />
          </Field>
        )}
        <p className="xsmall faint">{t.noPasswords}</p>
      </div>
    </Modal>
  );
}
