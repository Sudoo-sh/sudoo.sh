// Production access rules (beta), in a production instance's Settings tab: who may open the instance, by country and
// by address, for the back office or the whole site, with exceptions. A change goes through three steps: the rules,
// a check of what they would do (and whether the person's own address still gets in), then a strong confirmation
// (the instance's name typed and a one-time code). Shown only when the panel has ACCESS_RULES_BETA on.

import { Check, Globe2, Plus, Search, ShieldCheck, ShieldOff, Trash2, X } from "lucide-react";
import { useMemo, useState } from "react";
import { api, errorText } from "@/api/client";
import { useCall } from "@/api/queries";
import {
  accessKey,
  COUNTRIES,
  countryName,
  flag,
  looksLikeNetwork,
  useAccessRules,
  type AccessMode,
  type AccessPreset,
  type AccessPreview,
  type AccessRules,
  type AccessRulesState,
  type AccessScope,
  type CodeMethod,
  type Network,
} from "@/api/access-rules";
import type { Instance } from "@/api/types";
import { useLocale, useT } from "@/i18n";
import words from "@/i18n/access-rules";
import { ago } from "@/lib/format";
import { Button, Callout, Field, Segmented, Switch, Tag } from "./app/basics";
import { Modal, useToast } from "./app/overlay";
import { OtpInput } from "./app/OtpInput";
import { Input } from "./ui/input";
import { Textarea } from "./ui/textarea";

const PRESETS: AccessPreset[] = ["payments", "webhooks", "portal", "livechat", "mail_links"];

/** The card in the Settings tab: the rules as they are, and the way to change them. Nothing when the panel does not
 *  offer the feature or the instance is not in production. */
export function AccessRulesCard({ instance }: { instance: Instance }) {
  const production = instance.stage === "production";
  const { data } = useAccessRules(instance.name, production);
  const t = useT(words);
  const locale = useLocale();
  const [editing, setEditing] = useState(false);
  if (!production || !data?.available || !data.rules) return null;
  const rules = data.rules;
  const country = (code: string) => `${flag(code)} ${countryName(code, locale)}`;
  return (
    <section className="card access-rules">
      <div className="card-head">
        <h2 className="row" style={{ gap: 8 }}>
          {data.active ? <ShieldCheck size={18} strokeWidth={1.75} /> : <ShieldOff size={18} strokeWidth={1.75} />}
          {t.title}
        </h2>
        <Tag tone="accent">{t.beta}</Tag>
        <span className="spacer" />
        <Tag tone={data.active ? "positive" : undefined}>{data.active ? t.on : t.off}</Tag>
        {data.can_change && (
          <Button size="sm" onClick={() => setEditing(true)}>{rules.enabled ? t.edit : t.setUp}</Button>
        )}
      </div>
      <div className="card-body stack">
        <p className="small muted">{t.intro}</p>
        {!rules.enabled ? (
          <p className="small">{t.offText}</p>
        ) : (
          <dl className="access-summary">
            <dt>{t.modeLabel}</dt>
            <dd>
              <b>{t.modes[rules.mode]}</b> · <span className="muted">{t.modeText[rules.mode]}</span>
              {rules.mode !== "ips" && (
                <div className="chips">{rules.countries.map((c) => <span className="chip" key={c}>{country(c)}</span>)}</div>
              )}
              {rules.mode !== "countries" && rules.networks.length > 0 && (
                <ul className="nets">
                  {rules.networks.map((n) => <li key={n.value}><code dir="ltr">{n.value}</code>{n.label && <span className="muted"> · <bdi>{n.label}</bdi></span>}</li>)}
                </ul>
              )}
            </dd>
            <dt>{t.scopeLabel}</dt>
            <dd><b>{t.scopes[rules.scope]}</b> · <span className="muted">{t.scopeText[rules.scope]}</span></dd>
            <dt>{t.exceptions}</dt>
            <dd>
              {[...rules.exceptions.presets.map((p) => t.presets[p]), ...rules.exceptions.paths].join(" · ") || "—"}
              {rules.exceptions.networks.length > 0 && (
                <ul className="nets">{rules.exceptions.networks.map((n) => <li key={n.value}><code dir="ltr">{n.value}</code>{n.label && <span className="muted"> · <bdi>{n.label}</bdi></span>}</li>)}</ul>
              )}
            </dd>
          </dl>
        )}
        <GeoNote state={data} />
        {rules.enabled && rules.mode !== "ips" && <Callout tone="info">{t.vpnNote}</Callout>}
        <p className="xsmall faint">{t.passNote}</p>
        {!data.can_change && <p className="xsmall faint">{t.noChange}</p>}
        {rules.changed_by && rules.changed_at && <p className="xsmall faint">{t.changed(rules.changed_by, ago(rules.changed_at))}</p>}
      </div>
      {editing && <AccessRulesDialog instance={instance} state={data} onClose={() => setEditing(false)} />}
    </section>
  );
}

