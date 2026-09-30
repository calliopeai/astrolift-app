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
import Link from "next/link";

import type { AstroliftAgentListItem, AstroliftAgentTask } from "@/graphql/agents/agents.types";

import { ListSummary } from "@/components/list/ListSummary";
import { PageShell } from "@/components/PageShell";
import { PanelGrid } from "@/components/panel/Panel";
import { Section } from "@/components/ui/section";
import { StatTile } from "@/components/ui/stat-tile";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

import {
  ACTIVE_TASK_STATUSES,
  FAILING_TASK_STATUSES,
  type useFleetOverview,
} from "./use-fleet-overview";

export type FleetOverviewScreenProps = ReturnType<typeof useFleetOverview> & {
  /** The live fleet topology map (FleetMapPanel with its data). */
  map: React.ReactNode;
};

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

/**
 * /fleet: the live command center for agents, runtimes, dispatch and task
 * health. An overview (list rule 3): the counts, the topology map, then the
 * recent tasks, the runtimes and the roster as summaries of their top rows,
 * each with "View all" to its own list. Pure; the data half is
 * useFleetOverview.
 */
export function FleetOverviewScreen({
  agentCount,
  runningAgentCount,
  runtimeCount,
  activeTaskCount,
  incidentCount,
  loading,
  runtimes,
  runtimesLoading,
  runtimesError,
  refresh,
  recentTasks,
  tasksLoading,
  tasksError,
  agents,
  agentsLoading,
  agentsError,
  map,
}: FleetOverviewScreenProps) {
  return (
    <PageShell
      title="Fleet overview"
      description="A live command center for agents, runtimes, dispatch, and task health."
      actions={
        <>
          <Button variant="outline" size="sm" asChild>
            <Link href="/fleet/map">Open fleet map</Link>
          </Button>
          <Button variant="outline" size="sm" onClick={refresh}>
            <RefreshCwIcon className="mr-2 size-4" /> Refresh
          </Button>
        </>
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

      <PanelGrid>
        <ListSummary<AstroliftAgentTask>
          span={4}
          title="Recent activity"
          icon={<Clock3Icon className="size-4" />}
          description="The latest task transitions from Dispatch."
          count={recentTasks.length}
          rows={recentTasks}
          keyOf={(t) => t.id}
          rowHref={(t) => `/agents/runs/${encodeURIComponent(t.id)}`}
          viewAllHref="/agents?tab=history"
          loading={tasksLoading}
          error={tasksError}
          onRetry={refresh}
          empty={{
            icon: <Clock3Icon className="size-5" />,
            title: "No task activity yet",
            description: "Tasks dispatched through this organization will appear here.",
            actionHref: "/agents?tab=dispatch",
            actionLabel: "Open dispatch",
          }}
          renderRow={(t) => (
            <span className="flex min-w-0 items-center gap-2">
              <span className="min-w-0 flex-1 truncate font-medium">
                {t.agentName || t.agentSlug || "Agent task"}
              </span>
              <Badge variant={statusTone(t.status)} className="shrink-0 capitalize">
                {t.status.replace(/_/g, " ")}
              </Badge>
              <span className="text-muted-foreground shrink-0 font-mono text-xs">
                {age(t.createdAt)}
              </span>
            </span>
          )}
        />

        <ListSummary<(typeof runtimes)[number]>
          span={4}
          title="Runtime health"
          icon={<RadioTowerIcon className="size-4" />}
          description="Reusable environments currently available to the fleet."
          count={runtimes.length}
          rows={runtimes}
          keyOf={(r) => r.id}
          viewAllHref="/agents?tab=boxes"
          loading={runtimesLoading && runtimes.length === 0}
          error={runtimesError}
          onRetry={refresh}
          empty={{
            icon: <CpuIcon className="size-5" />,
            title: "No runtimes configured",
            description: "Add an environment spec before dispatching an agent.",
            actionHref: "/agents?tab=boxes",
            actionLabel: "Manage runtimes",
          }}
          renderRow={(r) => (
            <span className="flex min-w-0 items-center justify-between gap-3">
              <span className="min-w-0">
                <span className="block truncate font-medium">{r.name}</span>
                <span className="text-muted-foreground block truncate font-mono text-xs">
                  {r.runtime} · {r.agentType}
                </span>
              </span>
              <CircleDotIcon className="text-success size-4 shrink-0" aria-label="Available" />
            </span>
          )}
        />

        <ListSummary<AstroliftAgentListItem>
          span={4}
          title="Agent roster"
          icon={<BotIcon className="size-4" />}
          description="Registered agents and the runtime work they are carrying now."
          count={agents.length}
          rows={agents}
          keyOf={(a) => a.id}
          rowHref={(a) => `/agents/${encodeURIComponent(a.slug)}`}
          viewAllHref="/agents"
          loading={agentsLoading}
          error={agentsError}
          onRetry={refresh}
          empty={{
            icon: <BotIcon className="size-5" />,
            title: "No agents registered",
            description: "Register an agent repository before dispatching work.",
            actionHref: "/agents?tab=registry",
            actionLabel: "Open agent registry",
          }}
          renderRow={(a) => (
            <span className="flex min-w-0 items-center gap-2">
              <span className="min-w-0 flex-1">
                <span className="block truncate font-medium">{a.name}</span>
                <span className="text-muted-foreground block truncate font-mono text-xs">
                  {a.projectSlug || "—"}
                </span>
              </span>
              <Badge variant={a.runningCount > 0 ? "default" : "outline"} className="shrink-0">
                {a.runningCount > 0 ? (
                  <span className="font-mono">{a.runningCount} running</span>
                ) : (
                  "Idle"
                )}
              </Badge>
            </span>
          )}
        />
      </PanelGrid>
    </PageShell>
  );
}
