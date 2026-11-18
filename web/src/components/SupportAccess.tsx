// Support access (src/sudoo/support/): granting Sudoo.sh support temporary access to the panel, the grants and the
// sessions open now, and the banner every signed-in person sees while support is connected, with "End now" for those
// who manage it. The server checks everything again; withdrawing takes effect at once.

import { LifeBuoy, ShieldCheck, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router";
import { api, errorText } from "@/api/client";
import { keys, useCall, useInstances, useProjects, useSupportAccess, useSupportStatus } from "@/api/queries";
import type { SupportGrant, SupportLevel } from "@/api/types";
import { useT } from "@/i18n";
import words from "@/i18n/support";
import { dateTime } from "@/lib/format";
import { Button, Callout, Field, Segmented, Skeleton, Tag } from "./ui/basics";
import { useToast } from "./ui/overlay";

/** Shown on every page while a support session is open (or, for those who manage it, while support has access). */
export function SupportBanner() {
  const t = useT(words);
  const toast = useToast();
  const { data: status } = useSupportStatus();
  const end = useCall(() => api.post("/support-access/end"), [keys.support]);
  if (!status || (!status.connected && !(status.can_end && status.active_grants > 0))) return null;
  const names = [...new Set(status.sessions.map((s) => s.staff_name))].join(", ");
  return (
    <div className="notices" role="status">
      <Callout tone={status.connected ? "warning" : "info"}>
        <div className="row-wrap">
          <span className="spacer">
            {status.connected ? t.banner(names) : t.bannerAccess(status.active_grants)}{" "}
            {status.can_end && <Link to="/support-access" viewTransition>{t.manage}</Link>}
          </span>
          {status.can_end && (
            <Button size="sm" variant="danger" loading={end.isPending}
              onClick={() => end.mutate(undefined, { onSuccess: () => toast("success", t.ended), onError: (e) => toast("error", t.notEnded, errorText(e)) })}>
              {t.endNow}
            </Button>
          )}
        </div>
      </Callout>
    </div>
  );
}

function scopeText(t: (typeof words)["en"], grant: SupportGrant) {
  if (grant.scope.all) return t.everything;
  return [...grant.scope.projects.map((p) => p.name), ...grant.scope.instances].join(", ");
}

function GrantRow({ grant }: { grant: SupportGrant }) {
  const t = useT(words);
  const toast = useToast();
  const withdraw = useCall(() => api.del(`/support-access/grants/${encodeURIComponent(grant.id)}`), [keys.support]);
  const active = grant.state === "active";
  return (
    <div className="settings-row">
      <div>
        <div className="name">
          {t.levels[grant.level] ?? grant.level}
          <Tag tone={active ? "positive" : undefined}>{t.states[grant.state] ?? grant.state}</Tag>
          {grant.sessions.length > 0 && <Tag tone="caution">{t.connectedNow(grant.sessions.map((s) => s.staff_name).join(", "))}</Tag>}
        </div>
        <div className="desc">{scopeText(t, grant)}</div>
        <div className="env">
          <span>{t.until(dateTime(grant.revoked_at ?? grant.expires_at))}</span>
          {grant.created_by && <span>· {t.by(grant.created_by)}</span>}
          {grant.ticket && <span>· {t.ticketOf(grant.ticket)}</span>}
        </div>
      </div>
      {active && (
        <Button size="sm" variant="ghost" icon={<Trash2 />} loading={withdraw.isPending}
          onClick={() => withdraw.mutate(undefined, { onSuccess: () => toast("success", t.withdrawn), onError: (e) => toast("error", t.notWithdrawn, errorText(e)) })}>
          {t.withdraw}
        </Button>
      )}
    </div>
  );
}

function GrantForm({ durations, levels }: { durations: number[]; levels: SupportLevel[] }) {
  const t = useT(words);
  const toast = useToast();
  const { data: projects } = useProjects();
  const { data: instances } = useInstances();
  const [level, setLevel] = useState<SupportLevel>("view");
  const [hours, setHours] = useState(durations[0] ?? 1);
  const [all, setAll] = useState(true);
  const [chosenProjects, setChosenProjects] = useState<string[]>([]);
  const [chosenInstances, setChosenInstances] = useState<string[]>([]);
  const [ticket, setTicket] = useState("");
  const outside = useMemo(
    () => (instances ?? []).filter((i) => !i.project || !chosenProjects.includes(i.project.id)).map((i) => i.name),
    [instances, chosenProjects],
  );
  const grant = useCall(
    () =>
      api.post("/support-access/grants", {
        level,
        hours,
        ticket: ticket.trim() || null,
        scope: all ? { all: true } : { all: false, projects: chosenProjects, instances: chosenInstances.filter((n) => outside.includes(n)) },
      }),
    [keys.support],
  );
  const toggle = (list: string[], value: string, set: (next: string[]) => void) =>
    set(list.includes(value) ? list.filter((v) => v !== value) : [...list, value]);
  const empty = !all && chosenProjects.length === 0 && chosenInstances.filter((n) => outside.includes(n)).length === 0;
  return (
    <div className="stack" style={{ padding: "0 20px 20px" }}>
      <Field label={t.level} hint={t.levelHints[level]}>
        <Segmented value={level} onChange={setLevel} options={levels.map((value) => ({ value, label: t.levels[value] ?? value }))} />
      </Field>
      <Field label={t.duration}>
        <Segmented value={String(hours)} onChange={(v) => setHours(Number(v))}
          options={durations.map((h) => ({ value: String(h), label: t.durations[h] ?? `${h} h` }))} />
      </Field>
      <Field label={t.scope} hint={all ? t.scopeAllHint : t.locksStay}>
        <Segmented value={all ? "all" : "chosen"} onChange={(v) => setAll(v === "all")}
          options={[{ value: "all", label: t.scopeAll }, { value: "chosen", label: t.scopeChosen }]} />
      </Field>
      {!all && (
        <>
          <Field label={t.projects}>
            <div className="stack-sm">
              {(projects?.projects ?? []).map((p) => (
                <label className="licence-check" key={p.id}>
                  <input type="checkbox" checked={chosenProjects.includes(p.id)} onChange={() => toggle(chosenProjects, p.id, setChosenProjects)} />
                  <span><bdi>{p.name}</bdi></span>
                </label>
              ))}
            </div>
          </Field>
          <Field label={t.instances}>
            <div className="stack-sm">
              {outside.map((name) => (
                <label className="licence-check" key={name}>
                  <input type="checkbox" checked={chosenInstances.includes(name)} onChange={() => toggle(chosenInstances, name, setChosenInstances)} />
                  <span className="mono" dir="ltr">{name}</span>
                </label>
              ))}
            </div>
          </Field>
          {empty && <Callout tone="info">{t.noneChosen}</Callout>}
        </>
      )}
      <Field label={t.ticket} hint={t.ticketHint}>
        <input className="input" dir="auto" maxLength={64} value={ticket} onChange={(e) => setTicket(e.target.value)} />
      </Field>
      <div className="row">
        <Button variant="primary" icon={<ShieldCheck />} disabled={empty} loading={grant.isPending}
          onClick={() => grant.mutate(undefined, {
            onSuccess: () => { toast("success", t.granted); setTicket(""); },
            onError: (e) => toast("error", t.notGranted, errorText(e)),
          })}>
          {t.grant}
        </Button>
      </div>
    </div>
  );
}

/** Settings › Support access (and the Support access page for administrators). */
export function SupportAccessSettings() {
  const t = useT(words);
  const { data, isPending } = useSupportAccess();
  if (isPending || !data) return <Skeleton height={300} />;
  const active = data.grants.filter((g) => g.state === "active");
  const past = data.grants.filter((g) => g.state !== "active");
  const levels = data.shell_enabled ? data.levels : data.levels.filter((l) => l !== "admin_shell");
  return (
    <div className="stack">
      <section className="card">
        <div className="card-head">
          <LifeBuoy />
          <div className="spacer">
            <h2>{t.grantTitle}</h2>
            <div className="sub">{t.text}</div>
          </div>
        </div>
        {data.unavailable ? (
          <div style={{ padding: "0 20px 20px" }}><Callout tone="info">{t.unavailable[data.unavailable]}</Callout></div>
        ) : (
          <GrantForm durations={data.durations} levels={levels} />
        )}
      </section>
      <section className="card">
        <div className="card-head">
          <ShieldCheck />
          <div className="spacer">
            <h2>{t.active}</h2>
            {data.channel.error && <div className="sub">{t.channelError(data.channel.error)}</div>}
          </div>
        </div>
        {active.length ? active.map((g) => <GrantRow key={g.id} grant={g} />) : <p className="small muted" style={{ padding: "0 20px 20px" }}>{t.none}</p>}
      </section>
      {past.length > 0 && (
        <section className="card">
          <div className="card-head"><div className="spacer"><h2>{t.past}</h2></div></div>
          {past.map((g) => <GrantRow key={g.id} grant={g} />)}
        </section>
      )}
    </div>
  );
}