function GeoNote({ state }: { state: AccessRulesState }) {
  const t = useT(words);
  const rules = state.rules;
  if (!rules || !state.geo) return null;
  const attribution = (
    <a href="https://db-ip.com" target="_blank" rel="noreferrer noopener" className="xsmall">{t.attribution}</a>
  );
  if (state.geo.ready)
    return <p className="xsmall faint row" style={{ gap: 6 }}><Globe2 size={13} /> {t.geoReady(state.geo.month ?? "")} {attribution}</p>;
  if (rules.enabled && rules.mode !== "ips")
    return <Callout tone="warning">{state.geo_missing === "closed" ? t.geoMissingClosed : t.geoMissingOpen} {attribution}</Callout>;
  return <p className="xsmall faint">{attribution}</p>;
}

// ---------------------------------------------------------------- changing the rules

function clean(rules: AccessRules): AccessRules {
  return {
    enabled: rules.enabled,
    mode: rules.mode,
    scope: rules.scope,
    countries: rules.countries,
    networks: rules.networks.filter((n) => n.value.trim()),
    exceptions: {
      presets: rules.exceptions.presets,
      paths: rules.exceptions.paths.map((p) => p.trim()).filter(Boolean),
      networks: rules.exceptions.networks.filter((n) => n.value.trim()),
    },
  };
}

