/**
 * Apps › Deployments (spec 44 §4.4, §5.1, §5.5): the list declaration, the
 * query variables it sends, and the pure run view of one deployment.
 *
 * The list: views All · Mine · Waiting approval · Failed · Today, cursor
 * paged, live. `astroliftDeploymentsPage` takes `appSlug`,
 * `environmentName`, `statuses`, `search`, `limit` and `after`, so app,
 * environment, status and search go to the server. Trigger, since and Mine
 * (who triggered it) have no argument yet: `narrowDeployments` keeps the
 * matching rows of a wider page (`NARROW_LIMIT`), and the views that lean
 * on it say so in their note. When the query grows those arguments the
 * hook sends them and this step goes away; the screen does not change.
 */
import type { ListDefinition } from "@/components/list/use-list-state";
import { standardViews } from "@/components/list/use-list-state";
import type { TimelineStep } from "@/components/run/Timeline";
import type { LogLine } from "@/components/run/LogView";
import type { PanelFailure } from "@/components/panel/Panel";
import type {
  AstroliftDeployment,
  AstroliftDeploymentLogEntry,
  DeploymentStatus,
  TriggerKind,
} from "@/graphql/lifecycle/lifecycle.types";

import { SINCE_WITH_TODAY, sinceIso } from "./apps-area";
import { IN_FLIGHT } from "./deployments-format";
import { spanMs } from "./run-support";

const STATUSES: DeploymentStatus[] = [
  "pending_approval",
  "pending",
  "deploying",
  "redeploying",
  "running",
  "failed",
  "rolled_back",
  "superseded",
];

const TRIGGERS: TriggerKind[] = ["push", "manual", "ci", "scheduled", "rollback", "promotion"];

export const DEPLOYMENTS_LIST: ListDefinition = {
  id: "apps.deployments",
  fields: [
    { key: "app", label: "App" },
    { key: "environment", label: "Environment" },
    {
      key: "status",
      label: "Status",
      options: STATUSES.map((s) => ({ value: s, label: s.replace(/_/g, " ") })),
    },
    { key: "trigger", label: "Trigger", options: TRIGGERS.map((t) => ({ value: t, label: t })) },
    { key: "since", label: "Since", options: SINCE_WITH_TODAY },
  ],
  // The server matches app, environment, branch, image tag and commit.
  searchPlaceholder: "Search apps, branches, tags, commits…",
  defaultSort: [{ key: "started", dir: "desc" }],
  views: standardViews(
    { triggeredBy: "me" },
    [
      { key: "waiting", label: "Waiting approval", filters: { status: "pending_approval" } },
      { key: "failed", label: "Failed", filters: { status: "failed" } },
      {
        key: "today",
        label: "Today",
        filters: { since: "today" },
        note: "Today keeps the deployments started since midnight among the newest 100, until the deployments query takes a start time.",
      },
    ],
    {
      mineNote:
        "Mine keeps the deployments you triggered among the newest 100, until the deployments query takes who triggered them.",
    }
  ),
  paging: "cursor",
  pageSizes: [25, 50, 100],
};

/** The page walked when trigger, since or Mine narrows it client-side. */
export const NARROW_LIMIT = 100;

/** True when a filter the server cannot answer is on. */
export function narrows(filters: Record<string, string>): boolean {
  return Boolean(filters.trigger || filters.since || filters.triggeredBy);
}

export interface DeploymentsVariables {
  appSlug: string | null;
  environmentName: string | null;
  statuses: string[] | null;
  search: string | null;
  limit: number;
  after: string | null;
}

/** What `astroliftDeploymentsPage` is sent for a list state. */
export function deploymentsVariables(
  filters: Record<string, string>,
  { q, pageSize, after }: { q: string; pageSize: number; after: string | null }
): DeploymentsVariables {
  return {
    appSlug: filters.app || null,
    environmentName: filters.environment || null,
    statuses: filters.status ? [filters.status] : null,
    search: q.trim() || null,
    limit: narrows(filters) ? Math.max(pageSize, NARROW_LIMIT) : pageSize,
    after,
  };
}

/** Trigger, since and Mine over the rows in hand. */
export function narrowDeployments(
  rows: AstroliftDeployment[],
  filters: Record<string, string>,
  now: number
): AstroliftDeployment[] {
  const since = sinceIso(filters.since, now);
  const sinceTs = since ? Date.parse(since) : null;
  return rows.filter((d) => {
    if (filters.trigger && d.triggerKind !== filters.trigger) return false;
    if (filters.triggeredBy === "me" && !d.triggeredByMe) return false;
    if (sinceTs !== null && Date.parse(d.startedAt ?? d.createdAt) < sinceTs) return false;
    return true;
  });
}

// ---------------------------------------------------------------------------
// One deployment as a run (spec 44 §5.5)
// ---------------------------------------------------------------------------

/** The short name of a deploy: its commit, else its id, eight characters. */
export function deploySha(d: Pick<AstroliftDeployment, "commitSha" | "id">): string {
  return (d.commitSha || d.id).slice(0, 8);
}

const ROLLOUT = new Set(["deploying", "redeploying"]);
const ENDED_BADLY = new Set(["failed", "rolled_back"]);
// A failure whose message names a probe or health check failed after the
// manifests applied: the rollout finished, the health gate did not.
const HEALTH_FAILURE = /readiness|liveness|startup probe|probe failed|health|crashloop/i;

function lastFailure(log: AstroliftDeploymentLogEntry[]) {
  return log.findLast((e) => ENDED_BADLY.has(e.status));
}

