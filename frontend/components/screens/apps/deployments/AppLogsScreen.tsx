"use client";

import { QueryError } from "@/components/QueryError";

import {
  AlertTriangleIcon,
  BoxIcon,
  FileDownIcon,
  LayersIcon,
  PauseIcon,
  PlayIcon,
  Trash2Icon,
} from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { AppLogExportDialog } from "@/components/observability";
import type { useLogExport } from "@/components/observability/use-log-export";
import { PageShell } from "@/components/PageShell";
import { Panel } from "@/components/panel/Panel";
import { LogView } from "@/components/run/LogView";
import { Button } from "@/components/ui/button";
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

import { countLevels, filterLogLines, type LogLevelFilter, toLogLines } from "./app-log-lines";
import { LogFilters } from "./LogFilters";
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

const ALL_CONTAINERS = "__all__";

/**
 * Logs & metrics › Logs (spec 44 §5.2, §5.5): one pod's live log in the
 * shared LogView, which follows the end until the reader scrolls up. Pod,
 * container, level and text narrow what is shown; Download saves the whole
 * buffer and Export hands a longer window to the export dialog (#483).
 *
 * Reading only: the shell lives in the Console section (#1247).
 */
export function AppLogsScreen({
  slug,
  app: a,
  loading,
  appError,
  onRetryApp,
  streaming,
  toggleStreaming,
  lines,
  clearLines,
  error,
  retry,
  downloadLines,
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
  const tViewer = useTranslations("apps.logViewer");

  const [exportOpen, setExportOpen] = React.useState(false);
  const [level, setLevel] = React.useState<LogLevelFilter>("all");
  const [query, setQuery] = React.useState("");

  const mapped = React.useMemo(() => toLogLines(lines), [lines]);
  const shown = React.useMemo(() => filterLogLines(mapped, level, query), [mapped, level, query]);
  const counts = React.useMemo(() => countLevels(mapped), [mapped]);
  const filtered = shown.length !== mapped.length;

  if (loading && !a) {
    return (
      <PageShell title={tCommon("loading")}>
        <Skeleton className="h-32 w-full" />
      </PageShell>
    );
  }

  if (!a && appError) {
    return (
      <PageShell title="App logs">
        <QueryError title="Could not load app" error={appError} onRetry={onRetryApp} />
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
        selectedPod ? (
          <span className="font-mono text-xs [overflow-wrap:anywhere]">
            {tObs("logs.streaming")} {selectedPod} {tObs("logs.fromCluster")}
          </span>
        ) : (
          t("logs.selectPrompt")
        )
      }
      actions={
        <Button
          size="sm"
          variant="outline"
          onClick={() => setExportOpen(true)}
          disabled={!selectedPod}
          title={tObs("logs.exportTitle")}
        >
          <FileDownIcon className="size-3.5" /> {tObs("logs.export")}
        </Button>
      }
    >
      {tabs}

      {noPods ? (
        <Panel
          title={t("logs.title")}
          empty={{
            icon: <BoxIcon className="size-5" />,
            title: tObs("pods.emptyTitle"),
            description: tObs("pods.emptyDescription"),
            actionHref: deploymentsHref,
            actionLabel: tObs("pods.emptyAction"),
          }}
        />
      ) : (
        <div className="flex min-w-0 flex-col gap-3">
          <LogFilters
            level={level}
            onLevelChange={setLevel}
            query={query}
            onQueryChange={setQuery}
            counts={counts}
          >
            <PodPicker
              pods={podRows}
              value={selectedPod}
              onChange={setPickedPod}
              disabled={podsLoading}
              label={t("logs.podLabel")}
              placeholder={t("logs.podPlaceholder")}
            />
            {podContainers.length > 1 && (
              <div className="flex min-w-0 items-center gap-1.5">
                <LayersIcon aria-hidden className="text-muted-foreground size-3.5 shrink-0" />
                <Select
                  value={selectedContainer ?? ALL_CONTAINERS}
                  onValueChange={(v) => setPickedContainer(v === ALL_CONTAINERS ? null : v)}
                >
                  <SelectTrigger
                    size="sm"
                    aria-label={tViewer("containerLabel")}
                    className="max-w-full min-w-0 font-mono text-xs"
                  >
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={ALL_CONTAINERS}>{tViewer("containerAll")}</SelectItem>
                    {podContainers.map((c) => (
                      <SelectItem key={c} value={c} className="font-mono">
                        {c}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}
          </LogFilters>

          <LogView
            title={t("logs.title")}
            lines={shown}
            loading={podsLoading}
            error={error}
            onRetry={retry}
            onDownload={downloadLines}
            emptyHint={
              filtered
                ? tViewer("filteredEmpty")
                : streaming
                  ? tObs("logs.waiting")
                  : selectedPod
                    ? tObs("logs.pressStream")
                    : tObs("logs.pickPod")
            }
            actions={
              <>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={clearLines}
                  disabled={lines.length === 0}
                >
                  <Trash2Icon className="size-3.5" /> {tObs("logs.clear")}
                </Button>
                <Button
                  size="sm"
                  variant={streaming ? "outline" : "default"}
                  onClick={toggleStreaming}
                  disabled={!selectedPod}
                >
                  {streaming ? (
                    <>
                      <PauseIcon className="size-3.5" /> {tObs("logs.pause")}
                    </>
                  ) : (
                    <>
                      <PlayIcon className="size-3.5" /> {tObs("logs.stream")}
                    </>
                  )}
                </Button>
              </>
            }
          />

          <p className="text-muted-foreground text-xs [overflow-wrap:anywhere]">
            {tObs("logs.bufferCap", { limit: LOG_BUFFER_LIMIT })}{" "}
            <code className="font-mono">astro logs --app={a.slug} --follow</code>.{" "}
            <Link href="/downloads" className="underline">
              {tObs("logs.installCli")}
            </Link>
            .
          </p>
        </div>
      )}

      {/* #483 app-log export modal: vendor handoff and compliance. */}
      <AppLogExportDialog
        open={exportOpen}
        onOpenChange={setExportOpen}
        podName={selectedPod}
        {...logExport}
      />
    </PageShell>
  );
}

function PodPicker({
  pods,
  value,
  onChange,
  disabled,
  label,
  placeholder,
}: {
  pods: Pick<AstroliftAppPod, "name">[];
  value: string | null;
  onChange: (pod: string) => void;
  disabled: boolean;
  label: string;
  placeholder: string;
}) {
  return (
    <div className="flex min-w-0 items-center gap-1.5">
      <BoxIcon aria-hidden className="text-muted-foreground size-3.5 shrink-0" />
      <Select value={value ?? ""} onValueChange={onChange} disabled={disabled}>
        <SelectTrigger
          size="sm"
          aria-label={label}
          className="max-w-full min-w-0 font-mono text-xs"
        >
          <SelectValue placeholder={placeholder} />
        </SelectTrigger>
        <SelectContent>
          {pods.map((p) => (
            <SelectItem key={p.name} value={p.name} className="font-mono">
              {p.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