function AccessRulesDialog({ instance, state, onClose }: { instance: Instance; state: AccessRulesState; onClose: () => void }) {
  const t = useT(words);
  const toast = useToast();
  const [step, setStep] = useState(0);
  const [rules, setRules] = useState<AccessRules>(() => clean(state.rules!));
  const [preview, setPreview] = useState<AccessPreview | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [typed, setTyped] = useState("");
  const channels = state.channels ?? [];
  const [method, setMethod] = useState<CodeMethod | "">(channels[0] ?? "");
  const [sentTo, setSentTo] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [shake, setShake] = useState(false);
  const path = `/instances/${instance.name}/access-rules`;
  const check = useCall((body: { rules: AccessRules; test_address?: string }) => api.post<AccessPreview>(`${path}/preview`, body));
  const send = useCall((m: CodeMethod) => api.post<{ method: string; sent_to?: string }>(`${path}/code`, { method: m }));
  const save = useCall(
    (body: Record<string, unknown>) => api.put<AccessRulesState>(path, body),
    [accessKey(instance.name)],
  );
  const toCheck = () =>
    check.mutate({ rules: clean(rules) }, {
      onSuccess: (found) => { setPreview(found); setProblem(null); setStep(1); },
      onError: (error) => setProblem(errorText(error)),
    });
  const sendCode = (m: CodeMethod) =>
    send.mutate(m, {
      onSuccess: (told) => setSentTo(told.sent_to ?? ""),
      onError: (error) => toast("error", t.codeNotSent, errorText(error)),
    });
  const apply = (value = code) =>
    save.mutate({ rules: clean(rules), confirm_name: typed, method, code: value }, {
      onSuccess: () => { toast("success", t.saved); onClose(); },
      onError: (error) => { setShake(true); setTimeout(() => setShake(false), 400); setCode(""); toast("error", t.notSaved, errorText(error)); },
    });
  const ready = typed === instance.name && !!method && code.length === 6;
  const footer =
    step === 0 ? (
      <><Button onClick={onClose}>{t.cancel}</Button><Button variant="primary" loading={check.isPending} onClick={toCheck}>{t.next}</Button></>
    ) : step === 1 ? (
      <><Button onClick={() => setStep(0)}>{t.back}</Button><Button variant="primary" onClick={() => setStep(2)}>{t.toConfirm}</Button></>
    ) : (
      <><Button onClick={() => setStep(1)}>{t.back}</Button><Button variant="primary" disabled={!ready} loading={save.isPending} onClick={() => apply()}>{t.apply}</Button></>
    );
  return (
    <Modal wide title={<span className="row" style={{ gap: 8 }}>{t.title} <Tag tone="accent">{t.beta}</Tag></span>} onClose={onClose} footer={footer}>
      <ol className="access-steps" aria-label={t.title}>
        {t.steps.map((label, i) => (
          <li key={label} data-state={i < step ? "done" : i === step ? "current" : "next"} aria-current={i === step ? "step" : undefined}>
            <span className="n">{i < step ? <Check size={12} /> : i + 1}</span>{label}
          </li>
        ))}
      </ol>
      {step === 0 && <RulesForm rules={rules} onChange={setRules} state={state} problem={problem} />}
      {step === 1 && preview && <PreviewView preview={preview} rules={rules} path={path} />}
      {step === 2 && (
        <div className="stack">
          <Callout tone="warning">{t.confirmText}</Callout>
          <Field label={t.typeName(instance.name)}>
            <Input value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" spellCheck={false} dir="ltr" />
          </Field>
          {channels.length === 0 ? (
            <Callout tone="danger">{t.noChannel}</Callout>
          ) : (
            <>
              <Field label={t.method}>
                <Segmented value={method} onChange={(m) => { setMethod(m); setCode(""); setSentTo(null); }}
                  options={channels.map((c) => ({ value: c, label: t.methods[c] }))} />
              </Field>
              {method === "email" && (
                <div className="row-wrap" style={{ gap: 8 }}>
                  <Button size="sm" loading={send.isPending} onClick={() => sendCode("email")}>{sentTo === null ? t.sendCode : t.resend}</Button>
                  {sentTo && <span className="small muted">{t.sentTo(sentTo)}</span>}
                </div>
              )}
              {method === "app" && <p className="small muted">{t.appCode}</p>}
              {(method === "app" || sentTo !== null) && (
                <OtpInput value={code} onChange={setCode} shake={shake} label={t.code}
                  onComplete={(value) => typed === instance.name && apply(value)} />
              )}
            </>
          )}
        </div>
      )}
    </Modal>
  );
}

