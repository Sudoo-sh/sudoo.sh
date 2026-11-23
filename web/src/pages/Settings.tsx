import { useQueryClient } from "@tanstack/react-query";
import { Bell, CloudUpload, Gauge, SlidersHorizontal, Mail, Plug, Puzzle, RefreshCcw, RotateCcw, Send, ServerCog, Webhook } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router";
import { api, errorText } from "@/api/client";
import { keys, useCall, useCapacity, useServer, useSettings } from "@/api/queries";
import { RESOURCE_KNOBS, type Server, type Setting, type Stage } from "@/api/types";
import { LicenceSettings } from "@/components/LicenceSettings";
import { MarketplaceSettings } from "@/components/Marketplace";
import { PlanSettings } from "@/components/PlanSettings";
import { ManagedSubdomainsCard } from "@/components/ManagedSubdomainsCard";
import { PlanCard, PremiumFeaturesCard, PremiumSlot } from "@/premium/components";
import { usePremium } from "@/premium/loader";
import { Page, PageHead, useMe, useSubCrumb } from "@/components/Shell";
import { Button, Callout, Skeleton, Switch, Tag } from "@/components/app/basics";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Input } from "@/components/ui/input";
import { SelectBox } from "@/components/app/select";
import { Modal, useToast } from "@/components/app/overlay";
import { TabInk } from "@/components/app/motion";
import { useLocale, useT } from "@/i18n";
import { bytes } from "@/lib/format";
import common from "@/i18n/common";
import words from "@/i18n/settings";

/** `test`: the group has a "Send a test alert" button, by e-mail or to that webhook. */
type GroupId = Exclude<keyof (typeof words)["en"]["groups"], never>;
type Group = { id: GroupId | `webhook${1 | 2 | 3}`; keys: string[]; test?: "email" | 1 | 2 | 3 };
type TabId = "plan" | "support" | "clients" | "general" | "instances" | "resources" | "odoo" | "backups" | "security" | "domains" | "email" | "servers" | "alerts" | "github" | "marketplace" | "advanced" | "about";

const webhookGroup = (n: 1 | 2 | 3): Group => ({
  id: `webhook${n}`,
  keys: [`alert_webhook_${n}_url`, `alert_webhook_${n}_format`, `alert_webhook_${n}_secret`],
  test: n,
});

/** The tabs of the page and the groups of settings in each (their words are in i18n/settings.ts). Every setting that
 *  can be changed is in one group only; one the page does not know yet shows under Advanced, Other. */
