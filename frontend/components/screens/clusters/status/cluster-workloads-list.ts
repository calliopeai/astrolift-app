/**
 * Cluster › Health › Workload health (spec 44 §5.1): the embedded list's
 * declaration and the filter, sort and page step the hook runs.
 * `astroliftClusterWorkloadHealth` returns every Deployment in the managed
 * namespaces at once, with no arguments, so namespace, state, search, sort
 * and numbered pages all run in the client (needsBackend: a Page field).
 * The rows are the cluster's, not a person's, so Mine is empty.
 */
import { useTranslations } from "next-intl";
import { useMemo } from "react";

import { type ListDefinition, standardViews } from "@/components/list/list-state";
import type { SelectRowsSpec } from "@/components/list/select-rows";

import type { WorkloadRow } from "./types";
import { observedHealthCount, observedHealthDate, observedReadiness } from "./health-observations";

export type WorkloadState = "ready" | "degraded" | "down" | "inactive" | "unknown";

/** How ready a Deployment is: every replica, some, or none. */
export function workloadState(row: WorkloadRow): WorkloadState {
  if (!observedReadiness(row.readyReplicas, row.desiredReplicas)) return "unknown";
  if (row.desiredReplicas === 0) return "inactive";
  const deficit = row.desiredReplicas - row.readyReplicas;
  if (deficit <= 0) return "ready";
  return deficit >= row.desiredReplicas ? "down" : "degraded";
}

/** Localized chrome retains the same URL filter values and preference identity. */
export function useClusterWorkloadsListDefinition(): ListDefinition {
  const t = useTranslations("clusterHealthTab");
  return useMemo(
    () => ({
      id: "clusters.health.workloads",
      fields: [
        { key: "namespace", label: t("namespace") },
        {
          key: "state",
          label: t("state"),
          options: (["ready", "degraded", "down", "inactive", "unknown"] as const).map((value) => ({
            value,
            label: t(value),
          })),
        },
        {
          key: "restarts",
          label: t("restarts"),
          options: [{ value: "yes", label: t("restarted") }],
        },
      ],
      searchPlaceholder: t("search"),
      defaultSort: [
        { key: "deficit", dir: "desc" },
        { key: "restarts", dir: "desc" },
      ],
      views: standardViews(
        { owner: "me" },
        [{ key: "unhealthy", label: t("unhealthy"), filters: { unhealthy: "yes" } }],
        { mineNote: t("mineNote") }
      ).map((view) => ({
        ...view,
        label: view.key === "all" ? t("all") : view.key === "mine" ? t("mine") : view.label,
      })),
      paging: "numbered",
      pageSizes: [25, 50, 100],
    }),
    [t]
  );
}

export const CLUSTER_WORKLOADS_SELECT: SelectRowsSpec<WorkloadRow> = {
  filter: {
    owner: () => false,
    unhealthy: (r) => ["degraded", "down"].includes(workloadState(r)),
    namespace: (r, v) => r.namespace === v,
    state: (r, v) => workloadState(r) === v,
    restarts: (r) => observedHealthCount(r.restartCount24h) && r.restartCount24h > 0,
  },
  text: (r) => [r.workloadName, r.namespace],
  sort: {
    deficit: (r) =>
      observedReadiness(r.readyReplicas, r.desiredReplicas)
        ? r.desiredReplicas - r.readyReplicas
        : -1,
    restarts: (r) => (observedHealthCount(r.restartCount24h) ? r.restartCount24h : -1),
    name: (r) => r.workloadName.toLowerCase(),
    namespace: (r) => r.namespace.toLowerCase(),
    deployed: (r) => observedHealthDate(r.lastImageDeployedAt)?.getTime() ?? 0,
  },
  id: (r) => `${r.namespace}/${r.workloadName}`,
};