function RulesForm({ rules, onChange, state, problem }: { rules: AccessRules; onChange: (rules: AccessRules) => void; state: AccessRulesState; problem: string | null }) {
  const t = useT(words);
  const set = (values: Partial<AccessRules>) => onChange({ ...rules, ...values });
  const setExceptions = (values: Partial<AccessRules["exceptions"]>) => set({ exceptions: { ...rules.exceptions, ...values } });
  return (
    <div className="stack access-form">
      {problem && <Callout tone="danger">{problem}</Callout>}
      <Switch checked={rules.enabled} onChange={(enabled) => set({ enabled })} label={rules.enabled ? t.on : t.off} />
      <fieldset disabled={!rules.enabled} className="stack">
        <Field label={t.modeLabel} hint={t.modeText[rules.mode]}>
          <Segmented<AccessMode> value={rules.mode} onChange={(mode) => set({ mode })}
            options={(["countries", "ips", "either"] as AccessMode[]).map((m) => ({ value: m, label: t.modes[m] }))} />
        </Field>
        <Field label={t.scopeLabel} hint={t.scopeText[rules.scope]}>
          <Segmented<AccessScope> value={rules.scope} onChange={(scope) => set({ scope })}
            options={(["back_office", "site"] as AccessScope[]).map((s) => ({ value: s, label: t.scopes[s] }))} />
        </Field>
        {rules.mode !== "ips" && (
          <CountryPicker value={rules.countries} onChange={(countries) => set({ countries })} />
        )}
        {rules.mode !== "countries" && (
          <NetworkList title={t.addresses} hint={t.addressesText} value={rules.networks} onChange={(networks) => set({ networks })} />
        )}
        <div className="stack-sm">
          <div className="name">{t.exceptions}</div>
          <p className="xsmall muted">{t.exceptionsText}</p>
          <ul className="presets">
            {PRESETS.map((preset) => (
              <li key={preset}>
                <Switch
                  checked={rules.exceptions.presets.includes(preset)}
                  onChange={(on) => setExceptions({ presets: on ? [...rules.exceptions.presets, preset] : rules.exceptions.presets.filter((p) => p !== preset) })}
                  label={<span>{t.presets[preset]} <code className="faint" dir="ltr">{(state.presets?.[preset] ?? []).join(" ")}</code></span>}
                />
              </li>
            ))}
          </ul>
          <Field label={t.customPaths} hint={t.customPathsHint}>
            <Textarea rows={2} dir="ltr" spellCheck={false} value={rules.exceptions.paths.join("\n")}
              onChange={(e) => setExceptions({ paths: e.target.value.split("\n") })} />
          </Field>
          <NetworkList title={t.exceptionAddresses} value={rules.exceptions.networks} onChange={(networks) => setExceptions({ networks })} />
        </div>
      </fieldset>
    </div>
  );
}

/** Countries: the chosen ones as chips with their flags, and a searchable list to add or remove one (by name in the
 *  language shown, in English, or by code). */
function CountryPicker({ value, onChange }: { value: string[]; onChange: (value: string[]) => void }) {
  const t = useT(words);
  const locale = useLocale();
  const [query, setQuery] = useState("");
  const all = useMemo(
    () => COUNTRIES.map((code) => ({ code, name: countryName(code, locale), english: countryName(code, "en") }))
      .sort((a, b) => a.name.localeCompare(b.name, locale)),
    [locale],
  );
  const needle = query.trim().toLowerCase();
  const shown = needle
    ? all.filter((c) => c.code.toLowerCase() === needle || c.name.toLowerCase().includes(needle) || c.english.toLowerCase().includes(needle))
    : all;
  const toggle = (code: string) => onChange(value.includes(code) ? value.filter((c) => c !== code) : [...value, code].sort());
  return (
    <div className="stack-sm country-picker">
      <div className="row"><span className="name">{t.countries}</span><span className="spacer" /><span className="xsmall faint">{t.selected(value.length)}</span></div>
      {value.length > 0 && (
        <div className="chips">
          {value.map((code) => (
            <button type="button" className="chip removable" key={code} onClick={() => toggle(code)} aria-label={t.removeCountry(countryName(code, locale))}>
              {flag(code)} {countryName(code, locale)} <X size={12} />
            </button>
          ))}
        </div>
      )}
      <div className="search-field">
        <Search size={14} />
        <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder={t.searchCountries} aria-label={t.searchCountries} />
      </div>
      <ul className="country-list" role="listbox" aria-multiselectable="true" aria-label={t.countries}>
        {shown.map((c) => {
          const chosen = value.includes(c.code);
          return (
            <li key={c.code} role="option" aria-selected={chosen} tabIndex={0} data-chosen={chosen || undefined}
              onClick={() => toggle(c.code)}
              onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(c.code); } }}>
              <span className="flag" aria-hidden="true">{flag(c.code)}</span>
              <span className="truncate">{c.name}</span>
              <span className="spacer" />
              <code className="faint">{c.code}</code>
              <span className="tick">{chosen && <Check size={14} />}</span>
            </li>
          );
        })}
        {!shown.length && <li className="faint small empty">{t.noCountry}</li>}
      </ul>
    </div>
  );
}