const TABS: { id: TabId; groups: Group[] }[] = [
  {
    id: "general",
    groups: [
      { id: "contact", keys: ["admin_contact_name", "admin_contact_email", "admin_contact_phone", "brand_color", "default_language"] },
      { id: "help", keys: ["support_url", "hub_support_url", "docs_url", "plan_pricing_url", "plan_account_url"] },
    ],
  },
  {
    id: "instances",
    groups: [
      { id: "newInstances", keys: ["auto_pause", "idle_hours", "auto_update", "auto_upgrade", "delete_after_days", "odoo_admin_password"] },
      { id: "stages", keys: ["max_production_instances", "production_auto_update", "staging_delete_after_days"] },
      {
        id: "projects",
        keys: ["project_production_branches", "project_auto_development", "project_max_auto_development", "project_development_demo_data", "project_staging_on_push", "project_branch_deleted", "project_upgrade_test_on_push", "project_builds_kept",
          "project_development_on_new_commit", "project_module_install", "project_modules", "project_test_suite", "project_test_tags", "project_staging_tests", "project_test_timeout_minutes", "project_commit_statuses", "project_email_pusher",
          "project_promotion_backup_hours", "project_promotion_blocks", "project_pr_previews", "project_pr_preview_limit", "project_pr_previews_forks"],
      },
      { id: "howMany", keys: ["max_instances", "max_instances_per_user", "create_cooldown_minutes", "max_initial_modules"] },
      { id: "capacity", keys: ["capacity_reserve_mb", "capacity_reserve_percent", "capacity_instance_memory_mb", "capacity_warning_percent", "max_running_instances"] },
      { id: "containers", keys: ["odoo_memory_limit", "odoo_cpu_limit", "db_memory_limit", "db_cpu_limit", "container_pids_limit", "container_log_max_size", "container_log_max_files", "log_download_max_lines", "drift_action"] },
      { id: "gateway", keys: ["gateway_max_upload_mb", "gateway_idle_timeout_seconds", "gateway_websocket_max_message_mb"] },
    ],
  },
  { id: "resources", groups: [{ id: "resourceRules", keys: ["resources_computed_limits", "resources_base_memory_mb", "resources_reservation_check", "capacity_overcommit"] }, { id: "storageQuotas", keys: ["storage_quota_gb_production", "storage_quota_gb_staging", "storage_quota_gb_development", "storage_warning_percent", "storage_measure_minutes"] }] },
  {
    id: "odoo",
    groups: [
      { id: "versions", keys: ["odoo_versions", "odoo_min_version", "prepull_images", "odoo_image_check_hours", "odoo_image", "postgres_image"] },
      { id: "enterprise", keys: ["enterprise_source", "enterprise_path", "enterprise_git_url", "enterprise_git_branch", "enterprise_marker"] },
      { id: "upgrades", keys: ["upgrade_methods", "openupgrade_repository", "openupgrade_refs", "openupgrade_lib", "upgrade_rollback_minutes", "upgrade_max_hours", "upgrade_service_url"] },
    ],
  },
  {
    id: "backups",
    groups: [
      { id: "backups", keys: ["daily_backups", "staging_daily_backups", "daily_backup_time", "timezone", "rollback_hours", "safe_updates", "import_max_gb"] },
      {
        id: "offsiteStorage",
        keys: ["offsite_backups", "offsite_endpoint", "offsite_region", "offsite_bucket", "offsite_prefix", "offsite_addressing", "offsite_access_key_id", "offsite_secret_access_key"],
      },
      { id: "offsiteRules", keys: ["offsite_other_stages", "offsite_kinds", "offsite_verify_days", "offsite_timeout_seconds"] },
      { id: "offsiteRetention", keys: ["offsite_keep_daily_production", "offsite_keep_monthly_production", "offsite_keep_daily_other", "offsite_keep_monthly_other"] },
    ],
  },
  {
    id: "security",
    groups: [
      { id: "signIn", keys: ["require_second_factor", "second_factors", "session_idle_minutes", "session_max_hours", "auth"] },
      { id: "signInWays", keys: ["sign_in_methods", "client_sign_in_methods", "email_code_sign_in", "email_code_sends_per_hour", "github_sign_in", "github_sign_in_orgs", "github_sign_in_role", "github_sign_in_skips_second_factor", "github_access_from_repositories", "github_access_check_minutes"] },
      { id: "signInEmail", keys: ["sign_in_email", "sign_in_email_opt_out", "recovery_codes_count"] },
      { id: "invitations", keys: ["invitation_hours", "invitation_password_fallback", "invitations_per_hour"] },
      { id: "requests", keys: ["rate_limit_reads_per_minute", "rate_limit_changes_per_minute", "rate_limiter"] },
      { id: "webShell", keys: ["shell_enabled", "shell_roles", "shell_production", "shell_root", "shell_idle_minutes", "shell_max_minutes", "shell_max_per_user", "shell_max_per_instance", "shell_ticket_seconds"] },
      { id: "shellRecordings", keys: ["shell_recording", "shell_recording_days", "shell_recording_max_mb", "shell_recordings_total_mb"] },
      { id: "profiler", keys: ["profiler_enabled", "profiler_roles", "profiler_production", "profiler_max_minutes", "profiler_keep", "profiler_rate", "profiler_image", "audit_export_max_rows"] },
    ],
  },
  {
    id: "domains",
    groups: [
      { id: "publicAddress", keys: ["exposure", "panel_domain", "public_host", "panel_managed_name"] },
      { id: "customDomains", keys: ["custom_domains", "domain_dns_target", "domains_per_instance"] },
      { id: "certificates", keys: ["acme_directory", "acme_email", "certificate_renew_days", "certificate_retry_max_hours"] },
      { id: "checks", keys: ["domain_dns_timeout_seconds", "domain_http_probe", "domain_http_port", "domain_https_port"] },
      { id: "managedSubdomains", keys: ["managed_subdomains", "managed_subdomain_company", "managed_subdomains_check_minutes"] },
    ],
  },
  {
    id: "email",
    groups: [
      { id: "email", keys: ["mailer", "mail_from", "smtp_host", "smtp_port", "smtp_security", "smtp_username", "smtp_password", "mail_templates_dir"] },
      { id: "mailCatcher", keys: ["mail_catcher_retention_days", "mail_catcher_max_messages", "mail_catcher_max_mb", "mail_catcher_max_message_mb", "mail_catcher_release"] },
    ],
  },
  {
    id: "servers",
    groups: [
      { id: "servers", keys: ["server_name", "server_region", "servers_panel_url", "servers_join_code_minutes", "servers_heartbeat_seconds", "servers_offline_seconds", "servers_request_seconds", "servers_wait_minutes", "servers_instances_kept", "servers_report_kb", "servers_log_entries_per_day"] },
      { id: "moves", keys: ["move_max_backup_gb", "move_chunk_mb", "move_at_once", "move_health_seconds"] },
    ],
  },
  {
    id: "alerts",
    groups: [
      { id: "recipients", keys: ["alerts", "alert_super_admins", "alert_emails"], test: "email" },
      { id: "jobs", keys: ["alert_job_failed", "alert_update_failed", "alert_backup_failed", "alert_drift"] },
      { id: "instancesServer", keys: ["alert_instance_down", "alert_instance_down_minutes", "alert_public_link", "alert_public_link_minutes", "alert_memory", "alert_memory_minutes", "alert_disk", "alert_disk_free_percent", "alert_storage"] },
      { id: "certificateAlerts", keys: ["alert_certificate", "alert_certificate_days"] },
      { id: "offsiteAlerts", keys: ["alert_offsite_failed", "alert_offsite_missing", "alert_offsite_missing_hours"] },
      { id: "howOften", keys: ["alert_recovery", "alert_quiet_minutes", "alert_digest", "alert_digest_time", "alert_webhook_timeout_seconds"] },
      webhookGroup(1),
      webhookGroup(2),
      webhookGroup(3),
    ],
  },
  { id: "github", groups: [{ id: "codeUpdates", keys: ["poll_minutes", "update_triggers", "github_webhook_secret"] }] },
  { id: "marketplace", groups: [] },
  {
    id: "plan",
    groups: [
      { id: "premium", keys: ["premium_auto_install", "premium_check_hours", "premium_retry_minutes", "premium_watch_minutes", "premium_binding_renew_days", "premium_max_package_mb", "public_ipv4", "public_ipv6"] },
    ],
  },
  { id: "support", groups: [{ id: "supportAccess", keys: ["support_access", "support_access_max_hours"] }] },
  {
    id: "clients",
    groups: [
      { id: "portalAddress", keys: ["portal_domain", "portal_name"] },
      { id: "clientSwitch", keys: ["client_open_hours", "client_open_max_hours", "client_request_hours", "client_request_max_hours", "client_request_expire_hours", "client_emergency_access", "client_emergency_minutes", "client_confirmations", "client_confirm_minutes"] },
    ],
  },
  { id: "advanced", groups: [{ id: "monitoring", keys: ["metrics_interval_seconds", "cloudflared_image"] }] },
  { id: "about", groups: [] },
];
const STAGES: Stage[] = ["production", "staging", "development"];
/** The Resources tab: two tables, a row per setting and a column per stage (setting `<row>_<stage>` for the
 *  priority weights, `<stage>_<row>` for the default limits). */
