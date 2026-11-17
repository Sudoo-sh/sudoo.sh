import { useQueryClient } from "@tanstack/react-query";
import { Bell, Mail, Plug, Puzzle, RefreshCcw, RotateCcw, Send, ServerCog, Webhook } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router";
import { api, errorText } from "@/api/client";
import { keys, useCall, useSettings } from "@/api/queries";
import type { Setting } from "@/api/types";
import { MarketplaceSettings } from "@/components/Marketplace";
import { Page, PageHead, useMe } from "@/components/Shell";
import { Button, Callout, Skeleton, Switch, Tag } from "@/components/ui/basics";
import { Modal, useToast } from "@/components/ui/overlay";

/** `test`: the group has a "Send a test alert" button, by e-mail or to that webhook. */
type Group = { title: string; text: string; keys: string[]; test?: "email" | 1 | 2 | 3 };
type TabId = "general" | "instances" | "odoo" | "backups" | "security" | "email" | "alerts" | "github" | "marketplace" | "advanced";

const webhookGroup = (n: 1 | 2 | 3): Group => ({
  title: `Webhook ${n}`,
  text: n === 1
    ? "Alerts posted to Slack, Discord, Teams (their incoming webhook address) or your own system (signed JSON). https only"
    : "Another place alerts are posted to",
  keys: [`alert_webhook_${n}_url`, `alert_webhook_${n}_format`, `alert_webhook_${n}_secret`],
  test: n,
});

/** The tabs of the page and the groups of settings in each. Every setting that can be changed is in one group only;
 *  one the page does not know yet shows under Advanced, Other. */
const TABS: { id: TabId; label: string; groups: Group[] }[] = [
  {
    id: "general",
    label: "General",
    groups: [
      { title: "Contact and appearance", text: "Who people are told to contact, and the accent colour of the panel and its e-mails", keys: ["admin_contact_name", "admin_contact_email", "admin_contact_phone", "brand_color"] },
    ],
  },
  {
    id: "instances",
    label: "Instances",
    groups: [
      { title: "New instances", text: "What a new instance starts with; each instance can change its own", keys: ["auto_pause", "idle_hours", "auto_update", "auto_upgrade", "delete_after_days", "odoo_admin_password"] },
      { title: "How many instances", text: "How many instances, and how often a new one can be created. 0 turns a limit off", keys: ["max_instances", "max_instances_per_user", "create_cooldown_minutes"] },
      { title: "Capacity", text: "How many instances this server runs comfortably at once, and when the panel warns. 0 turns the hard limit off: the panel then only warns", keys: ["capacity_reserve_mb", "capacity_reserve_percent", "capacity_instance_memory_mb", "capacity_warning_percent", "max_running_instances"] },
      { title: "Instance containers", text: "Limits of each instance's containers, applied when an instance next starts, and what happens when someone changes them by hand. 0 turns a limit off", keys: ["odoo_memory_limit", "odoo_cpu_limit", "db_memory_limit", "db_cpu_limit", "container_pids_limit", "container_log_max_size", "container_log_max_files", "drift_action"] },
      { title: "Gateway", text: "What visitors may send to an instance through the gateway, which every instance shares", keys: ["gateway_max_upload_mb", "gateway_idle_timeout_seconds", "gateway_websocket_max_message_mb"] },
    ],
  },
  {
    id: "odoo",
    label: "Odoo",
    groups: [
      { title: "Odoo versions and images", text: "Which versions are offered and where Odoo and PostgreSQL come from", keys: ["odoo_versions", "odoo_min_version", "prepull_images", "odoo_image", "postgres_image"] },
      { title: "Enterprise add-ons", text: "Where the Enterprise add-ons of each version are", keys: ["enterprise_source", "enterprise_path", "enterprise_git_url", "enterprise_git_branch", "enterprise_marker"] },
    ],
  },
  {
    id: "backups",
    label: "Backups",
    groups: [
      { title: "Backups", text: "Each instance used during the day is backed up once, at this time; it keeps only its latest daily backup", keys: ["daily_backups", "daily_backup_time", "timezone", "rollback_hours", "safe_updates"] },
    ],
  },
  {
    id: "security",
    label: "Sign-in and security",
    groups: [
      { title: "Sign-in", text: "Second factors, how people sign in, and how long a session lasts before signing in again", keys: ["require_second_factor", "second_factors", "session_idle_minutes", "session_max_hours", "auth"] },
      { title: "Requests per minute", text: "How many requests one person may send. 0 turns a limit off", keys: ["rate_limit_reads_per_minute", "rate_limit_changes_per_minute", "rate_limiter"] },
    ],
  },
  {
    id: "email",
    label: "E-mail",
    groups: [
      { title: "E-mail", text: "The mailbox that sends sign-in codes, for example a Gmail account with an app password", keys: ["mailer", "mail_from", "smtp_host", "smtp_port", "smtp_security", "smtp_username", "smtp_password", "mail_templates_dir"] },
    ],
  },
  {
    id: "alerts",
    label: "Alerts",
    groups: [
      { title: "Who receives alerts", text: "Alerts by e-mail, through the mail settings of the E-mail tab. The bell at the top lists them for everyone who may read the audit log", keys: ["alerts", "alert_super_admins", "alert_emails"], test: "email" },
      { title: "Jobs and backups", text: "Alerted as soon as they happen, with the plain explanation and a link to the job", keys: ["alert_job_failed", "alert_update_failed", "alert_backup_failed", "alert_drift"] },
      { title: "Instances and server", text: "Problems that last are alerted after their minutes, and a notice follows when they are over", keys: ["alert_instance_down", "alert_instance_down_minutes", "alert_public_link", "alert_public_link_minutes", "alert_memory", "alert_memory_minutes", "alert_disk", "alert_disk_free_percent"] },
      { title: "How often", text: "The same alert is sent once per quiet period; repeats only count up in the bell", keys: ["alert_recovery", "alert_quiet_minutes", "alert_digest", "alert_digest_time", "alert_webhook_timeout_seconds"] },
      webhookGroup(1),
      webhookGroup(2),
      webhookGroup(3),
    ],
  },
  {
    id: "github",
    label: "GitHub and updates",
    groups: [
      { title: "Code updates", text: "How new code reaches the instances: how often GitHub is checked, and what else starts an update", keys: ["poll_minutes", "update_triggers", "github_webhook_secret"] },
    ],
  },
  { id: "marketplace", label: "Marketplace", groups: [] },
  {
    id: "advanced",
    label: "Advanced",
    groups: [
      { title: "Monitoring", text: "How often figures are measured, and the image of the Cloudflare connector", keys: ["metrics_interval_seconds", "cloudflared_image"] },
    ],
  },
];
const TAB_IDS = new Set<string>(TABS.map((t) => t.id));
const KNOWN = new Set<string>([...TABS.flatMap((t) => t.groups.flatMap((g) => g.keys)), "marketplace"]);
const keysOfTab = (tab: (typeof TABS)[number], other: string[]) =>
  [...tab.groups.flatMap((g) => g.keys), ...(tab.id === "marketplace" ? ["marketplace"] : []), ...(tab.id === "advanced" ? other : [])];

