// The demo's live events: what the server would push on GET /api/v1/events. The fake EventSource (transport.ts)
// listens here and hands each event to the web app exactly as the real stream frames it.

export type Topic = "instance" | "instance.deleted" | "project" | "domains" | "job" | "job.output" | "metrics" | "settings" | "plugins" | "marketplace" | "alert" | "offsite" | "premium" | "support" | "reset";
type Listener = (topic: Topic, key: string, data: unknown, id: number) => void;

const listeners = new Set<Listener>();
let sequence = 0;

export function publish(topic: Topic, key: string, data: unknown) {
  sequence += 1;
  // a copy, as if it had travelled as JSON: later changes to the demo's state must not reach the caches by reference
  const copy = JSON.parse(JSON.stringify(data)) as unknown;
  for (const listener of listeners) listener(topic, key, copy, sequence);
}

export function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