const PRIORITY_ROWS = ["priority_cpu_weight", "priority_memory_reservation", "priority_db_memory_reservation", "priority_oom_score"] as const;
const priorityKey = (row: string, stage: Stage) => `${row}_${stage}`;
const limitKey = (row: string, stage: Stage) => `${stage}_${row}`;
const maximumKey = (row: string) => `resources_max_${row}`;
const RESOURCE_KEYS = [
  ...PRIORITY_ROWS.flatMap((row) => STAGES.map((stage) => priorityKey(row, stage))),
  ...RESOURCE_KNOBS.flatMap((row) => STAGES.map((stage) => limitKey(row, stage))),
  ...RESOURCE_KNOBS.map(maximumKey),
];
const TAB_IDS = new Set<string>(TABS.map((t) => t.id));
const KNOWN = new Set<string>([...TABS.flatMap((t) => t.groups.flatMap((g) => g.keys)), "marketplace", "licence_required_version", "link_deadline_days", ...RESOURCE_KEYS]);
const keysOfTab = (tab: (typeof TABS)[number], other: string[]) => [
  ...tab.groups.flatMap((g) => g.keys),
  ...(tab.id === "marketplace" ? ["marketplace"] : []),
  ...(tab.id === "resources" ? RESOURCE_KEYS : []),
  ...(tab.id === "advanced" ? other : []),
  ...(tab.id === "about" ? ["licence_required_version", "link_deadline_days"] : []),
];

