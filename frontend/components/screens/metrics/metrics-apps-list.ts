/**
 * Admin › Metrics › Apps (spec 44 §5.1): the per-app health list's
 * declaration and its client-side step. `astroliftAppHealthSummary`
 * returns every app at once with no arguments, so search, the filters, sort
 * and numbered pages run in the client (needsBackend: a Page field). The
 * summary carries no owner, so Mine is empty.
 */
import type { SelectRowsSpec } from "@/components/list/select-rows";
import { type ListDefinition, standardViews } from "@/components/list/use-list-state";
import type { AstroliftAppHealthSummary } from "@/graphql/lifecycle/lifecycle.types";

const STATUSES = [
  "pending_approval",
  "pending",
  "deploying",
  "redeploying",
  "running",
  "failed",
  "superseded",
  "rolled_back",
];

export const METRICS_APPS_LIST: ListDefinition = {
  id: "admin.metrics.apps",
  fields: [
    {
      key: "status",
      label: "Latest deploy",
      options: STATUSES.map((s) => ({ value: s, label: s.replace(/_/g, " ") })),
    },
    {
      key: "kind",
      label: "Kind",
      options: [
        { value: "app", label: "app" },
        { value: "agent", label: "agent" },
      ],
    },
  ],
  searchPlaceholder: "Search apps, slugs, image tags…",
  defaultSort: [{ key: "deployed", dir: "desc" }],
  views: standardViews(
    { owner: "me" },
    [{ key: "failing", label: "Recent failure", filters: { failing: "yes" } }],
    { mineNote: "The health summary does not record who owns an app yet, so Mine is empty." }
  ),
  paging: "numbered",
  pageSizes: [25, 50, 100],
};

export const METRICS_APPS_SELECT: SelectRowsSpec<AstroliftAppHealthSummary> = {
  filter: {
    owner: () => false,
    failing: (a) => a.hasRecentFailure,
    status: (a, v) => a.latestDeploymentStatus === v,
    kind: (a, v) => a.primitiveKind === v,
  },
  text: (a) => [a.appName, a.appSlug, a.latestImageTag],
  sort: {
    name: (a) => a.appName.toLowerCase(),
    envs: (a) => a.environmentCount,
    deployed: (a) => (a.lastDeployedAt ? Date.parse(a.lastDeployedAt) : 0),
  },
  id: (a) => a.appSlug,
};
