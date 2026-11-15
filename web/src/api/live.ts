// The live event stream (GET /api/v1/events): every change arrives as it happens and is written straight into
// the query caches, so pages update without asking. EventSource reconnects by itself and resumes where it stopped.
// When the server refuses the stream (too many open connections: 429 or 503), EventSource gives up; the stream is
// then opened again after a growing pause, unless the session has ended.

import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { keys, type JobLog } from "./queries";
import type { Columns, Instance, Job, Metrics, MetricsHistory } from "./types";

export type LiveState = "connecting" | "open" | "closed";

const HISTORY_SECONDS = 3600;

function upsert<T>(list: T[] | undefined, item: T, same: (a: T) => boolean, prepend = false): T[] | undefined {
  if (!list) return list;
  const index = list.findIndex(same);
  if (index === -1) return prepend ? [item, ...list] : [...list, item];
  const copy = list.slice();
  copy[index] = item;
  return copy;
}

function appendColumns(columns: Columns | undefined, t: number, values: Record<string, unknown>): Columns {
  const base: Columns = columns ?? ({ t: [] } as unknown as Columns);
  const keep = base.t.findIndex((time) => time >= t - HISTORY_SECONDS);
  const from = keep === -1 ? base.t.length : keep;
  const next: Columns = { t: [...base.t.slice(from), t] } as Columns;
  for (const key of new Set([...Object.keys(base), ...Object.keys(values)])) {
    if (key === "t") continue;
    const old = (base[key] ?? base.t.map(() => null)).slice(from);
    const value = values[key];
    next[key] = [...old, typeof value === "number" ? value : null];
  }
  return next;
}

function handle(client: QueryClient, topic: string, key: string, data: unknown) {
  switch (topic) {
    case "instance": {
      const instance = data as Instance;
      client.setQueryData<Instance[]>(keys.instances, (list) =>
        upsert(list, instance, (i) => i.name === instance.name)?.sort((a, b) => a.name.localeCompare(b.name)),
      );
      if (client.getQueryData(keys.instance(instance.name))) {
        client.setQueryData<Instance>(keys.instance(instance.name), (old) => (old ? { ...old, ...instance } : old));
      } else {
        // a page that asked before the instance was known (a creation just accepted) asks again
        client.invalidateQueries({ queryKey: keys.instance(instance.name) });
      }
      break;
    }
    case "instance.deleted":
      client.setQueryData<Instance[]>(keys.instances, (list) => list?.filter((i) => i.name !== key));
      client.removeQueries({ queryKey: keys.instance(key) });
      break;
    case "job": {
      const job = data as Job;
      client.setQueryData(keys.job(job.id), job);
      for (const scope of [undefined, job.instance]) {
        client.setQueryData<Job[]>(keys.jobs(scope), (list) => upsert(list, job, (j) => j.id === job.id, true));
      }
      if (job.status === "success" || job.status === "failed") {
        if (job.kind.startsWith("plugin")) client.invalidateQueries({ queryKey: keys.plugins });
        else client.invalidateQueries({ queryKey: keys.instance(job.instance) });
        if (job.kind === "backup" || job.kind === "delete") client.invalidateQueries({ queryKey: ["backups"] });
      }
      break;
    }
    case "job.output": {
      const piece = data as { job: string; offset: number; next_offset: number; text: string };
      const current = client.getQueryData<JobLog>(keys.jobLog(piece.job));
      if (!current) break;
      if (current.next === piece.offset) {
        client.setQueryData<JobLog>(keys.jobLog(piece.job), { text: current.text + piece.text, next: piece.next_offset });
      } else if (current.next < piece.offset) {
        client.invalidateQueries({ queryKey: keys.jobLog(piece.job) });
      }
      break;
    }
    case "metrics": {
      const metrics = data as Metrics;
      client.setQueryData(keys.metrics, metrics);
      const t = metrics.at ?? Math.floor(Date.now() / 1000);
      client.setQueryData<MetricsHistory>(keys.history, (history) => {
        if (!history) return history;
        const instances: Record<string, Columns> = {};
        for (const [name, figures] of Object.entries(metrics.instances)) {
          instances[name] = appendColumns(history.instances[name], t, figures as unknown as Record<string, unknown>);
        }
        return { server: appendColumns(history.server, t, metrics.server), instances };
      });
      break;
    }
    case "settings":
      client.invalidateQueries({ queryKey: keys.settings });
      client.invalidateQueries({ queryKey: ["account"] });
      client.invalidateQueries({ queryKey: ["quota"] });
      break;
    case "plugins":
      client.invalidateQueries({ queryKey: keys.plugins });
      break;
    case "marketplace":
      client.invalidateQueries({ queryKey: keys.marketplace });
      client.invalidateQueries({ queryKey: keys.plugins });
      break;
    case "reset":
      client.invalidateQueries();
      break;
  }
}

const TOPICS = ["instance", "instance.deleted", "job", "job.output", "metrics", "settings", "plugins", "marketplace", "reset"];

export interface Sync {
  state: LiveState;
  /** when the stream last opened (ms) */
  lastAt: number | null;
}

const RETRY_FIRST_MS = 5_000;
const RETRY_MAX_MS = 60_000;

/** Whether the session still works; the stream is refused for other reasons too (too many open connections). */
async function signedIn(): Promise<boolean> {
  try {
    const answer = await fetch("/api/v1/session", { credentials: "same-origin", headers: { Accept: "application/json" } });
    return answer.status !== 401;
  } catch {
    return true; // the server cannot be reached: keep trying
  }
}

export function useLiveEvents(enabled: boolean): Sync {
  const client = useQueryClient();
  const [sync, setSync] = useState<Sync>({ state: "connecting", lastAt: null });
  useEffect(() => {
    if (!enabled) return;
    let source: EventSource | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let stopped = false;
    let lastId = "";
    let pause = RETRY_FIRST_MS;
    const open = () => {
      const current = new EventSource(lastId ? `/api/v1/events?last_event_id=${encodeURIComponent(lastId)}` : "/api/v1/events");
      source = current;
      current.onopen = () => {
        pause = RETRY_FIRST_MS;
        setSync((s) => ({ state: "open", lastAt: s.lastAt ?? Date.now() }));
      };
      current.onerror = () => {
        if (current.readyState !== EventSource.CLOSED) {
          setSync((s) => ({ ...s, state: "connecting" })); // EventSource tries again by itself
          return;
        }
        current.close();
        void signedIn().then((yes) => {
          if (stopped) return;
          if (!yes) {
            setSync((s) => ({ ...s, state: "closed" }));
            return;
          }
          setSync((s) => ({ ...s, state: "connecting" }));
          timer = setTimeout(open, pause);
          pause = Math.min(pause * 2, RETRY_MAX_MS);
        });
      };
      for (const topic of TOPICS) {
        current.addEventListener(topic, (message) => {
          const event = message as MessageEvent<string>;
          if (event.lastEventId) lastId = event.lastEventId;
          // a new object only when the state changes: the frame (Shell) re-renders with it, and events arrive often
          setSync((s) => (s.state === "open" ? s : { state: "open", lastAt: Date.now() }));
          try {
            const { key, data } = JSON.parse(event.data);
            handle(client, topic, key, data);
          } catch {
            /* a malformed event is skipped; the next one carries the full state again */
          }
        });
      }
    };
    open();
    return () => {
      stopped = true;
      clearTimeout(timer);
      source?.close();
    };
  }, [client, enabled]);
  return sync;
}
