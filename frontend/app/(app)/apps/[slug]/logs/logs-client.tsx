"use client";

import { useQuery, useSubscription } from "@apollo/client/react";
import {
  AlertTriangleIcon,
  BoxIcon,
  DownloadIcon,
  PauseIcon,
  PlayIcon,
  ScrollTextIcon,
  Trash2Icon,
} from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { AppLogExportDialog, LogViewer } from "@/components/observability";
import { useLogExport } from "@/components/observability/use-log-export";
import { PageShell } from "@/components/PageShell";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { ON_APP_LOG } from "@/graphql/lifecycle/lifecycle.subscriptions";
import type { AstroliftAppLogLine } from "@/graphql/lifecycle/lifecycle.types";
import { GET_APP } from "@/graphql/registry/registry.queries";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";

import { appPath, useAppChrome } from "../components/app-chrome-context";
import { AppTabs } from "../components/app-tabs";
import { usePodTarget } from "../components/use-pod-target";

interface AppResp {
  astroliftApp: AstroliftRegisteredApp | null;
}

interface LogResp {
  astroliftOnAppLog: AstroliftAppLogLine;
}

const LOG_BUFFER_LIMIT = 500;
const DEFAULT_TAIL_LINES = 200;

/**
 * Observe › Logs — the read-only half of what used to be the Console tab.
 *
 * The shell and the script upload moved to Control › Shell (#1247): reading a
 * log and getting a root shell on a running pod are different acts with
 * different blast radius, and only one of them is observability.
 */
