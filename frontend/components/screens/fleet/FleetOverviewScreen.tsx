"use client";

import {
  AlertTriangleIcon,
  BotIcon,
  CircleDotIcon,
  Clock3Icon,
  CpuIcon,
  ListChecksIcon,
  RadioTowerIcon,
  RefreshCwIcon,
} from "lucide-react";
import type * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { DataTable, type Column } from "@/components/data-table";
import { PageShell } from "@/components/PageShell";
import { Section } from "@/components/ui/section";
import { StatTile } from "@/components/ui/stat-tile";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { AstroliftAgentListItem, AstroliftAgentTask } from "@/graphql/agents/agents.types";

import {
  ACTIVE_TASK_STATUSES,
  FAILING_TASK_STATUSES,
  type useFleetOverview,
} from "./use-fleet-overview";

export type FleetOverviewScreenProps = ReturnType<typeof useFleetOverview> & {
  /** The live fleet topology map (FleetMapPanel with its data). */
  map: React.ReactNode;
};

const AGENT_COLUMNS: Column<AstroliftAgentListItem>[] = [
  { id: "agent", header: "Agent", cell: (row) => <span className="font-medium">{row.name}</span> },
  {
    id: "project",
    header: "Project",
    cell: (row) => (
      <span className="text-muted-foreground font-mono text-xs">{row.projectSlug || "—"}</span>
    ),
  },
  {
    id: "status",
    header: "Status",
    cell: (row) => (
      <Badge variant={row.runningCount > 0 ? "default" : "outline"}>
        {row.runningCount > 0 ? `${row.runningCount} running` : "Idle"}
      </Badge>
    ),
  },
];

const TASK_COLUMNS: Column<AstroliftAgentTask>[] = [
  {
    id: "agent",
    header: "Agent",
    cell: (row) => (
      <span className="font-medium">{row.agentName || row.agentSlug || "Agent task"}</span>
    ),
  },
  {
    id: "project",
    header: "Project",
    cell: (row) => (
      <span className="text-muted-foreground font-mono text-xs">{row.projectSlug || "—"}</span>
    ),
  },
  {
    id: "status",
    header: "Status",
    cell: (row) => (
      <Badge variant={statusTone(row.status)} className="capitalize">
        {row.status.replace(/_/g, " ")}
      </Badge>
    ),
  },
  {
    id: "created",
    header: "Created",
    cell: (row) => <span className="text-muted-foreground text-xs">{age(row.createdAt)}</span>,
    align: "right",
  },
];

