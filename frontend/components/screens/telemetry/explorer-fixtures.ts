import { useLocalListState } from "@/components/list/use-list-state";
import type { ExplorerProps } from "./use-scoped-explorer";
export const APP = { id: "app-guid", name: "Example app", slug: "example" };
export const ENV = {
  id: "env-guid",
  name: "production",
  kind: "production",
  clusterId: "cluster-guid",
};
export const SCOPE = {
  organizationId: "org-guid",
  appId: APP.id,
  environmentId: ENV.id,
  clusterId: "cluster-guid",
  environmentName: ENV.name,
  namespace: "example-production",
};
export function useExplorerFixture(mode: "logs" | "traces"): ExplorerProps {
  const noop = () => {};
  const list = useLocalListState({
    id: "telemetry.story",
    fields: [],
    searchPlaceholder: "",
    defaultSort: [],
    views: [],
    paging: "cursor",
    pageSizes: [25],
  });
  return {
    mode,
    binding: "actor:org-guid",
    list,
    apps: [APP],
    appsLoading: false,
    appsError: null,
    appsNextCursor: null,
    appsTotal: 1,
    app: APP,
    onPickApp: noop,
    environments: [ENV],
    environment: ENV,
    environmentsLoading: false,
    environmentsError: null,
    envPage: 1,
    hasNextEnv: false,
    onEnvPage: noop,
    onPickEnvironment: noop,
    range: 3600,
    onRange: noop,
    filter: "",
    onSearch: noop,
    status: "all",
    onStatus: noop,
    loading: false,
    error: null,
    logs:
      mode === "logs"
        ? {
            reason: "NOT_CONFIGURED",
            items: [],
            nextCursor: null,
            historicalAvailable: false,
            reachedRetention: false,
            scope: SCOPE,
          }
        : null,
    lines: [],
    traces:
      mode === "traces"
        ? { reason: "NOT_CONFIGURED", items: [], truncated: false, scope: SCOPE }
        : null,
    onRefresh: noop,
    onOlder: noop,
    traceId: null,
    onTrace: noop,
    spans: null,
    spansLoading: false,
    spansError: null,
    onRetryChoices: noop,
    liveHref: "/apps/example/logs?section=metrics&panel=pods&env=production",
  };
}
