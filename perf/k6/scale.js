// Baseline, spike and soak of the panel with many records (perf/README.md, "Scale, spike and soak"), through the web
// service (Caddy) as browsers reach it. One script, three modes:
//
//   MODE=baseline  one user, each path asked ITERATIONS times one after the other: p50/p95/p99 with no load
//   MODE=spike     1 user, then 50 at once for SPIKE_SECONDS, then 1 again for RECOVERY_SECONDS: reads and sign-ins
//                  of 50 different accounts, with the panel's rate limits on (429 is a correct answer, 5xx is not)
//   MODE=soak      SOAK_VUS users with think time for SOAK_MINUTES: the mixed traffic of people using the panel
//
//   k6 run -e MODE=baseline -e BASE=http://127.0.0.1:19100 -e SECRETS=/out/k6-secrets.json -e OUT=/out/x.json scale.js
//
// SECRETS is the secrets.json of perf/seed.py with "users" and "user_password" (accounts that can sign in).

import http from "k6/http";
import { check, sleep } from "k6";
import exec from "k6/execution";
import { Counter, Trend } from "k6/metrics";

const BASE = __ENV.BASE || "http://127.0.0.1:19100";
const SECRETS = JSON.parse(open(__ENV.SECRETS));
const MODE = __ENV.MODE || "baseline";
const ITERATIONS = Number(__ENV.ITERATIONS || 30);
const SPIKE_SECONDS = Number(__ENV.SPIKE_SECONDS || 120);
const RECOVERY_SECONDS = Number(__ENV.RECOVERY_SECONDS || 60);
const SOAK_MINUTES = Number(__ENV.SOAK_MINUTES || 30);
const SOAK_VUS = Number(__ENV.SOAK_VUS || 10);

const headers = { Accept: "application/json", "Accept-Encoding": "gzip, br, zstd" };
const pick = (list) => list[Math.floor(Math.random() * list.length)];
const statuses = new Counter("statuses");
const limited = new Counter("rate_limited");
const serverErrors = new Counter("server_errors");
const signinTime = new Trend("signin_ms", true);

// the paths a person's browser asks for, by name
const READS = {
  session: () => "/api/v1/session",
  instances_list: () => "/api/v1/instances?limit=500",
  instances_page_2: () => "/api/v1/instances?limit=500&offset=500",
  instance_detail: () => `/api/v1/instances/${pick(SECRETS.instances)}`,
  projects_list: () => "/api/v1/projects",
  project_detail: () => `/api/v1/projects/project-${String(Math.floor(Math.random() * SECRETS.projects)).padStart(4, "0")}`,
  audit: () => "/api/v1/audit?limit=50",
  audit_deep_page: () => "/api/v1/audit?limit=50&offset=45000",
  audit_filtered: () => `/api/v1/audit?limit=50&user=${pick(SECRETS.users)}`,
  users: () => "/api/v1/users",
  jobs: () => "/api/v1/jobs?limit=40",
  metrics: () => "/api/v1/metrics",
  capacity: () => "/api/v1/server/capacity",
};

function signIn(username, password) {
  const jar = http.cookieJar();
  jar.clear(BASE);
  const response = http.post(`${BASE}/api/v1/session`, JSON.stringify({ username, password }), {
    headers: { ...headers, "Content-Type": "application/json" },
    tags: { endpoint: "signin" },
  });
  note(response);
  signinTime.add(response.timings.duration);
  if (__ENV.DEBUG && response.status !== 201) console.log("signin", response.status, String(response.body).slice(0, 200));
  return response.status === 201;
}

function note(response) {
  statuses.add(1, { status: String(response.status) });
  if (response.status === 429) limited.add(1);
  if (response.status >= 500 || response.status === 0) serverErrors.add(1, { status: String(response.status) });
}

function read(name) {
  const response = http.get(`${BASE}${READS[name]()}`, { headers, tags: { endpoint: name } });
  note(response);
  check(response, { "200 or 429": (r) => r.status === 200 || r.status === 429 });
  if (__ENV.DEBUG && response.status !== 200) console.log(name, response.status, String(response.body).slice(0, 160));
  return response;
}

// ------------------------------------------------------------------ modes

const BASELINE = ["signin", ...Object.keys(READS)];

