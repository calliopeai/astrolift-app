"use client";
import { useApolloClient } from "@apollo/client/react";
import * as React from "react";
import type { DocumentNode } from "graphql";
import { useTranslations } from "next-intl";
import { useMe } from "@/graphql/user/user.hooks";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { useLocalListState } from "@/components/list/use-list-state";
import type { ListDefinition } from "@/components/list/list-state";
import {
  TELEMETRY_APPS,
  TELEMETRY_ENVIRONMENTS,
  EXPLORER_LOGS,
  EXPLORER_TRACES,
  EXPLORER_SPANS,
} from "@/graphql/observability/explorers.queries";
import type { AstroliftAppTrace, AstroliftTraceSpan } from "@/graphql/__generated__/schema";
import type { AstroliftAppLogLine } from "@/graphql/lifecycle/lifecycle.types";
export interface AppChoice {
  id: string;
  name: string;
  slug: string;
}
export interface EnvironmentChoice {
  id: string;
  name: string;
  kind: string;
  clusterId: string | null;
}
export interface TelemetryScope {
  organizationId: string;
  appId: string;
  environmentId: string;
  environmentName: string;
  clusterId: string;
  namespace: string;
}
export interface LogPage {
  reason: string;
  items: AstroliftAppLogLine[];
  nextCursor: string | null;
  historicalAvailable: boolean;
  reachedRetention: boolean;
  scope: TelemetryScope | null;
}
export interface TracePage {
  reason: string;
  items: AstroliftAppTrace[];
  truncated: boolean;
  scope: TelemetryScope | null;
}
export interface SpanResult {
  reason: string;
  items: Array<
    Pick<
      AstroliftTraceSpan,
      | "traceId"
      | "spanId"
      | "parentSpanId"
      | "operation"
      | "service"
      | "startTime"
      | "durationMs"
      | "statusCode"
      | "attributes"
    > & { resourceAttributes?: unknown }
  >;
  scope: TelemetryScope | null;
}
function useRead<T>(query: DocumentNode, variables: Record<string, unknown>, key: string) {
  const client = useApolloClient();
  const encoded = JSON.stringify(variables);
  const latest = React.useRef("");
  const [snapshot, setSnapshot] = React.useState<{
    key: string;
    data: T | null;
    error: string | null;
  } | null>(null);
  React.useLayoutEffect(() => {
    latest.current = key;
  }, [key]);
  React.useEffect(() => {
    if (!key) return;
    let current = true;
    void client
      .query<T>({
        query,
        variables: JSON.parse(encoded),
        fetchPolicy: "no-cache",
        context: { queryDeduplication: false },
      })
      .then(
        ({ data, error }) => {
          if (current && latest.current === key)
            setSnapshot({
              key,
              data: error ? null : (data ?? null),
              error: error?.message ?? null,
            });
        },
        (error: unknown) => {
          if (current && latest.current === key)
            setSnapshot({ key, data: null, error: error instanceof Error ? error.message : "" });
        }
      );
    return () => {
      current = false;
    };
  }, [client, query, key, encoded]);
  return {
    data: key && snapshot?.key === key ? snapshot.data : null,
    error: key && snapshot?.key === key ? snapshot.error : null,
    loading: Boolean(key) && snapshot?.key !== key,
  };
}
export function useScopedExplorer(mode: "logs" | "traces") {
  const t = useTranslations("TelemetryExplorer");
  const { user } = useMe();
  const { org } = useActiveOrg();
  const actor = user && org ? `${user.id}:${org.id}` : "";
  const [admission, setAdmission] = React.useState({ actor, epoch: 0 });
  if (admission.actor !== actor) setAdmission({ actor, epoch: admission.epoch + 1 });
  const binding = actor ? `${actor}:${admission.epoch}` : "";
  const definition: ListDefinition = {
    id: `telemetry.${mode}.apps`,
    fields: [],
    searchPlaceholder: t("searchApps"),
    defaultSort: [],
    views: [{ key: "all", label: t("apps"), filters: {} }],
    paging: "cursor",
    pageSizes: [25, 50, 100],
  };
  const list = useLocalListState(definition);
  const [listBinding, setListBinding] = React.useState(binding);
  if (listBinding !== binding) {
    setListBinding(binding);
    list.setPageSize(list.state.pageSize); // reset opaque cursor walk before the new scope request
  }
  const [picked, setPicked] = React.useState<{ binding: string; app: AppChoice } | null>(null);
  const app = binding && picked?.binding === binding ? picked.app : null;
  const [choicesRevision, setChoicesRevision] = React.useState(0);
  const appsVariables = {
    search: list.state.q.trim() || null,
    cursor: listBinding === binding ? list.state.after : null,
    limit: list.state.pageSize,
  };
  const apps = useRead<{
    astroliftAppsPage: { items: AppChoice[]; nextCursor: string | null; totalCount: number | null };
  }>(
    TELEMETRY_APPS,
    appsVariables,
    binding ? `${binding}:apps:${choicesRevision}:${JSON.stringify(appsVariables)}` : ""
  );
  const [envPage, setEnvPage] = React.useState(1);
  const [pickedEnv, setPickedEnv] = React.useState<{
    appId: string;
    binding: string;
    value: EnvironmentChoice;
  } | null>(null);
  const envVariables = { appSlug: app?.slug ?? "", page: envPage, pageSize: 25 };
  const envs = useRead<{
    astroliftEnvironmentsPage: { items: EnvironmentChoice[]; totalCount: number };
  }>(
    TELEMETRY_ENVIRONMENTS,
    envVariables,
    app ? `${binding}:${app.id}:envs:${envPage}:${choicesRevision}` : ""
  );
  const environment =
    app && pickedEnv?.appId === app.id && pickedEnv.binding === binding ? pickedEnv.value : null;
  const [range, setRange] = React.useState(3600);
  const [filter, setFilter] = React.useState("");
  const [status, setStatus] = React.useState("all");
  const [revision, setRevision] = React.useState(0);
  const [until, setUntil] = React.useState(() => Date.now());
  const [cursor, setCursor] = React.useState<string | null>(null);
  const sinceIso = new Date(until - range * 1000).toISOString();
  const untilIso = new Date(until).toISOString();
  const identity =
    app && environment
      ? `${binding}:${app.id}:${environment.id}:${environment.clusterId}:${range}:${filter}:${status}:${until}:${revision}`
      : "";
  const variables = {
    appSlug: app?.slug ?? "",
    environmentName: environment?.name ?? "",
    environmentId: environment?.id ?? "",
    since: mode === "logs" ? sinceIso : String(Math.floor((until - range * 1000) / 1000)),
    until: mode === "logs" ? untilIso : String(Math.floor(until / 1000)),
    ...(mode === "logs"
      ? { search: filter || null, level: status === "all" ? null : status, cursor, limit: 200 }
      : { service: filter || null, status: status === "all" ? null : status, limit: 10 }),
  };
  const result = useRead<{ astroliftAppLogs?: LogPage; astroliftAppTracePage?: TracePage }>(
    mode === "logs" ? EXPLORER_LOGS : EXPLORER_TRACES,
    variables,
    identity ? `${identity}:${cursor ?? ""}` : ""
  );
  const validScope = (scope: TelemetryScope | null | undefined) =>
    Boolean(
      scope &&
      org &&
      app &&
      environment &&
      scope.organizationId === org.id &&
      scope.appId === app.id &&
      scope.environmentId === environment.id &&
      scope.clusterId === environment.clusterId &&
      scope.environmentName === environment.name
    );
  const rawLogs = result.data?.astroliftAppLogs;
  const rawTraces = result.data?.astroliftAppTracePage;
  // Error/unavailable pages may lack scope; successful payloads must match immutable selected identities.
  const logs =
    rawLogs && ((!rawLogs.items.length && rawLogs.reason !== "OK") || validScope(rawLogs.scope))
      ? rawLogs
      : null;
  const traces =
    rawTraces &&
    ((!rawTraces.items.length && rawTraces.reason !== "OK") || validScope(rawTraces.scope))
      ? rawTraces
      : null;
  const [buffer, setBuffer] = React.useState<{
    identity: string;
    token: LogPage;
    items: AstroliftAppLogLine[];
  } | null>(null);
  if (logs && buffer?.token !== logs)
    setBuffer({
      identity,
      token: logs,
      items:
        cursor && buffer?.identity === identity ? [...logs.items, ...buffer.items] : logs.items,
    });
  const [traceId, setTraceId] = React.useState<string | null>(null);
  const spanVars = {
    appSlug: app?.slug ?? "",
    environmentName: environment?.name ?? "",
    environmentId: environment?.id ?? "",
    traceId: traceId ?? "",
    since: String(Math.floor((until - range * 1000) / 1000)),
    until: String(Math.floor(until / 1000)),
  };
  const spanIdentity =
    identity && traceId && traces?.items.some((trace) => trace.traceId === traceId)
      ? `${identity}:trace:${traceId}`
      : "";
  const [spanRead, setSpanRead] = React.useState({ identity: spanIdentity, epoch: 0, revision: 0 });
  if (spanRead.identity !== spanIdentity) {
    setSpanRead({ identity: spanIdentity, epoch: spanRead.epoch + 1, revision: 0 });
  }
  const spans = useRead<{ astroliftTraceSpansResult: SpanResult }>(
    EXPLORER_SPANS,
    spanVars,
    spanIdentity && spanRead.identity === spanIdentity
      ? `${spanIdentity}:epoch:${spanRead.epoch}:retry:${spanRead.revision}`
      : ""
  );
  const spanData = spans.data?.astroliftTraceSpansResult;
  const safeSpans =
    spanData && ((!spanData.items.length && spanData.reason !== "OK") || validScope(spanData.scope))
      ? spanData
      : null;
  const refresh = () => {
    setCursor(null);
    setTraceId(null);
    setUntil(Date.now());
    setRevision((value) => value + 1);
  };
  return {
    mode,
    binding,
    list,
    apps: apps.data?.astroliftAppsPage.items ?? [],
    appsLoading: apps.loading,
    appsError:
      apps.error ??
      (binding && !apps.loading && !apps.data?.astroliftAppsPage ? t("failed") : null),
    appsNextCursor: apps.data?.astroliftAppsPage.nextCursor ?? null,
    appsTotal: apps.data?.astroliftAppsPage.totalCount ?? null,
    app,
    onPickApp: (value: AppChoice | null) => {
      setPicked(value ? { binding, app: value } : null);
      setPickedEnv(null);
      setEnvPage(1);
      refresh();
    },
    environments:
      envs.data?.astroliftEnvironmentsPage.items.filter(
        (env) => env.kind !== "preview" && env.clusterId
      ) ?? [],
    environmentsLoading: envs.loading,
    environmentsError:
      envs.error ??
      (app && !envs.loading && !envs.data?.astroliftEnvironmentsPage
        ? t("environmentFailed")
        : null),
    envPage,
    hasNextEnv: envPage * 25 < (envs.data?.astroliftEnvironmentsPage.totalCount ?? 0),
    onEnvPage: (value: number) => {
      setPickedEnv(null);
      setEnvPage(value);
      refresh();
    },
    environment,
    onPickEnvironment: (value: EnvironmentChoice) => {
      setPickedEnv({ appId: app?.id ?? "", binding, value });
      refresh();
    },
    range,
    onRange: (value: number) => {
      setRange(value);
      refresh();
    },
    filter,
    onSearch: (value: string) => {
      setFilter(value);
      refresh();
    },
    status,
    onStatus: (value: string) => {
      setStatus(value);
      refresh();
    },
    loading: result.loading,
    error:
      result.error ??
      (identity && !result.loading && !result.data ? t("failed") : null) ??
      (result.data && ((!logs && mode === "logs") || (!traces && mode === "traces"))
        ? t("stale")
        : null),
    logs,
    lines: buffer?.identity === identity ? buffer.items : [],
    traces,
    onRefresh: refresh,
    onOlder: () => {
      if (!result.loading && logs?.nextCursor) setCursor(logs.nextCursor);
    },
    traceId,
    onTrace: setTraceId,
    onRetryTrace: () => {
      if (!spanIdentity) return;
      setSpanRead((current) =>
        current.identity === spanIdentity &&
        current.epoch === spanRead.epoch &&
        current.revision === spanRead.revision
          ? { ...current, revision: current.revision + 1 }
          : current
      );
    },
    spans: safeSpans,
    spansLoading: spans.loading,
    spansError: spans.error ?? (spanData?.reason === "OK" && !safeSpans ? t("stale") : null),
    onRetryChoices: () => {
      setChoicesRevision((value) => value + 1);
    },
    liveHref:
      app && environment
        ? `/apps/${encodeURIComponent(app.slug)}/logs?${new URLSearchParams({ section: "metrics", panel: "pods", env: environment.name })}`
        : null,
  };
}
export type ExplorerProps = ReturnType<typeof useScopedExplorer>;