function age(iso: string | null): string {
  if (!iso) return "—";
  const ms = Date.now() - Date.parse(iso);
  if (!Number.isFinite(ms) || ms < 0) return "just now";
  const minutes = Math.floor(ms / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function statusTone(status: string): "default" | "secondary" | "destructive" | "outline" {
  if (status === "running") return "default";
  if (FAILING_TASK_STATUSES.has(status)) return "destructive";
  if (ACTIVE_TASK_STATUSES.has(status)) return "secondary";
  return "outline";
}

/** /fleet: the live command center for agents, runtimes, dispatch and task health. */
export function FleetOverviewScreen({
  agentCount,
  runningAgentCount,
  runtimeCount,
  activeTaskCount,
  incidentCount,
  loading,
  runtimes,
  runtimesLoading,
  refresh,
  agentTable,
  taskTable,
  map,
}: FleetOverviewScreenProps) {
  return (
    <PageShell
      title="Fleet overview"
      description="A live command center for agents, runtimes, dispatch, and task health."
      actions={
        <Button variant="outline" size="sm" onClick={refresh}>
          <RefreshCwIcon className="mr-2 size-4" /> Refresh
        </Button>
      }
    >
      <Section title="At a glance" description="Current state reported by the Dispatch service.">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatTile
            label="Agents"
            value={agentCount}
            icon={BotIcon}
            loading={loading}
            href="/agents?tab=registry"
            footer={`${runningAgentCount} active now`}
          />
          <StatTile
            label="Runtimes"
            value={runtimeCount}
            icon={CpuIcon}
            loading={loading}
            href="/agents?tab=boxes"
            footer="Available environment specs"
          />
          <StatTile
            label="Active tasks"
            value={activeTaskCount}
            icon={ListChecksIcon}
            loading={loading}
            href="/agents?tab=active"
            footer="Queued, provisioning, or running"
          />
          <StatTile
            label="Incidents"
            value={incidentCount}
            icon={AlertTriangleIcon}
            loading={loading}
            href="/agents?tab=history"
            footer="Failed or timed out tasks"
          />
        </div>
      </Section>

      <Section
        title="Fleet topology"
        description="Dispatchers route work through the live runtime fleet."
      >
        <Card className="overflow-hidden">{map}</Card>
      </Section>

      <div className="grid gap-6 xl:grid-cols-[1.35fr_1fr]">
        <Section title="Recent activity" description="The latest task transitions from Dispatch.">
          <DataTable
            label="Recent agent activity"
            controller={taskTable}
            columns={TASK_COLUMNS}
            getRowId={(row) => row.id}
            rowHref={(row) => `/agents/runs/${encodeURIComponent(row.id)}`}
            searchPlaceholder="Search tasks"
            empty={{
              icon: <Clock3Icon className="size-5" />,
              title: "No task activity yet",
              description: "Tasks dispatched through this organization will appear here.",
              actionHref: "/agents?tab=dispatch",
              actionLabel: "Open dispatch",
            }}
            emptyFiltered={{ title: "No matching tasks", description: "Try a different search." }}
          />
        </Section>

        <Section
          title="Runtime health"
          description="Reusable environments currently available to the fleet."
        >
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="flex items-center gap-2 text-base">
                <RadioTowerIcon className="size-4" /> Environment specs
              </CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              {runtimesLoading ? (
                <div className="space-y-3 px-5 pb-5">
                  <Skeleton className="h-8 w-full" />
                  <Skeleton className="h-8 w-full" />
                </div>
              ) : runtimes.length ? (
                <ul className="divide-y">
                  {runtimes.slice(0, 8).map((runtime) => (
                    <li
                      key={runtime.id}
                      className="flex items-center justify-between gap-3 px-5 py-3"
                    >
                      <div className="min-w-0">
                        <p className="truncate font-medium">{runtime.name}</p>
                        <p className="text-muted-foreground truncate font-mono text-xs">
                          {runtime.runtime} · {runtime.agentType}
                        </p>
                      </div>
                      <CircleDotIcon
                        className="text-success size-4 shrink-0"
                        aria-label="Available"
                      />
                    </li>
                  ))}
                </ul>
              ) : (
                <EmptyState
                  icon={<CpuIcon className="size-5" />}
                  title="No runtimes configured"
                  description="Add an environment spec before dispatching an agent."
                  actionHref="/agents?tab=boxes"
                  actionLabel="Manage runtimes"
                />
              )}
            </CardContent>
          </Card>
        </Section>
      </div>

      <Section
        title="Agent roster"
        description="Registered agents and the runtime work they are carrying now."
      >
        <DataTable
          label="Agent roster"
          controller={agentTable}
          columns={AGENT_COLUMNS}
          getRowId={(row) => row.id}
          rowHref={(row) => `/agents/${encodeURIComponent(row.slug)}`}
          searchPlaceholder="Search agents"
          empty={{
            icon: <BotIcon className="size-5" />,
            title: "No agents registered",
            description: "Register an agent repository before dispatching work.",
            actionHref: "/agents?tab=registry",
            actionLabel: "Open agent registry",
          }}
          emptyFiltered={{ title: "No matching agents", description: "Try a different search." }}
        />
      </Section>
    </PageShell>
  );
}