/**
 * The deploy's phases on the Timeline: build and push (the image this
 * deploy was handed, built outside Astrolift), approval when the
 * environment requires one, rollout (the manifests applying) and health
 * (the replicas becoming ready). Rollout and health read the lifecycle log,
 * which records status transitions; the image phases read the deployment.
 */
export function deploymentSteps(
  d: AstroliftDeployment,
  log: AstroliftDeploymentLogEntry[],
  now: number
): TimelineStep[] {
  const hasImage = Boolean(d.imageTag || d.imageDigest);
  const buildFailed = isBuildFailure(d, log);
  const steps: TimelineStep[] = [
    {
      id: "build",
      name: "build",
      state: buildFailed ? "failed" : hasImage ? "ok" : "pending",
      detail: buildFailed
        ? firstLine(d.buildError)
        : d.ciProvider
          ? `${d.ciProvider}${d.branch ? ` · ${d.branch}` : ""}`
          : d.branch || undefined,
    },
    {
      id: "push",
      name: "push",
      state: buildFailed ? "skipped" : hasImage ? "ok" : "pending",
      detail: d.imageDigest || d.imageTag || undefined,
    },
  ];

  const rolloutAt = log.findIndex((e) => ROLLOUT.has(e.status));
  const rollout = rolloutAt >= 0 ? log[rolloutAt] : null;
  const afterRollout = rolloutAt >= 0 ? log[rolloutAt + 1] : undefined;
  const failed = lastFailure(log);
  const terminal = !IN_FLIGHT.has(d.status) && d.status !== "running";
  // A build that failed never reached the cluster: every later phase is skipped.
  if (buildFailed) {
    for (const id of ["rollout", "health"]) steps.push({ id, name: id, state: "skipped" });
    return steps;
  }

  if (d.approvalsRequired > 0) {
    const waiting = d.status === "pending_approval";
    const refused = !waiting && !rollout && terminal;
    steps.push({
      id: "approval",
      name: "approval",
      state: waiting ? "running" : refused ? "failed" : "ok",
      detail: `${d.approvalsReceived}/${d.approvalsRequired} approvals`,
    });
  }

  const liveRollout = ROLLOUT.has(d.status) && !afterRollout;
  const healthFailed =
    Boolean(failed && afterRollout === failed) && HEALTH_FAILURE.test(failed?.message ?? "");

  let rolloutState: TimelineStep["state"];
  const wentLive = d.status === "running" || d.status === "superseded";
  if (liveRollout) rolloutState = "running";
  else if (!rollout) rolloutState = wentLive ? "ok" : terminal ? "skipped" : "pending";
  else if (afterRollout && ENDED_BADLY.has(afterRollout.status) && !healthFailed)
    rolloutState = "failed";
  else rolloutState = afterRollout ? "ok" : "pending";

  steps.push({
    id: "rollout",
    name: "rollout",
    state: rolloutState,
    durationMs: rollout
      ? spanMs(
          rollout.occurredAt,
          afterRollout ? afterRollout.occurredAt : liveRollout ? now : null
        )
      : null,
    detail:
      rolloutState === "failed"
        ? d.abortedReason || afterRollout?.message || d.statusReason
        : rollout?.message || undefined,
  });

  const ready = log.find((e) => e.status === "running");
  let healthState: TimelineStep["state"];
  if (healthFailed) healthState = "failed";
  else if (ready || d.status === "running" || d.status === "superseded") healthState = "ok";
  else if (rolloutState === "failed" || rolloutState === "skipped") healthState = "skipped";
  else healthState = "pending";

  steps.push({
    id: "health",
    name: "health",
    state: healthState,
    detail: healthFailed ? failed?.message : ready?.message || undefined,
  });
  return steps;
}

/** Why a failed deploy failed, for the first thing on the page (spec 44 §5.2). */
export function deploymentFailure(
  d: AstroliftDeployment,
  log: AstroliftDeploymentLogEntry[]
): PanelFailure | null {
  if (d.status !== "failed") return null;
  if (d.abortedReason) return { title: "Deployment aborted", reason: d.abortedReason };
  // The same order the app's latest-deploy panel reads them in.
  const reason =
    d.statusReason ||
    d.buildError ||
    lastFailure(log)?.message ||
    "No reason was recorded for this failure.";
  return { title: isBuildFailure(d, log) ? "Build failed" : "Deployment failed", reason };
}

/** A build error on a deploy that never reached a rollout: the image never existed. */
function isBuildFailure(d: AstroliftDeployment, log: AstroliftDeploymentLogEntry[]): boolean {
  return d.status === "failed" && Boolean(d.buildError) && !log.some((e) => ROLLOUT.has(e.status));
}

function firstLine(text: string): string {
  return text.split("\n").find((l) => l.trim()) ?? text;
}

const LEVEL: Partial<Record<string, LogLine["level"]>> = {
  failed: "error",
  rolled_back: "warn",
  superseded: "debug",
};

/** The lifecycle log as log lines: the transition and its message, then any detail. */
export function deploymentLogLines(log: AstroliftDeploymentLogEntry[]): LogLine[] {
  return log.flatMap((e) => {
    const status = e.status.replace(/_/g, " ");
    const head: LogLine = {
      ts: e.occurredAt,
      message: e.message ? `${status}: ${e.message}` : status,
      level: LEVEL[e.status] ?? "info",
    };
    const detail =
      e.detail && typeof e.detail === "object" && Object.keys(e.detail).length > 0
        ? [{ ts: e.occurredAt, message: JSON.stringify(e.detail), level: "debug" as const }]
        : [];
    return [head, ...detail];
  });
}
