import http from "k6/http";
import { check, sleep } from "k6";

const baseUrl = __ENV.CAP_BASE_URL || "http://127.0.0.1:8000";
const user = __ENV.CAP_USER || "administrator";
const proxySecret = __ENV.CAP_PROXY_SECRET || "change-me-proxy-secret";
const levels = [1, 10, 50, 100, 500, 1000];

const scenarios = {};
let startSeconds = 0;
for (const level of levels) {
  scenarios[`crud_${level}`] = {
    executor: "constant-vus",
    vus: level,
    duration: __ENV.CAP_DURATION || "10s",
    startTime: `${startSeconds}s`,
    gracefulStop: "5s",
    tags: { concurrency: String(level) },
  };
  startSeconds += Number.parseInt(__ENV.CAP_SCENARIO_GAP || "20", 10);
}

export const options = {
  scenarios,
  thresholds: {
    http_req_failed: ["rate<0.01"],
    http_req_duration: ["p(95)<500", "p(99)<1000"],
  },
};

const params = {
  headers: {
    "Content-Type": "application/json",
    "X-CAP-User": user,
    "X-CAP-Proxy-Secret": proxySecret,
  },
};

export default function () {
  const suffix = `${__VU}-${__ITER}-${Date.now()}`;
  const created = http.post(
    `${baseUrl}/assets`,
    JSON.stringify({
      asset_type: "HOST",
      name: `k6-${suffix}`,
      value: `k6-${suffix}.example.test`,
      environment: "phase22",
      tags: ["benchmark"],
    }),
    params,
  );
  check(created, { "POST asset is 201": (response) => response.status === 201 });
  if (created.status !== 201) {
    sleep(0.01);
    return;
  }
  const assetId = created.json("id");
  const read = http.get(`${baseUrl}/assets/${assetId}`, params);
  check(read, { "GET asset is 200": (response) => response.status === 200 });
  const updated = http.put(
    `${baseUrl}/assets/${assetId}`,
    JSON.stringify({ risk: "LOW" }),
    params,
  );
  check(updated, { "PUT asset is 200": (response) => response.status === 200 });
  const removed = http.del(`${baseUrl}/assets/${assetId}`, null, params);
  check(removed, { "DELETE asset is 204": (response) => response.status === 204 });
  sleep(0.01);
}
