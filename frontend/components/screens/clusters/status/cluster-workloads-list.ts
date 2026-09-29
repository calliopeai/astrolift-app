/**
 * Cluster › Health › Workload health (spec 44 §5.1): the embedded list's
 * declaration and the filter, sort and page step the hook runs.
 * `astroliftClusterWorkloadHealth` returns every Deployment in the managed
 * namespaces at once, with no arguments, so namespace, state, search, sort
 * and numbered pages all run in the client (needsBackend: a Page field).
 * The rows are the cluster's, not a person's, so Mine is empty.
 */
import { type ListDefinition, standardViews } from "@/components/list/use-list-state";
import type { SelectRowsSpec } from "@/components/list/select-rows";

import type { WorkloadRow } from "./types";

export type WorkloadState = "ready" | "degraded" | "down";

/** How ready a Deployment is: every replica, some, or none. */
export function workloadState(row: WorkloadRow): WorkloadState {
  const deficit = row.desiredReplicas - row.readyReplicas;
  if (deficit <= 0) return "ready";
  return deficit >= row.desiredReplicas ? "down" : "degraded";
}

export const CLUSTER_WORKLOADS_LIST: ListDefinition = {
  id: "clusters.health.workloads",
  fields: [
    { key: "namespace", label: "Namespace" },
    {
      key: "state",
      label: "State",
      options: [
        { value: "ready", label: "ready" },
        { value: "degraded", label: "degraded" },
        { value: "down", label: "down" },
      ],
    },
    {
      key: "restarts",
      label: "Restarts",
      options: [{ value: "yes", label: "restarted in 24h" }],
    },
  ],
  searchPlaceholder: "Search workloads, namespaces…",
  // Most broken first: the replicas short of desired, then restarts.
  defaultSort: [
    { key: "deficit", dir: "desc" },
    { key: "restarts", dir: "desc" },
  ],
  views: standardViews(
    { owner: "me" },
    [{ key: "unhealthy", label: "Unhealthy", filters: { unhealthy: "yes" } }],
    { mineNote: "Workloads here are the cluster's, not a person's, so Mine is empty." }
  ),
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

export const CLUSTER_WORKLOADS_SELECT: SelectRowsSpec<WorkloadRow> = {
  filter: {
    owner: () => false,
    unhealthy: (r) => workloadState(r) !== "ready",
    namespace: (r, v) => r.namespace === v,
    state: (r, v) => workloadState(r) === v,
    restarts: (r) => r.restartCount24h > 0,
  },
  text: (r) => [r.workloadName, r.namespace],
  sort: {
    deficit: (r) => r.desiredReplicas - r.readyReplicas,
    restarts: (r) => r.restartCount24h,
    name: (r) => r.workloadName.toLowerCase(),
    namespace: (r) => r.namespace.toLowerCase(),
    deployed: (r) => (r.lastImageDeployedAt ? Date.parse(r.lastImageDeployedAt) : 0),
  },
  id: (r) => `${r.namespace}/${r.workloadName}`,
};
