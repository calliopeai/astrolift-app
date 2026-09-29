import type { SelectRowsSpec } from "@/components/list/select-rows";
import { type ListDefinition, standardViews } from "@/components/list/list-state";
import type { AstroliftAppEndpointMetric } from "@/graphql/__generated__/schema";

/**
 * The per-route metric set: `astroliftAppEndpointMetrics` returns every
 * route at once with no arguments, so search, the error filter, sort and
 * numbered pages run in the client (needsBackend: a Page field). Routes are
 * the app's, not a person's, so Mine is empty.
 */
export const ENDPOINT_METRICS_LIST: ListDefinition = {
  id: "observability.endpoints",
  fields: [
    {
      key: "errors",
      label: "Errors",
      options: [
        { value: "any", label: "above 0%" },
        { value: "high", label: "above 5%" },
      ],
    },
  ],
  searchPlaceholder: "Search routes…",
  defaultSort: [{ key: "rate", dir: "desc" }],
  views: standardViews({ owner: "me" }, [], {
    mineNote: "Routes are the app's, not a person's, so Mine is empty.",
  }),
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

export const ENDPOINT_METRICS_SELECT: SelectRowsSpec<AstroliftAppEndpointMetric> = {
  filter: {
    owner: () => false,
    errors: (r, v) => r.errorRateRatio > (v === "high" ? 0.05 : 0),
  },
  text: (r) => [r.route],
  sort: {
    route: (r) => r.route,
    rate: (r) => r.requestRate,
    error: (r) => r.errorRateRatio,
    p50: (r) => r.p50Ms ?? -1,
    p90: (r) => r.p90Ms ?? -1,
    p99: (r) => r.p99Ms ?? -1,
  },
  id: (r) => r.route,
};