/** A group's title and explanation, in the language shown. */
function groupWords(t: (typeof words)["en"], group: Group): { title: string; text: string } {
  if (group.id.startsWith("webhook")) {
    const n = Number(group.id.slice(-1));
    return { title: t.webhook(n), text: n === 1 ? t.webhookFirst : t.webhookOther };
  }
  return t.groups[group.id as GroupId];
}

/** A setting's explanation: the server's on the English page, this page's own on the Arabic one when it has one. */
const describe = (t: (typeof words)["en"], locale: string, setting: Setting) =>
  (locale === "en" ? setting.description : t.descriptions[setting.key]) ?? setting.description;

type Value = string | number | boolean;

function Editor({ setting, value, onChange }: { setting: Setting; value: Value; onChange: (value: Value) => void }) {
  const t = useT(words);
  if (setting.secret)
    return (
      <Input
        type="password"
        autoComplete="new-password"
        placeholder={setting.value ? t.editor.secretSet : t.editor.secretNotSet}
        value={typeof value === "string" ? value : ""}
        onChange={(e) => onChange(e.target.value)}
      />
    );
  const choices = t.choices[setting.key];
  if (choices)
    return (
      <SelectBox value={String(value)} onValueChange={(value) => onChange(value)}>
        {Object.entries(choices).map(([v, label]) => <option key={v} value={v}>{label}</option>)}
      </SelectBox>
    );
  if (setting.key === "brand_color")
    return (
      <div className="row">
        <input type="color" aria-label={t.editor.pickColour} value={String(value)} onChange={(e) => onChange(e.target.value)}
          style={{ width: 40, height: 36, padding: 2, border: "1px solid var(--hairline-strong)", borderRadius: 8, background: "var(--surface)" }} />
        <Input className="mono" dir="ltr" value={String(value)} onChange={(e) => onChange(e.target.value)} />
      </div>
    );
  if (setting.type === "boolean") return <div style={{ justifySelf: "end" }}><Switch checked={Boolean(value)} onChange={onChange} /></div>;
  if (setting.type === "integer" || setting.type === "number")
    return (
      <Input className="tabular" type="number" min={setting.minimum ?? undefined} step={setting.type === "integer" ? 1 : "any"} value={String(value)}
        onChange={(e) => onChange(e.target.value === "" ? "" : Number(e.target.value))} />
    );
  return <Input dir="auto" value={String(value)} onChange={(e) => onChange(e.target.value)} />;
}

