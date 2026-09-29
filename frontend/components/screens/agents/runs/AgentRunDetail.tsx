"use client";

import {
  BracesIcon,
  CopyIcon,
  ExternalLinkIcon,
  InfoIcon,
  MonitorPlayIcon,
  MoreHorizontalIcon,
  ScrollIcon,
  WaypointsIcon,
  XCircleIcon,
} from "lucide-react";
import Link from "next/link";
import * as React from "react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { DetailTimestamp } from "@/components/detail/EntityDetailShell";
import { Identifier } from "@/components/Identifier";
import { VncViewer } from "@/components/observability/VncViewer";
import { Panel, PanelGrid } from "@/components/panel/Panel";
import { RunPage } from "@/components/run/RunPage";
import { outcomeOf } from "@/components/screens/administration/insights/combined-runs";
import { RunMissing } from "@/components/screens/jobs/RunDetailParts";
import { runCrumbs } from "@/components/screens/tasks/runs-list";
import { RunOutcomeCell } from "@/components/screens/tasks/RunsScreen";
import { Button } from "@/components/ui/button";
import { DefinitionList } from "@/components/ui/definition-list";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";

import { agentLogLines, agentRunSteps } from "./agent-run-steps";
import { AgentInteractionMapView } from "./AgentInteractionMap";
import type { AgentInteractionMapState } from "./use-agent-interaction-map";
import type { AgentRunDetailState } from "./use-agent-run-detail";

export interface AgentRunDetailProps extends AgentRunDetailState {
  /** The run's interactions, read once: the Timeline's calls and the map's nodes. */
  interactions: AgentInteractionMapState;
}

