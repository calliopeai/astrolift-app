"use client";

import { useQuery, useSubscription } from "@apollo/client/react";
import {
  AlertTriangleIcon,
  BoxIcon,
  CheckIcon,
  CopyIcon,
  DownloadIcon,
  ExternalLinkIcon,
  PauseIcon,
  PlayIcon,
  ScrollTextIcon,
  TerminalIcon,
  Trash2Icon,
} from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { EmptyState } from "@/components/EmptyState";
import { LogViewer } from "@/components/observability";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
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
import { LIST_APP_PODS } from "@/graphql/lifecycle/lifecycle.queries";
import { ON_APP_LOG } from "@/graphql/lifecycle/lifecycle.subscriptions";
import type { AstroliftAppLogLine, AstroliftAppPod } from "@/graphql/lifecycle/lifecycle.types";
import { GET_APP } from "@/graphql/registry/registry.queries";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";

import { AppTabs } from "../components/app-tabs";

interface AppResp {
  astroliftApp: AstroliftRegisteredApp | null;
}

interface PodsResp {
  astroliftAppPods: AstroliftAppPod[];
}

interface LogResp {
  astroliftOnAppLog: AstroliftAppLogLine;
}

// Same defaults the observability page uses — keep them in sync so an
// operator switching tabs gets identical buffer behaviour.
const POD_POLL_MS = 5000;
const LOG_BUFFER_LIMIT = 500;
const DEFAULT_TAIL_LINES = 200;

const KNOWN_SIDECARS = new Set([
  "istio-proxy",
  "envoy",
  "linkerd-proxy",
  "datadog-agent",
  "otel-collector",
  "otc-container",
  "newrelic-infrastructure",
  "fluent-bit",
  "fluentd",
  "filebeat",
  "vault-agent",
  "vault-agent-init",
]);

function pickDefaultContainer(
  containers: string[],
  workload: string | null | undefined
): string | null {
  if (containers.length === 0) return null;
  if (workload) {
    const match = containers.find((c) => c === workload);
    if (match) return match;
  }
  const nonSidecar = containers.find((c) => !KNOWN_SIDECARS.has(c));
  return nonSidecar ?? containers[0];
}

