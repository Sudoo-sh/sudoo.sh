// Maintenance windows (src/sudoo/maintenance.py): the banner at the top of the panel and of the client portal, and the
// list with the time picker (the Maintenance page, the portal's Maintenance page). Times are shown in the panel's
// TIMEZONE, the one the presets are counted in; the custom time is typed in it too and sent without an offset.

import { CalendarClock, Check, Clock, MoonStar, ShieldCheck, Sun, Wrench } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router";
import { errorText } from "@/api/client";
import { useApproveMaintenance, useChooseWindow, useMaintenance, type WindowChoice } from "@/api/maintenance";
import type {
  ClientActionAnswer,
  MaintenanceItem,
  MaintenanceMeta,
  MaintenancePreset,
  PublicMaintenance,
} from "@/api/types";
import { Button, Callout, Empty, Field, Skeleton, Tag } from "@/components/app/basics";
import { useToast } from "@/components/app/overlay";
import { Input } from "@/components/ui/input";
import { useLocale, useT, type Locale } from "@/i18n";
import words from "@/i18n/maintenance";

type Words = (typeof words)["en"];

const OPEN = new Set(["proposed", "scheduled", "running"]);
const PRESET_ICON: Record<MaintenancePreset, typeof Sun> = { tonight: MoonStar, weekend: Sun, default: Clock };

/** A time in the panel's time zone ("29 Nov 2026, 02:00"), Western digits in both languages. */
export function zoned(value: string | null | undefined, zone: string, locale: Locale): string {
  if (!value) return "—";
  const tag = locale === "ar" ? "ar-u-nu-latn" : "en-GB";
  try {
    return new Date(value).toLocaleString(tag, { dateStyle: "medium", timeStyle: "short", timeZone: zone });
  } catch {
    return new Date(value).toLocaleString(tag, { dateStyle: "medium", timeStyle: "short" });
  }
}

/** "2d 4h", "3h 20min", "12min": what is left until `value`. */
function left(value: string, t: Words, now: number): string {
  const minutes = Math.max(0, Math.round((new Date(value).getTime() - now) / 60_000));
  const days = Math.floor(minutes / 1440);
  const hours = Math.floor((minutes % 1440) / 60);
  const rest = minutes % 60;
  if (days) return `${days}${t.units.d} ${hours}${t.units.h}`;
  if (hours) return `${hours}${t.units.h} ${rest}${t.units.m}`;
  return `${rest}${t.units.m}`;
}

/** Now, every 30 seconds: enough for a countdown in minutes. */
function useNow(): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => window.clearInterval(timer);
  }, []);
  return now;
}

const TONE: Record<string, "accent" | "positive" | "caution" | "negative" | undefined> = {
  proposed: "caution",
  scheduled: "accent",
  running: "accent",
  done: "positive",
  failed: "negative",
};

// ---------------------------------------------------------------- the banner

