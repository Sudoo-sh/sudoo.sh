// Plugins: the marketplace's catalogue, the installed plugins (versions, updates, licences) and the purchases of
// this server. Installing is one click for a free plugin: a job downloads and verifies the package, then the backend
// restarts on its own and a toast says when the plugin is ready. Buying opens a checkout session: the account owner
// pays on app.sudoo.sh in another tab, this page follows it and installs once paid. The panel never charges anything
// and never shows a plugin's code.

import { useQueryClient } from "@tanstack/react-query";
import { Download, ExternalLink, FileDown, MapPin, Puzzle, Receipt, Search, ShoppingBag, Sparkles, Star, Trash2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { NavLink, useNavigate, useParams } from "react-router";
import { api, ApiError, errorText } from "@/api/client";
import {
  keys,
  useBilling,
  useCatalog,
  useCatalogPlugin,
  useCategories,
  useJob,
  useMarketplace,
  usePlugins,
  usePurchases,
  useReceipts,
  useRecommendations,
} from "@/api/queries";
import type { CatalogPlugin, CheckoutSession, InstalledPlugin, Job, PricePlan, Recommendation } from "@/api/types";
import { JobLine, Monogram } from "@/components/InstanceCard";
import { JobDrawer } from "@/components/JobDrawer";
import { LinkForm, marketplaceProblem, PublisherBadge } from "@/components/Marketplace";
import { Page, PageHead, useCan } from "@/components/Shell";
import { Button, Callout, Empty, Field, Segmented, Skeleton, Status, Tag } from "@/components/app/basics";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Input } from "@/components/ui/input";
import { SelectBox } from "@/components/app/select";
import { Menu, MenuItem, MenuSeparator, Modal, useToast } from "@/components/app/overlay";
import { useT, words } from "@/i18n";
import common from "@/i18n/common";
import dict from "@/i18n/plugins";
import { ago, day, money, number } from "@/lib/format";
import { waitForBackend } from "@/lib/restart";

/** An amount without ".00" when it is whole: 10000, "usd" -> "$100". */
function price(cents: number, currency: string) {
  const text = money(cents, currency);
  return cents % 100 === 0 ? text.replace(/[.,]00(?=\D*$)/, "") : text;
}

/** "$100/year · $8.33/month · save 17%", "$250 once, no renewal", "$30 for 90 days · $10.15/month". */
export function planPrice(plan: PricePlan) {
  const t = words(dict).price;
  const parts: string[] = [];
  if (plan.kind === "one_time" || plan.perpetual) return t.onceNoRenewal(price(plan.price_cents, plan.currency));
  if (plan.kind === "fixed_term") parts.push(t.forDays(price(plan.price_cents, plan.currency), String(plan.period_days ?? "?")));
  else parts.push(plan.kind === "yearly" ? t.perYear(price(plan.price_cents, plan.currency)) : t.perMonth(price(plan.price_cents, plan.currency)));
  if (plan.kind !== "monthly" && plan.monthly_equivalent_cents != null) parts.push(t.perMonth(money(plan.monthly_equivalent_cents, plan.currency)));
  if (plan.savings_percent) parts.push(t.save(plan.savings_percent));
  return parts.join(" · ");
}

/** Free, or the cheapest plan (the hub lists plans cheapest first). */
function priceOf(plugin: Pick<CatalogPlugin, "tier"> & { plans?: PricePlan[] }) {
  const t = words(dict).price;
  if (plugin.tier === "free") return t.free;
  const plan = plugin.plans?.[0];
  if (!plan) return t.paid;
  const amount = price(plan.price_cents, plan.currency);
  if (plan.perpetual) return t.once(amount);
  return plan.kind === "yearly" ? t.perYear(amount) : plan.kind === "fixed_term" ? t.perDays(amount, String(plan.period_days ?? "?")) : t.perMonth(amount);
}

/** The plugin's icon (through the panel), or its monogram. */
function PluginIcon({ plugin, large }: { plugin: { name: string; icon_url?: string | null }; large?: boolean }) {
  const [broken, setBroken] = useState(false);
  if (!plugin.icon_url || broken) return <Monogram name={plugin.name} large={large} />;
  return <img className={`plugin-icon ${large ? "lg" : ""}`} src={plugin.icon_url} alt="" loading="lazy" onError={() => setBroken(true)} />;
}

/** "Made for your country", or the countries a plugin is made for. */
function Countries({ plugin }: { plugin: { countries?: string[]; local?: boolean | null } }) {
  const t = useT(dict);
  if (plugin.local) return <Tag tone="accent" icon={<MapPin />}>{t.madeForYou}</Tag>;
  if (!plugin.countries?.length) return null;
  const shown = plugin.countries.slice(0, 3).join(", ") + (plugin.countries.length > 3 ? ` +${plugin.countries.length - 3}` : "");
  return <Tag icon={<MapPin />}>{shown}</Tag>;
}

