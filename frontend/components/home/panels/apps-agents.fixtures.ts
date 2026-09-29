/** Fixtures for the Apps and Agents panels' stories and tests. */

import { LONG_ARN, LONG_SHA, LONG_URL, QUERY_ERROR } from "@/components/panel/fixtures";

import { type RunDay, runningSnapshot } from "./apps-agents-model";
import type { FailedRunItem } from "./FailedRunsPanel";
import type { FailingItem } from "./FailingPanel";
import type { MyAgentItem } from "./MyAgentsPanel";
import type { MyAppItem } from "./MyAppsPanel";
import type { RecentDeployItem } from "./RecentDeploymentsPanel";
import type { SignalSeries } from "./TrafficErrorsPanel";
import type { WaitingItem } from "./WaitingPanel";

export { LONG_ARN, LONG_SHA, LONG_URL, QUERY_ERROR };

export const NOW = Date.UTC(2026, 8, 28, 14, 0, 0);
const ago = (minutes: number) => new Date(NOW - minutes * 60_000).toISOString();

export const READY = { loading: false, error: null, onRetry: () => {} };
export const LOADING = { loading: true, error: null, onRetry: () => {} };
export const FAILED = { loading: false, error: QUERY_ERROR, onRetry: () => {} };

export const WAITING: WaitingItem[] = [
  {
    key: "deploy:d1",
    kind: "deploy",
    title: "checkout-web to production",
    detail: "v2.14.0 · 1/2 approvals",
    at: ago(4),
    href: "/deployments/d1",
    approveId: "d1",
  },
  {
    key: "gate:r1:e1",
    kind: "gate",
    title: "Nightly sync · release-approval",
    detail: "Approvers: ops-lead@example.com",
    at: ago(18),
    href: "/workflows/nightly-sync/runs?run=r1",
  },
  {
    key: "secret:p1",
    kind: "secret",
    title: "billing-api secrets · staging",
    detail: "rotate by Dana Ortiz · 0/1 approvals",
    at: ago(60),
    href: "/approvals/secret/p1",
  },
];

export const WAITING_LONG: WaitingItem[] = [
  {
    ...WAITING[0]!,
    key: "deploy:long",
    title: `checkout-web-${LONG_SHA} to production`,
    detail: `${LONG_ARN} · 0/3 approvals`,
  },
  { ...WAITING[1]!, key: "gate:long", title: LONG_URL, detail: `Approvers: ${LONG_ARN}` },
];

export const FAILING: FailingItem[] = [
  {
    key: "deploy:d9",
    kind: "deploy",
    reason: "Image pull denied: ghcr.io/example/billing-api:v3.1.0 requires authentication",
    subject: "billing-api · production",
    at: ago(14),
    href: "/deployments/d9",
  },
  {
    key: "run:t7",
    kind: "run",
    reason: "Timed out after 900s waiting for the sandbox to report ready",
    subject: "support-bot",
    at: ago(32),
    href: "/agents/runs/t7",
  },
];

export const FAILING_LONG: FailingItem[] = [
  {
    ...FAILING[0]!,
    key: "deploy:long",
    reason: `AccessDenied: not authorized to perform ecr:BatchGetImage on ${LONG_ARN}`,
    subject: `checkout-web-${LONG_SHA} · production`,
  },
  { ...FAILING[1]!, key: "run:long", reason: `fetch ${LONG_URL} failed: ECONNRESET` },
];

export const FAILED_RUNS: FailedRunItem[] = [
  {
    key: "agent:t7",
    kind: "agent",
    subject: "support-bot",
    id: "7e11c2a0-4b1d-4d7e-9a61-2f0c3b8d9e10",
    reason: "Timed out after 900s waiting for the sandbox to report ready",
    at: ago(32),
    href: "/agents/runs/t7",
  },
  {
    key: "workflow:w3",
    kind: "workflow",
    subject: "Nightly sync",
    id: "a2b4c6d8-0000-4000-8000-000000000003",
    reason: "Failed at stage 2 (extract).",
    at: ago(95),
    href: "/workflows/nightly-sync/runs?run=w3",
  },
];

export const FAILED_RUNS_LONG: FailedRunItem[] = [
  { ...FAILED_RUNS[0]!, key: "agent:long", subject: `agent-${LONG_SHA}`, reason: LONG_URL },
  { ...FAILED_RUNS[1]!, key: "workflow:long", subject: LONG_ARN },
];

export const MY_APPS: MyAppItem[] = [
  {
    slug: "checkout-web",
    name: "Checkout web",
    status: "running",
    environment: "production",
    deployedAt: ago(2),
  },
  {
    slug: "billing-api",
    name: "Billing API",
    status: "failed",
    environment: "production",
    deployedAt: ago(14),
  },
  {
    slug: "report-runner",
    name: "Report runner",
    status: "deploying",
    environment: "staging",
    deployedAt: ago(1),
  },
  { slug: "docs-site", name: "Docs site", status: null, environment: null, deployedAt: null },
];

export const MY_APPS_LONG: MyAppItem[] = [
  {
    ...MY_APPS[0]!,
    slug: `checkout-web-${LONG_SHA}`,
    name: LONG_ARN,
    environment: LONG_URL,
  },
];

