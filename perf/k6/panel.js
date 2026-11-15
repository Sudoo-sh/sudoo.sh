// Load test of the panel's hot paths, through the web service (Caddy) as browsers and API clients reach it.
// Each scenario runs on its own, one after the other, so every endpoint gets its own p50/p95/p99 and requests per
// second. Run by perf/run.sh; see perf/README.md.
//
//   k6 run -e BASE=http://127.0.0.1:19100 -e SECRETS=/run/secrets.json -e OUT=/run/panel.json panel.js

import crypto from "k6/crypto";
import http from "k6/http";
import { check } from "k6";

const BASE = __ENV.BASE || "http://127.0.0.1:19100";
const SECRETS = JSON.parse(open(__ENV.SECRETS));
const SECONDS = Number(__ENV.SECONDS || 20);
const VUS = Number(__ENV.VUS || 16);

// name -> [virtual users, function]
const PLAN = [
  ["signin", 8, signIn],
  ["session", VUS, () => get("/api/v1/session")],
  ["instances_list", VUS, () => get("/api/v1/instances?limit=500")],
  ["instance_detail", VUS, () => get(`/api/v1/instances/${pick(SECRETS.instances)}`)],
  ["metrics", VUS, () => get("/api/v1/metrics")],
  ["metrics_history", VUS, () => get("/api/v1/metrics/history?minutes=60")],
  ["instance_metrics", VUS, () => get(`/api/v1/instances/${pick(SECRETS.instances)}/metrics?minutes=60`)],
  ["server", VUS, () => get("/api/v1/server")],
  ["jobs", VUS, () => get("/api/v1/jobs?limit=40")],
  ["audit", VUS, () => get("/api/v1/audit?limit=100")],
  ["api_token_instances", VUS, () => signed("GET", "/api/v1/instances?limit=100")],
  ["api_token_jobs", VUS, () => signed("GET", "/api/v1/jobs?limit=30")],
  ["webhook", VUS, webhook],
];

export const options = {
  scenarios: Object.fromEntries(
    PLAN.map(([name, vus], index) => [
      name,
      {
        executor: "constant-vus",
        vus,
        duration: `${SECONDS}s`,
        startTime: `${index * (SECONDS + 2)}s`,
        exec: name,
        tags: { endpoint: name },
      },
    ]),
  ),
  thresholds: Object.fromEntries(
    PLAN.flatMap(([name]) => [
      [`http_req_duration{endpoint:${name}}`, ["p(99)>=0"]],
      [`http_reqs{endpoint:${name}}`, ["count>=0"]],
      [`http_req_failed{endpoint:${name}}`, ["rate>=0"]],
      [`data_received{endpoint:${name}}`, ["count>=0"]],
    ]),
  ),
  summaryTrendStats: ["avg", "med", "p(95)", "p(99)", "max"],
  discardResponseBodies: false,
};

const pick = (list) => list[Math.floor(Math.random() * list.length)];
const headers = { Accept: "application/json", "Accept-Encoding": "gzip, br, zstd" };

function signIn() {
  const response = http.post(
    `${BASE}/api/v1/session`,
    JSON.stringify({ username: SECRETS.username, password: SECRETS.password }),
    { headers: { ...headers, "Content-Type": "application/json" }, jar: new http.CookieJar() },
  );
  check(response, { "signed in": (r) => r.status === 201 });
}

export function setup() {
  const response = http.post(
    `${BASE}/api/v1/session`,
    JSON.stringify({ username: SECRETS.username, password: SECRETS.password }),
    { headers: { ...headers, "Content-Type": "application/json" } },
  );
  if (response.status !== 201) throw new Error(`sign-in failed: ${response.status} ${response.body}`);
  return { cookie: response.cookies.sudoo_session[0].value };
}

let __cookie = "";

function get(path) {
  const response = http.get(`${BASE}${path}`, { headers: { ...headers, Cookie: `sudoo_session=${__cookie}` } });
  check(response, { "200": (r) => r.status === 200 });
}

function nonce() {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
  let text = "";
  for (let i = 0; i < 24; i++) text += alphabet[Math.floor(Math.random() * alphabet.length)];
  return text;
}

function signed(method, path, body = "") {
  const timestamp = String(Math.floor(Date.now() / 1000));
  const n = nonce();
  const message = [timestamp, n, method, path, crypto.sha256(body, "hex")].join("\n");
  const signature = "sha256=" + crypto.hmac("sha256", SECRETS.signing_secret, message, "hex");
  const response = http.request(method, `${BASE}${path}`, body || null, {
    headers: {
      ...headers,
      Authorization: `Bearer ${SECRETS.token}`,
      "X-Sudoo-Timestamp": timestamp,
      "X-Sudoo-Nonce": n,
      "X-Sudoo-Signature": signature,
    },
  });
  check(response, { "200": (r) => r.status === 200 });
}

const PUSH = JSON.stringify({
  ref: "refs/heads/main",
  repository: { full_name: "example/not-followed" },
  commits: [{ id: "0123456789abcdef", message: "A change" }],
});

function webhook() {
  const signature = "sha256=" + crypto.hmac("sha256", SECRETS.webhook_secret, PUSH, "hex");
  const response = http.post(`${BASE}/hooks/github`, PUSH, {
    headers: { "Content-Type": "application/json", "X-GitHub-Event": "push", "X-Hub-Signature-256": signature },
  });
  check(response, { "200": (r) => r.status === 200 });
}

// one exported function per scenario (k6 needs them by name)
const withSession = (fn) => (data) => {
  __cookie = data.cookie;
  fn();
};
export const signin = signIn;
export const session = withSession(PLAN[1][2]);
export const instances_list = withSession(PLAN[2][2]);
export const instance_detail = withSession(PLAN[3][2]);
export const metrics = withSession(PLAN[4][2]);
export const metrics_history = withSession(PLAN[5][2]);
export const instance_metrics = withSession(PLAN[6][2]);
export const server = withSession(PLAN[7][2]);
export const jobs = withSession(PLAN[8][2]);
export const audit = withSession(PLAN[9][2]);
export const api_token_instances = PLAN[10][2];
export const api_token_jobs = PLAN[11][2];
export { webhook };

export function handleSummary(data) {
  const rows = {};
  for (const [name] of PLAN) {
    const duration = data.metrics[`http_req_duration{endpoint:${name}}`];
    const count = data.metrics[`http_reqs{endpoint:${name}}`];
    const failed = data.metrics[`http_req_failed{endpoint:${name}}`];
    const received = data.metrics[`data_received{endpoint:${name}}`];
    if (!duration || !count) continue;
    rows[name] = {
      requests: count.values.count,
      rps: +(count.values.count / SECONDS).toFixed(1),
      p50_ms: +duration.values.med.toFixed(2),
      p95_ms: +duration.values["p(95)"].toFixed(2),
      p99_ms: +duration.values["p(99)"].toFixed(2),
      failed_percent: +(100 * failed.values.rate).toFixed(2),
      kb_per_request: +(received.values.count / count.values.count / 1024).toFixed(1),
    };
  }
  const text = JSON.stringify(rows, null, 1);
  return { [__ENV.OUT || "/dev/stdout"]: text, stdout: text + "\n" };
}
