import { Mail, Palette, RotateCcw, ServerCog, ShieldCheck, SlidersHorizontal, Sparkles, Timer } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { api, errorText } from "@/api/client";
import { keys, useCall, useSettings } from "@/api/queries";
import type { Setting } from "@/api/types";
import { Page, PageHead } from "@/components/Shell";
import { Badge, Button, Skeleton, Switch } from "@/components/ui/basics";
import { useToast } from "@/components/ui/overlay";

const GROUPS: { title: string; icon: ReactNode; text: string; keys: string[] }[] = [
  { title: "Sign-in and security", icon: <ShieldCheck />, text: "Second factors and how long sessions last.", keys: ["require_second_factor", "session_idle_minutes", "session_max_hours"] },
  { title: "Limits", icon: <Timer />, text: "Requests per minute, and how many instances, how often. 0 turns a limit off.", keys: ["rate_limit_reads_per_minute", "rate_limit_changes_per_minute", "max_instances", "max_instances_per_user", "create_cooldown_minutes"] },
  { title: "Instances", icon: <Sparkles />, text: "Defaults of new instances, pausing and code updates.", keys: ["auto_pause", "idle_hours", "auto_update", "auto_upgrade", "poll_minutes", "delete_after_days", "odoo_versions", "odoo_min_version"] },
  { title: "Contact", icon: <Mail />, text: "Who people are told to contact when they need the administrator.", keys: ["admin_contact_name", "admin_contact_email", "admin_contact_phone"] },
  { title: "Appearance", icon: <Palette />, text: "The accent colour of the panel and of its e-mails.", keys: ["brand_color"] },
];

const TITLES: Record<string, string> = {
  require_second_factor: "Second factor required for",
  session_idle_minutes: "Sign out after inactivity (minutes)",
  session_max_hours: "Longest session (hours)",
  rate_limit_reads_per_minute: "Reading requests per minute",
  rate_limit_changes_per_minute: "Changing requests per minute",
  max_instances: "Instances on this server",
  max_instances_per_user: "Instances per user",
  create_cooldown_minutes: "Minutes between two creations",
  auto_pause: "Pause new instances when idle",
  idle_hours: "Pause after (hours without visits)",
  auto_update: "New instances get new code automatically",
  auto_upgrade: "New instances upgrade changed modules",
  poll_minutes: "Check GitHub every (minutes)",
  delete_after_days: "Lifetime of new instances (days)",
  odoo_versions: "Odoo versions offered",
  odoo_min_version: "Oldest version offered",
  admin_contact_name: "Name",
  admin_contact_email: "E-mail",
  admin_contact_phone: "Phone",
  brand_color: "Brand colour",
};

type Value = string | number | boolean;

function Editor({ setting, value, onChange }: { setting: Setting; value: Value; onChange: (value: Value) => void }) {
  if (setting.key === "require_second_factor")
    return (
      <select className="select" value={String(value)} onChange={(e) => onChange(e.target.value)}>
        <option value="none">Nobody</option>
        <option value="admins">Administrators</option>
        <option value="everyone">Everyone</option>
      </select>
    );
  if (setting.key === "brand_color")
    return (
      <div className="row">
        <input type="color" value={String(value)} onChange={(e) => onChange(e.target.value)} style={{ width: 44, height: 38, padding: 2, border: "1px solid var(--border-strong)", borderRadius: 8, background: "var(--surface)" }} />
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

export function SettingsPage() {
  const { data: settings, isPending } = useSettings();
  const toast = useToast();
  const [draft, setDraft] = useState<Record<string, Value>>({});
  const byKey = useMemo(() => Object.fromEntries((settings ?? []).map((s) => [s.key, s])), [settings]);
  const dirty = Object.keys(draft).filter((k) => byKey[k] && draft[k] !== byKey[k]!.value);
  const save = useCall((values: Record<string, Value>) => api.patch<Setting[]>("/settings", values), [keys.settings]);
  const reset = useCall((key: string) => api.del(`/settings/${key}`), [keys.settings]);

  const submit = () =>
    save.mutate(Object.fromEntries(dirty.map((k) => [k, draft[k]!])), {
      onSuccess: () => {
        setDraft({});
        toast("success", "Settings saved", "They apply at once.");
      },
      onError: (error) => toast("error", "Not saved", errorText(error)),
    });

  const readOnly = (settings ?? []).filter((s) => !s.editable);

  return (
    <Page>
      <PageHead eyebrow="Administration" title="Settings">
        Changes apply at once, without a restart, and are kept on top of the server's <code>.env</code>. Reset brings back the <code>.env</code> value.
      </PageHead>
      {isPending ? (
        <Skeleton height={400} />
      ) : (
        <div className="stack stagger">
          {GROUPS.map((group) => (
            <section className="card" key={group.title}>
              <div className="card-head">
                <span className="icon" style={{ color: "var(--primary)", display: "grid" }}>{group.icon}</span>
                <div>
                  <h2>{group.title}</h2>
                  <div className="sub">{group.text}</div>
                </div>
              </div>
              {group.keys.map((key) => {
                const setting = byKey[key];
                if (!setting) return null;
                const value = key in draft ? draft[key]! : setting.value;
                return (
                  <div className="settings-row" key={key}>
                    <div>
                      <div className="name">
                        {TITLES[key] ?? key}
                        {setting.changed_in_panel && <Badge tone="primary">changed here</Badge>}
                        {dirty.includes(key) && <Badge tone="warning">unsaved</Badge>}
                      </div>
                      <div className="desc">{setting.description}</div>
                      <div className="env">
                        {setting.env}
                        {setting.changed_in_panel && <> · .env: {String(setting.env_value)}</>}
                        {setting.changed_in_panel && (
                          <Button size="sm" variant="ghost" icon={<RotateCcw />} style={{ height: 22, marginLeft: 6 }} loading={reset.isPending && reset.variables === key}
                            onClick={() => reset.mutate(key, { onSuccess: () => toast("success", `${TITLES[key] ?? key}: back to .env`) })}>
                            Reset
                          </Button>
                        )}
                      </div>
                    </div>
                    <Editor setting={setting} value={value} onChange={(v) => setDraft((d) => ({ ...d, [key]: v }))} />
                  </div>
                );
              })}
            </section>
          ))}

          <section className="card">
            <div className="card-head">
              <span style={{ color: "var(--text-3)", display: "grid" }}><ServerCog /></span>
              <div>
                <h2>Server configuration</h2>
                <div className="sub">Read when the panel starts: change these in <code>/opt/odoo/manager/.env</code>.</div>
              </div>
            </div>
            <div className="table-wrap">
              <table className="table">
                <tbody>
                  {readOnly.map((s) => (
                    <tr key={s.key}>
                      <td className="mono small nowrap">{s.env}</td>
                      <td className="mono small">{s.secret ? (s.value ? <Badge tone="success">set</Badge> : <Badge>not set</Badge>) : String(s.value) || <span className="faint">empty</span>}</td>
                      <td className="small muted">{s.description}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          {dirty.length > 0 && (
            <div className="save-bar">
              <SlidersHorizontal size={18} />
              <span className="spacer">{dirty.length} unsaved {dirty.length === 1 ? "change" : "changes"}</span>
              <Button variant="ghost" onClick={() => setDraft({})}>Discard</Button>
              <Button variant="primary" loading={save.isPending} onClick={submit}>Save changes</Button>
            </div>
          )}
        </div>
      )}
    </Page>
  );
}
