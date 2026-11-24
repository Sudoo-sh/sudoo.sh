// Recovery codes for the authenticator app: shown once when they are made (copy, download, print), how many are
// left, and new ones (the password confirms; the old ones stop working).

import { useQueryClient } from "@tanstack/react-query";
import { Check, Copy, Download, LifeBuoy, RefreshCw } from "lucide-react";
import { useState } from "react";
import { api, errorText } from "@/api/client";
import { keys } from "@/api/queries";
import type { NewRecoveryCodes, RecoveryCodes } from "@/api/types";
import { useT } from "@/i18n";
import account from "@/i18n/account";
import common from "@/i18n/common";
import { ago } from "@/lib/format";
import { Button, Callout, Field } from "./app/basics";
import { Input } from "@/components/ui/input";
import { Modal, useToast } from "./app/overlay";

/** The codes, once: copy them all, or save them as a text file, before closing. */
export function RecoveryCodesSheet({ codes, onClose }: { codes: string[]; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  const [saved, setSaved] = useState(false);
  const t = useT(account).recovery;
  const text = `${t.fileText(location.host)}\n\n${codes.join("\n")}\n`;
  const copy = async () => {
    await navigator.clipboard.writeText(text);
    setCopied(true);
    setSaved(true);
    setTimeout(() => setCopied(false), 1400);
  };
  const download = () => {
    const link = document.createElement("a");
    link.href = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
    link.download = `sudoo-recovery-codes-${location.hostname}.txt`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    setSaved(true);
  };
  return (
    <Modal
      title={t.sheetTitle}
      description={t.sheetText}
      onClose={onClose}
      footer={
        <Button variant="primary" onClick={onClose}>
          {saved ? t.done : t.saved}
        </Button>
      }
    >
      <ol className="recovery-codes" aria-label={t.list} dir="ltr">
        {codes.map((code) => (
          <li key={code} className="mono tabular">
            {code}
          </li>
        ))}
      </ol>
      <div className="row" style={{ marginTop: 12 }}>
        <Button size="sm" icon={copied ? <Check /> : <Copy />} onClick={copy}>
          {copied ? t.copied : t.copyAll}
        </Button>
        <Button size="sm" icon={<Download />} onClick={download}>
          {t.download}
        </Button>
      </div>
      <p className="small muted" style={{ marginTop: 12 }}>
        {t.keep}
      </p>
    </Modal>
  );
}

/** How many codes are left, and a button for new ones (the password confirms). */
export function RecoveryCodesRow({
  state,
  needed,
  onChange,
}: {
  state: RecoveryCodes | null;
  needed: boolean;
  onChange: () => void;
}) {
  const toast = useToast();
  const client = useQueryClient();
  const [asking, setAsking] = useState(false);
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [codes, setCodes] = useState<string[] | null>(null);
  const t = useT(account).recovery;
  const c = useT(common).common;
  const make = async () => {
    setBusy(true);
    try {
      const made = await api.post<NewRecoveryCodes>("/account/recovery-codes", { password });
      setAsking(false);
      setPassword("");
      setCodes(made.codes);
      onChange();
      client.invalidateQueries({ queryKey: keys.session }); // the banner asking for codes goes away
    } catch (error) {
      toast("error", t.noNew, errorText(error));
    } finally {
      setBusy(false);
    }
  };
  const low = state && state.left <= 3;
  return (
    <>
      {needed ? (
        <Callout tone="warning">
          <div className="row-wrap">
            <span className="spacer">
              {state ? t.usedAll : t.noneYet}
              {t.lockedOut}
            </span>
            <Button size="sm" variant="primary" onClick={() => setAsking(true)}>
              {t.make}
            </Button>
          </div>
        </Callout>
      ) : (
        state && (
          <div className="choice" style={{ cursor: "default" }}>
            <LifeBuoy />
            <span style={{ flex: 1 }}>
              <span className="t row">{t.title}</span>
              <span className="d">
                <span className={`tabular ${low ? "caution-text" : ""}`}>{t.left(state.left, state.total)}</span>
                {state.created_at && t.made(ago(state.created_at))}
                {t.each}
              </span>
            </span>
            <Button size="sm" variant="ghost" icon={<RefreshCw />} onClick={() => setAsking(true)}>
              {t.newCodes}
            </Button>
          </div>
        )
      )}
      {asking && (
        <Modal
          title={state ? t.newTitle : t.make}
          description={state ? t.newText : t.typePassword}
          onClose={() => {
            setAsking(false);
            setPassword("");
          }}
          footer={
            <>
              <Button
                onClick={() => {
                  setAsking(false);
                  setPassword("");
                }}
              >
                {c.cancel}
              </Button>
              <Button variant="primary" loading={busy} disabled={!password} onClick={make}>
                {t.makeCodes}
              </Button>
            </>
          }
        >
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (password) make();
            }}
          >
            <Field label={t.password}>
              <Input
                type="password"
                autoComplete="current-password"
                autoFocus
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </Field>
          </form>
        </Modal>
      )}
      {codes && <RecoveryCodesSheet codes={codes} onClose={() => setCodes(null)} />}
    </>
  );
}