/** A new key for each press of Buy; the same key is sent again if that one request is retried. */
function newIdempotencyKey() {
  const bytes = crypto.getRandomValues(new Uint8Array(18));
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

const reasonText = (reason: string | null) => (reason ? words(dict).reasons[reason] ?? reason.replace(/_/g, " ") : "");

// ---------------------------------------------------------------- the running plugin job, then the restart

type Verb = "installed" | "updated" | "uninstalled";
interface Work {
  jobId: string;
  name: string;
  verb: Verb;
}

function useInstaller() {
  const client = useQueryClient();
  const toast = useToast();
  const [work, setWork] = useState<Work | null>(null);
  const t = useT(dict).work;
  const start = (job: Job, name: string, verb: Verb = "installed") => setWork({ jobId: job.id, name, verb });
  const finish = async (job: Job) => {
    if (!work) return;
    if (job.status === "failed") {
      toast("error", t.notDone[work.verb](work.name), job.error ?? t.openDetails);
      return;
    }
    toast("info", t.restarting, t.restartingText);
    const back = await waitForBackend();
    await client.invalidateQueries();
    setWork(null);
    if (back) toast("success", t.done[work.verb](work.name));
    else toast("error", t.slow, t.slowText);
  };
  return { work, start, finish, clear: () => setWork(null) };
}

function WorkCard({ work, onFinished, onClear }: { work: Work; onFinished: (job: Job) => void; onClear: () => void }) {
  const { data: job } = useJob(work.jobId);
  const [details, setDetails] = useState(false);
  const reported = useRef<string | null>(null);
  const t = useT(dict).work;
  useEffect(() => {
    if (job && (job.status === "success" || job.status === "failed") && reported.current !== job.status) {
      reported.current = job.status;
      onFinished(job);
    }
  }, [job, onFinished]);
  return (
    <div className="card pad rise stack-sm">
      <div className="row">
        <Puzzle className="faint" />
        <bdi className="spacer truncate" style={{ fontWeight: 600 }}>{work.name}</bdi>
        <Button size="sm" variant="ghost" onClick={() => setDetails(true)}>{t.details}</Button>
        {job?.status === "failed" && <Button size="sm" variant="ghost" onClick={onClear}>{t.dismiss}</Button>}
      </div>
      {job ? (
        job.status === "success" ? (
          <div className="row small muted"><span className="spinner" /> {t.loading}</div>
        ) : (
          <JobLine job={job} />
        )
      ) : (
        <Skeleton height={24} />
      )}
      {job?.status === "failed" && job.error && <Callout tone="danger">{job.error}</Callout>}
      {details && <JobDrawer id={work.jobId} onClose={() => setDetails(false)} />}
    </div>
  );
}

// ---------------------------------------------------------------- catalogue

const ACTION_TONE: Partial<Record<CatalogPlugin["action"], "positive" | "accent" | "caution">> = {
  update: "accent",
  installed: "positive",
  renew: "caution",
  incompatible: "caution",
};

function PluginCard({ plugin, onOpen }: { plugin: CatalogPlugin; onOpen: () => void }) {
  const t = useT(dict);
  const label = t.actionTags[plugin.action] ?? "";
  const tone = ACTION_TONE[plugin.action];
  return (
    <button type="button" className="card link plugin-card" onClick={onOpen}>
      <div className="pc-head">
        <PluginIcon plugin={plugin} />
        <div className="pc-titles">
          <bdi className="pc-name truncate">{plugin.name}</bdi>
          <PublisherBadge publisher={plugin.publisher} />
        </div>
      </div>
      <p className="pc-text">{plugin.description}</p>
      <div className="pc-foot">
        <Tag tone={plugin.tier === "free" ? "positive" : undefined}>{priceOf(plugin)}</Tag>
        <span className="row xsmall faint">
          <Star /> {plugin.rating_count ? number(plugin.avg_rating, 1) : "—"}
        </span>
        <span className="xsmall faint">{t.installs(plugin.install_count, number(plugin.install_count))}</span>
        <span className="spacer" />
        {label ? <Tag tone={tone}>{label}</Tag> : <Countries plugin={plugin} />}
      </div>
    </button>
  );
}

function PluginSheet({
  plugin,
  installed,
  busy,
  onClose,
  onStarted,
}: {
  plugin: CatalogPlugin;
  installed?: InstalledPlugin;
  busy: boolean;
  onClose: () => void;
  onStarted: (job: Job, name: string, verb?: Verb) => void;
}) {
  const { data: detail } = useCatalogPlugin(plugin.id);
  const all = useT(dict);
  const t = all.sheet;
  const c = useT(common).common;
  const can = useCan();
  const manage = can("settings.manage");
  const paid = plugin.tier === "paid" && (plugin.action === "buy" || plugin.action === "renew");
  const plans = plugin.plans?.length ? plugin.plans : detail?.plans ?? [];
  const { data: billing, error: billingError } = useBilling(manage && paid);
  const toast = useToast();
  const client = useQueryClient();
  const [plan, setPlan] = useState<string>("");
  const [step, setStep] = useState<"idle" | "working" | "paying">("idle");
  const [session, setSession] = useState<CheckoutSession | null>(null);
  const [ended, setEnded] = useState<string | null>(null);
  const chosen = plans.find((p) => p.id === plan) ?? plans[0];
  const repair = installed?.status === "modified";
  const action = repair ? "install" : plugin.action;

  const install = async (verb: Verb = "installed") => {
    const job = await api.post<Job>("/plugins", { id: plugin.id });
    onStarted(job, plugin.name, verb);
    onClose();
  };

  // a checkout session the owner pays on app.sudoo.sh: follow it every 4 seconds and when this tab gets the focus
  // back, while it is open, processing or on Stripe; install once it succeeded
  useEffect(() => {
    if (step !== "paying" || !session) return;
    let stop = false;
    let checking = false;
    const check = async () => {
      if (stop || checking) return;
      checking = true;
      try {
        const current = await api.get<CheckoutSession>(`/marketplace/checkout-sessions/${session.id}`);
        if (stop) return;
        setSession(current);
        if (current.status === "succeeded") {
          stop = true;
          toast("success", t.paymentReceived, t.installing(plugin.name));
          void client.invalidateQueries({ queryKey: keys.purchases });
          try {
            await install();
          } catch (err) {
            toast("error", t.paidNotInstalled(plugin.name), `${errorText(err)}${t.installFromMarketplace}`);
            setStep("idle");
          }
        } else if (current.status === "expired" || current.status === "cancelled" || current.status === "failed") {
          stop = true;
          setEnded(
            current.status === "expired"
              ? t.expired
              : current.status === "cancelled"
                ? t.cancelled
                : t.failed(reasonText(current.reason)),
          );
          setStep("idle");
        }
      } catch (err) {
        if (!(err instanceof ApiError && err.status === 503)) {
          stop = true;
          toast("error", t.lostTrack, errorText(err));
          setStep("idle");
        }
      } finally {
        checking = false;
      }
    };
    const timer = setInterval(() => void check(), 4000);
    const focus = () => document.visibilityState === "visible" && void check();
    document.addEventListener("visibilitychange", focus);
    window.addEventListener("focus", focus);
    return () => {
      stop = true;
      clearInterval(timer);
      document.removeEventListener("visibilitychange", focus);
      window.removeEventListener("focus", focus);
    };
  }, [step, session?.id]);

  const installNow = async () => {
    setStep("working");
    try {
      await install(action === "update" ? "updated" : "installed");
    } catch (err) {
      toast("error", t.notInstalled(plugin.name), errorText(err));
      setStep("idle");
    }
  };

  const buy = async () => {
    if (!chosen) return;
    setStep("working");
    setEnded(null);
    // opened now, while the click still counts, so the browser does not block it; sent to the payment page once
    // the hub answers
    const tab = window.open("about:blank", "_blank");
    try {
      const body = { plugin_id: plugin.id, plan_id: chosen.id, idempotency_key: newIdempotencyKey() };
      const opened = await api.post<CheckoutSession>("/marketplace/checkout-sessions", body);
      if (opened.status === "succeeded") {
        tab?.close();
        setSession(opened);
        setStep("paying");
        return;
      }
      if (!opened.url) throw new Error(t.noPaymentPage);
      if (tab) {
        tab.opener = null;
        tab.location.href = opened.url;
      } else {
        window.open(opened.url, "_blank", "noopener");
      }
      setSession(opened);
      setStep("paying");
    } catch (err) {
      tab?.close();
      toast("error", t.notBought, errorText(err));
      setStep("idle");
    }
  };

  const button = () => {
    if (!manage) return null;
    if (action === "installed") return <Tag tone="positive">{t.installedVersion(installed?.version ?? plugin.installed_version ?? "")}</Tag>;
    if (action === "incompatible") return <Button disabled>{t.needsNewer}</Button>;
    if (action === "install" || action === "update")
      return (
        <Button variant="primary" icon={<Download />} loading={step === "working"} disabled={busy} onClick={installNow}>
          {repair ? t.installAgain : action === "update" ? t.updateTo(plugin.compatible_version?.version ?? "") : t.install}
        </Button>
      );
    return (
      <Button variant="primary" icon={<ShoppingBag />} loading={step === "working"} disabled={busy || !billing?.can_buy || !chosen || step === "paying"} onClick={buy}>
        {action === "renew" ? t.renew : t.buy}
      </Button>
    );
  };

  const waiting =
    session?.status === "processing"
      ? t.processing
      : session?.status === "open" && session.reason
        ? t.tryAnotherCard(reasonText(session.reason))
        : t.waiting;

  const version = plugin.compatible_version ?? plugin.latest_version;
  return (
    <Modal
      wide
      title={<span className="row"><PluginIcon plugin={plugin} large /> <bdi>{plugin.name}</bdi></span>}
      description={<PublisherBadge publisher={plugin.publisher} />}
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>{c.close}</Button>
          {button()}
        </>
      }
    >
      <div className="stack">
        <p>{plugin.description}</p>
        <div className="row-wrap">
          <Tag>{all.types[plugin.type] ?? plugin.type}</Tag>
          {version && <Tag mono><span dir="ltr">v{version.version}</span></Tag>}
          {version?.min_sudoo_version && <Tag mono><span dir="ltr">Sudoo.sh {version.min_sudoo_version}+</span></Tag>}
          <Tag tone={plugin.tier === "free" ? "positive" : undefined}>{priceOf({ ...plugin, plans })}</Tag>
          <Countries plugin={plugin} />
        </div>
        {(detail?.permissions?.length ?? 0) > 0 && (
          <Field label={t.asksTo}>
            <div className="row-wrap">{detail!.permissions.map((p) => <Tag key={p} mono><span dir="ltr">{p}</span></Tag>)}</div>
          </Field>
        )}
        {(plugin.requires?.length ?? 0) > 0 && (
          <Field label={t.needs}>
            <div className="row-wrap">{plugin.requires.map((r) => <Tag key={r.id} mono><span dir="ltr">{r.id} {r.version ?? ""}</span></Tag>)}</div>
          </Field>
        )}
        {plugin.license?.perpetual ? (
          <p className="small muted">{t.licencePerpetual(all.licenceStates[plugin.license.state] ?? plugin.license.state)}</p>
        ) : plugin.license?.expires_at ? (
          <p className="small muted">{t.licenceEnds(all.licenceStates[plugin.license.state] ?? plugin.license.state, day(plugin.license.expires_at))}</p>
        ) : null}
        {paid && manage && (
          <>
            {plans.length > 0 && (
              <Field label={t.plan}>
                <RadioGroup className="plan-list" value={chosen?.id ?? ""} onValueChange={setPlan} disabled={step === "paying"}>
                  {plans.map((p) => (
                    <label key={p.id} className="plan">
                      <RadioGroupItem value={p.id} />
                      <span className="stack-sm" style={{ gap: 0 }}>
                        <span className="plan-price">{all.plans[p.kind] ?? p.kind}</span>
                        <span className="small muted">{planPrice(p)}</span>
                      </span>
                    </label>
                  ))}
                </RadioGroup>
              </Field>
            )}
            {chosen && (
              <p className="small muted">
                {chosen.perpetual
                  ? t.paidOnce
                  : chosen.kind === "fixed_term"
                    ? t.paidForTerm
                    : t.renewed}{" "}
                {t.ownerPays}
              </p>
            )}
            {billingError && <Callout tone="warning">{errorText(billingError)}</Callout>}
            {billing?.reason === "link_required" && (
              <Callout tone="info">
                <div className="stack-sm">
                  <span>{t.linkFirst}</span>
                  <LinkForm onLinked={() => void client.invalidateQueries({ queryKey: ["marketplace", "billing"] })} />
                </div>
              </Callout>
            )}
            {billing?.reason === "fingerprint_mismatch" && (
              <Callout tone="danger">
                {t.flagged}{" "}
                {billing.manage_url && <a href={billing.manage_url} target="_blank" rel="noreferrer noopener">{t.acceptMachine} <ExternalLink className="flip-rtl" /></a>}
              </Callout>
            )}
            {billing?.reason === "payments_disabled" && <Callout tone="warning">{t.paymentsOff}</Callout>}
            {billing?.reason === "installation_suspended" && <Callout tone="danger">{t.suspended}</Callout>}
            {step === "paying" && session && (
              <Callout tone={session.status === "open" && session.reason ? "warning" : "info"}>
                <div className="stack-sm">
                  <div className="row"><span className="spinner" /> {waiting}{t.installsWhenPaid}</div>
                  {session.url && (
                    <a href={session.url} target="_blank" rel="noreferrer noopener">{t.openAgain} <ExternalLink className="flip-rtl" /></a>
                  )}
                </div>
              </Callout>
            )}
            {ended && step === "idle" && (
              <Callout tone="warning">
                <div className="stack-sm">
                  <span>{ended}</span>
                  <span className="small">{t.pressAgain}</span>
                </div>
              </Callout>
            )}
          </>
        )}
        {!manage && <p className="small muted">{t.superAdminOnly}</p>}
        <p className="xsmall faint">{t.signed}</p>
      </div>
    </Modal>
  );
}

