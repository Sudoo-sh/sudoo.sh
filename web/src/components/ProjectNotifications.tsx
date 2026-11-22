// A project's Notifications tab: its channels (Slack, Discord, Telegram, Microsoft Teams, a signed webhook, e-mail),
// the events each one hears about, how its last delivery went, Send test, and adding, changing and removing them.
// Saved addresses and tokens never come back from the server: only their last 4 characters. A webhook's signing secret
// is shown once, in the dialog that follows its creation.

import { useQueryClient } from "@tanstack/react-query";
import { Bell, Hash, Mail, MessagesSquare, Pencil, Plus, Send, Trash2, Users, Webhook } from "lucide-react";
import { useState, type ReactNode } from "react";
import { api, errorText } from "@/api/client";
import { useCall } from "@/api/queries";
import { statusKeys, useNotifications, type Channel, type ChannelType, type Delivery, type NotificationEvent } from "@/api/status";
import type { ProjectDetail } from "@/api/types";
import { useT } from "@/i18n";
import words from "@/i18n/status";
import { ago, fullDateTime } from "@/lib/format";
import { Button, Callout, CopyField, Empty, Field, Skeleton, Switch, Tag } from "./app/basics";
import { Modal, useToast } from "./app/overlay";
import { SelectBox } from "./app/select";
import { Checkbox } from "./ui/checkbox";
import { Input } from "./ui/input";
import { Textarea } from "./ui/textarea";

const TYPE_ICON: Record<ChannelType, ReactNode> = {
  slack: <Hash />,
  discord: <MessagesSquare />,
  teams: <Users />,
  telegram: <Send />,
  webhook: <Webhook />,
  email: <Mail />,
};

const EVENT_GROUPS: { id: "builds" | "stages" | "production" | "data"; events: NotificationEvent[] }[] = [
  { id: "builds", events: ["build_failed", "build_warning", "build_recovered"] },
  { id: "stages", events: ["stage_changed", "promoted"] },
  { id: "production", events: ["production_down", "production_up"] },
  { id: "data", events: ["backup_failed", "restore_done", "staging_refreshed", "storage_over"] },
];

export function ProjectNotifications({ project }: { project: ProjectDetail }) {
  const { data, isPending, error } = useNotifications(project.id);
  const t = useT(words).notifications;
  const [editing, setEditing] = useState<Channel | "new" | null>(null);
  const [secret, setSecret] = useState<{ name: string; value: string } | null>(null);
  if (isPending)
    return (
      <div className="stack tab-panel" aria-busy="true" aria-label={t.loading}>
        <Skeleton height={64} />
        <Skeleton height={140} />
      </div>
    );
  if (!data)
    return (
      <div className="stack tab-panel">
        <Callout tone="danger">{error ? errorText(error) : t.notRead}</Callout>
      </div>
    );
  const full = data.channels.length >= data.limit;
  return (
    <div className="stack tab-panel project-notifications">
      <div className="row-wrap" style={{ justifyContent: "space-between", alignItems: "flex-start", gap: 12 }}>
        <p className="small muted" style={{ maxWidth: "72ch", margin: 0 }}>{t.text}</p>
        <Button variant="primary" icon={<Plus />} disabled={full} onClick={() => setEditing("new")} title={full ? t.limit(data.limit) : undefined}>
          {t.add}
        </Button>
      </div>
      {!data.enabled && <Callout tone="warning">{t.off}</Callout>}
      {!data.channels.length ? (
        <div className="card">
          <Empty icon={<Bell />} title={t.none} action={<Button icon={<Plus />} onClick={() => setEditing("new")}>{t.add}</Button>}>
            {t.noneText}
          </Empty>
        </div>
      ) : (
        <div className="channel-list">
          {data.channels.map((channel) => (
            <ChannelCard key={channel.id} project={project} channel={channel} onEdit={() => setEditing(channel)} />
          ))}
        </div>
      )}
      {editing && (
        <ChannelDialog
          project={project}
          channel={editing === "new" ? null : editing}
          defaults={data.default_events}
          onClose={() => setEditing(null)}
          onSigningSecret={(name, value) => setSecret({ name, value })}
        />
      )}
      {secret && (
        <Modal title={t.signingSecret} description={secret.name} onClose={() => setSecret(null)} footer={<Button variant="primary" onClick={() => setSecret(null)}>{t.done}</Button>}>
          <div className="stack">
            <Callout tone="warning">{t.signingSecretOnce}</Callout>
            <CopyField value={secret.value} block />
          </div>
        </Modal>
      )}
    </div>
  );
}

function LastDelivery({ last }: { last: Delivery | null }) {
  const t = useT(words).notifications;
  if (!last) return <span className="xsmall muted">{t.never}</span>;
  return (
    <span className="xsmall delivery" data-ok={last.ok ? "true" : "false"} title={fullDateTime(last.at)}>
      <span className="dot" aria-hidden="true" />
      {last.ok ? t.delivered(ago(last.at), last.result ?? "") : t.failed(ago(last.at), last.result ?? "")}
      {last.test && <Tag>{t.testMark}</Tag>}
      {!last.ok && last.retry_at ? <span className="muted"> · {t.retrying(ago(last.retry_at))}</span> : null}
    </span>
  );
}

