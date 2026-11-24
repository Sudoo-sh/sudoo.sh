// The connection to the Sudoo.sh marketplace: shown on Settings (Marketplace) and inside the Plugins page.

import { useQueryClient } from "@tanstack/react-query";
import { BadgeCheck, Link2, LogOut, RefreshCcw } from "lucide-react";
import { useState } from "react";
import { api, errorText } from "@/api/client";
import { keys, useMarketplace, usePlugins } from "@/api/queries";
import type { MarketplaceStatus, Publisher } from "@/api/types";
import { useT, words } from "@/i18n";
import common from "@/i18n/common";
import plugins from "@/i18n/plugins";
import { ago, dateTime } from "@/lib/format";
import { useCan } from "./Shell";
import { Mark } from "./app/Brand";
import { Button, Callout, Field, Skeleton, Tag } from "./app/basics";
import { Input } from "@/components/ui/input";
import { Modal, useToast } from "./app/overlay";

/** Official (Sudoo.sh's own plugins) or Verified (Sudoo.sh checked who the publisher is). */
export function PublisherBadge({ publisher }: { publisher: Publisher | null }) {
  const t = useT(plugins).marketplace;
  if (!publisher) return <span className="faint">{t.unknownPublisher}</span>;
  return (
    <span className="publisher">
      <bdi className="truncate">{publisher.name}</bdi>
      {publisher.official ? (
        <Tag tone="accent" icon={<Mark size={12} />}>
          {t.official}
        </Tag>
      ) : publisher.verified ? (
        <span className="verified" title={t.verifiedTitle} aria-label={t.verified}>
          <BadgeCheck />
        </span>
      ) : null}
    </span>
  );
}

/** Why the marketplace cannot be used right now, in the person's words; null when it can. */
export function marketplaceProblem(
  status: MarketplaceStatus | undefined,
): { tone: "warning" | "danger"; text: string } | null {
  if (!status) return null;
  const t = words(plugins).marketplace;
  if (!status.enabled) return { tone: "warning", text: t.off };
  if (status.hub_status === "integrity_violation") return { tone: "danger", text: t.integrity };
  if (status.hub_status === "installation_suspended") return { tone: "danger", text: t.suspended };
  if (status.error_code === "unverified") return { tone: "danger", text: t.unverified };
  if (status.flags?.includes("fingerprint_mismatch")) return { tone: "danger", text: t.flagged };
  if (status.reachable === false) return { tone: "warning", text: t.unreachable(!!status.lease_valid) };
  return null;
}

