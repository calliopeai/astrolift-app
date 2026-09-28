"use client";

import { BotIcon, GitBranchIcon } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { ListControls, SortableHeader } from "@/components/ListControls";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type {
  AstroliftAgentListItem,
  AstroliftAgentLiveStatus,
} from "@/graphql/agents/agents.types";
import { type SortState, useListControls } from "@/hooks/use-list-controls";
import { formatRelativeAge } from "@/lib/format";

import type { useAgentRegistry } from "./use-agent-registry";

export type AgentRegistryPanelProps = ReturnType<typeof useAgentRegistry>;

// Render runFamily + runMode as a human-readable cell, e.g. "Task · Schedule"
// or "Service". Both are free `String!` fields on the schema; normalize
// case-insensitively and title-case any value we don't recognize so a new
// backend mode degrades gracefully rather than rendering a raw token.
const RUN_FAMILY_LABELS: Record<string, string> = {
  task: "Task",
  service: "Service",
};
const RUN_MODE_LABELS: Record<string, string> = {
  once: "Once",
  loop: "Loop",
  schedule: "Schedule",
  trigger: "Trigger",
  service: "Service",
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

function formatRunMode(runFamily: string, runMode: string): string {
  const family = RUN_FAMILY_LABELS[runFamily.toLowerCase()] ?? titleCase(runFamily);
  const mode = RUN_MODE_LABELS[runMode.toLowerCase()] ?? titleCase(runMode);
  // Service is single-axis (the family *is* the mode); Task carries a
  // sub-mode (Once/Loop/Schedule/Trigger) so we show "Family · Mode".
  if (!mode || mode === family) return family || "—";
  return `${family} · ${mode}`;
}

// Last-run status → {dot, badge-variant}. Agent run vocabulary (spec 33 §6):
// running / queued / completed / failed / timed_out / cancelled. Mirrors the
// dot+badge convention the platform uses elsewhere (RunStatusBadge) but with
// the agent-run vocabulary, which differs from ScheduledJobRun ("completed"
// not "succeeded").
type Dot = "ok" | "warn" | "error" | "muted" | "pending";
const RUN_STATUS_DOT: Record<string, Dot> = {
  running: "pending",
  queued: "warn",
  completed: "ok",
  succeeded: "ok",
  failed: "error",
  timed_out: "error",
  cancelled: "muted",
  canceled: "muted",
};

function LastRunCell({
  status,
  at,
}: {
  status: string | null | undefined;
  at: string | null | undefined;
}) {
  if (!status && !at) {
    return <span className="text-muted-foreground text-sm">Never run</span>;
  }
  const key = (status ?? "").toLowerCase();
  const dot = RUN_STATUS_DOT[key] ?? "muted";
  return (
    <span className="inline-flex items-center gap-1.5">
      {status && (
        <>
          <StatusDot status={dot} />
          <Badge variant={dot === "error" ? "destructive" : "secondary"} className="capitalize">
            {titleCase(status)}
          </Badge>
        </>
      )}
      {at && (
        <span className="text-muted-foreground text-xs" title={at}>
          {formatRelativeAge(at)}
        </span>
      )}
    </span>
  );
}

// Live-status cell: running count + a running / scheduled / paused / idle
// badge. Derived from the polled agentLiveStatus row (falls back to the
// list row's own runningCount/runPaused when live data hasn't arrived yet).
function LiveStatusCell({
  live,
  fallback,
}: {
  live: AstroliftAgentLiveStatus | undefined;
  fallback: Pick<AstroliftAgentListItem, "runningCount" | "runPaused">;
}) {
  const runningCount = live?.runningCount ?? fallback.runningCount;
  const isPaused = live?.isPaused ?? fallback.runPaused;
  const isIdle = live?.isIdle ?? runningCount === 0;
  const nextScheduledAt = live?.nextScheduledAt ?? null;

  if (runningCount > 0) {
    return (
      <span className="inline-flex items-center gap-1.5">
        <StatusDot status="pending" />
        <Badge variant="default">{runningCount} running</Badge>
      </span>
    );
  }
  if (isPaused) {
    return (
      <span className="inline-flex items-center gap-1.5">
        <StatusDot status="muted" />
        <Badge variant="outline">Paused</Badge>
      </span>
    );
  }
  if (nextScheduledAt) {
    return (
      <span className="inline-flex items-center gap-1.5">
        <StatusDot status="warn" />
        <Badge variant="secondary" title={nextScheduledAt}>
          Next {formatRelativeAge(nextScheduledAt)}
        </Badge>
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5">
      <StatusDot status="muted" />
      <Badge variant="outline">{isIdle ? "Idle" : "—"}</Badge>
    </span>
  );
}

function agentSortFn(
  a: AstroliftAgentListItem,
  b: AstroliftAgentListItem,
  sort: SortState
): number {
  const dir = sort.dir === "asc" ? 1 : -1;
  switch (sort.key) {
    case "name":
      return dir * a.name.localeCompare(b.name);
    case "repo":
      return dir * a.sourceRepo.localeCompare(b.sourceRepo);
    case "runMode":
      return (
        dir *
        formatRunMode(a.runFamily, a.runMode).localeCompare(formatRunMode(b.runFamily, b.runMode))
      );
    case "lastRun":
      return dir * (a.lastRunAt ?? "").localeCompare(b.lastRunAt ?? "");
    default:
      return 0;
  }
}

/**
 * Registry tab — the registered-agents list, scoped to a project or the
 * whole fleet, with per-agent live status merged in.
 */
export function AgentRegistryPanel({
  canCreateAgent,
  projectSlug,
  fleet,
  setProjectScope,
  projects,
  agents,
  loading,
  liveByWorkloadId,
}: AgentRegistryPanelProps) {
  const ctrl = useListControls({
    data: agents,
    searchFn: (a) =>
      [a.name, a.slug, a.appSlug, a.projectSlug, a.sourceRepo, a.runFamily, a.runMode].join(" "),
    initialPageSize: 25,
    initialSort: { key: "name", dir: "asc" },
    sortFn: agentSortFn,
  });

  const scopePicker = (
    <div className="flex flex-wrap items-center gap-2">
      <Label htmlFor="agent-scope" className="text-muted-foreground text-xs">
        Scope
      </Label>
      <Select
        value={fleet ? "__fleet__" : projectSlug}
        onValueChange={(v) => setProjectScope(v === "__fleet__" ? "" : v)}
      >
        <SelectTrigger id="agent-scope" className="h-8 w-56 text-sm">
          <SelectValue placeholder="Select project…" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="__fleet__">All agents (fleet)</SelectItem>
          {projects.map((p) => (
            <SelectItem key={p.id} value={p.slug}>
              {p.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );

  if (loading && agents.length === 0) {
    return (
      <Card>
        <CardContent className="space-y-3 p-6">
          {scopePicker}
          <Skeleton className="h-12 w-full" />
          <Skeleton className="h-12 w-full" />
        </CardContent>
      </Card>
    );
  }

  if (agents.length === 0) {
    return (
      <Card>
        <CardContent className="space-y-4 p-6">
          {scopePicker}
          <EmptyState
            icon={<BotIcon className="size-5" />}
            title={fleet ? "No agents registered" : "No agents in this project"}
            description={
              fleet
                ? "Register an agent repo to scan it for agent manifests and add each one as an agent here. Agents share the same image build and deployment pipeline as your apps."
                : "This project has no registered agents yet. Register an agent repo or switch the scope to view agents across the whole fleet."
            }
            actionHref={canCreateAgent ? "/agents/new" : undefined}
            actionLabel={canCreateAgent ? "Register an agent repo" : undefined}
            learnMoreHref="https://github.com/calliopeai/astrolift-docs/blob/main/reference/agents.md"
          />
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardContent className="space-y-3 p-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          {scopePicker}
          <ListControls controls={ctrl} searchPlaceholder="Filter agents…" />
        </div>
        <div className="overflow-x-auto rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>
                  <SortableHeader sortKey="name" sort={ctrl.sort} onToggle={ctrl.toggleSort}>
                    Name
                  </SortableHeader>
                </TableHead>
                <TableHead>
                  <SortableHeader sortKey="repo" sort={ctrl.sort} onToggle={ctrl.toggleSort}>
                    Repo
                  </SortableHeader>
                </TableHead>
                <TableHead>
                  <SortableHeader sortKey="runMode" sort={ctrl.sort} onToggle={ctrl.toggleSort}>
                    Run mode
                  </SortableHeader>
                </TableHead>
                <TableHead>Live status</TableHead>
                <TableHead>
                  <SortableHeader sortKey="lastRun" sort={ctrl.sort} onToggle={ctrl.toggleSort}>
                    Last run
                  </SortableHeader>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {ctrl.rows.map((a) => (
                <TableRow key={a.id}>
                  <TableCell>
                    <Link
                      href={`/agents/${encodeURIComponent(a.slug)}/build`}
                      className="font-medium hover:text-[var(--brand-primary)] hover:underline"
                    >
                      {a.name}
                    </Link>
                    <div className="text-muted-foreground font-mono text-xs">
                      {a.projectSlug}/{a.appSlug}/{a.slug}
                    </div>
                  </TableCell>
                  <TableCell>
                    {a.sourceRepo ? (
                      a.sourceUrl ? (
                        <a
                          href={a.sourceUrl}
                          target="_blank"
                          rel="noreferrer"
                          className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-sm"
                        >
                          <GitBranchIcon className="size-3.5" />
                          {a.sourceRepo}
                        </a>
                      ) : (
                        <span className="text-muted-foreground inline-flex items-center gap-1 text-sm">
                          <GitBranchIcon className="size-3.5" />
                          {a.sourceRepo}
                        </span>
                      )
                    ) : (
                      <span className="text-muted-foreground text-sm">—</span>
                    )}
                  </TableCell>
                  <TableCell>
                    <Badge variant="outline">{formatRunMode(a.runFamily, a.runMode)}</Badge>
                  </TableCell>
                  <TableCell>
                    <LiveStatusCell
                      live={liveByWorkloadId.get(a.id)}
                      fallback={{ runningCount: a.runningCount, runPaused: a.runPaused }}
                    />
                  </TableCell>
                  <TableCell>
                    <LastRunCell status={a.lastRunStatus} at={a.lastRunAt} />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </CardContent>
    </Card>
  );
}
