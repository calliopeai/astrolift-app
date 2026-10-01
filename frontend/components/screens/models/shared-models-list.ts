import type { ListDefinition } from "@/components/list/list-state";
import type { ListClusterModelsPageQueryVariables } from "@/graphql/__generated__/operations";

export const SHARED_MODEL_STATUSES = [
  "pending",
  "provisioning",
  "active",
  "updating",
  "deprovisioning",
  "failed",
] as const;
export type SharedModelsCopy = {
  search: string;
  all: string;
  mine: string;
  mineNote: string;
  clusterId: string;
  compute: string;
  cpu: string;
  gpu: string;
  status: string;
  readiness: string;
  confirmed: string;
  unconfirmed: string;
  subscriptions: string;
  enabled: string;
  disabled: string;
  statuses: Record<(typeof SHARED_MODEL_STATUSES)[number], string>;
};
export function sharedModelsList(copy: SharedModelsCopy): ListDefinition {
  return {
    id: "models.shared.deployments",
    fields: [
      { key: "clusterId", label: copy.clusterId },
      {
        key: "computeMode",
        label: copy.compute,
        options: [
          { value: "cpu", label: copy.cpu },
          { value: "gpu", label: copy.gpu },
        ],
      },
      {
        key: "status",
        label: copy.status,
        options: SHARED_MODEL_STATUSES.map((value) => ({ value, label: copy.statuses[value] })),
      },
      {
        key: "ready",
        label: copy.readiness,
        options: [
          { value: "true", label: copy.confirmed },
          { value: "false", label: copy.unconfirmed },
        ],
      },
      {
        key: "subscriptionsEnabled",
        label: copy.subscriptions,
        options: [
          { value: "true", label: copy.enabled },
          { value: "false", label: copy.disabled },
        ],
      },
    ],
    views: [
      { key: "all", label: copy.all, filters: {} },
      { key: "mine", label: copy.mine, filters: { deployedByMe: "true" }, note: copy.mineNote },
    ],
    searchPlaceholder: copy.search,
    defaultSort: [],
    paging: "numbered",
    pageSizes: [10, 25, 50],
    defaultPageSize: 25,
  };
}
export function sharedModelsVariables(
  organizationId: string,
  search: string,
  filters: Record<string, string>,
  page: number,
  pageSize: number
): ListClusterModelsPageQueryVariables | null {
  const booleanFields = ["ready", "subscriptionsEnabled", "deployedByMe"] as const;
  if (
    booleanFields.some(
      (field) => filters[field] !== undefined && !["true", "false"].includes(filters[field])
    )
  )
    return null;
  const boolean = (field: (typeof booleanFields)[number]) =>
    filters[field] === undefined ? null : filters[field] === "true";
  return {
    organizationId,
    search: search || null,
    page,
    pageSize,
    filter: {
      clusterId: filters.clusterId || null,
      computeMode: filters.computeMode || null,
      status: filters.status || null,
      ready: boolean("ready"),
      subscriptionsEnabled: boolean("subscriptionsEnabled"),
      deployedByMe: boolean("deployedByMe"),
    },
  };
}
