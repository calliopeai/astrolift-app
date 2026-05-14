"use client";

import { useQuery, useSubscription } from "@apollo/client/react";
import {
  ActivityIcon,
  AlertTriangleIcon,
  BoxIcon,
  ExternalLinkIcon,
  PauseIcon,
  PlayIcon,
  ScrollTextIcon,
  TerminalIcon,
  Trash2Icon,
} from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { LIST_APP_PODS } from "@/graphql/lifecycle/lifecycle.queries";
import { ON_APP_LOG } from "@/graphql/lifecycle/lifecycle.subscriptions";
import type {
  AstroliftAppLogLine,
  AstroliftAppPod,
} from "@/graphql/lifecycle/lifecycle.types";
import { LIST_EVENTS } from "@/graphql/operations/operations.queries";
import { GET_APP } from "@/graphql/registry/registry.queries";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";

import { AppTabs } from "../components/app-tabs";

interface AppResp {
  astroliftApp: AstroliftRegisteredApp | null;
}

interface EventRow {
  id: string;
  eventType: string;
  payload: Record<string, unknown>;
  organizationId: string | null;
  registeredAppId: string | null;
  occurredAt: string;
}

interface EventsResp {
  astroliftEvents: EventRow[];
}

interface PodsResp {
  astroliftAppPods: AstroliftAppPod[];
}

interface LogResp {
  astroliftOnAppLog: AstroliftAppLogLine;
}

// Live cluster surface — pods refetch periodically as a safety net
// against missed subscription events (the deploy.lifecycle stream
// is the canonical 'something changed' signal but the runtime
// cluster itself doesn't push us pod-state events).
const POD_POLL_MS = 5000;
const LOG_BUFFER_LIMIT = 500;
const DEFAULT_TAIL_LINES = 200;

// Roll the backend's surface status string into the brand StatusDot
// palette. Anything not enumerated here renders as a muted dot so the
// row still parses visually.
function statusToDot(status: string): "ok" | "warn" | "error" | "muted" | "pending" {
  switch (status) {
    case "Running":
      return "ok";
    case "Succeeded":
      return "muted";
    case "Pending":
      return "pending";
    case "Failed":
    case "CrashLoopBackOff":
    case "ImagePullBackOff":
    case "ErrImagePull":
    case "CreateContainerConfigError":
    case "InvalidImageName":
    case "CreateContainerError":
      return "error";
    default:
      return "muted";
  }
}

