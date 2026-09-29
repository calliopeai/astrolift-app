import type { LogLine } from "./LogView";
import type { TimelineStep } from "./Timeline";

/** Fixtures for the run view's stories and tests (spec 44 §5.5, §8). */

const BASE = Date.UTC(2026, 8, 28, 12, 1, 4);

export const LONG_SHA = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08";
export const LONG_ARN =
  "arn:aws:iam::123456789012:role/astrolift/clusters/conflict-astrolift/workloads/agents/support-bot/" +
  "service-accounts/support-bot-runtime-executor/permission-boundaries/astrolift-agent-runtime-boundary";
export const LONG_URL =
  "https://registry.example.com/v2/example/checkout/manifests/sha256:9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08?platform=linux%2Farm64&attempt=3";

export const RUNNING_STEPS: TimelineStep[] = [
  { id: "fetch", name: "fetch", state: "ok", durationMs: 12_000 },
  { id: "transform", name: "transform", state: "ok", durationMs: 100_000, detail: "1 retry" },
  { id: "publish", name: "publish", state: "running", durationMs: 130_000 },
  { id: "notify", name: "notify", state: "pending" },
];

export const FAILED_STEPS: TimelineStep[] = [
  { id: "build", name: "build image", state: "ok", durationMs: 84_000 },
  {
    id: "pull",
    name: "pull image",
    state: "failed",
    durationMs: 9_000,
    detail: "Image pull denied: authentication required",
  },
  { id: "rollout", name: "rollout", state: "skipped" },
  { id: "verify", name: "verify health", state: "skipped" },
];

export const LONG_STEPS: TimelineStep[] = [
  { id: "a", name: `resolve ${LONG_SHA}`, state: "ok", durationMs: 3_723_000 },
  { id: "b", name: LONG_ARN, state: "failed", durationMs: 1_000, detail: LONG_URL },
];

const MESSAGES = [
  "pulling image ghcr.io/example/checkout:1112015d",
  "started container checkout-web",
  'level=info msg="listening on :8080"',
  "WARN slow response from billing-api (1.8s)",
  "debug: cache warm 412 keys",
  "GET /healthz 200 2ms",
];

export function makeLines(count: number, start = 0): LogLine[] {
  return Array.from({ length: count }, (_, i) => ({
    ts: BASE + (start + i) * 1000,
    message: `${MESSAGES[(start + i) % MESSAGES.length]} (#${start + i + 1})`,
  }));
}

export const RUN_LINES: LogLine[] = makeLines(40);

export const FAILED_LINES: LogLine[] = [
  ...makeLines(12),
  {
    ts: BASE + 13_000,
    message: "ERROR Back-off pulling image: pull access denied, authentication required",
  },
  { ts: BASE + 14_000, message: "Error: ErrImagePull", level: "error" },
];

export const LONG_LINES: LogLine[] = [
  { ts: BASE, message: `commit ${LONG_SHA}` },
  { ts: BASE + 1000, message: `assume-role ${LONG_ARN}` },
  { ts: BASE + 2000, message: `GET ${LONG_URL}` },
  { ts: BASE + 3000, message: "x".repeat(400) },
];
