// Recovery codes for the authenticator app: shown once when they are made (copy, download, print), how many are
// left, and new ones (the password confirms; the old ones stop working).

import { useQueryClient } from "@tanstack/react-query";
import { Check, Copy, Download, LifeBuoy, RefreshCw } from "lucide-react";
import { useState } from "react";
import { api, errorText } from "@/api/client";
import { keys } from "@/api/queries";
import type { NewRecoveryCodes, RecoveryCodes } from "@/api/types";
import { ago } from "@/lib/format";
import { Button, Callout, Field } from "./ui/basics";
import { Modal, useToast } from "./ui/overlay";

/** The codes, once: copy them all, or save them as a text file, before closing. */
export function RecoveryCodesSheet({ codes, onClose }: { codes: string[]; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  const [saved, setSaved] = useState(false);
  const text = `Sudoo.sh recovery codes (${location.host})\nEach code works once, in place of the authenticator app's code.\n\n${codes.join("\n")}\n`;
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
      title="Save your recovery codes"
      description="If you lose the phone with your authenticator app, each code signs you in once. They are shown only now."
      onClose={onClose}
      footer={<Button variant="primary" onClick={onClose}>{saved ? "Done" : "I saved them"}</Button>}
    >
      <ol className="recovery-codes" aria-label="Recovery codes">
        {codes.map((code) => <li key={code} className="mono tabular">{code}</li>)}
      </ol>
      <div className="row" style={{ marginTop: 12 }}>
        <Button size="sm" icon={copied ? <Check /> : <Copy />} onClick={copy}>{copied ? "Copied" : "Copy all"}</Button>
        <Button size="sm" icon={<Download />} onClick={download}>Download</Button>
      </div>
      <p className="small muted" style={{ marginTop: 12 }}>
        Keep them in a password manager or somewhere safe, away from your phone. Making new codes stops these from working.
      </p>
    </Modal>
  );
}

/** How many codes are left, and a button for new ones (the password confirms). */
export function RecoveryCodesRow({ state, needed, onChange }: { state: RecoveryCodes | null; needed: boolean; onChange: () => void }) {
  const toast = useToast();
  const client = useQueryClient();
  const [asking, setAsking] = useState(false);
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [codes, setCodes] = useState<string[] | null>(null);
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
      toast("error", "No new codes", errorText(error));
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
            <span className="spacer">{state ? "You used every recovery code." : "You have no recovery codes yet."} Without them, losing your phone locks you out.</span>
            <Button size="sm" variant="primary" onClick={() => setAsking(true)}>Make recovery codes</Button>
          </div>
        </Callout>
      ) : (
        state && (
          <div className="choice" style={{ cursor: "default" }}>
            <LifeBuoy />
            <span style={{ flex: 1 }}>
              <span className="t row">Recovery codes</span>
              <span className="d">
                <span className={`tabular ${low ? "caution-text" : ""}`}>{state.left} of {state.total} left</span>
                {state.created_at && ` · made ${ago(state.created_at)}`}. Each signs you in once if you lose your phone.
              </span>
            </span>
            <Button size="sm" variant="ghost" icon={<RefreshCw />} onClick={() => setAsking(true)}>New codes</Button>
          </div>
        )
      )}
      {asking && (
        <Modal
          title={state ? "Make new recovery codes?" : "Make recovery codes"}
          description={state ? "The codes you have now stop working. Type your password to continue." : "Type your password to continue."}
          onClose={() => { setAsking(false); setPassword(""); }}
          footer={<><Button onClick={() => { setAsking(false); setPassword(""); }}>Cancel</Button><Button variant="primary" loading={busy} disabled={!password} onClick={make}>Make codes</Button></>}
        >
          <form onSubmit={(e) => { e.preventDefault(); if (password) make(); }}>
            <Field label="Password"><input className="input" type="password" autoComplete="current-password" autoFocus value={password} onChange={(e) => setPassword(e.target.value)} /></Field>
          </form>
        </Modal>
      )}
      {codes && <RecoveryCodesSheet codes={codes} onClose={() => setCodes(null)} />}
    </>
  );
}
