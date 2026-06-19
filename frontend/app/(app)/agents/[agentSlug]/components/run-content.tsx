"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import {
  ExternalLinkIcon,
  ListChecksIcon,
  Loader2Icon,
  MonitorPlayIcon,
  ZapIcon,
} from "lucide-react";
import Link from "next/link";
import * as React from "react";
import { toast } from "sonner";

import { EmptyState } from "@/components/EmptyState";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { VncViewer } from "@/components/observability/VncViewer";
import { RUN_AGENT } from "@/graphql/agents/agents.mutations";
import { LIST_AGENT_TASKS } from "@/graphql/agents/agents.queries";
import type { AstroliftAgentListItem } from "@/graphql/agents/agents.types";
import { formatRelativeAge } from "@/lib/format";

// One agent execution. Mirrors the AgentTask row shape used by the fleet
// Active/History tables in `agents-client.tsx`; LIST_AGENT_TASKS is the same
// operation, here scoped to a single agent via `workloadId`.
interface AgentTask {
  id: string;
  status: string;
  callbackUrl: string;
  result: unknown;
  createdAt: string;
  startedAt: string | null;
  finishedAt: string | null;
  vncEnabled: boolean;
  vncUrl: string;
  snapshotUrl: string | null;
}
interface AgentTasksResp {
  agentTasks: AgentTask[];
}

interface RunAgentResp {
  runAstroliftAgent: {
    ok: boolean;
    errors: { code: string; message: string; field: string | null }[];
    data: { id: string; status: string; createdAt: string } | null;
  };
}

// Agent run vocabulary (spec 33 §6): running / queued / completed / failed /
// timed_out / cancelled. Reuses the dot+badge convention shared with the
// registry list (`agents-client.tsx`) — "completed" is the success state, not
// "succeeded". Unknown states degrade to a muted dot + outline badge.
type Dot = "ok" | "warn" | "error" | "muted" | "pending";
const RUN_STATUS_DOT: Record<string, Dot> = {
  running: "pending",
  queued: "warn",
  pending: "warn",
  completed: "ok",
  succeeded: "ok",
  failed: "error",
  timed_out: "error",
  cancelled: "muted",
  canceled: "muted",
};

function titleCase(value: string): string {
  if (!value) return "";
  return value
    .replace(/[_-]+/g, " ")
    .split(" ")
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
    .join(" ");
}

function isRunning(t: AgentTask): boolean {
  return t.status === "running";
}

// A task is watchable only while RUNNING on a VNC-capable pod that has
// published its relay path — mirrors the fleet table + the relay gate.
function canWatch(t: AgentTask): boolean {
  return t.status === "running" && t.vncEnabled && Boolean(t.vncUrl);
}

// Status cell: a live "running now" badge (animated pending dot) for in-flight
// runs, otherwise the dot+badge for its terminal/queued state.
function StatusCell({ task }: { task: AgentTask }) {
  if (isRunning(task)) {
    return (
      <Badge variant="default" className="gap-1.5">
        <StatusDot status="pending" />
        Running now
      </Badge>
    );
  }
  const key = task.status.toLowerCase();
  const dot = RUN_STATUS_DOT[key] ?? "muted";
  return (
    <Badge
      variant={dot === "error" ? "destructive" : "secondary"}
      className="gap-1.5 capitalize"
    >
      <StatusDot status={dot} />
      {titleCase(task.status)}
    </Badge>
  );
}

/**
 * Run tab content for an agent (spec 33 PR-10).
 *
 * Two surfaces:
 *   - **Dispatch now** — the working "Run once" control. Fires
 *     `runAstroliftAgent` (PR-1) for this agent's `slug`, then refetches the
 *     executions list so the new run appears with a live "running now" badge.
 *     This is the #896 unstub. Once-mode only — the run-spec editor
 *     (Schedule / Service / Loop / Trigger) is PR-11/12.
 *   - **Executions** — the per-agent runs list, scoped via
 *     `agentTasks(workloadId:)` (PR-2). Reuses the AgentTask table, the
 *     status→variant mapping, the 5s poll, and the VNC "Watch live" dialog
 *     from the fleet surface, narrowed to THIS agent. Polls so a freshly
 *     dispatched run (and its state transitions) shows without a reload.
 *
 * `agent.id` is the agent's Workload id — the same key `agentLiveStatus` is
 * merged on in the registry list — so it is what scopes `agentTasks`.
 */