export function LogsClient({ slug }: { slug: string }) {
  const chrome = useAppChrome();
  const tCommon = useTranslations("apps.common");
  const t = useTranslations("apps.logs");
  const tObs = useTranslations("apps.observability");

  const app = useQuery<AppResp>(GET_APP, { variables: { slug } });
  const a = app.data?.astroliftApp;

  const {
    podRows,
    selectedPod,
    setPickedPod,
    podContainers,
    selectedContainer,
    setPickedContainer,
    podsLoading,
    noPods,
  } = usePodTarget(slug);
  const logExport = useLogExport({
    appSlug: slug,
    podName: selectedPod,
    container: selectedContainer,
  });

  const [streaming, setStreaming] = React.useState(false);
  const [logBuffer, setLogBuffer] = React.useState<AstroliftAppLogLine[]>([]);
  const [exportOpen, setExportOpen] = React.useState(false);

  // Pre-buffer on open: as soon as a pod resolves flip streaming on once
  // so the subscription replays the last DEFAULT_TAIL_LINES before following.
  // A one-shot guard stops the 5s pod poll from re-arming after the operator pauses.
  const [autoStreamed, setAutoStreamed] = React.useState(false);
  if (!autoStreamed && selectedPod) {
    setAutoStreamed(true);
    setStreaming(true);
  }

  // Reset the buffer whenever the operator switches pod or container.
  // See: https://react.dev/learn/you-might-not-need-an-effect#resetting-all-state-when-a-prop-changes
  const streamKey = `${selectedPod ?? ""}::${selectedContainer ?? ""}`;
  const [prevStreamKey, setPrevStreamKey] = React.useState(streamKey);
  if (prevStreamKey !== streamKey) {
    setPrevStreamKey(streamKey);
    if (logBuffer.length !== 0) setLogBuffer([]);
  }

  useSubscription<LogResp>(ON_APP_LOG, {
    variables: {
      appSlug: slug,
      podName: selectedPod ?? "",
      container: selectedContainer ?? null,
      follow: true,
      tailLines: DEFAULT_TAIL_LINES,
    },
    skip: !streaming || !selectedPod,
    onData: ({ data }) => {
      const line = data.data?.astroliftOnAppLog;
      if (!line) return;
      setLogBuffer((prev) => {
        const next = [...prev, line];
        return next.length > LOG_BUFFER_LIMIT ? next.slice(-LOG_BUFFER_LIMIT) : next;
      });
    },
  });

  if (app.loading && !a) {
    return (
      <PageShell title={tCommon("loading")}>
        <Skeleton className="h-32 w-full" />
      </PageShell>
    );
  }

  if (!a) {
    return (
      <PageShell title={tCommon("notFound")}>
        <EmptyState
          icon={<AlertTriangleIcon className="size-5" />}
          title={tCommon("notFoundSlug", { slug })}
          description={tCommon("notFoundDescription")}
          actionHref="/apps"
          actionLabel={tCommon("backToApps")}
        />
      </PageShell>
    );
  }

  return (
    <PageShell
      title={t("title", { name: a.name })}
      description={
        <span className="text-muted-foreground font-mono text-xs">
          {t("description", { slug: a.slug })}
        </span>
      }
    >
      <AppTabs slug={a.slug} active="console" />

      <Card>
        <CardHeader className="flex flex-row items-start justify-between gap-3 space-y-0 pb-3">
          <div>
            <CardTitle className="flex items-center gap-2 text-base">
              <ScrollTextIcon className="size-4" /> {t("logs.title")}
            </CardTitle>
            <CardDescription>
              {selectedPod ? (
                <>
                  {tObs("logs.streaming")}{" "}
                  <code className="bg-muted text-2xs rounded px-1 py-0.5 font-mono">
                    {selectedPod}
                  </code>{" "}
                  {tObs("logs.fromCluster")}
                </>
              ) : (
                t("logs.selectPrompt")
              )}
            </CardDescription>
          </div>
          <div className="flex items-center gap-2">
            <div className="flex items-center gap-1.5">
              <BoxIcon aria-hidden="true" className="text-muted-foreground size-3.5" />
              <Select
                value={selectedPod ?? ""}
                onValueChange={(v) => setPickedPod(v)}
                disabled={podsLoading || noPods}
              >
                <SelectTrigger
                  size="sm"
                  aria-label={t("logs.podLabel")}
                  className="font-mono text-xs"
                >
                  <SelectValue placeholder={t("logs.podPlaceholder")} />
                </SelectTrigger>
                <SelectContent>
                  {podRows.map((p) => (
                    <SelectItem key={p.name} value={p.name} className="font-mono">
                      {p.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <Button
              size="sm"
              variant="outline"
              onClick={() => setLogBuffer([])}
              disabled={logBuffer.length === 0}
            >
              <Trash2Icon className="size-3" /> {tObs("logs.clear")}
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => setExportOpen(true)}
              disabled={!selectedPod}
              title={tObs("logs.exportTitle")}
            >
              <DownloadIcon className="size-3" /> {tObs("logs.export")}
            </Button>
            <Button
              size="sm"
              variant={streaming ? "outline" : "default"}
              onClick={() => setStreaming((s) => !s)}
              disabled={!selectedPod}
            >
              {streaming ? (
                <>
                  <PauseIcon className="size-3" /> {tObs("logs.pause")}
                </>
              ) : (
                <>
                  <PlayIcon className="size-3" /> {tObs("logs.stream")}
                </>
              )}
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {noPods ? (
            <EmptyState
              icon={<BoxIcon className="size-5" />}
              title={tObs("pods.emptyTitle")}
              description={tObs("pods.emptyDescription")}
              actionHref={appPath(chrome, a.slug, "deployments")}
              actionLabel={tObs("pods.emptyAction")}
            />
          ) : (
            <LogViewer
              lines={logBuffer}
              appSlug={a.slug}
              podName={selectedPod}
              containers={podContainers}
              selectedContainer={selectedContainer}
              onContainerChange={setPickedContainer}
              loading={podsLoading}
              bufferLimit={LOG_BUFFER_LIMIT}
              emptyHint={
                streaming
                  ? tObs("logs.waiting")
                  : selectedPod
                    ? tObs("logs.pressStream")
                    : tObs("logs.pickPod")
              }
            />
          )}
        </CardContent>
      </Card>

      {/* #483 app-log export modal — vendor handoff + compliance. */}
      <AppLogExportDialog
        open={exportOpen}
        onOpenChange={setExportOpen}
        podName={selectedPod}
        {...logExport}
      />
    </PageShell>
  );
}