function Section({ title, text, icon, actions, children }: { title: string; text: ReactNode; icon?: ReactNode; actions?: ReactNode; children: ReactNode }) {
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
  const t = useT(words).testEmail;
  const c = useT(common).common;
  const send = async () => {
    setBusy(true);
    try {
      await api.post("/settings/test-email", { to });
      toast("success", t.sent, t.check(to));
      onClose();
    } catch (error) {
      toast("error", t.notSent, errorText(error));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title={t.title} description={t.text} onClose={onClose}
      footer={<><Button onClick={onClose}>{c.cancel}</Button><Button variant="primary" icon={<Send />} loading={busy} disabled={!to} onClick={send}>{c.send}</Button></>}>
      <Input type="email" dir="ltr" autoFocus value={to} onChange={(e) => setTo(e.target.value)} />
    </Modal>
  );
}

/** Sends a test alert with the saved settings: by e-mail to every alert recipient, or to one webhook. */
function useTestAlert() {
  const toast = useToast();
  const [busy, setBusy] = useState<string | null>(null);
  const t = useT(words).testAlert;
  const send = async (target: "email" | 1 | 2 | 3, unsaved: boolean) => {
    const id = String(target);
    setBusy(id);
    try {
      const body = target === "email" ? { channel: "email" } : { channel: "webhook", webhook: target };
      const result = await api.post<{ sent_to?: string[]; status?: number }>("/alerts/test", body);
      const where = target === "email" ? t.to(result.sent_to?.join(", ") ?? "") : t.answered(target, String(result.status));
      toast("success", t.sent, where + (unsaved ? t.unsaved : ""));
    } catch (error) {
      toast("error", t.notSent, errorText(error));
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
  const t = useT(words);
  const rtl = useLocale() === "ar";
  useEffect(() => {
    // on a narrow screen the tabs scroll sideways: keep the chosen one in view
    const chosen = list.current?.querySelector<HTMLElement>('[aria-selected="true"]');
    const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
    chosen?.scrollIntoView({ block: "nearest", inline: "nearest", behavior: reduce ? "auto" : "smooth" });
  }, [tab]);
  const move = (event: KeyboardEvent<HTMLDivElement>) => {
    const index = TABS.findIndex((t) => t.id === tab);
    // the arrow keys follow the reading direction: in Arabic the next tab is on the left
    const forward = rtl ? "ArrowLeft" : "ArrowRight";
    const backward = rtl ? "ArrowRight" : "ArrowLeft";
    const next =
      event.key === forward ? (index + 1) % TABS.length
      : event.key === backward ? (index - 1 + TABS.length) % TABS.length
      : event.key === "Home" ? 0
      : event.key === "End" ? TABS.length - 1
      : null;
    if (next === null) return;
    event.preventDefault();
    onSelect(TABS[next]!.id);
    list.current?.querySelector<HTMLElement>(`#settings-tab-${TABS[next]!.id}`)?.focus();
  };
  return (
    <div className="tabs settings-tabs" role="tablist" aria-label={t.tabsLabel} ref={list} onKeyDown={move}>
      {TABS.map((item) => {
        const mark = marks[item.id];
        const state = [
          mark?.unsaved ? t.tabState.unsaved(mark.unsaved) : "",
          mark?.restart ? t.tabState.restart : "",
        ].filter(Boolean).join(t.saveBar.separator);
        return (
          <button
            key={item.id}
            id={`settings-tab-${item.id}`}
            role="tab"
            type="button"
            aria-selected={item.id === tab}
            aria-controls={`settings-panel-${item.id}`}
            tabIndex={item.id === tab ? 0 : -1}
            className={item.id === tab ? "active" : ""}
            title={state || undefined}
            onClick={() => onSelect(item.id)}
          >
            {t.tabs[item.id]}
            <span className={`tab-mark ${mark?.unsaved ? "unsaved" : mark?.restart ? "restart" : ""}`} aria-hidden="true" />
            {state && <span className="sr-only">{t.saveBar.separator}{state}</span>}
          </button>
        );
      })}
      <TabInk />
    </div>
  );
}

/** Settings, Addresses and domains: how people reach this server now, and what a change of EXPOSURE does. */
function ExposureNotice({ saved, chosen }: { saved: string; chosen: string }) {
  const t = useT(words);
  const server = useServer().data;
  const now = server?.public_address;
  const label = (mode: string) => t.choices.exposure?.[mode] ?? mode;
  const certificate = now?.status ? t.exposure.certificate[now.status] : "";
  const target = chosen === "cloudflare-quick" ? "tunnel" : chosen;
  return (
    <div className="card-body stack-sm">
      <p className="small muted">
        {t.exposure.now(label(saved), server?.panel_url ? <bdi dir="ltr" className="mono">{server.panel_url}</bdi> : t.exposure.noAddress)}
      </p>
      {certificate && <p className="small muted">{certificate} {now?.reason && <bdi>{now.reason}</bdi>}</p>}
      <ManagedPanelName address={now} />
      {chosen !== saved && t.exposure.to[target] && (
        <Callout tone="warning">
          <div className="stack-sm">
            <span><strong>{t.exposure.change}</strong> {t.exposure.to[target]}</span>
            <span>{t.exposure.keepOpen}</span>
          </div>
        </Callout>
      )}
    </div>
  );
}

/** Settings, Addresses and domains: the panel's own managed name from Sudoo.sh, when it has or wants one. */
function ManagedPanelName({ address }: { address: NonNullable<Server["public_address"]> | undefined }) {
  const t = useT(words).exposure.managedName;
  const managed = address?.managed_name;
  if (!managed || !managed.wanted) return null;
  let text: ReactNode;
  if (managed.hostname) text = t.serving(<bdi dir="ltr" className="mono">{managed.hostname}</bdi>);
  else if (managed.error) text = <>{t.failed} <bdi>{managed.error}</bdi></>;
  else if (managed.checked_at && !managed.offered) text = t.notOffered;
  else text = t.waiting;
  return <p className="small muted">{text}</p>;
}

/** One cell of a resources table: the setting's editor, named for screen readers by its row and its stage. */
function Cell({ setting, value, label, onChange }: { setting: Setting | undefined; value: Value; label: string; onChange: (value: Value) => void }) {
  if (!setting) return <TableCell />;
  const number = setting.type === "integer" || setting.type === "number";
  return (
    <TableCell>
      <Input className="tabular" dir="ltr" aria-label={label} type={number ? "number" : "text"} min={setting.minimum ?? undefined}
        step={setting.type === "integer" ? 1 : "any"} value={String(value)}
        onChange={(e) => onChange(number ? (e.target.value === "" ? "" : Number(e.target.value)) : e.target.value)} />
    </TableCell>
  );
}

/** Settings, Resources: the priority weights of each stage and the default limits of each stage, as two tables. */
function ResourceSettings({ byKey, draft, onChange }: { byKey: Record<string, Setting>; draft: Record<string, Value>; onChange: (key: string, value: Value) => void }) {
  const t = useT(words);
  const r = t.resources;
  const c = useT(common);
  const capacity = useCapacity();
  const valueOf = (key: string): Value => (key in draft ? draft[key]! : (byKey[key]?.value as Value) ?? "");
  const table = (rows: readonly string[], keyOf: (row: string, stage: Stage) => string, extra?: (row: string) => string) => (
    <Table className="resources-table">
      <TableHeader>
        <TableRow>
          <TableHead>{r.knob}</TableHead>
          {STAGES.map((stage) => <TableHead key={stage}>{c.stages[stage]?.label ?? stage}</TableHead>)}
          {extra && <TableHead title={r.maximumText}>{r.maximum}</TableHead>}
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row) => {
          const words = r.rows[row as keyof typeof r.rows];
          return (
            <TableRow key={row}>
              <TableHead scope="row">
                <div>{words.label}</div>
                <div className="small faint">{words.hint}</div>
              </TableHead>
              {STAGES.map((stage) => {
                const key = keyOf(row, stage);
                return <Cell key={stage} setting={byKey[key]} value={valueOf(key)} label={r.cell(words.label, c.stages[stage]?.label ?? stage)} onChange={(v) => onChange(key, v)} />;
              })}
              {extra && <Cell setting={byKey[extra(row)]} value={valueOf(extra(row))} label={r.cell(words.label, r.maximum)} onChange={(v) => onChange(extra(row), v)} />}
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
  const reserved = capacity?.reservations?.total_bytes;
  const usable = capacity?.memory.usable_bytes;
  return (
    <>
      <Section title={r.priorityTitle} text={r.priorityText} icon={<Gauge />}>
        {table(PRIORITY_ROWS, priorityKey)}
        {reserved != null && usable != null && (
          <div className="card-body stack-sm">
            <p className="small muted">{r.reserved(bytes(reserved), bytes(usable))}</p>
            {reserved > usable && <Callout tone="warning">{r.overReserved}</Callout>}
          </div>
        )}
      </Section>
      <Section title={r.defaultsTitle} text={r.defaultsText} icon={<SlidersHorizontal />}>
        {table(RESOURCE_KNOBS, limitKey, maximumKey)}
        <div className="card-body"><p className="small muted">{r.maximumText}</p></div>
      </Section>
    </>
  );
}

export function SettingsPage() {
  const me = useMe();
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
  const t = useT(words);
  const all = useT(common);
  const locale = useLocale();
  // the settings of off-site copies show while the premium off-site package runs (they are kept either way)
  const offsiteRuns = usePremium().data?.packages.some((p) => p.id === "offsite" && p.status === "active") ?? false;
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
  const dirtyTabs = TABS.filter((tab) => marks[tab.id]!.unsaved > 0).map((tab) => t.tabs[tab.id]);

  // the content of a tab comes in only when the visitor moves between tabs (opening the page has its own transition)
  const [moved, setMoved] = useState(false);
  const select = (id: TabId) => {
    if (id === tab) return;
    setMoved(true);
    const next = new URLSearchParams(params);
    if (id === "general") next.delete("tab");
    else next.set("tab", id);
    // no page transition: the title and the tabs stay still, only the tab's content comes in (.tab-enter)
    setParams(next, { preventScrollReset: true });
  };

  const submit = () =>
    save.mutate(Object.fromEntries(dirty.map((k) => [k, draft[k]!])), {
      onSuccess: () => {
        setDraft({});
        toast("success", t.saved);
      },
      onError: (error) => toast("error", t.notSaved, errorText(error)),
    });

  const restart = async () => {
    setRestarting(true);
    try {
      await api.post("/server/restart");
      toast("info", t.restarting, t.restartingText);
      setTimeout(async () => {
        for (let i = 0; i < 30; i++) {
          if (await fetch("/healthz").then((r) => r.ok).catch(() => false)) break;
          await new Promise((r) => setTimeout(r, 1000));
        }
        await client.invalidateQueries();
        setRestarting(false);
        toast("success", t.restarted);
      }, 2500);
    } catch (error) {
      setRestarting(false);
      toast("error", t.notRestarted, errorText(error));
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
              {t.titles[key] ?? key}
              {setting.secret && <Tag tone={setting.value ? "positive" : undefined}>{setting.value ? t.tags.set : t.tags.notSet}</Tag>}
              {setting.pending_restart ? <Tag tone="caution">{t.tags.afterRestart}</Tag> : setting.applies === "restart" && <Tag>{t.tags.plugin}</Tag>}
            </div>
            <div className="desc">{describe(t, locale, setting)}</div>
            <div className="env">
              <span dir="ltr">{setting.env}</span>
              {setting.changed_in_panel && !setting.secret && <span>· <span dir="ltr">.env</span> {String(setting.env_value) ? <bdi>{String(setting.env_value)}</bdi> : t.env.empty}</span>}
              {setting.changed_in_panel && (
                <Button size="sm" variant="ghost" icon={<RotateCcw />} style={{ height: 24 }} loading={reset.isPending && reset.variables === key}
                  onClick={() => reset.mutate(key, { onSuccess: () => toast("success", t.env.backToEnv(t.titles[key] ?? key)) })}>
                  {t.env.useEnv}
                </Button>
              )}
            </div>
          </div>
          <Editor setting={setting} value={value} onChange={(v) => setDraft((d) => ({ ...d, [key]: v }))} />
        </div>
      );
    });

  const current = TABS.find((t) => t.id === tab)!;
  useSubCrumb(t.tabs[tab]);
  return (
    <Page>
      <PageHead title={t.title}>
        {t.lead(<code key="env">.env</code>)}
      </PageHead>
      {isPending ? (
        <Skeleton height={400} />
      ) : (
        <>
          {pending && (
            <Callout tone="warning">
              <div className="row-wrap">
                <span className="spacer">{t.pending}</span>
                <Button size="sm" variant="primary" icon={<RefreshCcw />} loading={restarting} onClick={restart}>{t.restart}</Button>
              </div>
            </Callout>
          )}
          <TabList tab={tab} onSelect={select} marks={marks} />
          <div className={moved ? "tab-enter stack" : "stack"} key={tab} id={`settings-panel-${tab}`} role="tabpanel" aria-labelledby={`settings-tab-${tab}`}>
            {current.groups.filter((group) => !group.id.startsWith("offsite") || offsiteRuns).map((group) => (
              <Section
                key={group.id}
                {...groupWords(t, group)}
                icon={group.id === "email" ? <Mail /> : group.id === "offsiteStorage" ? <CloudUpload /> : group.test === "email" ? <Bell /> : group.test ? <Webhook /> : undefined}
                actions={
                  group.id === "email" ? (
                    <div className="row">
                      {me.permissions.includes("mail.manage") && <Button asChild variant="ghost" size="sm"><Link to="/email-templates" viewTransition>{t.testEmail.texts}</Link></Button>}
                      <Button size="sm" icon={<Send />} onClick={() => setTesting(true)}>{t.testEmail.open}</Button>
                    </div>
                  ) : group.test ? (
                    <Button size="sm" icon={<Send />} loading={testAlert.busy === String(group.test)}
                      onClick={() => testAlert.send(group.test!, group.keys.some((k) => dirty.includes(k)))}>
                      {t.testAlert.send}
                    </Button>
                  ) : undefined
                }
              >
                {rows(group.keys)}
                {group.id === "publicAddress" && byKey.exposure && (
                  <ExposureNotice saved={String(byKey.exposure.value)} chosen={String(draft.exposure ?? byKey.exposure.value)} />
                )}
              </Section>
            ))}
            {tab === "resources" && <ResourceSettings byKey={byKey} draft={draft} onChange={(key, v) => setDraft((d) => ({ ...d, [key]: v }))} />}
            {tab === "backups" && <PremiumSlot name="settings.offsite" props={{ dirty }} />}
            {tab === "domains" && <ManagedSubdomainsCard />}
            {tab === "marketplace" && (
              <Section title={t.marketplace.title} text={t.marketplace.text} icon={<Puzzle />}
                actions={<Button asChild size="sm"><Link to="/plugins" viewTransition>{t.marketplace.open}</Link></Button>}>
                {rows(["marketplace"])}
                <MarketplaceSettings />
              </Section>
            )}
            {tab === "plan" && (
              <>
                <PlanSettings />
                <PremiumFeaturesCard />
              </>
            )}
            {tab === "support" && <PremiumSlot name="settings.support" fallback={<PlanCard id="support" />} />}
            {tab === "about" && <LicenceSettings>{rows(["licence_required_version", "link_deadline_days"])}</LicenceSettings>}
            {tab === "advanced" && (
              <>
                {other.length > 0 && <Section title={t.other.title} text={t.other.text}>{rows(other)}</Section>}
                <Section title={t.restart} icon={<Plug />}
                  text={t.restartSection}
                  actions={<Button size="sm" variant={pending ? "primary" : "default"} icon={<RefreshCcw />} loading={restarting} onClick={restart}>{t.restart}</Button>}>
                  {null}
                </Section>
                <Section title={t.server.title} text={<>{t.server.text(<code key="p">/opt/odoo/manager/.env</code>)}</>} icon={<ServerCog />}>
                  <Table>
                    <TableBody>
                      {(settings ?? []).filter((s) => s.applies === "env").map((s) => (
                        <TableRow key={s.key}>
                          <TableCell className="mono small nowrap" dir="ltr">{s.env}</TableCell>
                          <TableCell className="mono small">{String(s.value) ? <bdi dir="ltr">{String(s.value)}</bdi> : <span className="faint">{t.env.empty}</span>}</TableCell>
                          <TableCell className="small muted">{describe(t, locale, s)}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </Section>
              </>
            )}
          </div>
          {dirty.length > 0 && (
            <div className="save-bar">
              <span className="spacer">
                {t.saveBar.unsaved(dirty.length)}
                <span className="count">{t.saveBar.in(dirtyTabs.join(t.saveBar.separator))}</span>
              </span>
              <Button variant="ghost" onClick={() => setDraft({})}>{t.saveBar.discard}</Button>
              <Button variant="primary" loading={save.isPending} onClick={submit}>{all.common.save}</Button>
            </div>
          )}
        </>
      )}
      {testing && <TestEmail onClose={() => setTesting(false)} />}
    </Page>
  );
}
