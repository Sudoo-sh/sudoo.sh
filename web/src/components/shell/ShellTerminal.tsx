// One shell in the browser: a one-time ticket from the API (an authenticated POST), then a WebSocket that carries
// what is typed (binary) and what the terminal shows (binary), plus a few JSON messages: ready, notices before it
// closes, and why it closed. The terminal (xterm.js, ./xterm.ts) loads with the first shell. Always left to right.
//
// Also the player of a recording (ShellPlayer), for super admins.

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ClipboardPaste, Copy, Download, Eraser, Maximize2, Minimize2, Play, RotateCw, ShieldAlert, X } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { api, errorText } from "@/api/client";
import type { ShellRecording, ShellSession, ShellTarget, ShellTicket } from "@/api/types";
import { Button, Callout, Skeleton, Tag } from "@/components/ui/basics";
import { Modal, useToast } from "@/components/ui/overlay";
import { useT } from "@/i18n";
import words from "@/i18n/shell";
import { dateTime } from "@/lib/format";
import type { ShellScreen } from "./xterm";

interface Ready {
  session: string;
  instance: string;
  target: ShellTarget;
  container_user: string;
  root: boolean;
  production: boolean;
  recorded: boolean;
}
type Phase = { kind: "connecting" } | { kind: "open"; ready: Ready } | { kind: "closed"; reason: string; detail?: string };
type Notice = { code: "idle" | "ending"; seconds: number } | null;

const isCopy = (e: KeyboardEvent) => e.type === "keydown" && e.ctrlKey && e.shiftKey && e.code === "KeyC";
const isPaste = (e: KeyboardEvent) => e.ctrlKey && e.shiftKey && e.code === "KeyV";