export function RunContent({
  agent,
  orgId,
}: {
  agent: AstroliftAgentListItem;
  orgId: string;
}) {
  // Per-agent executions: every state, scoped to this Workload, polled like
  // the fleet Active tab so dispatch results + transitions surface live.
  const { data, loading, refetch } = useQuery<AgentTasksResp>(LIST_AGENT_TASKS, {
    variables: { orgId, status: null, workloadId: agent.id },
    skip: !orgId,
    pollInterval: 5000,
    fetchPolicy: "cache-and-network",
  });
  // The task whose live session is open in the Watch-live dialog.
  const [watching, setWatching] = React.useState<AgentTask | null>(null);

  const [runAgent, { loading: dispatching }] = useMutation<RunAgentResp>(RUN_AGENT);

  async function handleDispatch() {
    try {
      const { data: res } = await runAgent({
        variables: { input: { agentSlug: agent.slug } },
      });
      const result = res?.runAstroliftAgent;
      if (!result?.ok) {
        throw new Error(result?.errors?.[0]?.message ?? "Dispatch failed");
      }
      toast.success(`Dispatched ${agent.name}`);
      // Pull the new run into the list immediately; the 5s poll then tracks
      // its state. awaitRefetchQueries-style: we await so the row is present
      // before the success toast settles.
      await refetch();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error(`Couldn't dispatch ${agent.name}`, { description: message });
    }
  }

  // Sort newest-first by createdAt so a just-dispatched run lands at the top.
  const rows = React.useMemo(
    () =>
      [...(data?.agentTasks ?? [])].sort((a, b) =>
        (b.createdAt ?? "").localeCompare(a.createdAt ?? "")
      ),
    [data?.agentTasks]
  );

  return (
    <div className="space-y-6">
      {/* Dispatch now — the working Once-mode control (#896 unstub). */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <ZapIcon className="size-4" />
            Dispatch now
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-muted-foreground text-sm">
            Run this agent once, on demand. A new execution is created and picked up by
            the dispatch pipeline — it appears below with a live status. Scheduling,
            looping, and always-on Service modes are configured on the Control tab.
          </p>
          <Button onClick={handleDispatch} disabled={dispatching}>
            {dispatching ? (
              <Loader2Icon className="size-4 animate-spin" />
            ) : (
              <ZapIcon className="size-4" />
            )}
            Run once
          </Button>
        </CardContent>
      </Card>

      {/* Executions — per-agent runs list (scoped via workloadId). */}
      <section className="space-y-3">
        <div className="flex items-center gap-2">
          <ListChecksIcon className="text-muted-foreground size-4" />
          <h2 className="text-base font-semibold">Executions</h2>
          <span className="text-muted-foreground text-xs">
            Runs of this agent · running / queued / completed / failed / timed&nbsp;out
          </span>
        </div>

        {loading && rows.length === 0 ? (
          <Card>
            <CardContent className="space-y-2 p-6">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </CardContent>
          </Card>
        ) : rows.length === 0 ? (
          <Card>
            <CardContent className="p-6">
              <EmptyState
                icon={<ListChecksIcon className="size-5" />}
                title="No executions yet"
                description="This agent hasn't run yet. Use Dispatch now to trigger a run — it will appear here with a live status."
              />
            </CardContent>
          </Card>
        ) : (
          <Card>
            <CardContent className="p-0">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>ID</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Started</TableHead>
                    <TableHead>Finished</TableHead>
                    <TableHead className="text-right">Live</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((t) => (
                    <TableRow key={t.id}>
                      <TableCell className="font-mono text-xs">{t.id}</TableCell>
                      <TableCell>
                        <StatusCell task={t} />
                      </TableCell>
                      <TableCell className="text-muted-foreground text-sm">
                        {t.startedAt ? (
                          <span title={t.startedAt}>{formatRelativeAge(t.startedAt)}</span>
                        ) : (
                          "—"
                        )}
                      </TableCell>
                      <TableCell className="text-muted-foreground text-sm">
                        {t.finishedAt ? (
                          <span title={t.finishedAt}>{formatRelativeAge(t.finishedAt)}</span>
                        ) : (
                          "—"
                        )}
                      </TableCell>
                      <TableCell className="text-right">
                        {canWatch(t) && (
                          <div className="inline-flex items-center gap-2">
                            <Button
                              size="sm"
                              variant="outline"
                              onClick={() => setWatching(t)}
                            >
                              <MonitorPlayIcon className="size-4" />
                              Watch live
                            </Button>
                            <Button asChild size="sm" variant="ghost">
                              <Link
                                href={`/agents/runs/${encodeURIComponent(t.id)}/vnc`}
                                target="_blank"
                                rel="noreferrer"
                                aria-label="Open live session in a new tab"
                              >
                                <ExternalLinkIcon className="size-4" />
                              </Link>
                            </Button>
                          </div>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        )}
      </section>

      {/* Watch-live VNC dialog — reuses VncViewer, the same surface the fleet
          Active tab and the relocated agents/runs/[task]/vnc/ popout use. */}
      <Dialog open={watching !== null} onOpenChange={(open) => !open && setWatching(null)}>
        <DialogContent className="max-w-4xl sm:max-w-4xl">
          <DialogHeader>
            <DialogTitle>Live agent session</DialogTitle>
            <DialogDescription className="font-mono text-xs">
              {watching?.id}
            </DialogDescription>
          </DialogHeader>
          {watching && <VncViewer vncPath={watching.vncUrl} />}
        </DialogContent>
      </Dialog>
    </div>
  );
}
