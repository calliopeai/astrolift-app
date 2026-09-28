"use client";

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
import type { useLogExport } from "@/components/observability/use-log-export";
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
import type { AstroliftAppPod } from "@/graphql/lifecycle/lifecycle.types";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";

import { LOG_BUFFER_LIMIT, type useAppLogs } from "./use-app-logs";

type AppLogs = ReturnType<typeof useAppLogs>;

export interface AppLogsScreenProps extends Omit<AppLogs, "app"> {
  slug: string;
  app: Pick<AstroliftRegisteredApp, "name" | "slug"> | null;
  /** The pod picker, from the route's pod target. */
  podRows: Pick<AstroliftAppPod, "name">[];
  selectedPod: string | null;
  setPickedPod: (pod: string) => void;
  podContainers: string[];
  selectedContainer: string | null;
  setPickedContainer: (container: string | null) => void;
  podsLoading: boolean;
  noPods: boolean;
  logExport: ReturnType<typeof useLogExport>;
  /** Link to this app's deployments page (no-pods CTA). */
  deploymentsHref: string;
  /** The app tab row. */
  tabs?: React.ReactNode;
}

/**
 * Observe › Logs — the read-only half of what used to be the Console tab.
 *
 * The shell and the script upload moved to Control › Shell (#1247): reading a
 * log and getting a root shell on a running pod are different acts with
 * different blast radius, and only one of them is observability.
 */
export function AppLogsScreen({
  slug,
  app: a,
  loading,
  streaming,
  toggleStreaming,
  lines,
  clearLines,
  podRows,
  selectedPod,
  setPickedPod,
  podContainers,
  selectedContainer,
  setPickedContainer,
  podsLoading,
  noPods,
  logExport,
  deploymentsHref,
  tabs,
}: AppLogsScreenProps) {
  const tCommon = useTranslations("apps.common");
  const t = useTranslations("apps.logs");
  const tObs = useTranslations("apps.observability");

  const [exportOpen, setExportOpen] = React.useState(false);

  if (loading && !a) {
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
      {tabs}

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
            <Button size="sm" variant="outline" onClick={clearLines} disabled={lines.length === 0}>
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
              onClick={toggleStreaming}
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
              actionHref={deploymentsHref}
              actionLabel={tObs("pods.emptyAction")}
            />
          ) : (
            <LogViewer
              lines={lines}
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
