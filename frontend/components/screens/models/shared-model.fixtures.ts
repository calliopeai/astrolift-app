import { defaultListState, type ListStateController } from "@/components/list/list-state";
import type { ModelPage } from "./ModelSubscriptionsPanel";
import type { ModelSubscriptionsPanelProps } from "./ModelSubscriptionsPanel";

export const subscriptionProps: ModelSubscriptionsPanelProps = {
  deployment: {
    id: "shared-model",
    version: 5,
    organizationId: "org",
    name: "Qwen production",
    runtimeAdmission: "configured",
  },
  targets: fakeModelPage({
    rows: [
      {
        id: "env-production",
        version: 3,
        appSlug: "storefront",
        environmentName: "production",
        admission: "allowed",
        reason: null,
      },
      {
        id: "env-staging",
        version: 4,
        appSlug: "storefront",
        environmentName: "staging",
        admission: "allowed",
        reason: null,
      },
    ],
  }),
  subscriptions: fakeModelPage({
    rows: [
      {
        id: "subscription-one",
        version: 2,
        alias: "chat",
        bindingPrefix: "MODEL_CHAT_",
        appSlug: "storefront",
        environmentName: "production",
        status: "active",
        desiredVersion: 2,
        appliedVersion: 2,
        reason: null,
        canRevoke: true,
      },
    ],
  }),
  onSubscribe: async () => ({ accepted: true }),
  onRevoke: async () => ({ accepted: true }),
};

export function fakeModelPage<T>(patch: Partial<ModelPage<T>> = {}): ModelPage<T> {
  const noop = () => {};
  const definition = {
    id: "models.subscription-test",
    fields: [],
    searchPlaceholder: "Search app environments",
    defaultSort: [],
    views: [{ key: "all", label: "All", filters: {} }],
    paging: "cursor" as const,
    pageSizes: [20],
  };
  const list: ListStateController = {
    definition,
    state: defaultListState(definition),
    filters: {},
    isFiltered: false,
    setSearch: noop,
    setFilter: noop,
    applySearch: noop,
    clearFilters: noop,
    toggleSort: noop,
    setSort: noop,
    setPage: noop,
    older: noop,
    newer: noop,
    hasNewer: false,
    setPageSize: noop,
    viewHref: () => "#",
    hiddenColumns: [],
    toggleColumn: noop,
    mode: "list",
    setMode: noop,
  };
  return {
    list,
    rows: [],
    loading: false,
    stale: false,
    error: null,
    onRetry: noop,
    nextCursor: null,
    totalCount: null,
    ...patch,
  };
}