export function LinkForm({ onLinked }: { onLinked?: () => void }) {
  const client = useQueryClient();
  const toast = useToast();
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const t = useT(plugins).marketplace;
  const link = async () => {
    setBusy(true);
    try {
      const linked = await api.post<{ account_name: string }>("/marketplace/link", { code: code.trim() });
      toast("success", t.linked, t.linkedText(linked.account_name));
      setCode("");
      await client.invalidateQueries({ queryKey: keys.marketplace });
      onLinked?.();
    } catch (error) {
      toast("error", t.notLinked, errorText(error));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Field label={t.linkCode} hint={t.linkHint}>
      <div className="row">
        <Input
          className="mono"
          dir="ltr"
          autoComplete="one-time-code"
          spellCheck={false}
          placeholder="ABCD-1234"
          maxLength={20}
          value={code}
          onChange={(e) => setCode(e.target.value.toUpperCase())}
          onKeyDown={(e) => e.key === "Enter" && code.trim().length >= 8 && link()}
        />
        <Button variant="primary" icon={<Link2 />} loading={busy} disabled={code.trim().length < 8} onClick={link}>
          {t.link}
        </Button>
      </div>
    </Field>
  );
}

/** Settings, Marketplace: the connection, the lease, and linking to an account. */
export function MarketplaceSettings() {
  const { data: status, isPending } = useMarketplace();
  const can = useCan();
  const client = useQueryClient();
  const toast = useToast();
  const [sending, setSending] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);
  const { data: installed } = usePlugins();
  const t = useT(plugins).marketplace;
  const c = useT(common).common;
  if (isPending)
    return (
      <div className="card-body">
        <Skeleton height={120} />
      </div>
    );
  if (!status) return null;
  const problem = marketplaceProblem(status);
  const heartbeat = async () => {
    setSending(true);
    try {
      await api.post("/marketplace/heartbeat");
      toast("success", t.answered);
    } catch (error) {
      toast("error", t.noAnswer, errorText(error));
    } finally {
      await client.invalidateQueries({ queryKey: keys.marketplace });
      setSending(false);
    }
  };
  const paid = (installed ?? []).filter((p) => p.tier === "paid");
  const logOut = async () => {
    setLoggingOut(true);
    try {
      await api.post("/marketplace/unlink", { confirm: true });
      toast("success", t.loggedOut, paid.length ? t.paidRemoving : undefined);
      setLeaving(false);
    } catch (error) {
      toast("error", t.stillLoggedIn, errorText(error));
    } finally {
      await client.invalidateQueries({ queryKey: keys.marketplace });
      await client.invalidateQueries({ queryKey: keys.plugins });
      setLoggingOut(false);
    }
  };
  return (
    <div className="card-body stack">
      {problem && <Callout tone={problem.tone}>{problem.text}</Callout>}
      {status.key_revoked_at && !status.linked?.account_name && <Callout tone="warning">{t.keyRevoked}</Callout>}
      {status.removing_paid_plugins && <Callout tone="info">{t.removingPaid}</Callout>}
      <dl className="dl">
        <dt>{t.hub}</dt>
        <dd>
          <span className="mono small">{status.hub_url}</span>
        </dd>
        <dt>{t.installation}</dt>
        <dd>
          <span className="mono small">{status.installation_id ?? "—"}</span>
        </dd>
        <dt>{t.lastHeartbeat}</dt>
        <dd>
          {status.last_heartbeat_at
            ? `${ago(status.last_heartbeat_at)} · ${dateTime(status.last_heartbeat_at)}`
            : t.noneYet}
        </dd>
        <dt>{t.lease}</dt>
        <dd>
          {status.lease_valid ? (
            <>
              {t.validUntil(dateTime(status.lease_expires_at))}
              {status.lease_ending_soon && (
                <>
                  {" "}
                  <Tag tone="caution">{t.endsSoon}</Tag>
                </>
              )}
            </>
          ) : (
            <span className="muted">{t.noLease}</span>
          )}
        </dd>
        <dt>{t.account}</dt>
        <dd>
          {status.linked?.account_name ? (
            <>
              {t.linkedTo(
                <b key="a">
                  <bdi>{status.linked.account_name}</bdi>
                </b>,
              )}
            </>
          ) : (
            <span className="muted">{t.notLinkedText}</span>
          )}
        </dd>
      </dl>
      {can("settings.manage") && status.enabled && (
        <>
          {!status.linked?.account_name && <LinkForm />}
          <div className="row">
            <Button size="sm" icon={<RefreshCcw />} loading={sending} onClick={heartbeat}>
              {t.heartbeat}
            </Button>
            {status.linked?.account_name && (
              <Button size="sm" variant="ghost" icon={<LogOut />} onClick={() => setLeaving(true)}>
                {t.logOut}
              </Button>
            )}
          </div>
        </>
      )}
      {leaving && (
        <Modal
          title={t.logOutTitle}
          description={t.logOutText(status.linked?.account_name ?? null)}
          onClose={() => setLeaving(false)}
          footer={
            <>
              <Button onClick={() => setLeaving(false)}>{c.cancel}</Button>
              <Button variant="danger" icon={<LogOut />} loading={loggingOut} onClick={logOut}>
                {t.logOutRemove}
              </Button>
            </>
          }
        >
          <div className="stack-sm">
            <p>
              <b>{t.paidRemoved}</b>
              {t.paidRemovedText}
            </p>
            {paid.length > 0 ? (
              <div className="row-wrap">
                {paid.map((p) => (
                  <Tag key={p.id}>{p.name}</Tag>
                ))}
              </div>
            ) : (
              <p className="small muted">{t.noPaid}</p>
            )}
            <p className="small muted">{t.keepRunning}</p>
          </div>
        </Modal>
      )}
    </div>
  );
}