function Catalogue({ installed, busy, onStarted }: { installed: InstalledPlugin[]; busy: boolean; onStarted: (job: Job, name: string, verb?: Verb) => void }) {
  const { data: status } = useMarketplace();
  const all = useT(dict);
  const t = all.catalogue;
  const usable = !!status?.enabled;
  const [typed, setTyped] = useState("");
  const [q, setQ] = useState("");
  const [type, setType] = useState("");
  const [tier, setTier] = useState<"" | "free" | "paid">("");
  const [sort, setSort] = useState("trusted");
  const [open, setOpen] = useState<CatalogPlugin | null>(null);
  useEffect(() => {
    const timer = setTimeout(() => setQ(typed.trim()), 250);
    return () => clearTimeout(timer);
  }, [typed]);
  const query = useMemo(() => {
    const params = new URLSearchParams({ sort, limit: "60" });
    if (q) params.set("q", q);
    if (type) params.set("type", type);
    if (tier) params.set("tier", tier);
    return params.toString();
  }, [q, type, tier, sort]);
  const { data: plugins, error, isPending } = useCatalog(query, usable);
  const { data: categories } = useCategories(usable);
  const { data: recommended } = useRecommendations(usable);
  const byId = Object.fromEntries(installed.map((p) => [p.id, p]));
  const browsing = !q && !type && !tier;
  /** A suggested plugin as the sheet needs it: the catalogue's entry when loaded, else built from the suggestion. */
  const openSuggestion = (pick: Recommendation) => {
    const known = plugins?.find((p) => p.id === pick.plugin.id);
    setOpen(
      known ?? {
        ...pick.plugin,
        plans: [],
        latest_version: null,
        compatible_version: null,
        installed_version: null,
        license: null,
        action: pick.plugin.tier === "free" ? "install" : "buy",
      },
    );
  };

  if (!usable) return null;
  return (
    <div className="stack">
      <div className="toolbar">
        <div className="input-group">
          <Search />
          <Input placeholder={t.search} aria-label={t.search} value={typed} onChange={(e) => setTyped(e.target.value)} />
        </div>
        <SelectBox style={{ width: "auto" }} aria-label={t.category} value={type} onValueChange={(value) => setType(value)}>
          <option value="">{t.allCategories}</option>
          {(categories ?? []).map((c) => (
            <option key={c.type} value={c.type}>{all.types[c.type] ?? c.type} ({number(c.count)})</option>
          ))}
        </SelectBox>
        <Segmented value={tier} onChange={setTier} options={[{ value: "", label: t.all }, { value: "free", label: all.price.free }, { value: "paid", label: all.price.paid }]} />
        <span className="spacer" />
        <SelectBox style={{ width: "auto" }} aria-label={t.order} value={sort} onValueChange={(value) => setSort(value)}>
          {(Object.keys(t.sort) as (keyof typeof t.sort)[]).map((key) => <option key={key} value={key}>{t.sort[key]}</option>)}
        </SelectBox>
      </div>
      {browsing && !!recommended?.length && (
        <section className="stack-sm rise" aria-labelledby="recommended">
          <h2 id="recommended" className="row small"><Sparkles className="faint" /> {t.recommended}</h2>
          <div className="reco-row">
            {recommended.map((pick) => (
              <button key={pick.plugin.id} type="button" className="card link plugin-card" onClick={() => openSuggestion(pick)}>
                <div className="pc-head">
                  <PluginIcon plugin={pick.plugin} />
                  <div className="pc-titles">
                    <bdi className="pc-name truncate">{pick.plugin.name}</bdi>
                    <PublisherBadge publisher={pick.plugin.publisher} />
                  </div>
                </div>
                <span className="reco-why">{pick.reasons[0] ?? t.suggested}</span>
                <div className="pc-foot">
                  <Tag tone={pick.plugin.tier === "free" ? "positive" : undefined}>{pick.plugin.tier === "free" ? all.price.free : all.price.paid}</Tag>
                  <span className="spacer" />
                  <Countries plugin={pick.plugin} />
                </div>
              </button>
            ))}
          </div>
        </section>
      )}
      {isPending ? (
        <div className="plugin-grid">{[0, 1, 2].map((i) => <Skeleton key={i} height={168} />)}</div>
      ) : error ? (
        <Callout tone="warning">{errorText(error)}</Callout>
      ) : !plugins?.length ? (
        <div className="card"><Empty icon={<Puzzle />} title={t.noneFound}>{t.noneFoundText}</Empty></div>
      ) : (
        <div className="plugin-grid rise">
          {plugins.map((plugin) => (
            <PluginCard key={plugin.id} plugin={plugin} onOpen={() => setOpen(plugin)} />
          ))}
        </div>
      )}
      {open && <PluginSheet plugin={open} installed={byId[open.id]} busy={busy} onClose={() => setOpen(null)} onStarted={onStarted} />}
    </div>
  );
}

