import { useQueryClient } from "@tanstack/react-query";
import { LogOut } from "lucide-react";
import { useState } from "react";
import { Navigate, useNavigate } from "react-router";
import { api, errorText } from "@/api/client";
import { keys, useLicenceText } from "@/api/queries";
import type { LicenceAcceptance as Acceptance, LicenceStatus, Session } from "@/api/types";
import { LicenceAcceptance } from "@/components/LicenceAcceptance";
import { useMe } from "@/components/Session";
import { useSignOut } from "@/components/Shell";
import { LanguageSwitch } from "@/components/LanguageSwitch";
import { Brand } from "@/components/app/Brand";
import { Button, Callout, Spinner } from "@/components/app/basics";
import { useT } from "@/i18n";
import common from "@/i18n/common";
import words from "@/i18n/licence";

/** The licence agreement for a super admin: before anything else when acceptance is pending (an installation set up
 *  before 3.0, or a newer version required), or again from Settings, About. Instances keep serving meanwhile. */
export function LicencePage() {
  const me = useMe();
  const text = useLicenceText();
  const client = useQueryClient();
  const navigate = useNavigate();
  const signOut = useSignOut();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const t = useT(words);
  const signOutLabel = useT(common).nav.signOut;
  if (me.role !== "super_admin") return <Navigate to="/" replace />;
  const required = me.licence_required;

  const accept = async (acceptance: Acceptance) => {
    setBusy(true);
    setError("");
    try {
      const status = await api.post<LicenceStatus>("/licence/acceptances", acceptance);
      client.setQueryData(keys.licence, status);
      client.setQueryData<Session>(keys.session, (old) => (old ? { ...old, licence_required: false } : old));
      await client.invalidateQueries({ predicate: (query) => query.queryKey[0] !== "session" && query.queryKey[0] !== "licence" });
      navigate(required ? "/" : "/settings?tab=about", { replace: true, viewTransition: true });
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-panel" style={{ minHeight: "100vh" }}>
      <div className="auth-card wide">
        <div className="row"><Brand /><span className="spacer" /><LanguageSwitch signedIn /></div>
        <header>
          <h1>{required ? t.requiredTitle : t.title}</h1>
          <p>{required ? t.requiredText : t.againText}</p>
        </header>
        {text.data ? (
          <LicenceAcceptance
            text={text.data}
            busy={busy}
            error={error}
            onAccept={accept}
            secondary={
              required ? (
                <Button variant="ghost" icon={<LogOut />} onClick={signOut}>{signOutLabel}</Button>
              ) : (
                <Button variant="ghost" onClick={() => navigate("/settings?tab=about", { viewTransition: true })}>{t.cancel}</Button>
              )
            }
          />
        ) : text.isError ? (
          <Callout tone="danger">{t.loadFailed}</Callout>
        ) : (
          <Spinner />
        )}
      </div>
    </div>
  );
}