export const MINE_NOTE =
  "Mine means apps you hold a role on, directly or through their project, team or organization, until apps record who owns them.";

export const RECENT_DEPLOYS: RecentDeployItem[] = [
  {
    id: "1112015d-aaaa",
    short: "1112015d",
    app: "checkout-web",
    environment: "production",
    status: "running",
    at: ago(2),
  },
  {
    id: "9a0c11ee-bbbb",
    short: "9a0c11ee",
    app: "billing-api",
    environment: "production",
    status: "failed",
    at: ago(14),
  },
  {
    id: "5d3e9f10-cccc",
    short: "5d3e9f10",
    app: "checkout-web",
    environment: "staging",
    status: "pending_approval",
    at: ago(40),
  },
];

export const RECENT_DEPLOYS_LONG: RecentDeployItem[] = [
  { ...RECENT_DEPLOYS[0]!, id: LONG_SHA, app: LONG_ARN, environment: LONG_URL },
];

export const MY_AGENTS: MyAgentItem[] = [
  {
    slug: "support-bot",
    name: "Support bot",
    runningCount: 2,
    paused: false,
    lastRunStatus: "running",
    lastRunAt: ago(1),
  },
  {
    slug: "pr-review",
    name: "PR review",
    runningCount: 0,
    paused: false,
    lastRunStatus: "failed",
    lastRunAt: ago(32),
  },
  {
    slug: "cost-watch",
    name: "Cost watch",
    runningCount: 0,
    paused: true,
    lastRunStatus: "completed",
    lastRunAt: ago(600),
  },
  {
    slug: "docs-sync",
    name: "Docs sync",
    runningCount: 0,
    paused: false,
    lastRunStatus: null,
    lastRunAt: null,
  },
];

export const MY_AGENTS_LONG: MyAgentItem[] = [
  { ...MY_AGENTS[0]!, slug: `agent-${LONG_SHA}`, name: LONG_ARN },
];

export const AGENTS_NOTE =
  "Mine means agents on apps you hold a role on, directly or through their project, team or organization, until agents record who owns them.";

const FLEET = [
  {
    id: "a1",
    slug: "support-bot",
    name: "support-bot",
    projectSlug: "support",
    runningCount: 2,
    runPaused: false,
    lastRunStatus: "running",
  },
  {
    id: "a2",
    slug: "pr-review",
    name: "pr-review",
    projectSlug: "platform",
    runningCount: 1,
    runPaused: false,
    lastRunStatus: "completed",
  },
  {
    id: "a3",
    slug: "lead-scout",
    name: "lead-scout",
    projectSlug: "sales",
    runningCount: 1,
    runPaused: false,
    lastRunStatus: "completed",
  },
  {
    id: "a4",
    slug: "cost-watch",
    name: "cost-watch",
    projectSlug: "platform",
    runningCount: 0,
    runPaused: true,
    lastRunStatus: "completed",
  },
];

const RUNNING = [
  { id: "7f3c0000-1111-4000-8000-000000000001", agentSlug: "support-bot", startedAt: ago(1) },
  { id: "7f3c0000-1111-4000-8000-000000000002", agentSlug: "support-bot", startedAt: ago(3) },
  { id: "8a1d0000-2222-4000-8000-000000000003", agentSlug: "pr-review", startedAt: ago(5) },
  { id: "9b2e0000-3333-4000-8000-000000000004", agentSlug: "lead-scout", startedAt: ago(8) },
];

export const RUNNING_SNAPSHOT = runningSnapshot(FLEET, RUNNING, NOW);
export const QUIET_SNAPSHOT = runningSnapshot(
  FLEET.map((a) => ({ ...a, runningCount: 0 })),
  [],
  NOW
);
export const RUNNING_LONG_SNAPSHOT = runningSnapshot(
  [{ ...FLEET[0]!, name: `agent-${LONG_SHA}`, projectSlug: LONG_ARN }],
  [{ ...RUNNING[0]! }],
  NOW
);

const wave = (n: number, base: number, amp: number) =>
  Array.from({ length: n }, (_, i) => base + amp * Math.sin(i / 3) + (i % 5) * amp * 0.1);

export const TRAFFIC: SignalSeries = { values: wave(48, 42, 12), unit: "rps" };
export const ERRORS: SignalSeries = { values: wave(48, 0.012, 0.006), unit: "ratio" };

export const APPS_PICK = MY_APPS.map((a) => ({ slug: a.slug, name: a.name }));

export const RUN_DAYS: RunDay[] = [
  { date: "2026-09-22", runs: 31, failed: 1 },
  { date: "2026-09-23", runs: 28, failed: 0 },
  { date: "2026-09-24", runs: 40, failed: 3 },
  { date: "2026-09-25", runs: 36, failed: 2 },
  { date: "2026-09-26", runs: 12, failed: 0 },
  { date: "2026-09-27", runs: 9, failed: 0 },
  { date: "2026-09-28", runs: 22, failed: 1 },
];

export const QUIET_DAYS: RunDay[] = RUN_DAYS.map((d) => ({ ...d, runs: 0, failed: 0 }));

export const SPEND = {
  perDay: [1830, 1710, 2040, 1990, 820, 760, 1410],
  totalCents: 10560,
  currency: "USD",
};
