import type { WorkloadKind } from "@/lib/manifest/model";

/**
 * The shape of an app, for choosing how its dashboard is drawn (spec 44 viz
 * addendum). `classifyPrimitive` in lib/primitive.tsx stays the nav rule;
 * this is finer, and only decides layout: a single web service, a service
 * with a database, a web + worker pair, microservices, an app with an agent,
 * functions, and so on get different dashboards.
 */
export type TopologyKind =
  | "service"
  | "service-data"
  | "service-worker"
  | "microservices"
  | "service-agent"
  | "agent"
  | "functions"
  | "scheduled"
  | "task"
  | "workflow"
  | "mixed";

export const TOPOLOGY_META: Record<TopologyKind, { label: string; blurb: string }> = {
  service: { label: "Single service", blurb: "One long-running service" },
  "service-data": {
    label: "Service and data",
    blurb: "A service with its database, cache or bucket",
  },
  "service-worker": { label: "Web and worker", blurb: "A service plus background workers" },
  microservices: { label: "Microservices", blurb: "Several services calling each other" },
  "service-agent": { label: "App and agent", blurb: "Services with an agent working alongside" },
  agent: { label: "Agent", blurb: "One or more agents" },
  functions: { label: "Functions", blurb: "Event-triggered functions" },
  scheduled: { label: "Scheduled", blurb: "Jobs on a schedule" },
  task: { label: "Task", blurb: "One-off runs" },
  workflow: { label: "Workflow", blurb: "A multi-step pipeline" },
  mixed: { label: "Mixed", blurb: "Several kinds of workload" },
};

export interface TopologyInput {
  workloads: { kind: WorkloadKind; name: string }[];
  /** Managed service kinds attached to the app (postgres, redis, queue, s3...). */
  managedServices?: string[];
}

const WORKER_NAME = /(worker|consumer|processor|queue|celery|sidekiq|jobs?)\b/i;
const DATA_NAME = /(postgres|mysql|maria|redis|mongo|valkey|db|database)/i;
const QUEUE_SERVICES = new Set(["queue", "sqs", "rabbitmq", "kafka"]);

export function classifyTopology({ workloads, managedServices = [] }: TopologyInput): TopologyKind {
  if (workloads.length === 0) return "service";

  const agents = workloads.filter((w) => w.kind === "agent");
  // A self-hosted database runs as a statefulset; it is data, not a service.
  const selfHostedData = workloads.filter(
    (w) => w.kind === "statefulset" && DATA_NAME.test(w.name)
  );
  const longRunning = workloads.filter(
    (w) => w.kind === "deployment" || (w.kind === "statefulset" && !DATA_NAME.test(w.name))
  );
  const hasData =
    selfHostedData.length > 0 ||
    managedServices.some((s) => !QUEUE_SERVICES.has(s) && s !== "email");
  const hasQueue = managedServices.some((s) => QUEUE_SERVICES.has(s));

  if (agents.length > 0) {
    if (longRunning.length > 0) return "service-agent";
    return agents.length === workloads.length ? "agent" : "mixed";
  }

  const only = (k: WorkloadKind) =>
    workloads.every((w) => w.kind === k || selfHostedData.includes(w));
  if (only("function")) return "functions";
  if (only("workflow")) return "workflow";
  if (only("cronjob")) return "scheduled";
  if (workloads.every((w) => w.kind === "job" || w.kind === "task")) return "task";

  if (longRunning.length !== workloads.length - selfHostedData.length) return "mixed";

  if (longRunning.length === 1) return hasData ? "service-data" : "service";
  const workers = longRunning.filter((w) => WORKER_NAME.test(w.name)).length;
  if (longRunning.length - workers === 1) return "service-worker";
  // Two services sharing a queue and neither named for it: still web + worker.
  if (workers === 0 && hasQueue && longRunning.length === 2) return "service-worker";
  return "microservices";
}