/** The soonest open maintenance, sliding in under the top bar; links to the Maintenance page. */
export function MaintenanceBanner({ client = false, to = "/maintenance" }: { client?: boolean; to?: string }) {
  const t = useT(words);
  const locale = useLocale();
  const now = useNow();
  const { data } = useMaintenance(client);
  const open = (data?.items ?? []).filter((item) => OPEN.has(item.status));
  const item = open.find((i) => i.status === "running") ?? open[0];
  if (!data || !item) return null;
  const what = item.title[locale];
  const text =
    item.status === "running"
      ? t.banner.running(what)
      : item.window
        ? t.banner.upcoming(
            what,
            `${zoned(item.window.start, data.meta.timezone, locale)} (${t.facts.countdown(left(item.window.start, t, now))})`,
          )
        : t.banner.choose(what);
  return (
    <div className="notices maintenance-banner" role="status" data-status={item.status}>
      <div className="maintenance-banner-row">
        <span className="maintenance-banner-icon" aria-hidden>
          <Wrench />
        </span>
        <span className="spacer">{text}</span>
        <Link to={to} viewTransition className="maintenance-banner-link">
          {t.banner.open}
        </Link>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- the list

export function MaintenanceList({ client = false }: { client?: boolean }) {
  const t = useT(words);
  const { data, isPending, error } = useMaintenance(client);
  if (isPending) return <Skeleton height={180} />;
  if (error) return <Callout tone="warning">{errorText(error)}</Callout>;
  const items = data?.items ?? [];
  if (!items.length)
    return (
      <div className="card">
        <Empty icon={<CalendarClock />} title={t.empty}>
          {t.emptyText}
        </Empty>
      </div>
    );
  return (
    <div className="maintenance-list">
      <p className="small muted">{t.facts.timezone(data.meta.timezone)}</p>
      {items.map((item) => (
        <MaintenanceCard key={item.id} item={item} meta={data.meta} client={client} />
      ))}
    </div>
  );
}

function windowWords(item: MaintenanceItem, t: Words): string | null {
  if (!item.window) return null;
  if (item.window.source === "default") return t.facts.chosenDefault;
  if (item.window.source === "account") return t.facts.chosenAccount(item.window.chosen_by ?? "");
  return item.window.chosen_by ? t.facts.chosenBy(item.window.chosen_by) : null;
}

function MaintenanceCard({ item, meta, client }: { item: MaintenanceItem; meta: MaintenanceMeta; client: boolean }) {
  const t = useT(words);
  const locale = useLocale();
  const now = useNow();
  const at = (value: string | null | undefined) => zoned(value, meta.timezone, locale);
  const choosable = item.status === "proposed" || item.status === "scheduled";
  const [picking, setPicking] = useState(false);
  const worksOn = item.instances.length ? item.instances.map((i) => i.name).join(", ") : t.scope[item.scope];
  const chosen = windowWords(item, t);
  return (
    <section className="card pad maintenance-card" data-status={item.status}>
      <div className="row-wrap">
        <h2 className="spacer">{item.title[locale]}</h2>
        <Tag tone={item.required ? undefined : "accent"}>{item.required ? t.facts.required : t.facts.optional}</Tag>
        <Tag tone={TONE[item.status]}>{t.status[item.status] ?? item.status}</Tag>
      </div>
      <dl className="maintenance-facts">
        <dt>{t.facts.why}</dt>
        <dd dir="auto">{item.reason[locale]}</dd>
        <dt>{t.facts.worksOn}</dt>
        <dd>{worksOn}</dd>
        <dt>{t.facts.window}</dt>
        <dd>
          {item.window ? (
            <>
              <b>{at(item.window.start)}</b> – {at(item.window.end)}
              {item.status === "scheduled" && (
                <span className="maintenance-countdown"> · {t.facts.countdown(left(item.window.start, t, now))}</span>
              )}
              {chosen && <span className="small muted"> · {chosen}</span>}
            </>
          ) : (
            t.facts.notChosen
          )}
          <div className="small muted">
            {t.facts.duration(item.estimated_minutes)} · {t.facts.between(at(item.earliest_start), at(item.deadline))}
          </div>
        </dd>
      </dl>
      {item.auto_at && item.default_start && (
        <Callout tone="info">{t.facts.autoAt(at(item.auto_at), at(item.default_start))}</Callout>
      )}
      {client && <ClientApproval item={item} />}
      {choosable &&
        (meta.may_choose ? (
          picking || item.status === "proposed" ? (
            <WindowPicker item={item} meta={meta} client={client} onDone={() => setPicking(false)} />
          ) : (
            <div className="row-wrap">
              <Button size="sm" icon={<CalendarClock />} onClick={() => setPicking(true)}>
                {t.choose.change}
              </Button>
            </div>
          )
        ) : (
          <p className="small muted">{client ? t.choose.clientOff : t.choose.viewOnly}</p>
        ))}
      {item.steps.length > 0 && (
        <div className="maintenance-steps">
          <h3 className="small">{t.steps.title}</h3>
          <ul>
            {item.steps.map((step, i) => (
              <li key={`${step.name}-${i}`} data-status={step.status}>
                <span className="maintenance-step-name">{step.name}</span>
                <Tag tone={TONE[step.status === "skipped" ? "proposed" : step.status]}>
                  {t.steps[step.status] ?? step.status}
                </Tag>
                {step.detail && <span className="small muted">{step.detail}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
      {item.detail && !OPEN.has(item.status) && <p className="small muted">{item.detail}</p>}
    </section>
  );
}

/** Tonight, this weekend, the default window, or another time between the bounds. */
function WindowPicker({
  item,
  meta,
  client,
  onDone,
}: {
  item: MaintenanceItem;
  meta: MaintenanceMeta;
  client: boolean;
  onDone: () => void;
}) {
  const t = useT(words);
  const locale = useLocale();
  const toast = useToast();
  const choose = useChooseWindow(client);
  const [custom, setCustom] = useState("");
  const at = (value: string | null | undefined) => zoned(value, meta.timezone, locale);
  const local = (value: string | null) => (value ?? "").slice(0, 16); // the panel's own clock time, without the offset
  const send = (choice: WindowChoice) =>
    choose.mutate(
      { id: item.id, choice },
      {
        onSuccess: (answer) => {
          const waiting = client && (answer.data as ClientActionAnswer).done === false;
          toast(waiting ? "info" : "success", waiting ? t.client.waiting : t.choose.saved);
          if (!waiting) onDone();
        },
        onError: (error) => toast("error", errorText(error)),
      },
    );
  return (
    <div className="maintenance-picker">
      <h3 className="small">{t.choose.title}</h3>
      <div className="maintenance-presets">
        {item.presets.map((preset) => {
          const Icon = PRESET_ICON[preset.id];
          return (
            <button
              key={preset.id}
              type="button"
              className="maintenance-preset"
              disabled={choose.isPending}
              onClick={() => send({ preset: preset.id })}
            >
              <Icon aria-hidden />
              <span className="maintenance-preset-name">{t.choose[preset.id]}</span>
              <span className="small muted">{at(preset.start)}</span>
            </button>
          );
        })}
      </div>
      {item.choose_from && item.choose_until && (
        <form
          className="maintenance-custom"
          onSubmit={(event) => {
            event.preventDefault();
            if (custom) send({ start: custom });
          }}
        >
          <Field label={t.choose.custom} hint={t.choose.range(at(item.choose_from), at(item.choose_until))}>
            <Input
              type="datetime-local"
              value={custom}
              min={local(item.choose_from)}
              max={local(item.choose_until)}
              onChange={(e) => setCustom(e.target.value)}
            />
          </Field>
          <Button type="submit" size="sm" icon={<Check />} loading={choose.isPending} disabled={!custom}>
            {t.choose.save}
          </Button>
        </form>
      )}
    </div>
  );
}

/** A client lets the maintenance run on its closed or locked projects (confirmed by e-mail). */
function ClientApproval({ item }: { item: MaintenanceItem }) {
  const t = useT(words);
  const toast = useToast();
  const approve = useApproveMaintenance();
  const held = (item.held ?? []).filter((p) => !item.approvals.includes(p));
  const approved = (item.projects ?? []).filter((p) => item.approvals.includes(p.id));
  if (!held.length && !approved.length) return null;
  const name = (id: string) => item.projects?.find((p) => p.id === id)?.name ?? id;
  return (
    <div className="maintenance-approval">
      {approved.map((p) => (
        <Tag key={p.id} tone="positive" icon={<ShieldCheck />}>
          {t.client.approved}: {p.name}
        </Tag>
      ))}
      {held.length > 0 && (item.status === "proposed" || item.status === "scheduled") && (
        <>
          <p className="small">{t.client.approveText}</p>
          {held.map((project) => (
            <Button
              key={project}
              size="sm"
              icon={<ShieldCheck />}
              loading={approve.isPending}
              onClick={() =>
                approve.mutate(
                  { id: item.id, project },
                  {
                    onSuccess: (answer) =>
                      toast(
                        answer.data.done ? "success" : "info",
                        answer.data.done ? t.client.approved : t.client.waiting,
                      ),
                    onError: (error) => toast("error", errorText(error)),
                  },
                )
              }
            >
              {t.client.approve}: {name(project)}
            </Button>
          ))}
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- the public status page (a small hook)

/** Planned maintenance on a project's public status page: what and when, in the visitor's own time zone. */
export function PublicMaintenanceNotice({ items }: { items: PublicMaintenance[] }) {
  const t = useT(words);
  const locale = useLocale();
  if (!items.length) return null;
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  return (
    <div className="public-maintenance">
      {items.map((item, i) => (
        <Callout key={`${item.kind}-${i}`} tone="info">
          {item.status === "running"
            ? t.public.running(item.title[locale])
            : t.public.upcoming(
                item.title[locale],
                zoned(item.window_start, zone, locale),
                zoned(item.window_end, zone, locale),
              )}
        </Callout>
      ))}
    </div>
  );
}
