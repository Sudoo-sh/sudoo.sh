// An instance's Logs tab: Odoo's own log (odoo.log; jobs' technical logs are server logs, never shown), followed live over
// Server-Sent Events (GET /api/v1/instances/<name>/logs/stream, web/v1/log_stream.py). What is typed in the search
// box filters on the server too (plain text, any case) and is highlighted in the lines; Pause keeps the lines that
// arrive meanwhile and counts them; Download saves the log as text with the same filter. The stream ends every few
// minutes on the server's side and is opened again from where it stopped, and a stopped Odoo is asked again later.

import { useQuery } from "@tanstack/react-query";
import { Download, WrapText } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/api/client";
import { useT } from "@/i18n";
import toolWords from "@/i18n/tools";
import { Button, Callout } from "./app/basics";
import { SelectBox } from "./app/select";
import { LogView } from "./LogView";

interface LogFile {
  name: string;
  job: string | null;
  status: string | null;
  started_at: string | null;
}

const KEEP = 20000; // lines kept in the page; older ones are dropped (the download has more)
const TAIL = 500; // earlier lines sent when the log is opened
const WRAP_KEY = "sudoo.logs.wrap";

function storedWrap(): boolean {
  try {
    return localStorage.getItem(WRAP_KEY) !== "off";
  } catch {
    return true;
  }
}

function useDebounced(value: string, delay = 400): string {
  const [shown, setShown] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setShown(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return shown;
}

const kept = (lines: string[]) => (lines.length > KEEP ? lines.slice(lines.length - KEEP) : lines);

type State = "connecting" | "open" | "stopped" | "finished";

export function LogsPanel({ name }: { name: string }) {
  const t = useT(toolWords).logs;
  const files = useQuery({
    queryKey: ["log-files", name],
    queryFn: () => api.get<LogFile[]>(`/instances/${name}/logs/files`),
    refetchInterval: 30_000,
  });
  const [log, setLog] = useState("odoo.log");
  const [typed, setTyped] = useState("");
  const query = useDebounced(typed.trim());
  const [wrap, setWrap] = useState(storedWrap);
  const [lines, setLines] = useState<string[]>([]);
  const [state, setState] = useState<State>("connecting");
  const [paused, setPaused] = useState(false);
  const [pendingCount, setPendingCount] = useState(0);
  const pending = useRef<string[]>([]);
  const pausedNow = useRef(false);
  pausedNow.current = paused;

  const add = useCallback((got: string[]) => {
    if (!got.length) return;
    if (pausedNow.current) {
      pending.current = kept(pending.current.concat(got));
      setPendingCount(pending.current.length);
    } else {
      setLines((before) => kept(before.concat(got)));
    }
  }, []);

  useEffect(() => {
    let source: EventSource | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let lastId = "";
    let ended = false;
    setLines([]);
    pending.current = [];
    setPendingCount(0);
    setState("connecting");
    const open = (after?: string) => {
      const params = new URLSearchParams({ log, tail: after ? "0" : String(TAIL) });
      if (query) params.set("q", query);
      if (after) params.set("after", after);
      const current = new EventSource(`/api/v1/instances/${name}/logs/stream?${params}`);
      source = current;
      current.onopen = () => setState((s) => (s === "connecting" || s === "stopped" ? "open" : s));
      current.addEventListener("lines", (event) => {
        const message = event as MessageEvent<string>;
        if (message.lastEventId) lastId = message.lastEventId;
        add((JSON.parse(message.data) as { lines: string[] }).lines);
        setState("open");
      });
      current.addEventListener("end", (event) => {
        const message = event as MessageEvent<string>;
        if (message.lastEventId) lastId = message.lastEventId;
        const reason = (JSON.parse(message.data) as { reason: string }).reason;
        current.close();
        if (ended) return;
        if (reason === "max_duration") open(lastId || undefined);
        else if (reason === "stopped") {
          setState("stopped");
          timer = setTimeout(() => open(lastId || undefined), 5000);
        } else setState("finished");
      });
      current.onerror = () => {
        // EventSource tries again by itself (with Last-Event-ID) unless the server refused it: then later
        if (current.readyState === EventSource.CLOSED && !ended) timer = setTimeout(() => open(lastId || undefined), 10_000);
      };
    };
    open();
    return () => {
      ended = true;
      source?.close();
      clearTimeout(timer);
    };
  }, [name, log, query, add]);

  const onPause = (next: boolean) => {
    setPaused(next);
    if (!next && pending.current.length) {
      const waiting = pending.current;
      pending.current = [];
      setPendingCount(0);
      setLines((before) => kept(before.concat(waiting)));
    }
  };
  const toggleWrap = () => {
    setWrap((w) => {
      try {
        localStorage.setItem(WRAP_KEY, w ? "off" : "on");
      } catch {
        // a private window: the choice lasts for this page only
      }
      return !w;
    });
  };

  const available = files.data ?? [{ name: "odoo.log", job: null, status: null, started_at: null }];
  const download = `/api/v1/instances/${name}/logs/download?${new URLSearchParams({ log, ...(query ? { q: query } : {}) })}`;
  const tools = (
    <div className="row">
      <SelectBox size="sm" aria-label={t.which} value={log} onValueChange={setLog} dir="ltr">
        {available.map((file) => (
          <option key={file.name} value={file.name}>{t.names[file.name] ?? file.name}</option>
        ))}
      </SelectBox>
      <Button size="sm" variant={wrap ? "default" : "ghost"} icon={<WrapText />} aria-pressed={wrap} onClick={toggleWrap} aria-label={t.wrap} title={t.wrap} />
      <Button asChild size="sm" variant="ghost" icon={<Download />}>
        <a href={download} download title={t.downloadText}>{t.download}</a>
      </Button>
    </div>
  );
  return (
    <div className="stack">
      {state === "stopped" && <Callout tone="info">{t.stopped}</Callout>}
      <LogView
        text={lines.join("\n")}
        live={state === "open" && !paused}
        empty={state === "connecting" ? t.connecting : query ? t.noMatch : t.empty}
        tools={tools}
        wrap={wrap}
        onQuery={setTyped}
        pause={{ paused, pending: pendingCount, onChange: onPause }}
      />
    </div>
  );
}
