// The live event stream (GET /api/v1/events): every change arrives as it happens and is written straight into
// the query caches, so pages update without asking. EventSource reconnects by itself and resumes where it stopped.
// When the server refuses the stream (too many open connections: 429 or 503), EventSource gives up; the stream is
// then opened again after a growing pause, unless the session has ended.

import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { keys } from "./queries";
import type { Alert, BuildEvent, Columns, Domains, Instance, Job, Metrics, MetricsHistory, Project } from "./types";

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
      const known = client.getQueryData<Instance[]>(keys.instances)?.some((i) => i.name === instance.name);
      if (instance.project && known === false) {
        // a new instance of a project: its branches and counts change (statuses come from the instances list)
        client.invalidateQueries({ queryKey: keys.project(instance.project.id) });
        client.invalidateQueries({ queryKey: keys.projects });
      }
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
    case "project": {
      const project = data as Project | { id: string; deleted: true };
      if ("deleted" in project) {
        client.setQueryData<{ projects: Project[]; standalone: string[] }>(keys.projects, (old) =>
          old ? { ...old, projects: old.projects.filter((p) => p.id !== key) } : old,
        );
        client.removeQueries({ queryKey: keys.project(key) });
      } else {
        client.setQueryData<{ projects: Project[]; standalone: string[] }>(keys.projects, (old) =>
          old
            ? {
                ...old,
                projects: upsert(old.projects, project, (p) => p.id === project.id)!.sort((a, b) =>
                  a.name.localeCompare(b.name),
                ),
              }
            : old,
        );
        client.invalidateQueries({ queryKey: keys.project(key) });
      }
      break;
    }
    case "domains":
      client.setQueryData<Domains>(keys.domains(key), data as Domains);
      break;
    case "instance.deleted":
      client.setQueryData<Instance[]>(keys.instances, (list) => list?.filter((i) => i.name !== key));
      client.invalidateQueries({ queryKey: keys.projects });
      client.invalidateQueries({ queryKey: ["project"] });
      client.removeQueries({ queryKey: keys.instance(key) });
      break;
    case "job": {
      const job = data as Job;
      client.setQueryData(keys.job(job.id), job);
      // an instance being created belongs to its branch at once: the branch pane shows the creation, not "No instance"
      // and a Create button (read once when the creation is first heard of, and again when it ends)
      if (
        job.kind === "create" &&
        (!creationsSeen.has(job.id) || job.status === "success" || job.status === "failed")
      ) {
        creationsSeen.add(job.id);
        client.invalidateQueries({ queryKey: ["project"] });
      }
      for (const scope of [undefined, job.instance]) {
        client.setQueryData<Job[]>(keys.jobs(scope), (list) => upsert(list, job, (j) => j.id === job.id, true));
      }
      if (job.status === "success" || job.status === "failed") {
        if (job.kind.startsWith("plugin")) client.invalidateQueries({ queryKey: keys.plugins });
        // a deleted instance is gone: reading it again would only answer 404
        else if (job.kind === "delete" && job.status === "success")
          client.removeQueries({ queryKey: keys.instance(job.instance) });
        else client.invalidateQueries({ queryKey: keys.instance(job.instance) });
        if (job.kind === "backup" || job.kind === "delete") client.invalidateQueries({ queryKey: ["backups"] });
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
      // the standing default-admin-password alert depends on a setting: reread the bell after a change
      client.invalidateQueries({ queryKey: keys.alerts });
      client.invalidateQueries({ queryKey: ["account"] });
      client.invalidateQueries({ queryKey: ["quota"] });
      client.invalidateQueries({ queryKey: ["offsite"] });
      break;
    case "offsite": // an off-site copy changed (a restore also adds its files to the local backups)
      client.invalidateQueries({ queryKey: ["offsite"] });
      client.invalidateQueries({ queryKey: ["backups"] });
      break;
    case "plugins":
      client.invalidateQueries({ queryKey: keys.plugins });
      break;
    case "marketplace":
      client.invalidateQueries({ queryKey: keys.marketplace });
      client.invalidateQueries({ queryKey: keys.plugins });
      client.invalidateQueries({ queryKey: keys.plan });
      break;
    case "premium": // a premium feature was installed, updated or switched off
      client.invalidateQueries({ queryKey: ["premium"] });
      break;
    case "support": // support access changed: a grant, or a session starting or ending
      client.invalidateQueries({ queryKey: ["support-access"] }); // the premium support package's queries
      break;
    case "alert": {
      const alert = data as Alert;
      client.setQueryData<Alert[]>(keys.alerts, (list) =>
        upsert(list, alert, (a) => a.id === alert.id, true)
          ?.sort((a, b) => b.last_at - a.last_at)
          .slice(0, 30),
      );
      break;
    }
    case "build": {
      // an entry of a branch's history was added or changed: the builds and that branch's timeline are read again
      const entry = data as BuildEvent;
      client.invalidateQueries({ queryKey: ["project", entry.project, "builds"] });
      client.invalidateQueries({ queryKey: ["project", entry.project, "history", entry.branch] });
      if ("removed" in entry) client.removeQueries({ queryKey: keys.build(entry.project, key) });
      else client.setQueryData(keys.build(entry.project, key), entry);
      break;
    }
    case "reset":
      client.invalidateQueries();
      break;
  }
}

/** Creation jobs already heard of (the "job" topic): the projects are read again once for each. */
const creationsSeen = new Set<string>();
const TOPICS = [
  "instance",
  "instance.deleted",
  "project",
  "build",
  "domains",
  "job",
  "metrics",
  "settings",
  "plugins",
  "marketplace",
  "premium",
  "support",
  "alert",
  "offsite",
  "reset",
];

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
    const answer = await fetch("/api/v1/session", {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    });
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
      const current = new EventSource(
        lastId ? `/api/v1/events?last_event_id=${encodeURIComponent(lastId)}` : "/api/v1/events",
      );
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