function prettyJson(v: unknown): string {
  try {
    return JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}

function span(
  from: string | null | undefined,
  to: string | number | null | undefined
): number | null {
  if (!from || to == null) return null;
  const d = (typeof to === "number" ? to : Date.parse(to)) - Date.parse(from);
  return Number.isFinite(d) && d >= 0 ? d : null;
}

type Open = "map" | "live" | null;

/**
 * One agent run on the run archetype (spec 44 §5.5, #1105): its lifecycle
 * and the tool calls, gates and signals it made on the Timeline, the log
 * tail in the LogView, a spawn failure's reason first. Details and the
 * result sit in two panels under it; the interaction map and the live VNC
 * session open in a sheet from the Details panel or the `⋯` menu instead
 * of stacking the page. Pure view; the data half is useAgentRunDetail and
 * useAgentInteractionMap.
 */
export function AgentRunDetail({
  taskId,
  task,
  loading,
  error,
  onRetry,
  terminal,
  now,
  logs,
  logsLoading,
  logsError,
  onRetryLogs,
  onDownloadLogs,
  onHardStop,
  interactions,
}: AgentRunDetailProps) {
  const [killOpen, setKillOpen] = React.useState(false);
  const [open, setOpen] = React.useState<Open>(null);
  const title = `run ${taskId.slice(0, 8)}`;
  const crumbs = runCrumbs({ label: title });

  if (!task && loading) {
    return (
      <RunPage
        crumbs={crumbs}
        title={title}
        steps={[]}
        stepsLoading
        log={{ lines: [], loading: true }}
      />
    );
  }
  if (!task) {
    return (
      <RunMissing
        crumbs={crumbs}
        title="Agent run"
        icon={<ScrollIcon className="size-4" />}
        error={error ? { message: error } : null}
        onRetry={onRetry}
        empty={{
          icon: <ScrollIcon className="size-5" />,
          title: "Run not found",
          description: "This agent run may not exist, or you may not have access to it.",
          actionHref: "/tasks?kind=agent",
          actionLabel: "Open agent runs",
        }}
      />
    );
  }

  const canWatch = task.status === "running" && task.vncEnabled && Boolean(task.vncUrl);
  const vncPopout = `/agents/runs/${encodeURIComponent(taskId)}/vnc`;
  // The spawn/dispatch failure reason (null unless the run failed). A
  // spawn-failed run never starts a pod, so its log is empty and this is the
  // only debug signal: it leads the Timeline panel.
  const failureMessage = task.failureMessage?.trim() ? task.failureMessage : null;
  const outcome = outcomeOf("agent", task.status);
  const calls = interactions.interactions.filter((i) => i.kind !== "control_api").length;

  const menu = (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="icon" className="size-8" aria-label="More actions">
          <MoreHorizontalIcon className="size-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-44">
        <DropdownMenuItem onSelect={() => setOpen("map")}>
          <WaypointsIcon className="size-4" />
          Interaction map
        </DropdownMenuItem>
        {canWatch && (
          <DropdownMenuItem onSelect={() => setOpen("live")}>
            <MonitorPlayIcon className="size-4" />
            Watch live
          </DropdownMenuItem>
        )}
        <DropdownMenuItem
          onSelect={() => {
            navigator.clipboard
              .writeText(task.id)
              .then(() => toast.success("Run ID copied."))
              .catch(() => toast.error("Couldn't copy to clipboard."));
          }}
        >
          <CopyIcon className="size-4" />
          Copy run ID
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-6">
      <RunPage
        crumbs={crumbs}
        title={title}
        status={<RunOutcomeCell run={{ outcome, status: task.status }} />}
        durationMs={span(task.startedAt, task.finishedAt ?? (terminal ? null : now))}
        context={
          <span className="font-mono">
            created <DetailTimestamp iso={task.createdAt} />
          </span>
        }
        primaryAction={
          !terminal ? (
            <Button size="sm" variant="destructive" onClick={() => setKillOpen(true)}>
              <XCircleIcon className="size-4" />
              Kill agent
            </Button>
          ) : undefined
        }
        menu={menu}
        steps={agentRunSteps(task, interactions.interactions, now)}
        stepsError={interactions.error}
        failure={failureMessage ? { title: "Run failed", reason: failureMessage } : null}
        log={{
          title: "Log",
          lines: agentLogLines(logs, task.startedAt ?? task.createdAt),
          loading: logsLoading,
          error: logsError,
          onRetry: onRetryLogs,
          onDownload: logs.length > 0 ? onDownloadLogs : undefined,
          emptyHint: failureMessage
            ? "No pod logs: the run failed before a pod started. The reason is on the left."
            : terminal
              ? "This run wrote no log output."
              : "No output yet. Lines appear here once the run's pod writes them.",
          actions: canWatch ? (
            <Button type="button" size="sm" variant="outline" onClick={() => setOpen("live")}>
              <MonitorPlayIcon className="size-4" />
              Watch live
            </Button>
          ) : undefined,
        }}
      />

      <PanelGrid>
        <Panel
          title="Details"
          icon={<InfoIcon className="size-4" />}
          span={6}
          actions={
            <Button type="button" size="sm" variant="outline" onClick={() => setOpen("map")}>
              <WaypointsIcon className="size-4" />
              Interaction map
              <span className="text-muted-foreground font-mono tabular-nums">{calls}</span>
            </Button>
          }
        >
          <DefinitionList
            items={[
              { term: "Run ID", description: <Identifier value={task.id} form="full" /> },
              { term: "Created", description: <DetailTimestamp iso={task.createdAt} /> },
              { term: "Started", description: <DetailTimestamp iso={task.startedAt} /> },
              { term: "Finished", description: <DetailTimestamp iso={task.finishedAt} /> },
              {
                term: "Pod",
                description: (
                  <span className="font-mono text-xs [overflow-wrap:anywhere]">
                    {task.podName || "—"}
                  </span>
                ),
              },
              {
                term: "Namespace",
                description: (
                  <span className="font-mono text-xs [overflow-wrap:anywhere]">
                    {task.namespace || "—"}
                  </span>
                ),
              },
              {
                term: "Callback URL",
                description: (
                  <span className="font-mono text-xs [overflow-wrap:anywhere]">
                    {task.callbackUrl || "—"}
                  </span>
                ),
              },
              {
                term: "Live session",
                description: task.vncEnabled ? (
                  canWatch ? (
                    "live now"
                  ) : (
                    "VNC-capable"
                  )
                ) : (
                  <span className="text-muted-foreground">—</span>
                ),
              },
            ]}
          />
        </Panel>
        <Panel
          title="Result"
          icon={<BracesIcon className="size-4" />}
          span={6}
          empty={
            task.result == null
              ? {
                  icon: <BracesIcon className="size-5" />,
                  title: "No result yet",
                  description: "A finished run records its output payload here.",
                }
              : null
          }
        >
          {task.result != null && (
            <pre className="bg-muted/40 max-h-80 overflow-auto rounded-sm border p-3 font-mono text-xs [overflow-wrap:anywhere] whitespace-pre-wrap">
              {prettyJson(task.result)}
            </pre>
          )}
        </Panel>
      </PanelGrid>

      <Sheet open={open !== null} onOpenChange={(next) => !next && setOpen(null)}>
        <SheetContent side="right" className="flex w-full flex-col gap-4 sm:max-w-3xl">
          <SheetHeader>
            <SheetTitle>{open === "live" ? "Live agent session" : "Interaction map"}</SheetTitle>
            <SheetDescription className="font-mono text-xs [overflow-wrap:anywhere]">
              {open === "live"
                ? task.id
                : "Control-plane activity: the API, tool calls, gates and signals."}
            </SheetDescription>
          </SheetHeader>
          <div className="min-h-0 flex-1 overflow-auto px-4 pb-4">
            {open === "map" && <AgentInteractionMapView {...interactions} />}
            {open === "live" && canWatch && (
              <div className="flex h-full min-h-0 flex-col gap-3">
                <Button asChild size="sm" variant="outline" className="self-start">
                  <Link href={vncPopout} target="_blank" rel="noreferrer">
                    Pop out
                    <ExternalLinkIcon className="size-3.5" />
                  </Link>
                </Button>
                <VncViewer vncPath={task.vncUrl} className="min-h-0 flex-1" />
              </div>
            )}
          </div>
        </SheetContent>
      </Sheet>

      <ConfirmDialog
        open={killOpen}
        onOpenChange={setKillOpen}
        title="Kill this agent task?"
        description="Astrolift will delete the Kubernetes Job and its per-task Secret. The run is marked cancelled only after the cluster confirms deletion; a failed deletion leaves the run active and reports the error."
        confirmLabel="Kill agent"
        destructive
        onConfirm={onHardStop}
      />
    </div>
  );
}