export function ConsoleClient({ slug }: { slug: string }) {
  const tCommon = useTranslations("apps.common");
  const t = useTranslations("apps.console");
  const tObs = useTranslations("apps.observability");
  const app = useQuery<AppResp>(GET_APP, { variables: { slug } });
  const a = app.data?.astroliftApp;

  const pods = useQuery<PodsResp>(LIST_APP_PODS, {
    variables: { appSlug: slug },
    pollInterval: POD_POLL_MS,
    fetchPolicy: "cache-and-network",
  });

  const podRows: AstroliftAppPod[] = React.useMemo(
    () => pods.data?.astroliftAppPods ?? [],
    [pods.data]
  );

  const [pickedPod, setPickedPod] = React.useState<string | null>(null);
  const selectedPod: string | null = React.useMemo(() => {
    if (pickedPod && podRows.some((p) => p.name === pickedPod)) return pickedPod;
    const running = podRows.find((p) => p.status === "Running");
    return running?.name ?? podRows[0]?.name ?? null;
  }, [pickedPod, podRows]);

  const podContainers: string[] = React.useMemo(() => {
    const pod = podRows.find((p) => p.name === selectedPod);
    return pod?.containerStatuses.map((c) => c.name) ?? [];
  }, [podRows, selectedPod]);
  const selectedPodWorkload = React.useMemo(
    () => podRows.find((p) => p.name === selectedPod)?.workload ?? null,
    [podRows, selectedPod]
  );

  const [pickedContainer, setPickedContainer] = React.useState<string | null>(null);
  const selectedContainer: string | null = React.useMemo(() => {
    if (pickedContainer && podContainers.includes(pickedContainer)) {
      return pickedContainer;
    }
    return pickDefaultContainer(podContainers, selectedPodWorkload);
  }, [pickedContainer, podContainers, selectedPodWorkload]);

  const [streaming, setStreaming] = React.useState(false);
  const [logBuffer, setLogBuffer] = React.useState<AstroliftAppLogLine[]>([]);

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

  const sampleCommands = React.useMemo(
    () => [
      {
        key: "openShell" as const,
        label: t("shortcuts.openShell"),
        template: (s: string) => `astro exec --app=${s} -- bash`,
      },
      {
        key: "djangoCmd" as const,
        label: t("shortcuts.djangoCmd"),
        template: (s: string) => `astro exec --app=${s} -- python manage.py migrate --check`,
      },
      {
        key: "tailLogs" as const,
        label: t("shortcuts.tailLogs"),
        template: (s: string) => `astro logs --app=${s} --follow`,
      },
    ],
    [t]
  );

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

  const podsLoading = pods.loading && podRows.length === 0;
  const noPods = !podsLoading && podRows.length === 0;

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

      {/* ─── live logs (pod + container picker + LogViewer) ──────────── */}
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
                  <code className="bg-muted rounded px-1 py-0.5 font-mono text-[11px]">
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
              actionHref={`/apps/${a.slug}/deployments`}
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

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <TerminalIcon className="size-4" />
            {t("terminal.title")}
            <Badge variant="outline" className="text-[10px] uppercase">
              {t("terminal.comingSoon")}
            </Badge>
          </CardTitle>
          <CardDescription>{t("terminal.description")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="bg-muted/40 flex h-64 items-center justify-center rounded-md border border-dashed">
            <div className="text-muted-foreground flex flex-col items-center gap-2 text-center">
              <TerminalIcon className="size-8" />
              <p className="text-sm">{t("terminal.lands")}</p>
              <p className="max-w-md text-xs">
                {t.rich("terminal.untilThen", {
                  cli: () => (
                    <code className="bg-background rounded px-1 py-0.5 font-mono">astro</code>
                  ),
                })}
              </p>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button asChild>
              <Link href="/downloads">
                <DownloadIcon className="size-4" />
                {t("terminal.install")}
              </Link>
            </Button>
            <Button asChild variant="outline">
              <Link href={`/apps/${a.slug}/tokens`}>
                <ExternalLinkIcon className="size-4" />
                {t("terminal.manageTokens")}
              </Link>
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("shortcuts.title")}</CardTitle>
          <CardDescription>
            {t.rich("shortcuts.description", {
              login: () => (
                <code className="bg-muted rounded px-1 py-0.5 font-mono">astro login</code>
              ),
            })}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {sampleCommands.map((s) => (
            <CopyableCommand key={s.key} label={s.label} command={s.template(a.slug)} />
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("lands.title")}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <Item
            done
            label={t("lands.execLabel")}
            note={t("lands.execNote")}
            shippedLabel={t("lands.shipped")}
          />
          <Item
            label={t("lands.bridgeLabel")}
            note={t("lands.bridgeNote")}
            shippedLabel={t("lands.shipped")}
          />
          <Item
            label={t("lands.webLabel")}
            note={t("lands.webNote")}
            shippedLabel={t("lands.shipped")}
          />
        </CardContent>
      </Card>
    </PageShell>
  );
}

function CopyableCommand({ label, command }: { label: string; command: string }) {
  return (
    <div>
      <div className="text-muted-foreground mb-1 text-xs tracking-wide uppercase">{label}</div>
      <div className="bg-muted flex items-start gap-2 rounded-md p-2">
        <pre className="flex-1 overflow-x-auto font-mono text-xs leading-relaxed break-all whitespace-pre-wrap">
          {command}
        </pre>
        <CopyButton value={command} />
      </div>
    </div>
  );
}

function CopyButton({ value }: { value: string }) {
  const t = useTranslations("apps.console");
  const [copied, setCopied] = React.useState(false);
  return (
    <Button
      type="button"
      variant="ghost"
      size="icon"
      className="size-7 shrink-0"
      aria-label={t("copy")}
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(value);
          setCopied(true);
          toast.success(t("copied"));
          setTimeout(() => setCopied(false), 1200);
        } catch {
          toast.error(t("copyFailed"));
        }
      }}
    >
      {copied ? <CheckIcon className="size-3.5" /> : <CopyIcon className="size-3.5" />}
    </Button>
  );
}

function Item({
  done,
  label,
  note,
  shippedLabel,
}: {
  done?: boolean;
  label: string;
  note: string;
  shippedLabel: string;
}) {
  return (
    <div className="flex items-start gap-3">
      <div
        className={`mt-1 size-2 shrink-0 rounded-full ${done ? "bg-[var(--brand-primary)]" : "bg-muted-foreground/40"}`}
      />
      <div>
        <div className="text-sm font-medium">
          {label}
          {done && (
            <Badge
              variant="secondary"
              className="ml-2 bg-emerald-500/15 text-xs text-emerald-700 dark:text-emerald-300"
            >
              {shippedLabel}
            </Badge>
          )}
        </div>
        <div className="text-muted-foreground text-xs">{note}</div>
      </div>
    </div>
  );
}
