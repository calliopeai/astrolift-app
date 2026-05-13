"use client";

import { useQuery } from "@apollo/client/react";
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
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
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

// The pod list and live log surface depend on backend GraphQL fields
// that don't exist yet (astroliftAppPods / astroliftAppLogs subscription
// or HTTP endpoint). Until they land, we render an empty pod table and
// an opt-in log viewer that subscribes when the runtime stream is wired,
// plus the existing platform event stream filtered to this app.
const LOG_POLL_MS = 3000;
const LOG_BUFFER_LIMIT = 500;

export function ObservabilityClient({ slug }: { slug: string }) {
  const app = useQuery<AppResp>(GET_APP, { variables: { slug } });
  const events = useQuery<EventsResp>(LIST_EVENTS, {
    variables: { limit: 200 },
    pollInterval: 15000,
  });

  const a = app.data?.astroliftApp;
  const appEvents = React.useMemo(() => {
    const all = events.data?.astroliftEvents ?? [];
    if (!a) return [];
    return all.filter((e) => e.registeredAppId === a.id);
  }, [events.data, a]);

  const [streaming, setStreaming] = React.useState(false);
  const [logBuffer, setLogBuffer] = React.useState<string[]>([]);
  const logRef = React.useRef<HTMLPreElement | null>(null);

  // Placeholder log stream: until the backend exposes a real log
  // subscription, surface the platform event stream's deploy lifecycle
  // events as readable log lines. When the WS endpoint lands, swap this
  // out for useSubscription on onAppLog or equivalent.
  React.useEffect(() => {
    if (!streaming) return;
    const tick = () => {
      if (!a) return;
      setLogBuffer((prev) => {
        const next = [...prev];
        for (const e of appEvents.slice(0, 10)) {
          const line = `[${new Date(e.occurredAt).toISOString()}] ${e.eventType} ${JSON.stringify(e.payload)}`;
          if (!next.includes(line)) next.push(line);
        }
        // Cap memory so the buffer doesn't grow unbounded over hours.
        return next.length > LOG_BUFFER_LIMIT
          ? next.slice(-LOG_BUFFER_LIMIT)
          : next;
      });
    };
    tick();
    const handle = window.setInterval(tick, LOG_POLL_MS);
    return () => window.clearInterval(handle);
  }, [streaming, a, appEvents]);

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
            Live pod state from the tenant cluster. Empty until the
            astroliftAppPods resolver wires through to the runtime.
          </CardDescription>
        </CardHeader>
        <CardContent className="p-0">
          <div className="p-6">
            <EmptyState
              icon={<BoxIcon className="size-5" />}
              title="Live pod data not yet wired"
              description="The platform tracks deployment-level rollout state today. Per-pod readiness, restarts, and CrashLoopBackOff diagnostics arrive once the runtime exposes them."
              actionHref={`/apps/${a.slug}/deployments`}
              actionLabel="View deployment history"
            />
          </div>
          {/* Render the table shell so the layout doesn't jump when the
              backend lands and we hide the empty state. */}
          <Table className="hidden">
            <TableHeader>
              <TableRow>
                <TableHead>Pod</TableHead>
                <TableHead>Workload</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Ready</TableHead>
                <TableHead>Restarts</TableHead>
                <TableHead>Age</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody />
          </Table>
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
              Streaming output from the app&apos;s workloads. Shows the
              platform event stream until the cluster log subscription
              lands.
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
                  : 'Press "Stream" to start tailing.'}
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