function formatAge(iso: string | null | undefined): string {
  if (!iso) return "—";
  const ms = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(ms) || ms < 0) return "—";
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h}h`;
  const d = Math.floor(h / 24);
  return `${d}d`;
}

function formatLogLine(line: AstroliftAppLogLine): string {
  const ts = new Date(line.timestamp).toISOString();
  const pod = line.podName.length > 30 ? line.podName.slice(-30) : line.podName;
  const container = line.container ? ` (${line.container})` : "";
  return `[${ts}] ${pod}${container} ${line.message}`;
}

export function ObservabilityClient({ slug }: { slug: string }) {
  const app = useQuery<AppResp>(GET_APP, { variables: { slug } });
  const events = useQuery<EventsResp>(LIST_EVENTS, {
    variables: { limit: 200 },
    pollInterval: 15000,
  });

  const pods = useQuery<PodsResp>(LIST_APP_PODS, {
    variables: { appSlug: slug },
    pollInterval: POD_POLL_MS,
  });

  const a = app.data?.astroliftApp;
  // Memo guards podRows so its identity is stable when the response
  // is undefined / unchanged — useEffect deps below depend on it.
  const podRows: AstroliftAppPod[] = React.useMemo(
    () => pods.data?.astroliftAppPods ?? [],
    [pods.data],
  );

  const appEvents = React.useMemo(() => {
    const all = events.data?.astroliftEvents ?? [];
    if (!a) return [];
    return all.filter((e) => e.registeredAppId === a.id);
  }, [events.data, a]);

  // Operator picks one pod to tail at a time. Track only the
  // *user-overridden* selection in state; derive the effective
  // pod for the subscription from the pod list so we don't have
  // to setState inside an effect when the list changes.
  const [pickedPod, setPickedPod] = React.useState<string | null>(null);
  const selectedPod: string | null = React.useMemo(() => {
    if (pickedPod && podRows.some((p) => p.name === pickedPod)) return pickedPod;
    const running = podRows.find((p) => p.status === "Running");
    return running?.name ?? podRows[0]?.name ?? null;
  }, [pickedPod, podRows]);

  const [streaming, setStreaming] = React.useState(false);
  const [logBuffer, setLogBuffer] = React.useState<string[]>([]);
  const logRef = React.useRef<HTMLPreElement | null>(null);
  // Clearing the buffer when the operator switches pods is done at
  // selection-time via a callback rather than in an effect, which
  // sidesteps the cascading-render lint and is the React-recommended
  // shape for "reset state on a different key".
  const lastTailedPod = React.useRef<string | null>(null);
  if (lastTailedPod.current !== selectedPod) {
    lastTailedPod.current = selectedPod;
    if (logBuffer.length !== 0) {
      // Set during render is acceptable when guarded — React batches
      // and replays the render with the new state.
      setLogBuffer([]);
    }
  }

  // Live log subscription — only opens while ``streaming`` is true
  // and we have a pod to tail. ``skip`` prevents an Apollo socket
  // from opening on initial render and on Pause.
  useSubscription<LogResp>(ON_APP_LOG, {
    variables: {
      appSlug: slug,
      podName: selectedPod ?? "",
      follow: true,
      tailLines: DEFAULT_TAIL_LINES,
    },
    skip: !streaming || !selectedPod,
    onData: ({ data }) => {
      const line = data.data?.astroliftOnAppLog;
      if (!line) return;
      setLogBuffer((prev) => {
        const next = [...prev, formatLogLine(line)];
        // Cap memory so an hours-long tail doesn't grow unbounded.
        return next.length > LOG_BUFFER_LIMIT
          ? next.slice(-LOG_BUFFER_LIMIT)
          : next;
      });
    },
  });

  // Autoscroll the log pane as new lines arrive — operators expect a
  // tail-like UX. Easy to defeat by scrolling up (we only auto-snap
  // when the user is already near the bottom).
  React.useEffect(() => {
    const el = logRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    if (nearBottom) el.scrollTop = el.scrollHeight;
  }, [logBuffer]);

  if (app.loading && !a) {
    return (
      <PageShell title="Loading…">
        <Skeleton className="h-32 w-full" />
      </PageShell>
    );
  }

  if (!a) {
    return (
      <PageShell title="App not found">
        <EmptyState
          icon={<AlertTriangleIcon className="size-5" />}
          title={`No app with slug ${slug}`}
          description="It may have been soft-deleted, or you may not have permission to read it."
          actionHref="/apps"
          actionLabel="Back to apps"
        />
      </PageShell>
    );
  }

  const podsLoading = pods.loading && podRows.length === 0;

  return (
    <PageShell
      title={`${a.name} · Observability`}
      description={
        <span className="text-muted-foreground font-mono text-xs">
          {a.slug} · pod state and log stream from the runtime cluster
        </span>
      }
    >
      <AppTabs slug={a.slug} active="observability" />

      {/* ─── pod list ──────────────────────────────────────────────────── */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <BoxIcon className="size-4" /> Pods
          </CardTitle>
          <CardDescription>
            Live pod state from the tenant cluster, refreshed every{" "}
            {POD_POLL_MS / 1000}s. Click a row to tail its logs below.
          </CardDescription>
        </CardHeader>
        <CardContent className="p-0">
          {podsLoading ? (
            <div className="space-y-2 p-6">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </div>
          ) : podRows.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<BoxIcon className="size-5" />}
                title="No pods running for this app"
                description="Once a deployment has rolled out and the cluster reports pods, they appear here. If you expected pods, check the deployment log."
                actionHref={`/apps/${a.slug}/deployments`}
                actionLabel="View deployment history"
              />
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Pod</TableHead>
                  <TableHead>Workload</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Ready</TableHead>
                  <TableHead className="text-right">Restarts</TableHead>
                  <TableHead className="text-right">Age</TableHead>
                  <TableHead>Node</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {podRows.map((pod) => {
                  const readyCount = pod.containerStatuses.filter(
                    (c) => c.ready,
                  ).length;
                  const total = pod.containerStatuses.length;
                  const isSelected = pod.name === selectedPod;
                  return (
                    <TableRow
                      key={pod.name}
                      onClick={() => setPickedPod(pod.name)}
                      data-selected={isSelected}
                      className="hover:bg-muted/40 cursor-pointer data-[selected=true]:bg-muted/60"
                    >
                      <TableCell className="font-mono text-xs">
                        {pod.name}
                      </TableCell>
                      <TableCell className="font-mono text-xs">
                        {pod.workload || "—"}
                      </TableCell>
                      <TableCell>
                        <span className="inline-flex items-center gap-2 text-xs">
                          <StatusDot status={statusToDot(pod.status)} />
                          <span>{pod.status}</span>
                          {pod.status !== pod.phase && pod.phase && (
                            <span className="text-muted-foreground font-mono">
                              ({pod.phase})
                            </span>
                          )}
                        </span>
                      </TableCell>
                      <TableCell className="text-right font-mono text-xs">
                        {readyCount}/{total || 0}
                      </TableCell>
                      <TableCell className="text-right font-mono text-xs">
                        {pod.restarts}
                      </TableCell>
                      <TableCell className="text-right font-mono text-xs">
                        {formatAge(pod.age)}
                      </TableCell>
                      <TableCell className="text-muted-foreground font-mono text-xs">
                        {pod.node || "—"}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* ─── log viewer ────────────────────────────────────────────────── */}
      <Card>
        <CardHeader className="flex flex-row items-start justify-between gap-3 space-y-0 pb-3">
          <div>
            <CardTitle className="flex items-center gap-2 text-base">
              <TerminalIcon className="size-4" /> Live logs
            </CardTitle>
            <CardDescription>
              {selectedPod ? (
                <>
                  Streaming{" "}
                  <code className="bg-muted rounded px-1 py-0.5 font-mono text-[11px]">
                    {selectedPod}
                  </code>{" "}
                  from the runtime cluster.
                </>
              ) : (
                "Select a pod above to tail its log stream."
              )}
            </CardDescription>
          </div>
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="outline"
              onClick={() => setLogBuffer([])}
              disabled={logBuffer.length === 0}
            >
              <Trash2Icon className="size-3" /> Clear
            </Button>
            <Button
              size="sm"
              variant={streaming ? "outline" : "default"}
              onClick={() => setStreaming((s) => !s)}
              disabled={!selectedPod}
            >
              {streaming ? (
                <>
                  <PauseIcon className="size-3" /> Pause
                </>
              ) : (
                <>
                  <PlayIcon className="size-3" /> Stream
                </>
              )}
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          <pre
            ref={logRef}
            className="bg-muted/40 h-72 overflow-auto rounded-md border p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap"
          >
            {logBuffer.length === 0 ? (
              <span className="text-muted-foreground italic">
                {streaming
                  ? "Waiting for the first line…"
                  : selectedPod
                    ? 'Press "Stream" to start tailing.'
                    : "Pick a pod above first."}
              </span>
            ) : (
              logBuffer.join("\n")
            )}
          </pre>
          <p className="text-muted-foreground mt-2 text-xs">
            Buffer is capped at {LOG_BUFFER_LIMIT} lines. Use the CLI for
            a true tail without the buffer cap:{" "}
            <code className="bg-muted rounded px-1 py-0.5 font-mono text-[11px]">
              astro logs --app={a.slug} --follow
            </code>
            . <Link href="/downloads" className="underline">Install the CLI</Link>.
          </p>
        </CardContent>
      </Card>

      {/* ─── platform events for this app ──────────────────────────────── */}
      <Card>
        <CardHeader className="flex flex-row items-start justify-between gap-3 space-y-0 pb-3">
          <div>
            <CardTitle className="flex items-center gap-2 text-base">
              <ActivityIcon className="size-4" /> Recent platform events
            </CardTitle>
            <CardDescription>
              App-scoped slice of the platform event stream — deploy
              lifecycle, scaling decisions, alert transitions.
            </CardDescription>
          </div>
          <Link
            href="/events"
            className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-xs"
          >
            All events <ExternalLinkIcon className="size-3" />
          </Link>
        </CardHeader>
        <CardContent className="p-0">
          {events.loading && appEvents.length === 0 ? (
            <div className="space-y-2 p-6">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </div>
          ) : appEvents.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<ScrollTextIcon className="size-5" />}
                title="No events for this app yet"
                description="Platform events appear here as deploys, scale changes, and alerts fire."
              />
            </div>
          ) : (
            <ul className="divide-y">
              {appEvents.slice(0, 25).map((e) => (
                <li key={e.id} className="px-6 py-2 text-sm">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-muted-foreground w-44 shrink-0 font-mono text-xs">
                      {new Date(e.occurredAt).toLocaleString()}
                    </span>
                    <Badge variant="outline" className="font-mono text-xs">
                      {e.eventType}
                    </Badge>
                  </div>
                  {Object.keys(e.payload).length > 0 && (
                    <pre className="text-muted-foreground mt-1 ml-44 overflow-x-auto font-mono text-[11px]">
                      {JSON.stringify(e.payload, null, 2)}
                    </pre>
                  )}
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </PageShell>
  );
}