function ChannelCard({ project, channel, onEdit }: { project: ProjectDetail; channel: Channel; onEdit: () => void }) {
  const t = useT(words).notifications;
  const toast = useToast();
  const client = useQueryClient();
  const [removing, setRemoving] = useState(false);
  const base = `/projects/${project.id}/notifications/${channel.id}`;
  const refresh = [statusKeys.notifications(project.id)];
  const toggle = useCall((enabled: boolean) => api.patch<Channel>(base, { enabled }), refresh);
  const test = useCall(() => api.post<{ channel: Channel; delivery: Delivery }>(`${base}/test`), refresh);
  const remove = useCall(() => api.del(base), refresh);
  const hints = Object.values(channel.secrets).filter((hint): hint is string => !!hint);
  return (
    <section className="card channel-card" data-enabled={channel.enabled} aria-label={channel.name}>
      <div className="card-head">
        <span className="channel-icon" data-type={channel.type} aria-hidden="true">{TYPE_ICON[channel.type]}</span>
        <div className="spacer" style={{ minWidth: 0 }}>
          <h3 className="truncate">{channel.name}</h3>
          <div className="sub row-wrap" style={{ gap: 6 }}>
            <span>{t.types[channel.type]}</span>
            {channel.type === "telegram" && channel.config.chat_id && <bdi dir="ltr" className="mono">{channel.config.chat_id}</bdi>}
            {channel.type === "email" && channel.config.addresses && <bdi dir="ltr" className="mono truncate">{channel.config.addresses.join(", ")}</bdi>}
            {hints.map((hint) => <bdi key={hint} dir="ltr" className="mono">{hint}</bdi>)}
          </div>
        </div>
        <Switch
          checked={channel.enabled}
          disabled={toggle.isPending}
          label={<span className="sr-only">{channel.enabled ? t.enabled : t.paused}</span>}
          onChange={(enabled) =>
            toggle.mutate(enabled, { onSuccess: () => toast("success", t.changed), onError: (error) => toast("error", t.notSaved, errorText(error)) })
          }
        />
      </div>
      <div className="card-body stack">
        <ul className="event-tags" aria-label={t.whichEvents}>
          {channel.events.map((event) => (
            <li key={event}><Tag>{t.events[event]}</Tag></li>
          ))}
        </ul>
        <div className="row-wrap" style={{ justifyContent: "space-between", gap: 8 }}>
          <LastDelivery last={channel.last} />
          <div className="row" style={{ gap: 6 }}>
            <Button
              size="sm"
              icon={<Send />}
              loading={test.isPending}
              onClick={() =>
                test.mutate(undefined, {
                  onSuccess: (answer) => {
                    client.invalidateQueries({ queryKey: statusKeys.notifications(project.id) });
                    if (answer.delivery.ok) toast("success", t.testSent, answer.delivery.result);
                    else toast("error", t.testFailed, answer.delivery.result);
                  },
                  onError: (error) => toast("error", t.testFailed, errorText(error)),
                })
              }
            >
              {t.test}
            </Button>
            <Button size="sm" variant="ghost" icon={<Pencil />} onClick={onEdit} aria-label={t.edit} title={t.edit} />
            <Button size="sm" variant="ghost" icon={<Trash2 />} onClick={() => setRemoving(true)} aria-label={t.remove} title={t.remove} />
          </div>
        </div>
      </div>
      {removing && (
        <Modal
          title={t.removeTitle(channel.name)}
          description={t.removeText}
          onClose={() => setRemoving(false)}
          footer={
            <>
              <Button onClick={() => setRemoving(false)}>{t.cancel}</Button>
              <Button
                variant="danger"
                loading={remove.isPending}
                onClick={() =>
                  remove.mutate(undefined, {
                    onSuccess: () => {
                      setRemoving(false);
                      toast("success", t.removed);
                    },
                    onError: (error) => toast("error", t.notSaved, errorText(error)),
                  })
                }
              >
                {t.remove}
              </Button>
            </>
          }
        />
      )}
    </section>
  );
}