// ---------------------------------------------------------------- installed

const STATUS: Record<InstalledPlugin["status"], string> = {
  active: "online",
  disabled: "stopped",
  modified: "failed",
  blocked: "failed",
  withheld: "failed",
  switched_off: "paused",
  unlicensed: "paused",
};
const PHASE: Record<string, "positive" | "caution" | "negative"> = {
  active: "positive",
  perpetual: "positive",
  withheld: "negative",
  expiring: "caution",
  grace: "caution",
  ended: "negative",
  no_lease: "negative",
  unlicensed: "negative",
};

function Installed({ plugins, busy, onStarted }: { plugins: InstalledPlugin[]; busy: boolean; onStarted: (job: Job, name: string, verb?: Verb) => void }) {
  const can = useCan();
  const t = useT(dict).installed;
  const c = useT(common).common;
  const toast = useToast();
  const client = useQueryClient();
  const [removing, setRemoving] = useState<InstalledPlugin | null>(null);
  const [confirm, setConfirm] = useState("");
  const manage = can("settings.manage");

  const switchTo = async (plugin: InstalledPlugin, on: boolean) => {
    try {
      await api.post(`/plugins/${plugin.id}/${on ? "enable" : "disable"}`);
      toast("info", t.switched(plugin.name, on), t.restartsToApply);
      await waitForBackend();
      await client.invalidateQueries();
    } catch (error) {
      toast("error", t.notChanged, errorText(error));
    }
  };
  const update = async (plugin: InstalledPlugin) => {
    try {
      onStarted(await api.post<Job>(`/plugins/${plugin.id}/update`), plugin.name, "updated");
    } catch (error) {
      toast("error", t.notUpdated, errorText(error));
    }
  };
  const uninstall = async () => {
    if (!removing) return;
    try {
      onStarted(await api.del<Job>(`/plugins/${removing.id}`, { confirm }), removing.name, "uninstalled");
      setRemoving(null);
      setConfirm("");
    } catch (error) {
      toast("error", t.notUninstalled, errorText(error));
    }
  };

  if (!plugins.length)
    return (
      <div className="card">
        <Empty icon={<Puzzle />} title={t.none}>{t.noneText}</Empty>
      </div>
    );
  return (
    <div className="card rise">
      <Table>
        <TableHeader>
          <TableRow><TableHead>{t.columns.plugin}</TableHead><TableHead>{t.columns.version}</TableHead><TableHead>{t.columns.status}</TableHead><TableHead>{t.columns.licence}</TableHead><TableHead /></TableRow>
        </TableHeader>
        <TableBody>
          {plugins.map((p) => {
            const cls = STATUS[p.status] ?? "unknown";
            const label = t.status[p.status] ?? p.status;
            const phase = p.licence?.phase ? [t.phase[p.licence.phase] ?? p.licence.phase, PHASE[p.licence.phase]] as const : null;
            return (
              <TableRow key={p.id}>
                <TableCell>
                  <div className="row">
                    <PluginIcon plugin={p} />
                    <div style={{ minWidth: 0 }}>
                      <div className="truncate"><bdi>{p.name}</bdi></div>
                      <div className="xsmall faint"><PublisherBadge publisher={p.publisher} /></div>
                    </div>
                  </div>
                </TableCell>
                <TableCell className="nowrap">
                  <span className="mono small">v{p.version}</span>
                  {p.update_available && manage && (
                    <> <Button size="sm" variant="soft" disabled={busy} onClick={() => update(p)}>{t.updateTo(p.latest_version ?? "")}</Button></>
                  )}
                </TableCell>
                <TableCell>
                  <Status status={p.restart_pending ? "starting" : cls} label={p.restart_pending ? t.waitingRestart : label} />
                </TableCell>
                <TableCell className="small">
                  {p.tier === "free" ? (
                    <span className="faint">{t.free}</span>
                  ) : (
                    <div className="stack-sm" style={{ gap: 2, alignItems: "flex-start" }}>
                      {phase && <Tag tone={phase[1]}>{phase[0]}</Tag>}
                      {p.licence?.expires_at && <span className="xsmall faint">{t.ends(day(p.licence.expires_at))}{p.licence.grace_until ? t.stops(day(p.licence.grace_until)) : ""}</span>}
                      {p.licence?.perpetual && <span className="xsmall faint">{t.oneTime}</span>}
                    </div>
                  )}
                </TableCell>
                <TableCell className="actions">
                  {manage && (
                    <Menu trigger={(toggle) => <Button size="sm" variant="ghost" onClick={toggle} disabled={busy}>{t.manage}</Button>}>
                      {(close) => (
                        <>
                          {p.enabled ? (
                            <MenuItem onSelect={() => { close(); void switchTo(p, false); }}>{t.switchOff}</MenuItem>
                          ) : (
                            <MenuItem disabled={p.status === "modified"} onSelect={() => { close(); void switchTo(p, true); }}>{t.switchOn}</MenuItem>
                          )}
                          <MenuSeparator />
                          <MenuItem danger onSelect={() => { close(); setRemoving(p); }}>
                            <Trash2 /> {t.uninstall}
                          </MenuItem>
                        </>
                      )}
                    </Menu>
                  )}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
      {removing && (
        <Modal
          title={t.uninstallTitle(removing.name)}
          description={t.uninstallText}
          onClose={() => setRemoving(null)}
          footer={
            <>
              <Button onClick={() => setRemoving(null)}>{c.cancel}</Button>
              <Button variant="danger" icon={<Trash2 />} disabled={confirm !== removing.id} onClick={uninstall}>{t.uninstall}</Button>
            </>
          }
        >
          <Field label={<>{t.typeToConfirm(<code key="id">{removing.id}</code>)}</>}>
            <Input className="mono" dir="ltr" autoFocus value={confirm} onChange={(e) => setConfirm(e.target.value)} />
          </Field>
          {removing.tier === "paid" && <p className="small muted" style={{ marginTop: 12 }}>{t.subscriptionStays}</p>}
        </Modal>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- purchases

const PURCHASE_TONE: Record<string, "positive" | "caution" | "negative"> = {
  succeeded: "positive",
  open: "caution",
  processing: "caution",
  checkout_required: "caution",
  failed: "negative",
};

function Purchases() {
  const { data: status } = useMarketplace();
  const usable = !!status?.enabled;
  const { data: receipts, error: receiptsError, isPending: receiptsPending } = useReceipts(usable);
  const { data: purchases, error, isPending } = usePurchases(usable);
  const { data: billing } = useBilling(usable);
  const all = useT(dict);
  const t = all.purchases;
  if (!usable) return null;
  return (
    <div className="stack">
      <div className="card rise">
        <div className="card-head">
          <Receipt />
          <div className="spacer">
            <h2>{t.receipts}</h2>
            <div className="sub">{t.receiptsText}</div>
          </div>
          {billing?.manage_url && (
            <Button asChild variant="ghost" size="sm"><a href={billing.manage_url} target="_blank" rel="noreferrer noopener">
              app.sudoo.sh <ExternalLink className="flip-rtl" />
            </a></Button>
          )}
          {!!receipts?.length && (
            <Button asChild size="sm"><a href="/api/v1/marketplace/receipts?format=csv" download>
              <FileDown /> {t.exportCsv}
            </a></Button>
          )}
        </div>
        {receiptsPending ? (
          <div className="card-body"><Skeleton height={100} /></div>
        ) : receiptsError ? (
          <div className="card-body"><Callout tone="warning">{errorText(receiptsError)}</Callout></div>
        ) : !receipts?.length ? (
          <Empty icon={<Receipt />} title={t.noReceipts}>{t.noReceiptsText}</Empty>
        ) : (
          <Table>
            <TableHeader>
              <TableRow><TableHead>{t.columns.number}</TableHead><TableHead>{t.columns.date}</TableHead><TableHead>{t.columns.plugin}</TableHead><TableHead>{t.columns.plan}</TableHead><TableHead>{t.columns.askedBy}</TableHead><TableHead className="num">{t.columns.total}</TableHead></TableRow>
            </TableHeader>
            <TableBody>
              {receipts.map((r) => (
                <TableRow key={r.id}>
                  <TableCell className="small nowrap"><span className="mono">{r.number}</span>{r.kind === "credit_note" && <span className="faint">{t.creditNote}</span>}</TableCell>
                  <TableCell className="small nowrap">{day(r.issued_at)}</TableCell>
                  <TableCell><bdi>{r.plugin_name ?? r.plugin_id}</bdi>{r.payment_kind === "renewal" && <span className="faint small">{t.renewal}</span>}</TableCell>
                  <TableCell className="small">{r.plan_kind ? all.plans[r.plan_kind] ?? r.plan_kind : "—"}</TableCell>
                  <TableCell className="small muted"><bdi>{r.requested_by_name ?? "—"}</bdi></TableCell>
                  <TableCell className="num">{money(r.kind === "credit_note" ? -Math.abs(r.total_cents) : r.total_cents, r.currency)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </div>
      <div className="card rise">
        <div className="card-head">
          <ShoppingBag />
          <div className="spacer">
            <h2>{t.title}</h2>
            <div className="sub">{t.text}</div>
          </div>
          {!!purchases?.length && (
            <Button asChild size="sm"><a href="/api/v1/marketplace/checkout-sessions?format=csv" download>
              <FileDown /> {t.exportCsv}
            </a></Button>
          )}
        </div>
        {isPending ? (
          <div className="card-body"><Skeleton height={100} /></div>
        ) : error ? (
          <div className="card-body"><Callout tone="warning">{errorText(error)}</Callout></div>
        ) : !purchases?.length ? (
          <Empty icon={<ShoppingBag />} title={t.none}>{t.noneText}</Empty>
        ) : (
          <Table>
            <TableHeader>
              <TableRow><TableHead>{t.columns.date}</TableHead><TableHead>{t.columns.plugin}</TableHead><TableHead>{t.columns.plan}</TableHead><TableHead>{t.columns.askedBy}</TableHead><TableHead>{t.columns.status}</TableHead><TableHead className="num">{t.columns.amount}</TableHead></TableRow>
            </TableHeader>
            <TableBody>
              {purchases.map((p) => {
                const label = t.status[p.status ?? ""] ?? p.status ?? "—";
                const tone = PURCHASE_TONE[p.status ?? ""];
                return (
                  <TableRow key={p.id}>
                    <TableCell className="small nowrap">{day(p.created_at)} <span className="faint">· {ago(p.created_at)}</span></TableCell>
                    <TableCell><bdi>{p.plugin_name}</bdi></TableCell>
                    <TableCell className="small">{p.plan_kind ? all.plans[p.plan_kind] ?? p.plan_kind : "—"}</TableCell>
                    <TableCell className="small muted"><bdi>{p.requested_by?.name ?? "—"}</bdi></TableCell>
                    <TableCell>
                      <div className="stack-sm" style={{ gap: 2, alignItems: "flex-start" }}>
                        <Tag tone={tone}>{label}</Tag>
                        {p.reason && p.status !== "succeeded" && <span className="xsmall faint">{reasonText(p.reason)}</span>}
                        {p.receipt_number && <span className="xsmall faint mono">{p.receipt_number}</span>}
                        {p.url && <a className="xsmall" href={p.url} target="_blank" rel="noreferrer noopener">{t.paymentPage} <ExternalLink className="flip-rtl" /></a>}
                      </div>
                    </TableCell>
                    <TableCell className="num">{money(p.amount_cents, p.currency)}</TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- the page

const TABS = ["", "installed", "purchases"];

export function PluginsPage() {
  const { tab = "" } = useParams();
  const navigate = useNavigate();
  const { data: status } = useMarketplace();
  const { data: installed, isPending } = usePlugins();
  const installer = useInstaller();
  const t = useT(dict).page;
  const problem = marketplaceProblem(status);
  const updates = installed?.filter((p) => p.update_available).length ?? 0;
  const started = (job: Job, name: string, verb?: Verb) => {
    installer.start(job, name, verb);
    if (tab !== "installed") navigate("/plugins/installed", { viewTransition: true });
  };
  return (
    <Page>
      <PageHead title={t.title}>
        {t.text}
      </PageHead>
      {problem && <Callout tone={problem.tone}>{problem.text}</Callout>}
      {installer.work && <WorkCard work={installer.work} onFinished={installer.finish} onClear={installer.clear} />}
      <nav className="tabs">
        {TABS.map((id) => (
          <NavLink key={id} to={`/plugins${id ? `/${id}` : ""}`} end viewTransition className={({ isActive }) => (isActive ? "active" : "")}>
            {t.tabs[id]}
            {id === "installed" && !!installed?.length && <span className="faint tabular">{number(installed.length)}</span>}
            {id === "installed" && updates > 0 && <Tag tone="accent">{t.updates(updates)}</Tag>}
          </NavLink>
        ))}
      </nav>
      <div className="rise tab-panel" key={tab}>
        {tab === "installed" ? (
          isPending ? <Skeleton height={160} /> : <Installed plugins={installed ?? []} busy={!!installer.work} onStarted={started} />
        ) : tab === "purchases" ? (
          <Purchases />
        ) : (
          <Catalogue installed={installed ?? []} busy={!!installer.work} onStarted={started} />
        )}
      </div>
    </Page>
  );
}