function scenarios() {
  if (MODE === "baseline") {
    return Object.fromEntries(
      BASELINE.map((name, index) => [
        name,
        {
          executor: "per-vu-iterations",
          vus: 1,
          iterations: name === "signin" ? 10 : ITERATIONS,
          startTime: `${index * 12}s`,
          maxDuration: "11s",
          exec: "baseline",
          env: { ENDPOINT: name },
          tags: { endpoint: name },
        },
      ]),
    );
  }
  if (MODE === "spike") {
    return {
      spike: {
        executor: "ramping-vus",
        startVUs: 1,
        stages: [
          { duration: "30s", target: 1 }, // before: one person
          { duration: "5s", target: 50 }, // the jump
          { duration: `${SPIKE_SECONDS}s`, target: 50 },
          { duration: "5s", target: 1 },
          { duration: `${RECOVERY_SECONDS}s`, target: 1 }, // after: does it come back by itself?
        ],
        gracefulRampDown: "10s",
        exec: "spike",
      },
    };
  }
  return {
    soak: { executor: "constant-vus", vus: SOAK_VUS, duration: `${SOAK_MINUTES}m`, exec: "soak" },
  };
}

export const options = {
  scenarios: scenarios(),
  noCookiesReset: true, // a person stays signed in from one iteration to the next
  summaryTrendStats: ["avg", "med", "p(95)", "p(99)", "max"],
  thresholds: Object.fromEntries(
    [...BASELINE].flatMap((name) => [
      [`http_req_duration{endpoint:${name}}`, ["p(99)>=0"]],
      [`http_reqs{endpoint:${name}}`, ["count>=0"]],
      [`http_req_failed{endpoint:${name}}`, ["rate>=0"]],
      [`data_received{endpoint:${name}}`, ["count>=0"]],
    ]),
  ),
};

export function baseline() {
  const name = __ENV.ENDPOINT;
  if (name === "signin") {
    check(null, { "signed in": () => signIn(SECRETS.username, SECRETS.password) });
    return;
  }
  if (exec.vu.iterationInScenario === 0) signIn(SECRETS.username, SECRETS.password);
  read(name);
}

// each virtual user is one person (one of 50 accounts) who signs in once, then reads as fast as answered
export function spike() {
  if (exec.vu.iterationInScenario % 200 === 0) {
    if (!signIn(SECRETS.users[(__VU - 1) % SECRETS.users.length], SECRETS.user_password)) {
      sleep(1);
      return;
    }
  }
  const response = read(pick(["session", "instances_list", "instance_detail", "projects_list", "audit", "jobs"]));
  if (response.status === 429) sleep(Number(response.headers["Retry-After"] || 1));
}

// people at work: open the dashboard, an instance, a project, the audit log; sign in again now and then
export function soak() {
  const user = SECRETS.users[(__VU - 1) % SECRETS.users.length];
  if (exec.vu.iterationInScenario % 50 === 0) signIn(user, SECRETS.user_password);
  for (const name of ["session", "instances_list", "instance_detail", "projects_list", "project_detail", "audit", "jobs"]) {
    const response = read(name);
    if (response.status === 429) sleep(Number(response.headers["Retry-After"] || 1));
    sleep(0.5 + Math.random());
  }
}

export function handleSummary(data) {
  const rows = {};
  for (const name of BASELINE) {
    const duration = data.metrics[`http_req_duration{endpoint:${name}}`];
    const count = data.metrics[`http_reqs{endpoint:${name}}`];
    const failed = data.metrics[`http_req_failed{endpoint:${name}}`];
    const received = data.metrics[`data_received{endpoint:${name}}`];
    if (!duration || !count || !count.values.count) continue;
    rows[name] = {
      requests: count.values.count,
      p50_ms: +duration.values.med.toFixed(1),
      p95_ms: +duration.values["p(95)"].toFixed(1),
      p99_ms: +duration.values["p(99)"].toFixed(1),
      max_ms: +duration.values.max.toFixed(1),
      failed_percent: +(100 * failed.values.rate).toFixed(2),
      kb_per_request: +(received.values.count / count.values.count / 1024).toFixed(1),
    };
  }
  const all = data.metrics.http_req_duration.values;
  const result = {
    mode: MODE,
    endpoints: rows,
    all: {
      requests: data.metrics.http_reqs.values.count,
      rps: +data.metrics.http_reqs.values.rate.toFixed(1),
      p50_ms: +all.med.toFixed(1),
      p95_ms: +all["p(95)"].toFixed(1),
      p99_ms: +all["p(99)"].toFixed(1),
      max_ms: +all.max.toFixed(1),
      rate_limited: data.metrics.rate_limited ? data.metrics.rate_limited.values.count : 0,
      server_errors: data.metrics.server_errors ? data.metrics.server_errors.values.count : 0,
      signin_p95_ms: data.metrics.signin_ms ? +data.metrics.signin_ms.values["p(95)"].toFixed(1) : null,
    },
  };
  const text = JSON.stringify(result, null, 1);
  return { [__ENV.OUT || "/dev/stdout"]: text, stdout: text + "\n" };
}