const TITLES: Record<string, string> = {
  require_second_factor: "Second factor required for",
  session_idle_minutes: "Sign out after inactivity, minutes",
  session_max_hours: "Longest session, hours",
  mail_from: "Sender",
  smtp_host: "Mail server",
  smtp_port: "Port",
  smtp_security: "Encryption",
  smtp_username: "User name",
  smtp_password: "Password",
  mail_templates_dir: "Own e-mail templates folder",
  rate_limit_reads_per_minute: "Reading requests per minute",
  rate_limit_changes_per_minute: "Changing requests per minute",
  max_instances: "Instances on this server",
  max_instances_per_user: "Instances per person",
  create_cooldown_minutes: "Minutes between two creations",
  capacity_reserve_mb: "Memory kept for the system, MB",
  capacity_reserve_percent: "Memory kept for the system, %",
  capacity_instance_memory_mb: "Memory per instance until measured, MB",
  capacity_warning_percent: "Warn from server memory use, %",
  max_running_instances: "Running instances at most",
  gateway_max_upload_mb: "Largest upload, MB",
  gateway_idle_timeout_seconds: "Give up on a silent upload after, seconds",
  gateway_websocket_max_message_mb: "Largest WebSocket message, MB",
  auto_pause: "Pause when idle",
  idle_hours: "Pause after, hours without visits",
  auto_update: "Get new code automatically",
  auto_upgrade: "Upgrade changed modules",
  delete_after_days: "Lifetime, days",
  odoo_admin_password: "Odoo admin password",
  odoo_versions: "Versions offered",
  odoo_min_version: "Oldest version offered",
  prepull_images: "Download images ahead of time",
  odoo_image: "Odoo image",
  postgres_image: "PostgreSQL image",
  odoo_memory_limit: "Odoo memory (512m, 2g)",
  odoo_cpu_limit: "Odoo processor cores",
  db_memory_limit: "Database memory (512m, 1g)",
  db_cpu_limit: "Database processor cores",
  container_pids_limit: "Processes per container",
  container_log_max_size: "Log file size before rotation",
  container_log_max_files: "Rotated log files kept",
  drift_action: "When changed outside the panel",
  enterprise_path: "Folder",
  enterprise_git_url: "Git repository",
  enterprise_git_branch: "Branch of a version",
  enterprise_marker: "Module that proves the add-ons are there",
  safe_updates: "Back up before updates, put back failed ones",
  poll_minutes: "Check GitHub every, minutes",
  github_webhook_secret: "GitHub webhook secret",
  metrics_interval_seconds: "Measure every, seconds",
  cloudflared_image: "Cloudflare connector image",
  admin_contact_name: "Contact name",
  admin_contact_email: "Contact e-mail",
  admin_contact_phone: "Contact phone",
  brand_color: "Accent colour",
  marketplace: "Use the marketplace",
  mailer: "Mailer",
  second_factors: "Second factors offered",
  update_triggers: "Update triggers",
  enterprise_source: "Enterprise source",
  auth: "Authenticator",
  rate_limiter: "Rate limiter",
  alerts: "Send alerts",
  alert_super_admins: "E-mail every super admin",
  alert_emails: "Also e-mail these addresses",
  alert_job_failed: "A job failed",
  alert_update_failed: "An update failed and was rolled back",
  alert_backup_failed: "A backup failed",
  alert_drift: "Changed outside the panel",
  alert_instance_down: "An instance is down",
  alert_instance_down_minutes: "Down for, minutes",
  alert_public_link: "No public link",
  alert_public_link_minutes: "Without a link for, minutes",
  alert_memory: "Memory high (from the capacity warning)",
  alert_memory_minutes: "Memory high for, minutes",
  alert_disk: "Disk almost full",
  alert_disk_free_percent: "Alert below, % free",
  alert_recovery: "Say when a problem is over",
  alert_quiet_minutes: "Same alert at most every, minutes",
  alert_digest: "One e-mail a day instead",
  alert_digest_time: "Daily e-mail at (HH:MM)",
  alert_webhook_timeout_seconds: "Webhook timeout, seconds",
  ...Object.fromEntries(
    [1, 2, 3].flatMap((n) => [
      [`alert_webhook_${n}_url`, "Address"],
      [`alert_webhook_${n}_format`, "Format"],
      [`alert_webhook_${n}_secret`, "Signing secret (json)"],
    ]),
  ),
};

