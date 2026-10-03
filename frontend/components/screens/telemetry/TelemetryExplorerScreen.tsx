"use client";
import * as React from "react";
import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";
import { PageShell } from "@/components/PageShell";
import { QueryError } from "@/components/QueryError";
import { EmptyState } from "@/components/EmptyState";
import { ListPage } from "@/components/list/ListPage";
import { type Column } from "@/components/data-table";
import { useLocalListState } from "@/components/list/use-list-state";
import { LogViewer } from "@/components/observability/LogViewer";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import type { AstroliftAppTrace } from "@/graphql/__generated__/schema";
import type { AppChoice, ExplorerProps, SpanResult } from "./use-scoped-explorer";
export const TELEMETRY_SETUP =
  "https://github.com/calliopeai/astrolift-app/blob/main/docs/operators/scoped-telemetry-explorers.md";
export function TelemetryExplorerScreen(props: ExplorerProps) {
  const t = useTranslations("TelemetryExplorer");
  const locale = useLocale();
  const sampleDefinition = {
    id: "telemetry.trace.sample",
    fields: [],
    searchPlaceholder: "",
    defaultSort: [],
    views: [{ key: "all", label: t("tracesTitle"), filters: {} }],
    paging: "cursor" as const,
    pageSizes: [20],
  };
  const traceList = useLocalListState(sampleDefinition);
  const spanList = useLocalListState({ ...sampleDefinition, id: "telemetry.trace.detail" });
  const { mode, app, environment } = props;
  const draftScope = JSON.stringify([
    mode,
    props.binding,
    app?.id,
    environment?.id,
    environment?.clusterId,
  ]);
  const [draftState, setDraftState] = React.useState({ scope: draftScope, value: props.filter });
  if (draftState.scope !== draftScope) {
    setDraftState({ scope: draftScope, value: props.filter });
  }
  const draft = draftState.scope === draftScope ? draftState.value : props.filter;
  const reason = mode === "logs" ? props.logs?.reason : props.traces?.reason;
  const appColumns: Column<AppChoice>[] = [
    {
      id: "name",
      header: t("app"),
      cell: (row) => (
        <Button variant="link" onClick={() => props.onPickApp(row)}>
          {row.name}
        </Button>
      ),
    },
    { id: "slug", header: t("slug"), cell: (row) => <span className="font-mono">{row.slug}</span> },
  ];
  const traceColumns: Column<AstroliftAppTrace>[] = [
    {
      id: "operation",
      header: t("operation"),
      cell: (row) => (
        <Button variant="link" onClick={() => props.onTrace(row.traceId)}>
          {row.rootOperation}
        </Button>
      ),
    },
    { id: "service", header: t("service"), cell: (row) => row.rootService },
    {
      id: "id",
      header: t("traceId"),
      cell: (row) => <span className="font-mono text-xs">{row.traceId}</span>,
    },
    { id: "spans", header: t("spans"), cell: (row) => row.spanCount },
    {
      id: "duration",
      header: t("duration"),
      cell: (row) => t("milliseconds", { count: row.durationMs }),
    },
    { id: "status", header: t("status"), cell: (row) => row.statusCode },
  ];
  const spanColumns: Column<SpanResult["items"][number]>[] = [
    {
      id: "detail",
      header: t("spanDetail"),
      cell: (row) => (
        <details>
          <summary className="cursor-pointer font-mono text-xs">{row.spanId}</summary>
          <pre className="mt-2 max-w-xl overflow-auto text-xs">
            {JSON.stringify(
              {
                parentSpanId: row.parentSpanId,
                attributes: row.attributes,
                resourceAttributes: row.resourceAttributes,
              },
              null,
              2
            )}
          </pre>
        </details>
      ),
    },
    { id: "operation", header: t("operation"), cell: (row) => row.operation },
    { id: "service", header: t("service"), cell: (row) => row.service },
    {
      id: "start",
      header: t("start"),
      cell: (row) => {
        const stamp = new Date(Number(row.startTime) / 1_000_000);
        return (
          <span title={row.startTime}>
            {Number.isFinite(stamp.getTime()) ? stamp.toLocaleString(locale) : row.startTime}
          </span>
        );
      },
    },
    {
      id: "duration",
      header: t("duration"),
      cell: (row) => t("milliseconds", { count: row.durationMs }),
    },
    { id: "status", header: t("status"), cell: (row) => row.statusCode },
  ];
  if (!app) {
    return (
      <PageShell title={t(`${mode}Title`)} description={t(`${mode}Description`)}>
        {props.binding ? (
          <ListPage
            embedded
            list={props.list}
            label={t("apps")}
            columns={appColumns}
            rows={props.apps}
            getRowId={(row) => row.id}
            loading={props.appsLoading}
            error={props.appsError ? { message: props.appsError } : null}
            onRetry={props.onRetryChoices}
            totalCount={props.appsTotal}
            nextCursor={props.appsNextCursor}
            empty={{ icon: null, title: t("noApps"), description: t("noAppsDescription") }}
          />
        ) : (
          <p role="status">{t("identityLoading")}</p>
        )}
      </PageShell>
    );
  }
  return (
    <PageShell title={t(`${mode}Title`)} description={t(`${mode}Description`)}>
      <div className="space-y-5">
        {!props.binding && <p role="status">{t("identityLoading")}</p>}
        {props.binding && (
          <div className="space-y-3">
            <div className="flex items-center justify-between gap-3">
              <p className="font-medium">
                {app.name}{" "}
                <span className="text-muted-foreground font-mono text-xs">{app.slug}</span>
              </p>
              <Button variant="outline" onClick={() => props.onPickApp(null)}>
                {t("changeApp")}
              </Button>
            </div>
            <QueryError
              title={t("environmentFailed")}
              error={props.environmentsError}
              onRetry={props.onRetryChoices}
              retryLabel={t("retry")}
            />
            {props.environmentsLoading ? (
              <Skeleton className="h-10 w-full" aria-label={t("environmentLoading")} />
            ) : (
              <>
                <Select
                  value={environment?.id ?? ""}
                  onValueChange={(id) => {
                    const value = props.environments.find((env) => env.id === id);
                    if (value) props.onPickEnvironment(value);
                  }}
                >
                  <SelectTrigger aria-label={t("environment")}>
                    <SelectValue placeholder={t("selectEnvironment")} />
                  </SelectTrigger>
                  <SelectContent>
                    {props.environments.map((env) => (
                      <SelectItem key={env.id} value={env.id}>
                        {env.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {!props.environments.length && !props.environmentsError && (
                  <p role="status">{t("noEnvironments")}</p>
                )}
                <p className="text-muted-foreground text-xs">{t("previewHint")}</p>
                <div className="flex gap-2">
                  <Button
                    variant="outline"
                    disabled={props.envPage <= 1}
                    onClick={() => props.onEnvPage(props.envPage - 1)}
                  >
                    {t("previous")}
                  </Button>
                  <Button
                    variant="outline"
                    disabled={!props.hasNextEnv}
                    onClick={() => props.onEnvPage(props.envPage + 1)}
                  >
                    {t("next")}
                  </Button>
                </div>
              </>
            )}
          </div>
        )}
        {app && environment && (
          <>
            {mode === "traces" && props.traceId ? (
              <section className="space-y-3" aria-label={t("spanDetail")}>
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <p className="font-mono text-sm">{props.traceId}</p>
                  <Button variant="outline" onClick={() => props.onTrace(null)}>
                    {t("backToResults")}
                  </Button>
                </div>
                <p className="text-muted-foreground text-xs">{t("traceScope")}</p>
                <QueryError
                  title={t("failed")}
                  error={props.spansError}
                  retryLabel={t("retry")}
                  onRetry={props.onRetryTrace}
                />
                {props.spans?.reason && props.spans.reason !== "OK" && (
                  <div role="status" className="space-y-2">
                    <p>
                      {t(
                        props.spans.reason === "ERROR"
                          ? "backendError"
                          : props.spans.reason === "NOT_CONFIGURED"
                            ? "tracesUnavailable"
                            : "empty"
                      )}
                    </p>
                    {props.spans.reason === "ERROR" && (
                      <Button variant="outline" onClick={props.onRetryTrace}>
                        {t("retry")}
                      </Button>
                    )}
                  </div>
                )}
                {(props.spansLoading || props.spans?.reason === "OK") && (
                  <ListPage
                    embedded
                    bounded
                    label={t("spanDetail")}
                    columns={spanColumns}
                    list={spanList}
                    rows={props.spans?.reason === "OK" ? props.spans.items : []}
                    loading={props.spansLoading}
                    getRowId={(row) => row.spanId}
                    empty={{ icon: null, title: t("empty"), description: t("traceScope") }}
                  />
                )}
              </section>
            ) : (
              <>
                <div className="flex flex-wrap items-center gap-3">
                  <Select
                    value={String(props.range)}
                    onValueChange={(value) => props.onRange(Number(value))}
                  >
                    <SelectTrigger className="w-44" aria-label={t("range")}>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {[900, 3600, 21600, 86400].map((seconds) => (
                        <SelectItem key={seconds} value={String(seconds)}>
                          {t(`range${seconds}`)}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <Select value={props.status} onValueChange={props.onStatus}>
                    <SelectTrigger
                      className="w-44"
                      aria-label={t(mode === "logs" ? "level" : "status")}
                    >
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {(mode === "logs"
                        ? ["all", "error", "warn", "info", "debug", "other"]
                        : ["all", "OK", "ERROR"]
                      ).map((value) => (
                        <SelectItem key={value} value={value}>
                          {t(`filter.${value}`)}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <Button variant="outline" onClick={props.onRefresh} disabled={props.loading}>
                    {t("refresh")}
                  </Button>
                  {mode === "logs" && props.liveHref && (
                    <Button asChild variant="outline">
                      <Link href={props.liveHref}>{t("liveLogs")}</Link>
                    </Button>
                  )}
                </div>
                <form
                  className="flex items-end gap-2"
                  onSubmit={(event) => {
                    event.preventDefault();
                    props.onSearch(draft);
                  }}
                >
                  <div className="flex-1 space-y-2">
                    <Label htmlFor="telemetry-filter">
                      {t(mode === "logs" ? "searchLogs" : "service")}
                    </Label>
                    <Input
                      id="telemetry-filter"
                      value={draft}
                      onChange={(event) =>
                        setDraftState({ scope: draftScope, value: event.target.value })
                      }
                    />
                  </div>
                  <Button type="submit">{t("search")}</Button>
                </form>
                <QueryError
                  title={t("failed")}
                  error={props.error}
                  onRetry={props.onRefresh}
                  retryLabel={t("retry")}
                />
                {props.loading && <p role="status">{t("loading")}</p>}
                {(reason === "NOT_CONFIGURED" || reason === "NOT_SUPPORTED_BY_PROVIDER") && (
                  <EmptyState
                    icon={null}
                    title={t(`${mode}Unavailable`)}
                    description={t(
                      (mode === "logs" ? props.logs?.scope : props.traces?.scope)
                        ? `${mode}Setup`
                        : "scopeUnavailable"
                    )}
                    actionHref={TELEMETRY_SETUP}
                    actionLabel={t("setup")}
                  />
                )}
                {reason === "ERROR" && (
                  <div role="alert" className="space-y-2">
                    <p>{t("backendError")}</p>
                    <Button variant="outline" onClick={props.onRefresh}>
                      {t("retry")}
                    </Button>
                    <a href={TELEMETRY_SETUP} className="text-primary underline">
                      {t("setup")}
                    </a>
                  </div>
                )}
                {reason === "NO_DATA_YET" && <p role="status">{t("empty")}</p>}
                {mode === "logs" && (
                  <>
                    {props.logs?.reachedRetention && <p role="status">{t("retention")}</p>}
                    {reason === "OK" && (
                      <LogViewer
                        lines={props.lines}
                        appSlug={app.slug}
                        environmentName={environment.name}
                        showPodBadge
                        bufferLimit={200}
                      />
                    )}
                    <Button
                      variant="outline"
                      disabled={props.loading || !props.logs?.nextCursor}
                      onClick={props.onOlder}
                    >
                      {t("older")}
                    </Button>
                  </>
                )}
                {mode === "traces" && (
                  <>
                    {props.traces?.truncated && <p role="status">{t("truncated")}</p>}
                    <p className="text-muted-foreground text-xs">{t("boundedSearch")}</p>
                    {reason === "OK" && (
                      <ListPage
                        embedded
                        bounded
                        label={t("tracesTitle")}
                        columns={traceColumns}
                        list={traceList}
                        rows={props.traces?.items ?? []}
                        loading={props.loading}
                        getRowId={(row) => row.traceId}
                        empty={{ icon: null, title: t("empty"), description: t("traceScope") }}
                      />
                    )}
                  </>
                )}
              </>
            )}
          </>
        )}
      </div>
    </PageShell>
  );
}