function NetworkList({ title, hint, value, onChange }: { title: string; hint?: string; value: Network[]; onChange: (value: Network[]) => void }) {
  const t = useT(words);
  const change = (i: number, values: Partial<Network>) => onChange(value.map((n, j) => (j === i ? { ...n, ...values } : n)));
  return (
    <div className="stack-sm">
      <div className="name">{title}</div>
      {hint && <p className="xsmall muted">{hint}</p>}
      {value.map((n, i) => {
        const bad = !!n.value.trim() && !looksLikeNetwork(n.value);
        return (
          <div className="network-row" key={i}>
            <Field error={bad ? t.notAddress : undefined}>
              <Input value={n.value} dir="ltr" spellCheck={false} placeholder="203.0.113.0/24" aria-label={t.address}
                aria-invalid={bad || undefined} onChange={(e) => change(i, { value: e.target.value })} />
            </Field>
            <Input value={n.label} placeholder={t.labelHint} aria-label={t.label} maxLength={60} onChange={(e) => change(i, { label: e.target.value })} />
            <Button variant="ghost" size="sm" icon={<Trash2 />} aria-label={t.removeAddress} onClick={() => onChange(value.filter((_, j) => j !== i))} />
          </div>
        );
      })}
      <div><Button size="sm" variant="ghost" icon={<Plus />} onClick={() => onChange([...value, { value: "", label: "" }])}>{t.addAddress}</Button></div>
    </div>
  );
}

function PreviewView({ preview, rules, path }: { preview: AccessPreview; rules: AccessRules; path: string }) {
  const t = useT(words);
  const locale = useLocale();
  const [test, setTest] = useState("");
  const [tested, setTested] = useState<AccessPreview["test"]>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const run = useCall((body: { rules: AccessRules; test_address: string }) => api.post<AccessPreview>(`${path}/preview`, body));
  const where = (country: string | null) => (country ? `${flag(country)} ${countryName(country, locale)}` : t.noCountryKnown);
  const you = preview.you;
  return (
    <div className="stack">
      {you && (
        <Callout tone={you.allowed ? "success" : "warning"}>
          <b>{t.youTitle}.</b> {(you.allowed ? t.youIn : t.youOut)(you.address, where(you.country))}
        </Callout>
      )}
      {preview.warnings.filter((w) => w !== "you_are_outside").map((w) => (
        <Callout key={w} tone={w === "vpn" ? "info" : "warning"}>{t.warnings[w]}</Callout>
      ))}
      <div className="stack-sm">
        <div className="name">{t.testTitle}</div>
        <form className="row" style={{ gap: 8 }} onSubmit={(e) => {
          e.preventDefault();
          run.mutate({ rules: clean(rules), test_address: test.trim() }, {
            onSuccess: (found) => { setTested(found.test); setProblem(null); },
            onError: (error) => { setTested(null); setProblem(errorText(error)); },
          });
        }}>
          <Input value={test} onChange={(e) => setTest(e.target.value)} placeholder="198.51.100.23" aria-label={t.testHint} dir="ltr" />
          <Button type="submit" loading={run.isPending} disabled={!test.trim()}>{t.test}</Button>
        </form>
        {problem && <p className="small negative">{problem}</p>}
        {tested && (
          <p className={`small ${tested.allowed ? "positive" : "negative"}`} aria-live="polite">
            {(tested.allowed ? t.testIn : t.testOut)(tested.address, where(tested.country))}
          </p>
        )}
      </div>
      <div className="stack-sm">
        <div className="name">{t.samplesTitle}</div>
        <ul className="samples">
          {preview.samples.map((s) => (
            <li key={s.path}>
              <code dir="ltr">{s.path}</code>
              <span className="spacer" />
              <Tag tone={s.open_to_everyone ? "positive" : "negative"}>{s.open_to_everyone ? t.open : t.closed}</Tag>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
