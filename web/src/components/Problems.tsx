// When something goes wrong. The error screen replaces a page that fails to draw (a render error anywhere in the app
// never leaves a blank page), and the banner tells administrators what is wrong with the panel while it keeps working
// around it (GET /health: a plugin that failed, a setting with a wrong value, background work that keeps failing).

import { useQueryClient } from "@tanstack/react-query";
import { Component, useState, type ErrorInfo, type ReactNode } from "react";
import { useRouteError } from "react-router";
import { api, errorText } from "@/api/client";
import { keys, usePanelHealth } from "@/api/queries";
import type { FailedPlugin } from "@/api/types";
import { useT } from "@/i18n";
import problems from "@/i18n/problems";
import { Button, Callout } from "./app/basics";
import { useToast } from "./app/overlay";
import { useCan } from "./Session";

const SHOWN = 4; // problems listed in the banner; the rest are counted

/** The page shown instead of one that failed to draw. Plain markup on purpose: it must draw when the rest cannot. */
export function ErrorScreen({ error }: { error?: unknown }) {
  const t = useT(problems).screen;
  const detail = error instanceof Error ? error.message : error == null ? "" : String(error);
  return (
    <main role="alert" style={{ display: "grid", placeItems: "center", minHeight: "100vh", padding: "16px" }}>
      <div style={{ maxWidth: 520, display: "flex", flexDirection: "column", gap: 16 }}>
        <h1 style={{ fontSize: "1.25rem", fontWeight: 600 }}>{t.title}</h1>
        <p>{t.text}</p>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <Button variant="primary" onClick={() => window.location.reload()}>
            {t.reload}
          </Button>
          <Button onClick={() => window.location.assign("/")}>{t.home}</Button>
        </div>
        {detail && (
          <details>
            <summary>{t.details}</summary>
            <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", fontSize: "0.8rem" }}>
              {detail.slice(0, 500)}
            </pre>
          </details>
        )}
      </div>
    </main>
  );
}

/** The router's error element: a page (or its code) that failed to load or draw. */
export function RouteError() {
  return <ErrorScreen error={useRouteError()} />;
}

/** Around the whole app: any render error outside the router's pages shows the error screen, never a blank page. */
export class AppErrorBoundary extends Component<{ children: ReactNode }, { error: unknown }> {
  state: { error: unknown } = { error: null };
  static getDerivedStateFromError(error: unknown) {
    return { error: error ?? new Error("unknown") };
  }
  componentDidCatch(error: unknown, info: ErrorInfo) {
    console.error("the page failed to draw", error, info.componentStack);
  }
  render() {
    return this.state.error ? <ErrorScreen error={this.state.error} /> : this.props.children;
  }
}

/** The administrators' banner: what is wrong with the panel, and a switch for plugins switched off after failing. */
export function PanelProblems() {
  const can = useCan();
  const { data } = usePanelHealth(can("plugins.view"));
  const t = useT(problems).banner;
  if (!data || data.status === "ok") return null;
  const listed = data.problems.slice(0, SHOWN);
  const off = data.plugins.filter((p) => p.disabled);
  const danger = data.problems.some((p) => p.level === "danger") || off.length > 0;
  return (
    <div className="notices" role="status">
      <Callout tone={danger ? "danger" : "warning"}>
        <strong>{t.title}</strong> {t.text}
        <ul style={{ margin: "4px 0 0", paddingInlineStart: 18 }}>
          {listed.map((p) => (
            <li key={`${p.kind}-${p.key}`}>
              {t.kinds[p.kind] ?? p.kind}: {p.title}
              {p.detail && <span style={{ opacity: 0.8, overflowWrap: "anywhere" }}> ({p.detail})</span>}
            </li>
          ))}
          {data.problems.length > SHOWN && <li>{t.more(data.problems.length - SHOWN)}</li>}
        </ul>
        {can("settings.manage") &&
          off.map((plugin) => <SwitchOn key={`${plugin.group}-${plugin.name}`} plugin={plugin} />)}
      </Callout>
    </div>
  );
}

function SwitchOn({ plugin }: { plugin: FailedPlugin }) {
  const t = useT(problems).banner;
  const toast = useToast();
  const client = useQueryClient();
  const [busy, setBusy] = useState(false);
  const switchOn = async () => {
    setBusy(true);
    try {
      await api.post(`/health/plugins/${encodeURIComponent(plugin.group)}/${encodeURIComponent(plugin.name)}/enable`);
      toast("success", t.switchedOn(plugin.name));
      await client.invalidateQueries({ queryKey: keys.health });
    } catch (error) {
      toast("error", errorText(error));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
      <span>
        {plugin.label} {plugin.name}: {t.switchedOff}, {t.failures(plugin.failures)}
      </span>
      <Button size="sm" loading={busy} onClick={switchOn}>
        {t.switchOn}
      </Button>
    </div>
  );
}
