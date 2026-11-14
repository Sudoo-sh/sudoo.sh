// Plugins: the marketplace's catalogue, the installed plugins (versions, updates, licences) and the purchases of
// this server. Installing is one click for a free plugin: a job downloads and verifies the package, then the backend
// restarts on its own and a toast says when the plugin is ready. The panel never shows a plugin's code.

import { useQueryClient } from "@tanstack/react-query";
import { Download, ExternalLink, FileDown, Puzzle, Receipt, Search, ShoppingBag, Star, Trash2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { NavLink, useNavigate, useParams } from "react-router";
import { api, ApiError, errorText } from "@/api/client";
import {
  useCatalog,
  useCatalogPlugin,
  useCategories,
  useJob,
  useMarketplace,
  usePlugins,
  usePurchases,
} from "@/api/queries";
import type { CatalogPlugin, InstalledPlugin, Job, PricePlan, Purchase } from "@/api/types";
import { JobLine, Monogram } from "@/components/InstanceCard";
import { JobDrawer } from "@/components/JobDrawer";
import { LinkForm, marketplaceProblem, PublisherBadge } from "@/components/Marketplace";
import { Page, PageHead, useCan } from "@/components/Shell";
import { Button, Callout, Empty, Field, Segmented, Skeleton, Status, Tag } from "@/components/ui/basics";
import { Menu, Modal, useToast } from "@/components/ui/overlay";
import { ago, day, money, number } from "@/lib/format";
import { waitForBackend } from "@/lib/restart";

const TYPE_LABEL: Record<string, string> = {
  auth: "Sign-in",
  second_factor: "Second factor",
  mailer: "E-mail",
  exposure: "Exposure",
  update_trigger: "Code updates",
  addon_source: "Add-on source",
  storage: "Storage",
  rate_limiter: "Rate limits",
  backup: "Backups",
  notification: "Notifications",
  instance_hook: "Instance hooks",
  dashboard_widget: "Dashboard",
  web_page: "Pages",
  background_task: "Background work",
  theme: "Themes",
  api: "API",
  mcp: "MCP",
  extension: "Extensions",
};
const PLAN_LABEL: Record<string, string> = { monthly: "month", yearly: "year", fixed_term: "term" };
const PLAN_NAME: Record<string, string> = { monthly: "Monthly", yearly: "Yearly", fixed_term: "Fixed term" };

function planPrice(plan: PricePlan) {
  const per = plan.kind === "fixed_term" ? `${plan.period_days ?? "?"} days` : PLAN_LABEL[plan.kind];
  return `${money(plan.price_cents, plan.currency)} / ${per}`;
}

function priceOf(plugin: CatalogPlugin) {
  return plugin.tier === "free" ? "Free" : plugin.price_cents != null ? `${money(plugin.price_cents, plugin.currency)} / month` : "Paid";
}

// ---------------------------------------------------------------- the running plugin job, then the restart

interface Work {
  jobId: string;
  name: string;
  verb: string;
}

function useInstaller() {
  const client = useQueryClient();
  const toast = useToast();
  const [work, setWork] = useState<Work | null>(null);
  const start = (job: Job, name: string, verb = "installed") => setWork({ jobId: job.id, name, verb });
  const finish = async (job: Job) => {
    if (!work) return;
    if (job.status === "failed") {
      toast("error", `${work.name} was not ${work.verb}`, job.error ?? "Open the details to see why.");
      return;
    }
    toast("info", "Restarting the panel", "Instances keep running; this takes a few seconds");
    const back = await waitForBackend();
    await client.invalidateQueries();
    setWork(null);
    if (back) toast("success", `${work.name} is ${work.verb}`);
    else toast("error", "The panel is slow to come back", "Reload the page in a minute.");
  };
  return { work, start, finish, clear: () => setWork(null) };
}

function WorkCard({ work, onFinished, onClear }: { work: Work; onFinished: (job: Job) => void; onClear: () => void }) {
  const { data: job } = useJob(work.jobId);
  const [details, setDetails] = useState(false);
  const reported = useRef<string | null>(null);
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
        <b className="spacer truncate">{work.name}</b>
        <Button size="sm" variant="ghost" onClick={() => setDetails(true)}>Details</Button>
        {job?.status === "failed" && <Button size="sm" variant="ghost" onClick={onClear}>Dismiss</Button>}
      </div>
      {job ? (
        job.status === "success" ? (
          <div className="row small muted"><span className="spinner" /> Restarting the panel to load it</div>
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

function PluginCard({ plugin, onOpen }: { plugin: CatalogPlugin; onOpen: () => void }) {
  return (
    <button type="button" className="card link plugin-card" onClick={onOpen}>
      <div className="pc-head">
        <Monogram name={plugin.name} />
        <div className="pc-titles">
          <span className="pc-name truncate">{plugin.name}</span>
          <PublisherBadge publisher={plugin.publisher} />
        </div>
      </div>
      <p className="pc-text">{plugin.description}</p>
      <div className="pc-foot">
        <Tag tone={plugin.tier === "free" ? "positive" : undefined}>{priceOf(plugin)}</Tag>
        <span className="row xsmall faint">
          <Star /> {plugin.rating_count ? plugin.avg_rating.toFixed(1) : "—"}
        </span>
        <span className="xsmall faint">{number(plugin.install_count)} installs</span>
        <span className="spacer" />
        {plugin.installed_version && <Tag mono>v{plugin.installed_version}</Tag>}
      </div>
    </button>
  );
}

function PluginSheet({
  id,
  installed,
  busy,
  onClose,
  onStarted,
}: {
  id: string;
  installed?: InstalledPlugin;
  busy: boolean;
  onClose: () => void;
  onStarted: (job: Job, name: string) => void;
}) {
  const { data: plugin, error, isPending } = useCatalogPlugin(id);
  const { data: status } = useMarketplace();
  const can = useCan();
  const toast = useToast();
  const [plan, setPlan] = useState<string>("");
  const [step, setStep] = useState<"idle" | "working" | "paying">("idle");
  const [purchase, setPurchase] = useState<Purchase | null>(null);
  const chosen = plugin?.plans?.find((p) => p.id === plan) ?? plugin?.plans?.[0];
  const manage = can("settings.manage");
  const linked = !!status?.linked?.account_name;

  const install = async (name: string) => {
    const job = await api.post<Job>("/plugins", { id });
    onStarted(job, name);
    onClose();
  };

  // a Checkout opened in another tab: follow the purchase until it is paid, then install
  useEffect(() => {
    if (step !== "paying" || !purchase || !plugin) return;
    let stop = false;
    const started = Date.now();
    const poll = async () => {
      while (!stop && Date.now() - started < 30 * 60_000) {
        await new Promise((r) => setTimeout(r, 3000));
        if (stop) return;
        try {
          const current = await api.get<Purchase>(`/marketplace/purchases/${purchase.id}`);
          if (current.status === "paid") {
            toast("success", "Payment received", `Installing ${plugin.name}`);
            await install(plugin.name);
            return;
          }
          if (current.status && ["failed", "expired", "cancelled"].includes(current.status)) {
            toast("error", "The payment did not go through", "Nothing was charged. Try again when you are ready.");
            setStep("idle");
            return;
          }
        } catch (err) {
          if (err instanceof ApiError && err.status === 503) continue; // the marketplace is briefly away
          toast("error", "Lost track of the payment", errorText(err));
          setStep("idle");
          return;
        }
      }
    };
    void poll();
    return () => {
      stop = true;
    };
  }, [step, purchase, plugin]);

  const installFree = async () => {
    if (!plugin) return;
    setStep("working");
    try {
      await install(plugin.name);
    } catch (err) {
      toast("error", `${plugin.name} was not installed`, errorText(err));
      setStep("idle");
    }
  };

  const buy = async () => {
    if (!plugin || !chosen) return;
    setStep("working");
    // opened now, while the click still counts, so the browser does not block it; filled in once the hub answers
    const tab = window.open("about:blank", "_blank");
    try {
      const answer = await api.post<{ purchase: Purchase; job: Job | null }>("/marketplace/purchases", { plugin_id: plugin.id, plan_id: chosen.id });
      if (answer.job) {
        tab?.close();
        toast("success", "Paid with your saved card", `Installing ${plugin.name}`);
        onStarted(answer.job, plugin.name);
        onClose();
        return;
      }
      if (!answer.purchase.checkout_url) throw new Error("The marketplace did not send a payment page.");
      if (tab) {
        tab.opener = null;
        tab.location.href = answer.purchase.checkout_url;
      } else {
        window.open(answer.purchase.checkout_url, "_blank", "noopener");
      }
      setPurchase(answer.purchase);
      setStep("paying");
    } catch (err) {
      tab?.close();
      toast("error", "Not bought", errorText(err));
      setStep("idle");
    }
  };

  const action = () => {
    if (!plugin || !manage) return null;
    if (installed && !installed.update_available) return <Tag tone="positive">Installed · v{installed.version}</Tag>;
    if (plugin.tier === "free" || installed)
      return (
        <Button variant="primary" icon={<Download />} loading={step === "working"} disabled={busy} onClick={installFree}>
          {installed ? `Update to v${plugin.latest_version?.version}` : "Install"}
        </Button>
      );
    return (
      <Button variant="primary" icon={<ShoppingBag />} loading={step === "working"} disabled={busy || !linked || !chosen || step === "paying"} onClick={buy}>
        Buy &amp; install
      </Button>
    );
  };

  return (
    <Modal
      wide
      title={plugin ? <span className="row"><Monogram name={plugin.name} /> {plugin.name}</span> : "Plugin"}
      description={plugin ? <PublisherBadge publisher={plugin.publisher} /> : undefined}
      onClose={onClose}
      footer={
        <>
          <Button onClick={onClose}>Close</Button>
          {action()}
        </>
      }
    >
      {isPending ? (
        <Skeleton height={160} />
      ) : error || !plugin ? (
        <Callout tone="warning">{errorText(error)}</Callout>
      ) : (
        <div className="stack">
          <p>{plugin.description}</p>
          <div className="row-wrap">
            <Tag>{TYPE_LABEL[plugin.type] ?? plugin.type}</Tag>
            {plugin.latest_version && <Tag mono>v{plugin.latest_version.version}</Tag>}
            {plugin.latest_version && <Tag mono>Sudoo.sh {plugin.latest_version.min_sudoo_version}+</Tag>}
            <Tag tone={plugin.tier === "free" ? "positive" : undefined}>{priceOf(plugin)}</Tag>
          </div>
          {(plugin.permissions?.length ?? 0) > 0 && (
            <Field label="It asks to">
              <div className="row-wrap">{plugin.permissions.map((p) => <Tag key={p} mono>{p}</Tag>)}</div>
            </Field>
          )}
          {(plugin.requires?.length ?? 0) > 0 && (
            <Field label="It needs">
              <div className="row-wrap">{plugin.requires.map((r) => <Tag key={r.id} mono>{r.id} {r.version ?? ""}</Tag>)}</div>
            </Field>
          )}
          {plugin.tier === "paid" && !installed && manage && (
            <>
              {(plugin.plans?.length ?? 0) > 1 && (
                <Field label="Plan">
                  <Segmented value={chosen?.id ?? ""} onChange={setPlan} options={plugin.plans.map((p) => ({ value: p.id, label: planPrice(p) }))} />
                </Field>
              )}
              {plugin.plans?.length === 1 && chosen && <p className="small muted">{planPrice(chosen)}, renewed until you cancel it on app.sudoo.sh.</p>}
              {!linked && (
                <Callout tone="info">
                  <div className="stack-sm">
                    <span>Paid plugins belong to an account on app.sudoo.sh. Link this panel to yours first.</span>
                    <LinkForm />
                  </div>
                </Callout>
              )}
              {step === "paying" && (
                <Callout tone="info">
                  <div className="row"><span className="spinner" /> Finish the payment in the other tab. The plugin installs as soon as it is paid.</div>
                </Callout>
              )}
            </>
          )}
          {!manage && <p className="small muted">Only a super admin can install or buy plugins.</p>}
          <p className="xsmall faint">Every package is signed by the marketplace and checked by this panel before it is installed. The plugin's code stays on the server and is never shown.</p>
        </div>
      )}
    </Modal>
  );
}

function Catalogue({ installed, busy, onStarted }: { installed: InstalledPlugin[]; busy: boolean; onStarted: (job: Job, name: string) => void }) {
  const { data: status } = useMarketplace();
  const usable = !!status?.enabled;
  const [q, setQ] = useState("");
  const [typed, setTyped] = useState("");
  const [type, setType] = useState("");
  const [tier, setTier] = useState<"" | "free" | "paid">("");
  const [sort, setSort] = useState("trusted");
  const [open, setOpen] = useState<string | null>(null);
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
  const byId = Object.fromEntries(installed.map((p) => [p.id, p]));

  if (!usable) return null;
  return (
    <div className="stack">
      <div className="toolbar">
        <div className="input-group">
          <Search />
          <input className="input" placeholder="Search plugins" aria-label="Search plugins" value={typed} onChange={(e) => setTyped(e.target.value)} />
        </div>
        <select className="select" style={{ width: "auto" }} aria-label="Category" value={type} onChange={(e) => setType(e.target.value)}>
          <option value="">All categories</option>
          {(categories ?? []).map((c) => (
            <option key={c.type} value={c.type}>{TYPE_LABEL[c.type] ?? c.type} ({c.count})</option>
          ))}
        </select>
        <Segmented value={tier} onChange={setTier} options={[{ value: "", label: "All" }, { value: "free", label: "Free" }, { value: "paid", label: "Paid" }]} />
        <span className="spacer" />
        <select className="select" style={{ width: "auto" }} aria-label="Order" value={sort} onChange={(e) => setSort(e.target.value)}>
          <option value="trusted">Official and verified first</option>
          <option value="popular">Most installed</option>
          <option value="rating">Best rated</option>
          <option value="newest">Newest</option>
          <option value="name">Name</option>
        </select>
      </div>
      {isPending ? (
        <div className="plugin-grid">{[0, 1, 2].map((i) => <Skeleton key={i} height={168} />)}</div>
      ) : error ? (
        <Callout tone="warning">{errorText(error)}</Callout>
      ) : !plugins?.length ? (
        <div className="card"><Empty icon={<Puzzle />} title="No plugins found">Try another word or category.</Empty></div>
      ) : (
        <div className="plugin-grid rise">
          {plugins.map((plugin) => (
            <PluginCard key={plugin.id} plugin={plugin} onOpen={() => setOpen(plugin.id)} />
          ))}
        </div>
      )}
      {open && <PluginSheet id={open} installed={byId[open]} busy={busy} onClose={() => setOpen(null)} onStarted={onStarted} />}
    </div>
  );
}

// ---------------------------------------------------------------- installed

const STATUS: Record<InstalledPlugin["status"], [string, string]> = {
  active: ["online", "Running"],
  disabled: ["stopped", "Switched off"],
  modified: ["failed", "Files changed"],
  blocked: ["failed", "Refused by the marketplace"],
  switched_off: ["paused", "Stopped (licence)"],
  unlicensed: ["paused", "No licence"],
};
const PHASE: Record<string, [string, "positive" | "caution" | "negative" | undefined]> = {
  active: ["Licensed", "positive"],
  expiring: ["Ends soon", "caution"],
  grace: ["In grace period", "caution"],
  ended: ["Licence ended", "negative"],
  no_lease: ["Marketplace unreachable", "negative"],
  unlicensed: ["No licence", "negative"],
};

function Installed({ plugins, busy, onStarted }: { plugins: InstalledPlugin[]; busy: boolean; onStarted: (job: Job, name: string, verb?: string) => void }) {
  const can = useCan();
  const toast = useToast();
  const client = useQueryClient();
  const [removing, setRemoving] = useState<InstalledPlugin | null>(null);
  const [confirm, setConfirm] = useState("");
  const manage = can("settings.manage");

  const switchTo = async (plugin: InstalledPlugin, on: boolean) => {
    try {
      await api.post(`/plugins/${plugin.id}/${on ? "enable" : "disable"}`);
      toast("info", `${plugin.name} is switched ${on ? "on" : "off"}`, "The panel restarts to apply it");
      await waitForBackend();
      await client.invalidateQueries();
    } catch (error) {
      toast("error", "Not changed", errorText(error));
    }
  };
  const update = async (plugin: InstalledPlugin) => {
    try {
      onStarted(await api.post<Job>(`/plugins/${plugin.id}/update`), plugin.name, "updated");
    } catch (error) {
      toast("error", "Not updated", errorText(error));
    }
  };
  const uninstall = async () => {
    if (!removing) return;
    try {
      onStarted(await api.del<Job>(`/plugins/${removing.id}`, { confirm }), removing.name, "uninstalled");
      setRemoving(null);
      setConfirm("");
    } catch (error) {
      toast("error", "Not uninstalled", errorText(error));
    }
  };

  if (!plugins.length)
    return (
      <div className="card">
        <Empty icon={<Puzzle />} title="No plugins yet">Plugins from the marketplace add features to the panel: sign-in methods, mailers, backup destinations and more.</Empty>
      </div>
    );
  return (
    <div className="card rise">
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr><th>Plugin</th><th>Version</th><th>Status</th><th>Licence</th><th /></tr>
          </thead>
          <tbody>
            {plugins.map((p) => {
              const [cls, label] = STATUS[p.status] ?? ["unknown", p.status];
              const phase = p.licence?.phase ? PHASE[p.licence.phase] : null;
              return (
                <tr key={p.id}>
                  <td>
                    <div className="row">
                      <Monogram name={p.name} />
                      <div style={{ minWidth: 0 }}>
                        <div className="truncate">{p.name}</div>
                        <div className="xsmall faint"><PublisherBadge publisher={p.publisher} /></div>
                      </div>
                    </div>
                  </td>
                  <td className="nowrap">
                    <span className="mono small">v{p.version}</span>
                    {p.update_available && manage && (
                      <> <Button size="sm" variant="soft" disabled={busy} onClick={() => update(p)}>Update to v{p.latest_version}</Button></>
                    )}
                  </td>
                  <td>
                    <Status status={p.restart_pending ? "starting" : cls} label={p.restart_pending ? "Waiting for a restart" : label} />
                  </td>
                  <td className="small">
                    {p.tier === "free" ? (
                      <span className="faint">Free</span>
                    ) : (
                      <div className="stack-sm" style={{ gap: 2, alignItems: "flex-start" }}>
                        {phase && <Tag tone={phase[1]}>{phase[0]}</Tag>}
                        {p.licence?.expires_at && <span className="xsmall faint">Ends {day(p.licence.expires_at)}{p.licence.grace_until ? ` · stops ${day(p.licence.grace_until)}` : ""}</span>}
                      </div>
                    )}
                  </td>
                  <td className="actions">
                    {manage && (
                      <Menu trigger={(toggle) => <Button size="sm" variant="ghost" onClick={toggle} disabled={busy}>Manage</Button>}>
                        {(close) => (
                          <>
                            {p.enabled ? (
                              <button onClick={() => { close(); void switchTo(p, false); }}>Switch off</button>
                            ) : (
                              <button disabled={p.status === "modified"} onClick={() => { close(); void switchTo(p, true); }}>Switch on</button>
                            )}
                            <hr />
                            <button className="danger" onClick={() => { close(); setRemoving(p); }}>
                              <Trash2 /> Uninstall
                            </button>
                          </>
                        )}
                      </Menu>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {removing && (
        <Modal
          title={`Uninstall ${removing.name}`}
          description="The panel's data is backed up first; then the plugin's files are removed and the panel restarts."
          onClose={() => setRemoving(null)}
          footer={
            <>
              <Button onClick={() => setRemoving(null)}>Cancel</Button>
              <Button variant="danger" icon={<Trash2 />} disabled={confirm !== removing.id} onClick={uninstall}>Uninstall</Button>
            </>
          }
        >
          <Field label={<>Type <code>{removing.id}</code> to confirm</>}>
            <input className="input mono" autoFocus value={confirm} onChange={(e) => setConfirm(e.target.value)} />
          </Field>
          {removing.tier === "paid" && <p className="small muted" style={{ marginTop: 12 }}>Uninstalling does not cancel its subscription: do that on app.sudoo.sh.</p>}
        </Modal>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- purchases

function Purchases() {
  const { data: status } = useMarketplace();
  const { data: purchases, error, isPending } = usePurchases(!!status?.enabled);
  const PAID: Record<string, "positive" | "caution" | "negative" | undefined> = { paid: "positive", pending: "caution", failed: "negative", refunded: undefined };
  return (
    <div className="card rise">
      <div className="card-head">
        <Receipt />
        <div className="spacer">
          <h2>Purchases of this server</h2>
          <div className="sub">Bought and renewed plugins, as the marketplace records them</div>
        </div>
        {!!purchases?.length && (
          <a className="btn sm" href="/api/v1/marketplace/purchases?format=csv" download>
            <FileDown /> Export CSV
          </a>
        )}
      </div>
      {isPending ? (
        <div className="card-body"><Skeleton height={120} /></div>
      ) : error ? (
        <div className="card-body"><Callout tone="warning">{errorText(error)}</Callout></div>
      ) : !purchases?.length ? (
        <Empty icon={<Receipt />} title="No purchases yet">Paid plugins bought for this server appear here, with their receipts.</Empty>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr><th>Date</th><th>Plugin</th><th>Plan</th><th className="num">Amount</th><th>Status</th><th /></tr>
            </thead>
            <tbody>
              {purchases.map((p) => (
                <tr key={p.id}>
                  <td className="small nowrap">{day(p.created_at)} <span className="faint">· {ago(p.created_at)}</span></td>
                  <td>{p.plugin_name}{p.kind === "renewal" && <span className="faint small"> · renewal</span>}</td>
                  <td className="small">{p.plan ? PLAN_NAME[p.plan] ?? p.plan : "—"}</td>
                  <td className="num">{money(p.amount_cents, p.currency)}</td>
                  <td><Tag tone={PAID[p.status ?? ""]}>{p.status ?? "—"}</Tag></td>
                  <td className="actions">
                    {p.receipt_url && (
                      <a className="btn sm ghost" href={p.receipt_url} target="_blank" rel="noreferrer noopener">
                        Receipt <ExternalLink />
                      </a>
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

// ---------------------------------------------------------------- the page

const TABS = [
  { id: "", label: "Marketplace" },
  { id: "installed", label: "Installed" },
  { id: "purchases", label: "Purchases" },
];

export function PluginsPage() {
  const { tab = "" } = useParams();
  const navigate = useNavigate();
  const { data: status } = useMarketplace();
  const { data: installed, isPending } = usePlugins();
  const installer = useInstaller();
  const problem = marketplaceProblem(status);
  const updates = installed?.filter((p) => p.update_available).length ?? 0;
  const started = (job: Job, name: string, verb?: string) => {
    installer.start(job, name, verb);
    if (tab !== "installed") navigate("/plugins/installed", { viewTransition: true });
  };
  return (
    <Page>
      <PageHead title="Plugins">
        Add features from the Sudoo.sh marketplace. Every package is signed by the marketplace and checked here before it is installed.
      </PageHead>
      {problem && <Callout tone={problem.tone}>{problem.text}</Callout>}
      {installer.work && <WorkCard work={installer.work} onFinished={installer.finish} onClear={installer.clear} />}
      <nav className="tabs">
        {TABS.map((t) => (
          <NavLink key={t.id} to={`/plugins${t.id ? `/${t.id}` : ""}`} end viewTransition className={({ isActive }) => (isActive ? "active" : "")}>
            {t.label}
            {t.id === "installed" && !!installed?.length && <span className="faint tabular">{installed.length}</span>}
            {t.id === "installed" && updates > 0 && <Tag tone="accent">{updates} {updates === 1 ? "update" : "updates"}</Tag>}
          </NavLink>
        ))}
      </nav>
      <div className="rise" key={tab}>
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