/** Full screen with the browser's own full-screen mode where there is one, else filling the window. */
function useFullScreen(frame: React.RefObject<HTMLDivElement | null>) {
  const [full, setFull] = useState(false);
  useEffect(() => {
    const onChange = () => setFull(document.fullscreenElement === frame.current && !!frame.current);
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, [frame]);
  useEffect(() => {
    if (!full || document.fullscreenElement) return;
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && event.shiftKey && setFull(false);
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [full]);
  const toggle = () => {
    const el = frame.current;
    if (!el) return;
    if (document.fullscreenElement) void document.exitFullscreen();
    else if (full) setFull(false);
    else if (el.requestFullscreen) el.requestFullscreen().catch(() => setFull(true));
    else setFull(true);
  };
  return [full, toggle] as const;
}

export function ShellTerminal({ instance, target, asRoot, confirmed, onReconnect, onClose }: {
  instance: string;
  target: ShellTarget;
  asRoot: boolean;
  /** the person confirmed opening it in production, this time */
  confirmed: boolean;
  onReconnect: () => void;
  onClose: () => void;
}) {
  const t = useT(words);
  const toast = useToast();
  const client = useQueryClient();
  const host = useRef<HTMLDivElement>(null);
  const frame = useRef<HTMLDivElement>(null);
  const screen = useRef<ShellScreen | null>(null);
  const socket = useRef<WebSocket | null>(null);
  const [phase, setPhase] = useState<Phase>({ kind: "connecting" });
  const [notice, setNotice] = useState<Notice>(null);
  const [copied, setCopied] = useState(false);
  const [full, toggleFull] = useFullScreen(frame);

  const copySelection = async () => {
    const text = screen.current?.term.getSelection() ?? "";
    if (!text) return;
    await navigator.clipboard.writeText(text).catch(() => undefined);
    setCopied(true);
    setTimeout(() => setCopied(false), 1400);
  };

  useEffect(() => {
    let cancelled = false;
    const abort = new AbortController();
    let ws: WebSocket | null = null;
    let opened: ShellScreen | null = null;
    void (async () => {
      const { openTerminal } = await import("./xterm");
      if (cancelled || !host.current) return;
      opened = openTerminal(host.current);
      screen.current = opened;
      const { term } = opened;
      // copy and paste as in terminals: Ctrl+Shift+C copies the selection, Ctrl+Shift+V lets the browser paste
      // (⌘C and ⌘V work as everywhere on a Mac); plain Ctrl+C stays an interrupt for the shell
      term.attachCustomKeyEventHandler((event) => {
        if (isCopy(event)) {
          void copySelection();
          event.preventDefault();
          return false;
        }
        return !isPaste(event);
      });
      let ticket: ShellTicket;
      try {
        ticket = await api.post<ShellTicket>(
          `/instances/${instance}/shell/tickets`,
          { target, as_root: asRoot, confirm_production: confirmed },
          abort.signal,
        );
      } catch (error) {
        if (!cancelled) setPhase({ kind: "closed", reason: "refused", detail: errorText(error) });
        return;
      }
      if (cancelled) return;
      const scheme = location.protocol === "https:" ? "wss:" : "ws:";
      ws = new WebSocket(`${scheme}//${location.host}${ticket.socket}?cols=${term.cols}&rows=${term.rows}`, [ticket.protocol, `ticket.${ticket.ticket}`]);
      ws.binaryType = "arraybuffer";
      socket.current = ws;
      let ended: Phase | null = null;
      const encoder = new TextEncoder();
      const send = (data: Uint8Array<ArrayBuffer> | string) => ws?.readyState === WebSocket.OPEN && ws.send(data);
      ws.onmessage = (event: MessageEvent<ArrayBuffer | string>) => {
        if (typeof event.data !== "string") {
          term.write(new Uint8Array(event.data));
          return;
        }
        let message: { type?: string; code?: string; seconds?: number; reason?: string; detail?: string } & Partial<Ready>;
        try {
          message = JSON.parse(event.data);
        } catch {
          return;
        }
        if (message.type === "ready") {
          setPhase({ kind: "open", ready: message as Ready });
          term.focus();
          void client.invalidateQueries({ queryKey: ["shells", instance] });
        } else if (message.type === "notice" && (message.code === "idle" || message.code === "ending")) {
          setNotice({ code: message.code, seconds: Number(message.seconds) || 0 });
        } else if (message.type === "closed") {
          ended = { kind: "closed", reason: String(message.reason ?? "closed"), detail: message.detail || undefined };
        }
      };
      ws.onclose = () => {
        void client.invalidateQueries({ queryKey: ["shells", instance] });
        if (cancelled) return;
        setPhase(ended ?? { kind: "closed", reason: "lost" });
        setNotice(null);
        term.options.cursorBlink = false;
        term.blur();
      };
      term.onData((data) => {
        send(encoder.encode(data));
        setNotice((n) => (n?.code === "idle" ? null : n));
      });
      term.onBinary((data) => send(Uint8Array.from(data, (c) => c.charCodeAt(0) & 0xff)));
      term.onResize(({ cols, rows }) => send(JSON.stringify({ type: "resize", cols, rows })));
    })();
    return () => {
      cancelled = true;
      abort.abort();
      ws?.close();
      opened?.dispose();
      screen.current = null;
      socket.current = null;
    };
    // a new shell for each mount (Reconnect mounts a new one)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const paste = async () => {
    try {
      const text = await navigator.clipboard.readText();
      if (text) screen.current?.term.paste(text);
      screen.current?.term.focus();
    } catch {
      toast("info", t.pasteBlocked);
    }
  };

  const ready = phase.kind === "open" ? phase.ready : null;
  const closed = phase.kind === "closed" ? phase : null;
  return (
    <div className={`shell-frame${full ? " shell-full" : ""}`} ref={frame} data-phase={phase.kind}>
      <div className="shell-bar">
        <span className="shell-state">
          <span className="dot" aria-hidden="true" />
          {ready ? (
            <span className="truncate">{t.connectedTo(<b key="i" className="mono">{ready.instance}</b>, <b key="u" className="mono">{ready.container_user}</b>)}</span>
          ) : closed ? (
            t.closedTitle
          ) : (
            t.connecting
          )}
        </span>
        {ready?.production && <Tag tone="caution">{t.production}</Tag>}
        {ready?.root && <Tag tone="negative" icon={<ShieldAlert />}>{t.root}</Tag>}
        {ready?.recorded && <span title={t.recordedHint}><Tag tone="accent">{t.recorded}</Tag></span>}
        <span className="spacer" />
        <div className="shell-actions">
          <Button size="sm" variant="ghost" icon={copied ? <Check /> : <Copy />} onClick={copySelection} aria-label={copied ? t.copied : t.copy} title={`${copied ? t.copied : t.copy}\n${t.keys}`} />
          <Button size="sm" variant="ghost" icon={<ClipboardPaste />} disabled={!ready} onClick={paste} aria-label={t.paste} title={t.paste} />
          <Button size="sm" variant="ghost" icon={<Eraser />} onClick={() => screen.current?.term.clear()} aria-label={t.clear} title={t.clear} />
          <Button size="sm" variant="ghost" icon={full ? <Minimize2 /> : <Maximize2 />} onClick={toggleFull} aria-label={full ? t.exitFullScreen : t.fullScreen} title={full ? t.exitFullScreen : t.fullScreen} />
          {closed ? (
            <>
              <Button size="sm" variant="ghost" onClick={onClose}>{t.done}</Button>
              <Button size="sm" icon={<RotateCw />} onClick={onReconnect}>{t.reconnect}</Button>
            </>
          ) : (
            <Button size="sm" variant="ghost" icon={<X />} onClick={() => (socket.current ? socket.current.close() : onClose())} aria-label={t.disconnect} title={t.disconnect} />
          )}
        </div>
      </div>
      {notice && (
        <div className="shell-notice" role="status">
          {notice.code === "idle" ? t.idleSoon(notice.seconds) : t.endingSoon(notice.seconds)}
        </div>
      )}
      <div className="shell-screen" dir="ltr">
        <div className="shell-host" ref={host} />
        {phase.kind === "connecting" && <div className="shell-overlay"><span className="spinner" /></div>}
      </div>
      {closed && (
        <div className="shell-closed" role="status">
          <span>{t.reasons[closed.reason] ?? t.reasons.closed}</span>
          {closed.detail && <span className="muted">{closed.detail}</span>}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- recordings (super admins)

function castFile(recording: ShellRecording): string {
  return [JSON.stringify(recording.header), ...recording.events.map((e) => JSON.stringify(e))].join("\n") + "\n";
}

export function ShellPlayer({ session, onClose }: { session: ShellSession; onClose: () => void }) {
  const t = useT(words);
  const host = useRef<HTMLDivElement>(null);
  const screen = useRef<ShellScreen | null>(null);
  const timers = useRef<number[]>([]);
  const { data, error } = useQuery({
    queryKey: ["shell-recording", session.id],
    queryFn: () => api.get<ShellRecording>(`/shells/${session.id}/recording`),
    staleTime: Infinity,
  });

  const stop = () => {
    for (const timer of timers.current) clearTimeout(timer);
    timers.current = [];
  };
  const showAll = (recording: ShellRecording) => {
    const term = screen.current?.term;
    if (!term) return;
    stop();
    term.reset();
    term.write(recording.events.filter((e) => e[1] === "o").map((e) => e[2]).join(""));
  };
  /** played again as it happened, pauses longer than a second shortened to one */
  const replay = (recording: ShellRecording) => {
    const term = screen.current?.term;
    if (!term) return;
    stop();
    term.reset();
    let at = 0;
    let last = 0;
    for (const [time, kind, text] of recording.events) {
      at += Math.min(1, Math.max(0, time - last));
      last = time;
      if (kind === "o") timers.current.push(window.setTimeout(() => term.write(text), at * 1000));
    }
  };

  useEffect(() => {
    if (!data) return;
    let cancelled = false;
    void import("./xterm").then(({ openTerminal }) => {
      if (cancelled || !host.current) return;
      screen.current = openTerminal(host.current, { readOnly: true });
      showAll(data);
    });
    return () => {
      cancelled = true;
      stop();
      screen.current?.dispose();
      screen.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);

  const download = () => {
    if (!data) return;
    const url = URL.createObjectURL(new Blob([castFile(data)], { type: "application/x-asciicast" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `${session.instance}-${session.id}.cast`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  const cut = data?.events.some((e) => e[1] === "m" && e[2].includes("size limit"));
  const footer: ReactNode = (
    <>
      <Button icon={<Download />} disabled={!data} onClick={download}>{t.download}</Button>
      <Button variant="primary" icon={<Play />} disabled={!data} onClick={() => data && replay(data)}>{t.replay}</Button>
    </>
  );
  return (
    <Modal wide title={t.recordingTitle(session.instance)} description={t.recordingBy(session.user, dateTime(session.started_at))} onClose={onClose} footer={footer}>
      {error ? (
        <Callout tone="danger">{errorText(error)}</Callout>
      ) : !data ? (
        <Skeleton height={360} />
      ) : (
        <>
          {cut && <Callout tone="warning">{t.recordingCut}</Callout>}
          <div className="shell-screen shell-player" dir="ltr">
            <div className="shell-host" ref={host} />
          </div>
        </>
      )}
    </Modal>
  );
}
