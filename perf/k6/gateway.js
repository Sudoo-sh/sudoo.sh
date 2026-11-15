// Load test of the gateway: what it adds in front of Odoo. Run it twice, once straight against the stub Odoo
// (perf/stub_odoo.py) and once through the instance's gateway; the difference is the gateway's cost.
// Run by perf/run.sh; see perf/README.md.
//
//   k6 run -e BASE=http://127.0.0.1:19200 -e OUT=/run/gateway.json gateway.js

import http from "k6/http";
import ws from "k6/ws";
import { check } from "k6";
import { Trend } from "k6/metrics";

const BASE = __ENV.BASE || "http://127.0.0.1:19200";
const SECONDS = Number(__ENV.SECONDS || 20);
const UPLOAD_MB = Number(__ENV.UPLOAD_MB || 50);
let upload = null; // made by the upload scenario's virtual users only (every virtual user has its own copy)
const roundTrip = new Trend("ws_round_trip", true);

// name -> [virtual users, function]
const PLAN = [
  ["page", 32, () => ok(http.get(`${BASE}/web`))],
  ["static_asset", 32, () => ok(http.get(`${BASE}/web/assets/web.assets_web.min.js`))],
  ["rpc", 32, () => ok(http.post(`${BASE}/web/dataset/call_kw`, '{"jsonrpc":"2.0","method":"call","params":{}}', { headers: { "Content-Type": "application/json" } }))],
  ["long_polling", 200, () => ok(http.get(`${BASE}/longpolling/poll`, { timeout: "30s" }))],
  ["websocket", 50, websocket],
  ["upload_50mb", 4, uploadOnce],
  ["download_100mb", 4, () => ok(http.get(`${BASE}/web/content/download?mb=100`, { responseType: "none", timeout: "120s" }))],
];

function uploadOnce() {
  upload = upload || "x".repeat(UPLOAD_MB * 1024 * 1024);
  const params = { headers: { "Content-Type": "application/octet-stream" }, timeout: "120s", responseType: "none" };
  ok(http.post(`${BASE}/web/binary/upload`, upload, params));
}

function ok(response) {
  check(response, { "200": (r) => r.status === 200 });
}

function websocket() {
  const url = BASE.replace("http", "ws") + "/websocket";
  ws.connect(url, {}, (socket) => {
    let sent = 0;
    let at = 0;
    socket.on("open", () => {
      at = Date.now();
      socket.send(`{"event_name":"subscribe","data":{"channels":[],"last":${sent}}}`);
    });
    socket.on("message", () => {
      roundTrip.add(Date.now() - at);
      sent += 1;
      if (sent >= 100) {
        socket.close();
        return;
      }
      at = Date.now();
      socket.send(`{"event_name":"update_presence","data":{"n":${sent}}}`);
    });
    socket.setTimeout(() => socket.close(), 15000);
  });
}

export const options = {
  scenarios: Object.fromEntries(
    PLAN.map(([name, vus], index) => [
      name,
      {
        executor: "constant-vus",
        vus,
        duration: `${SECONDS}s`,
        startTime: `${index * (SECONDS + 3)}s`,
        exec: name,
        tags: { endpoint: name },
        gracefulStop: "30s",
      },
    ]),
  ),
  thresholds: Object.fromEntries(
    PLAN.flatMap(([name]) => [
      [`http_req_duration{endpoint:${name}}`, ["p(99)>=0"]],
      [`http_reqs{endpoint:${name}}`, ["count>=0"]],
      [`http_req_failed{endpoint:${name}}`, ["rate>=0"]],
      [`data_received{endpoint:${name}}`, ["count>=0"]],
      [`data_sent{endpoint:${name}}`, ["count>=0"]],
    ]).concat([["ws_round_trip", ["p(99)>=0"]]]),
  ),
  summaryTrendStats: ["avg", "med", "p(95)", "p(99)", "max"],
};

export const page = PLAN[0][2];
export const static_asset = PLAN[1][2];
export const rpc = PLAN[2][2];
export const long_polling = PLAN[3][2];
export { websocket };
export const upload_50mb = PLAN[5][2];
export const download_100mb = PLAN[6][2];

export function handleSummary(data) {
  const rows = {};
  for (const [name] of PLAN) {
    const count = data.metrics[`http_reqs{endpoint:${name}}`];
    if (name === "websocket") {
      const trip = data.metrics.ws_round_trip;
      if (trip) {
        rows[name] = {
          p50_ms: +trip.values.med.toFixed(2),
          p95_ms: +trip.values["p(95)"].toFixed(2),
          p99_ms: +trip.values["p(99)"].toFixed(2),
        };
      }
      continue;
    }
    const duration = data.metrics[`http_req_duration{endpoint:${name}}`];
    if (!duration || !count) continue;
    const received = data.metrics[`data_received{endpoint:${name}}`].values.count;
    const sent = data.metrics[`data_sent{endpoint:${name}}`].values.count;
    rows[name] = {
      requests: count.values.count,
      rps: +(count.values.count / SECONDS).toFixed(1),
      p50_ms: +duration.values.med.toFixed(2),
      p95_ms: +duration.values["p(95)"].toFixed(2),
      p99_ms: +duration.values["p(99)"].toFixed(2),
      failed_percent: +(100 * data.metrics[`http_req_failed{endpoint:${name}}`].values.rate).toFixed(2),
      mb_per_second: +((received + sent) / SECONDS / 1048576).toFixed(1),
    };
  }
  const text = JSON.stringify(rows, null, 1);
  return { [__ENV.OUT || "/dev/stdout"]: text, stdout: text + "\n" };
}