function ChannelDialog({
  project,
  channel,
  defaults,
  onClose,
  onSigningSecret,
}: {
  project: ProjectDetail;
  channel: Channel | null;
  defaults: NotificationEvent[];
  onClose: () => void;
  onSigningSecret: (name: string, value: string) => void;
}) {
  const t = useT(words).notifications;
  const toast = useToast();
  const [type, setType] = useState<ChannelType>(channel?.type ?? "slack");
  const [name, setName] = useState(channel?.name ?? "");
  const [events, setEvents] = useState<NotificationEvent[]>(channel?.events ?? defaults);
  const [url, setUrl] = useState("");
  const [botToken, setBotToken] = useState("");
  const [chatId, setChatId] = useState(channel?.config.chat_id ?? "");
  const [addresses, setAddresses] = useState((channel?.config.addresses ?? []).join(", "));
  const [problem, setProblem] = useState<string | null>(null);
  const refresh = [statusKeys.notifications(project.id)];
  const save = useCall(
    (body: Record<string, unknown>) =>
      channel ? api.patch<Channel>(`/projects/${project.id}/notifications/${channel.id}`, body) : api.post<Channel>(`/projects/${project.id}/notifications`, body),
    refresh,
  );
  const usesUrl = ["slack", "discord", "teams", "webhook"].includes(type);
  const submit = () => {
    setProblem(null);
    const secrets: Record<string, string> = {};
    if (usesUrl && url.trim()) secrets.url = url.trim();
    if (type === "telegram" && botToken.trim()) secrets.bot_token = botToken.trim();
    const config: Record<string, unknown> = {};
    if (type === "telegram") config.chat_id = chatId.trim();
    if (type === "email") config.addresses = addresses.split(",").map((a) => a.trim()).filter(Boolean);
    const body: Record<string, unknown> = { name: name.trim(), events, config, ...(Object.keys(secrets).length ? { secrets } : {}) };
    if (!channel) body.type = type;
    save.mutate(body, {
      onSuccess: (saved) => {
        toast("success", channel ? t.changed : t.added);
        onClose();
        if (saved.signing_secret) onSigningSecret(saved.name, saved.signing_secret);
      },
      onError: (error) => setProblem(errorText(error)),
    });
  };
  const toggleEvent = (event: NotificationEvent, on: boolean) =>
    setEvents((current) => (on ? [...current, event] : current.filter((e) => e !== event)));
  return (
    <Modal
      title={channel ? channel.name : t.add}
      onClose={onClose}
      wide
      footer={
        <>
          <Button onClick={onClose}>{t.cancel}</Button>
          <Button variant="primary" loading={save.isPending} onClick={submit}>{t.save}</Button>
        </>
      }
    >
      <form className="stack" onSubmit={(e) => { e.preventDefault(); submit(); }}>
        {!channel && (
          <Field label={t.types[type]} hint={t.typeHint[type]} htmlFor="channel-type">
            <SelectBox id="channel-type" value={type} onValueChange={(value) => setType(value as ChannelType)} aria-label={t.add}>
              {(Object.keys(t.types) as ChannelType[]).map((kind) => (
                <option key={kind} value={kind}>{t.types[kind]}</option>
              ))}
            </SelectBox>
          </Field>
        )}
        <Field label={t.name} htmlFor="channel-name">
          <Input id="channel-name" value={name} maxLength={60} placeholder={t.namePlaceholder} onChange={(e) => setName(e.target.value)} />
        </Field>
        {usesUrl && (
          <Field label={t.url} hint={channel?.secrets.url ? t.urlKeep : t.typeHint[type]} htmlFor="channel-url">
            <Input id="channel-url" dir="ltr" type="url" autoComplete="off" spellCheck={false} value={url}
              placeholder={channel?.secrets.url ? t.secretSaved(channel.secrets.url) : "https://"} onChange={(e) => setUrl(e.target.value)} />
          </Field>
        )}
        {type === "telegram" && (
          <>
            <Field label={t.botToken} hint={channel?.secrets.bot_token ? t.botTokenKeep : t.typeHint.telegram} htmlFor="channel-token">
              <Input id="channel-token" dir="ltr" autoComplete="off" spellCheck={false} value={botToken}
                placeholder={channel?.secrets.bot_token ? t.secretSaved(channel.secrets.bot_token) : "123456789:AA…"} onChange={(e) => setBotToken(e.target.value)} />
            </Field>
            <Field label={t.chatId} hint={t.chatIdHint} htmlFor="channel-chat">
              <Input id="channel-chat" dir="ltr" value={chatId} onChange={(e) => setChatId(e.target.value)} />
            </Field>
          </>
        )}
        {type === "email" && (
          <Field label={t.addresses} hint={t.addressesHint} htmlFor="channel-addresses">
            <Textarea id="channel-addresses" dir="ltr" rows={2} value={addresses} onChange={(e) => setAddresses(e.target.value)} />
          </Field>
        )}
        <fieldset className="event-groups">
          <legend className="label">{t.whichEvents}</legend>
          {EVENT_GROUPS.map((group) => (
            <div key={group.id} className="event-group">
              <div className="xsmall muted">{t.groups[group.id]}</div>
              {group.events.map((event) => (
                <label key={event} className="check-row">
                  <Checkbox checked={events.includes(event)} onCheckedChange={(value) => toggleEvent(event, value === true)} />
                  <span>{t.events[event]}</span>
                </label>
              ))}
            </div>
          ))}
        </fieldset>
        {problem && <Callout tone="danger">{problem}</Callout>}
      </form>
    </Modal>
  );
}
