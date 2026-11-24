// What happens on the demo server by itself while the visitor looks around: a push to GitHub updates a development
// instance, a public link stops answering for a moment and comes back, and the alerts say so.

import type { Alert } from "@/api/types";
import { publish } from "./bus";
import { startJob } from "./jobs";
import { alerts, commitsOf, findRepo, instances, session, UPDATE_STEPS } from "./state";
import { hex, iso, SECOND } from "./util";

function raise(alert: Alert) {
  const index = alerts.findIndex((a) => a.id === alert.id);
  if (index >= 0) alerts[index] = alert;
  else alerts.unshift(alert);
  publish("alert", alert.key, alert);
}

export function startScenario() {
  const timers: ReturnType<typeof setTimeout>[] = [];
  const later = (seconds: number, fn: () => void) => timers.push(setTimeout(fn, seconds * SECOND));

  // a push to fleet-gps: the GitHub App starts "Get latest code" on its own
  later(8, () => {
    const instance = instances.get("nile-logistics-fleet-gps");
    if (!instance || instance.current_job || instance.status !== "online") return;
    const user = session.user;
    session.user = "GitHub push";
    startJob({
      instance: instance.name,
      action: "Get latest code",
      kind: "update",
      steps: UPDATE_STEPS,
      seconds: [3, 2, 3, 4, 3],
      status: "busy",
      onDone: () => {
        const repo = findRepo(instance.git.repository)!;
        const commit = {
          sha: hex(7),
          subject: "Driver app: offline mode for proof of delivery",
          author: "Omar",
          when: iso(Date.now())!,
        };
        instance.recent_commits = [commit, ...commitsOf(repo, "fleet-gps")].slice(0, 10);
        instance.git.commit = { sha: commit.sha, subject: commit.subject, author: commit.author, age: "just now" };
        instance.status = "online";
      },
    });
    session.user = user;
  });

  // the public link of a development instance stops answering for half a minute
  const id = hex(12);
  const name = "acme-retail-pos-refunds";
  const base = {
    id,
    key: `public_link:${name}`,
    type: "public_link" as const,
    details: [],
    instance: name,
    link: `/i/${name}`,
    count: 1,
    delivery: ["e-mailed to 2 of 2 addresses", "webhook 1: delivered (HTTP 200)"],
  };
  let started = 0;
  later(55, () => {
    if (!instances.has(name)) return;
    started = Date.now() / 1000;
    raise({
      ...base,
      severity: "problem",
      title: `The public link of ${name} does not answer`,
      text: `Visitors cannot reach ${name} through its link for 10 minutes now; Odoo itself runs. The panel renews the link by itself.`,
      hint: "Nothing to do unless it lasts.",
      at: started,
      last_at: started,
      resolved_at: null,
    });
  });
  later(85, () => {
    if (!started) return;
    const now = Date.now() / 1000;
    raise({
      ...base,
      severity: "recovered",
      title: `The public link of ${name} answers again`,
      text: `${name} is reachable through its link again, after 10 minutes.`,
      hint: null,
      at: started,
      last_at: now,
      resolved_at: now,
    });
  });
  return () => timers.forEach(clearTimeout);
}
