/**
 * The app's real topology as the snapshot AppView draws (spec 44 viz
 * addendum). Built from the same nodes and edges `appTopology` synthesises
 * for the app, its workloads and its managed services, so the overview's
 * picture and the old Topology tab never disagree about what exists.
 *
 * Only what the platform knows goes in: each node's health from its
 * provisioning state, and which nodes connect. Live traffic (requests per
 * second, latency, error rate, utilisation, ready replicas) is not in the
 * app queries yet, so it is zero or absent rather than invented, and nothing
 * moves that is not state.
 */

import type { TopologyEdge, TopologyNode, TopologyNodeStatus } from "@/components/topology/types";
import type { AppEdge, AppNode, AppNodeRole, AppSnapshot } from "@/components/viz/core/app-model";
import type { Health } from "@/components/viz/core/semantics";
import type { WorkloadKind } from "@/lib/manifest/model";
import { classifyTopology } from "@/lib/topology";

const HEALTH: Record<TopologyNodeStatus, Health> = {
  running: "ok",
  provisioning: "degraded",
  failed: "failing",
  unknown: "idle",
};

const WORKER_NAME = /(worker|consumer|processor|queue|celery|sidekiq|jobs?)\b/i;
const DATA_NAME = /(postgres|mysql|maria|redis|mongo|valkey|db|database)/i;
const QUEUE_KINDS = new Set(["queue", "sqs", "rabbitmq", "kafka"]);

function workloadRole(kind: string, name: string): AppNodeRole {
  switch (kind) {
    case "function":
      return "function";
    case "agent":
      return "agent";
    case "cronjob":
      return "schedule";
    case "job":
    case "task":
    case "workflow":
      return "worker";
    case "statefulset":
      return DATA_NAME.test(name) ? "data" : "service";
    default:
      return WORKER_NAME.test(name) ? "worker" : "service";
  }
}

/** A workload node carries its kind as the sublabel. */
function workloadKind(node: TopologyNode): string {
  return (node.sublabel || "deployment").toLowerCase();
}

/** The managed service's kind, from the `kind · variant` sublabel. */
function serviceKind(node: TopologyNode): string {
  return (node.sublabel ?? "").split(" · ")[0] || node.label;
}

export function topologySnapshot(
  app: { slug: string; name: string },
  nodes: TopologyNode[],
  edges: TopologyEdge[],
  now: number = Date.now()
): AppSnapshot {
  const workloads = nodes.filter((n) => n.type === "workload");
  const services = nodes.filter((n) => n.type === "managed-service" || n.type === "cache");
  const topology = classifyTopology({
    workloads: workloads.map((w) => ({ kind: workloadKind(w) as WorkloadKind, name: w.label })),
    managedServices: services.map(serviceKind),
  });

  const out: AppNode[] = [];
  for (const n of nodes) {
    // A k8s Service only routes ingress to its workload; the picture draws
    // that as one hop, so the Service node folds into the edge.
    if (n.type === "service") continue;
    const kind =
      n.type === "workload"
        ? workloadKind(n)
        : n.type === "ingress"
          ? "ingress"
          : n.type === "external"
            ? "external"
            : serviceKind(n);
    const role: AppNodeRole =
      n.type === "ingress"
        ? "ingress"
        : n.type === "external"
          ? "external"
          : n.type === "workload"
            ? workloadRole(kind, n.label)
            : QUEUE_KINDS.has(kind)
              ? "queue"
              : "data";
    out.push({ id: n.id, name: n.label, role, kind, health: HEALTH[n.status], load: 0 });
  }

  // Resolve Service hops: ingress → svc-x → wl-x becomes ingress → wl-x.
  const kept = new Set(out.map((n) => n.id));
  const via = new Map<string, string[]>();
  for (const e of edges) {
    if (!kept.has(e.source)) via.set(e.source, [...(via.get(e.source) ?? []), e.target]);
  }
  const seen = new Set<string>();
  const appEdges: AppEdge[] = [];
  const add = (from: string, to: string) => {
    const key = `${from}>${to}`;
    if (seen.has(key) || !kept.has(to)) return;
    seen.add(key);
    appEdges.push({ from, to, rps: 0, errorRate: 0 });
  };
  for (const e of edges) {
    if (!kept.has(e.source)) continue;
    if (kept.has(e.target)) add(e.source, e.target);
    else for (const t of via.get(e.target) ?? []) add(e.source, t);
  }

  return { now, app, topology, nodes: out, edges: appEdges, events: [] };
}