const CHOICES: Record<string, [string, string][]> = {
  require_second_factor: [["none", "Nobody"], ["admins", "Super admins and administrators"], ["everyone", "Everyone"]],
  smtp_security: [["starttls", "STARTTLS (port 587)"], ["ssl", "SSL (port 465)"], ["none", "None"]],
  mailer: [["smtp", "SMTP"], ["none", "No e-mail"]],
  drift_action: [["restore", "Report it and restore the panel's configuration"], ["warn", "Only report it"]],
  enterprise_source: [["folder", "Folder"], ["git", "Git repository"], ["none", "None (Community only)"]],
  ...Object.fromEntries(
    [1, 2, 3].map((n) => [
      `alert_webhook_${n}_format`,
      [["json", "JSON, signed (your own system)"], ["slack", "Slack"], ["discord", "Discord"], ["teams", "Microsoft Teams"]] as [string, string][],
    ]),
  ),
};

type Value = string | number | boolean;

function Editor({ setting, value, onChange }: { setting: Setting; value: Value; onChange: (value: Value) => void }) {
  if (setting.secret)
    return (
      <input
        className="input"
        type="password"
        autoComplete="new-password"
        placeholder={setting.value ? "Set. Type to replace it" : "Not set"}
        value={typeof value === "string" ? value : ""}
        onChange={(e) => onChange(e.target.value)}
      />
    );
  const choices = CHOICES[setting.key];
  if (choices)
    return (
      <select className="select" value={String(value)} onChange={(e) => onChange(e.target.value)}>
        {choices.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
      </select>
    );
  if (setting.key === "brand_color")
    return (
      <div className="row">
        <input type="color" aria-label="Pick a colour" value={String(value)} onChange={(e) => onChange(e.target.value)}
          style={{ width: 40, height: 36, padding: 2, border: "1px solid var(--hairline-strong)", borderRadius: 8, background: "var(--surface)" }} />
        <input className="input mono" value={String(value)} onChange={(e) => onChange(e.target.value)} />
      </div>
    );
  if (setting.type === "boolean") return <div style={{ justifySelf: "end" }}><Switch checked={Boolean(value)} onChange={onChange} /></div>;
  if (setting.type === "integer" || setting.type === "number")
    return (
      <input className="input tabular" type="number" min={setting.minimum ?? undefined} step={setting.type === "integer" ? 1 : "any"} value={String(value)}
        onChange={(e) => onChange(e.target.value === "" ? "" : Number(e.target.value))} />
    );
  return <input className="input" value={String(value)} onChange={(e) => onChange(e.target.value)} />;
}

function Section({ title, text, icon, actions, children }: { title: string; text: string; icon?: ReactNode; actions?: ReactNode; children: ReactNode }) {
  return (
    <section className="card">
      <div className="card-head">
        {icon}
        <div className="spacer">
          <h2>{title}</h2>
          <div className="sub">{text}</div>
        </div>
        {actions}
      </div>
      {children}
    </section>
  );
}

function TestEmail({ onClose }: { onClose: () => void }) {
  const me = useMe();
  const toast = useToast();
  const [to, setTo] = useState(me.email ?? "");
  const [busy, setBusy] = useState(false);
  const send = async () => {
    setBusy(true);
    try {
      await api.post("/settings/test-email", { to });
      toast("success", "Test e-mail sent", `Check ${to}, and its spam folder the first time`);
      onClose();
    } catch (error) {
      toast("error", "Not sent", errorText(error));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title="Send a test e-mail" description="Uses the saved mail settings" onClose={onClose}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" icon={<Send />} loading={busy} disabled={!to} onClick={send}>Send</Button></>}>
      <input className="input" type="email" autoFocus value={to} onChange={(e) => setTo(e.target.value)} />
    </Modal>
  );
}

/** Sends a test alert with the saved settings: by e-mail to every alert recipient, or to one webhook. */
function useTestAlert() {
  const toast = useToast();
  const [busy, setBusy] = useState<string | null>(null);
  const send = async (target: "email" | 1 | 2 | 3, unsaved: boolean) => {
    const id = String(target);
    setBusy(id);
    try {
      const body = target === "email" ? { channel: "email" } : { channel: "webhook", webhook: target };
      const result = await api.post<{ sent_to?: string[]; status?: number }>("/alerts/test", body);
      const where = target === "email" ? `To ${result.sent_to?.join(", ")}` : `Webhook ${target} answered ${result.status}`;
      toast("success", "Test alert sent", where + (unsaved ? ". Unsaved changes were not used" : ""));
    } catch (error) {
      toast("error", "Test alert not sent", errorText(error));
    } finally {
      setBusy(null);
    }
  };
  return { busy, send };
}

/** The tabs as an accessible tablist: arrow keys, Home and End move between tabs and open them; the chosen tab is
 *  the only one in the Tab order. A dot marks a tab with unsaved changes, or one waiting for a restart. */
function TabList({ tab, onSelect, marks }: { tab: TabId; onSelect: (id: TabId) => void; marks: Record<string, { unsaved: number; restart: boolean }> }) {
  const list = useRef<HTMLDivElement>(null);
  useEffect(() => {
    // on a narrow screen the tabs scroll sideways: keep the chosen one in view
    const chosen = list.current?.querySelector<HTMLElement>('[aria-selected="true"]');
    const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
    chosen?.scrollIntoView({ block: "nearest", inline: "nearest", behavior: reduce ? "auto" : "smooth" });
  }, [tab]);
  const move = (event: KeyboardEvent<HTMLDivElement>) => {
    const index = TABS.findIndex((t) => t.id === tab);
    const next =
      event.key === "ArrowRight" ? (index + 1) % TABS.length
      : event.key === "ArrowLeft" ? (index - 1 + TABS.length) % TABS.length
      : event.key === "Home" ? 0
      : event.key === "End" ? TABS.length - 1
      : null;
    if (next === null) return;
    event.preventDefault();
    onSelect(TABS[next]!.id);
    list.current?.querySelector<HTMLElement>(`#settings-tab-${TABS[next]!.id}`)?.focus();
  };
  return (
    <div className="tabs settings-tabs" role="tablist" aria-label="Settings" ref={list} onKeyDown={move}>
      {TABS.map((t) => {
        const mark = marks[t.id];
        const state = [
          mark?.unsaved ? `${mark.unsaved} unsaved ${mark.unsaved === 1 ? "change" : "changes"}` : "",
          mark?.restart ? "waits for a restart" : "",
        ].filter(Boolean).join(", ");
        return (
          <button
            key={t.id}
            id={`settings-tab-${t.id}`}
            role="tab"
            type="button"
            aria-selected={t.id === tab}
            aria-controls={`settings-panel-${t.id}`}
            tabIndex={t.id === tab ? 0 : -1}
            className={t.id === tab ? "active" : ""}
            title={state || undefined}
            onClick={() => onSelect(t.id)}
          >
            {t.label}
            <span className={`tab-mark ${mark?.unsaved ? "unsaved" : mark?.restart ? "restart" : ""}`} aria-hidden="true" />
            {state && <span className="sr-only">, {state}</span>}
          </button>
        );
      })}
    </div>
  );
}

export function SettingsPage() {
  const { data: settings, isPending } = useSettings();
  const toast = useToast();
  const client = useQueryClient();
  const [params, setParams] = useSearchParams();
  const asked = params.get("tab") ?? "";
  const tab: TabId = TAB_IDS.has(asked) ? (asked as TabId) : "general";
  const [draft, setDraft] = useState<Record<string, Value>>({});
  const [testing, setTesting] = useState(false);
  const testAlert = useTestAlert();
  const [restarting, setRestarting] = useState(false);
  const byKey = useMemo(() => Object.fromEntries((settings ?? []).map((s) => [s.key, s])), [settings]);
  const dirty = Object.keys(draft).filter((k) => byKey[k] && (byKey[k]!.secret ? draft[k] !== "" : draft[k] !== byKey[k]!.value));
  const save = useCall((values: Record<string, Value>) => api.patch<Setting[]>("/settings", values), [keys.settings, keys.session]);
  const reset = useCall((key: string) => api.del(`/settings/${key}`), [keys.settings, keys.session]);
  const pending = (settings ?? []).some((s) => s.pending_restart);
  // a setting the page does not place yet still shows, under Advanced
  const other = (settings ?? []).filter((s) => s.applies !== "env" && !KNOWN.has(s.key)).map((s) => s.key);
  const marks = Object.fromEntries(
    TABS.map((t) => {
      const keysOf = keysOfTab(t, other);
      return [t.id, { unsaved: dirty.filter((k) => keysOf.includes(k)).length, restart: keysOf.some((k) => byKey[k]?.pending_restart) }];
    }),
  );
  const dirtyTabs = TABS.filter((t) => marks[t.id]!.unsaved > 0).map((t) => t.label);

  const select = (id: TabId) => {
    if (id === tab) return;
    const next = new URLSearchParams(params);
    if (id === "general") next.delete("tab");
    else next.set("tab", id);
    setParams(next, { preventScrollReset: true, viewTransition: true });
  };

  const submit = () =>
    save.mutate(Object.fromEntries(dirty.map((k) => [k, draft[k]!])), {
      onSuccess: () => {
        setDraft({});
        toast("success", "Settings saved");
      },
      onError: (error) => toast("error", "Not saved", errorText(error)),
    });

  const restart = async () => {
    setRestarting(true);
    try {
      await api.post("/server/restart");
      toast("info", "Restarting", "The panel is back in a few seconds");
      setTimeout(async () => {
        for (let i = 0; i < 30; i++) {
          if (await fetch("/healthz").then((r) => r.ok).catch(() => false)) break;
          await new Promise((r) => setTimeout(r, 1000));
        }
        await client.invalidateQueries();
        setRestarting(false);
        toast("success", "The panel restarted");
      }, 2500);
    } catch (error) {
      setRestarting(false);
      toast("error", "Not restarted", errorText(error));
    }
  };

  const rows = (keysOf: string[]) =>
    keysOf.map((key) => {
      const setting = byKey[key];
      if (!setting) return null;
      const value = key in draft ? draft[key]! : setting.secret ? "" : setting.value;
      return (
        <div className="settings-row" key={key}>
          <div>
            <div className="name">
              {TITLES[key] ?? key}
              {setting.secret && <Tag tone={setting.value ? "positive" : undefined}>{setting.value ? "set" : "not set"}</Tag>}
              {setting.pending_restart ? <Tag tone="caution">applies after a restart</Tag> : setting.applies === "restart" && <Tag>plugin, needs a restart</Tag>}
            </div>
            <div className="desc">{setting.description}</div>
            <div className="env">
              <span>{setting.env}</span>
              {setting.changed_in_panel && !setting.secret && <span>· .env {String(setting.env_value) || "empty"}</span>}
              {setting.changed_in_panel && (
                <Button size="sm" variant="ghost" icon={<RotateCcw />} style={{ height: 24 }} loading={reset.isPending && reset.variables === key}
                  onClick={() => reset.mutate(key, { onSuccess: () => toast("success", `${TITLES[key] ?? key} is back to .env`) })}>
                  Use .env
                </Button>
              )}
            </div>
          </div>
          <Editor setting={setting} value={value} onChange={(v) => setDraft((d) => ({ ...d, [key]: v }))} />
        </div>
      );
    });

  const current = TABS.find((t) => t.id === tab)!;
  return (
    <Page>
      <PageHead title="Settings">
        Changes apply at once and are kept on top of the server's <code>.env</code>. Use .env brings back its value. Passwords are stored encrypted and never shown.
      </PageHead>
      {isPending ? (
        <Skeleton height={400} />
      ) : (
        <>
          {pending && (
            <Callout tone="warning">
              <div className="row-wrap">
                <span className="spacer">Some changes wait for a restart. Instances, links and the web app keep running while the panel restarts.</span>
                <Button size="sm" variant="primary" icon={<RefreshCcw />} loading={restarting} onClick={restart}>Restart the panel</Button>
              </div>
            </Callout>
          )}
          <TabList tab={tab} onSelect={select} marks={marks} />
          <div className="rise tab-panel stack" key={tab} id={`settings-panel-${tab}`} role="tabpanel" aria-labelledby={`settings-tab-${tab}`}>
            {current.groups.map((group) => (
              <Section
                key={group.title}
                title={group.title}
                text={group.text}
                icon={group.title === "E-mail" ? <Mail /> : group.test === "email" ? <Bell /> : group.test ? <Webhook /> : undefined}
                actions={
                  group.title === "E-mail" ? (
                    <Button size="sm" icon={<Send />} onClick={() => setTesting(true)}>Send a test</Button>
                  ) : group.test ? (
                    <Button size="sm" icon={<Send />} loading={testAlert.busy === String(group.test)}
                      onClick={() => testAlert.send(group.test!, group.keys.some((k) => dirty.includes(k)))}>
                      Send a test alert
                    </Button>
                  ) : undefined
                }
              >
                {rows(group.keys)}
              </Section>
            ))}
            {tab === "marketplace" && (
              <Section title="Marketplace" text="Plugins from Sudoo.sh: the connection to the hub, and linking this panel to an account on app.sudoo.sh" icon={<Puzzle />}
                actions={<Link className="btn sm" to="/plugins" viewTransition>Open Plugins</Link>}>
                {rows(["marketplace"])}
                <MarketplaceSettings />
              </Section>
            )}
            {tab === "advanced" && (
              <>
                {other.length > 0 && <Section title="Other" text="Settings this page does not group yet">{rows(other)}</Section>}
                <Section title="Restart the panel" icon={<Plug />}
                  text="Settings marked as a plugin choose which plugin does a job; a change applies when the panel restarts. It is refused while jobs run"
                  actions={<Button size="sm" variant={pending ? "primary" : "default"} icon={<RefreshCcw />} loading={restarting} onClick={restart}>Restart the panel</Button>}>
                  {null}
                </Section>
                <Section title="Server" text="Folders, ports and how the panel is reached. Change these in /opt/odoo/manager/.env" icon={<ServerCog />}>
                  <div className="table-wrap">
                    <table className="table">
                      <tbody>
                        {(settings ?? []).filter((s) => s.applies === "env").map((s) => (
                          <tr key={s.key}>
                            <td className="mono small nowrap">{s.env}</td>
                            <td className="mono small">{String(s.value) || <span className="faint">empty</span>}</td>
                            <td className="small muted">{s.description}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </Section>
              </>
            )}
          </div>
          {dirty.length > 0 && (
            <div className="save-bar">
              <span className="spacer">
                {dirty.length} unsaved {dirty.length === 1 ? "change" : "changes"}
                <span className="count"> in {dirtyTabs.join(", ")}</span>
              </span>
              <Button variant="ghost" onClick={() => setDraft({})}>Discard</Button>
              <Button variant="primary" loading={save.isPending} onClick={submit}>Save</Button>
            </div>
          )}
        </>
      )}
      {testing && <TestEmail onClose={() => setTesting(false)} />}
    </Page>
  );
}
